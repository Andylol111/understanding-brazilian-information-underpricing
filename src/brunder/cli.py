"""brunder CLI: data collection, verification, observations, pre-registration and studies."""

from __future__ import annotations

import argparse
import asyncio
import re
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
            s.add_argument("--interval", type=float, default=10.0)
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
    s.add_argument("--interval", type=float, default=5.0, help="live: seconds between ticks")
    s.add_argument("--lag", default="0min", help="replay: receipt → publication delay")
    s.add_argument("--step", default="1min", help="replay: tick spacing")
    s = sub.add_parser("stage", help="copy a replay's input files to local disk, verified by SHA-256")
    s.add_argument("config", type=Path)
    s.add_argument("dest", type=Path)
    s = sub.add_parser("snap", help="passive election-night recorder: URLs + Polymarket books, saved on change")
    s.add_argument("tag")
    s.add_argument("--url", action="append", default=[])
    s.add_argument("--pm-event", action="append", default=[], help="Polymarket event slug (repeatable)")
    s.add_argument("--interval", type=float, default=30.0)
    s.add_argument("--until", help="UTC stop time, e.g. 2026-10-28T12:00Z")
    s.add_argument("--clarity", action="append", default=[],
                   help='Clarity ENR base, or "BASE|LISTING_URL|TEXT_REGEX" (day-of EID/version resolved)')
    s.add_argument("--ga-enr", action="append", default=[], help="Enhanced Voting jurisdiction URL (day-of id resolved)")
    s.add_argument("--vt-static", action="append", default=[],
                   help="Vermont static results file …/elections/<guid>.json (records it + its statewide file)")
    s.add_argument("--ncsbe", action="append", default=[],
                   help="NCSBE ENR data base, e.g. https://er.ncsbe.gov/enr/20261103/data (county.txt + results_<id>.txt)")
    s.add_argument("--date", help="election date YYYY-MM-DD for the resolvers")
    s.add_argument("--feed-interval", action="append", default=[], metavar="URL_PREFIX=SECONDS",
                   help="poll feeds whose URL starts with URL_PREFIX every SECONDS (≥ 10) instead of --interval (repeatable)")
    s = sub.add_parser("pm-ws", help="passive Polymarket market-channel WebSocket recorder (server-timestamped events)")
    s.add_argument("tag", help="recorder tag; writes under <snap root>/<tag> (use a tag of its own, e.g. us_midterms_2026_ws)")
    s.add_argument("--pm-event", action="append", default=[], help="Polymarket event slug (repeatable)")
    s.add_argument("--pm-events-from", action="append", default=[], type=Path,
                   help="systemd unit whose --pm-event slugs to record (deploy/brunder-snap-us.service)")
    s.add_argument("--shard", type=int, default=250, help="tokens per WebSocket connection")
    s.add_argument("--until", help="UTC stop time, e.g. 2026-11-20T00:00Z")
    s = sub.add_parser("kalshi", help="Kalshi public market data: find series, list an event, record order books")
    s.add_argument("step", choices=["search", "event", "record"])
    s.add_argument("arg", help="search: regex on series ticker/title; event: event ticker; record: snap tag")
    s.add_argument("--ticker", action="append", default=[], help="record: market ticker (repeatable)")
    s.add_argument("--event", action="append", default=[], help="record: every open market of an event (repeatable)")
    s.add_argument("--pairs", action="append", default=[], type=Path, help="record: Kalshi tickers of configs/pairs/<eid>.toml")
    s.add_argument("--interval", type=float, default=30.0)
    s.add_argument("--fast-config-from", action="append", default=[], type=Path,
                   help="record: systemd unit whose configs/us2026_*_v1.toml tickers are polled every 10 s")
    s.add_argument("--fast-interval", type=float, default=10.0, help="record: seconds between modelled-race books (≥ 10)")
    s.add_argument("--until", help="UTC stop time, e.g. 2026-11-04T12:00Z")
    s = sub.add_parser("us", help="US midterms 2026: county priors; dashboard files before the count, live, replay")
    s.add_argument("step", choices=["priors", "preelection", "export", "live", "earlyvote", "score"])
    s.add_argument("configs", nargs="*", type=Path, help="configs/us2026_*_v1.toml")
    s.add_argument("--source", action="append", default=[], help="priors: only these sources (repeatable)")
    s.add_argument("--out", type=Path, default=Path("site/data"), help="data root; files go to <out>/us2026/")
    s.add_argument("--snap", type=Path, help="recorder directory (default data/raw/snap/us_midterms_2026)")
    s.add_argument("--until", help="export: replay up to this UTC time; live: stop time")
    s.add_argument("--interval", type=float, default=30.0,
                   help="live: longest gap between passes (passes run as soon as a new minute row can be computed)")
    s.add_argument("--updates", type=Path, help="live: model-update log (default logs/us2026_updates.jsonl)")
    s.add_argument("--days", type=float, default=14, help="preelection: days of price history")
    s.add_argument("--force", action="store_true", help="preelection: overwrite live/replay files")
    s = sub.add_parser("civic", help="civicAPI (civicapi.org), secondary US midterms source: record races, resolve ids")
    s.add_argument("step", choices=["record", "resolve"])
    s.add_argument("--tag", default="us_midterms_2026", help="record: snap directory name")
    s.add_argument("--interval", type=float, default=30.0, help="record: seconds between polls of a race (not below 30)")
    s.add_argument("--until", help="record: UTC stop time, e.g. 2026-11-20T00:00Z")
    s = sub.add_parser("nz", help="New Zealand 2026: ingest official files; record, preelection, live, replay")
    s.add_argument("step", choices=["ingest", "record", "preelection", "export", "live"])
    s.add_argument("config", nargs="?", type=Path, default=Path("configs/nz2026_v2.toml"))
    s.add_argument("--source", type=Path, help="ingest: directory containing official browser downloads")
    s.add_argument("--year", type=int, default=2023, help="ingest: election year")
    s.add_argument("--out", type=Path, default=Path("site/data"))
    s.add_argument("--snap", type=Path, help="recorder directory (default data/raw/snap/nz_2026)")
    s.add_argument("--until", help="record/live/export stop time (UTC)")
    s.add_argument("--interval", type=float, default=120.0)
    s.add_argument("--days", type=float, default=14)
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("taiwan", help="Taipei mayor 2026: official CEC recorder, preelection, live and replay")
    s.add_argument("step", choices=["record", "preelection", "export", "live"])
    s.add_argument("config", nargs="?", type=Path, default=Path("configs/tw2026_taipei_v1.toml"))
    s.add_argument("--out", type=Path, default=Path("site/data"))
    s.add_argument("--snap", type=Path, help="recorder directory (default data/raw/snap/taipei_2026)")
    s.add_argument("--until", help="record/live/export stop time (UTC)")
    s.add_argument("--interval", type=float, default=600.0)
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("bulgaria", help="Bulgaria presidential runoff 2026-11-01: MIR nowcast from saved CIK pages")
    s.add_argument("step", choices=["prior", "ingest"])
    s.add_argument("config", type=Path, help="configs/bg2026r2_v1.toml")
    s.add_argument("--r1", type=Path, required=True, help="first round: folder of saved result pages or export.zip")
    s.add_argument("--r2", type=Path, help="ingest: folder of runoff result pages saved on the night")
    s.add_argument("--no-markets", action="store_true", help="ingest: skip Polymarket/Kalshi price history")


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
        case "stage":
            from . import integrity
            meta = integrity.stage(a.config, a.dest)
            print(f"staged {len(meta['files'])} verified files for {a.config} in {a.dest} "
                  f"({meta['new_manifest_entries']} new manifest entries)\n"
                  f"run: BRUNDER_DATA={a.dest} uv run python analysis/night_replay.py {a.config}")
        case "snap":
            from . import snap
            until = pd.Timestamp(a.until).tz_convert("UTC").to_pydatetime() if a.until else None
            asyncio.run(snap.run(a.tag, a.url, a.pm_event, a.interval, until, a.clarity, a.ga_enr, a.date,
                                 vt_static=a.vt_static, ncsbe=a.ncsbe, intervals=snap.feed_intervals(a.feed_interval)))
        case "pm-ws":
            from . import pm_ws, snap
            from .pm_ws import slugs_from_unit
            until = pd.Timestamp(a.until).tz_convert("UTC").to_pydatetime() if a.until else None
            events = list(dict.fromkeys(a.pm_event + [s for f in a.pm_events_from for s in slugs_from_unit(f)]))
            tokens = snap._pm_tokens(events)
            asyncio.run(pm_ws.run(a.tag, tokens, until, shard=a.shard))
        case "kalshi":
            from . import arb, kalshi, us
            match a.step:
                case "search":
                    print(kalshi.search_series(a.arg).to_string(index=False))
                case "event":
                    m = kalshi.markets(kalshi.event(a.arg))
                    print(m[["ticker", "label", "status", "yes_bid", "yes_ask", "volume", "close_time"]].to_string(index=False))
                case "record":
                    tickers = a.ticker + [p.kalshi_ticker for f in a.pairs for p in arb.load_pairs(f)[1]]
                    configs = [Path(p) for f in a.fast_config_from
                               for p in re.findall(r"configs/us2026_\w+_v1\.toml", f.read_text())]
                    fast = [x["ticker"] for x in us.kalshi_markets([us.load_config(f) for f in configs]).values()]
                    until = pd.Timestamp(a.until).tz_convert("UTC").to_pydatetime() if a.until else None
                    asyncio.run(kalshi.record(a.arg, kalshi.expand(tickers, a.event), a.interval, until,
                                              fast_tickers=fast, fast_interval=a.fast_interval))
        case "us":
            from . import us
            us.cli(a)
        case "civic":
            from . import civic
            civic.cli(a)
        case "nz":
            from . import newzealand
            newzealand.cli(a)
        case "taiwan":
            from . import taiwan
            taiwan.cli(a)
        case "bulgaria":
            from . import bulgaria
            bulgaria.cli(a)


if __name__ == "__main__":
    main()
