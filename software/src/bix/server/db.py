"""Database model (SQLAlchemy 2). SQLite for a single machine, PostgreSQL in the cloud.

Everything carries org_id so one deployment can serve several organisations;
the portal currently works with one organisation per user.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


DEFAULT_ORG_SETTINGS: dict[str, Any] = {
    "language": "bg",
    # Звукът се трие след N дни; текстът (транскрипция и минути) след M дни. 0 = никога.
    "retention_audio_days": 30,
    "retention_text_days": 365,
    # Текст на живо: ~4 пъти по-скъпо от обработка след срещата (blueprint, т. 06).
    "live_transcription": False,
    # voice_profiles | calendar_direction | anonymous
    "speaker_mode": "voice_profiles",
    # Минутите се пращат автоматично след обработка (когато имейлът е настроен и има към кого).
    "auto_send": True,
    # Към кого: organizer | attendees (от календара и разпознатите по глас) | room | fixed
    "send_to": ["organizer", "attendees", "room"],
    "fixed_recipients": [],
    # Членовете виждат всички срещи (иначе само тези, в които са участвали).
    "members_see_all_meetings": False,
    # Срещата спира сама след толкова минути без реч или без присъствие.
    "auto_stop_silence_min": 10,
    "max_meeting_hours": 4,
    # Текст на екрана на устройството (AI Act, чл. 50).
    "notice_text": "Тази среща се записва. Bix е ИИ асистент, който прави транскрипция и минути.",
    # Лицето на екрана в залата: pixel | capsule | eve | dots | brows | marker | phosphor | terminal | orb
    "face_style": "pixel",
    # Речник: имена, продукти, съкращения с правилния правопис; „Правилно: грешно1, грешно2“ на ред.
    "glossary": [],
}


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=now)

    def setting(self, key: str) -> Any:
        return (self.settings or {}).get(key, DEFAULT_ORG_SETTINGS.get(key))

    def all_settings(self) -> dict[str, Any]:
        return {**DEFAULT_ORG_SETTINGS, **(self.settings or {})}


ROLES = ("admin", "member", "auditor")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    # admin: всичко; member: своите срещи и тези в залите си; auditor: само одит и метаданни
    role: Mapped[str] = mapped_column(String(16), default="member")
    password_hash: Mapped[str | None] = mapped_column(String(300), nullable=True)
    oidc_sub: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    last_login: Mapped[float | None] = mapped_column(Float, nullable=True)


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(200), default="")
    # Пощенска кутия на залата в Microsoft 365 (за календара).
    mailbox: Mapped[str] = mapped_column(String(320), default="")
    # Таен iCal адрес на календара на залата (Google Calendar, Outlook). Тайна: показва се само на администратори.
    calendar_ics_url: Mapped[str] = mapped_column(Text, default="")
    recipients: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    serial: Mapped[str] = mapped_column(String(100), default="")
    token_hash: Mapped[str] = mapped_column(String(128))
    cert_fingerprint: Mapped[str] = mapped_column(String(128), default="")
    enrolled_at: Mapped[float] = mapped_column(Float, default=now)
    last_seen: Mapped[float | None] = mapped_column(Float, nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="unknown")
    fw_version: Mapped[str] = mapped_column(String(50), default="")
    platform: Mapped[str] = mapped_column(String(100), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    telemetry: Mapped[dict[str, Any]] = mapped_column(default=dict)
    commands: Mapped[list[Any]] = mapped_column(default=list)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class EnrollmentCode(Base):
    __tablename__ = "enrollment_codes"
    code_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"))
    room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    expires_at: Mapped[float] = mapped_column(Float)
    used_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


MEETING_STATUSES = ("recording", "uploading", "queued", "processing", "done", "failed")


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id"), nullable=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="device")  # device | upload
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    # True while the title is a placeholder (date, file name) that the calendar
    # subject or the generated minutes may replace; False once a person set it.
    title_auto: Mapped[bool] = mapped_column(Boolean, default=True)
    organizer_email: Mapped[str] = mapped_column(String(320), default="")
    attendees: Mapped[list[Any]] = mapped_column(default=list)  # [{name, email}]
    status: Mapped[str] = mapped_column(String(16), default="recording", index=True)
    started_at: Mapped[float] = mapped_column(Float, default=now, index=True)
    ended_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    expected_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speakers: Mapped[list[Any]] = mapped_column(default=list)
    steps: Mapped[dict[str, Any]] = mapped_column(default=dict)
    minutes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    transcript_text: Mapped[str] = mapped_column(Text, default="")  # за търсене
    error: Mapped[str] = mapped_column(Text, default="")
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    audio_deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    sent_to: Mapped[list[Any]] = mapped_column(default=list)
    sent_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The calendar event the meeting was started for (picked on the device, or matched later).
    calendar_uid: Mapped[str] = mapped_column(String(300), default="")
    scheduled_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Said or marked in the room: [{t, kind: star|task|decision|note, text}]
    notes: Mapped[list[Any]] = mapped_column(default=list)


class Chunk(Base):
    __tablename__ = "chunks"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_s: Mapped[float] = mapped_column(Float)
    duration_s: Mapped[float] = mapped_column(Float)
    key: Mapped[str] = mapped_column(String(400))
    size: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[float] = mapped_column(Float, default=now)
    live_done: Mapped[bool] = mapped_column(Boolean, default=False)  # already transcribed while the meeting runs


class Booking(Base):
    """A room booking made in the portal: for rooms without a calendar, and for testing."""

    __tablename__ = "bookings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    start: Mapped[float] = mapped_column(Float, index=True)
    end: Mapped[float] = mapped_column(Float)
    organizer_name: Mapped[str] = mapped_column(String(200), default="")
    organizer_email: Mapped[str] = mapped_column(String(320), default="")
    attendees: Mapped[list[Any]] = mapped_column(default=list)  # [{name, email}]
    # weekly | "" : the same time every week (a regular meeting)
    repeat: Mapped[str] = mapped_column(String(16), default="")
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class LiveSegment(Base):
    __tablename__ = "live_segments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)


class Person(Base):
    """A voice profile. Created only with recorded consent of the person."""

    __tablename__ = "people"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), default="")
    embedding: Mapped[list[Any]] = mapped_column(default=list)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    consent_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    consent_by: Mapped[str] = mapped_column(String(320), default="")
    consent_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))
    meeting_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    args: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[float] = mapped_column(Float, default=now)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    locked_by: Mapped[str] = mapped_column(String(100), default="")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    at: Mapped[float] = mapped_column(Float, default=now, index=True)
    actor: Mapped[str] = mapped_column(String(400))  # user:<email> | device:<id> | system
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(200), default="")
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(url, pool_pre_ping=True, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def _pragmas(conn, _):  # noqa: ANN001
                # SQLite's lower() (and so ILIKE) only folds ASCII: Cyrillic search
                # would be case-sensitive. Replace it with Python's Unicode-aware one.
                conn.create_function("lower", 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True)
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()

        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Forward-only schema upgrade: add columns that newer versions define.

        Enough for additive changes between releases; renames or type changes
        will need real migrations (Alembic) when they come.
        """
        from sqlalchemy import inspect, text

        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not insp.has_table(table.name):
                    continue
                have = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in have:
                        continue
                    ddl = col.type.compile(dialect=self.engine.dialect)
                    default = ""
                    if col.default is not None and col.default.is_scalar:
                        v = col.default.arg
                        default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v) if isinstance(v, str) else v}"
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}{default}'))

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._sessions()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


def audit(s: Session, actor: str, action: str, target: str = "", org_id: str | None = None, ip: str = "", **details: Any) -> None:
    s.add(AuditLog(org_id=org_id, actor=actor, action=action, target=target, details=details, ip=ip))


def first_org(s: Session) -> Org | None:
    return s.scalars(select(Org).order_by(Org.created_at)).first()
