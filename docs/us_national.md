# US 2026 national election-night estimate (`brunder.us_national`)

Its own process (`deploy/brunder-us-national.service`, CPU 15 %, 512 MB, nice 15, 60 s cycle). It changes no registered config, no
Senate/governor model and no House model; it reads what `brunder us live` and `python -m brunder.us_house_night live` already
write, plus the recorder's market books, and writes only `site/data/us2026/national_live/`.

```sh
uv run python -m brunder.us_national live configs/us2026_national/national_v1.toml --out site/data [--snap DIR] [--interval 60]
BRUNDER_DATA=… uv run python analysis/us_national_backtest.py [tune] [test] [report]
```

## Method

All quantities are two-party Democratic share. For a race $r$ with pre-count prior $P_r$ (Senate/governor: poll average, else the
2024-president baseline plus the environment shift measured on the polled races; House: 2024 president on the 2026 lines plus the
generic-ballot swing, `districts.json`) the final share is $P_r + e_r$ with

$$e_r = c + h\,\mathbf 1[\text{House}] + g_{\text{region}(r)} + \lambda_r s_{\text{state}(r)} + \varepsilon_r .$$

| component | sd (points) | source |
|---|---:|---|
| $c$ national common | 1.34 | REML 2002-2022, `docs/us_poll_error.md` |
| $g$ region | 0.41 | same |
| $s$ state (persistent + state x cycle) | 2.03 ($\lambda$ = 0.74 for House) | same ($\sqrt{0.79^2+1.87^2}$) |
| $h$ House environment beyond $c$ | 3.75 | generic-ballot sd 3.98 (`districts.json`) |
| $\varepsilon_r$ | $\sqrt{\text{prior sd}^2-\text{shared}}$, floor 1.0 | race's own prior sd |

**Count signal.** A race with counted votes gives $y_r=\hat s_r-P_r$, variance $v_r=\mathrm{sd}_r^2$, where $\hat s_r,\mathrm{sd}_r$
are the *count-only* projection of its final share (no poll prior): the `share`/`sd` of the v1 row of the race's live
timeline (Senate/governor: `timeline_tail.json`; House: `house_live/timeline_tail.json`), or the same nowcast on the extra feeds.
A race with no counted vote (or whose state's polls have not closed) gives no signal, so before the count the estimate is the prior
exactly. Because $\hat s_r$ carries no prior, nothing is counted twice.

**Posterior.** Gaussian conjugate update of $(c,h,g,s)$, $\varepsilon_r$ of observed races marginalised
(noise $=\varepsilon_r^2+v_r$). Reported as `swing` (mean, sd in points; positive = more Democratic than priors).

**Propagation (joint simulation, 20 000 draws, fixed seed).** Draw the factors from the posterior, then each race given them.
Races with their own live model (Senate/governor with a registered county model, the 40 House districts) keep **their own** mean/sd
as marginal, correlated with all others through the shared draw (`p_control`, headline); `*_pooled` lets them borrow strength from
the other races too; `*_prior` is the same structure with no count. Senate: D caucus = holdovers (34) + races won, control = 51
(Republican vice president); House: 218 of 435. Both chambers use the same draw: balance-of-power probabilities are joint.

## Output (`site/data/us2026/national_live/`, rewritten atomically every cycle)

- `national.json` – latest state:
  - `as_of`, `n_races`, `n_informing`, `n_pooled`, `n_states_closed`, `n_states`, `hours_since_first_close`, `errors`
  - `swing`: `unit`, `common {mean, sd, prior_sd}`, `house_env {mean, sd}`, `house_total {mean}`, `regions {name: {mean, sd}}`, `states {st: {mean, sd}}` (states with counted races only), points
  - `senate`: `d_holdover`, `need`, `p_control`, `p_control_pooled`, `p_control_prior`, `seats {mean, p10, p50, p90, dist[0..100]}`, `seats_prior {mean,p10,p90}`, `races {key: {office, state, prior, prior_sd, source (own|count|prior), counted, modeled, p_d, share, sd, p_d_pooled, p_d_prior}}` (35 Senate + 36 governor races)
  - `house`: `pooled` (true once >= 150 districts have counted votes: only then do House counts move the shared factors; before, the House estimate is the prior plus the 40 own-model districts), `need`, `p_control`, `p_control_pooled`, `p_control_prior`, `seats {…, dist[0..435]}`, `seats_prior`, `n_own_model`, `districts_cols`, `districts {"AZ-01": [prior, p_d, share, sd, src, counted]}`
  - `joint`: `live` / `prior` `{DD, DR, RD, RR}` (first letter Senate, second House, D = Democratic control)
  - `markets`: `senate {polymarket, kalshi}`, `house {polymarket, kalshi}`, `bop {DD, DR, RD, RR}` (Polymarket balance-of-power event), P(D) from the recorder's latest books; `gaps` = our probability minus each price
- `timeline.json` – list of rows (older than 6 h thinned to 10 min, older than 48 h to 1 h; restart-safe): `t`, `h_close`, `n_inf`, `n_closed`, `c`, `c_sd`, `h`, `h_sd`, `P_sen`, `P_sen_pooled`, `P_sen_prior`, `sen_mean`, `sen_p10`, `sen_p90`, `P_house`, `P_house_pooled`, `P_house_prior`, `house_mean`, `house_p10`, `house_p90`, `pm_sen`, `kalshi_sen`, `pm_house`, `kalshi_house`, `joint {DD,DR,RD,RR}`, `bop`
- `timeline_tail.json` – `{rows: last 60 min, tail: true}`; `meta.json` (params, inputs); `status.json` (`t`, `errors`)

Balance-of-power books are only present if the recorder records the Polymarket event `balance-of-power-2026-midterms`
(`--pm-event balance-of-power-2026-midterms` in `deploy/brunder-snap-us.service`; not there today).

## Inputs and live coverage

| source | what | file / module |
|---|---|---|
| `brunder us live` | count-only (v1) `share`/`sd` of the 21 Senate/governor models (signal) and their poll-model version's `share_vN`/`sd_vN` (own marginal; version = `us.poll_model` of the registered configs) | `timeline_tail.json` |
| House process | the same for the 40 district models | `house_live/timeline_tail.json` |
| civicAPI statewide totals, all 435 districts | counted two-party share and `percent_reporting` (assumed precincts) -> count signal for every district without a model; candidate lists -> uncontested seats known beforehand | recorder key `civic_87524` (attribution "civicAPI (civicapi.org)" in `meta.json`) |
| extra official feeds (SD, VA, ID, MD, MA) | county rows -> the same count-only nowcast on the 2024 president county baseline (`us_national_extra`); fetched by this process (one request per feed per cycle, raw bytes kept under `data/raw/us_national/<st>/`) | `brunder.us_national_feeds` |
| recorder books | Senate / House control (Polymarket event + Kalshi `CONTROLS-2026-D`, `CONTROLH-2026-D`) and the Polymarket balance-of-power event | `polymarket_books`, `kalshi_books` |

Coverage checked for part 2b on 2026-10-30 (details and reasons: `NOT_USABLE` in `brunder/us_national_feeds.py`). Verified against
certified totals (county sums within 0.5 %; SD, VA, MD exact; MA exact on its Totals row): **SD, VA, ID, MD, MA**. Checked, not usable
(no open county-level feed on the night, blocked from the VM, robots.txt, or published only after the canvass): OR, LA, WY, NJ, IA, CO, KS,
MI, NH, ME, MT, NV, WI, NY, IL, TN, OK, CT, RI, DE, HI, KY, AR, MS, AL, WV. Idaho's Democratic-side Senate candidate is listed as
Independent: no Democratic row, so no ID Senate signal. Massachusetts posts nothing before the night (`fetch` raises until it does).
South Carolina's Clarity feed is blocked from the VM (202 challenge): SC is a no-count state (prior + national swing only).

## Replays of past nights (docs: `analysis/us_national_backtest.py`, `us_national_2024.py`, `us_national_2020.py`)

### 1. Replay of 2018, 2020, 2022, 2024 (simulated reporting orders; `results/us_national_backtest/`)

County Senate/governor returns (MEDSL; `analysis/us_national_data.py` for all tables), House results by district, 538 poll priors, official
poll-close times. **The order of returns is simulated** (county size, state close time, noise, a per-state early-count lean sd 1.5 pts,
partial counts = county share + sampling noise; 12 replications per night): it stresses the method against an order-bias
assumption, it does not reproduce the real count. Covered = the 2026 live-model states with a feed (CA TX OH GA VT NC FL PA MN NE NM AZ; SC and
AK no-count) and the 40 House districts closest to even in TX FL NC OH GA AZ; everything else = prior + estimated swing. Tuning
(Senate/governor: Huber k; House: sd_house_env, house_c_load, house prior sd; grid files `tune_grid_pass1..6*.csv`) on 2018 and 2020 only;
2022 and 2024 are the test. Four nights: descriptive. Cells: Brier of P(control) / ranked probability score over seat thresholds /
mean |expected seats − actual| / share of nights whose 80 % band holds the actual. "Prior" = the same structure with no count.

| chamber, period | prior alone | 1 h | 2 h | 4 h | 6 h |
|---|---|---|---|---|---|
| Senate, tune 2018/20 | .356/.102/2.6/.50 | .356/.102/2.6/.50 | .104/.047/1.5/1.00 | .027/.027/0.7/1.00 | .022/.026/0.8/1.00 |
| **Senate, TEST 2022/24** | .196/.035/1.0/1.00 | .196/.035/1.0/1.00 | .289/.043/1.5/1.00 | .273/.034/1.2/1.00 | .235/.042/1.4/.96 |
| Senate, TEST, + extra states (SD VA ID MD MA) | same | same | .289/.040/1.3/1.00 | .312/.036/1.2/1.00 | .287/.043/1.5/.96 |
| House (40 districts own), tune 2018/20 | .010/.050/7.0/1.00 | .010/.050/7.0/1.00 | .025/.046/7.6/1.00 | .075/.054/8.3/1.00 | .062/.051/8.0/1.00 |
| **House (40 districts), TEST 2022/24** | .160/.031/1.7/1.00 | .160/.031/1.7/1.00 | .116/.029/2.4/1.00 | .132/.028/3.0/1.00 | .116/.028/3.0/1.00 |
| House + all-district totals (civicAPI-like), tune | .010/.050/7.0/1.00 | .011/.048/7.0/1.00 | .023/.037/6.1/1.00 | .266/.057/7.5/**0.00** | .319/.055/6.7/**0.00** |
| **House + all-district totals, TEST** | .160/.031/1.7/1.00 | .160/.031/1.7/1.00 | .083/.028/3.0/1.00 | .084/.018/2.8/1.00 | .028/.019/2.9/.67 |

Seat error at 4 h by night (live / prior): Senate 2018 0.3/0.2, 2020 1.2/5.1, 2022 1.4/1.1, 2024 1.0/0.8 (with all-district House totals and extra
states: 0.3/0.2, 0.6/5.1, 1.5/1.1, 0.6/0.8). House (40 districts) 12.8/2.3, 3.8/11.7, 2.5/2.0, 3.5/1.5; with all-district totals 8.6/2.3, 6.5/11.7,
3.3/2.0, 2.4/1.5. Reading, honestly: the count sharply improves the Senate when the poll prior misses (2020: 5.1 to 1.2 seats) and is about
as good as, slightly worse than, a good prior (2018, 2022, 2024); Brier of P(control) is worse than the prior in 2022/24 because the prior
leaned the right way and the count adds spread. The House is **not** better than its prior in seat error in 3 of 4 nights: the 40 districts
sit in six southern/midwestern states and are not a sample of the country (2018: covered districts deviated -1.4 points while all contested
districts deviated +1.9), and with all 435 districts reporting the 80 % band fails in 2018/2020 at 4-6 h (coverage 0.00). Two specifications were
changed after replays failed, each time re-chosen on 2018/2020 only: robust Huber weights (an unpolled Republican-incumbent governor race, VT, had a
count 45 points off its 2024 baseline and moved the nation by 1.5 points), and House districts not loading on the Senate/governor poll miss
(`house_c_load` 0). The House pooling threshold (`house_min_pool` 150 districts) was set after seeing 2022/24 (House counts from a few dozen districts
made the seat estimate worse than the prior), so 2022/24 are no longer a pristine test of that rule.

### 2. US 2024 night (RETROSPECTIVE; test cycle, nothing tuned on it; `analysis/us_national_2024.py`, `results/us_national_2024/`)

Per-state count-only paths of the 2024 replay (ENR county feed) pooled with the national / region / state factors, presidential structure fitted on
2000-2020 polls (`us_national_pres_structure.py`: common 1.42, region 0.57, persistent state 1.29, state x cycle 1.38 points). Time (UTC, Nov 6) from which the winner stays
>= 0.9, lead over Polymarket in minutes (+ earlier): GA 02:23, v3 -88, national -43; NC 02:21, -65, -40; PA 04:22, -54, -19; WI 04:36, -35, -20; NV 06:19, +18, +13;
ME 04:40, -181, -136; MI 05:36, -260, -260. Median lead: v3 -65, national (2026 Senate/governor structure) -65, national (presidential) -40. Median |margin error|
(points, 7 contested states) by UTC hour 0..8: prior 2.0 flat; v3 2.0 2.3 1.7 2.3 2.7 1.4 1.4 0.8 0.9; national 2.5 1.6 1.0 0.4 0.2 1.0 1.1 0.9 0.6. The national path is worse than
the prior at hours 0-1 (a handful of early states mislead) and better from 02 UTC. The gain exists because 2024 was one large uniform miss (mean
final - poll average -1.8 points, half of the squared poll error); the expected gain over cycles is smaller.

### 3. US 2020 night (RETROSPECTIVE; `analysis/us_national_2020.py`, NYT county feed, doi:10.7910/DVN/LZXR5A)

Same pipeline (2016 county prior, expected vote 2016 x 1.2, structure 2000-2016 only). Median |margin error| over 11 contested states at 5/25/50 % counted: v3 4.1/3.5/2.3,
national 2.0/1.6/1.9. Time-to-0.9 is the same in 6 states, earlier in NH (268 min) and TX (32 min), **later** in NV and PA (the poll-prior v3 was
at 0.9 for Biden early against the red mirage; the pooled estimate had learned the -2.6 point miss). Sharing improves the margin, not the 0.9 call.

## Decision (2026-11-01)

Senate/governor national estimate: enable (publish `national_live/`, label experimental). It adds information when the polls miss, tracks
the prior when they do not, and its Senate 80 % band held in >= 96 % of replications in 2022/24. House control probability: **publish but label experimental and do not use as a headline**:
without an all-district feed it is the prior (plus the 40 own districts); with the civicAPI totals it is about equal to the prior in seat error and
over-confident in 2018/2020. Balance-of-power comparison needs the recorder to add `--pm-event balance-of-power-2026-midterms`.

## Hooks outside new files (integrator)

1. `deploy/brunder-election-night.target`: add `brunder-us-national.service` to `Wants=`.
2. `deploy/brunder-snap-us.service`: add `--pm-event balance-of-power-2026-midterms` (balance-of-power books); the Civic recorder must write key `civic_87524`.
3. Register `configs/us2026_national/national_v1.toml` (the file lives in a subdirectory because `configs/us2026_*_v1.toml` is globbed as race configs by `brunder.us`, `test_us.py`, `analysis/us_hub.py`; a top-level `us2026_national_v1.toml` would be loaded as a race and fail).
4. No change in `us.py`, `cli.py`, `nowcast.py`, the House process or any registered config.
