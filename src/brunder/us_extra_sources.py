"""Registry for election-night sources added on 2026-11-02.

Each source module is self-contained: RECORD declarations for the passive recorder, FEEDS/STATES
for the model, and ``parse(kind, blobs)``.  Sources remain secondary and unregistered.
"""
from __future__ import annotations

from . import extra_civic_house, extra_clarity, extra_ev, extra_md, extra_mo, extra_mt, extra_or, extra_va

MODULES = (extra_ev, extra_civic_house, extra_va, extra_md, extra_clarity, extra_mt, extra_mo, extra_or)


def records() -> list[tuple[object, dict]]:
    return [(module, dict(spec)) for module in MODULES for spec in module.RECORD]
