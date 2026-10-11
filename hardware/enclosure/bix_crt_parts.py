"""Bix CRT — параметричен корпус за 3D печат (FDM).

Генерира STL файловете на корпуса около реалните размери на:
  * Raspberry Pi Touch Display 2, 7"  — 189.5 x 120 x 15 мм, видима площ 155.5 x 88 мм
  * reSpeaker XVF3800 4-Mic Array     — 108 x 108 x 18 мм
  * Raspberry Pi 5 + Waveshare PoE+ HAT (F)
  * HLK-LD2410C mmWave радар, говорител 3 W / 4 Ω, Ø40 мм

Стартиране:  pip install manifold3d trimesh numpy && python3 bix_crt_parts.py
Всички мерки са в милиметри. Координати в сглобено състояние:
  X — ширина (дясно +), Y — дълбочина (0 = предна равнина на корпуса, + назад), Z — височина (0 = дъно на корпуса).

ПРЕДИ ПЕЧАТ проверете с шублер реалните платки и коригирайте параметрите с ТОДО.
"""
import math, os, sys
import numpy as np
import manifold3d as mf
import trimesh

M, CS, J = mf.Manifold, mf.CrossSection, mf.JoinType
mf.set_circular_segments(48)

# ---------------------------------------------------------------- параметри
W, H, D = 214.0, 176.0, 140.0   # външен размер на тялото (без основата)
R = 22.0                        # радиус на ъглите на фронталния профил
T = 2.4                         # дебелина на стената
CLR = 0.3                       # хлабина при сглобки
BEZEL_T = 4.0                   # дебелина на предния панел
LIP = 8.0                       # колко навлиза рамката в тялото

DISP_W, DISP_H = 189.5, 120.0   # Touch Display 2 (хоризонтално)
VIEW_W, VIEW_H = 155.5, 88.0    # видима площ
DISP_CZ = 85.0                  # височина на центъра на дисплея
VIEW_DX, VIEW_DZ = 0.0, 0.0     # ТОДО: отместване на видимата площ спрямо стъклото, ако не е центрирана
CLAMP_POST_H = 7.0              # ТОДО: височина на стойките за скобите = дебелина на ръба на дисплея

XVF = 108.0                     # reSpeaker XVF3800 — квадратна платка
TRAY_DEPTH = 22.0               # ТОДО: дълбочина на гнездото под капака (височина на платката + 4)
TOP_CY = 74.0                   # център на горния капак по Y
FLANGE = 132.0                  # размер на фланеца на капака
OPEN = 114.0                    # отвор в тялото за гнездото
MUTE_XY = (0.0, -44.0)          # ТОДО: позиция на бутона Mute на XVF3800 спрямо центъра (от STEP файла на Seeed)

SPK_D = 40.0                    # говорител Ø40
SPK_Y, SPK_Z = 100.0, 62.0      # позиция на говорителя на дясната стена

RADAR_W, RADAR_H = 36.0, 17.0   # гнездо за HLK-LD2410C / LD2410B
INSERT_M3 = 4.0                 # отвор за месингова втулка M3 (OD 4.2–4.6)
HOLE_M3 = 3.4
PILOT_M25 = 2.2                 # самонарезен M2.5 в пластмаса

BASE_W, BASE_D, BASE_H = 190.0, 120.0, 14.0

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stl')

# ---------------------------------------------------------------- помощни
def rrect(w, h, r):
    r = max(0.01, min(r, w / 2 - .01, h / 2 - .01))
    return CS.square((w - 2 * r, h - 2 * r), True).offset(r, J.Round, circular_segments=64)

def prism_y(w, h, r, y0, y1, cx=0.0, cz=None):
    """Заоблен правоъгълник в равнината XZ, изтеглен по Y от y0 до y1."""
    cz = H / 2 if cz is None else cz
    m = M.extrude(rrect(w, h, r), y1 - y0).rotate((-90, 0, 0))   # extrude по +Z -> по +Y
    return m.translate((cx, y0, cz))

def prism_z(w, d, r, z0, z1, cx=0.0, cy=0.0):
    """Заоблен правоъгълник в XY, изтеглен по Z."""
    return M.extrude(rrect(w, d, r), z1 - z0).translate((cx, cy, z0))

def box(x0, x1, y0, y1, z0, z1):
    return M.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))

def cyl_z(r, z0, z1, x, y, seg=32):
    return M.cylinder(z1 - z0, r, r, seg).translate((x, y, z0))

def cyl_y(r, y0, y1, x, z, seg=32):
    return M.cylinder(y1 - y0, r, r, seg).rotate((-90, 0, 0)).translate((x, y0, z))

def cyl_x(r, x0, x1, y, z, seg=32):
    return M.cylinder(x1 - x0, r, r, seg).rotate((0, 90, 0)).translate((x0, y, z))

def union(parts):
    return M.batch_boolean(list(parts), mf.OpType.Add)

def hex_holes(cx, cy, radius, pitch, hole_r, keep=lambda x, y: True):
    pts = []
    rows = int(radius * 2 / (pitch * .866)) + 2
    for j in range(-rows, rows + 1):
        y = j * pitch * .866
        off = (pitch / 2) if j % 2 else 0
        for i in range(-rows, rows + 1):
            x = i * pitch + off
            if x * x + y * y <= (radius - hole_r) ** 2 and keep(x, y):
                pts.append((cx + x, cy + y))
    return pts

# ---------------------------------------------------------------- 1. тяло
def shell():
    outer = prism_y(W, H, R, 0, D)
    inner = prism_y(W - 2 * T, H - 2 * T, R - T, -1, D - T)
    s = outer - inner
    add, cut = [], []
    zin_top, zin_bot = H - T, T
    # горни релси (по Y) за втулки M3 — държат капака
    rx = FLANGE / 2 - 4.5
    for sx in (-1, 1):
        add.append(box(sx * rx - 4, sx * rx + 4, LIP + 1, D - T + .1, zin_top - 9, zin_top + .1))
    # долни релси за основата
    for sx in (-1, 1):
        add.append(box(sx * 50 - 5, sx * 50 + 5, 18, D - T + .1, zin_bot - .1, zin_bot + 8))
    # пръстен за говорителя (дясна стена)
    xw = W / 2 - T
    add.append(cyl_x(SPK_D / 2 + 3, xw - 4, xw + .1, SPK_Y, SPK_Z, 64) - cyl_x(SPK_D / 2 + .4, xw - 5, xw + 1, SPK_Y, SPK_Z, 64))
    s = union([s] + add)
    # горен отвор + отвори за винтовете на капака
    cut.append(prism_z(OPEN, OPEN, 6, H - 12, H + 1, 0, TOP_CY))
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * rx, TOP_CY + sy * (FLANGE / 2 - 4.5)
            cut.append(cyl_z(INSERT_M3 / 2, zin_top - 7, H + 1, x, y, 24))
    # отвори за основата (отдолу) + винтове на предната рамка
    for sx in (-1, 1):
        for y in (40, 115):
            cut.append(cyl_z(INSERT_M3 / 2, -1, zin_bot + 6, sx * 50, y, 24))
        cut.append(cyl_z(HOLE_M3 / 2, -1, zin_bot + 1, sx * 60, LIP / 2, 24))
    # решетка на говорителя
    for (y, z) in hex_holes(SPK_Y, SPK_Z, SPK_D / 2 - 2, 4.2, 1.1):
        cut.append(cyl_x(1.1, W / 2 - T - 1, W / 2 + 1, y, z, 12))
    # заден панел: RJ45 keystone, USB-C, вентилация
    yb = D - T
    ks_x, ks_z = -40.0, 40.0
    cut.append(box(ks_x - 7.45, ks_x + 7.45, yb - 1, D + 1, ks_z - 8.15, ks_z + 8.15))      # keystone 14.9 x 16.3
    cut.append(box(ks_x - 12, ks_x + 12, yb - 1, yb + T - 1.8, ks_z - 13, ks_z + 13))        # стената изтънена до 1.8 мм около него
    usb_x, usb_z = 10.0, 30.0
    cut.append(prism_y(12.4, 6.0, 2.8, yb - 1, D + 1, usb_x, usb_z))
    for dx in (-11, 11):
        cut.append(cyl_y(1.6, yb - 1, D + 1, usb_x + dx, usb_z, 16))
    for i in range(9):                                       # горни вентилационни прорези
        x = -48 + i * 12
        cut.append(prism_y(4, 46, 2, yb - 1, D + 1, x, 128))
    for i in range(7):                                       # долни прорези (под основата)
        x = -36 + i * 12
        cut.append(prism_z(4, 60, 2, -1, T + 1, x, 78))
    # гравиран надпис отзад
    s = s - union(cut)
    return s

# ---------------------------------------------------------------- 2. предна рамка
def bezel():
    plate = prism_y(W, H, R, -BEZEL_T, 0)
    win = prism_y(VIEW_W + 1, VIEW_H + 1, 2.5, -BEZEL_T - 1, 1, VIEW_DX, DISP_CZ + VIEW_DZ)
    # фаска около прозореца (по-широк отвор отпред)
    win_ch = prism_y(VIEW_W + 5, VIEW_H + 5, 4.5, -BEZEL_T - 1, -BEZEL_T + 1.2, VIEW_DX, DISP_CZ + VIEW_DZ)
    iw, ih = W - 2 * T - 2 * CLR, H - 2 * T - 2 * CLR
    lip = prism_y(iw, ih, R - T - CLR, -.1, LIP) - prism_y(iw - 4, ih - 4, R - T - CLR - 2, -1, LIP + 1)
    # рамка, която центрира стъклото на дисплея
    fw, fh = DISP_W + 1, DISP_H + 1
    frame = prism_y(fw + 4, fh + 4, 3, -.1, 5, 0, DISP_CZ) - prism_y(fw, fh, 1, -1, 6, 0, DISP_CZ)
    # стойки за 4 скоби
    posts, holes = [], []
    px, pz = DISP_W / 2 + 5.5, 40.0
    for sx in (-1, 1):
        for sz in (-1, 1):
            posts.append(cyl_y(4, -.1, CLAMP_POST_H, sx * px, DISP_CZ + sz * pz))
            holes.append(cyl_y(PILOT_M25 / 2, 0, CLAMP_POST_H + 1, sx * px, DISP_CZ + sz * pz, 16))
    # гнездо за радара (под дисплея, по средата)
    rz = 14.0
    radar = prism_y(RADAR_W + 3, RADAR_H + 3, 1.5, -.1, 3, 0, rz) - prism_y(RADAR_W, RADAR_H, .8, -1, 4, 0, rz)
    # блокове за долните винтове M3 (втулки отдолу)
    zb = T + CLR
    blocks = [box(sx * 60 - 6, sx * 60 + 6, 0, LIP, zb, zb + 9) for sx in (-1, 1)]
    b = union([plate, lip, frame, radar] + posts + blocks)
    cut = [win, win_ch] + holes + [cyl_z(INSERT_M3 / 2, zb - 1, zb + 7, sx * 60, LIP / 2, 24) for sx in (-1, 1)]
    # тънка зона пред радара (1.2 мм) — mmWave минава през пластмаса, но по-тънко е по-добре
    cut.append(prism_y(RADAR_W - 2, RADAR_H - 2, .8, -BEZEL_T + 1.2, .1, 0, rz))
    return b - union(cut)

# ---------------------------------------------------------------- 3. горен капак с гнездо за микрофоните
def top_cap():
    z0 = H
    flange = prism_z(FLANGE, FLANGE, 10, z0, z0 + 3, 0, TOP_CY)
    tray_o = prism_z(OPEN - 2 * .5, OPEN - 2 * .5, 5.5, z0 - TRAY_DEPTH, z0 + .1, 0, TOP_CY)
    tray_i = prism_z(XVF + 1, XVF + 1, 3, z0 - TRAY_DEPTH - 1, z0 - 1.2, 0, TOP_CY)
    c = flange + (tray_o - tray_i)
    cut = []
    # прорез за кабелите (USB-C, говорител) към задната страна
    cut.append(box(-30, 30, TOP_CY + XVF / 2 - 1, TOP_CY + OPEN / 2 + 1, z0 - TRAY_DEPTH - 1, z0 - 8))
    # 4 винта M3 с вдлъбнатина
    r = FLANGE / 2 - 4.5
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * r, TOP_CY + sy * r
            cut.append(cyl_z(HOLE_M3 / 2, z0 - 1, z0 + 4, x, y, 24))
            cut.append(M.cylinder(1.8, HOLE_M3 / 2, 3.2, 24).translate((x, y, z0 + 1.21)))
    # бутон Mute — гъвкав език (U-прорез) с издатък отдолу
    mx, my = MUTE_XY[0], TOP_CY + MUTE_XY[1]
    tw, tl, g = 14.0, 12.0, 1.0
    ucut = box(mx - tw / 2 - g, mx + tw / 2 + g, my - tl / 2 - g, my + tl / 2, z0 - 1.3, z0 + 4) - \
        box(mx - tw / 2, mx + tw / 2, my - tl / 2, my + tl / 2 + 1, z0 - 2, z0 + 5)
    cut.append(ucut)
    # перфорация за звук и светлина от 12-те LED на платката
    keep = lambda x, y: not (abs(x - MUTE_XY[0]) < tw / 2 + 3 and abs(y - MUTE_XY[1]) < tl / 2 + 3)
    for (x, y) in hex_holes(0, TOP_CY, 46, 3.6, .9, keep):
        cut.append(cyl_z(.9, z0 - 1.5, z0 + 4, x, y, 10))
    c = c - union(cut)
    # издатък под езика + маркировка с вдлъбнат кръг отгоре
    nub = cyl_z(2.0, z0 - 1.2 - 3.5, z0 - 1.2 + .1, mx, my - 1.5, 24)
    dimple = cyl_z(4.0, z0 + 2.4, z0 + 3.1, mx, my - 1.5, 32)
    return (c + nub) - dimple

# ---------------------------------------------------------------- 4. основа
def base():
    cy = 18 + BASE_D / 2 - 5
    outer = prism_z(BASE_W, BASE_D, 14, -BASE_H, 0, 0, cy)
    hollow = prism_z(BASE_W - 5, BASE_D - 5, 11.5, -BASE_H - 1, -3, 0, cy)
    b = outer - hollow
    cut = []
    for sx in (-1, 1):
        for y in (40, 115):
            cut.append(cyl_z(HOLE_M3 / 2, -4, 1, sx * 50, y, 24))
    for i in range(7):  # вентилация, съвпада с тялото
        cut.append(prism_z(4, 60, 2, -4, 1, -36 + i * 12, 78))
    for i in range(10):  # странични прорези за въздух
        y = cy - 40 + i * 9
        cut.append(box(-BASE_W / 2 - 1, -BASE_W / 2 + 4, y - 2, y + 2, -BASE_H + 3, -5))
        cut.append(box(BASE_W / 2 - 4, BASE_W / 2 + 1, y - 2, y + 2, -BASE_H + 3, -5))
    b = b - union(cut)
    # 4 кухини за гумени крачета Ø10 (отдолу на ръба) — ребра
    feet = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * (BASE_W / 2 - 12), cy + sy * (BASE_D / 2 - 12)
            feet.append(cyl_z(7, -BASE_H, -3, x, y, 32) - cyl_z(5.1, -BASE_H - 1, -BASE_H + 1.2, x, y, 32))
    return union([b] + feet)

# ---------------------------------------------------------------- 5. скоби
def clamp():
    c = prism_z(18, 9, 2, 0, 2.4, 0, 0)
    return c - cyl_z(1.4, -1, 4, -3.5, 0, 16)  # отвор M2.5, ръбът стъпва 5 мм върху стъклото

def xvf_clamp():
    """Ъглова скоба, която държи платката XVF3800 в гнездото (винт M2.5 в стената)."""
    c = box(0, 14, 0, 14, 0, 2.4) - box(4.5, 15, 4.5, 15, -1, 3.4)
    c = c + box(0, 14, 0, 4.5, 0, 2.4) + box(0, 4.5, 0, 14, 0, 2.4)
    return c - cyl_z(1.4, -1, 4, 2.2, 2.2, 16)

# ---------------------------------------------------------------- изход
def to_trimesh(man):
    mesh = man.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3]
    f = np.asarray(mesh.tri_verts)
    return trimesh.Trimesh(vertices=v, faces=f, process=False)

def lay_flat(man, part):
    """Ориентация за печат: най-голямата равна стена на леглото."""
    if part == 'shell':          # отзад на леглото, отворът нагоре
        man = man.rotate((-90, 0, 0))
    elif part == 'bezel':        # лицето на леглото
        man = man.rotate((90, 0, 0))
    elif part == 'top':          # фланецът на леглото
        man = man.rotate((180, 0, 0))
    elif part == 'base':         # горната повърхност на леглото
        man = man.rotate((180, 0, 0))
    bb = man.bounding_box()
    return man.translate((-(bb[0] + bb[3]) / 2, -(bb[1] + bb[4]) / 2, -bb[2]))

PARTS = {
    'bix_crt_shell': ('shell', shell),
    'bix_crt_bezel': ('bezel', bezel),
    'bix_crt_top_cap': ('top', top_cap),
    'bix_crt_base': ('base', base),
    'bix_crt_display_clamp_x4': ('clamp', clamp),
    'bix_crt_xvf_clamp_x4': ('clamp', xvf_clamp),
}

if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    preview = {}
    for name, (kind, fn) in PARTS.items():
        man = fn()
        assert man.status() == mf.Error.NoError, (name, man.status())
        assembled = to_trimesh(man)
        preview[name] = assembled
        tm = to_trimesh(lay_flat(man, kind))
        tm.export(os.path.join(OUT, name + '.stl'))
        bb = tm.bounds
        print(f"{name:28s} {bb[1][0]-bb[0][0]:6.1f} x {bb[1][1]-bb[0][1]:6.1f} x {bb[1][2]-bb[0][2]:6.1f} mm  "
              f"vol {man.volume()/1000:6.1f} cm3  tris {len(tm.faces):6d}  watertight {tm.is_watertight}")
    # сглобен модел за прегледа в браузъра (в координатите на сглобката)
    if '--preview' in sys.argv:
        for name, tm in preview.items():
            tm.export(os.path.join(OUT, 'asm_' + name + '.stl'))
