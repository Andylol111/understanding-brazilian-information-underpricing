"""brunder CLI: data collection, verification, observations, pre-registration and studies."""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import bweb, live, night, polymarket, registry, studies, tse_live, tse_open, universe, verify
from .paths import PROCESSED


def _build(cycle: str, ele: int, cargo: int) -> None:
    votes, progress, cat = [], [], None
    for snap in tse_live.snapshots(cycle, ele, cargo):
        v, p, cat = tse_live.parse_snapshot(snap)
        votes.append(v)
        progress.append(p)
    out = PROCESSED / cycle / f"e{ele}_c{cargo}"
    out.mkdir(parents=True, exist_ok=True)
    pd.concat(votes).to_parquet(out / "votes.parquet")
    pd.concat(progress).to_parquet(out / "progress.parquet")
    cat.to_parquet(out / "municipalities.parquet")
    print(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="brunder")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("elections", help="list elections in the TSE live feed")
    for name in ("tse-snapshot", "tse-watch", "tse-build"):
        s = sub.add_parser(name)
        s.add_argument("--cycle", default="ele2026")
        s.add_argument("--ele", type=int, required=True, help="e.g. 6257 (2026 R1 federal), 6258 (R2)")
        s.add_argument("--cargo", type=int, default=1, help="1=Presidente, 3=Governador")
        if name != "tse-build":
            s.add_argument("--concurrency", type=int, default=8)
        if name == "tse-watch":
            s.add_argument("--interval", type=float, default=30.0)
        if name == "tse-snapshot":
            s.add_argument("--resume", action="store_true", help="complete the latest snapshot (final counts only)")

    s = sub.add_parser("tse-open", help="download a TSE open-data zip")
    s.add_argument("dataset", choices=sorted(tse_open.DATASETS))
    s.add_argument("year", type=int)

    s = sub.add_parser("pm-search")
    s.add_argument("query")
    s = sub.add_parser("pm-download", help="archive a Polymarket event (prices + trades)")
    s.add_argument("slug")
    s.add_argument("--fidelity", type=int, default=1, help="minutes")
    s.add_argument("--no-trades", action="store_true")
    s = sub.add_parser("universe", help="sample resolved markets across categories; fetch price paths")
    s.add_argument("step", choices=["catalogue", "prices", "all"])
    s.add_argument("--categories", nargs="*", default=universe.CATEGORIES, choices=universe.CATEGORIES)
    s.add_argument("--concurrency", type=int, default=8)
    sub.add_parser("verify", help="price-history point-in-time check against trades")
    sub.add_parser("observations", help="build shocks / horizons / label checks from price paths")
    s = sub.add_parser("register", help="pre-register a committed study spec (unlocks its holdout)")
    s.add_argument("spec", type=Path)
    s = sub.add_parser("study", help="run a study spec on a split")
    s.add_argument("spec", type=Path)
    s.add_argument("--split", choices=["discovery", "holdout"], default="discovery")
    s.add_argument("--fills", action=argparse.BooleanOptionalAction, default=None,
                   help="check executability against trades (default: holdout only)")
    s = sub.add_parser("live", help="forward test: record signals + order books live; status; final verdict")
    s.add_argument("step", choices=["run", "status", "evaluate"])
    s.add_argument("spec", type=Path)
    s.add_argument("--interval", type=float, default=120.0, help="seconds between book polls")
    s = sub.add_parser("bweb", help="section-level BU-web files (receipt times + votes) for a round")
    s.add_argument("year", type=int)
    s.add_argument("round", type=int)
    s.add_argument("--wait", action="store_true", help="poll the TSE portal hourly until the files are published")
    s = sub.add_parser("night", help="election-night edge engine: live, or replay a past count")
    s.add_argument("step", choices=["live", "replay"])
    s.add_argument("config", type=Path)
    s.add_argument("--interval", type=float, default=30.0)
    s.add_argument("--lag", default="0min", help="replay: receipt → publication delay")
    s.add_argument("--step", default="1min", help="replay: tick spacing")

    a = ap.parse_args(argv)
    pd.set_option("display.width", 200, "display.max_colwidth", 80, "display.max_rows", 200)
    match a.cmd:
        case "elections":
            print(tse_live.elections().to_string())
        case "tse-snapshot":
            print(asyncio.run(tse_live.snapshot(a.cycle, a.ele, a.cargo, a.concurrency, resume=a.resume)))
        case "tse-watch":
            asyncio.run(tse_live.watch(a.cycle, a.ele, a.cargo, a.interval, a.concurrency))
        case "tse-build":
            _build(a.cycle, a.ele, a.cargo)
        case "tse-open":
            print(tse_open.download(a.dataset, a.year))
        case "pm-search":
            print(polymarket.search_events(a.query).to_string())
        case "pm-download":
            print(polymarket.download_event(a.slug, a.fidelity, not a.no_trades))
        case "universe":
            if a.step in ("catalogue", "all"):
                ev, mk = asyncio.run(universe.catalogue(a.categories, concurrency=a.concurrency))
                print(f"{len(ev)} events, {len(mk)} resolved binary markets", flush=True)
            if a.step in ("prices", "all"):
                print(asyncio.run(universe.prices(a.concurrency)).to_string())
        case "verify":
            print(verify.price_semantics().round(4).to_string())
        case "observations":
            studies.build_observations()
        case "register":
            print(registry.register(a.spec))
        case "study":
            print(studies.run(a.spec, a.split, a.fills))
        case "live":
            match a.step:
                case "run":
                    asyncio.run(live.run(a.spec, a.interval))
                case "status":
                    print(live.status(a.spec).to_string(index=False))
                case "evaluate":
                    print(live.evaluate(a.spec).round(4).to_string(index=False))
        case "bweb":
            while a.wait:
                try:
                    if bweb.files(a.year, a.round):
                        print(bweb.fetch(a.year, a.round), flush=True)
                        break
                    msg = "not published yet"
                except Exception as e:                      # network blips must not end an hours-long wait
                    msg = f"check failed ({type(e).__name__}: {e})"
                print(f"{datetime.now():%m-%d %H:%M} BU-web {a.year} round {a.round}: {msg}; retry in 1h", flush=True)
                time.sleep(3600)
            else:
                print(bweb.fetch(a.year, a.round))
        case "night":
            if a.step == "live":
                asyncio.run(night.run_live(a.config, a.interval))
            else:
                print(night.replay(a.config, a.lag, a.step).tail().to_string())


if __name__ == "__main__":
    main()
