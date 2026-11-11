"""Render preview PNGs of the Bix CRT enclosure parts (run from repo root)."""
import os, glob
import numpy as np, trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
STL = os.path.join(HERE, "Bix", "hardware", "enclosure", "stl")
OUT = os.path.join(HERE, "Bix", "hardware", "enclosure", "preview")
os.makedirs(OUT, exist_ok=True)

BG = "#101014"
parts = [
    ("bix_crt_shell.stl", "Shell"),
    ("bix_crt_bezel.stl", "Bezel"),
    ("bix_crt_top_cap.stl", "Top cap"),
    ("bix_crt_base.stl", "Base"),
    ("bix_crt_display_clamp_x4.stl", "Display clamp x4"),
    ("bix_crt_xvf_clamp_x4.stl", "XVF clamp x4"),
]

fig = plt.figure(figsize=(14, 8.5), facecolor=BG)
for i, (f, name) in enumerate(parts, 1):
    m = trimesh.load(os.path.join(STL, f), process=False)
    ax = fig.add_subplot(2, 3, i, projection="3d", facecolor=BG)
    v = m.vertices
    ax.plot_trisurf(v[:, 0], v[:, 1], v[:, 2], triangles=m.faces, color="#8fb7ff",
                    linewidth=0.15, edgecolor="#20304a", antialiased=True, alpha=0.97)
    ax.set_title(name, color="#e8eefc", fontsize=11, pad=2)
    ax.set_xticks([]); ax.set_yticks([]); ax.set_zticks([])
    for p in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
        p.fill = False; p.edgecolor = "#2a2a35"
    ax.view_init(elev=28, azim=-55)
fig.suptitle("Bix CRT enclosure - rev 0.1, six printable parts", color="#e8eefc", fontsize=14, y=0.965)
fig.tight_layout(rect=(0, 0, 1, 0.94))
p1 = os.path.join(OUT, "bix_parts.png")
fig.savefig(p1, dpi=125, facecolor=BG)
plt.close(fig)

fig2 = plt.figure(figsize=(12, 9), facecolor=BG)
ax = fig2.add_subplot(111, projection="3d", facecolor=BG)
zs = {"bix_crt_base.stl": 0, "bix_crt_bezel.stl": -18, "bix_crt_shell.stl": 20, "bix_crt_top_cap.stl": 170}
cols = {"bix_crt_base.stl": "#7fd1a8", "bix_crt_bezel.stl": "#ffb86b",
        "bix_crt_shell.stl": "#8fb7ff", "bix_crt_top_cap.stl": "#d9a5ff"}
for f, dz in zs.items():
    m = trimesh.load(os.path.join(STL, f), process=False)
    v = m.vertices + np.array([0, 0, dz])
    ax.plot_trisurf(v[:, 0], v[:, 1], v[:, 2], triangles=m.faces, color=cols[f],
                    linewidth=0.1, edgecolor="#1a2436", alpha=0.85)
ax.set_xticks([]); ax.set_yticks([]); ax.set_zticks([])
for p in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
    p.fill = False; p.edgecolor = "#2a2a35"
ax.view_init(elev=22, azim=-60)
ax.set_title("Exploded: bezel / base / shell / top cap  (mm)", color="#e8eefc", fontsize=12)
fig2.tight_layout()
p2 = os.path.join(OUT, "bix_exploded.png")
fig2.savefig(p2, dpi=125, facecolor=BG)
plt.close(fig2)
print("wrote", p1, p2)
