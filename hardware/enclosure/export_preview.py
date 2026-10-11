"""Конвертира сглобените STL (stl/asm_*.stl) в компактен JSON за 3D прегледа в docs/."""
import json, os, glob
import numpy as np, trimesh
here = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(here, '..', '..', 'docs', 'stl')
os.makedirs(out, exist_ok=True)
for p in glob.glob(os.path.join(here, 'stl', 'asm_*.stl')):
    m = trimesh.load(p, process=True)
    d = {'s': 0.1, 'v': np.round(m.vertices * 10).astype(int).ravel().tolist(), 'f': m.faces.ravel().tolist()}
    name = os.path.basename(p).replace('.stl', '.json')
    open(os.path.join(out, name), 'w').write(json.dumps(d, separators=(',', ':')))
    print(name, len(m.faces), 'триъгълника')
