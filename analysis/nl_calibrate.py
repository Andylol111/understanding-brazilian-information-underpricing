"""Netherlands 2025: settings of the prior-only projection, chosen on EARLIER elections only.

Backtests the projection on the 2017, 2021 and 2023 elections (previous result: 2012, 2017, 2021), never on
2025: for each, the final polling average (each pollster's last poll that ended 1-7 days before the vote, from
the English Wikipedia poll tables, CC BY-SA 4.0) is mixed with the previous election's vote shares, and its
error against the official national result is measured.

  w_prev  weight of the previous result in the central share: the value with the lowest RMS share error
          over the three backtests (grid 0 to 0.5);
  err     sd of a list's share error = err * sqrt(b (1 - b)): the RMS of the standardised errors at that w_prev,
          lists with b >= 0.5% (about one seat);
  err_prev  the same for a projection from the previous result alone (the 'without polls' line: w_prev = 1);
  other   mean share of valid votes for lists that won no seat (2017, 2021, 2023).

    BRUNDER_DATA=~/brunder-data-local uv run python analysis/nl_calibrate.py

Prints the tables used in docs/netherlands.md and writes $BRUNDER_DATA/processed/netherlands_calibration.json.
The frozen values are copied by hand into configs/nl2025_v1.toml.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from brunder import netherlands as nl
from brunder.paths import PROCESSED, RAW

NLRAW = RAW / "netherlands"
BACKTESTS = {2017: 2012, 2021: 2017, 2023: 2021}      # election -> previous election
GRID = np.round(np.arange(0.0, 0.5001, 0.05), 2)


def poll_grid(year: int) -> list[list[str]]:
    """The poll table with the most rows on the year's Wikipedia page that has a 'Polling firm' column."""
    html = json.loads((NLRAW / "wiki" / f"polls_html_{year}.json").read_text())["parse"]["text"]
    ts = [t for t in nl.html_tables(html) if any(c.startswith("Polling") for r in t[:3] for c in r)]
    return max(ts, key=len)


def shares_of_lists(votes_by_id: pd.Series, ids: list[str]) -> pd.Series:
    """Vote shares of `ids` among those lists (a share of the votes of lists in the poll table)."""
    v = votes_by_id.reindex(ids).fillna(0.0)
    return v / v.sum()


def backtest(year: int, prev: int):
    polls = nl.parse_polls(poll_grid(year))
    avg, used = nl.final_poll(polls, nl.ELECTION_DATE[year])
    if year == 2023:
        # the joint list ran for the first time; the 2021 result is merged the same way
        pass
    ids = [c for c in avg.index if avg[c] > 0 or c in ("gl", "pvda")]
    ids = [c for c in ids if c not in ("bvnl",)]
    cur = nl.merge_gl_pvda(nl.national_by_id(nl.national_by_name(year, NLRAW))) if year == 2023 else \
        nl.national_by_id(nl.national_by_name(year, NLRAW))
    if year == 2023:
        avg = nl.merge_gl_pvda(avg)                 # 2023 page: joint column only
        ids = [c for c in avg.index if avg[c] > 0]
    pv = nl.national_by_id(nl.national_by_name(prev, NLRAW))
    if year == 2023:
        pv = nl.merge_gl_pvda(pv)
    actual = shares_of_lists(cur, ids)
    prev_share = shares_of_lists(pv, ids)
    poll_share = avg.reindex(ids).fillna(0.0)
    poll_share = poll_share / poll_share.sum()
    other = 1 - cur.reindex(ids).fillna(0).sum() / (cur.sum())    # votes outside the poll table's lists
    return {"year": year, "ids": ids, "actual": actual, "poll": poll_share, "prev": prev_share, "used": used, "other_table": other}


def main() -> None:
    bt = {y: backtest(y, p) for y, p in BACKTESTS.items()}
    # other: share of the valid votes for lists that win no seat
    other = []
    for y in BACKTESTS:
        v = nl.national_by_name(y, NLRAW)
        s = nl.seats_by_name(v)
        other.append(float(v[s == 0].sum() / v.sum()))
    print("pollsters used (last poll ending 1-7 days before the vote):")
    for y, r in bt.items():
        print(y, [(p.pollster, str(p.end)) for p in r["used"].itertuples()])
    print("lists with no seat, share of valid votes:", dict(zip(BACKTESTS, np.round(other, 4))), "mean", round(float(np.mean(other)), 4))

    rows = []
    for w in GRID:
        e2 = []
        for y, r in bt.items():
            b = (1 - w) * r["poll"] + w * r["prev"]
            b = b / b.sum()
            e2 += list(((r["actual"] - b) ** 2).to_numpy())
        rows.append({"w_prev": w, "rmse_pt": 100 * float(np.sqrt(np.mean(e2)))})
    gt = pd.DataFrame(rows)
    print(gt.round(3).to_string(index=False))
    w_star = float(gt.loc[gt["rmse_pt"].idxmin(), "w_prev"])

    def zvals(w: float):
        z = []
        for y, r in bt.items():
            b = (1 - w) * r["poll"] + w * r["prev"]
            b = b / b.sum()
            keep = b >= 0.005
            z += list(((r["actual"] - b)[keep] / np.sqrt(b[keep] * (1 - b[keep]))).to_numpy())
        return np.array(z)

    z = zvals(w_star)
    zp = zvals(1.0)
    err = float(np.sqrt(np.mean(z**2)))
    err_prev = float(np.sqrt(np.mean(zp**2)))
    per_year = {y: float(np.sqrt(np.mean(((r["actual"] - r["poll"]) ** 2).to_numpy()))) * 100 for y, r in bt.items()}
    print(f"w_prev = {w_star}  err = {err:.3f} ({len(z)} standardised errors)   err_prev (previous result alone) = {err_prev:.3f} ({len(zp)})")
    print("RMS share error of the poll average alone, points:", {y: round(v, 2) for y, v in per_year.items()})
    for y, r in bt.items():
        d = pd.DataFrame({"poll": r["poll"], "prev": r["prev"], "actual": r["actual"]}).mul(100).round(1)
        d["poll_err"] = (d["actual"] - d["poll"]).round(1)
        print(y, "(share among the lists of the poll table, %)")
        print(d.sort_values("actual", ascending=False).to_string())
    out = {"w_prev": w_star, "err": err, "err_prev": err_prev, "other": float(np.mean(other)), "other_by_year": dict(zip(map(str, BACKTESTS), other)),
           "n_err": int(len(z)), "n_err_prev": int(len(zp)), "grid": gt.to_dict("records"), "poll_rmse_pt": per_year,
           "pollsters": {str(y): [{"pollster": p.pollster, "end": str(p.end)} for p in r["used"].itertuples()] for y, r in bt.items()}}
    PROCESSED.mkdir(parents=True, exist_ok=True)
    (PROCESSED / "netherlands_calibration.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
