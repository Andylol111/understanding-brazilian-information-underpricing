"""Verified local staging of the files a replay reads.

Why: the external SSD holding `data/` returned wrong bytes on a few large reads (2026-10-27: four
mismatches against SHA-256, each correct on reread; no I/O errors logged). A registered replay gets one
look, so it must not read through that drive. `stage(cfg_path, dest)` copies exactly the files the
config's replay reads to `dest` (local disk) and verifies every copy:
  - files in the data manifest (`<DATA>/MANIFEST.sha256`): the local copy must hash to the manifest;
  - files not in it yet (e.g. runoff BU-web files arriving later): the source must hash identically on
    two reads, the copy must match, and the hash is appended to the manifest.
Each file is retried up to `tries` times before failing. `dest/STAGED.json` records the config's hash
and every file's hash; `analysis/night_replay.py` refuses a registered config unless BRUNDER_DATA points
at a stage made for that exact config.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from . import paths

MANIFEST = "MANIFEST.sha256"
STAGED = "STAGED.json"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(root: Path) -> dict[str, str]:
    m = root / MANIFEST
    if not m.exists():
        return {}
    out = {}
    for line in m.read_text().splitlines():
        digest, rel = line.split("  ", 1)
        out[rel.removeprefix("./")] = digest
    return out


def files_for_config(cfg_path: Path, root: Path | None = None) -> list[str]:
    """Paths (relative to the data root) that night.replay / analysis/night_replay.py read."""
    root = root or paths.DATA
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    E, P, R = cfg["election"], cfg["prior"], cfg["replay"]
    rel: list[str] = []
    bdir = Path("raw/tse_open/bweb") / f"{R['year']}_{R['round']}t"
    pattern = f"{E['uf'].upper()}.parquet" if E.get("uf") else "*.parquet"
    rel += sorted(str(p.relative_to(root)) for p in (root / bdir).glob(pattern))
    if not any(r.startswith(str(bdir)) for r in rel):
        raise FileNotFoundError(f"no BU-web files under {root / bdir}")
    if P["kind"] == "open":
        rel.append(f"raw/tse_open/{E.get('cargo_name', 'PRESIDENTE').lower()}_by_municipality_{P['year']}.parquet")
    else:
        rel.append(f"processed/{E['cycle']}/e{P['ele']}_c1/votes.parquet")
    markets = cfg.get("markets", {})
    ms = ([markets["national"]] if "national" in markets else []) + list(markets.get("states", {}).values())
    rel += [f"raw/polymarket/{m['event']}/prices/{m['market']}.parquet" for m in ms]
    return rel


def stage(cfg_path: Path, dest: Path, tries: int = 3) -> dict:
    src_root = paths.DATA
    manifest = load_manifest(src_root)
    rels = files_for_config(cfg_path, src_root)
    record, appended = {}, []
    for rel in rels:
        src, out = src_root / rel, dest / rel
        if not src.exists():
            raise FileNotFoundError(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        expected = manifest.get(rel)
        for attempt in range(1, tries + 1):
            if expected is None:
                a, b = sha256(src), sha256(src)
                if a != b:
                    continue                                # unstable read of the source; retry
                want = a
            else:
                want = expected
            shutil.copyfile(src, out)
            if sha256(out) == want:
                record[rel] = want
                if expected is None:
                    appended.append(f"{want}  ./{rel}")
                break
        else:
            raise OSError(f"could not stage a verified copy of {rel} after {tries} tries")
    if appended:
        with open(src_root / MANIFEST, "a") as f:
            f.write("\n".join(appended) + "\n")
    meta = {"config": str(cfg_path), "config_sha256": sha256(Path(cfg_path)), "source": str(src_root.resolve()),
            "staged_utc": datetime.now(timezone.utc).isoformat(), "files": record,
            "new_manifest_entries": len(appended)}
    (dest / STAGED).write_text(json.dumps(meta, indent=1))
    return meta


def check_staged(cfg_path: Path, root: Path | None = None) -> None:
    """Raise unless `root` (default: the current data root) is a verified stage for this exact config."""
    root = root or paths.DATA
    f = root / STAGED
    if not f.exists():
        raise RuntimeError(f"{root} is not a verified stage; run `brunder stage {cfg_path} <dir>` and "
                           f"rerun with BRUNDER_DATA=<dir>")
    meta = json.loads(f.read_text())
    if meta["config_sha256"] != sha256(Path(cfg_path)):
        raise RuntimeError(f"stage {root} was made for a different version of {cfg_path}")
    missing = set(files_for_config(cfg_path, root)) - set(meta["files"])
    if missing:
        raise RuntimeError(f"stage {root} lacks verified copies of {sorted(missing)[:3]}")
