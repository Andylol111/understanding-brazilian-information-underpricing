# brunder — Brazilian election information underpricing on Polymarket

Does Polymarket underweight public, cheaply computable election projections? Case: Brazil 2026
(1st round 2026-10-04, runoff 2026-10-25, Lula d. Flávio Bolsonaro 51.79–48.21). Research design,
hypotheses and model math: [docs/research_design.md](docs/research_design.md).

## Setup

```sh
uv sync                      # Python 3.13, deps from uv.lock
uv run brunder --help
```

## Data collection

```sh
uv run brunder elections                                   # TSE election codes (6257 = 2026 R1, 6258 = R2)
uv run brunder tse-snapshot --ele 6258 [--resume]          # full live-feed tree: national, 27 UFs + abroad, 5,757 municipalities
uv run brunder tse-watch --ele <code> --interval 30        # record a count as it happens (future elections)
uv run brunder tse-build --ele 6258                        # → data/processed/ele2026/e6258_c1/{votes,progress,municipalities}.parquet
uv run brunder tse-open votacao_candidato_munzona 2022     # certified results (priors / transfer estimation)
uv run brunder pm-search "brazil presidential"
uv run brunder pm-download brazil-presidential-election    # markets, 1-min prices, every trade
```

`data/raw/tse_live` cannot be re-downloaded once TSE takes the cycle down (2022 is already gone)
— back it up outside git. Everything else is reproducible from the commands above.
The TSE server rate-limits (429) at high concurrency; keep `--concurrency` ≤ 8.

## Library

| Module | Purpose |
|---|---|
| `brunder.tse_live` | live-feed archive / watch / parse (votes per unit, per-municipality completion time) |
| `brunder.tse_open` | TSE open-data downloads; `president_by_municipality(year)` |
| `brunder.polymarket` | Gamma events, CLOB price history + books, data-api trades |
| `brunder.nowcast` | count-progress model → P(win), national or per state (`uf=`) |
| `brunder.edge` | model-vs-market alignment, crossing delays, lead–lag, scores, cost-aware backtest, Kelly |
| `brunder.microstructure` | wallet concentration, flows, wash round-trips, price-impact persistence, capacity |

## Analyses

`analysis/r2_night.py` — runoff count: reconstructed nowcast vs the Polymarket "Lula wins" price,
for τ ∈ {0.005, 0.01, 0.02}. Writes `results/r2_night.png`, `results/r2_night_summary.csv`,
`results/r2_nowcast_tau*.csv`.

`analysis/calibrate_tau.py` — calibrates τ on the 2026 R1 count (prior: 2022 R1, needs
`brunder tse-open votacao_candidato_munzona 2022`), checks it on the runoff. → `results/tau_calibration*.csv`

`analysis/flows.py` — wallet flows, concentration, PnL to resolution, wash/self-trade checks and
price-impact persistence for the Lula/Flávio winner markets. → `results/flows_*.csv`

`analysis/figures.py` — paper figures from the outputs above → `results/fig1…fig5_*.png`.

`analysis/track_record.py` — cross-election test over every resolved Polymarket election market
(`uv run brunder crawl` first: catalogue + 5-min prices into `data/raw/polymarket_crawl/`, resumable)
→ `results/track_*.csv`, `results/fig7_track_record.png`.
