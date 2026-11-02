"""Builder of the ready-to-register blend config set (configs/us2026_blend/). NOT registered by this study.

Only component 6 passed its bar (docs/us_blend.md): Georgia House v2 (generic-ballot start) with the count settings of Georgia House v1
([count] vote_type = true, early_share = 0.64 copied from the registered v1 file). Every file is the registered us2026_house_ga-NN_v2.toml with
exactly those two [count] lines changed, the id suffixed `_v2c`, and a header comment. Registered files are only read.

    uv run python analysis/us_blend_build_configs.py
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "configs" / "us2026_house"
OUT = ROOT / "configs" / "us2026_blend"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for v2 in sorted(SRC.glob("ga-*_v2.toml")):
        v1 = tomllib.loads((SRC / v2.name.replace("_v2", "_v1")).read_text())["count"]
        txt = v2.read_text()
        assert "vote_type = false" in txt and "early_share" not in txt
        txt = txt.replace("vote_type = false", f"vote_type = true\nearly_share = {v1['early_share']}", 1)
        old_id = re.search(r'^id = "(.+)"$', txt, re.M).group(1)
        txt = txt.replace(f'id = "{old_id}"', f'id = "{old_id}c"', 1)
        head = ("# BLEND CANDIDATE v2c (docs/us_blend.md component 5.6, simulation-only evidence on Georgia House nights 2022/2024): registered v2 start\n"
                "# (generic-ballot swing prior) + the [count] settings of registered v1 (vote_type = true, early_share = 0.64). Everything else identical to the v2 file.\n"
                "# Built by analysis/us_blend_build_configs.py; NOT registered. Register (prereg/registry.jsonl) before 2026-11-03 22:30 UTC or drop.\n")
        out = OUT / v2.name.replace("_v2", "_v2c")
        out.write_text(head + txt)
        assert tomllib.loads(out.read_text())["count"]["vote_type"] is True
        print(out.name)


if __name__ == "__main__":
    main()
