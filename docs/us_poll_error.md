# US 2026 poll error: weighting, uncertainty and a likely common miss

This is an **out-of-sample analysis**. It writes no config and changes no registered or frozen input. Reproduce with:

```sh
BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_poll_error.py
```

Machine-readable tables are in `results/us_poll_error/` (gitignored). The behavioral tests are
`tests/test_us_poll_error.py`.

## Data and design

- **Polls and certified results:** FiveThirtyEight pollster-ratings `raw_polls.csv`, CC BY 4.0. The race universe is
  Democratic-vs-Republican Senate and governor general elections with at least one poll in the final 21 days. The
  current rule still uses its actual 45-day window, so this is a final-three-week race universe rather than silently
  changing v3's window. The test cycles are 2016, 2018, 2020 and 2022 (200 races); 2006-2014 (277 races) is a robustness
  check. Historical pollster statistics may also use earlier Senate, governor, president and House general-election
  polls, always only from cycles before the one predicted.
- **State lean:** MIT Election Data and Science Lab County Presidential Election Returns 2000-2024,
  doi:10.7910/DVN/VOQCHQ, CC0. Each prediction uses the latest presidential result already known before that election.
- **Education diagnostic:** Census ACS 2020-2024 5-year, public domain, via `site/data/us2026/hub/census.json`. This was
  published after the historical tests: it is labelled look-ahead and cannot select a model.
- **2026:** `site/data/us2026/hub/polls.json` (VoteHub Polling API, CC BY 4.0), public race metadata and the published
  crosstabs in `data_public/us2026_crosstabs.csv`. The crosstab adjustment is the display-only method documented in
  [us_crosstabs.md](us_crosstabs.md).

For each test cycle, every pollster statistic and every standard-deviation model is fitted on **earlier cycles only**.
Error is poll average minus certified two-party Democratic share; all errors below are points of two-party share (twice
that in D-R margin). `v3` is the current rule: $\sqrt n$ weight, 14-day half-life, 45-day window, and 25% maximum weight
per pollster. Race-level MAE, RMSE and interval coverage never count a race once per poll.

The alternatives are:

- recency half-lives 7 and 30 days, no decay, and a 21-day window;
- equal, $\sqrt n$ (v3), $n$, or inverse-variance weights; inverse variance uses the pollster's excess error after
  sampling variance, shrunk by 10 prior polls;
- one latest poll per pollster;
- pollster house-effect correction: prior-cycle poll error minus its race's mean poll error, shrunk by 10 prior polls;
- partisan-sponsored poll weight $\times0.5$, or correction by the sponsor party's prior-cycle mean house effect;
- a **recalled-vote proxy**, because historical recalled-vote crosstabs are not openly archived: subtract the pollster's
  prior absolute lean, shrunk by 10 polls. It is a proxy, not evidence that recall reweighting works;
- a constant correction by the mean error of all earlier cycles (the bet that the industry-wide miss repeats).

The 538 file has no population field. Therefore **LV > RV > adult cannot be tested historically** from this source. The
2026 parser already makes that preference when duplicate versions exist; claiming a historical gain would be invented.

## 1. Weighting backtest

`vs v3` is paired race MAE difference, with a stratified race-bootstrap 95% interval. Negative is better. Coverage and
Brier use poll-count uncertainty fitted on earlier cycles.

| scheme | 2016 | 2018 | 2020 | 2022 | MAE 2016-22 | vs v3 [95%] | vs v3, 2006-14 | coverage 90% | Brier |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v3 - earlier mean error | 2.64 | 1.98 | 3.21 | 2.24 | **2.432** | -0.115 [-0.18,-0.05] | **+0.168** | 0.935 | 0.0661 |
| latest per pollster | 2.80 | **1.89** | 3.52 | 2.24 | 2.499 | -0.048 [-0.10,+0.00] | +0.011 | 0.875 | 0.0649 |
| partisan shift | 2.85 | 1.91 | 3.46 | 2.25 | 2.506 | -0.041 [-0.09,+0.00] | +0.009 | 0.925 | **0.0631** |
| recall proxy (absolute lean) | 2.84 | 1.99 | 3.36 | 2.25 | 2.512 | -0.035 [-0.10,+0.03] | +0.038 | 0.935 | 0.0690 |
| quality + house + partisan shift | 2.90 | 1.94 | 3.45 | 2.26 | 2.531 | -0.016 [-0.09,+0.05] | +0.026 | 0.930 | 0.0638 |
| equal weights | **2.82** | 1.96 | 3.56 | 2.26 | 2.540 | -0.006 [-0.02,+0.01] | -0.001 | 0.900 | 0.0657 |
| inverse-variance quality | 2.86 | 1.97 | 3.54 | 2.24 | 2.543 | -0.004 [-0.02,+0.01] | +0.006 | 0.915 | 0.0660 |
| half-life 7 | 2.81 | 1.95 | 3.53 | 2.31 | 2.544 | -0.003 [-0.03,+0.02] | +0.007 | 0.925 | 0.0655 |
| partisan weight x0.5 | 2.84 | 1.98 | 3.53 | 2.27 | 2.545 | -0.001 [-0.02,+0.01] | +0.006 | 0.915 | 0.0656 |
| **v3** | 2.85 | 1.97 | 3.54 | 2.27 | 2.547 | reference | reference | 0.910 | 0.0659 |
| half-life 30 | 2.88 | 1.98 | 3.56 | 2.25 | 2.552 | +0.005 [-0.02,+0.03] | +0.008 | 0.910 | 0.0662 |
| quality + house | 2.92 | 2.00 | 3.50 | 2.24 | 2.553 | +0.006 [-0.03,+0.04] | +0.016 | 0.915 | 0.0660 |
| no decay | 2.90 | 1.98 | 3.57 | 2.23 | 2.554 | +0.008 [-0.04,+0.05] | +0.039 | 0.900 | 0.0667 |
| house effect | 2.90 | 2.00 | 3.50 | 2.26 | 2.556 | +0.009 [-0.03,+0.05] | +0.013 | 0.910 | 0.0654 |
| weight n | 2.86 | 1.98 | 3.53 | 2.29 | 2.558 | +0.011 [-0.00,+0.02] | +0.003 | 0.915 | 0.0663 |
| window 21 | 3.27 | 2.06 | **3.38** | **2.13** | 2.598 | +0.051 [-0.12,+0.27] | +0.034 | 0.880 | 0.0714 |
| choose best scheme on earlier cycles | 3.27 | 1.89 | 3.52 | 2.24 | 2.605 | +0.058 [-0.06,+0.23] | +0.034 | 0.870 | 0.0684 |

Plain answer:

- No weighting rule is a stable improvement. Latest-per-pollster and partisan correction gain 0.04-0.05 points in
  2016-22, but their intervals include zero and both lose in 2006-14. House effects and quality weighting do not help.
- The remembered-industry-miss correction is the only statistically clear 2016-22 MAE gain, but it **adds 0.17 points**
  of MAE in 2006-14. It exploits a recent run of Democratic overstatement; it does not generalize.
- The recalled-vote proxy lowers recent MAE by 0.035 points, interval [-0.10,+0.03], worsens Brier and loses earlier.
  The actual 2026 recall adjustment cannot be backtested with open historical crosstabs. It remains display only.
- Selecting the prior winner is worse than v3: the scheme chosen from earlier cycles changes (21-day window, then latest
  poll per pollster) and scores MAE 2.605 vs 2.547.

## 2. Error model

### Out-of-sample calibration

| average | standard deviation | mean sd | coverage 90% [95%] | 2016 | 2018 | 2020 | 2022 | coverage 2006-14 | Brier |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| v3 | bins fitted on earlier cycles | 3.33 | **0.910 [0.87,0.94]** | 0.91 | 0.97 | 0.84 | 0.90 | 0.93 | **0.0659** |
| v3 | registered bins | 3.13 | 0.885 [0.84,0.93] | 0.87 | 0.95 | 0.84 | 0.86 | 0.91 | 0.0660 |
| v3 | flat | 3.32 | 0.885 [0.84,0.93] | 0.89 | 0.97 | 0.78 | 0.86 | 0.96 | 0.0661 |
| v3 | only log(number polls) | 3.27 | 0.895 [0.85,0.94] | 0.87 | 0.98 | 0.84 | 0.86 | 0.92 | 0.0677 |
| v3 | heteroscedastic predictors | 3.18 | **0.860 [0.81,0.90]** | 0.84 | 0.98 | **0.70** | 0.85 | 0.91 | 0.0686 |
| v3 | hetero + education (look-ahead) | 3.18 | 0.880 [0.83,0.92] | 0.89 | 0.98 | 0.73 | 0.86 | 0.91 | 0.0684 |

Bootstrap 95% intervals for v3's fitted-bin RMSE / coverage / Brier are respectively
**[2.96,3.65] / [0.87,0.945] / [0.0478,0.0854]**. For the registered bins they are
**[2.96,3.65] / [0.84,0.925] / [0.0474,0.0861]**. The heteroscedastic model cannot change point error, so its RMSE
interval is the same; coverage is [0.81,0.90] and Brier [0.0489,0.0903].


Refitting the simple poll-count bins is the only eligible variable-uncertainty model that keeps aggregate coverage in
85-95%; it is actually *wider* on average than the frozen bins. The registered bins cover 88.5% overall.
Registered-bin coverage by number of polls is 87% (1-2), 91% (3-5), 88% (6-10), 88% (11+). There is no badly
miscalibrated bin to repair.

### Observable predictors

The fitted model is $\log \sigma^2=X\beta$. `abs_lean` is per ten points of absolute prior presidential lean.

| predictor | coefficient (SE) | fitted before 2016 / 2018 / 2020 / 2022 |
|---|---:|---|
| log(number polls) | -0.393 (0.076) | -0.474 / -0.481 / -0.459 / -0.424 |
| abs state lean | +0.372 (0.118) | +0.183 / +0.256 / +0.268 / +0.295 |
| midterm | -0.094 (0.130) | +0.254 / +0.162 / +0.056 / -0.100 |
| incumbent running | -0.220 (0.120) | -0.302 / -0.316 / -0.352 / -0.267 |
| governor | -0.006 (0.124) | -0.055 / -0.061 / -0.047 / +0.014 |

More polls, less partisan states, and an incumbent are associated with lower variance. Midterm, governor and president's
party add no stable signal. Education appears to lower variance (-0.259, SE 0.117 per ten points), but is look-ahead and
cannot count. More importantly, the eligible heteroscedastic model **undercovers**: 86% overall and 70% in 2020, with
worse Brier. The common 2020 miss hit exactly the well-polled races it made most confident. Do not use it for v5.

### Correlated error decomposition

REML crossed random-effects fit, 575 races, 2002-2022:

| component | sd (points) | variance share |
|---|---:|---:|
| cycle-wide common | 1.34 | 16.2% |
| region x cycle | 0.41 | 1.5% |
| persistent state | 0.79 | 5.6% |
| state x cycle | 1.87 | 31.9% |
| race-specific | 2.22 | 44.8% |
| **marginal** | **3.32** | 100% |

Implied error correlations are 0.16 across regions, 0.18 within a region, 0.06 for the same state in different cycles,
and **0.55 for Senate and governor in the same state and cycle**. These matter to multi-race outcomes, not one-race
marginal probabilities.

## 3. Expected 2026 miss

Cycle-wide Democratic-share errors of the current rule were -1.8 (1998), -1.0 (2000), +1.3 (2002), +1.6 (2004), -0.5
(2006), 0.0 (2008), -0.3 (2010), -1.6 (2012), +1.7 (2014), +2.4 (2016), -0.3 (2018), +3.6 (2020), +1.0 (2022).

Orienting the error to the president's party does **not** predict the next midterm: leave-one-midterm-out MAE is 1.32
points, versus 0.76 for simply forecasting zero. From the seven prior midterms, the 2026 in-party Republican error has
mean **+0.01** point, sd 1.23, and 90% predictive interval **[-2.55,+2.57]**. From all 13 even cycles it is -0.41,
[-3.40,+2.58]. Positive means polls overstate Republicans; negative means they understate Republicans.

**Forecast:** no defensible directional adjustment. Center the common 2026 miss at zero and carry about **1.2-1.6 points
of two-party share** (2.4-3.2 margin points) as common-cycle sd. A miss around five margin points is inside the historical
90% predictive range. Recent Democratic overstatement is a real warning, but correcting for it worked in 2016-22 and
failed in 2006-14, so the sign is honestly unknown.

### Current 2026 polls

Averages and sd are points of two-party Democratic share. `frozen` is the primary poll prior already in the config.
`current v3` reruns its rule on `hub/polls.json`; small differences are new polls, not a frozen-input change. The
heteroscedastic sd is shown but rejected above. `latest` is the alternative chosen on all 2002-22 races; it changes any
of 50 polled races by at most 0.43 point. Full 50-race table: `results/us_poll_error/projection_2026.csv`.

| race | polls | frozen avg / sd | current v3 avg / refit-bin sd | hetero sd | latest-per-pollster avg | recall-adjusted avg |
|---|---:|---|---|---:|---:|---:|
| fl-sen (v3) | 10 | 46.69 / 3.34 | 46.68 / 3.13 | 2.62 | 46.68 | 46.45 |
| ga-sen (v3) | 7 | 55.06 / 3.34 | 55.06 / 3.13 | 2.53 | 55.06 | 55.06 |
| nc-sen (v3) | 8 | 56.03 / 3.34 | 55.78 / 3.13 | 2.78 | 55.78 | 55.79 |
| oh-sen (v4) | 13 | 51.96 / 2.28 | 51.96 / 2.57 | 2.44 | 51.92 | 51.85 |
| sc-sen (v3) | 2 | 49.46 / 4.29 | 49.45 / 4.78 | 3.76 | 49.45 | 49.45 |
| tx-sen (v3) | 17 | 51.26 / 2.28 | 51.26 / 2.57 | 2.65 | 51.21 | 51.22 |
| ca-gov (v3) | 5 | 62.90 / 3.34 | 62.90 / 3.36 | 3.68 | 62.91 | 62.47 |
| fl-gov (v3) | 11 | 47.60 / 2.28 | 47.60 / 2.57 | 2.86 | 47.43 | 47.21 |
| ga-gov (v3) | 8 | 49.34 / 3.34 | 49.34 / 3.13 | 2.75 | 49.36 | 49.34 |
| oh-gov (v4) | 13 | 51.47 / 2.28 | 51.47 / 2.57 | 2.72 | 51.38 | 51.35 |
| sc-gov (v3) | 2 | 42.79 / 4.29 | 42.79 / 4.78 | 4.18 | 42.79 | 42.79 |
| tx-gov (v3) | 16 | 48.58 / 2.28 | 48.58 / 2.57 | 2.39 | 48.62 | 48.54 |
| vt-gov (v3) | 2 | 50.84 / 4.29 | 50.84 / 4.78 | 4.41 | 50.84 | 49.89 |

Recall reweighting changes only polls with published recall data; the largest modeled-race move is Vermont governor,
-0.95 point. It is **not** a validated prediction. Pollster correction and deduplication barely move the averages.

## 4. v5 decision

**No poll-side v5. No config was generated.** The decision required a stable out-of-sample gain: lower race error with a
95% paired interval below zero, 90% coverage in 85-95%, no Brier regression, and no regression in the 2006-14 robustness
period. The constant earlier-mean-error correction meets the narrow 2016-22 error/coverage condition but fails badly in
2006-14 (+0.168 point MAE), so it is a changing-era bet rather than a clear winner. Choosing a weighting rule from
earlier cycles scores:

| model | MAE | RMSE [95%] | coverage 90% [95%] | Brier [95%] |
|---|---:|---:|---:|---:|
| v3 registered prior | **2.547** | **3.300 [2.96,3.65]** | 0.885 [0.84,0.925] | **0.0660 [0.0474,0.0861]** |
| earlier-cycle-selected weighting + refit bins | 2.605 | 3.446 [3.01,3.94] | 0.870 [0.82,0.91] | 0.0684 [0.0486,0.0903] |

Its MAE difference is +0.058 [-0.060,+0.233] points, and it is also +0.034 in 2006-14. The heteroscedastic error model
makes intervals smaller but undercovers; the simple refit bins are calibrated but not smaller overall. Lower and honest
uncertainty is therefore not supported by these data. Keep v3 primary and its existing secondary turnout models.
