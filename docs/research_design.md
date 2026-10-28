# Research design: underpricing of public election information on Polymarket (Brazil 2026)

## Origin notes (verbatim)

> The information edge and prediction markets_is the actual weight of the information being shared
>
> POLYMARKET UNDERPRICED BRAZIL’S PRESIDENTIAL PERFORMANCE THE MARKET IS UNDERPRICED, THERE IS A
> INFORMATION EDGE IN BEING VAGUELY AWARE OF PROJECTIONS, THIS IS A GOOD THING TO WRITE A PAPER ABOUT

## Setting (from archived data)

| | Date | Result (valid votes) |
|---|---|---|
| 1st round (TSE `ele` 6257) | 2026-10-04 | Flávio Bolsonaro 47.03%, Lula 45.16%, Cury 2.89%, Renan Santos 2.24%, Caiado 2.18% |
| 2nd round (TSE `ele` 6258) | 2026-10-25 | Lula 51.79%, Flávio Bolsonaro 48.21% |

Polymarket event `brazil-presidential-election` (~$248M volume) plus per-state, margin, turnout
and city markets for the runoff.

## Core claim, made testable

"The weight of the information" = how much the price moves per unit of public information,
relative to how much it *should* move. Underpricing means the market's response to cheap,
public projections is too small or too late:

$$p_t = p_{t-1} + \beta\,(P_t - P_{t-1}) + \varepsilon_t,\qquad \beta<1 \text{ or lagged}$$

where $P_t$ is a projection any attentive trader could compute and $p_t$ is the price.
The edge is $P_t - p_t$; it is real only if it predicts resolution better than the price
**and** is executable at size after costs.

## Hypotheses

| | Hypothesis | Window | Test |
|---|---|---|---|
| H1 | Between rounds the market underpriced the runoff favourite implied by 1st-round municipal returns + vote-transfer projections. | 10-04 → 10-25 | Projection $P$ vs price $p$; Brier/log score on the outcome; size of the gap vs. polls. |
| H2 | On election night the price lagged a count-based nowcast. | count of 10-25 (and 10-04) | `edge.crossing_times`, `edge.information_lag` (peak corr of Δp on lagged ΔP at L>0). |
| H3 | Sub-national markets (1st place in state X) are more underpriced than the national market (thinner, less attention). | both rounds | Same metrics per state market, using `nowcast(..., uf=)`. |
| H4 | Mispricing episodes coincide with concentrated / non-informational flow (manipulation or whale ideology). | full history | `microstructure.concentration`, `wallet_flows`, `round_trips`, `price_impact` reversal (λ short ≫ λ long). |
| H5 | The edge is capturable net of spread, fees and capacity. | H1–H3 windows | `edge.backtest` with costs; stakes capped by `microstructure.capacity`. |

H4 matters for interpretation: persistent underpricing in a deep market is either (a) limits to
arbitrage / attention (Grossman–Stiglitz: the edge is the rent to the informed), or (b) a
manipulator/ideological whale holding the price off (Rhode–Strumpf; Rothschild–Sethi).

## Models

### A. Between rounds: runoff projection from 1st-round municipal returns (H1)

Per municipality $i$, runoff share of Lula
$$s^{(2)}_i = \frac{L_i + \sum_k \theta_k X_{ik}}{L_i + F_i + \sum_k X_{ik}}$$
$L_i, F_i$ = 1st-round Lula / Flávio votes, $X_{ik}$ = votes of eliminated candidate $k$,
$\theta_k$ = share of $k$'s voters transferring to Lula. Estimate $\theta$ by ecological
regression on 2022 R1→R2 municipal data (`tse_open.votes_by_municipality(2022)`), then
perturb with poll-reported transfer intentions; add turnout change per municipality.
Aggregate to national and state win probabilities with the same variance structure as B.
The naive prior `nowcast.prior_from_final(r1, cand_a="13", cand_b="22")` (θ = Lula's share of
the top two) is the baseline; the transfer model is the "vaguely aware of projections" upgrade.

### B. Election night: count-progress nowcast (H2, H3) — `brunder.nowcast`

Formulas in the module docstring. Units are TSE municipalities (5,570 + abroad `zz`).
Prior $s_{0i}, V_{0i}$ from model A or the previous round; state swing shrunk to national
swing with $\kappa$; uncertainty from municipal residuals, state-level swing dispersion and a
national reporting-order bias $\tau$.

Calibration of $\tau$ (the parameter that decides when the result is "known"):
1. 2026 R1 counts (known outcome), from the reconstructed path;
2. any third-party archive of the 2022 live feed (TSE removed the 2022 JSON tree);
3. live snapshots in future elections (`brunder tse-watch`).
Report results as a function of $\tau \in \{0.005, 0.01, 0.02\}$ — the edge claim must survive
a conservative $\tau$.

**Data limitation.** No live snapshots were recorded during the 2026 counts. The final `ab`
files give each municipality's completion time; `nowcast.reconstruct_states` treats a
municipality as uncounted until it finishes. A live observer saw more (partial big-city
counts) and a less small-town-skewed sample, so the reconstruction's bias in $P_t$ is unsigned:
reconstructed leads over the market are indicative, not a bound, until checked on live snapshots.

**Runoff night timeline** (Brasília time, UTC-3) — sets what was public when:

| BRT | Event | Source |
|---|---|---|
| 17:00 | polls close nationwide (unified voting hours) | O Globo, 2026-10-25 |
| 17:06 | first partial published: 0.25% of sections (1,225 / 499,248), Lula 57.66% | [InfoMoney](https://www.infomoney.com.br/politica/tse-pausa-divulgacao-de-resultados-das-eleicoes-com-025-das-urnas-apuradas/) |
| ~17:06–19:00 | STF (Zanin) extends voting to 19:00 where still open; TSE pauses BU receipt, totalization and publication | InfoMoney; [Times Brasil](https://timesbrasil.com.br/brasil/decisao-2026-tse-anuncia-retomada-da-totalizacao-e-divulgacao-dos-votos-apos-as-19h/) |
| 19:00+ | publication resumes | Times Brasil |
| 23:26 | totalization finished (`tf = s` in the live feed) | archived `br` file |

The reconstruction uses `release = 22:00 UTC`: municipalities finished earlier become visible
then. The 17:06 partial cannot be identified in the final files, so the model is prior-only until
release while the market already reacted to it — this favours the market.

## Preliminary results (`analysis/r2_night.py`, reconstructed path) — SUPERSEDED

> Superseded: the municipal reconstruction lagged the real count badly (a third-party count history
> shows the real count several times further along at 20:00 BRT; figures withheld, see below). The
> lags, scores and capacity below understate what live observers saw and must be rerun on the
> official runoff section files before use.

Prior = 2026 R1 two-party share per municipality (proportional transfers). Metrics from release
(22:00 UTC) to 02:30 UTC.

| τ | market reaches 0.9 after model (min) | Brier model | Brier market | backtest ROI (5pt threshold, 0.5¢ half-spread) | taker YES notional ≤ signal cost, ±5 min |
|---|---|---|---|---|---|
| 0.005 | +66 | 0.0015 | 0.0351 | +43% (21 entries) | $2.5M |
| 0.010 | +42 | 0.0043 | 0.0351 | +52% (17) | $1.6M |
| 0.020 | −30 | 0.0207 | 0.0351 | −22% (22) | $0.6M |

Readings:
- The first post-release returns already showed the final national swing (+2.5–2.8 pt toward
  Lula vs the R1 two-party share; final +2.8). The market sat at ~0.40 while the nowcast was at
  0.71–0.84 depending on τ.
- The edge's existence hinges on τ: at τ = 0.02 the market led. See the τ calibration below.
- Pre-count, the naive R1-proportional prior gives P(Lula) ≈ 0.19, about where the market
  traded (0.18–0.24 that afternoon). The between-rounds underpricing (H1) therefore needs a
  better projection than naive proportional transfers: vote-transfer model, turnout, polls.
- Cross-correlation peak lags are unstable across τ (−38 to +51 min); the step-like series make
  `information_lag` noisy at 1-minute resolution. Rely on crossing delays and scores.

### τ calibration (`analysis/calibrate_tau.py`)

Races: Brazil + each state. Checkpoints: first moment the reconstructed count passes 10/25/50/75%
of a race's votes. z = (final share − projected share)/projected sd; calibrated ⇒ E[z²] ≈ 1.
R1 (Lula share of valid votes, prior = Lula 2022 R1 per municipality, release 20:00 UTC) is
out-of-sample for the runoff; R2 (prior = 2026 R1 two-party share) is in-sample.

| τ | R1 E[z²] | R1 95% coverage | R1 national z @10/25/50/75% | R2 E[z²] | R2 national z @10/25/50/75% |
|---|---|---|---|---|---|
| 0 | 2.02 | 0.89 | 5.9 / 5.6 / 4.1 / 2.1 | 0.21 | 1.1 / 1.9 / 0.7 / −0.1 |
| 0.005 | 1.36 | 0.90 | 3.9 / 3.3 / 2.6 / 1.7 | 0.13 | 0.5 / 0.6 / 0.2 / −0.1 |
| 0.01 | 0.87 | 0.99 | 2.4 / 2.0 / 1.6 / 1.2 | 0.09 | 0.3 / 0.3 / 0.1 / 0.0 |
| 0.02 | 0.44 | 1.00 | 1.3 / 1.0 / 0.8 / 0.7 | 0.04 | 0.1 / 0.1 / 0.1 / 0.0 |

- Pooled over states, R1 picks τ ≈ 0.01. The R1 national race alone needs τ ≈ 0.02: early returns
  understated Lula by ~2–3 pt (z > 0 at every checkpoint) — the reporting-order bias τ exists for.
- R1 is the harder case (4-year-old prior, different opponents); the runoff prior is 3 weeks old
  with the same two candidates, and in R2 the model is over-dispersed even at τ = 0.
- Working choice: τ = 0.01 headline, τ = 0.02 robustness. Under τ = 0.01 the market lagged the
  model by ~42 min to 0.9; under τ = 0.02 it did not. The H2 claim is therefore conditional on
  the runoff prior being better than the R1 prior — which the R2 in-sample z's support but cannot
  prove from one night.

### Flows and manipulation (`analysis/flows.py`, H4)

Lula and Flávio winner markets combined in a Lula-YES frame (complements after R1). Volume = taker
notional (each fill counted once); wallet stats use every fill row of that wallet.

| | between rounds (10-05 → 10-25 20:00 UTC) | count night (22:00 → 02:30 UTC) |
|---|---|---|
| taker volume | $12.4M | $19.0M |
| wallets | 9,427 | 7,679 |
| top-10 wallets' share of fill notional | 23% | 34% |
| net taker flow toward Lula | −$3.3M | +$1.8M |
| PnL to resolution of losing wallets | −$8.7M | −$3.3M |
| top-10 losers' share of losses | 30% | 28% |
| same-wallet both sides of one tx (direct wash) | 0 | 0 |
| opposite-side same-size flips < 10 min (share of volume) | 9% | 48% |
| price impact per $10k net taker flow, 1m / 5m / 30m / 2h (pt) | 0.02 / 0.11 / 0.14 / 0.06 | 0.01 / 0.04 / 0.09 / 0.11 |
| impact t-stats | 5.4 / 16.8 / 10.3 / 2.0 | 0.6 / 1.4 / 1.7 / 1.2 |

Readings:
- Between rounds, aggressive flow was net −$3.3M against the eventual winner. Price impact built
  up to 30 min and then ~55% reverted by 2 h — the transient-pressure pattern, consistent with
  uninformed or price-pushing flow on the Flávio side rather than information.
- The losses were not one manipulator: the top 10 losing wallets carry 30% of losses. The largest
  single loser (`0xbc75…`, 7 fills) sold ~1.13M Lula-YES at ≈0.13 (−$0.98M to resolution).
- The mirror image: wallet `0x2e7b…` accumulated +1.28M Lula-YES between rounds at 0.185
  (+$1.05M), then sold 1.67M on count night at ≈0.75 — taking profit before the count settled.
- No direct self-trading. The 48% "flip" share on count night is market-making churn, not
  evidence of wash trading: the rule (opposite side, ±10% size, < 10 min) matches liquidity providers.
- On count night flow had no significant price impact — prices moved on the published count, not
  on order flow, which is what the nowcast lag result measures.


### Third-party runoff count history (Poliwave): unlicensed, figures withheld

poliwave.com publishes a timestamped history of the runoff's national count (share of sections, raw
shares) and a projected final share. A copy was archived privately after the count ended. Its terms
forbid scraping and a licence has been requested ([data_requests.md](data_requests.md)), so **no
figure derived from it is published in this repo**; they will be re-derived from the official
runoff section files (BU-web, receipt time per section) once TSE publishes them.

What it was used for privately, and how far it was checked:
- Raw-count columns: consistent with TSE-published partials reported in the press and with the
  national path implied by the TSE feed, so likely the real live count. Used only to see that the
  municipal reconstruction above lagged the real count, which is why that section is superseded.
- Projection column: **not verified as what readers saw live** (it could have been recomputed
  afterwards; Wayback captures of the page have no content; no contemporaneous mention found).
- Qualitatively, the history suggests the market followed the raw national count, including a dip
  while the raw count briefly favoured Flávio, rather than a projection that corrects for reporting
  order. This is a hypothesis for the official replay to test, not a finding.

### Third-party captures of the TSE feed (Wayback Machine, `data/raw/wayback/`)

What was recorded during the counts, by someone other than us:

| round | mid-count captures | content |
|---|---|---|
| R1 (6257) | 17:26 BRT (1.70% of sections): national + all 27 state files + abroad; 21:43 BRT (99.57%) national | the only mid-count state cross-section found |
| R1 national file | the other 16 captures are pre-count (zeros) or ≥ 99.9% counted | no usable national path |
| R2 (6258) | national only, at 21:55 BRT (near-final) | no mid-count state or municipal data |

One snapshot cannot calibrate a bias-vs-%-counted curve. What it does show is *where* the early error
comes from. At 17:26 BRT on R1:

| view | Lula | vs final 45.16 |
|---|---|---|
| national file (includes abroad: 58% counted, Lula 52.8%) | 42.19 | −3.0 pt |
| domestic, pooled across states | 40.90 | −4.3 pt |
| early state mix evaluated at each state's *final* share | — | −4.95 pt (composition) |
| within-state deviation, early vs final: mean (weighted by counted votes) | — | +0.7 pt |
| within-state deviation: spread across the 26 states with counts | — | weighted sd 3.5 pt, unweighted sd 6.7 pt, range −11.1 (RJ) to +21.5 (RR) |
| state-weighted projection (each state's partial share × its electorate) | 43.49 | −1.7 pt |

The systematic part of the early national error is **which states report first** (Paraná, DF,
Tocantins, Rondônia: Bolsonaro-leaning, high early %): composition alone is −4.95 pt. Within states
the early-vs-final deviation averages out (+0.7 pt) but is large state by state: sd 3.4 pt even
among the 9 states with ≥ 2% counted (PB +4.5, RO +6.4, MT +5.5, AM −3.8), and wilder below 2%. That
dispersion is the state-level variance term the model needs (σ_state ≈ 3.5 pt at 1–12% counted, from
one cross-section). Re-weighting state partials by state size removes ~40% of the national gap at
1.7% counted even with no model; the remainder comes from states with ~0.04% counted (SP, RJ) whose
partial shares are noise and need shrinking toward a prior. So the projection should be built
from **state files**, which are also published first: in this cross-section state files carry
timestamps 17:25:14–17:26:24 BRT, the national aggregate 17:26:29 (up to 75 s later).

`brunder tse-watch` now polls the national and all state files every interval and snapshots whenever
*any* state changes (it previously waited for the national file).

### State-level nowcast on the archived R1 cross-section (`brunder.statecast`)

Prior per state = Lula's 2022 R1 share, expected votes = 2022 R1 valid votes (both known ex ante);
each state's swing shrinks toward the pooled domestic swing with κ = 200,000 counted votes (set
before running). At 17:26 BRT, 1.70% of sections counted. Re-derived 2026-10-27 with the current
model (which has since gained a national-swing prior: sd 5 pt, variance halved at 200k counted votes) by
`analysis/reporting_order_test.py`; the first run, before that change, gave +0.28 pt for the nowcast.

| method | projected Lula R1 share | error vs final 45.16 |
|---|---|---|
| national file (raw) | 42.19 | −2.97 pt |
| state partials weighted by 2022 size | 43.45 | −1.71 pt |
| **state nowcast (κ = 200k, current defaults)** | **45.66** | **+0.50 pt** |
| sensitivity: κ = 0 / 50k / ∞ | 43.64 / 45.36 / 45.98 | −1.52 / +0.20 / +0.82 |

The fair test here is the point estimate only. An earlier version reported "± 1.17" using a
within-state σ taken from this same snapshot (in-sample) and no shared national error. The model
now uses inputs from other data: σ_swing = 6.6 pt (between-state dispersion of the PT first-round
swing, 2018 → 2022) and a correlated national error τ = 1 pt (not yet calibrated on a state-level
path). With those, the band at this snapshot is ± 2.17 pt (state 1.93, national 0.98). τ must be
calibrated before the model's p_win is used for any count-night lag claim.

A public, ex-ante method applied to the state files would have been within 0.5 pt of the final
first-round result at 1.7% counted, against a 3-pt error in the raw national number. This is one
snapshot: it shows the information was in the state files, not how the error evolves through the
count. The runoff has no archived state path, so the same model cannot yet be run on count night.

## H1 — between rounds: vote-transfer projection (`brunder.transfer`, `analysis/transfer_backtest.py`)

Runoff two-party share per municipality = α·(PT R1 share) + β·(opponent R1 share) + θ·(others' R1
share), fitted by bounded weighted ecological regression; applied out of sample (transfers from
cycle c project cycle c + 4 from its first round only).

| target | proportional split | transfers from previous cycle | actual | in-sample fit |
|---|---|---|---|---|
| 2018 (Haddad) | 38.08 (−6.8) | 39.78 (−5.1, 2014 transfers) | 44.87 | 44.92 |
| 2022 (Lula) | 52.67 (+1.8) | 53.74 (+2.8, 2018 transfers) | 50.90 | 51.09 |
| **2026 (Lula)** | **48.76 (−3.0)** | **47.64 (−4.1, 2022 transfers)** | **51.79** | 51.50 |

In-sample θ (others → PT): 2014 0.43, 2018 0.63, 2022 0.32, **2026 0.69**. Eliminated
candidates' voters broke for Lula far more than in 2022, which no first-round-night projection
based on past transfers could know.

The benchmark rests on two unknowns: the national error σ (4.1 pt is the out-of-sample RMS of only
two cycles) and the transfer rate θ of eliminated candidates' voters, which swung from 0.32 (2022)
to 0.69 (2026). Model P(Lula) against the 21 daily market closes, Oct 5–24
(`results/transfer_benchmark_grid.csv`, α and β held at the 2022 fit):

| θ (others → Lula) | projected share | P_model at σ = 2 / 3 / 4.1 pt | days the market was below P_model (of 21) | mean gap P_model − p |
|---|---|---|---|---|
| 0.32 (2022) | 47.66 | 0.12 / 0.22 / 0.29 | 2 / 15 / 21 | −0.06 / +0.04 / +0.11 |
| 0.43 (2014) | 48.52 | 0.23 / 0.31 / 0.36 | 17 / 21 / 21 | +0.05 / +0.13 / +0.18 |
| 0.50 | 49.07 | 0.32 / 0.38 / 0.41 | 21 / 21 / 21 | +0.14 / +0.20 / +0.23 |
| 0.63 (2018) | 50.08 | 0.52 / 0.51 / 0.51 | 21 / 21 / 21 | +0.34 / +0.33 / +0.33 |

The market (0.105–0.25; 0.105–0.16 for most of Oct 5–16) sat at or below most of this grid. Only
the lowest past transfer rate with a tight σ puts the model below the market. Polls published 18–20 Oct were
split but leaned Flávio (Datafolha 51–49, PoderData 52–48, RealTime Big Data 52–48 for Flávio;
Vox Brasil 51–49 for Lula; as reported by UOL, CNN Brasil, Portal 6).

**H1 is inconclusive.** Whether the market under-priced Lula between rounds depends almost entirely
on the transfer assumption, which past cycles do not pin down (θ 0.32–0.63 before 2026), and σ
rests on two cycles. The grid leans toward "the market was low", but this is a sensitivity table, not
a test. Making it one needs a defensible ex-ante θ: poll-reported second choices of each eliminated
candidate's voters, or transfer rates by candidate ideology across more elections. The count-night
evidence is currently the stronger of the two.

## Count-night section: what the paper can claim now

1. Market vs the runoff's raw national count, minute by minute: to be measured on the official
   runoff section files (the only series seen so far is third-party and unlicensed; see above).
2. The raw count was predictably biased by which states report first; a state-level nowcast built
   from public state files and ex-ante priors removes most of that bias (R1 test: −2.97 → +0.50 pt).
3. Whether that model called the runoff earlier than the market needs the runoff state files over
   time. Requests: [data_requests.md](data_requests.md).

## Election-market track record — exploratory (Oct 2026)

> Historical record of the exploration that motivated `prereg/shock_depth_v1.toml`. The scripts
> were replaced by the pre-registered pipeline ([cross_category.md](cross_category.md)); their
> anchor used min(scheduled end, close), which leaks resolution timing into the near/far windows.
> Treat these numbers as hypothesis generation, not evidence.

Universe: 1,957 closed events tagged elections / global-elections / world-elections; 9,215
resolved binary markets ≥ $1k volume; 9,205 with prices (5-min, anchor A = min(scheduled end,
close), window A−14d…A+3d). Analysis panel: 5,380 markets / 1,660 events with undecided prices
(0.03–0.97) in A−7d…A+2d. CIs: event-cluster bootstrap.

After the first ≥10-pt one-hour move in a market, bet the direction of the move, hold to resolution:

| subset | markets | events | drift s·(y−p) to resolution | ROI per $ at 0 / 1¢ / 2¢ cost |
|---|---|---|---|---|
| all | 3,259 | 1,235 | −7.1 pt [−8.7, −5.6] | −22% / −23% / −25% |
| vol ≥ $100k | 596 | 324 | −1.2 pt [−5.2, +2.8] | −4% / −6% / −8% (CIs include 0) |
| **vol ≥ $1M** | 176 | 100 | **+8.8 pt [+0.9, +16.4]** | **+22% [4, 42] / +19% [3, 37] / +17% [−0.1, 35]** |
| near anchor (A−24h…A+48h) | 1,264 | 626 | −4.2 pt | −15% … −19% |

Calibration: prices are close to calibrated overall (favourite slope +0.04 [0.01, 0.06], all
markets; not significant for ≥ $100k). Momentum on the full 30-min grid is negative in every
subset (1h moves partly revert), strongest in thin markets.

Readings (post-hoc, superseded by the pre-registered test below):
- The "≥ $1M" slice was one of 8 slices and was chosen after seeing results; Brazil events were
  in the sample. Excluding all 84 Brazil events and Holm-adjusting across the 8 slices, it is
  +7.1 pt [−0.8, +14.8], p_Holm = 0.135 — **not significant**. Lifetime volume is also endogenous
  (markets that trend into news attract volume afterwards).

### Depth test (exploratory; specified after seeing the slices above)

Brazil events excluded; signal = first ≥10-pt one-hour move; depth = log10(1 + taker notional in
the 7 days *before* the signal,
from data-api trades — known at entry); outcome = drift s·(y − p); H1: drift rises with depth.

| threshold | markets | events | drift per 10× pre-signal volume | 95% CI | p (b ≤ 0) |
|---|---|---|---|---|---|
| 5 pt | 3,972 | 1,347 | +0.9 pt | [−0.0, +1.9] | 0.027 |
| **10 pt (primary)** | **3,175** | **1,195** | **+2.6 pt** | **[+1.5, +3.7]** | **0.0005** |
| 15 pt | 2,656 | 1,092 | +2.8 pt | [+1.6, +4.1] | 0.0005 |
| 20 pt | 2,176 | 978 | +3.3 pt | [+2.0, +4.8] | 0.0005 |

Drift by pre-signal depth (10-pt signal; descriptive):

| 7-day pre-signal volume | markets | events | drift s·(y − p) | fade ROI per $ at 0 / 1¢ / 2¢ (mid prices, no CI) |
|---|---|---|---|---|
| < $1k | 1,741 | 699 | −9.5 pt | +17% / +13% / +10% |
| $1k–10k | 974 | 547 | −8.3 pt | +26% / +20% / +16% |
| $10k–100k | 317 | 207 | +2.3 pt | −14% / −17% / −20% |
| $100k–1M | 121 | 81 | +1.2 pt | +23% / +10% / +2% |
| ≥ $1M | 22 | 16 | +22.7 pt | −75% (n too small) |

Readings:
- The pre-registered relationship holds out of sample: the deeper the market before a shock,
  the less the shock reverts. Big moves in thin markets overshoot by ~8–10 pt and revert
  (overreaction by thin, uninformed flow); in deep markets they hold.
- What is **not** shown: that deep markets systematically *under*-react. Drift in the
  $10k–$1M bins is +1–2 pt, small; the ≥ $1M bin has 22 markets.
- The large fade ROIs come from markets trading < $10k a week, computed at historical mid/last
  prices. Those books are where execution at the quoted price is least credible and capacity is
  tiny. Next: check fills — did trades on the fade side occur at ≤ entry + cost after the signal,
  and for how much notional.


### C. Edge and capture — `brunder.edge`

- `align`: as-of join on a 1-minute grid (no look-ahead).
- `crossing_times`: first time P and p settle above 0.9 / 0.95 / 0.99. Minutes between them = the
  information delay.
- `information_lag`: cross-correlation of increments.
- `scores`: Brier/log loss over the window for model vs market.
- `backtest`: threshold strategy, hold to resolution, half-spread + fee; `kelly` for sizing.
  Dollar edge only with stakes capped by `microstructure.capacity` (volume that actually traded
  on that side near that time).

### D. Manipulation diagnostics — `brunder.microstructure`

Wallet concentration through time, top net-position wallets and their VWAP, round-trip
(wash) candidates, and price-impact persistence. A manipulation episode = large one-sided flow
moving p away from P, followed by reversion toward P.

## Data sources

| Source | What | Code |
|---|---|---|
| TSE live feed `resultados.tse.jus.br/oficial` | national/state/municipal votes + per-municipality completion time; **ephemeral** | `tse_live.snapshot/watch/parse_snapshot` |
| TSE open data `cdn.tse.jus.br/estatistica/sead/odsele` | certified results by municipality×zone and section; 2022 for priors | `tse_open` |
| Polymarket Gamma | events, markets, token ids | `polymarket.event/markets` |
| Polymarket CLOB | price history (1-min), order book snapshots | `polymarket.price_history/book` |
| Polymarket data-api | every fill: wallet, side, size, price, tx hash | `polymarket.trades` |
| Polls (to add) | registered polls (TSE PesqEle), aggregators | — |

## Literature anchors

- Grossman & Stiglitz (1980), informationally efficient markets are impossible — the edge is the return to information.
- Hong & Stein (1999); DellaVigna & Pollet (2009) — gradual diffusion / inattention to public information.
- Wolfers & Zitzewitz (2004), prediction markets survey; Snowberg & Wolfers (2010), favourite–longshot bias.
- Rhode & Strumpf (2004, 2008), historical betting markets and manipulation; Hanson & Oprea (2009), manipulators can aid accuracy; Rothschild & Sethi (2016), trader-level strategies on Intrade.
- Kyle (1985), price impact.

## Paper outline

1. Introduction — the Brazilian runoff: R1 leader lost; what the market priced vs what the public data implied.
2. Data — TSE live tree archive, Polymarket prices/trades.
3. Projections — model A (between rounds), model B (count).
4. Results — H1/H2/H3 edges, delays, scores.
5. Who was on the other side — H4 flows.
6. Capturing the edge — H5 net of costs and capacity.
7. Discussion — limits to arbitrage vs manipulation; why sub-national/foreign-election information is underweighted.
