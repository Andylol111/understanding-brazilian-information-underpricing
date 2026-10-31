"""France, presidential elections: parsers, prior, count-night projection, race probabilities.

Data (Ministère de l'Intérieur; final results by polling station from data.gouv.fr, Licence Ouverte; the election-night
national page `FE.html` ("France entière") is an official page of the ministry's results site, read from Internet
Archive captures, each content stamped with the time of its first capture):

* `read_final`  final result by polling station -> by commune (2012 long format; 2017/2022 wide format),
* `parse_fe`    the national page: candidate votes, registered voters counted, "résultats incomplets calculés sur la
                base de N% des inscrits" (2017 wording: "des inscrits reçus"),
* `Prior`, `project`  size-order projection of the final shares from the counted totals (below).

The model. The count history available for these nights is national only: at each captured state the page gives the
votes of every candidate and the registered voters (inscrits) of the polling stations already counted. Which communes
are in is not published, so the model *assumes* communes are counted from the smallest to the largest (small rural
communes report first; the five largest cities and the votes of French citizens abroad last). Every departure from
that order is the reporting-order error `tau` of `brunder.nowcast_order`, learned from earlier nights, never from the
night being replayed.

1. Prior: each commune's share of every category (the candidates followed, plus "other") at the previous election of the
   same family (2012 round 1 for 2017 round 1, 2017 round 1 for 2022 round 1, round 1 for round 2), and its exprimés
   per registered voter. The registered voters of the night being replayed are the only number taken from it (they
   are the electoral roll, known before the vote); they set the size order and the weights.
2. Counted set at a state: the smallest communes whose registered voters add up to the counted registered voters.
   Turnout ratio `lam` = counted exprimés per registered voter over the prior's for that set.
3. Ratio swing per category: `r_c` = counted share over the prior's share of the counted set, shrunk in log space by
   `w = E / (E + kappa)`; the uncounted communes' prior shares are multiplied by `r_c` and renormalised.
4. Final share = (counted votes + projected uncounted votes) / total. Its standard error at `tau = 0` is the
   commune-level noise (`sigma_u`) of the uncounted communes plus the swing estimate's own error; the reporting-order
   error adds `(1 - counted) * tau` in quadrature (share units). `tau` is one number or an equal-weight mixture of the
   `tau` that earlier nights needed.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from brunder import nowcast_order as no
from brunder.paths import RAW

RAW_FR = RAW / "france"
OPEN = RAW_FR / "open"
WAYBACK = RAW_FR / "wayback"
UNPOOLED_DEPTS = ("ZZ",)           # French citizens abroad: counted last, whatever their size
Z90 = no.Z90


def slug(name: str) -> str:
    """Candidate key: accents stripped, upper case, words joined by '_' ("Marine LE PEN" -> "LE_PEN")."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z]+", "_", s).strip("_")


def surname(display: str) -> str:
    """The trailing upper-case words of a page name: "Mme Marine LE PEN" -> "LE_PEN"."""
    words = display.replace("\xa0", " ").split()
    out = []
    for w in reversed(words):
        letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", w)
        if letters and letters == letters.upper():
            out.append(w)
        else:
            break
    return slug(" ".join(reversed(out)))


# ------------------------------------------------------------------------------------------------ final results
def _fr_int(s: str) -> int:
    return int(re.sub(r"\D", "", s) or 0)


def read_final(path: Path, round_: int = 1) -> pd.DataFrame:
    """Final result by commune: dept, commune (code), name, inscrits, votants, exprimes and one column per candidate
    (slug of the surname). Reads the ministry's polling-station files: 2012 `PR12_Bvot_T1T2.txt` (long: one row per
    station and candidate, both rounds), 2017 and 2022 (wide: 21 station columns then 7 per candidate)."""
    with open(path, encoding="latin-1", newline="") as fh:
        head = fh.readline().strip().split(";")
        fh.seek(0)
        rd = csv.reader(fh, delimiter=";")
        rows: list[dict] = []
        if head[0].startswith("Code du d"):                       # 2017 / 2022
            next(rd)
            for r in rd:
                if len(r) < 28:
                    continue
                d = {"dept": r[0], "commune": r[4], "name": r[5], "bureau": r[6], "inscrits": _fr_int(r[7]),
                     "votants": _fr_int(r[10]), "exprimes": _fr_int(r[18])}
                for i in range(21, len(r) - 6, 7):
                    d[slug(r[i + 2])] = _fr_int(r[i + 4])
                rows.append(d)
        else:                                                     # 2012 long format
            cur, d = None, None
            for r in rd:
                if len(r) < 15 or int(r[0]) != round_:
                    continue
                k = (r[1], r[2], r[6])
                if k != cur:
                    if d:
                        rows.append(d)
                    cur = k
                    d = {"dept": r[1], "commune": r[2], "name": r[3], "bureau": r[6],
                         "inscrits": _fr_int(r[7]), "votants": _fr_int(r[8]), "exprimes": _fr_int(r[9])}
                d[slug(r[11])] = _fr_int(r[14])
            if d:
                rows.append(d)
    df = pd.DataFrame(rows)
    cand = [c for c in df.columns if c not in ("dept", "commune", "name", "bureau", "inscrits", "votants", "exprimes")]
    df[cand] = df[cand].fillna(0).astype(int)
    df["dept"] = df["dept"].astype(str).str.upper().str.zfill(2)
    df["commune"] = df["commune"].astype(str).str.zfill(3)
    df["key"] = df["dept"] + df["commune"]
    g = df.groupby("key", sort=False)
    out = g[["inscrits", "votants", "exprimes", *cand]].sum()
    out.insert(0, "dept", g["dept"].first())
    out.insert(1, "name", g["name"].first())
    return out


# ------------------------------------------------------------------------------------------------ national page
class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell).replace("\xa0", " ")).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        self.text.append(data)
        if self._cell is not None:
            self._cell.append(data)


_PCT = re.compile(r"base de ([\d.,]+) ?% des (?:[ée]lecteurs )?inscrits", re.I)


def parse_fe(raw: bytes) -> dict | None:
    """The national "France entière" page of the ministry's results site -> {"round", "pct_inscrits" (the page's own
    rounded 'basé sur N%'), "inscrits", "votants", "blancs", "nuls", "exprimes", "votes": {slug: voix}}, or None when
    the page holds no result table yet. Only the first tables (the round shown) are read; the 2017 round-2 page also
    carries a "Rappel des résultats au 1er tour" that is ignored."""
    html = raw.decode("latin-1")
    p = _Tables()
    p.feed(html)
    text = re.sub(r"\s+", " ", "".join(p.text))
    rnd = 2 if re.search(r"(?:au|du) 2 ?(?:e|d)", text, re.I) or "second tour" in text.lower() else 1
    heads = [i for i, t in enumerate(p.tables) if t and t[0] and t[0][0] == "Liste des candidats"]
    if not heads:
        return None
    i = heads[0]
    cands = {surname(r[0]): _fr_int(r[1]) for r in p.tables[i] if len(r) >= 4 and re.match(r"^\d[\d ]*$", r[1] or "")}
    if not cands or i + 1 >= len(p.tables):
        return None
    ment = {slug(r[0]): _fr_int(r[1]) for r in p.tables[i + 1] if len(r) >= 2 and re.match(r"^\d[\d ]*$", r[1] or "")}
    m = _PCT.search(text)
    need = ("INSCRITS", "VOTANTS", "BLANCS", "NULS", "EXPRIMES")
    if not all(k in ment for k in need):
        return None
    return {"round": rnd, "pct_inscrits": float(m.group(1).replace(",", ".")) if m else None,
            "inscrits": ment["INSCRITS"], "votants": ment["VOTANTS"], "blancs": ment["BLANCS"],
            "nuls": ment["NULS"], "exprimes": ment["EXPRIMES"], "votes": cands}


def read_night(night: str, root: Path = WAYBACK) -> pd.DataFrame:
    """Every archived national-page state of a night: one row per distinct content, t = its first capture time (UTC)
    (the page gives no time of its own; the capture time is the first moment it is known to have been visible, so the
    count may have been public earlier). Columns t, round, pct_inscrits, inscrits, votants, exprimes, then one per
    candidate. Rows whose counted registered voters do not increase are dropped (a re-captured older page)."""
    rows = []
    for f in sorted((root / night).glob("FE_*.html")):
        st = parse_fe(f.read_bytes())
        if st is None:
            continue
        t = pd.Timestamp(f.stem.split("_")[1], tz="UTC")
        rows.append({"t": t, "round": st["round"], "pct_inscrits": st["pct_inscrits"], "inscrits": st["inscrits"],
                     "votants": st["votants"], "exprimes": st["exprimes"], **st["votes"]})
    df = pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
    cand = [c for c in df.columns if c not in ("t", "round", "pct_inscrits", "inscrits", "votants", "exprimes")]
    df[cand] = df[cand].fillna(0).astype(int)
    keep = df["inscrits"].cummax().diff().fillna(1) > 0
    return df[keep & (df["inscrits"] >= df["inscrits"].cummax())].reset_index(drop=True)


# ------------------------------------------------------------------------------------------------ prior
@dataclass
class Prior:
    """Units (communes) with their size, prior exprimés per registered voter and prior shares of the categories."""
    cats: list[str]                      # tracked categories; the last one is "OTHER" (everything else)
    V: np.ndarray                        # registered voters of the night's election, in counting order (ascending)
    x0: np.ndarray                       # prior exprimés per registered voter
    s0: np.ndarray                       # prior share of each category (rows sum to 1), shape (n, len(cats))
    keys: np.ndarray = field(default_factory=lambda: np.array([]))

    def __post_init__(self):
        self.cumV = np.cumsum(self.V)
        self.vx = self.V * self.x0
        self.cum_vx = np.cumsum(self.vx)
        self.cum_vxs = np.cumsum(self.vx[:, None] * self.s0, axis=0)


def shares_of(df: pd.DataFrame, cats: list[str]) -> np.ndarray:
    """Share of exprimés of each tracked category, then OTHER = the rest, per row of a commune table."""
    e = df["exprimes"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        s = np.column_stack([df[c].to_numpy(float) if c in df else np.zeros(len(df)) for c in cats[:-1]]) / e[:, None]
    other = 1.0 - s.sum(axis=1)
    return np.column_stack([s, other])


def make_prior(target: pd.DataFrame, previous: pd.DataFrame, cats: list[str],
               new_candidate: dict[str, list[str]] | None = None, two_way: bool = False) -> Prior:
    """Prior of the night's election `target` (only its `inscrits` are used, as the roll) from `previous` (the earlier
    election's commune table). A target commune without a previous counterpart (merged or created since) takes its
    département's average. `new_candidate`: category -> previous-election candidates standing in for a category that
    did not run before (2017 Macron <- 2012 Hollande + Bayrou). `two_way` (round 2): previous shares are renormalised
    over the two categories tracked."""
    prev = previous.copy()
    for c, srcs in (new_candidate or {}).items():
        prev[c] = sum(prev[s] for s in srcs)
    s_prev = shares_of(prev, cats)
    if two_way:
        with np.errstate(invalid="ignore", divide="ignore"):
            s_prev = s_prev[:, :-1] / s_prev[:, :-1].sum(axis=1, keepdims=True)
    x_prev = (prev["exprimes"] / prev["inscrits"].replace(0, np.nan)).to_numpy()
    sp = pd.DataFrame(s_prev, index=prev.index)
    sp["x"] = x_prev
    sp["dept"] = prev["dept"]
    sp["w"] = prev["inscrits"]
    t = target[target["inscrits"] > 0]
    sp = sp.reindex(t.index)
    sp["dept"] = sp["dept"].fillna(t["dept"])
    ncat = s_prev.shape[1]
    cols = list(range(ncat)) + ["x"]
    dept_mean = (prev.assign(**{str(i): s_prev[:, i] for i in range(ncat)}, x=x_prev)
                 .groupby("dept").apply(lambda g: pd.Series({**{i: np.average(g[str(i)].fillna(0), weights=g["inscrits"] + 1) for i in range(ncat)},
                                                          "x": np.nanmean(g["x"])}), include_groups=False))
    nat = {i: float(np.nansum(s_prev[:, i] * prev["inscrits"]) / prev["inscrits"].sum()) for i in range(ncat)}
    nat["x"] = float(np.nansum(x_prev * prev["inscrits"]) / prev["inscrits"].sum())
    for c in cols:
        fill = sp["dept"].map(dept_mean[c]) if c in dept_mean else pd.Series(np.nan, index=sp.index)
        sp[c] = sp[c].fillna(fill).fillna(nat[c])
    s0 = sp[list(range(ncat))].to_numpy(float)
    s0 = np.clip(s0, 1e-6, None)
    s0 /= s0.sum(axis=1, keepdims=True)
    order = np.lexsort((t["inscrits"].to_numpy(), t["dept"].isin(UNPOOLED_DEPTS).to_numpy()))
    idx = t.index.to_numpy()[order]
    pr = Prior(cats=cats if not two_way else cats[:-1], V=t["inscrits"].to_numpy(float)[order],
               x0=np.clip(sp["x"].to_numpy(float), 0.2, 1.0)[order], s0=s0[order], keys=idx)
    return pr


# ------------------------------------------------------------------------------------------------ projection
@dataclass(frozen=True)
class Params:
    kappa: float = 1.0e6          # pseudo exprimés shrinking the log swing to 0
    sigma_u: float = 0.04         # commune-level sd of a category's share around prior x swing
    tau: tuple[float, ...] = (0.0,)   # reporting-order error, share units: one value, or an equal-weight mixture


def counted_set(pr: Prior, inscrits_counted: float) -> tuple[int, float]:
    """(index of the first unit not entirely counted, counted fraction of it) when the smallest units are in."""
    n = int(np.searchsorted(pr.cumV, inscrits_counted, side="right"))
    n = min(n, len(pr.V))
    frac = 0.0 if n >= len(pr.V) else (inscrits_counted - (pr.cumV[n - 1] if n else 0.0)) / pr.V[n]
    return n, float(min(max(frac, 0.0), 1.0))


def project(pr: Prior, inscrits: float, exprimes: float, votes: np.ndarray, p: Params) -> dict:
    """Projection at one state. `votes`: counted votes of the tracked categories (len(cats) - 1); OTHER = exprimes - sum.
    Returns final share per category, sd at tau = 0, counted fraction of exprimés and diagnostics."""
    k = pr.s0.shape[1]
    a = np.append(votes.astype(float), exprimes - votes.sum())
    total_V = pr.V.sum()
    inscrits = float(min(inscrits, total_V))
    n, frac = counted_set(pr, inscrits)
    vx_counted = (pr.cum_vx[n - 1] if n else 0.0) + frac * (pr.vx[n] if n < len(pr.V) else 0.0)
    P = (pr.cum_vxs[n - 1] if n else np.zeros(k)) + frac * (pr.vx[n] * pr.s0[n] if n < len(pr.V) else 0.0)
    if inscrits >= total_V - 1:
        return {"share": a / a.sum(), "sd0": np.zeros(k), "counted": 1.0, "lam": 1.0, "n_units": n, "r": np.ones(k)}
    lam = (exprimes / inscrits) / (vx_counted / inscrits) if vx_counted > 0 else 1.0
    S, S0 = a / exprimes, P / vx_counted
    w = exprimes / (exprimes + p.kappa)
    r = np.exp(w * np.log(np.clip(S, 1e-9, None) / np.clip(S0, 1e-9, None)))
    rest_vx = np.concatenate([[(1 - frac) * pr.vx[n]] if n < len(pr.V) else [], pr.vx[n + 1:]])
    rest_s0 = np.vstack([pr.s0[n:n + 1], pr.s0[n + 1:]]) if n < len(pr.V) else pr.s0[:0]
    sh = rest_s0 * r
    sh /= sh.sum(axis=1, keepdims=True)
    R = lam * (rest_vx[:, None] * sh).sum(axis=0)
    final = a + R
    share = final / final.sum()
    counted = float(a.sum() / final.sum())
    wts = rest_vx / rest_vx.sum()
    n_units = max(n + 1, 1)
    sd0 = (1 - counted) * p.sigma_u * np.sqrt((wts**2).sum() + 1.0 / n_units) * np.sqrt(share * (1 - share) / 0.25)
    return {"share": share, "sd0": sd0, "counted": counted, "lam": float(lam), "n_units": n, "r": r}


def sd_at(share_sd0: np.ndarray, counted: float, tau: float) -> np.ndarray:
    return np.sqrt(share_sd0**2 + ((1 - counted) * tau) ** 2)


# ------------------------------------------------------------------------------------------------ probabilities
def draw_shares(share: np.ndarray, sd: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """Joint draws of the category shares: independent normals with sd `sd`, conditioned to sum to 0 (the shares of a
    complete count add up to 1)."""
    z = rng.standard_normal((n, len(share))) * sd
    var = sd**2
    z -= (z.sum(axis=1, keepdims=True)) * (var / var.sum())
    return np.clip(share + z, 0.0, 1.0)


def mixture_draws(share: np.ndarray, sd0: np.ndarray, counted: float, taus: tuple[float, ...], n: int,
                  rng: np.random.Generator) -> np.ndarray:
    """Draws pooled over an equal-weight mixture of taus (n draws per tau)."""
    return np.vstack([draw_shares(share, sd_at(sd0, counted, t), n, rng) for t in taus])


def pwin_two(share_a: float, sd_a: float) -> float:
    """P(A's share of a two-candidate vote exceeds 1/2)."""
    return float(norm.cdf((share_a - 0.5) / sd_a)) if sd_a > 0 else float(share_a > 0.5)


# ------------------------------------------------------------------------------------------------ nights
R1 = ["MACRON", "LE_PEN", "MELENCHON", "OTHER"]
R2 = ["MACRON", "LE_PEN", "OTHER"]
# night -> final-result file, previous election file (+ round) the prior is built from, categories, round-2 flag
NIGHTS = {
    "pr17r1": {"final": ("pr2017", "t1_PR17_BVot_T1_FE.txt", 1), "prior": ("pr2012", "PR12_Bvot_T1T2.txt", 1), "cats": R1,
               "new": {"MACRON": ["HOLLANDE", "BAYROU"]}, "two_way": False, "date": "2017-04-23", "poll_close_utc": "2017-04-23T18:00:00Z"},
    "pr17r2": {"final": ("pr2017", "t2_PR17_BVot_T2_FE.txt", 2), "prior": ("pr2017", "t1_PR17_BVot_T1_FE.txt", 1), "cats": R2,
               "new": {}, "two_way": True, "date": "2017-05-07", "poll_close_utc": "2017-05-07T18:00:00Z"},
    "pr22r1": {"final": ("pr2022", "t1_resultats-par-niveau-burvot-t1-france-entiere.txt", 1),
               "prior": ("pr2017", "t1_PR17_BVot_T1_FE.txt", 1), "cats": R1, "new": {}, "two_way": False,
               "date": "2022-04-10", "poll_close_utc": "2022-04-10T18:00:00Z"},
    "pr22r2": {"final": ("pr2022", "t2_resultats-par-niveau-burvot-t2-france-entiere.txt", 2),
               "prior": ("pr2022", "t1_resultats-par-niveau-burvot-t1-france-entiere.txt", 1), "cats": R2, "new": {},
               "two_way": True, "date": "2022-04-24", "poll_close_utc": "2022-04-24T18:00:00Z"},
}
ORDER = ["pr17r1", "pr17r2", "pr22r1", "pr22r2"]       # chronological: settings for a night come from the ones before it
_cache: dict = {}


def final_table(night: str) -> pd.DataFrame:
    sub, name, rnd = NIGHTS[night]["final"]
    k = ("final", sub, name)
    if k not in _cache:
        _cache[k] = read_final(OPEN / sub / name, rnd)
    return _cache[k]


def prior_table(night: str) -> pd.DataFrame:
    sub, name, rnd = NIGHTS[night]["prior"]
    k = ("final", sub, name)
    if k not in _cache:
        _cache[k] = read_final(OPEN / sub / name, rnd)
    return _cache[k]


def night_prior(night: str) -> Prior:
    n = NIGHTS[night]
    return make_prior(final_table(night), prior_table(night), n["cats"], n["new"], n["two_way"])


def final_shares(night: str) -> np.ndarray:
    """Official final share of exprimés for the prior's categories (OTHER last in round 1, none in round 2)."""
    pr = night_prior(night)
    f = final_table(night)
    cats = NIGHTS[night]["cats"]
    return shares_of(f.sum(numeric_only=True).to_frame().T, cats)[0][: len(pr.cats)]


def night_frames(night: str, p: Params, pr: Prior | None = None) -> pd.DataFrame:
    """Projection at every archived state of a night, with the official final alongside: columns t, counted_inscrits,
    counted (exprimés fraction of the projected total), then share_<c>, sd0_<c>, err_<c> (final - projected) per category."""
    pr = pr or night_prior(night)
    states = read_night(night)
    fin = final_shares(night)
    rows = []
    for st in states.itertuples():
        votes = np.array([getattr(st, c) for c in pr.cats[:-1]], float)
        o = project(pr, st.inscrits, st.exprimes, votes, p)
        d = {"t": st.t, "inscrits": st.inscrits, "counted_inscrits": st.inscrits / pr.V.sum(), "counted": o["counted"],
             "lam": o["lam"]}
        for j, c in enumerate(pr.cats):
            d[f"share_{c}"], d[f"sd0_{c}"], d[f"err_{c}"] = o["share"][j], o["sd0"][j], fin[j] - o["share"][j]
        rows.append(d)
    return pd.DataFrame(rows)


def target_cats(night: str) -> list[str]:
    """Categories whose share error enters a night's tau: the tracked candidates in round 1, Macron in round 2."""
    return NIGHTS[night]["cats"][:-1] if not NIGHTS[night]["two_way"] else ["MACRON"]


def night_arrays(frames: pd.DataFrame, cats: list[str], lo: float = 0.01, hi: float = 0.95) -> dict:
    """Frames with lo < counted <= hi stacked over the categories, as `nowcast_order.needed_tau` wants them
    (total votes set to 1, so margin_sd0 = 2 sd0 and two_r = 2 (1 - counted) give the share sd directly)."""
    f = frames[(frames["counted"] > lo) & (frames["counted"] <= hi)]
    err = np.concatenate([f[f"err_{c}"].to_numpy() for c in cats])
    sd0 = np.concatenate([f[f"sd0_{c}"].to_numpy() for c in cats])
    cnt = np.concatenate([f["counted"].to_numpy() for _ in cats])
    return {"err": err, "margin_sd0": 2 * sd0, "two_r": 2 * (1 - cnt), "total": np.ones(len(err)), "counted": cnt}


def needed_tau(frames: pd.DataFrame, cats: list[str], grid=no.TAU_GRID, level: float = 0.90) -> float:
    a = night_arrays(frames, cats)
    if not len(a["err"]):
        return float("nan")
    return no.needed_tau(a["err"], a["margin_sd0"], a["two_r"], a["total"], grid, level)


def unit_noise(night: str) -> float:
    """Commune-level sd of a category's share around (prior share x the night's own national ratio): vote-weighted RMS of
    the residual over the tracked categories. Measured on a *finished* night (its final result), so only nights before the
    one being replayed may feed `Params.sigma_u`."""
    pr = night_prior(night)
    f = final_table(night).loc[pr.keys]
    cats = NIGHTS[night]["cats"]
    s = shares_of(f, cats)[:, : len(pr.cats)]
    w = f["exprimes"].to_numpy(float)
    s = np.where(w[:, None] > 0, s, 0.0)
    nat_f = (s * w[:, None]).sum(axis=0) / w.sum()
    wp = pr.vx
    nat_p = (pr.s0 * wp[:, None]).sum(axis=0) / wp.sum()
    pred = pr.s0 * (nat_f / nat_p)
    pred /= pred.sum(axis=1, keepdims=True)
    res = s - pred
    keep = w > 0
    return float(np.sqrt(((res[keep][:, : len(pr.cats) - (0 if NIGHTS[night]["two_way"] else 1)] ** 2) * w[keep, None]).sum()
                         / (w[keep].sum() * (len(pr.cats) - (0 if NIGHTS[night]["two_way"] else 1)))))
