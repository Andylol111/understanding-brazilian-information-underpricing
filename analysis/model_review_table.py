"""Cross-election model-review table: one row per election x model version, from the published site/data/*/stats.json.

    uv run python analysis/model_review_table.py            # writes data_public/model_review_table.csv and prints markdown

Read-only on site data. Numbers are the ones the dashboard publishes (coverage / mean z over the minutes with 1-95% counted,
error of the projected share at the first minute reaching 1/5/10/25/50% counted), re-aggregated across the races of an
election: coverage = mean over races (weighted by n where the stats carry n), MAE = mean over races of |projected - final|,
mean z = mean over races (the sign is the bias direction: z = (final - projected) / sd, so z > 0 = the model projected A too low).
Minutes-to-90% is the published lead of the model over the market on CONTESTED markets only (decided-before-the-count
markets excluded): positive = model first, median over markets; the 1%-gate variant is shown next to it.
Descriptive metadata (order type, unit, prior, settings and what they were chosen on) is hand-curated from docs/*.md and
the configs; each entry cites its source in `META_SOURCE`.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site" / "data"
KS = ("1%", "5%", "10%", "25%", "50%")

# id -> descriptive metadata. order: real = archived/recorded publication order; simulated = reordered final results;
# reconstructed = completion times of finished units. `settings_on` says what the settings were chosen on.
META = {
    "br2022r1": dict(country="Brazil", order="real (BU-web receipt times)", unit="municipality, 5,708 (sections within)",
                     prior="2018 R1 by municipality (Haddad vs Bolsonaro as proxy)", tau="0.015", settings_on="2026 R1 dev night, frozen before 2022 files were downloaded"),
    "br2022r2": dict(country="Brazil", order="real (BU-web receipt times)", unit="municipality, 5,708",
                     prior="2022 R1 by municipality", tau="0.015", settings_on="2026 R1 dev night (frozen before)"),
    "br2026r1": dict(country="Brazil", order="real (BU-web receipt times, rebuilt)", unit="municipality, 5,708",
                     prior="2022 R1 by municipality", tau="0.015", settings_on="this night itself (development set: in-sample)"),
    "br2026r2": dict(country="Brazil", order="real (BU-web receipt times, rebuilt)", unit="municipality, 5,716",
                     prior="2026 R1 by municipality", tau="0.015", settings_on="2026 R1 (earlier election, but result of R2 already known when frozen)"),
    "br2026r2gov": dict(country="Brazil", order="real (BU-web receipt times, rebuilt)", unit="municipality (DF: one unit)",
                        prior="own 2026 R1 governor result by municipality", tau="0.015", settings_on="presidential R1 dev night (never developed on governors)"),
    "pl2025r1": dict(country="Poland", order="real archived versions (31) but model settings from a SIMULATED night", unit="voivodeship x gmina-size class cells (128), counted marginals only",
                     prior="2020 R1 by cell (Duda stands in for Nawrocki)", tau="0.04 (rho 0.5, kappa 1e5)", settings_on="2020 runoff counted in simulated smallest-first order (48 frames)"),
    "pl2025r2": dict(country="Poland", order="real archived versions (31 per night, 14 in 1-95%)", unit="voivodeship x gmina-size class cells (128)",
                     prior="2020 runoff by cell", tau="0.005 (rho 0.8, kappa 1e5)", settings_on="2025 R1 real night alone (14 frames)"),
    "mx2024": dict(country="Mexico", order="real (acta verification times, 5-min PREP cuts)", unit="electoral section, 70,537",
                   prior="2018 cómputo by section", tau="0.01", settings_on="2018 night alone (PREP stopped at 80%)"),
    "mx2024_qc": dict(country="Mexico", order="real (PREP) + INE quick count", unit="electoral section, 70,537",
                      prior="2018 cómputo by section", tau="0.01", settings_on="2018 night alone; quick count combined by inverse variance"),
    "de2025": dict(country="Germany", order="real archived pages (129 distinct minutes; between pages the state is held)", unit="Wahlkreis 299 (177 individually dated, rest via Land/national totals)",
                   prior="2021 result on 2025 Wahlkreise", tau="sd_a 0.2, sd_b 0.9, kappa 3e6 (own module, not nowcast.py)", settings_on="2021 night only"),
    "hu2026": dict(country="Hungary", order="real official feed (40 publications, 10-min)", unit="OEVK district 106 (list: 20 counties pooled)",
                   prior="2022 district votes on 2024 map", tau="district 0.0, list 0.05", settings_on="2022 night (9 archived frames)"),
    "il2026": dict(country="Israel", order="real CEC releases (18) via recorder/mirror", unit="locality ~1,200 + double envelopes",
                   prior="2022 Knesset by locality", tau="0.01", settings_on="2021 + 2022 Wayback captures (23)"),
    "nl2025": dict(country="Netherlands", order="none (no timestamped count)", unit="national list shares", prior="2023 result + final polls", tau="n/a", settings_on="2017/2021/2023 poll errors"),
    "ca2025": dict(country="Canada", order="none (6 page captures)", unit="343 districts (not modelled)", prior="2021 transposed", tau="n/a", settings_on="n/a"),
    "qc2026": dict(country="Canada (Quebec)", order="none (3 captures)", unit="127 circonscriptions (not modelled)", prior="none", tau="n/a", settings_on="n/a"),
    "nz2023": dict(country="New Zealand", order="simulated (six orders; 'night' for the page)", unit="electorate 72", prior="2020 electorates", tau="n/a (seat model)", settings_on="n/a; no prices exist"),
    "nz2020": dict(country="New Zealand", order="simulated", unit="electorate 72", prior="2017 electorates", tau="n/a (seat model)", settings_on="in-sample"),
}

# which published race ids are the "headline" race(s) of an election for the pooled rows
HEADLINE = {"br2022r1": ["br"], "br2022r2": ["br"], "br2026r1": ["br"], "br2026r2": ["br"], "pl2025r1": ["winner"],
            "pl2025r2": ["winner"], "mx2024": ["mx"], "mx2024_qc": ["mx"], "hu2026": ["list", "seats"], "de2025": None}


def _num(x):
    return float(x) if isinstance(x, (int, float)) and np.isfinite(x) else np.nan


def _mean(xs):
    xs = [x for x in xs if np.isfinite(x)]
    return float(np.mean(xs)) if xs else np.nan


def pooled_cov(cal: dict, races=None):
    """(coverage, mean_z, n_races, minutes) over races; weighted by n if present."""
    rows = [(k, v) for k, v in (cal or {}).items() if v and (races is None or k in races)]
    if not rows:
        return np.nan, np.nan, 0, 0
    cov = [(_num(v.get("coverage90_1_95")), v.get("n") or 1) for _, v in rows]
    w = np.array([c[1] for c in cov], float)
    c = np.array([c[0] for c in cov], float)
    ok = np.isfinite(c)
    z = [_num(v.get("mean_z")) for _, v in rows]
    return (float((c[ok] * w[ok]).sum() / w[ok].sum()) if ok.any() else np.nan, _mean(z), int(ok.sum()),
            int(w[ok].sum()) if all(v.get("n") for _, v in rows) else 0)


def mae_by_counted(ee: dict, races=None):
    """mean |model error| and mean |raw tally error| (points) at each k, and the signed model mean (bias direction)."""
    out = {}
    for k in KS:
        m, r, s = [], [], []
        for race, v in (ee or {}).items():
            if not v or (races is not None and race not in races) or k not in v:
                continue
            m.append(abs(_num(v[k]["model"]))); r.append(abs(_num(v[k]["raw"]))); s.append(_num(v[k]["model"]))
        out[k] = (_mean(m), _mean(r), _mean(s))
    return out


def minutes(a, b):
    return (pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() / 60 if a and b else np.nan


def lead_from_settle(settle: dict, contested: set | None, model_key="model", market_key="polymarket"):
    """median over contested races of (market settle - model settle) in minutes; positive = model first."""
    leads = {}
    for race, v in (settle or {}).items():
        if not v or (contested is not None and race not in contested):
            continue
        if v.get("contested") is False:
            continue
        d = minutes(v.get(model_key), v.get(market_key))
        if np.isfinite(d):
            leads[race] = d
    return (float(np.median(list(leads.values()))) if leads else np.nan), leads


def contested_set(stats: dict) -> set:
    mc = (stats.get("market_classification") or {}).get("by_race") or {}
    return {r for r, v in mc.items() if not v.get("decided_before_count")}


def row(eid, version, label, cal, ee, stats, races=None, lead=None, lead_gate=None, n_contested=None, extra=None, mae_override=None):
    m = META.get(eid, {})
    cov, z, n_races, minutes_n = pooled_cov(cal, races)
    mae = mae_override or mae_by_counted(ee, races)
    r = {"election": eid, "version": version, "label": label, "country": m.get("country", ""),
         "evidence": (stats.get("evidence") or {}).get("calibration", "") if isinstance(stats.get("evidence"), dict) else "",
         "order": m.get("order", ""), "unit": m.get("unit", ""), "prior": m.get("prior", ""), "settings": m.get("tau", ""),
         "settings_on": m.get("settings_on", ""), "coverage90": cov, "mean_z": z, "n_races": n_races}
    for k in KS:
        r[f"mae_{k}"] = mae[k][0]
    for k in KS:
        r[f"rawmae_{k}"] = mae[k][1]
    for k in KS:
        r[f"bias_{k}"] = mae[k][2]
    r["lead_contested_min"] = lead
    r["lead_contested_gate1pct_min"] = lead_gate
    r["n_contested"] = n_contested
    r.update(extra or {})
    return r


def build() -> list[dict]:
    rows = []
    S = {p.parent.name: json.loads(p.read_text()) for p in SITE.glob("*/stats.json")}

    for eid in ("br2022r1", "br2022r2", "br2026r1", "br2026r2", "br2026r2gov"):
        s = S[eid]
        lc = s["lead_contested"]
        cal, ee = s["calibration"], s["early_error"]
        nat = ["br"] if "br" in cal else None
        if nat:
            rows.append(row(eid, "v1", "headline (national)", cal, ee, s, races=nat,
                            lead=_num(lc["by_race"].get("br")) if lc.get("by_race") else np.nan,
                            n_contested=1 if "br" in contested_set(s) else 0,
                            extra={"note": "national race"}))
            sens = (s.get("sensitivity") or {}).get("br")
            if sens:
                for v in ("v2",):
                    d = sens[v]
                    mae = {k: (_num(d["mae_pt_by_counted"].get(k)), np.nan, np.nan) for k in KS}
                    mkt = minutes(s["settle"]["br"].get("gate") or (s.get("settle_gated") or {}).get("br", {}).get("gate"), s["settle"]["br"].get("polymarket"))
                    lead_v = np.nan
                    if d.get("settle_90"):
                        lead_v = minutes(d["settle_90"], s["settle"]["br"].get("polymarket")) if "br" in contested_set(s) or eid.startswith("br2022") is False else np.nan
                    rows.append(row(eid, "v2 (sens.)", "retrospective sensitivity",
                                    {"br": {"coverage90_1_95": d["coverage90_1_95"], "mean_z": np.nan}}, None, s, mae_override=mae,
                                    lead=lead_v,
                                    extra={"settings": "tau 0.005 (2026) / 0.0025 (2022), kappa 1e5, rho 0.5",
                                           "settings_on": "2014/2018 simulated + 2022 real nights (rule in docs/brazil.md), chosen after results known",
                                           "note": f"mae_pt_1_95 {d.get('mae_pt_1_95')}"}))
        states = [k for k in cal if k != "br"] if nat else list(cal)
        if states:
            ec = contested_set(s)
            lead_states = [v for k, v in (lc.get("by_race") or {}).items() if k != "br"]
            rows.append(row(eid, "v1", "state/governor races pooled", cal, ee, s, races=set(states),
                            lead=_mean(lead_states) if False else (float(np.median(lead_states)) if lead_states else np.nan),
                            lead_gate=_num(lc.get("median_lead_min_gate1pct")), n_contested=len(lead_states),
                            extra={"note": f"{len(states)} races; min coverage {min(_num(cal[k]['coverage90_1_95']) for k in states):.2f}"}))

    for eid in ("pl2025r1", "pl2025r2"):
        s = S[eid]; lc = s["lead_contested"]
        rows.append(row(eid, "v1", "headline (winner race)", s["calibration"], s["early_error"], s, races={"winner"},
                        lead=_num(lc.get("by_race", {}).get("winner")), lead_gate=np.nan, n_contested=lc["n"],
                        extra={"note": "median lead over all contested markets %s" % lc.get("median_lead_min")}))
    s = S["pl2025r2"]
    sv = s["sensitivity"]
    lead2, _ = lead_from_settle(sv["settle"], None)
    rows.append(row("pl2025r2", "v2 (sens.)", "pooled settings, chosen after result", sv["calibration"], sv.get("early_error"), s, races={"winner"},
                    lead=lead_from_settle({"winner": sv["settle"]["winner"]}, None)[0],
                    extra={"settings": "tau 0.03, rho 0.5, kappa 1e5",
                           "settings_on": "2025 R1 real night + 2020 runoff simulated (chosen after the result)"}))
    sim = s["sensitivity_2020_simulation_settings"]
    rows.append(row("pl2025r2", "v1b (sens.)", "2020-simulation settings", {"winner": sim["calibration_winner"]}, sim.get("early_error"), s, races={"winner"},
                    lead=lead_from_settle({"winner": sim["settle_winner"]}, None)[0],
                    extra={"settings": "tau 0.04, rho 0.5, kappa 1e5", "settings_on": "2020 runoff simulated night only"}))

    s = S["mx2024"]
    sv = s["sensitivity"]
    for v in ("v1", "v2"):
        d = sv[v]
        mae = {k: (abs(_num(d["share_error_pt"][k]["model"])), abs(_num(d["share_error_pt"][k]["raw"])), _num(d["share_error_pt"][k]["model"])) for k in KS}
        rows.append(row("mx2024", v if v == "v1" else "v2 (sens.)", "vs the official cómputo", {"mx": {"coverage90_1_95": d["coverage90_vs_computo"], "mean_z": d["mean_z_vs_computo"]}},
                        None, s, mae_override=mae, lead=np.nan, n_contested=0,
                        extra={"settings": "tau 0.01" if v == "v1" else "tau 0.015, rho 1.0, kappa 1e4",
                               "settings_on": "2018 night alone" if v == "v1" else "2012 + 2018 nights (chosen after the result)",
                               "note": f"coverage vs end-of-PREP share {d['coverage90_vs_prep_end']}; market had decided before the count"}))
    rows.append(row("mx2024_qc", "v1+quick count", "PREP + INE quick count", S["mx2024_qc"]["calibration"], S["mx2024_qc"]["early_error"], S["mx2024_qc"], lead=np.nan, n_contested=0))

    s = S["hu2026"]; lc = s["lead_contested"]
    rows.append(row("hu2026", "v1", "district-list/seats pooled", s["calibration"], s["early_error"], s, races={"list", "seats"},
                    lead=_num(lc.get("median_lead_min")), lead_gate=_num(lc.get("median_lead_min_gate1pct")), n_contested=lc["n_contested_markets"],
                    extra={"note": "list tau 0.05; seats use the district model + seat law"}))
    sv = s["sensitivity"]
    cv = sv.get("calibration") or {}
    rows.append(row("hu2026", "v2 (sens.)", "settings from 2022/2018/2014, after result", cv, sv.get("early_error"), s, races={"list", "seats"},
                    lead=lead_from_settle(sv.get("settle"), None)[0],
                    extra={"settings": "district tau 0.015, sigma_mun0 0.03; list tau 0.03", "settings_on": "2022 real + 2018/2014 simulated (chosen after the result)"}))

    s = S["de2025"]; lc = s["lead_contested"]
    rows.append(row("de2025", "v1", "8 parties / thresholds", s["calibration"], s["early_error"], s, lead=_num(lc.get("median_lead_min")),
                    lead_gate=_num(lc.get("median_lead_min_gate1pct")), n_contested=lc["n_contested_markets"],
                    extra={"note": "coverage mean over 9 markets; market had settled 42/46 markets from the exit poll before any count"}))
    sv = s["sensitivity_v2"]
    rows.append(row("de2025", "v2 (sens.)", "kappa 3e7, after result", sv["calibration"], sv.get("early_error"), s, lead=lead_from_settle(sv.get("settle"), None)[0],
                    extra={"settings": "kappa 3e7, sd_a 0.2, sd_b 0.931", "settings_on": "2021 night only (chosen after the result)"}))

    s = S["il2026"]; lc = s["lead_contested"]
    cl = s["calibration_list_shares"]
    ee = {"list": {k: {"model": v["model"], "raw": v["raw"]} for k, v in s["early_error_list_shares_mae"].items()}}
    rows.append(row("il2026", "v1", "list shares (MAE, vs provisional outcome)", {"list": cl}, ee, s, lead=_num(lc.get("median_lead_min")),
                    lead_gate=_num(lc.get("median_lead_min_gate1pct")), n_contested=lc["n_contested_markets"],
                    extra={"note": "15 releases only; envelopes uncounted in the outcome; first release 0.1% counted"}))

    for eid in ("nl2025", "ca2025", "qc2026", "nz2023", "nz2020"):
        rows.append(row(eid, "no count model scored", "", None, None, S[eid], extra={"note": "; ".join(f"{k}: {v}" for k, v in list((S[eid].get('reasons') or {}).items())[:1])}))
    return rows


def md(rows: list[dict]) -> str:
    f = lambda x, d=2: "–" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f"{x:.{d}f}" if isinstance(x, (int, float, np.floating)) else str(x))
    out = ["| election | version | 90% cov | mean z | MAE pt at 1 / 5 / 10 / 25 / 50% counted | raw-tally MAE at 1 / 5 / 10 / 25% | lead on contested, min (from 1% gate) |\n",
           "|---|---|---:|---:|---|---|---:|\n"]
    for r in rows:
        if not np.isfinite(_num(r["coverage90"])) and not np.isfinite(_num(r["mae_1%"])):
            continue
        mae = " / ".join(f(r[f"mae_{k}"], 2) for k in KS)
        raw = " / ".join(f(r[f"rawmae_{k}"], 2) for k in KS[:4])
        lead = f(r["lead_contested_min"], 0) + (f" ({f(r['lead_contested_gate1pct_min'], 0)})" if np.isfinite(_num(r.get("lead_contested_gate1pct_min"))) else "")
        out.append(f"| {r['election']} | {r['version']}, {r['label']} | {f(r['coverage90'])} | {f(r['mean_z'])} | {mae} | {raw} | {lead} |\n")
    out.append("\n| election | order | unit | prior | settings (chosen on) |\n|---|---|---|---|---|\n")
    seen = set()
    for r in rows:
        if r["election"] in seen:
            continue
        seen.add(r["election"])
        out.append(f"| {r['election']} | {r['order']} | {r['unit']} | {r['prior']} | {r['settings']} ({r['settings_on']}) |\n")
    return "".join(out)


def main():
    rows = build()
    out = ROOT / "data_public" / "model_review_table.csv"
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        w.writeheader()
        w.writerows(rows)
    print(md(rows))
    print("wrote", out)


if __name__ == "__main__":
    main()
