"""Australia 2025: "tau mixture over earlier nights (chosen after the result was known)", a retrospective SENSITIVITY.

The Australian model's reporting-order error is not a `tau` of the margin: its error that does NOT shrink as polling places
report is the national swing floor `s_nat_f` (brunder.australia.project: sd_nat^2 = s_nat_f^2 + s_nat0^2 K_n / (den + K_n); v1 has no
floor, so the band becomes far too narrow as the count piles up). This script plays the role of `nowcast_order.needed_tau` for it:

1. Earlier nights (the only two replayable ones, 2019 and 2022, each with v1's other settings): the smallest floor of
   `nowcast_order.TAU_GRID` at which that night's own 90% band of the national Labor two-party share covered the AEC's final share on
   >= 90% of its frames with 1% < counted <= 95% (`needed_floor`).
2. 2025: the model is run once per earlier night with that floor in place of v1's 0 (every other v1 setting and the same random
   numbers). Probability of every market = equal-weight mean of the components' probabilities (equivalent to pooling their
   draws); national two-party share band = 5%-95% quantiles of the equal-weight mixture of normals (`nowcast_order.mixture_band`,
   same centre, sd_k = sqrt(sd_v1^2 + floor_k^2)); Labor's seat count = the pooled draws.
Nothing from the 2025 count or result chooses a floor or a weight. The mixture itself was adopted after the result was known.

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mix_au.py run      # -> $BRUNDER_DATA/results/mix/au/
    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mix_au.py export   # -> site/data/au2025 (new fields only)
"""
from __future__ import annotations

import json
import pickle
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from brunder import australia as au  # noqa: E402
from brunder import nowcast_order as no  # noqa: E402
from brunder.paths import DATA, ROOT  # noqa: E402

CFG = ROOT / "configs" / "au2025_v1.toml"
OUT = DATA / "results" / "mix" / "au"
EARLIER = (2019, 2022)                       # strictly earlier, replayable count nights in the AEC archive
KEY = "mix"
LABEL = "tau mixture over earlier nights (chosen after the result was known)"
CONFIG = "analysis/mix_au.py"
NOTE = ("Instead of one setting for how wrong the early count can be, the model is run twice, once with the national "
        "swing error that the 2019 night needed and once with the one the 2022 night needed (equal weights, the seat draws "
        "pooled); the 2025 count and result chose nothing, but the idea was adopted after the result was known.")


def mix_sd(sd_nat: float, floor: float) -> float:
    """National swing sd with a floor added in quadrature (v1 has s_nat_f = 0, so sd_nat is the floorless value)."""
    return float(np.sqrt(sd_nat**2 + floor**2))


def needed_floor(final: float, proj, sd, counted, grid=no.TAU_GRID) -> float:
    """Smallest floor of `grid` at which the 90% band of the national share (proj +- 1.645 sqrt(sd^2 + floor^2)) covers `final` on
    >= 90% of the frames with 1% < counted <= 95%; the share enters `nowcast_order.needed_tau` as a normal with margin_sd0 = sd and
    two_r = 1, total = 1/2 (so the sd of the share is sqrt(sd^2 + tau^2))."""
    proj, sd, counted = (np.asarray(x, float) for x in (proj, sd, counted))
    w = (counted > 0.01) & (counted <= 0.95)
    return no.needed_tau(final - proj[w], sd[w], np.ones(w.sum()), np.full(w.sum(), 0.5), grid)


def national(d: dict, p: dict) -> pd.DataFrame:
    """Per frame: counted, national Labor 2PP projection and its sd (v1 settings), as analysis/au_score.replay computes them."""
    S = d["static"]
    ids = sorted(S["divs"])
    n = len(ids)
    tot_booth = {did: sum(b["hist_formal"] for b in S["booths"][did]) for did in ids}
    all_hist = sum(S["booth_hist"].values())
    w2pp = np.array([(sum(S["divs"][i]["hist_tpp"].values()) if S["divs"][i]["hist_tpp"] else 0) *
                     S["divs"][i]["enrol"] / max(S["divs"][i]["enrol_hist"], 1) for i in ids], dtype=float)
    w2pp /= w2pp.sum()
    prior2 = np.array([S["divs"][i]["hist_tpp"][1] / sum(S["divs"][i]["hist_tpp"].values()) if S["divs"][i]["hist_tpp"] else 0.5
                       for i in ids])
    rows = []
    for t, o in d["obs"]:
        pj = au.project(o, S, p)
        proj = np.empty(n)
        for i, did in enumerate(ids):
            x = o.get(did, {})
            tp = x.get("tpp") or {}
            ca = tp[1] / (tp[1] + tp[2]) if tp and tp.get(1, 0) + tp.get(2, 0) > 0 else -1
            fc = min(1.0, x.get("booth_hist", 0) / tot_booth[did]) if tot_booth[did] else 0.0
            if pj["pair"][i] == ("ALP", "LNC"):
                proj[i] = pj["mean"][i]
            else:
                proj[i] = prior2[i] + pj["st_mu"].get(pj["state"][i], 0.0)
                if ca >= 0 and fc >= 0.9:
                    proj[i] = ca
        wst = {}
        for i in range(n):
            wst[pj["state"][i]] = wst.get(pj["state"][i], 0.0) + w2pp[i]
        var = pj["sd_nat"] ** 2 + sum((wst[s] * pj["st_sd"][s]) ** 2 for s in wst) + sum((w2pp[i] * pj["sd_div"][i]) ** 2 for i in range(n))
        rows.append({"t": t.ceil("min"), "counted": sum(x["booth_hist"] for x in o.values()) / all_hist,
                     "proj2pp": float((w2pp * proj).sum()), "sd2pp": float(var ** 0.5)})
    return pd.DataFrame(rows)


def final_2pp(d: dict) -> float:
    f2 = [d["final"][i]["tpp"] for i in sorted(d["static"]["divs"]) if d["final"][i]["tpp"]]
    a, b = sum(v[1] for v in f2), sum(v[2] for v in f2)
    return a / (a + b)


def load(year: int) -> dict:
    with open(DATA / "au" / f"obs_{year}.pkl", "rb") as f:
        return pickle.load(f)


def run() -> None:
    cfg = tomllib.loads(CFG.read_text(encoding="utf-8"))
    p = cfg["model"]
    assert p["s_nat_f"] == 0.0, "components add the floor to v1's floorless national sd"
    OUT.mkdir(parents=True, exist_ok=True)
    floors, nights = [], {}
    for y in EARLIER:
        d = load(y)
        s = national(d, p)
        fl = needed_floor(final_2pp(d), s["proj2pp"], s["sd2pp"], s["counted"])
        w = s[(s["counted"] > 0.01) & (s["counted"] <= 0.95)]
        cov0 = float((np.abs(final_2pp(d) - w["proj2pp"]) < 1.645 * w["sd2pp"]).mean())
        nights[y] = {"needed_floor": fl, "frames": int(len(w)), "coverage90_at_floor0": round(cov0, 3)}
        floors.append(fl)
        print(y, nights[y], flush=True)
    d = load(2025)
    S, fin = d["static"], d["final"]
    ids = sorted(S["divs"])
    f_seats, _ = au.seats_of_final(fin, ids)
    rows = []
    for t, o in d["obs"]:
        pj = au.project(o, S, p)
        n_pair = sum(1 for x in pj["pair"] if x)
        row = {"t": t.ceil("min"), "created": t, "n_pair": n_pair}
        if n_pair < 100:                        # not used by the site (no usable two-candidate count yet)
            rows.append(row)
            continue
        pk, seats_all = {}, []
        for f in floors:
            q = {**pj, "sd_nat": mix_sd(pj["sd_nat"], f)}
            sim = au.simulate(q, o, S, p, cfg["draws"], np.random.default_rng(cfg["seed"]))
            seats_all.append(sim["seats"][:, 0])
            for m in cfg["markets"]:
                k = f"P:{m['platform']}:{m['market']}"
                pk.setdefault(k, []).append(float(au.market_hit(m, sim["seats"], sim["win"], ids).mean()))
        sa = np.concatenate(seats_all)
        row.update({k: float(np.mean(v)) for k, v in pk.items()})
        row.update({"seats_alp": float(sa.mean()), "seats_alp_sd": float(sa.std())})
        rows.append(row)
    R = pd.DataFrame(rows)
    N = national(d, p)
    R["counted"] = N["counted"].to_numpy()
    R["proj2pp"] = N["proj2pp"].to_numpy()
    R["sd2pp"] = N["sd2pp"].to_numpy()
    ok = R["n_pair"] >= 100
    lo, hi = no.mixture_band(R.loc[ok, "proj2pp"], R.loc[ok, "sd2pp"], np.ones(ok.sum()), np.full(ok.sum(), 0.5), floors)
    R.loc[ok, "lo2pp"], R.loc[ok, "hi2pp"] = lo, hi
    R.to_csv(OUT / "releases.csv", index=False)
    (OUT / "components.json").write_text(json.dumps({"floors": floors, "nights": nights, "final_seats_alp": int(f_seats[0])}, indent=1))
    print("floors", floors)


def export() -> None:
    from sens_export import write_sensitivity, write_v2_block
    cfg = tomllib.loads(CFG.read_text(encoding="utf-8"))
    comp = json.loads((OUT / "components.json").read_text())
    R = pd.read_csv(OUT / "releases.csv", parse_dates=["t", "created"])
    R = R[R["n_pair"] >= 100].drop_duplicates("t", keep="last").sort_values("t")
    d = load(2025)
    final = final_2pp(d)
    races: dict[str, list] = {}
    for m in cfg["markets"]:
        races.setdefault(m["race"], []).append(m)
    series, finals = {}, {}
    for rid, ms in races.items():
        lead = next((m for m in ms if m["platform"] == "polymarket"), None) or ms[0]
        df = pd.DataFrame({"t": R["t"], "P": R[f"P:{lead['platform']}:{lead['market']}"]})
        if rid == "most-alp":
            df["share"], df["lo"], df["hi"] = R["proj2pp"], R["lo2pp"], R["hi2pp"]
        series[rid] = df
        finals[rid] = final if rid == "most-alp" else None
    fl = comp["floors"]
    seat = R[(R["counted"] > 0.01) & (R["counted"] <= 0.95)]
    zs = (comp["final_seats_alp"] - seat["seats_alp"]) / seat["seats_alp_sd"].clip(lower=0.8)
    extra = {"seats_coverage90_1_95": {"coverage90": round(float((zs.abs() <= 1.645).mean()), 3), "mean_z": round(float(zs.mean()), 2),
                                       "n": int(len(zs)), "final_seats_labor": comp["final_seats_alp"],
                                       "definition": "Labor's final seat count against the pooled draws' mean +- 1.645 sd (sd floored at 0.8), as the "
                                                     "v1 headline calibration is defined"},
             "components": {"floors": fl, "nights": {str(k): v for k, v in comp["nights"].items()}}}
    write_sensitivity("au2025", KEY, LABEL, CONFIG, NOTE, series, finals, "most-alp", extra_stats=extra)
    write_v2_block("au2025", finals, "most-alp")
    seat_headline(extra["seats_coverage90_1_95"]["coverage90"])


def seat_headline(mix_seats: float) -> None:
    """The v1 headline for Australia is Labor's SEAT count inside its band (stats.calibration.most-alp), not the two-party share. So that the
    homepage compares like with like, `summary.coverage90_headline` of v2 and mix is the same seat-count definition; the two-party-share
    coverage stays beside it as `coverage90_2pp_headline`."""
    path = ROOT / "site" / "data" / "au2025" / "stats.json"
    st = json.loads(path.read_text())
    sens = st["sensitivities"]
    for key, seats in (("mix", mix_seats), ("v2", st["sensitivity_v2"]["coverage90"]["alp_seats"]["coverage90"])):
        s = sens[key]["summary"]
        s.setdefault("coverage90_2pp_headline", s["coverage90_headline"])
        s["coverage90_headline"] = round(float(seats), 3)
        s["coverage90_headline_definition"] = "Labor seat count inside the 90% band over frames with 1% < counted <= 95% (the definition of stats.calibration.most-alp)"
    path.write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    {"run": run, "export": export}[sys.argv[1]]()
