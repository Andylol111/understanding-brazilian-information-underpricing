from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BRUNDER_DATA", ROOT / "data"))
RAW = DATA / "raw"
PROCESSED = DATA / "processed"

TSE_LIVE = RAW / "tse_live"
TSE_OPEN = RAW / "tse_open"
POLYMARKET = RAW / "polymarket"
