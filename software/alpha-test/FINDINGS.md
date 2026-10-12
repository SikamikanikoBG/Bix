# Bix · alpha-test findings

Severity: critical · high · medium · low. Status: fixed (verified) · open.

### F-001 [medium] Navigation and "Изход" visible on setup/login pages
Where: portal, logged out. Cause: `aside { display:flex }` overrides the `hidden` attribute; the same applies to any styled element toggled with `hidden`.
Fix: global `[hidden] { display:none !important }` in app.css and panel.html. Status: fixed

### F-002 [low] Error toast lingers after navigating to the next page
Fix: `route()` hides the toast. Status: fixed

### F-003 [critical] Enrolling a device from the room panel always fails (422)
Where: device panel → Регистрирай. Cause: the pydantic model was declared inside `create_panel()`; with postponed annotations FastAPI could not resolve it and read `body` as a query parameter.
Fix: model at module level (`device/panel.py`). Test: `test_panel.py::test_enroll_from_panel`. Status: fixed

### F-004 [high] Panel swallows error messages
Where: panel, any failing action. The error was shown and then cleared at once by the status refresh, so the user saw nothing happen. Validation errors also showed as "[object Object]".
Fix: `uiError` is kept until the next successful action; 422 details are turned into text. Status: fixed

### F-005 [medium] Invalid e-mail addresses accepted for recipients
Where: rooms (recipients, mailbox), fixed recipients, people, the send dialog. "bad-address" was stored and the minutes were sent to it.
Fix: `clean_emails()` validates, normalises (lower case) and de-duplicates; a 400 names the bad address. Empty room/person names are rejected too. Test: `test_invalid_emails_rejected`. Status: fixed

### F-006 [high] "Свали .md" fails with 500 for every Cyrillic meeting title
Cause: the file name went raw into Content-Disposition (headers are Latin-1).
Fix: ASCII fallback plus an RFC 5987 `filename*=UTF-8''…`. Test: `test_export_with_cyrillic_title`. Status: fixed

### F-007 [high] "Запомни гласа" for a new person fails with 500
Cause: column defaults are applied only on INSERT, so `samples` was None before the flush.
Fix: explicit initial values. Test: `test_remember_voice_creates_profile`. Status: fixed

### F-008 [medium] Line link in the minutes e-mail loses its target when logged out
Steps: open `…/#/meetings/<id>#L10` while logged out → log in. You landed on the dashboard.
Fix: the target is kept in sessionStorage and restored after login (password and SSO). Status: fixed (verified in browser)

### F-009 [low] Virtual file microphone plays once from the agent's start
A test meeting started later got only silence. Fix: the file loops with a 3 s gap. Status: fixed

### F-010 [medium] A dialog left open blocks the page after navigation; no way to close with Esc
Fix: dialogs are removed on route change and close with Esc. Status: fixed

### F-011 [low] Empty or non-audio upload says "install ffmpeg"
Fix: "Файлът е празен." / "Файлът не е звуков запис или е повреден…" with the supported formats. Status: fixed

### F-012 [medium] A typed meeting title is replaced by the generated one if it starts with "Среща "
Cause: placeholder titles were recognised by their text. Fix: a `title_auto` flag (device and file-name titles are placeholders; typed, edited and calendar titles are kept). Test: `test_typed_title_survives_generated_minutes`. Status: fixed

### F-013 [high] No schema upgrades: any new column breaks existing installations
Fix: on start, missing columns are added (`Database._add_missing_columns`, forward-only; Alembic is needed once renames come). Verified on the alpha DB and the user's DB. Test: `test_schema_upgrade_adds_new_columns`. Status: fixed

### F-014 [medium] Members can list devices (IPs, telemetry) through the API
The UI hid the page, the API didn't. Fix: admin/auditor only. Test in `test_roles_and_csrf`. Status: fixed

### F-015 [medium] 10 failed logins from one IP lock out everyone behind that IP for 15 min
Fix: 10 per account+IP, 100 per IP. Test: `test_login_throttle_is_per_account`. Status: fixed

### F-016 [medium] Every page scrolls sideways on phones; audit overflows on desktop
Fix: tables scroll inside their own box; long values wrap; 2-column stats on phones. Verified on all 9 pages at 390 px dark and 1360 px. Status: fixed

### F-017 [low] Minutes mention speaker labels ("С2 се ангажира…")
Fix: prompt rule: names or neutral wording, never labels. Status: fixed (prompt; LLM behaviour, re-check in Phase 1)

### F-018 [medium] No logout on phones
Fix: the user block stays visible in the top bar. Status: fixed

### F-019 [low] Dashboard shows "0 ч" for short recordings
Fix: minutes under one hour. Status: fixed

### F-020 [low] Speaker-model status shown as raw JSON
Fix: "VAD ✓ · сегментиране ✓ · гласове ✓". Status: fixed

### F-021 [medium] Settings says the vLLM model is OK when the server is down
Cause: with `vllm_model` set, the status never contacted the server. Fix: the status probes `/models` and checks that the model exists. Status: fixed (verified with a dead port)

### F-022 [medium] Meeting shows "грешка" while an automatic retry is scheduled
Fix: status stays "в опашка" with "Нов опит след N мин (опит k от 3)". Test: `test_transient_failure_shows_retry_not_failed`. Verified: after the outage the retry finished by itself. Status: fixed

### F-023 [low] Raw OS errors ("WinError 10061 …") in the UI
Fix: readable Bulgarian messages for Whisper, vLLM and SMTP (auth, recipients, connection). Test: `test_smtp_down_gives_readable_error`. Status: fixed

### F-024 [medium] Intermittent "Server disconnected without sending a response"
Cause: uvicorn closes idle keep-alive connections after 5 s, which races with clients polling every 5–15 s (devices). Fix: keep-alive 65 s (server and panel). Not seen again in later runs. Status: fixed

### F-025 [medium] CLI prints Python tracebacks
Fix: one readable line ("Грешка: няма връзка със сървъра …", invalid TOML with position, missing file). Test: `test_cli_errors_are_readable`. Status: fixed

### F-026 [low] `bix server admin` asks for a password before checking the user exists
Fix: duplicate check first. Status: fixed

### F-027 [medium] Negative retention silently becomes 0 = "never delete"
Fix: 400 for negative values and for max duration outside 1–12 h. Test: `test_settings_validation`. Status: fixed

### F-028 [medium] Text in a number setting → 500
Fix: 400 with the field name. Test: `test_settings_validation`. Status: fixed

### F-029 [low] Right after enrolling, the panel shows "без връзка" for up to 15 s
Fix: immediate heartbeat after enrollment (`Agent.sync_now`). Verified: online with room within 1.5 s. Status: fixed

### F-030 [medium] Whisper sometimes writes whole segments in CAPITALS
Fix: segments that are ≥80% capitals become sentence case (words too, since speaker splits rebuild text from words). Tests: `test_fix_caps`, `test_fix_caps_words_keeps_sentence_starts`. Status: fixed

### F-031 [high] Search is case-sensitive for Cyrillic (SQLite)
Cause: SQLite's LIKE and lower() fold only ASCII, and SQLAlchemy renders ILIKE as LIKE on SQLite. Fix: Unicode `lower()` registered on SQLite connections + explicit `lower(x) LIKE lower(q)`; % and _ in the query are escaped. Test: `test_search_is_case_insensitive_for_cyrillic`. Status: fixed

### F-032 [low] Device enrollment throttle (10 per IP) could block an office rollout behind one NAT
Fix: 30 per IP per 15 min (codes have ~60 bits). Status: fixed

## Not bugs (checked)
- Hard power loss mid-meeting loses the last unsaved chunk (at most 45 s, usually under 20 s); everything before it is recovered and processed. By design; documented.
- libsndfile's Vorbis encoder crashes on long audio; Bix never writes Vorbis (Opus only). Reading OGG/Vorbis uploads works.

## Run 2 (11.10.2026)

### F-033 [medium] Portal shows the device's previous state (one heartbeat behind)
Steps: Devices → command "Започни запис" → the badge still says "онлайн"; after "Спри" it still says "записва".
Cause: the device reported state only on its next heartbeat, and the devices page redrew only every 15 s.
Fix: the device sends a heartbeat on every state change (`Agent._publish_state` → `sync_now`); the devices page redraws on the server's device event. Verified: badge follows in 0.3 s without reload. Status: fixed

### F-034 [low] "Покажи се" (identify) does nothing visible on devices without an LED ring
Fix: the room panel flashes a full-screen "Bix / <room>" for 5 s. Verified in the browser. Status: fixed

### F-035 [critical] Docker image cannot start: Permission denied on /data/server.toml
Where: `docker run … -v bix-data:/data bix-server`. Cause: the app runs as user `bix`, `/data` was root-owned. Every cloud deployment failed at first start.
Fix: `/data` created and owned by `bix` in the Dockerfile. Verified on vader: health OK, setup, a Cyrillic upload processed in the container in 42 s. Status: fixed

### F-036 [low] `bix doctor` fails on a server because it has no microphone
Fix: microphones are informational ("само за устройство"). Status: fixed

### F-037 [medium] install-device.sh aborts when /etc/udev/rules.d does not exist (minimal Ubuntu)
Fix: `install -D` for the udev rule and the systemd unit. Verified in a clean ubuntu:24.04 container: the script completes. Status: fixed

### F-038 [high] `bix-device` helper needs sudo, which minimal Ubuntu does not have
Fix: `runuser -u bix` (util-linux, always present). Verified: `bix-device mics` / `enroll` run in the clean container. Status: fixed

### F-039 [low] `bix device mics` prints nothing when there is no microphone
Fix: a clear message and exit code 1. Status: fixed

### F-040 [medium] One long word in a table cell makes the page wider than the window
Fix: all table cells wrap long values. Status: fixed

### F-041 [medium] A member's dashboard shows organisation-wide numbers
Steps: log in as a member who attended 1 of 5 meetings → "5 срещи", "2/2 устройства". The list shows 1. Leaks activity and contradicts the list.
Fix: members get counts for their own meetings only and no device statistics. Test in `test_roles_and_csrf`. Status: fixed

### F-042 [medium] Meetings cannot be opened with the keyboard
Cause: rows had click handlers but nothing focusable. Fix: the title is a real link. Verified: Tab reaches it, Enter opens the meeting. Status: fixed

### Checked in run 2 without findings
Portal → device commands (start, stop, mute, unmute, identify); "Не записвай" leaves nothing on device or server; double start/stop; two rooms recording at once; room edit, delete blocked while it has devices; meeting delete; person erase; user disable (session ends at once); password change (old password rejected, current tab stays logged in); member sees only attended meetings and can upload; audit filter and paging; live updates of list, live text and status in the portal without reload.
