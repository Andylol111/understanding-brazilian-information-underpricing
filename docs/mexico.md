# Mexico presidential election, 2 June 2024 (`mx2024`)

A replay of the 2024 count from INE's acta-level PREP database, scored with the edge-audit method
(`docs/edge_audit.md`). The two questions are:

1. Does the count model's projection hold?
2. Was there executable profit after costs?

**Evidence label: not-preregistered.** All settings were chosen on the 2018 count only and written into
`configs/mx2024_v1.toml`. But the config was first committed after scoring, and one exploratory run on the 2024
count (τ = 0.015, 30-minute ticks) was looked at before it was frozen. The outcome had been public for over two
years. Details are in the disclosure under [Model](#model-settings-chosen-on-2018-only).

## Results

**Market.** There was no trade to make. Polymarket's "Will Claudia Sheinbaum win?" stayed at or above 0.90 from
**2024-06-01 00:05 UTC** onwards. That is 31 May in Mexico, two days before the vote and about 50 hours before the
PREP published its first cut. During the count the market sat at 0.955–0.9995. Gálvez-YES taker trades were at 0.1–2.6¢.
Both markets closed at 12:46–12:56 UTC on 3 June, with the PREP at about 75% counted. The model's P(Sheinbaum)
was 1.00 from the first cut.

**Profit (frozen Brazil rule).** The rule is: favoured side only, edge ≥ 5¢ after costs (price + 1¢ half-spread,
no fee), at least 1% counted, first signal per market, held to resolution. It produced **0 signals in both
markets**.

| market | best favoured-side edge after costs, ≥ 1% counted |
|---|---|
| Sheinbaum YES | 0.5¢ (02:50 UTC, at 0.9845) |
| Gálvez NO | 4.6¢ (04:29 UTC) |

The Gálvez figure comes from a spike in the minute price history (0.056). Real taker trades in that window were
Gálvez-YES buys at 1.5–2.6¢, so the true NO edge was about 1¢.

Fillable dollars and P&L are $0 in every execution variant (latency 0/60 s, horizon 5/30 min, slippage 0/2/5¢).
There is no P(win) comparison either: model and market had both settled before 1% was counted, so the audit's
window is empty.

**Accuracy.** Two-way share Sheinbaum / (Sheinbaum + Gálvez) against the official cómputo, **68.52%**:

| counted (UTC) | 1% (02:50) | 5% (03:55) | 10% (04:25) | 25% (05:35) | 50% (07:25) |
|---|---|---|---|---|---|
| model − final (pt) | +1.86 | +0.43 | −0.59 | −1.51 | −1.55 |
| raw count − final (pt) | −0.29 | −1.56 | −2.53 | −2.90 | −2.22 |

- **Projection.** From 5% counted onwards the projection's error was about half the raw count's. At 1% it was
  worse, because the first cuts came from a few sections and the swing estimate had not settled.
- **Uncertainty.** This **failed**. The 90% interval contained the final share in only **10%** of minutes
  between 1% and 95% counted (mean z +3.3):
  - 1–90% counted: 14%
  - 90–95% counted: 0%
  - 1–50% counted: 53%

  The model was consistently 1.5 points short of Sheinbaum's final share in mid-count, with an sd of about
  0.5 point. Diagnostic only, not a setting: a τ of about 0.03 would have been needed for 90% coverage, against
  the 0.01 chosen on 2018.
- **Part of the gap is the gap between PREP and the cómputo.**
  - In the 61,806 sections whose actas the PREP counted completely, the official cómputo gave Sheinbaum
    67.87% two-way against the PREP's 67.65% (+0.22 pt).
  - The 2,302 sections the PREP never counted voted 74.0% for her.
  - The PREP stopped at 93.2% of actas, with a raw share of 68.02%. The model's last projection was 68.19%.

  2018 showed the same sign: the end-of-PREP projection was −0.34 pt against the cómputo.

For 2024, the dashboard (`site/data/mx2024/`) shows:
- the count minute by minute;
- the market until it closed;
- a 300-district map with 44 frames, built from the section projections.

## Data and licences

Everything published is INE data or Polymarket public market data. No Kalshi data is used.

| data | source | size on disk |
|---|---|---|
| PREP 2024 database (one download, 47 MB zip; presidential CSV kept) | https://prep2024.ine.mx/publicacion/nacional/assets/20240603_2005_PREP.zip, the URL the base-datos page's `configuracion.json` names (`urlBaseDatos`) | 52 MB |
| PREP 2018 database, used for development | https://prep2018.ine.mx/20180702_2100_PREP.zip | 48 MB |
| Cómputos 2018 by casilla: prior for 2024, final result for 2018 | https://computos2018.ine.mx/20180708_2130_CW.zip | 31 MB |
| SICEE section results 2012 (prior for 2018) and 2024 (final result for 2024) | https://sicee.ine.mx/ACTASSICEEN2024/download/FEDERALES_{2012,2024}/PRESIDENCIA_{2012,2024}.zip | 14 MB |
| Federal-district boundaries (2023 distritación) | https://prep2024.ine.mx/publicacion/nacional/assets/GeoJson/distritos/distritos.geojson | 3 MB (cached) |
| Archived PREP cut files, used only for the publication check | Wayback Machine, 7 files | < 0.1 MB |
| Polymarket event `mexican-presidential-election-who-will-win`: four markets, minute prices and all fills | Gamma / CLOB / data-api | 6 MB |

Raw data totals about 150 MB under `BRUNDER_DATA=~/brunder-data-local/raw/` (`mx2012c`, `mx2018`, `mx2018c`, `mx2024`,
`mx2024c`, and `polymarket/mexican-presidential-election-who-will-win`). None of it is committed.

The commit includes:
- `site/data/mx2024/` (≈ 370 kB);
- `site/data/geo/mx_districts.topo.json` (0.16 MB);
- a 20 kB test fixture of INE rows (`tests/fixtures/mexico/`).

- **Licence.** INE's "Términos de libre uso" are shown on the SICEE results system (sicee.ine.mx, the same text
  as siceef.ine.mx/terminos.html), and are based on Art. 6 of the Constitution and the Ley General de
  Transparencia.
  - They permit anyone to copy, distribute, publish, adapt, extract from and build derived datasets on these
    data, including commercially.
  - Conditions: cite the source (dataset, INE, URL, access date); do not distort the information; do not imply
    INE endorsement.
  - The PREP site shows no terms of its own. Its data are INE's preliminary results published under the same
    principle of *máxima publicidad*, so the same terms are applied and cited (accessed 2026-10-28).
  - Nothing in them forbids automated download or research use.
- **robots.txt** (prep2024, prep2018, sicee): `User-agent: * Allow: /` and `Content-Signal: search=yes,
  ai-train=no, use=reference`. They disallow only named AI crawlers.
  - Every request used an honest, non-AI research User-Agent: `brunder-research/0.1 (academic election-count
    research; non-AI)`. The map download used `geo_build.py`'s UA.
  - There was one download per file, sequential, with pauses for the Wayback copies.
  - Nothing was evaded.
  - The data are not used to train any AI model.

## The count path: when an acta became public

Each PREP row is one acta (a polling-station tally sheet). It carries FECHA_HORA_ACOPIO (received at the collection
centre), FECHA_HORA_CAPTURA (keyed) and FECHA_HORA_VERIFICACION (verified), all in central time. The header says
`(UTC-6)` for 2024 and `(UTC-5)` for 2018, which was summer time. The PREP published a cut every 5 minutes from
20:00 central time: 290 cuts to 20:05 on 3 June.

The archived national progress file and state files from 2024 (Wayback; `analysis/mx_publication_check.py`) show
three things:
- a cut labelled *c* held the actas **verified** up to about *c* + 1 to *c* + 4 minutes;
- it was online within about 5–6 minutes of its label;
- state 19 at the 20:45 cut matches the cumulative verified two-way votes exactly.

The rule is therefore:

    visible = max(ceil_5min(t_verification − 3.5 min), 20:00 opening) + 5 min

| archived copy (capture, UTC) | copy | rule at capture time | cut reached (verification) |
|---|---|---|---|
| national, cut 20:30 (02:35:48) | 1,350 actas | 1,432 (+6.1%) | label + 1.0 min |
| national, cut 23:35 (05:41:15) | 46,438 | 46,338 (−0.2%) | label + 3.8 min |
| national, cut 11:25 (17:30:50) | 146,024 | 145,456 (−0.4%) | label + 13.6 min¹ |
| national, cut 12:30 (18:35:33) | 149,569 | 149,122 (−0.3%) | label + 14.5 min¹ |
| state 15, votes (02:35:48) | 38,032 | 41,990 (+10.4%) | ≈ 02:31:34 |
| state 19, votes (02:48:47) | 20,253 | 19,608 (−3.2%) | 02:44:35 (exact) |
| state 10, votes (03:49:29) | 92,727 | 86,004 (−7.3%) | ≈ 03:48:34 |

¹ About 0.5% of actas were corrected later ("cotejo"), and the final file stores the later time. These actas are
shown later than they really appeared. Their corrected votes are used.

Limitation: in the first cut the rule shows some actas up to about 2.5 minutes early. Elsewhere it shows them a few
minutes late (state 10: 7% fewer votes than the copy at its capture time).

The catalogue of expected actas and each casilla's lista nominal come from the same file. They are pre-election
information: the casilla catalogue is published before election day. "counted" on the dashboard is the model's
share of expected two-way votes counted. The map's f is each district's share of expected actas.

## Model (settings chosen on 2018 only)

**Units, prior and race.**
- **Units.** 70,537 electoral sections in 2024, with state as the region. Votes outside sections go to unpooled
  units in region `zz`: abroad, one per state; early voting and pre-trial detention, one national unit each.
- **Race.** Sheinbaum (MORENA, PT, PVEM and their combinations) against Gálvez (PAN, PRI, PRD and their
  combinations). The share is two-way, and a win is a share above ½. Máynez, of MC, was a distant third and could
  not win.
- **Prior.** The previous presidential cómputo by section, with the left candidate against the PAN- and PRI-led
  candidates combined:
  - 2018 for 2024: López Obrador vs Anaya + Meade.
  - 2012 for 2018: López Obrador vs Peña Nieto + Vázquez Mota.
  - s0 is the section's previous two-way share.
  - V0 is today's lista nominal times the section's previous two-way votes per registered voter.
  - Sections created by re-sectioning (4,040 in 2024; 1,952 in 2018) take their federal district's values.
- **Engine.** `brunder.nowcast` (the municipal model used for Brazil) with every parameter at its default except
  τ.

**Development set: the 2018 count** (`configs/mx2024_dev_2018.toml`, `analysis/mx_calibrate.py`; in-sample for 2018).
The selection criterion is the Brazil tool's (`docs/edge_tool.md`, development item 3): the smallest τ whose 90%
intervals cover the final share in ≥ 90% of minutes between 1% and 95% counted. The 2018 PREP stopped at 80% of
actas.

| τ | 0 | 0.0025 | 0.005 | 0.0075 | **0.01** | 0.015 | 0.02 | 0.03 |
|---|---|---|---|---|---|---|---|---|
| coverage, 1–80% counted | 0.62 | 0.67 | 0.71 | 0.79 | **0.94** | 0.98 | 1.00 | 1.00 |
| of which 1–50% | 0.80 | 0.81 | 0.82 | 0.84 | 0.86 | 0.95 | 1.00 | 1.00 |

2018 errors, model vs raw, in points:
- 1%: −1.49 vs −5.98
- 5%: −3.19 vs −4.64
- 10%: −2.23 vs −2.85
- 25%: −0.36 vs +0.04
- 50%: −0.05 vs +0.69

The trading rule is the registered Brazil rule, unchanged. The fee is 0 because Polymarket charged no taker fee
on these markets in 2024.

**Disclosure, and why the label is not-preregistered.** While testing the code, and before
`configs/mx2024_v1.toml` was written and the 2018 calibration had run, one exploratory run of the same model with
τ = 0.015 was looked at on the 2024 count (30-minute ticks). The config was committed only after the 2024 replay
had been scored, so nothing outside this account documents the freeze.
- Nothing in the config was set from that run.
- The 2018 criterion then picked τ = 0.01. That is a *narrower* interval than the one explored, so the choice did
  not favour the 2024 result.
- The publication rule was fitted on the 2024 archived cut files. These describe when data appeared, not the
  outcome. This is the same kind of step as Brazil's receipt-lag calibration.

## Reproduce

    export BRUNDER_DATA=~/brunder-data-local
    uv run python analysis/mx_calibrate.py configs/mx2024_dev_2018.toml   # 2018 development (~1.5 min)
    uv run python analysis/mx_replay.py configs/mx2024_v1.toml            # 2024 replay (~1.5 min)
    uv run python analysis/mx_score.py configs/mx2024_v1.toml             # accuracy + profit (edge audit)
    uv run python analysis/mx_publication_check.py configs/mx2024_v1.toml # rule vs archived PREP cuts
    uv run python analysis/mx_geo.py                                      # site/data/geo/mx_districts.topo.json
    uv run python analysis/mx_export.py configs/mx2024_v1.toml            # site/data/mx2024/ + validation

Inputs come from the URLs above. The Polymarket event comes from
`brunder.polymarket.download_event("mexican-presidential-election-who-will-win")`.

Outputs go to `results/mexico/` (not published) and `site/data/mx2024/`:
- `meta.json`, `timeline.json`, `units.json`, `stats.json`, with `accuracy` and `profit` in the edge-audit schema;
- `entry.json`, the landing item for the integrator to merge into `elections.json`.

Code: `src/brunder/mexico.py`, with tests in `tests/test_mexico.py` on real INE rows.
