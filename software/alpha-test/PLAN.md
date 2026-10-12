# Bix · alpha-test plan

Isolated instance: server on :8720 (own data dir, SQLite), virtual device panel on :8721 (file microphone), SMTP sink on :8025. STT = ArDi Whisper, LLM = vader vLLM (private, no cost, no outward effects).

## Roles
anonymous · admin · member · auditor · device (token) · revoked device

## Journeys

### J1 First run & login
- [ ] fresh server shows setup; setup with short password / empty org / bad email
- [ ] setup cannot be repeated (409); login wrong password ×11 → throttled
- [ ] logout; session cookie flags; password change invalidates old sessions

### J2 Device enrollment
- [ ] code for room → enroll via panel UI and via CLI; code single-use, expired, malformed, lower-case/no dashes
- [ ] device appears online in portal with telemetry; rename; move room; revoke → device stops

### J3 Meeting from the room (core)
- [ ] start in panel → live text appears (UI) → pause → resume → stop
- [ ] chunks uploaded; processing; portal shows transcript, speakers, minutes, audio plays
- [ ] double start / double stop; "Не записвай" deletes everywhere; stop with nothing recorded
- [ ] network down mid-meeting (server stopped) → buffer → server back → completes
- [ ] device killed mid-meeting → restart → recovered meeting processed

### J4 Review in portal
- [ ] minutes refs jump to transcript lines; deep link #L12
- [ ] name speaker (new name / existing person / remember voice with & without consent)
- [ ] regenerate minutes; reprocess from stt/speakers; title edit; export .md; send (SMTP sink)
- [ ] search Cyrillic text; room filter

### J5 Upload a recording
- [ ] WAV/MP3/OGG upload; non-audio file; 1-second file; huge-ish file; Cyrillic file name

### J6 People & voices
- [ ] add person; voice sample with/without consent; too short; forget voice; GDPR erase

### J7 Users & roles
- [ ] create member/auditor; duplicate email; weak password; disable user; self-demotion blocked
- [ ] member sees only own meetings; auditor sees no content; member cannot admin endpoints

### J8 Settings & policy
- [ ] change each setting → device receives it; invalid values (negative, text); speaker_mode anonymous hides names
- [ ] retention removes audio files from disk, then whole meeting

### J9 Dependencies down
- [ ] STT down → fallback / clean failure + retry; LLM down → fallback / failure visible in UI; SMTP down → clear error

### J10 Security
- [ ] XSS in title, room, person name, speaker name, settings notice, upload filename (portal + panel + e-mail HTML)
- [ ] CSRF header required (portal + panel); panel rejects foreign Host
- [ ] path traversal / bad IDs in every {id}; search injection; audio of other org
- [ ] secrets not in /api/settings, logs, errors; security headers present

### J11 UX
- [ ] every page desktop + 390 px + dark mode screenshots; console errors; empty states; misleading text

### J12 Ops
- [ ] /healthz /metrics; bix doctor with broken config; example configs load; CLI commands with bad args

## Run 2 (11.10.2026) — areas run 1 did not cover

- [ ] R1 Portal → device commands: start, stop, mute, unmute, identify reach the device and show in both UIs
- [ ] R2 Panel "Не записвай" (discard) mid-meeting: nothing left on device or server; double-click start/stop
- [ ] R3 Two devices recording at once in different rooms; meetings attributed to the right room
- [ ] R4 Edit / delete flows in the UI: room edit+delete (with devices → blocked), meeting title edit, meeting delete, person erase, user disable/role change, password change (old sessions logged out)
- [ ] R5 Member journey in the UI: attended meeting visible (via speaker → person e-mail), others not; upload as member → visible to them
- [ ] R6 Portal live updates (SSE): meeting list/status changes without reload; live text in portal transcript tab
- [ ] R7 Audit page: filter, paging, every important action recorded with actor
- [ ] R8 Reprocess dialog ("Още…"): each step; blocked while processing; after audio deleted
- [ ] R9 Long input: 20-minute meeting (performance, chunk count, minutes length); 300-char titles; 1000-line transcript UI
- [ ] R10 Docker image builds and serves (vader, throw-away container); install-device.sh on clean Ubuntu container
- [ ] R11 Accessibility basics: keyboard-only login and start meeting; labels on inputs; contrast in dark mode
