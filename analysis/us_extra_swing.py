"""Poll swing prior of the added-source statewide configs (configs/us2026_extra/{me_sen,nv_gov,or_gov,nh_sen,nh_gov}_v1.toml).

Without it the pre-count P of a v1 config is only the 2024 president baseline (no swing): NH Senate and Governor, one
county prior, came out identical (0.5746). These races are unregistered (no frozen v1 to keep), so the poll model of
the civic v3 convention is written into v1 itself: swing_prior_mean = poll average - prior statewide share (V-weighted),
swing_prior_sd = the poll average's sd, count_scaled_terms = true, and sigma_mun0 = the county-swing spread around the
statewide swing (brunder.us.county_resid_sd, the one the overview estimate uses; the default 0.08 per county gave
Clark County alone 0.058 of statewide sd in Nevada), so before any vote P = Phi((avg - 0.5) / sd), the overview.json
estimate of the same race (within 0.01), and the count takes over as it grows. Poll values are those of
site/data/us2026/overview.json (VoteHub polls, CC BY 4.0, asof 2026-11-02), frozen here.

    uv run --frozen python analysis/us_extra_swing.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from brunder import us, us_extra_live

ROOT = Path(__file__).resolve().parents[1]
RACES = {"me-sen": "me_sen", "nv-gov": "nv_gov", "or-gov": "or_gov", "nh-sen": "nh_sen", "nh-gov": "nh_gov"}


def main() -> None:
    ov = json.loads((ROOT / "site/data/us2026/overview.json").read_text())
    races = {r["race_key"]: r for off in ("senate", "governor") for r in ov["offices"][off]["races"]}
    us_extra_live.install()
    priors = us.load_priors()
    for rk, stem in RACES.items():
        f = ROOT / "configs" / "us2026_extra" / f"{stem}_v1.toml"
        cfg = us.load_config(f)
        pri = us.race_prior(cfg, priors)
        base = us.baseline_share(pri)
        p = races[rk]["polls"]
        text = f.read_text()
        text = re.sub(r"\n# Poll swing prior \(analysis/us_extra_swing.py\).*?(?=\n+\[count\])", "", text, flags=re.S)
        text = re.sub(r"\n(poll_avg|poll_sd|poll_n|poll_last|poll_asof|swing_prior_mean|swing_prior_sd|count_scaled_terms) =.*", "", text)
        block = (f"# Poll swing prior (analysis/us_extra_swing.py): polls frozen from site/data/us2026/overview.json, asof "
                 f"{ov['as_of'][:10]} (VoteHub, CC BY 4.0); pre-count P = Phi((poll avg - 0.5) / poll sd), as the overview estimate.\n"
                 f"poll_avg = {p['avg']}          # two-party Dem share, {p['n_polls']} polls, last {p['last_date']}\n"
                 f"poll_sd = {p['sd']}\n"
                 f"swing_prior_mean = {p['avg'] - base:.4f}   # poll avg - prior statewide share {base:.4f} (V-weighted)\n"
                 f"swing_prior_sd = {p['sd']}\n"
                 f"count_scaled_terms = true   # tau / between-region terms scale in with the counted votes\n\n")
        text = text.replace("\n[model]", "\n[model]", 1)
        text = re.sub(r"sigma_mun0 = [^\n]*", f"sigma_mun0 = {us.county_resid_sd():.4f}   # county swing spread (us.county_resid_sd)", text, count=1)
        text = re.sub(r"(sigma_mun0 = [0-9.]+[^\n]*\n)", lambda m: m.group(1) + block, text, count=1)
        f.write_text(text)
        print(rk, f.name, f"avg {p['avg']} sd {p['sd']} base {base:.4f} shift {p['avg'] - base:+.4f}")


if __name__ == "__main__":
    main()
