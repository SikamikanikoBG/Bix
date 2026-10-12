# alpha-test notes for Bix

Read this first when running the `alpha-test` skill on Bix.

## Isolated instance

Everything runs from `alpha-test/harness/` with its state in `harness/work/` (git-ignored). The user's own instance (`bix dev`, ports 8710/8711) is never touched. `stop.ps1` only stops `bix server`, `bix device` and the SMTP sink.

| Part | Where |
|---|---|
| server | http://127.0.0.1:8720, `work/server/server.toml` |
| device + panel | http://127.0.0.1:8721, `work/device/device.toml`, file microphone (loops) |
| SMTP sink | 127.0.0.1:8025, mails saved as `.eml` in `work/mail/` |
| STT / LLM | ArDi Whisper (`http://<whisper-server>:9110`), vader vLLM (`http://<vllm-server>:8000/v1`); private, no cost |

One-time setup (PowerShell, from `software/`):

```powershell
uv pip install --python .venv playwright aiosmtpd edge-tts
.venv\Scripts\python -m playwright install chromium
$W = "alpha-test\harness\work"; mkdir $W\server, $W\device\models, $W\tm -Force
.venv\Scripts\python tools\make_test_meeting.py $W\tm            # synthetic Bulgarian meeting
copy $env:LOCALAPPDATA\Bix\server\models $W\server\ -Recurse      # or let `bix doctor` download
copy $W\server\models\silero_vad.onnx $W\device\models\
```

`work/server/server.toml`: port 8720, `public_url = "http://127.0.0.1:8720"`, `[stt] provider = "whisper_http"` with the ArDi URL, `[llm] provider = "vllm"` with the vader URL, `[smtp] host = "127.0.0.1", port = 8025, starttls = false`.

`work/device/device.toml`: `heartbeat_s = 5`, `[audio] input_device = "file:<abs path>/work/tm/meeting.wav"`, `[panel] port = 8721`.

Then `harness\start.ps1` (all) / `harness\stop.ps1 -what server|device|smtp|all`.

## Journeys (scripts in harness/, run with `.venv\Scripts\python -u <script>`)

| Script | Covers |
|---|---|
| j1_setup.py | first run, setup validation, XSS in org name, login/logout |
| j2_enroll.py, j2b.py | room with bad e-mail, enrollment code, enroll from the panel (wrong code, lower-case code) |
| j3_meeting.py | live text on, panel start/pause/resume/stop, processing, minutes refs, audio, export, send |
| j3c_crash.py | device killed mid-meeting → recovered and processed |
| j4_review.py, j4b_deeplink.py | export, send, name speakers (consent), regenerate, search; e-mail line links logged in/out |
| j5_upload.py (`mkfiles.py` first) | empty, PDF, 1 s, silence, 48 kHz stereo, Cyrillic MP3, corrupt OGG |
| j6_people.py | people, voice sample with consent, settings validation, all pages desktop + 390 px dark |
| j7_security.py | role × endpoint matrix, CSRF, bad IDs, headers, secrets, throttling, disabled user |
| j8.py | anonymous mode, settings validation, CLI errors |
| j9_deps.py, j9b_recover.py | Whisper/vLLM/SMTP down (start server with `BIX_STT_WHISPER_HTTP_URL=http://127.0.0.1:9` etc.), automatic retry after |
| j11_sweep.py | every page: horizontal overflow, console errors, screenshots in `work/shots/` |

## Run 2 additions

- `start.ps1 -what deviceB` starts a second device (panel :8722, `work/deviceB/device.toml`, same file mic) for two-room tests; `r2_r3_devices.py` enrolls it.
- Scripts that open a meeting read `work/mid.txt`; write it from a finished meeting first (see the one-liner at the end of run 2 in FINDINGS.md / r9c_ui.py).
- Live text is off in a fresh organisation: enable it (`PATCH /api/settings {"live_transcription": true}`) before R6.
- Docker and the device installer are tested on **vader** (`ssh <user>@<docker-host>`, has Docker): tar the source (without .venv/alpha-test) to `/tmp/bix-alpha`, `docker build -f deploy/docker/Dockerfile -t bix-alpha:test .`, run on port 18730 with `-e BIX_STT_WHISPER_HTTP_URL=http://<lan-host>:9110 -e BIX_LLM_VLLM_URL=http://<lan-host>:18022/v1`, then `r10_docker_flow.py`. Installer: `docker run --rm -v /tmp/bix-alpha:/src:ro ubuntu:24.04` with a stub `/usr/local/bin/systemctl`. **Remove the container, volume, image and /tmp files afterwards.**
- New scripts: r1_commands.py, r1b_identify.py, r2_r3_devices.py, r4_r8.py, r6_live_portal.py, r9b_latency.py (API latency while a 20-min meeting is processed), r9c_ui.py, r10_docker_flow.py, r11_keyboard.py.

## Things learned

- Don't run start/stop scripts with `subprocess.run(capture_output=True)`: the started server inherits the pipe and the call never returns. Use DEVNULL (see j3c_crash.py).
- The laptop microphone cancels audio played through its own speakers (echo cancellation), so acoustic tests need the file microphone.
- libsndfile's Vorbis encoder crashes on long audio: make test files as MP3/FLAC/WAV.
- Whisper output differs per run; check text with `in`, not equality.
- Long CPU work in the server process (speaker models hold the GIL) froze the API: always measure `/healthz` latency while a long meeting is processed (r9b_latency.py).
- The harness's dialog handler auto-accepts `window.prompt` with its default value; test prompt-based edits through the API.

## Daily-use additions (12.10.2026)

- `d1_day.py`: bookings in "Зала Витоша", start the booked meeting on the room screen (1280 x 720), ⭐, Ask Bix (`--ask` makes a real call: ~45 s of the file meeting, then the "decisions" preset), countdown, end screen.
- `d2_portal_calendar.py`: rooms → room calendar → booking dialog; checks the d1 meeting got the calendar title/attendees and was auto-sent (to the SMTP sink).
- `d3_sim.py`: the virtual device at `/sim` (3D model, real screen in an iframe, radar, Mute, lights).
- Don't stop processes by matching "bix.exe dev" in the command line: it also matches "bix.exe device" (the harness device). Use `stop.ps1` for the harness and match `dev --no-browser` for the user's instance.
