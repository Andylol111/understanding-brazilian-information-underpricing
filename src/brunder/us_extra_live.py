"""Live runner for secondary U.S. sources added on 2026-11-02.

The wrapper registers their feed declarations and pure parsers at runtime, then runs the unchanged
statewide and House exporters against the shared snapshot archive.  It writes only to the supplied
output root (night service: ``site/data/extra/us2026/``) and does not register these models for the
primary forward test.
"""
from __future__ import annotations

import argparse
import asyncio
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import extra_markets, us, us_house_night
from .paths import RAW, ROOT
from .us_extra_sources import MODULES

_INSTALLED = False
PRIOR_INFO = ("MIT Election Data and Science Lab, 2024 president returns allocated to 2026 House districts "
              "(data_public/us2026_extra_priors_*.csv)", "https://github.com/MEDSL/2024-elections-official",
              "CC0 1.0")


def _check_pdf(body: bytes) -> None:
    if not body.startswith(b"%PDF") or b"%%EOF" not in body[-2048:]:
        raise ValueError("incomplete PDF")


def _check_text(body: bytes) -> None:
    if not body.strip():
        raise ValueError("empty snapshot")


def live_units(kind: str) -> set[str] | None:
    """Units the feed of `kind` can ever report (module LIVE_UNITS); None = every unit of the race's prior."""
    for module in MODULES:
        lu = getattr(module, "LIVE_UNITS", {}).get(kind)
        if lu is not None:
            return set(lu)
    return None


def coverage(cfg: dict, priors: pd.DataFrame) -> float | None:
    """Live share of the race's expected vote: prior two-party votes (the `votes` source, which sets the expected
    vote) of the units a live source can report / of all the race's units; 1.0 for a full-coverage feed. The
    engine's counted share is always against the whole race (unreported units keep their expected vote), so a
    partial district whose live counties are all counted shows counted ~= coverage."""
    pri = us.race_prior(cfg, priors)
    if pri is None or pri.empty:
        return None
    lu = live_units(cfg["election"]["feed"])
    if lu is None:
        return 1.0
    tot = float(pri["V"].sum())
    return round(float(pri.loc[pri["unit_id"].isin(lu), "V"].sum()) / tot, 4) if tot > 0 else None


def install() -> None:
    """Install extra feed/parse/prior hooks once in this process."""
    global _INSTALLED
    if _INSTALLED:
        return
    owners = {}
    for module in MODULES:
        for kind, roles in module.FEEDS.items():
            if kind in us.FEEDS:
                raise ValueError(f"extra feed kind collides with existing feed: {kind}")
            us.FEEDS[kind] = roles
            owners[kind] = module
        us.STATES.update(module.STATES)
        for st in module.STATES:
            us.SOURCE_INFO.setdefault(st, getattr(module, "SOURCE_INFO", {}).get(st, PRIOR_INFO))
        if hasattr(us, "POLL_CLOSE"):
            us.POLL_CLOSE.update(module.POLL_CLOSE)
        # brunder.us.Archive validates a snapshot as JSON / XML / UTF-8 text unless its key has its own check: PDFs
        # and non-UTF-8 HTML pages of the added sources would otherwise be skipped as malformed.
        for spec in module.RECORD:
            ext = spec.get("ext", "json")
            if ext == "pdf":
                us._extra.CHECKS.setdefault(spec["key"], _check_pdf)
            elif ext in ("html", "txt", "csv"):
                us._extra.CHECKS.setdefault(spec["key"], _check_text)

    old_parse = us.parse_feed

    def parse_feed(kind: str, blobs: dict):
        module = owners.get(kind)
        return module.parse(kind, blobs) if module is not None else old_parse(kind, blobs)

    us.parse_feed = parse_feed
    old_priors = us.load_priors

    def load_priors(path=None, processed=None, public=None):
        out = old_priors(path=path, processed=processed, public=public)
        if path is not None or public is not None:
            return out
        extras = []
        for f in sorted((ROOT / "data_public").glob("us2026_extra_priors_*.csv")):
            extras.append(pd.read_csv(f, comment="#"))
        if not extras:
            return out
        extra = pd.concat(extras, ignore_index=True)
        # A source already committed in the primary/civic files remains authoritative.
        extra = extra[~extra["source"].isin(set(out["source"]))]
        return pd.concat([out, extra], ignore_index=True)

    us.load_priors = load_priors
    old_race_prior = us.race_prior

    def race_prior(cfg: dict, priors: pd.DataFrame):
        """Allow an added source to use district/town units absent from the county geometry."""
        p = cfg.get("prior", {})
        if p.get("share") not in set(priors["source"]) or p.get("votes") not in set(priors["source"]):
            return old_race_prior(cfg, priors)
        sh = priors[priors["source"] == p["share"]].set_index("unit_id")
        vo = priors[priors["source"] == p["votes"]].set_index("unit_id")
        common = sh.index.intersection(vo.index)
        county_ids = set(us.counties(cfg["election"]["state"])["unit_id"])
        if common.empty or set(common).issubset(county_ids):
            return old_race_prior(cfg, priors)
        name = cfg["election"]["name"]
        return pd.DataFrame({"unit_id": common, "name": [name] * len(common),
                             "s0": sh.loc[common, "dem"].to_numpy(float) /
                                   (sh.loc[common, "dem"].to_numpy(float) + sh.loc[common, "rep"].to_numpy(float)),
                             "V": vo.loc[common, "dem"].to_numpy(float) + vo.loc[common, "rep"].to_numpy(float)})

    us.race_prior = race_prior
    # Market lines: the added races' Polymarket / Kalshi markets come from configs/us2026_extra/markets.json (YES =
    # side a); their books are recorded by brunder.us_extra_rec into this archive in the registered formats.
    us.kalshi_markets = lambda cfgs, pairs_path=None: extra_markets.kalshi_markets(cfgs)
    us.pm_tokens = lambda cfgs, pairs_path=None, lookup=False: extra_markets.race_tokens(cfgs)
    old_meta = us.meta
    _cov_cache: dict = {}

    def meta(cfgs, *args, **kw):
        """brunder.us.meta plus, per added race, `coverage` (0-1, live share of the expected vote) and, below 1, a
        `coverage_note` the page can show."""
        m = old_meta(cfgs, *args, **kw)
        if "pri" not in _cov_cache:
            _cov_cache["pri"] = us.load_priors()
        pri = _cov_cache["pri"]
        by_id = {c["election"]["race_id"]: c for c in cfgs}
        for r in m.get("races", []):
            c = by_id.get(r.get("race_id"))
            cov = coverage(c, pri) if c is not None else None
            r["coverage"] = cov
            if cov is not None and cov < 0.999:
                r["coverage_note"] = (f"partial: {round(100 * cov)}% of the district's expected vote has a live "
                                      "source; the rest stays at its prior")
        return m

    us.meta = meta

    for cls in (us.Exporter, us_house_night.HouseExporter):
        if "write" in cls.__dict__ and not getattr(cls.__dict__["write"], "_stale_marked", False):
            orig = cls.__dict__["write"]

            def write(self, out, _orig=orig):
                d = _orig(self, out)
                try:
                    mark_stale(d, self.cfgs, self.arc.root)
                except Exception as e:             # a status flag must never stop the model's output
                    print(f"stale marking failed, continuing: {type(e).__name__}: {e}", flush=True)
                return d
            write._stale_marked = True
            setattr(cls, "write", write)
    _INSTALLED = True


# ------------------------------------------------------------------ stale sources
NIGHT_START = pd.Timestamp("2026-11-03T23:30:00Z")
NIGHT_END = pd.Timestamp("2026-11-04T10:00:00Z")
STALE_AFTER = pd.Timedelta(minutes=10)
_MARKET_KEYS = {extra_markets.PM_KEY, extra_markets.KALSHI_KEY}


def _markers(root: Path) -> list[dict]:
    return list(us.poll_markers(root).values())


def key_fresh(root: Path, key: str) -> pd.Timestamp | None:
    """When `key` last had fresh data: the latest of (a) its recorder's last successful poll (poll markers'
    per-feed `last_ok`: the server recorder's .poll/extra.json, the Mac recorder's pushed .poll/mac.json), (b) for a
    key linked from the shared archive, its recorder's last finished poll cycle (.poll/<recorder>.json `done`), and
    (c) the newest snapshot of the key (recorders store a body only when it changed, so (a)/(b) keep an unchanged
    but polled feed fresh)."""
    cand = []
    for mk in _markers(root):
        ok = ((mk.get("feeds") or {}).get(key) or {}).get("last_ok")
        if ok:
            cand.append(pd.Timestamp(ok))
    p = root / key
    if p.is_symlink():
        for mk in _markers(p.resolve().parent):
            if mk.get("done"):
                cand.append(pd.Timestamp(mk["done"]))
    if p.is_dir():
        names = [f.name for f in p.iterdir() if f.name[:1].isdigit()]
        if names:
            cand.append(pd.Timestamp(pd.to_datetime(max(names)[:22], format="%Y%m%dT%H%M%S.%f", utc=True)))
    return max(cand) if cand else None


def race_staleness(root: Path, cfg: dict, now: pd.Timestamp) -> tuple[bool, str | None]:
    """(stale, stale_since) of one race: during the night window, a feed key of the race whose freshest data is older
    than STALE_AFTER (or that never had data since the window opened) makes it stale; stale_since = the oldest such
    key's last fresh time (None if it never had data). Outside the window never stale."""
    if not (NIGHT_START <= now <= NIGHT_END):
        return False, None
    pats = us.FEEDS[cfg["election"]["feed"]].values()
    keys = set()
    for pat in pats:
        if pat.endswith("*"):
            keys.update(x.name for x in root.glob(pat) if x.is_dir())
        else:
            keys.add(pat)
    keys -= _MARKET_KEYS
    worst, stale = None, False
    for k in sorted(keys):
        f = key_fresh(root, k)
        if f is None or now - f > STALE_AFTER:
            stale = True
            if f is not None and (worst is None or f < worst):
                worst = f
    return stale, (None if worst is None else us._iso(worst))


def mark_stale(d: Path, cfgs: list[dict], root: Path, now: pd.Timestamp | None = None) -> None:
    """Set `stale` / `stale_since` on each stale race in <d>/meta.json and on its latest row of timeline.json and
    timeline_tail.json (the files the exporter just rewrote from memory, so fresh races carry no flag: clearing
    is automatic). The model output itself is unchanged."""
    import json
    now = now or pd.Timestamp.now(tz="UTC")
    st = {c["election"]["race_id"]: race_staleness(Path(root), c, now) for c in cfgs}
    for name in ("meta.json", "timeline.json", "timeline_tail.json"):
        f = d / name
        if not f.exists():
            continue
        doc = json.loads(f.read_text())
        changed = False
        if name == "meta.json":
            for r in doc.get("races", []):
                s, since = st.get(r.get("race_id"), (False, None))
                old = (r.pop("stale", None), r.pop("stale_since", None))
                if s:
                    r["stale"], r["stale_since"] = True, since
                changed |= old != ((True, since) if s else (None, None))
        else:
            for rid, rows in doc.items():
                s, since = st.get(rid, (False, None))
                if rows and isinstance(rows, list) and s:
                    rows[-1]["stale"], rows[-1]["stale_since"] = True, since
                    changed = True
        if changed:
            us._write_json(f, doc)


def _is_house(path: Path) -> bool:
    with path.open("rb") as f:
        return str(tomllib.load(f).get("election", {}).get("race_id", "")).startswith("house:")


SHARED_KEYS: tuple[str, ...] = ()      # market books are recorded into the extra archive itself


def link_shared(configs: list[Path], snap_root: Path, shared_root: Path | None) -> list[Path]:
    """Symlink the shared-archive keys these configs read (civic_* race documents, market books) into the extra
    archive. The added recorder writes only to its own archive, so the registered processes never see its index
    lines or poll marker. Dangling links are fine: a key appears once the shared recorder first saves it."""
    if shared_root is None:
        return []
    own = ({spec["key"] for module in MODULES for spec in module.RECORD}     # written here by brunder.us_extra_rec
           | {extra_markets.PM_KEY, extra_markets.KALSHI_KEY})
    keys = set(SHARED_KEYS)
    for p in configs:
        with p.open("rb") as f:
            kind = tomllib.load(f)["election"]["feed"]
        keys.update(pat for pat in us.FEEDS[kind].values() if not pat.endswith("*") and pat not in own)
    keys -= own
    snap_root.mkdir(parents=True, exist_ok=True)
    made = []
    for k in sorted(keys):
        dst = snap_root / k
        if dst.exists() or dst.is_symlink():
            continue
        dst.symlink_to(shared_root / k, target_is_directory=True)
        made.append(dst)
    return made


async def run(configs: list[Path], out: Path, snap_root: Path, interval: float,
              stop_at: datetime | None, shared_root: Path | None = None) -> None:
    install()
    link_shared(configs, snap_root, shared_root)
    house = [x for x in configs if _is_house(x)]
    state = [x for x in configs if x not in house]
    tasks = []
    if state:
        tasks.append(us.run_live(state, out, snap_root, interval, stop_at,
                                 ROOT / "logs" / "us2026_extra_updates.jsonl"))
    if house:
        stop_ts = pd.Timestamp(stop_at) if stop_at is not None else None
        tasks.append(asyncio.to_thread(us_house_night.run_live, house, out, snap_root,
                                       max(interval, 60.0), stop_ts))
    if not tasks:
        raise ValueError("no configs")
    await asyncio.gather(*tasks)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("configs", nargs="+", type=Path)
    p.add_argument("--out", type=Path, default=ROOT / "site" / "data" / "extra")
    p.add_argument("--snap", type=Path, default=RAW / "snap" / "us_midterms_2026_extra",
                   help="the added recorder's archive (brunder.us_extra_rec --out)")
    p.add_argument("--shared", type=Path, default=RAW / "snap" / "us_midterms_2026",
                   help="shared archive (brunder-snap-us, civic, kalshi recorders): linked keys, read only")
    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--until", default="2026-11-20T00:00:00Z")
    a = p.parse_args(argv)
    stop = datetime.fromisoformat(a.until.replace("Z", "+00:00")) if a.until else None
    if stop is not None and stop.tzinfo is None:
        stop = stop.replace(tzinfo=timezone.utc)
    asyncio.run(run(a.configs, a.out, a.snap, max(10.0, a.interval), stop, a.shared))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
