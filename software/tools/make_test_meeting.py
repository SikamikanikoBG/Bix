"""Generate a synthetic Bulgarian meeting for end-to-end tests.

Uses edge-tts (Microsoft neural voices bg-BG-Kalina / bg-BG-Borislav) with
pitch/rate variations to imitate four participants. Writes:

  <out>/meeting.wav        16 kHz mono
  <out>/reference.txt      the spoken text, one turn per line ("Име: текст")
  <out>/reference.json     turns with start/end seconds and speaker name

Usage:  python tools/make_test_meeting.py out_dir
Needs:  pip install edge-tts   (network access to Microsoft TTS)
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

VOICES = {
    "Мария": ("bg-BG-KalinaNeural", "+0Hz", "+0%"),
    "Иван": ("bg-BG-BorislavNeural", "+0Hz", "+0%"),
    "Елена": ("bg-BG-KalinaNeural", "-25Hz", "-8%"),
    "Георги": ("bg-BG-BorislavNeural", "-20Hz", "+6%"),
}

SCRIPT = [
    ("Мария", "Добро утро на всички. Днес имаме три точки: бюджетът за четвъртото тримесечие, пускането на новия сайт и наемането на още един разработчик."),
    ("Иван", "Добро утро. По бюджета: маркетингът е похарчил около осемдесет процента от планираното, така че имаме място за още една кампания."),
    ("Елена", "Предлагам да пуснем кампанията в началото на ноември, преди Черния петък. Мога да подготвя материалите до двадесет и пети октомври."),
    ("Мария", "Добре, решаваме така. Кампанията тръгва първи ноември, а Елена подготвя материалите до двадесет и пети октомври."),
    ("Георги", "По сайта имаме проблем. Плащанията с карта още не са тествани с банката и не знам дали ще успеем за датата."),
    ("Иван", "Кога точно е датата за пускане?"),
    ("Мария", "Петнадесети ноември. Георги, можеш ли да говориш с банката тази седмица и да ни кажеш до петък?"),
    ("Георги", "Да, ще се обадя утре и ще пиша на всички до петък."),
    ("Елена", "Остава въпросът дали сайтът ще бъде и на английски. Клиентите от Румъния питат за това."),
    ("Мария", "Това не можем да решим днес. Нека го обсъдим следващата седмица, когато имаме данни колко посетители идват от чужбина."),
    ("Иван", "За наемането: имаме двама добри кандидати. Предлагам да направим втори разговор и с двамата."),
    ("Мария", "Съгласна. Иван, организирай разговорите до края на месеца. Благодаря на всички, това беше всичко за днес."),
]


async def synth(text: str, voice: str, pitch: str, rate: str) -> np.ndarray:
    import edge_tts

    buf = io.BytesIO()
    comm = edge_tts.Communicate(text, voice, pitch=pitch, rate=rate)
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    audio, sr = sf.read(buf, dtype="float32", always_2d=True)
    audio = audio[:, 0]
    if sr != 16000:
        t_old = np.arange(len(audio)) / sr
        t_new = np.arange(int(len(audio) * 16000 / sr)) / 16000
        audio = np.interp(t_new, t_old, audio).astype(np.float32)
    return audio


async def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    pieces, turns, t = [], [], 0.0
    for name, text in SCRIPT:
        voice, pitch, rate = VOICES[name]
        audio = await synth(text, voice, pitch, rate)
        gap = np.zeros(int(16000 * rng.uniform(0.4, 1.1)), dtype=np.float32)
        pieces += [audio, gap]
        turns.append({"speaker": name, "start": round(t, 2), "end": round(t + len(audio) / 16000, 2), "text": text})
        t += (len(audio) + len(gap)) / 16000
    mix = np.concatenate(pieces)
    mix += rng.normal(0, 0.002, len(mix)).astype(np.float32)  # light room noise
    sf.write(out / "meeting.wav", mix, 16000)
    (out / "reference.txt").write_text("\n".join(f"{s}: {x}" for s, x in SCRIPT), encoding="utf-8")
    (out / "reference.json").write_text(json.dumps(turns, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out / 'meeting.wav'}: {len(mix) / 16000:.1f} s, {len(SCRIPT)} turns")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "test_meeting")))
