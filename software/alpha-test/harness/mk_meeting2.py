"""A second meeting with the same voices but different words (for voice-profile tests)."""
import asyncio
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from make_test_meeting import VOICES, synth  # noqa: E402

SCRIPT = [
    ("Мария", "Здравейте отново. Днес ще прегледаме какво стана с кампанията и докъде сме със сайта."),
    ("Иван", "Кампанията тръгна навреме и първата седмица имаме два пъти повече посещения от очакваното."),
    ("Мария", "Чудесно. А какво казаха от банката за плащанията с карта?"),
    ("Иван", "Тестовете минаха успешно в сряда, така че можем да пуснем сайта по план."),
    ("Мария", "Тогава потвърждаваме датата. Иван, моля те да изпратиш съобщение до клиентите в понеделник."),
    ("Иван", "Добре, ще го подготвя до петък и ще ти го пратя да го прегледаш."),
]


async def main(out: Path) -> None:
    pieces = []
    for name, text in SCRIPT:
        voice, pitch, rate = VOICES[name]
        pieces += [await synth(text, voice, pitch, rate), np.zeros(12000, dtype=np.float32)]
    sf.write(out, np.concatenate(pieces), 16000)
    print(out)


asyncio.run(main(Path(sys.argv[1])))
