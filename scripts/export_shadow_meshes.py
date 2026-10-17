#!/usr/bin/env python3
"""Export light, untextured performer meshes for the shadow source in Max (jit.gl.model).

A shadow only needs the silhouette, so the 40 MB textured GLBs are reduced to
geometry only, decimated, and normalised the same way for every performer:
feet on y = 0, height 1, centred on x/z. Max can then place any performer with
the same @position / @scale.

Outputs models/shadow/<id>.obj (about 5 MB each at the default 80k faces).
Copy that folder next to the Max patch as `performers/`.

Usage:
  python3 scripts/export_shadow_meshes.py                  # every models/raw/*/*.glb
  python3 scripts/export_shadow_meshes.py --faces 40000 models/raw/violin/violin.glb

Requires: pip install trimesh numpy fast-simplification
"""
import argparse
from pathlib import Path

import fast_simplification
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent


def export(glb, out_dir, faces):
    scene = trimesh.load(glb, force="scene")
    mesh = trimesh.util.concatenate([g for g in scene.dump() if isinstance(g, trimesh.Trimesh)])
    v, f = np.asarray(mesh.vertices, dtype=np.float32), np.asarray(mesh.faces)
    if len(f) > faces:
        v, f = fast_simplification.simplify(v, f, target_reduction=1 - faces / len(f))
    lo, hi = v.min(0), v.max(0)
    v = (v - [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2]) / (hi[1] - lo[1])
    out = trimesh.Trimesh(v, f, process=False)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{glb.stem}.obj"
    out.export(path, include_normals=True)
    print(f"{glb.stem}: {len(mesh.faces)} -> {len(f)} faces, {path.stat().st_size / 1e6:.1f} MB")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("glbs", nargs="*", type=Path, help="default: models/raw/*/*.glb")
    p.add_argument("--faces", type=int, default=80000)
    p.add_argument("--out", type=Path, default=ROOT / "models" / "shadow")
    args = p.parse_args()
    glbs = args.glbs or sorted((ROOT / "models" / "raw").glob("*/*.glb"))
    for glb in glbs:
        export(glb, args.out, args.faces)


if __name__ == "__main__":
    main()
