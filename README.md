# brunder — underpricing of information on Polymarket

Does Polymarket under-price public information? Three parts:

1. **Brazil 2026 case study.** Did Polymarket under-weight public, cheaply computable projections of
   the presidential race (1st round 2026-10-04, runoff 2026-10-25, Lula d. Flávio Bolsonaro
   51.79–48.21)?
2. **Cross-category edge program.** Which Polymarket categories under-weight information, how, and
   is it tradable after costs, under pre-registered, out-of-sample tests with no look-ahead?
3. **Election-night edge tool** (`brunder night`). Turns the official count into a calibrated
   probability faster than the market does, live or replayed from section-level receipt times.

## Documents

| document | contents |
|---|---|
| [docs/research_design.md](docs/research_design.md) | Brazil case study: design, hypotheses, model math |
| [docs/cross_category.md](docs/cross_category.md) | cross-category method and pre-registered results; forward test |
| [docs/edge_tool.md](docs/edge_tool.md) | election-night tool: validation, first-round development, registered runoff and governor replays, fast feed |
| [docs/data_requests.md](docs/data_requests.md) | data sources requested from third parties and their status |
| [prereg/NOTES.md](prereg/NOTES.md) | what each registration does and does not guarantee |

Headline results so far: thin-market shocks revert in 6 of 10 categories (holdout, Holm-corrected);
mentions markets under-price favourites; election-night replays are development or pre-specified
(outcomes known), not yet a forward test. Figures are in [docs/figures](docs/figures).

## Setup

```sh
uv sync                      # Python 3.13, deps from uv.lock
uv run brunder --help
```

## Data collection

```sh
uv run brunder elections                                   # TSE election codes (6257 = 2026 R1, 6258 = R2)
uv run brunder tse-snapshot --ele 6258 [--resume]          # full live-feed tree: national, 27 UFs + abroad, 5,757 municipalities
uv run brunder tse-watch --ele <code>                      # record a count as it happens (state-first, via brunder.feed)
uv run brunder tse-build --ele 6258                        # → data/processed/ele2026/e6258_c1/{votes,progress,municipalities}.parquet
uv run brunder tse-open votacao_candidato_munzona 2022     # certified results (priors / transfer estimation)
uv run brunder pm-search "brazil presidential"
uv run brunder pm-download brazil-presidential-election    # markets, 1-min prices, every trade
```

`data/raw/tse_live` cannot be re-downloaded once TSE takes the cycle down (2022 is already gone)
— back it up outside git. Everything else is reproducible from the commands above.

Where `data/` lives: on the author's laptop `data` is a symlink to an external SSD
(`/Volumes/Untitled/brunder-data`, with its own README and a `MANIFEST.sha256`; check integrity with
`cd /Volumes/Untitled/brunder-data && shasum -a 256 -c --quiet MANIFEST.sha256`). With the drive
unmounted the symlink dangles and data reads fail. Elsewhere, set `BRUNDER_DATA=/path/to/data` or
keep a plain `data/` directory. On the always-on VM it is `/srv/brunder/repo/data` (see `deploy/`).
The TSE CDN rate-limits (429) per client; live reads go through `brunder.feed`, which budgets requests
(see [docs/edge_tool.md](docs/edge_tool.md#reading-the-feed-fast-brunderfeed)).

## Election night

```sh
uv run brunder bweb 2026 1                                  # section-level BU-web files (receipt times + votes)
uv run brunder night replay configs/<config>.toml           # replay a past count against Polymarket prices
uv run python analysis/night_replay.py configs/<config>.toml   # replay + evaluation, logged per config id
uv run brunder stage configs/<config>.toml /tmp/stage       # registered configs: verified local copy of every input…
BRUNDER_DATA=/tmp/stage uv run python analysis/night_replay.py configs/<config>.toml   # …required before the one look
uv run brunder night live configs/<config>.toml             # live: model vs order books every 5 s (paper only)
```

## Library

| Module | Purpose |
|---|---|
| `brunder.tse_live` | live-feed archive / watch / parse (votes per unit, per-municipality completion time) |
| `brunder.tse_open` | TSE open-data downloads; `votes_by_municipality(year, cargo)` |
| `brunder.bweb` | section-level BU-web results: receipt time and votes per polling section, presidential and governor |
| `brunder.feed` | low-latency TSE feed reader: per-edge HTTP/2, newest copy across CDN edges, request budget, latency log |
| `brunder.night` | election-night engine: live (municipal or state model vs order books) and replay; evaluation |
| `brunder.statecast` | state-level count model with a national-swing prior (superseded for replay by `nowcast`) |
| `brunder.transfer` | first-round → runoff vote-transfer model (H1) |
| `brunder.polymarket` | Gamma events, CLOB price history + books, data-api trades |
| `brunder.nowcast` | count-progress model → P(win), national or per state (`uf=`) |
| `brunder.edge` | model-vs-market alignment, crossing delays, lead–lag, scores, cost-aware backtest, Kelly |
| `brunder.microstructure` | wallet concentration, flows, wash round-trips, price-impact persistence, capacity |
| `brunder.universe` | category × week sampled universe of resolved markets; 5-min price paths; pre-signal volume; fill checks |
| `brunder.pit` | point-in-time rules: `asof`, execution delay, embargoed discovery/holdout split, taker cost |
| `brunder.studies` | observation builder and the study kinds (`shock_drift`, `favourite_tilt`, `late_certainty`) |
| `brunder.stats` | event-cluster bootstrap, Holm and Benjamini–Hochberg |
| `brunder.registry` | pre-registration and the holdout vault |
| `brunder.verify` | price-history point-in-time check against trades |
| `brunder.live` | forward-test recorder: discovery, book polling, live signals, book-walk fills, gated verdict |

## Analyses

`analysis/r2_night.py` — runoff count: reconstructed nowcast vs the Polymarket "Lula wins" price,
for τ ∈ {0.005, 0.01, 0.02}. Writes `results/r2_night.png`, `results/r2_night_summary.csv`,
`results/r2_nowcast_tau*.csv`.

`analysis/calibrate_tau.py` — calibrates τ on the 2026 R1 count (prior: 2022 R1, needs
`brunder tse-open votacao_candidato_munzona 2022`), checks it on the runoff. → `results/tau_calibration*.csv`

`analysis/flows.py` — wallet flows, concentration, PnL to resolution, wash/self-trade checks and
price-impact persistence for the Lula/Flávio winner markets. → `results/flows_*.csv`

`analysis/figures.py` — paper figures from the outputs above → `results/fig1…fig6_*.png`.

## Cross-category workflow

```sh
uv run brunder universe all              # sample events per category × week; fetch 5-min price paths (resumable)
uv run brunder verify                    # price-history points vs trades (point-in-time check)
uv run brunder observations              # shocks / horizon observations / label-conflict flags
uv run brunder study prereg/<spec>.toml  # discovery split, run at will
uv run brunder register prereg/<spec>.toml          # spec must be committed; records its hash
uv run brunder study prereg/<spec>.toml --split holdout   # opens only for registered, unchanged specs; every look is logged
uv run brunder live run prereg/forward_v1.toml            # forward test: live signals + order books (run detached)
uv run brunder live status prereg/forward_v1.toml         # counts only; not a look
uv run brunder live evaluate prereg/forward_v1.toml       # one verdict, after the spec's stopping rule
```
