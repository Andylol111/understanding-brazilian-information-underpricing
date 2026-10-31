"""Tau-mixture sensitivity on every real-order count night that can be rebuilt from raw data on the SSD ($BRUNDER_DATA).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/mix_replay.py br2026r1 [mx2024 pl2025r2 ...]   (no argument = all)

The point forecast is the base model of `brunder.nowcast` (rho 0.8, kappa 50,000, the settings the model review used). Only the
uncertainty differs: instead of one reporting-order error `tau`, the forecast is an equal-weight mixture, one component per EARLIER
count night, each at the smallest `tau` (grid of nowcast_order.TAU_GRID) at which that night's own 90% band held the final share
(`nowcast_order.needed_tau`, computed on that night's frames from `model_review_frames.py`, 1% < counted <= 95%). Chronological: a
night's mixture uses only nights strictly before its date (nights in analysis/model_review_loeo.DATES, the nine real-order nights);
nothing about the night being shown is used. The mixture, like the rest of the model review, was chosen after the 2026 results were
known, so it is a labelled retrospective sensitivity.

Output: site/data/<id>/{timeline,meta,stats}.json gain P_sens_mix (+ share_sens_mix / sd_sens_mix on share races), meta.sensitivities
and stats.sensitivities.{mix,v2} through analysis/sens_export.py. Intermediate series: $BRUNDER_DATA/results/mix/<id>.parquet.
Run `uv run python analysis/decided_stats.py <id>` afterwards.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import model_review_loeo as L  # noqa: E402
import model_review_tau_mix as TM  # noqa: E402
import mr_nights_br  # noqa: E402
import mr_nights_mx  # noqa: E402
import mr_nights_pl  # noqa: E402
from mr_common import BRUNDER_DATA  # noqa: E402
from sens_export import DATA, write_sensitivity, write_v2_block  # noqa: E402

from brunder import nowcast as nc  # noqa: E402
from brunder import nowcast_order as no  # noqa: E402
from brunder import poland as pl  # noqa: E402

OUT = BRUNDER_DATA / "results" / "mix"
LABEL = "tau mixture over earlier nights (chosen after the result was known)"
NIGHTS = {**mr_nights_br.NIGHTS, **mr_nights_mx.NIGHTS, **mr_nights_pl.NIGHTS}
RHO, KAPPA = 0.8, 50_000.0
# election id -> (night key, headline race, v1 settings for the reproduction check: (rho, kappa, tau))
ELECTIONS = {
    "br2022r1": ("br2022r1", "br", (0.8, 50_000.0, 0.015)),
    "br2022r2": ("br2022r2", "br", (0.8, 50_000.0, 0.015)),
    "br2026r1": ("br2026r1", "br", (0.8, 50_000.0, 0.015)),
    "br2026r2": ("br2026r2", "br", (0.8, 50_000.0, 0.015)),
    "mx2024": ("mx2024", "mx", (0.8, 50_000.0, 0.01)),
    "pl2025r1": ("pl2025r1", "winner", (0.5, 100_000.0, 0.04)),
    "pl2025r2": ("pl2025r2", "winner", (0.8, 100_000.0, 0.005)),
    # six governor runoffs: each state is its own night (own sections, own prior); the taus are those of the nights before 2026-10-25
    "br2026r2gov": ("br2026r1", None, (0.8, 50_000.0, 0.015)),
}
GOV = {"br2026r2gov": ("gov-{uf}", "governor_r2/night_gov_r2_{uf}_v1", "2026-10-25")}


def training(night_key: str) -> tuple[list[str], list[float]]:
    """Earlier nights (strictly before this night's date) and the tau each needed, from the stage-1 frames on the SSD."""
    fr = L.load()
    fr = fr[(fr["rho"] == RHO) & (fr["kappa"] == KAPPA) & (fr["cov"] == "none")]
    nights = sorted(fr["night"].unique(), key=lambda n: L.DATES[n])
    train = [n for n in nights if L.DATES[n] < L.DATES[night_key]]
    return train, TM.training_taus(fr, train)


def scope_of(eid: str, race: str) -> str | None:
    if eid.startswith("br"):
        return None if race == "br" else race
    return None


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"uf": pd.Series(dtype=str), "cd_mun": pd.Series(dtype=str), "n": pd.Series(dtype=float),
                         "a": pd.Series(dtype=float), "f": pd.Series(dtype=float)})


def gov_night(eid: str, uf: str):
    """One governor-runoff night as a municipal Night (same construction as the presidential nights)."""
    _, cfg_id, date = GOV[eid]
    mr_nights_br._SPEC[f"{eid}_{uf}"] = (cfg_id.format(uf=uf), date, "2026 governor first round by municipality, same two candidates")
    return mr_nights_br.build(f"{eid}_{uf}")


def replay(eid: str, scopes: list[str | None], p: nc.Params, night=None) -> tuple[dict, dict]:
    """tau = 0 nowcast of every scope at every distinct count state; returns ({scope: DataFrame}, {scope: final share})."""
    night = night or NIGHTS[ELECTIONS[eid][0]]()
    p = dataclasses.replace(p, unpooled=night.unpooled)          # abroad (Brazil, Poland) / pseudo-units (Mexico) stay out of the national swing
    tl0 = pd.Timestamp(json.loads((DATA / eid / "meta.json").read_text())["poll_close_utc"])
    recs = {s: [] for s in scopes}

    def evaluate(t, st):
        for s in scopes:
            r = nc.nowcast(st, night.prior, p, s)
            recs[s].append({"t": t, "counted": r["counted_frac"], "share_hat": r["share_hat"], "share_sd0": r["share_sd"],
                            "margin_hat": r["margin_hat"], "margin_sd0": r["margin_sd"]})

    evaluate(tl0.tz_convert("UTC") if tl0.tzinfo else tl0.tz_localize("UTC"), _empty())
    last_sig, last = None, None
    last_state = None
    for t, st in night.ticks(1):
        sig = (len(st), round(float(st["n"].sum()), 3), round(float(st["f"].sum()), 6))
        if sig == last_sig:
            continue
        last_sig, last_state = sig, st
        evaluate(t, st)
    finals = {}
    for s in scopes:
        if s is None:
            finals[s] = night.final
        else:
            u = last_state[last_state["uf"] == s]
            finals[s] = float(u["a"].sum() / u["n"].sum())
    return {s: pd.DataFrame(v) for s, v in recs.items()}, finals


def mixture_series(d: pd.DataFrame, taus: list[float]) -> pd.DataFrame:
    sd0 = d["share_sd0"].to_numpy(float)
    msd = d["margin_sd0"].to_numpy(float)
    ok = sd0 > 0
    total = np.where(ok, msd / (2 * np.where(ok, sd0, 1.0)), 1.0)
    two_r = np.where(ok, 2 * total * (1 - d["counted"].to_numpy(float)), 0.0)
    lo, hi = no.mixture_band(d["share_hat"], msd, two_r, total, taus)
    p = no.mixture_pwin(d["margin_hat"], msd, two_r, taus)
    return pd.DataFrame({"t": d["t"], "P": p, "share": d["share_hat"].to_numpy(), "lo": lo, "hi": hi,
                         "_msd": msd, "_two_r": two_r, "_total": total, "_margin_hat": d["margin_hat"].to_numpy(), "_sh": d["share_hat"].to_numpy()})


def band_series(m: pd.DataFrame, taus: list[float], side: str, lo: float, hi: float) -> pd.DataFrame:
    """Poland margin-band market: mean over the taus of band_p(share_hat, sd(tau)) (equal-weight mixture of normals)."""
    sd = no._sd_matrix(m["_msd"], m["_two_r"], m["_total"], taus)
    pp = np.mean([pl.band_p(m["_sh"].to_numpy(), sd[:, j], side, lo, hi) for j in range(len(taus))], axis=0)
    return pd.DataFrame({"t": m["t"], "P": pp})


def run(eid: str) -> None:
    key, headline, (rho1, kappa1, tau1) = ELECTIONS[eid]
    gov = eid in GOV
    train, taus = training(key if not gov else "br2026r2")
    p = nc.Params(tau=0.0, rho=RHO, kappa=KAPPA, unpooled=("zz",))
    tl = json.loads((DATA / eid / "timeline.json").read_text())
    races = list(tl)
    scopes = sorted({scope_of(eid, r) for r in races}, key=lambda x: (x is not None, x or ""))
    print(f"{eid}: training nights {train} taus {taus}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    series, finals = {}, {}
    if gov:                      # one night per race
        for race in races:
            uf = race.split("-", 1)[1]
            D1, f1 = replay(eid, [None], p, gov_night(eid, uf))
            series[race] = mixture_series(D1[None], taus)[["t", "P", "share", "lo", "hi"]]
            finals[race] = f1[None]
            pd.DataFrame(D1[None]).to_parquet(OUT / f"{eid}_{uf}.parquet")
        finish(eid, headline, train, taus, series, finals)
        return
    D, finals_scope = replay(eid, scopes, p)
    pd.concat([d.assign(scope=s or "national") for s, d in D.items()]).to_parquet(OUT / f"{eid}.parquet")
    if eid.startswith("pl"):
        cfg_races = {r["id"]: r for r in tomllib.loads((DATA.parents[1] / "configs" / f"{eid}_v1.toml").read_text())["race"]}
    M = {s: mixture_series(d, taus) for s, d in D.items()}
    for race in races:
        s = scope_of(eid, race)
        if eid.startswith("pl") and cfg_races[race]["kind"] == "band":
            c = cfg_races[race]
            series[race], finals[race] = band_series(M[s], taus, c["side"], c["lo"], c["hi"]), None
        else:
            series[race] = M[s][["t", "P", "share", "lo", "hi"]]
            finals[race] = finals_scope[s]
    finish(eid, headline, train, taus, series, finals)
    check_v1(eid, D[scope_of(eid, headline)], tl[headline], tau1, rho1, kappa1)


def finish(eid: str, headline, train: list[str], taus: list[float], series: dict, finals: dict) -> None:
    names = ", ".join(f"{n} {t:g}" for n, t in zip(train, taus))
    note = (f"An equal-weight mixture over the reporting-order errors that {len(train)} earlier count nights needed ({names}). Same point forecast as the base model "
            f"(rho {RHO:g}, kappa {KAPPA:,.0f}); only the band and the win chance allow for those {len(train)} different errors instead of one. "
            "Built from earlier nights only, but chosen after this result was known, so it is a sensitivity, not a forecast.")
    block = write_sensitivity(eid, "mix", LABEL, "analysis/mix_replay.py", note, series, finals, headline,
                              extra_stats={"training": {"scheme": "chronological", "nights": train, "taus": dict(zip(train, taus)),
                                                        "components": len(train), "point_forecast": {"rho": RHO, "kappa": KAPPA}}})
    write_v2_block(eid, finals, headline)
    s = block["summary"]
    print(f"  {eid} mix: headline coverage {s['coverage90_headline']} MAE {s['mae_pt_headline']} lead(median, gate) {s['median_lead_90_min_gate1pct']} "
          f"mean coverage {s['coverage90_mean_share_races']} n={s['n_contested_with_lead']}", flush=True)


def check_v1(eid: str, d: pd.DataFrame, rows: list[dict], tau1: float, rho1: float, kappa1: float) -> None:
    """Sanity: the tau = 0 run re-scaled to the v1 tau reproduces the published v1 share sd (only meaningful where v1 used the same rho / kappa)."""
    sd0, msd = d["share_sd0"].to_numpy(float), d["margin_sd0"].to_numpy(float)
    ok = sd0 > 0
    total = np.where(ok, msd / (2 * np.where(ok, sd0, 1.0)), 1.0)
    two_r = np.where(ok, 2 * total * (1 - d["counted"].to_numpy(float)), 0.0)
    sd1 = pd.Series(np.sqrt(msd**2 + (two_r * tau1) ** 2) / (2 * total), index=pd.DatetimeIndex(d["t"]))
    sh = pd.Series(d["share_hat"].to_numpy(), index=pd.DatetimeIndex(d["t"]))
    pub = pd.DataFrame(rows)
    pub["t"] = pd.to_datetime(pub["t"], utc=True)
    pub = pub.set_index("t")
    sd1 = sd1[~sd1.index.duplicated(keep="last")]
    sh = sh[~sh.index.duplicated(keep="last")]
    a = sd1.reindex(sd1.index.union(pub.index)).ffill().reindex(pub.index)
    b = sh.reindex(sh.index.union(pub.index)).ffill().reindex(pub.index)
    m = pub["share"].notna() & pub["counted"].between(0.01, 0.95)
    print(f"  v1 check ({eid}, same rho/kappa: {(rho1, kappa1) == (RHO, KAPPA)}): max |share - published| {np.nanmax(np.abs(b - pub['share'])[m]):.4f}, "
          f"max |sd - published| {np.nanmax(np.abs(a - pub['sd'])[m]):.4f}", flush=True)


def main(argv: list[str]) -> None:
    for eid in argv or list(ELECTIONS):
        run(eid)


if __name__ == "__main__":
    main(sys.argv[1:])
