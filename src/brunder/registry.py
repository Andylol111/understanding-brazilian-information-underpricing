"""Pre-registration and the holdout vault.

A study spec (prereg/<id>.toml) may be run on the DISCOVERY split at will. The HOLDOUT split opens
only for a spec that is (1) committed to git with no uncommitted changes and (2) registered: its
SHA-256 and commit are appended to prereg/registry.jsonl. Every holdout run is appended to
prereg/holdout_runs.jsonl; a second run of the same spec is labelled a re-run in the output, so
the number of looks at the holdout is on the record.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from .paths import ROOT

PREREG = ROOT / "prereg"
REGISTRY = PREREG / "registry.jsonl"
RUNS = PREREG / "holdout_runs.jsonl"


class HoldoutLocked(RuntimeError):
    pass


def load_spec(path: Path) -> dict:
    with open(path, "rb") as f:
        spec = tomllib.load(f)
    spec["_path"] = str(path)
    spec["_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return spec


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _committed_clean(path: Path) -> str:
    repo = Path(_git("rev-parse", "--show-toplevel", cwd=path.parent))
    rel = str(path.resolve().relative_to(repo.resolve()))
    if not _git("ls-files", rel, cwd=repo):
        raise HoldoutLocked(f"{rel} is not committed to git")
    if _git("status", "--porcelain", "--", rel, cwd=repo):
        raise HoldoutLocked(f"{rel} has uncommitted changes")
    return _git("rev-parse", "HEAD", cwd=repo)


def _read(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def register(path: Path, registry: Path = REGISTRY) -> dict:
    spec = load_spec(path)
    commit = _committed_clean(Path(path))
    for r in _read(registry):
        if r["id"] == spec["id"]:
            if r["sha256"] != spec["_sha256"]:
                raise HoldoutLocked(f"{spec['id']} already registered with a different spec; use a new id")
            return r
    rec = {"id": spec["id"], "sha256": spec["_sha256"], "commit": commit,
           "registered_utc": datetime.now(timezone.utc).isoformat()}
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def assert_registered(spec: dict, registry: Path = REGISTRY) -> dict:
    """Raise unless spec is committed, registered and unchanged. Does not count as a look."""
    _committed_clean(Path(spec["_path"]))
    reg = [r for r in _read(registry) if r["id"] == spec["id"]]
    if not reg:
        raise HoldoutLocked(f"{spec['id']} is not registered (brunder register {spec['_path']})")
    if reg[0]["sha256"] != spec["_sha256"]:
        raise HoldoutLocked(f"{spec['id']} changed since registration")
    return reg[0]


def open_holdout(spec: dict, registry: Path = REGISTRY, runs: Path = RUNS) -> dict:
    """Raise unless spec is registered unchanged; log the look and return its run record."""
    assert_registered(spec, registry)
    look = 1 + sum(r["id"] == spec["id"] for r in _read(runs))
    rec = {"id": spec["id"], "sha256": spec["_sha256"], "look": look, "utc": datetime.now(timezone.utc).isoformat()}
    with runs.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec
