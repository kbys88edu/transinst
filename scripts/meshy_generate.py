#!/usr/bin/env python3
"""Generate one 3D model per performer in models/manifest.json with the Meshy text-to-3D API.

Each performer runs preview (geometry) -> refine (texture) and saves:
  models/raw/<id>/<id>.glb, preview.png, task.json

Re-running skips performers whose GLB already exists and resumes
in-flight tasks recorded in task.json, so an interrupted batch costs nothing extra.

Auth: MESHY_API_KEY, or a "Bearer" network secret for api.meshy.ai on the
cloud environment (the proxy adds the header, so no key is needed here).

Usage:
  MESHY_API_KEY=... python3 scripts/meshy_generate.py --tier 1
  python3 scripts/meshy_generate.py --ids violin cello --dry-run
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "models" / "manifest.json"
API = "https://api.meshy.ai/openapi/v2/text-to-3d"
MAX_PROMPT = 600  # Meshy rejects longer prompts


def build_prompt(manifest, performer):
    parts = [performer["subject"], manifest["wardrobe"][performer["wardrobe"]], manifest["style"]["suffix"]]
    prompt = ", ".join(parts)
    if len(prompt) > MAX_PROMPT:
        raise ValueError(f"{performer['id']}: prompt is {len(prompt)} chars (max {MAX_PROMPT})")
    return prompt


def request(method, url, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key:  # without a key, the cloud environment's network secret adds the header
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} -> {e.code}: {e.read().decode(errors='replace')}") from None


def wait(task_id, key, label):
    while True:
        task = request("GET", f"{API}/{task_id}", key)
        status = task.get("status")
        print(f"  {label}: {status} {task.get('progress', 0)}%", flush=True)
        if status == "SUCCEEDED":
            return task
        if status in ("FAILED", "CANCELED", "EXPIRED"):
            raise RuntimeError(f"{label} {status}: {task.get('task_error')}")
        time.sleep(10)


def download(url, path):
    with urllib.request.urlopen(url, timeout=300) as res, open(path, "wb") as f:
        f.write(res.read())


def generate(manifest, performer, args, key):
    pid = performer["id"]
    out = args.out / pid
    glb = out / f"{pid}.glb"
    if glb.exists():
        print(f"[skip] {pid}: {glb.relative_to(ROOT)} exists")
        return
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / "task.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    state["prompt"] = build_prompt(manifest, performer)

    def save():
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))

    print(f"[gen] {pid} ({performer['ja']})")
    if "preview_id" not in state:
        body = {
            "mode": "preview",
            "prompt": state["prompt"],
            "ai_model": args.ai_model,
            "topology": "triangle",
            "should_remesh": True,
            "target_polycount": args.polycount,
        }
        state["preview_id"] = request("POST", API, key, body)["result"]
        save()
    final = wait(state["preview_id"], key, "preview")

    if not args.no_refine:
        if "refine_id" not in state:
            body = {"mode": "refine", "preview_task_id": state["preview_id"], "enable_pbr": False}
            state["refine_id"] = request("POST", API, key, body)["result"]
            save()
        final = wait(state["refine_id"], key, "refine")

    download(final["model_urls"]["glb"], glb)
    if final.get("thumbnail_url"):
        download(final["thumbnail_url"], out / "preview.png")
    state["done"] = True
    save()
    print(f"  -> {glb.relative_to(ROOT)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ids", nargs="*", help="performer ids (default: all, filtered by --tier)")
    p.add_argument("--tier", type=int, help="only performers with this tier (1 = one per section, try first)")
    p.add_argument("--out", type=Path, default=ROOT / "models" / "raw")
    p.add_argument("--ai-model", default="latest")
    p.add_argument("--polycount", type=int, default=30000)
    p.add_argument("--no-refine", action="store_true", help="geometry only, no texture (cheaper)")
    p.add_argument("--dry-run", action="store_true", help="print prompts without calling the API")
    args = p.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    performers = manifest["performers"]
    if args.ids:
        known = {x["id"] for x in performers}
        unknown = set(args.ids) - known
        if unknown:
            sys.exit(f"unknown ids: {', '.join(sorted(unknown))}")
        performers = [x for x in performers if x["id"] in args.ids]
    if args.tier is not None:
        performers = [x for x in performers if x["tier"] == args.tier]

    if args.dry_run:
        for x in performers:
            prompt = build_prompt(manifest, x)
            print(f"{x['id']} ({len(prompt)} chars)\n  {prompt}\n")
        return

    key = os.environ.get("MESHY_API_KEY")
    if not key:
        print("MESHY_API_KEY not set; relying on a network secret for api.meshy.ai", file=sys.stderr)
    failed = []
    for x in performers:
        try:
            generate(manifest, x, args, key)
        except Exception as e:  # keep going so one failure doesn't stop the batch
            print(f"  !! {x['id']}: {e}", file=sys.stderr)
            failed.append(x["id"])
    if failed:
        sys.exit(f"failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
