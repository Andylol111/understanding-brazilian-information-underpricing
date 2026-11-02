"""Registry for election-night sources added on 2026-11-02.

Each source module is self-contained: RECORD declarations for the passive recorder, FEEDS/STATES
for the model, and ``parse(kind, blobs)``.  Sources remain secondary and unregistered.
"""
from __future__ import annotations

from . import extra_civic_house, extra_ev

MODULES = (extra_ev, extra_civic_house)


def records() -> list[tuple[object, dict]]:
    return [(module, dict(spec)) for module in MODULES for spec in module.RECORD]
