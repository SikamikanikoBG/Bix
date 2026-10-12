import numpy as np
import soundfile as sf
from pathlib import Path

AT = Path(__file__).parent / "work"
F = AT / "files"
F.mkdir(exist_ok=True)
a, sr = sf.read(AT / "tm" / "meeting.wav", dtype="float32")
sf.write(F / "Среща с клиент №1 (телефон).mp3", a[: 16000 * 40], sr, format="MP3")
sf.write(F / "short.wav", a[:16000], sr)
sf.write(F / "silence.wav", np.zeros(16000 * 20, dtype=np.float32), sr)
(F / "notes.pdf").write_bytes(b"%PDF-1.4 fake")
(F / "empty.wav").write_bytes(b"")
st = np.stack([a[: 16000 * 30]] * 2, 1).repeat(3, 0)
sf.write(F / "stereo48k.wav", st, 48000)
print(sorted(p.name for p in F.iterdir()))
