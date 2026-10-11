# Bix CRT — корпус за 3D печат

Ревизия 0.1 · 10.10.2026

```
Файлове (ориентирани за печат, мм):
  bix_crt_shell.stl            тяло         — гърбът на леглото, поддръжки само за горния отвор
  bix_crt_bezel.stl            предна рамка — лицето на леглото
  bix_crt_top_cap.stl          капак с гнездо за reSpeaker XVF3800 — прозрачен/natural PETG
  bix_crt_base.stl             основа
  bix_crt_display_clamp_x4.stl скоба за дисплея — печатайте 4 бр.
  bix_crt_xvf_clamp_x4.stl     скоба за микрофонния масив — печатайте 4 бр.
  bix_crt_parts.py             параметричен генератор (pip install manifold3d trimesh numpy)

Настройки: PETG, 0,2 мм, 4 периметъра, 20% gyroid, 240/80 °C, легло поне 220 x 220 мм.
Втулки: 10 x M3 месингови (отвор Ø4,0). Винтове: M3 x 8 (капак, 4), M3 x 10 (рамка, 2), M3 x 16 (основа, 4), M2.5 x 6 (скоби, 8).

ПРЕДИ ПЕЧАТ: проверете с шублер параметрите с ТОДО в bix_crt_parts.py
(позиция на бутона Mute на XVF3800, дълбочина на гнездото, видимата площ и ръба на дисплея).
Първият печат е тестов. За серия: огнеустойчив материал (UL94 V-0).
```

## Генериране

```bash
pip install manifold3d trimesh numpy
python3 bix_crt_parts.py --preview      # STL в ./stl, сглобен модел за сайта
python3 export_preview.py               # обновява ../../docs/stl/*.json
```
