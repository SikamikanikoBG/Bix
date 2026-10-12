"""Room calendars: what is booked in a room, and when.

A room can have any of these sources; their events are merged:
  bookings   made in the portal (rooms without a calendar, and testing)
  iCal link  the secret address of the room calendar (Google Calendar,
             Outlook / Microsoft 365 "publish calendar"): no app registration
  m365       the room mailbox through Microsoft Graph ([graph] in server.toml)

An event is {uid, title, start, end, organizer: {name, email},
attendees: [{name, email}], source}. Times are Unix seconds.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import or_, select

from bix.server.db import Booking, Room

log = logging.getLogger(__name__)

ICS_TTL_S = 300
GRAPH_TTL_S = 120
WEEK = 7 * 86400
MAX_ICS_BYTES = 10 * 1024 * 1024


class CalendarError(RuntimeError):
    pass


class Calendars:
    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.errors: dict[str, str] = {}  # room id -> last problem, shown in the portal

    # --- queries ---------------------------------------------------------------
    def events(self, s: Any, room: Room, start: float, end: float) -> list[dict[str, Any]]:
        out = bookings(s, room, start, end)
        problems = []
        if room.calendar_ics_url:
            try:
                cal = self._cached("ics:" + room.calendar_ics_url, ICS_TTL_S, lambda: fetch_ics(room.calendar_ics_url))
                out += ics_events(cal, start, end)
            except CalendarError as e:
                problems.append(str(e))
        if room.mailbox and self.graph.enabled:
            win = f"{int(start // 300)}:{int(end // 300)}"
            try:
                out += self._cached(f"m365:{room.mailbox}:{win}", GRAPH_TTL_S, lambda: self.graph.events(room.mailbox, start, end))
            except Exception as e:
                problems.append(f"Microsoft 365: {e}"[:300])
        if problems:
            self.errors[room.id] = "; ".join(problems)
        else:
            self.errors.pop(room.id, None)
        seen, merged = set(), []
        for ev in sorted(out, key=lambda e: e["start"]):
            if ev["uid"] not in seen:
                seen.add(ev["uid"])
                merged.append(ev)
        return merged

    def at(self, s: Any, room: Room, start: float, end: float) -> dict[str, Any] | None:
        """The event that overlaps most with [start, end] (a recorded meeting)."""
        best, best_ov = None, 0.0
        for ev in self.events(s, room, start - 900, end + 900):
            ov = min(end, ev["end"]) - max(start, ev["start"])
            if ov > best_ov:
                best, best_ov = ev, ov
        return best

    def find(self, s: Any, room: Room, uid: str, around: float) -> dict[str, Any] | None:
        for ev in self.events(s, room, around - 12 * 3600, around + 12 * 3600):
            if ev["uid"] == uid:
                return ev
        return None

    def agenda(self, s: Any, room: Room, now: float | None = None) -> list[dict[str, Any]]:
        """What the room screen shows: the meeting now and the next ones today (and tomorrow morning)."""
        now = now or time.time()
        evs = [e for e in self.events(s, room, now - 6 * 3600, now + 24 * 3600) if e["end"] > now - 1800]
        return [
            {
                "uid": e["uid"], "title": e["title"] or "Среща", "start": e["start"], "end": e["end"],
                "organizer": e["organizer"].get("name") or e["organizer"].get("email", "").split("@")[0],
                "attendees": [a.get("name") or a.get("email", "").split("@")[0] for a in e["attendees"]][:12],
                "n_attendees": len(e["attendees"]),
            }
            for e in evs[:6]
        ]

    def _cached(self, key: str, ttl: float, load: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            value = load()
        except Exception:
            if hit:  # a calendar that is down for a minute should not empty the room screen
                log.warning("calendar %s: using the last good copy", key.split(":")[0])
                return hit[1]
            raise
        with self._lock:
            self._cache[key] = (time.time(), value)
            if len(self._cache) > 500:
                self._cache.pop(next(iter(self._cache)))
        return value


# --- portal bookings -------------------------------------------------------------
def bookings(s: Any, room: Room, start: float, end: float) -> list[dict[str, Any]]:
    rows = s.scalars(
        select(Booking).where(Booking.room_id == room.id, Booking.start < end, or_(Booking.end > start, Booking.repeat == "weekly"))
    ).all()
    out = []
    for b in rows:
        dur = b.end - b.start
        k = 0 if b.repeat != "weekly" or b.end > start else int((start - b.end) // WEEK) + 1
        while True:
            s0 = b.start + k * WEEK
            if s0 >= end or (k and b.repeat != "weekly"):
                break
            if s0 + dur > start:
                out.append({
                    "uid": f"bix:{b.id}:{s0:.0f}", "title": b.title, "start": s0, "end": s0 + dur,
                    "organizer": {"name": b.organizer_name, "email": b.organizer_email},
                    "attendees": list(b.attendees or []), "source": "bix",
                })
            k += 1
    return out


# --- iCal ------------------------------------------------------------------------
def fetch_ics(url: str) -> Any:
    import icalendar

    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[9:]
    if not url.lower().startswith(("https://", "http://")):
        raise CalendarError("iCal адресът трябва да започва с https://")
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        raise CalendarError(f"Календарът не отговаря ({type(e).__name__}).") from e
    if r.status_code in (401, 403, 404):
        raise CalendarError(f"Календарът върна {r.status_code}: проверете тайния iCal адрес.")
    if r.status_code >= 400:
        raise CalendarError(f"Календарът върна {r.status_code}.")
    if len(r.content) > MAX_ICS_BYTES:
        raise CalendarError("Календарът е твърде голям (над 10 MB).")
    try:
        return icalendar.Calendar.from_ical(r.content)
    except Exception as e:
        raise CalendarError("Адресът не връща iCal календар (.ics).") from e


def ics_events(cal: Any, start: float, end: float) -> list[dict[str, Any]]:
    import recurring_ical_events

    a = datetime.fromtimestamp(start, timezone.utc)
    b = datetime.fromtimestamp(end, timezone.utc)
    out = []
    for ev in recurring_ical_events.of(cal).between(a, b):
        ds = ev.get("DTSTART")
        if ds is None or not isinstance(ds.dt, datetime):
            continue  # all-day entries are not meetings
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        s0 = ds.dt if ds.dt.tzinfo else ds.dt.astimezone()
        de = ev.get("DTEND")
        if de is not None and isinstance(de.dt, datetime):
            e0 = de.dt if de.dt.tzinfo else de.dt.astimezone()
        else:
            dur = ev.get("DURATION")
            e0 = s0 + (dur.dt if dur is not None else timedelta(hours=1))
        org = ev.get("ORGANIZER")
        att = ev.get("ATTENDEE") or []
        if not isinstance(att, list):
            att = [att]
        out.append({
            "uid": f"ics:{ev.get('UID', '')}:{s0.timestamp():.0f}",
            "title": str(ev.get("SUMMARY", "")),
            "start": s0.timestamp(),
            "end": e0.timestamp(),
            "organizer": _person(org) if org is not None else {"name": "", "email": ""},
            "attendees": [_person(x) for x in att if str(getattr(x, "params", {}).get("CUTYPE", "")).upper() not in ("ROOM", "RESOURCE")],
            "source": "ics",
        })
    return out


def _person(addr: Any) -> dict[str, str]:
    email = str(addr)
    if email.lower().startswith("mailto:"):
        email = email[7:]
    name = str(getattr(addr, "params", {}).get("CN", "")).strip('"')
    return {"name": name if name != email else "", "email": email}
