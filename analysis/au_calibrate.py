"""Choose the Australian count-night model's settings on earlier elections' count nights, replayed in feed order.

For each earlier election the per-frame statistics (analysis/au_observe.py) are projected with candidate settings and
scored against that election's final division results, on the frames with 2%..95% of the expected polling-place votes in:

* division level: normal log score of the final two-candidate share, by kind of evidence (places matched to the
  previous election / not matched / pair with no usable prior),
* national level: normal log score of Labor's final seat count (from 1,500 joint draws) and of its two-party share.

The national swing prior (`s_nat0`) is the root-mean-square of the national two-party swings of ALL EARLIER elections
that the archive can measure (the election's own preload gives the notional start).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/au_calibrate.py 2022 2019 --out results/australia/calibration_2025.json
    (settings for election Y = fit on the elections listed; 2022 and 2019 for 2025; 2019 alone for the 2022 check)
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from brunder import australia as au  # noqa: E402
from brunder.paths import DATA, ROOT

AU = DATA / "au"


def load(year: int) -> dict:
    with open(AU / f"obs_{year}.pkl", "rb") as f:
        d = pickle.load(f)
    S = d["static"]
    S["prev"] = {}
    total = sum(S["booth_hist"].values())
    d["total_hist"] = total
    d["year"] = year
    return d


def national_swing(year: int) -> float:
    """Final Labor two-party share minus the AEC's notional share at the start (preload), national."""
    import au_observe as ao
    event = ao.ELECTIONS[year][0]
    pre = ao.preload(event)
    S = {"divs": pre["divs"]}
    fin = au.final_divisions(ao.final_frame(event), pre)
    a0 = sum(v["hist_tpp"][1] for v in S["divs"].values() if v["hist_tpp"])
    b0 = sum(v["hist_tpp"][2] for v in S["divs"].values() if v["hist_tpp"])
    a1 = sum(v["tpp"][1] for v in fin.values() if v["tpp"])
    b1 = sum(v["tpp"][2] for v in fin.values() if v["tpp"])
    return a1 / (a1 + b1) - a0 / (a0 + b0)


def truth(d: dict) -> dict:
    fin = d["final"]
    seats = {g: 0 for g in au.SEAT_GROUPS}
    for v in fin.values():
        if v["winner"]:
            seats[au.seat_group(v["winner"])] += 1
    return {"seats": seats}


def frames_in_range(d: dict, lo: float = 0.02, hi: float = 0.95, step: int = 6):
    tot = d["total_hist"]
    out = []
    for t, o in d["obs"]:
        f = sum(x["booth_hist"] for x in o.values()) / tot
        if lo <= f <= hi:
            out.append((t, o, f))
    return out[::step]


def div_share(fin_d: dict, pair) -> float | None:
    tcp = fin_d["tcp"]
    if set(tcp) == set(pair) and sum(tcp.values()) > 0:
        return tcp[pair[0]] / sum(tcp.values())
    return None


def band_rules() -> list[dict]:
    """The seat-count market rules of the 2025 config (most seats, majority, hung, bands, margins): generic events scored
    on every earlier night too (their thresholds mean the same in any election with ~150 seats)."""
    import au_config
    seen, out = set(), []
    for _, _, rule, _ in au_config.RACES:
        if rule["kind"] == "div":
            continue
        k = json.dumps(rule, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(rule)
    return out


def evaluate(d: dict, p: dict, fr: list, draws: int = 1500, seed: int = 7) -> dict:
    S, fin = d["static"], d["final"]
    rng = np.random.default_rng(seed)
    nll = {"matched": [], "prior": [], "unmatched": [], "noprior": []}
    zs = {k: [] for k in nll}
    seat_nll, seat_z, share_nll = [], [], []
    ids = sorted(S["divs"])
    wtruth = np.array([au.SEAT_GROUPS.index(au.seat_group(fin[i]["winner"])) if fin[i]["winner"] else 3 for i in ids])
    T = truth(d)["seats"]["ALP"]
    win_all, win_fb, ev_b = [], [], []
    rules = band_rules()
    f_seats, f_win = au.seats_of_final(fin, ids)
    y_ev = np.array([bool(au.market_hit(r, f_seats, f_win, ids)[0]) for r in rules], dtype=float)
    for _, o, _f in fr:
        pj = au.project(o, S, p)
        for i, did in enumerate(pj["ids"]):
            pr = pj["pair"][i]
            if pr is None:
                continue
            y = div_share(fin[did], pr)
            if y is None:
                continue
            sd2 = pj["sd_div"][i] ** 2
            if pj["classic"][i]:
                sd2 += pj["st_sd"][pj["state"][i]] ** 2 + pj["sd_nat"] ** 2
            z = (y - pj["mean"][i]) / math.sqrt(sd2)
            nll[pj["kind"][i]].append(0.5 * z * z + 0.5 * math.log(sd2))
            zs[pj["kind"][i]].append(z)
        sim = au.simulate(pj, o, S, p, draws, rng)
        pe = np.array([au.market_hit(r, sim["seats"], sim["win"], ids).mean() for r in rules])
        ev_b.append(float(((pe - y_ev) ** 2).mean()))
        pw = (sim["win"] == wtruth[None, :]).mean(0)
        nll_w = -np.log(np.clip(pw, 0.02, 1))
        win_all.append(float(nll_w.mean()))
        fbm = np.array([bool(o.get(i, {}).get("fallback")) for i in ids])
        if fbm.any():
            win_fb.append(float(nll_w[fbm].mean()))
        s = sim["seats"][:, 0]
        m, sd = s.mean(), max(s.std(), 0.8)
        seat_nll.append(0.5 * ((T - m) / sd) ** 2 + math.log(sd))
        seat_z.append((T - m) / sd)
    cov = {k: float(np.mean(np.abs(v) <= 1.645)) if v else None for k, v in zs.items()}
    return {"nll": {k: float(np.mean(v)) if v else None for k, v in nll.items()}, "n": {k: len(v) for k, v in nll.items()},
            "seat_nll": float(np.mean(seat_nll)), "seat_cov90": float(np.mean(np.abs(seat_z) <= 1.645)),
            "cov90": cov, "seat_z_mean": float(np.mean(seat_z)), "win_nll": float(np.mean(win_all)), "event_brier": float(np.mean(ev_b)),
            "win_nll_fallback": float(np.mean(win_fb)) if win_fb else 0.0}


def fit(ds: list[dict], base: dict, variant: int = 1) -> tuple[dict, dict]:
    """Coordinate search on small grids; objective = division log score (weighted by n) + national seat log score."""
    frs = [frames_in_range(d) for d in ds]

    def total(p: dict) -> tuple[float, list]:
        rs = [evaluate(d, p, f, draws=800) for d, f in zip(ds, frs)]
        loss = 0.0
        for r in rs:
            for k, w in (("matched", 1.0), ("prior", 0.3), ("unmatched", 0.5), ("noprior", 0.5)):
                if r["nll"][k] is not None:
                    loss += w * r["nll"][k]
            loss += 2.0 * r["seat_nll"] / 10.0 + 0.2 * r["win_nll"] + 0.5 * r["win_nll_fallback"] + 30.0 * r["event_brier"]
        return loss, rs

    grids = {"K_d": [500, 1.5e3, 4e3, 1e4, 2.5e4], "s_d0": [0.01, 0.017, 0.025, 0.035, 0.05],
             "s_floor": [0.002, 0.004, 0.008, 0.013], "K_s": [1e3, 2.5e3, 5e3, 1.5e4, 5e4],
             "s_st0": [0.004, 0.008, 0.012, 0.02, 0.03], "K_n": [3e3, 8e3, 2e4, 6e4, 2e5], "fb": [0.02, 0.05, 0.1, 0.2, 0.5],
             "s_np": [0.04, 0.06, 0.08, 0.12], "K_np": [300, 1e3, 3e3], "m_a": [-3.0, -2.0, -1.0, 0.0],
             "m_b": [4.0, 8.0, 14.0, 24.0]}
    if variant == 2:         # sensitivity: floors under the state and national swing errors, third vs the leading major party
        grids |= {"s_nat_f": [0.0, 0.003, 0.006, 0.01], "s_st_f": [0.0, 0.003, 0.006, 0.01],
                  "m_a": [-4.0, -3.0, -2.0, -1.0, 0.0], "m_b": [8.0, 14.0, 24.0, 40.0]}
        base = dict(base, m_mode="max", s_nat_f=0.004, s_st_f=0.004)
    p = dict(base)
    best, _ = total(p)
    for sweep in range(2):
        for k, vals in grids.items():
            for v in vals:
                q = dict(p, **{k: v})
                loss, _ = total(q)
                if loss < best - 1e-9:
                    best, p = loss, q
        print("sweep", sweep, round(best, 4), {k: p[k] for k in grids}, flush=True)
    return p, {"loss": best}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("years", nargs="+", type=int, help="elections whose count nights are used")
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", type=int, default=1, choices=(1, 2))
    ap.add_argument("--check", type=int, help="also score this later election with the fitted settings (not used in the fit)")
    a = ap.parse_args()
    ds = [load(y) for y in a.years]
    swings = {}
    for y in (2016, 2019, 2022):
        swings[y] = national_swing(y)
    earlier = [v for y, v in swings.items() if y <= max(a.years)]
    base = au.default_params()
    base["s_nat0"] = float(math.sqrt(np.mean(np.square(earlier)))) if earlier else base["s_nat0"]
    p, info = fit(ds, base, a.variant)
    res = {"variant": a.variant, "fitted_on": a.years, "national_swings": swings, "settings": p, "fit": info,
           "fit_scores": {str(d["year"]): evaluate(d, p, frames_in_range(d, step=3)) for d in ds}}
    if a.check:
        c = load(a.check)
        res["check_year"] = a.check
        res["check_scores"] = evaluate(c, p, frames_in_range(c, step=3))
    out = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in res if k != "settings"}, indent=1)[:3000])
    print(json.dumps(p))


if __name__ == "__main__":
    main()
