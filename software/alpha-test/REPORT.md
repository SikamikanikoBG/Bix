# Bix · alpha-test report

11.10.2026 · build 0.1.0 (uncommitted working tree) · tester: Claude (alpha-test skill)

## Verdict: GO for UAT (internal pilot), with the open points below

All 32 findings are fixed and verified, including one critical: enrolling from the room panel was impossible. A full regression pass on a fresh instance found no new critical or high issues. Auth and data deletion passed twice. The automated suite grew from 16 to 31 tests and is green.

## Scope and environment

- Isolated instance on the laptop (Windows 11): server :8720 with SQLite and local storage, device agent and panel :8721 with a looping file microphone, local SMTP sink. The user's instance on :8710/:8711 was not used for testing; it was only upgraded and smoke-checked read-only at the end.
- Real dependencies: ArDi Whisper (speech to text), vader vLLM (Qwen 3.8 27B, minutes), sherpa-onnx models (speakers).
- UI driven in headless Chromium (Playwright): screenshots reviewed by eye, console and HTTP errors captured. Desktop 1360 px light and phone 390 px dark.
- API driven with httpx for every role. CLI run with good and bad arguments.

## Journeys covered

| Journey | Result |
|---|---|
| First run, login, logout, password rules | pass |
| Rooms, enrollment codes, enroll from the panel and the CLI, revoke | pass after F-003, F-004, F-029 |
| Meeting in the room: live text, pause, resume, stop, AI notice | pass |
| Processing → transcript, speakers, minutes with line references, audio | pass after F-030 |
| Review: line links, speakers with consent, regenerate, title edit, export, send | pass after F-006, F-007, F-012 |
| Links from the e-mail (logged in / out) | pass after F-008 |
| Upload: empty, PDF, too short, silence, 48 kHz stereo, Cyrillic MP3, corrupt file | pass after F-010, F-011 |
| People and voice profiles, GDPR erasure | pass |
| Roles: admin / member / auditor / anonymous × every sensitive endpoint | pass after F-014 |
| Security: CSRF, XSS (portal, panel, e-mail), bad IDs, LIKE injection, headers, secrets, throttling | pass after F-015, F-032 |
| Settings and policy reach the device; validation; anonymous mode | pass after F-027, F-028 |
| Retention deletes audio files, then the whole meeting | pass (files checked on disk) |
| Whisper / vLLM / SMTP down → readable error → automatic retry → success | pass after F-021, F-022, F-023 |
| Server down mid-meeting → offline buffer → upload when it's back | pass |
| Device killed mid-meeting → recovered and processed | pass (last unsaved chunk lost, as designed) |
| Upgrading an existing database | pass after F-013 (verified on the user's real DB) |
| Every page on phone (dark) and desktop | pass after F-001, F-016, F-018 |
| CLI | pass after F-025, F-026 |

## Numbers

| Severity | Found | Fixed | Open |
|---|---|---|---|
| critical | 1 | 1 | 0 |
| high | 5 | 5 | 0 |
| medium | 16 | 16 | 0 |
| low | 10 | 10 | 0 |
| **total** | **32** | **32** | **0** |

Details: [FINDINGS.md](FINDINGS.md). How to re-run: [PROJECT.md](PROJECT.md) and `harness/`.

## Open questions (product decisions)

1. **Speaker separation quality.** The synthetic test voices are pitch-shifted copies of two TTS voices, so they say little about real people. Needs 10 real meetings (Phase 1) to tune `diarization.threshold`.
2. **Titles from file names.** An upload without a typed title is named after the file and may be replaced by the generated title. Is that what users expect, or should a file name count as "typed"?
3. **Member visibility.** By default members see only meetings they attended (by calendar e-mail or a recognised voice). Without Microsoft 365 or voice profiles, members see almost nothing until an admin enables "members see all".
4. **Live-text cost.** Live text is on in the user's instance. That is cheap on own GPUs, but on Azure it is about 4× the post-meeting cost.

## Not tested (no access or no hardware)

- Raspberry Pi 5, reSpeaker XVF3800 (LEDs, direction), LD2410C radar, SDL2 face on the 7" display.
- Azure Speech, Amazon Bedrock, Microsoft Graph (calendar, sendMail), OIDC SSO with a real tenant, S3/KMS, PostgreSQL, the Docker image, `install-device.sh` on a clean Pi/Ubuntu.
- Load: many devices at once, meetings over one hour.
- Real people in a real room (acoustic echo, distance, overlapping speech).


---

# Run 2 · 11.10.2026

## Verdict: GO for UAT (internal pilot)

Run 2 covered what run 1 did not. It found 12 new issues and all are fixed. Two were blockers for real deployments: the Docker image could not start (F-035), and the whole server froze for about 2 minutes while a long meeting was processed (F-043). The regression pass on the final build is clean, and the automated suite has 32 tests, all green.

## Covered in run 2

| Area | Result |
|---|---|
| Portal → device commands (start, stop, mute, unmute, identify) | pass after F-033, F-034; command latency = one heartbeat (≈ 5 s in test, up to 15 s with the default config) |
| "Не записвай", double start/stop, two rooms recording at once | pass |
| Room edit/delete, meeting delete, person erase, user disable, password change | pass |
| Member journey (only attended meetings, upload, read-only settings) | pass after F-041 |
| Live updates in the portal (list, live text, status) without reload | pass after F-033 |
| Audit filter and paging | pass |
| 19-minute meeting: upload 13 s, processing 5.4 min, 326 transcript lines render in 0.2 s | pass after F-043, F-044 |
| Docker image on Ubuntu (vader, throw-away): health, setup, Cyrillic upload processed in the container, isolated processing on Linux | pass after F-035, F-036 |
| install-device.sh on clean ubuntu:24.04 | pass after F-037, F-038, F-039 (systemd stubbed: no systemd in the container) |
| Keyboard only: login, navigation, open a meeting, start/stop in the panel | pass after F-042 |

## Numbers (run 1 + run 2)

| Severity | Found | Fixed | Open |
|---|---|---|---|
| critical | 2 | 2 | 0 |
| high | 7 | 7 | 0 |
| medium | 23 | 23 | 0 |
| low | 12 | 12 | 0 |
| **total** | **44** | **44** | **0** |

## Notes for the product owner

- **Command latency.** Portal commands reach a device at its next heartbeat: up to 15 s with the default `heartbeat_s`. Fine for start/stop; if it should feel instant, the next step is a long-poll or WebSocket channel to the device.
- **Docker image size** is 1.17 GB (ffmpeg, sherpa-onnx, numpy). Fine for a server; a slimmer image without ffmpeg is possible if M4A/MP4 uploads aren't needed.
- **Processing time** grows with meeting length: about 0.28× real time on this laptop for speakers + ArDi for text. A one-hour meeting takes roughly 15–20 min on a laptop-class CPU; a server with more cores, or Azure's built-in speaker separation, shortens it.

## Still not tested

Raspberry Pi hardware (XVF3800, radar, SDL2 face on the display); systemd itself (the container had none); Azure Speech, Bedrock, Microsoft 365, SSO with a real tenant, S3/KMS, PostgreSQL (the Docker test used SQLite on a volume); docker-compose with Caddy and TLS (needs a public domain); load with 20+ devices; real people in a real room.
