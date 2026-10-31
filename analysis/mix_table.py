"""Markdown table of original (v1) vs recalibrated (v2) vs tau mixture, from site/data/*/stats.json only (no hand-typed numbers).

    uv run python analysis/mix_table.py
"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "site" / "data"


def fmt(x, pct=False):
    return "–" if x is None else (f"{100 * x:.0f}%" if pct else f"{x:+.0f}")


def main():
    rows = ["| election | headline | 90% band held: original / v2 / mixture | mean over share races: v2 / mixture | lead on contested markets, min (n): original / v2 / mixture |", "|---|---|---|---|---|"]
    for p in sorted(DATA.glob("*/stats.json")):
        s = json.loads(p.read_text())
        mix = (s.get("sensitivities") or {}).get("mix")
        if not mix:
            continue
        v2 = s["sensitivities"].get("v2", {}).get("summary", {})
        m = mix["summary"]
        h = m["headline_race"]
        lc = s.get("lead_contested") or {}
        v1c = (s.get("calibration") or {}).get(h, {}).get("coverage90_1_95") if h else None
        lead = lambda x, n: f"{fmt(x)} ({n})" if x is not None else "–"  # noqa: E731
        rows.append(f"| {p.parent.name} | {h or 'all governor races'} | {fmt(v1c, True)} / {fmt(v2.get('coverage90_headline'), True)} / {fmt(m['coverage90_headline'], True)} | "
                    f"{fmt(v2.get('coverage90_mean_share_races'), True)} / {fmt(m['coverage90_mean_share_races'], True)} | "
                    f"{lead(lc.get('median_lead_min_gate1pct'), lc.get('n', 0))} / {lead(v2.get('median_lead_90_min_gate1pct'), v2.get('n_contested_with_lead', 0))} / "
                    f"{lead(m['median_lead_90_min_gate1pct'], m['n_contested_with_lead'])} |")
    print("\n".join(rows))


if __name__ == "__main__":
    main()
