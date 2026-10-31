"""Check that adding a sensitivity changed nothing but the new fields: compares site/data/<id>/{timeline,meta,stats}.json with git HEAD.

    uv run python analysis/mix_verify_v1.py br2026r1 [...]
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def head(path: str):
    return json.loads(subprocess.check_output(["git", "show", f"HEAD:{path}"], cwd=ROOT))


def strip_tl(tl):
    return {k: [{a: b for a, b in r.items() if not a.endswith("_sens_mix") and "_sens_" not in a} for r in rows] for k, rows in tl.items()}


def main(ids):
    for i in ids:
        base = f"site/data/{i}/"
        ok = True
        new_tl = json.loads((ROOT / base / "timeline.json").read_text())
        if strip_tl(new_tl) != head(base + "timeline.json"):
            print(i, "timeline v1 fields CHANGED"); ok = False
        meta = json.loads((ROOT / base / "meta.json").read_text())
        meta.pop("sensitivities", None)
        old_meta = head(base + "meta.json")
        old_meta.pop("sensitivities", None)
        if meta != old_meta:
            print(i, "meta CHANGED beyond sensitivities", [k for k in set(meta) | set(old_meta) if meta.get(k) != old_meta.get(k)]); ok = False
        st = json.loads((ROOT / base / "stats.json").read_text())
        st.pop("sensitivities", None)
        old = head(base + "stats.json")
        old.pop("sensitivities", None)
        if st != old:
            print(i, "stats CHANGED beyond sensitivities", [k for k in set(st) | set(old) if st.get(k) != old.get(k)]); ok = False
        print(i, "v1 fields identical" if ok else "FAILED")


if __name__ == "__main__":
    main(sys.argv[1:])
