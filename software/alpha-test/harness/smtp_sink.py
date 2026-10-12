import asyncio, sys, time
from pathlib import Path
from aiosmtpd.controller import Controller
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
class H:
    async def handle_DATA(self, server, session, envelope):
        (out / f"{time.time():.3f}.eml").write_bytes(b"X-Rcpt: " + ",".join(envelope.rcpt_tos).encode() + b"\r\n" + envelope.content)
        return "250 OK"
c = Controller(H(), hostname="127.0.0.1", port=8025); c.start()
while True: time.sleep(3600)
