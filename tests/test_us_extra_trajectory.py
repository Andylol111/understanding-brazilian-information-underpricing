"""Count trajectories of the five added statewide models (ME-Sen, NV-Gov, NH-Sen, NH-Gov, OR-Gov) with the registered
civic-v3 settings: synthetic progressive feeds in the real feed formats (civicAPI race documents with county rows;
Oregon GetMapData rows), ten cumulative batches, fed through the unchanged statewide exporter via the archive.

Truth of a scenario = the config's own 2024-president county prior moved to a statewide share (the config's poll average,
or the poll average minus a 3-point poll miss) with seeded county noise (sd 0.02 in share) and ±5 % turnout noise; county
size = 0.72 x its 2024 two-party votes. Orders: smallest counties first (rural first) and random (seed 1). A county is
posted whole (percent reporting 100 / all precincts reporting). Pass rules (integrator, 2026-11-03):
  * P never < 0.01 or > 0.99 before 30 % counted unless the final margin is > 10 points;
  * P ends on the side of the final result;
  * sd shrinks as counted grows (final sd < 0.5 x the first counted sd, and no step above 1.25 x the running minimum);
  * no exporter errors.
Run `pytest -s tests/test_us_extra_trajectory.py` to print the trajectories.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import us, us_extra_live

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "us"
RACES = {"me-sen": ("me_sen", "civic_87188", "sen"), "nv-gov": ("nv_gov", "civic_87260", "gov"),
         "nh-sen": ("nh_sen", "civic_87283", "sen"), "nh-gov": ("nh_gov", "civic_87284", "gov"),
         "or-gov": ("or_gov", "or_gov_mapdata", "gov")}
N_BATCH = 10
TABLES: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _restore_us():
    from brunder import us_feeds_extra, us_house_night
    names = ("parse_feed", "load_priors", "race_prior", "kalshi_markets", "pm_tokens", "meta")
    saved = {n: getattr(us, n) for n in names}
    writes = {cls: cls.__dict__["write"] for cls in (us.Exporter, us_house_night.HouseExporter)}
    dicts = [(d, dict(d)) for d in (us.FEEDS, us.STATES, us.POLL_CLOSE, us.SOURCE_INFO, us_feeds_extra.CHECKS)]
    yield
    for n, f in saved.items():
        setattr(us, n, f)
    for cls, w in writes.items():
        setattr(cls, "write", w)
    for d, copy in dicts:
        d.clear()
        d.update(copy)
    us_extra_live._INSTALLED = False


def scenario(rk: str, miss: float, seed: int = 7):
    """(cfg, pri, final): per county unit_id -> (dem votes, rep votes) of the synthetic truth."""
    stem = RACES[rk][0]
    cfg_path = ROOT / "configs" / "us2026_extra" / f"{stem}_v1.toml"
    cfg = tomllib.loads(cfg_path.read_text())
    pri = us.race_prior(us.load_config(cfg_path), us.load_priors())
    rng = np.random.default_rng(seed)
    target = cfg["model"]["poll_avg"] + miss
    base = us.baseline_share(pri)
    share = np.clip(pri["s0"].to_numpy() + (target - base) + rng.normal(0, 0.02, len(pri)), 0.02, 0.98)
    tot = 0.72 * pri["V"].to_numpy() * rng.normal(1, 0.05, len(pri))
    final = {u: (t * s, t * (1 - s)) for u, t, s in zip(pri["unit_id"], tot, share)}
    return cfg_path, cfg, pri, final


def snapshot(rk: str, cfg: dict, pri: pd.DataFrame, final: dict, done: set[str]) -> tuple[bytes, str]:
    E = cfg["election"]
    a, b = E["a"], E["b"]
    names = dict(zip(pri["unit_id"], pri["name"]))
    if rk == "or-gov":
        rows = []
        for u in names:
            for cand, party, k in ((a["name"], "Democrat, Working Families", 0), (b["name"], "Republican, Libertarian", 1)):
                rows.append({"CountyName": names[u], "RaceName": "Governor", "calcCandidate": cand, "PartyCode": "NON",
                             "PartyName": party, "calcCandidateVotes": int(final[u][k]) if u in done else 0,
                             "PrecinctsReporting": 10 if u in done else 0, "TotalPrecincts": 10})
        return json.dumps({"d": rows}).encode(), "json"
    regions = {}
    for u in names:
        v = [int(x) if u in done else 0 for x in final[u]]
        regions[names[u].lower()] = {"name": names[u], "type": "County", "fips": u[-5:],
                                     "percent_reporting": 100 if u in done else 0,
                                     "candidates": [{"name": a["name"], "party": "Democratic", "votes": v[0]},
                                                    {"name": b["name"], "party": "Republican", "votes": v[1]}]}
    tot = [sum(int(final[u][k]) for u in done) for k in (0, 1)]
    return json.dumps({"election_type": "x", "test_data": False, "percent_reporting": 0, "region_results": regions,
                       "candidates": [{"name": a["name"], "party": "Democratic", "votes": tot[0]},
                                      {"name": b["name"], "party": "Republican", "votes": tot[1]}]}).encode(), "json"


def trajectory(tmp_path: Path, rk: str, miss: float, order: str):
    us_extra_live.install()
    cfg_path, cfg, pri, final = scenario(rk, miss)
    key = RACES[rk][1]
    size = {u: sum(final[u]) for u in final}
    ids = sorted(final, key=size.get) if order == "small-first" else list(np.random.default_rng(1).permutation(list(final)))
    cuts = np.linspace(0, len(ids), N_BATCH + 1).round().astype(int)
    arc = tmp_path / "snap"
    (arc / key).mkdir(parents=True)
    t0 = pd.Timestamp("2026-11-04T03:00:00Z")
    for i in range(N_BATCH + 1):                                  # batch 0 = nothing counted
        done = set(ids[:cuts[i]])
        body, ext = snapshot(rk, cfg, pri, final, done)
        (arc / key / f"{(t0 + pd.Timedelta(minutes=i)):%Y%m%dT%H%M%S}.000000Z.{ext}").write_bytes(body)
    ex = us.Exporter([cfg_path], arc)
    ex.update(t0 + pd.Timedelta(minutes=N_BATCH + 1))
    rows = ex.timeline[rk]
    d_final = sum(v[0] for v in final.values())
    r_final = sum(v[1] for v in final.values())
    return rows, d_final / (d_final + r_final), ex.errors


def fmt(rk, miss, order, rows, final_share) -> str:
    out = [f"{rk} order={order} poll miss {miss:+.2f}: final D share {final_share:.4f} (margin {200 * final_share - 100:+.1f} pts)",
           "  counted     P    share     sd"]
    for r in rows:
        out.append(f"  {r['counted']:7.3f} {r['P']:6.3f} {r['share']:8.4f} {r['sd']:6.4f}")
    early = [r["P"] for r in rows if r["counted"] < 0.30]
    sd = [r["sd"] for r in rows if r["counted"] > 0]
    out.append(f"  summary: P<30% counted in [{min(early):.3f}, {max(early):.3f}], final P {rows[-1]['P']:.3f}, "
               f"sd {sd[0]:.4f} -> {sd[-1]:.4f}")
    return "\n".join(out)


@pytest.mark.parametrize("miss", [0.0, -0.03])
@pytest.mark.parametrize("order", ["small-first", "random"])
@pytest.mark.parametrize("rk", list(RACES))
def test_count_trajectory(tmp_path, rk, order, miss, capsys):
    rows, final_share, errors = trajectory(tmp_path, rk, miss, order)
    TABLES[f"{rk}|{order}|{miss}"] = fmt(rk, miss, order, rows, final_share)
    print(TABLES[f"{rk}|{order}|{miss}"])
    assert not errors, errors
    assert len(rows) >= N_BATCH and rows[0]["counted"] in (0, 0.0)
    assert rows[-1]["counted"] > 0.95
    margin = abs(200 * final_share - 100)
    if margin <= 10:
        early = [r for r in rows if r["counted"] < 0.30]
        assert all(0.01 <= r["P"] <= 0.99 for r in early), [(r["counted"], r["P"]) for r in early]
    assert (rows[-1]["P"] > 0.5) == (final_share > 0.5), (rows[-1]["P"], final_share)
    counted = [r for r in rows if r["counted"] > 0]
    sd = [r["sd"] for r in counted]
    assert sd[-1] < 0.5 * sd[0], sd
    run_min = sd[0]
    for s in sd[1:]:
        assert s <= 1.25 * run_min, sd
        run_min = min(run_min, s)
