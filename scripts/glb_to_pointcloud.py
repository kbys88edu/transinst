#!/usr/bin/env python3
"""Turn performer GLBs into fixed-size point clouds for Max/Jitter.

Every model is sampled to the same number of points (W x H), normalised to the
same height and ordered the same way (by height band, then angle around the
body), so point i of one performer roughly matches point i of another. That
makes "morph into another person" a plain per-point mix in jit.gen, and
melting / scattering are per-point displacements.

Outputs per performer, in models/points/:
  <id>_pos.jxf   float32, 3 planes (x y z), dim W x H
  <id>_col.jxf   float32, 4 planes (r g b a), dim W x H, greyscale
  <id>.npz       same data for Python tools

Usage:
  python3 scripts/glb_to_pointcloud.py                 # every models/raw/*/*.glb
  python3 scripts/glb_to_pointcloud.py models/raw/violin/violin.glb --size 512 512

Requires: pip install trimesh numpy pillow
"""
import argparse
import struct
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parent.parent
HEIGHT_BANDS = 64


def write_jxf(path, data):
    """Write a float32 array shaped (H, W, planes) as a Jitter .jxf matrix (big-endian)."""
    h, w, planes = data.shape
    dims = (w, h)  # Jitter dim 0 is the fastest-varying axis
    header_len = 24 + 4 * len(dims)
    payload = data.astype(">f4").tobytes()  # row-major (H, W, planes) == Jitter cell order
    mtrx = struct.pack(">4sII4sII", b"MTRX", header_len + len(payload), header_len, b"FL32", planes, len(dims))
    mtrx += struct.pack(f">{len(dims)}I", *dims) + payload
    fver = struct.pack(">4sII", b"FVER", 12, 0x3C93DC80)
    form = struct.pack(">4sI4s", b"FORM", 12 + len(fver) + len(mtrx), b"JIT!")
    path.write_bytes(form + fver + mtrx)


def load_mesh(path):
    scene = trimesh.load(path, force="scene")
    meshes = [g for g in scene.dump() if isinstance(g, trimesh.Trimesh)]  # world transforms applied
    if not meshes:
        raise ValueError(f"{path}: no triangle meshes")
    return meshes


def surface_colour(mesh, points, faces):
    """RGB 0..1 at each sampled point from the base-colour texture (Meshy GLBs use PBR materials)."""
    visual = mesh.visual
    material = getattr(visual, "material", None)
    image = getattr(material, "baseColorTexture", None) or getattr(material, "image", None)
    factor = getattr(material, "baseColorFactor", None)
    factor = np.asarray([255] * 4 if factor is None else factor, dtype=np.float32)[:3]
    factor = factor / 255.0 if factor.max() > 1 else factor
    uv = getattr(visual, "uv", None)
    if image is None or uv is None:
        return np.tile(factor, (len(points), 1))

    tri = mesh.vertices[mesh.faces[faces]]
    bary = trimesh.triangles.points_to_barycentric(tri, points)
    st = np.einsum("ij,ijk->ik", bary, uv[mesh.faces[faces]])
    tex = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    h, w = tex.shape[:2]
    x = (np.mod(st[:, 0], 1) * (w - 1)).round().astype(int)
    y = ((1 - np.mod(st[:, 1], 1)) * (h - 1)).round().astype(int)
    return tex[y, x] * factor


def sample(meshes, count, seed):
    """Sample `count` surface points across all meshes, area-weighted, with greyscale colour."""
    areas = np.array([m.area for m in meshes])
    counts = np.random.default_rng(seed).multinomial(count, areas / areas.sum())
    pts, cols = [], []
    for m, n in zip(meshes, counts):
        if n == 0:
            continue
        p, faces = trimesh.sample.sample_surface(m, n, seed=seed)
        pts.append(p)
        cols.append(surface_colour(m, p, faces))
    pts = np.concatenate(pts).astype(np.float32)
    grey = np.concatenate(cols) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    return pts, grey


def normalise(pts):
    """Feet on y=-1, head on y=+1, centred on x/z (GLB is y-up)."""
    lo, hi = pts.min(0), pts.max(0)
    centre = (lo + hi) / 2
    centre[1] = lo[1]
    return (pts - centre) * (2.0 / (hi[1] - lo[1])) - np.array([0, 1, 0], dtype=np.float32)


def order(pts):
    """Consistent ordering across performers: height band, then angle around the vertical axis."""
    band = np.minimum((np.argsort(np.argsort(pts[:, 1])) * HEIGHT_BANDS) // len(pts), HEIGHT_BANDS - 1)
    angle = np.arctan2(pts[:, 2], pts[:, 0])
    return np.lexsort((angle, band))


def convert(glb, out_dir, w, h, seed):
    pts, grey = sample(load_mesh(glb), w * h, seed)
    pts = normalise(pts)
    idx = order(pts)
    pts, grey = pts[idx], grey[idx]
    col = np.stack([grey, grey, grey, np.ones_like(grey)], axis=1)

    name = glb.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jxf(out_dir / f"{name}_pos.jxf", pts.reshape(h, w, 3))
    write_jxf(out_dir / f"{name}_col.jxf", col.reshape(h, w, 4))
    np.savez_compressed(out_dir / f"{name}.npz", pos=pts, grey=grey, size=np.array([w, h]))
    print(f"{glb.relative_to(ROOT) if glb.is_relative_to(ROOT) else glb} -> {out_dir / name}_*.jxf ({w}x{h})")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("glbs", nargs="*", type=Path, help="default: models/raw/*/*.glb")
    p.add_argument("--size", nargs=2, type=int, default=[256, 256], metavar=("W", "H"))
    p.add_argument("--out", type=Path, default=ROOT / "models" / "points")
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()

    glbs = args.glbs or sorted((ROOT / "models" / "raw").glob("*/*.glb"))
    if not glbs:
        raise SystemExit("no GLB files found (run scripts/meshy_generate.py first)")
    for glb in glbs:
        convert(glb.resolve(), args.out, *args.size, args.seed)


if __name__ == "__main__":
    main()
