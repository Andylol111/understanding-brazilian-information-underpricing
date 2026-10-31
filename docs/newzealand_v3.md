# New Zealand 2026: the v3 model (`configs/nz2026_v3.toml`)

v3 extends v2 (same markets, prior, feed and seed) and changes only the vote-count model, behind config flags in
`brunder.newzealand`. v1 and v2 keep every new option off; `tests/test_newzealand_v3.py` checks that their seat draws are
bit-identical to the pre-v3 model (sha256 of the int64 seat array at 0%, 35% and 100% counted). v3 is a DRAFT until it is
committed and hash-registered; nothing in it uses 2026 count data or market prices.

## What changed and how each piece was chosen

| piece | flag(s) | evidence |
|---|---|---|
| (a) honest seat tails | `count_log_sd`, `count_exp`, `special_log_sd`, `special_cand_sd`, `candidate_log_sd`, `error_df`, `bloc_log_sd` | party-vote error sd $=\sqrt{floor^2+special^2+(count\cdot(1-f)^{exp})^2}$ with Student-t(4) errors; candidate-score noise $\sqrt{cand^2(1-f)+special\_cand^2}$. Chosen by coordinate descent on the **2017 and 2020** night replays (objective: bracket log loss plus a penalty if 90% party-seat intervals cover under 92%). 2023 is untouched test data. The search picked `special_log_sd = 0`; because two elections cannot establish such a tight tail, v3 uses 0.03 (leave-one-election-out RMS of the special shift over 2017/20/23 is 0.027). |
| (b) special-vote shift | `[special]` (log final/night share per party) | Official voting-place files: ordinary (advance + election day) vs all votes. Shift = mean of 2017, 2020, 2023 (GP +0.058, LAB +0.016, NAT -0.033, ACT -0.031, NZF -0.047, OPP +0.075, TPMAO +0.131). Special votes are 16%, 17%, 21% of the vote and settle the markets. In the backtest each scored year uses only the other training year(s). |
| (c) poll prior | `[poll]` | Mean of polls ending in the 21 days before election day (`analysis/nz_polls.py`; Wikipedia poll tables, CC BY-SA, in `data_public/nz_polls_*.csv`). Tilts the 2023 electorate compositions to the poll on the election-night scale, so its weight fades as ordinary votes arrive (`prior_votes`); its error is the `count_log_sd` at nothing counted. Historical poll error (final vs 3-week mean, parties >= 3%): RMS log share 0.073 (2017), 0.136 (2020), 0.116 (2023); pooled 0.112 (`analysis/nz_polls.py error`). |
| (d) advance vs election day | `advance_shrink`, `[advance] file` | Implemented and tested, **not adopted** (`advance_shrink = 0`). Advance places hold 47%/71%/61% of ordinary votes (2017/20/23) and report first. The national party gap between advance and election-day votes flips sign between elections (NAT advance/election-day log ratio -0.02, -0.16, +0.13), so it cannot be corrected; the per-electorate deviation from the national gap is persistent (r about 0.75 for NAT between 2020 and 2023) and `data_public/nz2023_advance_split.csv` holds it, but using it did not improve the 2017/2020 training objective and was worse on 2023 (table below, "+ advance split"). |

## Backtest design

`analysis/nz_history_fetch.py` fetches the Commission's historical voting-place CSVs from the Internet Archive (the
Commission site answers non-browser clients with a Cloudflare 403, which we do not evade). `analysis/nz_replay.py` reveals
the official voting-place results of 2017, 2020 and 2023 in six orders (random, small-first, advance-first, advance-last,
region-lag, and `night` = all advance places first, then election-day places) because the Commission does not archive
timestamped counts; the prior of each year is the previous election's electorates (2014 HTML for 2017). At each share of
voting places counted the model sees only ordinary votes, and everything is scored against the FINAL official result. Stages
are fractions of **voting places** counted. Metrics are means over orders, 4 seeds, 2000 draws: `cover` = share of party
seat totals inside the 90% interval; `brier` / `ll` = Brier score and log loss of the party seat brackets (width 5 for
NAT/LAB, 3 otherwise; ll is floored at p = 0.001); `brier_nl` = the same for National and Labour only; `brier_most` =
most-seats Brier; `ll_margin` = log loss of the National-minus-Labour margin bands in the config; `pit_lo/hi` = share of
final seat totals below the 5th / above the 95th percentile (should be about 0.05 each).

Reproduce: `uv run python analysis/nz_history_fetch.py 2017 2020 2023`, `uv run python analysis/nz_v3_study.py select`,
`uv run python analysis/nz_v3_study.py report out.md`.

## Results

On the 2023 test v3 improves the seat brackets and the most-seats probability at every stage up to 95%, and the 90%
intervals cover 92% of party seat totals (v1: 90%, and only 50% once all ordinary votes are in). It is not better everywhere: at 100% of ordinary places v1's brackets are
slightly better (log loss 0.17 vs 0.21) although its intervals are overconfident there (cover 0.50), and the National-minus-Labour margin
bands are worse in 2023 (log loss 1.21 vs 0.64): the 2017/2020 special-vote shift moved the projected margin to 9.9 points, next
to the 10-point band edge, against a final 11.2. Margin bands were much better in 2017 and 2020 (0.65 vs 2.25 and 0.09 vs 0.45).

### Overall by year and variant (all six count orders, all stages)

| year | variant | cover | brier | ll | brier_nl | brier_most | ll_margin | pit_lo | pit_hi | worst_ll |
|---|---|---|---|---|---|---|---|---|---|---|
| 2017 | v1 | 0.904 | 0.482 | 1.087 | 0.746 | 0.205 | 2.249 | 0.033 | 0.070 | 1.976 |
| 2017 | tails only | 0.923 | 0.279 | 0.499 | 0.434 | 0.016 | 1.163 | 0.003 | 0.087 | 1.053 |
| 2017 | tails + special shift | 0.918 | 0.205 | 0.395 | 0.306 | 0.030 | 0.799 | 0.005 | 0.090 | 0.742 |
| 2017 | tails + special + poll (as selected) | 0.920 | 0.144 | 0.263 | 0.236 | 0.030 | 0.566 | 0.000 | 0.093 | 0.638 |
| 2017 | v3 | 0.922 | 0.161 | 0.300 | 0.267 | 0.032 | 0.654 | 0.000 | 0.083 | 0.684 |
| 2017 | v3 + advance split (not adopted) | 0.922 | 0.161 | 0.300 | 0.267 | 0.032 | 0.654 | 0.000 | 0.083 | 0.684 |
| 2020 | v1 | 0.929 | 0.545 | 1.158 | 0.918 | 0.110 | 0.454 | 0.033 | 0.037 | 2.166 |
| 2020 | tails only | 0.922 | 0.432 | 0.776 | 0.789 | 0.036 | 0.295 | 0.031 | 0.082 | 1.630 |
| 2020 | tails + special shift | 0.929 | 0.255 | 0.520 | 0.393 | 0.026 | 0.228 | 0.009 | 0.082 | 1.104 |
| 2020 | tails + special + poll (as selected) | 0.926 | 0.195 | 0.362 | 0.352 | 0.000 | 0.083 | 0.031 | 0.070 | 0.819 |
| 2020 | v3 | 0.933 | 0.204 | 0.386 | 0.372 | 0.000 | 0.085 | 0.030 | 0.067 | 0.872 |
| 2020 | v3 + advance split (not adopted) | 0.917 | 0.198 | 0.391 | 0.362 | 0.000 | 0.108 | 0.030 | 0.078 | 0.871 |
| 2023 | v1 | 0.895 | 0.560 | 1.212 | 0.708 | 0.198 | 0.642 | 0.028 | 0.083 | 1.826 |
| 2023 | tails only | 0.864 | 0.321 | 0.621 | 0.553 | 0.150 | 0.887 | 0.017 | 0.159 | 1.429 |
| 2023 | tails + special shift | 0.903 | 0.364 | 0.701 | 0.766 | 0.185 | 1.686 | 0.042 | 0.099 | 2.277 |
| 2023 | tails + special + poll (as selected) | 0.907 | 0.269 | 0.442 | 0.682 | 0.003 | 1.375 | 0.028 | 0.094 | 1.765 |
| 2023 | v3 | 0.921 | 0.240 | 0.394 | 0.590 | 0.004 | 1.205 | 0.012 | 0.090 | 1.404 |
| 2023 | v3 + advance split (not adopted) | 0.909 | 0.276 | 0.465 | 0.600 | 0.009 | 1.283 | 0.022 | 0.096 | 1.533 |

### 2017 train: v1 vs v3 by share of voting places counted (mean over the six count orders)

| variant | stage | cover | brier | ll | brier_nl | brier_most | ll_margin | pit_lo | pit_hi | worst_ll |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 0.050 | 0.841 | 0.599 | 1.482 | 0.975 | 0.310 | 2.951 | 0.000 | 0.159 | 2.802 |
| v1 | 0.250 | 0.845 | 0.571 | 1.351 | 0.962 | 0.342 | 2.867 | 0.000 | 0.155 | 2.641 |
| v1 | 0.500 | 0.939 | 0.621 | 1.418 | 0.927 | 0.292 | 2.675 | 0.000 | 0.072 | 2.415 |
| v1 | 0.750 | 1.000 | 0.599 | 1.306 | 0.872 | 0.224 | 2.411 | 0.000 | 0.033 | 2.121 |
| v1 | 0.950 | 1.000 | 0.410 | 0.803 | 0.671 | 0.064 | 1.715 | 0.000 | 0.000 | 1.365 |
| v1 | 1.000 | 0.800 | 0.092 | 0.164 | 0.070 | 0.000 | 0.875 | 0.200 | 0.000 | 0.513 |
| v3 | 0.050 | 0.800 | 0.275 | 0.499 | 0.494 | 0.091 | 1.217 | 0.000 | 0.200 | 1.016 |
| v3 | 0.250 | 0.800 | 0.255 | 0.453 | 0.417 | 0.077 | 1.106 | 0.000 | 0.200 | 0.946 |
| v3 | 0.500 | 0.933 | 0.196 | 0.347 | 0.283 | 0.024 | 0.826 | 0.000 | 0.100 | 0.834 |
| v3 | 0.750 | 1.000 | 0.126 | 0.225 | 0.186 | 0.000 | 0.423 | 0.000 | 0.000 | 0.586 |
| v3 | 0.950 | 1.000 | 0.064 | 0.147 | 0.125 | 0.000 | 0.183 | 0.000 | 0.000 | 0.393 |
| v3 | 1.000 | 1.000 | 0.049 | 0.132 | 0.100 | 0.000 | 0.170 | 0.000 | 0.000 | 0.327 |
| v3 + advance split (not adopted) | 0.050 | 0.800 | 0.275 | 0.499 | 0.494 | 0.091 | 1.217 | 0.000 | 0.200 | 1.016 |
| v3 + advance split (not adopted) | 0.250 | 0.800 | 0.255 | 0.453 | 0.417 | 0.077 | 1.106 | 0.000 | 0.200 | 0.946 |
| v3 + advance split (not adopted) | 0.500 | 0.933 | 0.196 | 0.347 | 0.283 | 0.024 | 0.826 | 0.000 | 0.100 | 0.834 |
| v3 + advance split (not adopted) | 0.750 | 1.000 | 0.126 | 0.225 | 0.186 | 0.000 | 0.423 | 0.000 | 0.000 | 0.586 |
| v3 + advance split (not adopted) | 0.950 | 1.000 | 0.064 | 0.147 | 0.125 | 0.000 | 0.183 | 0.000 | 0.000 | 0.393 |
| v3 + advance split (not adopted) | 1.000 | 1.000 | 0.049 | 0.132 | 0.100 | 0.000 | 0.170 | 0.000 | 0.000 | 0.327 |

### 2017 train: night order only (advance places first, then election day)

| variant | stage | cover | brier | ll | brier_nl | brier_most | ll_margin | pit_lo | pit_hi | worst_ll |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 0.050 | 0.833 | 0.628 | 1.530 | 0.968 | 0.356 | 2.961 | 0.000 | 0.167 | 2.713 |
| v1 | 0.250 | 0.857 | 0.530 | 1.255 | 0.961 | 0.373 | 2.894 | 0.000 | 0.143 | 2.661 |
| v1 | 0.500 | 0.944 | 0.651 | 1.477 | 0.924 | 0.305 | 2.650 | 0.000 | 0.122 | 2.389 |
| v1 | 0.750 | 1.000 | 0.593 | 1.293 | 0.864 | 0.232 | 2.358 | 0.000 | 0.067 | 2.102 |
| v1 | 0.950 | 1.000 | 0.407 | 0.798 | 0.671 | 0.066 | 1.728 | 0.000 | 0.000 | 1.367 |
| v1 | 1.000 | 0.800 | 0.092 | 0.164 | 0.070 | 0.000 | 0.875 | 0.200 | 0.000 | 0.513 |
| v3 | 0.050 | 0.800 | 0.254 | 0.478 | 0.538 | 0.117 | 1.292 | 0.000 | 0.200 | 1.084 |
| v3 | 0.250 | 0.800 | 0.177 | 0.352 | 0.366 | 0.036 | 1.009 | 0.000 | 0.200 | 0.773 |
| v3 | 0.500 | 0.933 | 0.097 | 0.217 | 0.214 | 0.005 | 0.666 | 0.000 | 0.133 | 0.559 |
| v3 | 0.750 | 1.000 | 0.072 | 0.158 | 0.155 | 0.000 | 0.354 | 0.000 | 0.000 | 0.481 |
| v3 | 0.950 | 1.000 | 0.055 | 0.134 | 0.119 | 0.000 | 0.175 | 0.000 | 0.000 | 0.398 |
| v3 | 1.000 | 1.000 | 0.049 | 0.132 | 0.100 | 0.000 | 0.170 | 0.000 | 0.000 | 0.327 |
| v3 + advance split (not adopted) | 0.050 | 0.800 | 0.254 | 0.478 | 0.538 | 0.117 | 1.292 | 0.000 | 0.200 | 1.084 |
| v3 + advance split (not adopted) | 0.250 | 0.800 | 0.177 | 0.352 | 0.366 | 0.036 | 1.009 | 0.000 | 0.200 | 0.773 |
| v3 + advance split (not adopted) | 0.500 | 0.933 | 0.097 | 0.217 | 0.214 | 0.005 | 0.666 | 0.000 | 0.133 | 0.559 |
| v3 + advance split (not adopted) | 0.750 | 1.000 | 0.072 | 0.158 | 0.155 | 0.000 | 0.354 | 0.000 | 0.000 | 0.481 |
| v3 + advance split (not adopted) | 0.950 | 1.000 | 0.055 | 0.134 | 0.119 | 0.000 | 0.175 | 0.000 | 0.000 | 0.398 |
| v3 + advance split (not adopted) | 1.000 | 1.000 | 0.049 | 0.132 | 0.100 | 0.000 | 0.170 | 0.000 | 0.000 | 0.327 |

### 2020 train: v1 vs v3 by share of voting places counted (mean over the six count orders)

| variant | stage | cover | brier | ll | brier_nl | brier_most | ll_margin | pit_lo | pit_hi | worst_ll |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 0.050 | 0.976 | 0.612 | 1.508 | 0.964 | 0.317 | 0.786 | 0.000 | 0.024 | 3.000 |
| v1 | 0.250 | 1.000 | 0.598 | 1.438 | 0.954 | 0.185 | 0.639 | 0.000 | 0.000 | 2.559 |
| v1 | 0.500 | 1.000 | 0.603 | 1.412 | 0.925 | 0.115 | 0.551 | 0.000 | 0.000 | 2.408 |
| v1 | 0.750 | 1.000 | 0.545 | 1.179 | 0.852 | 0.045 | 0.487 | 0.000 | 0.000 | 2.034 |
| v1 | 0.950 | 1.000 | 0.456 | 0.839 | 0.674 | 0.000 | 0.262 | 0.000 | 0.000 | 1.389 |
| v1 | 1.000 | 0.600 | 0.456 | 0.569 | 1.140 | 0.000 | 0.000 | 0.200 | 0.200 | 1.605 |
| v3 | 0.050 | 0.756 | 0.475 | 0.905 | 0.787 | 0.000 | 0.371 | 0.056 | 0.189 | 1.818 |
| v3 | 0.250 | 0.911 | 0.320 | 0.564 | 0.555 | 0.000 | 0.095 | 0.056 | 0.144 | 1.083 |
| v3 | 0.500 | 0.933 | 0.241 | 0.449 | 0.432 | 0.000 | 0.042 | 0.067 | 0.067 | 1.022 |
| v3 | 0.750 | 1.000 | 0.114 | 0.199 | 0.275 | 0.000 | 0.002 | 0.000 | 0.000 | 0.640 |
| v3 | 0.950 | 1.000 | 0.048 | 0.113 | 0.117 | 0.000 | 0.001 | 0.000 | 0.000 | 0.388 |
| v3 | 1.000 | 1.000 | 0.027 | 0.087 | 0.066 | 0.000 | 0.001 | 0.000 | 0.000 | 0.281 |
| v3 + advance split (not adopted) | 0.050 | 0.722 | 0.477 | 0.977 | 0.807 | 0.002 | 0.511 | 0.056 | 0.222 | 1.959 |
| v3 + advance split (not adopted) | 0.250 | 0.856 | 0.307 | 0.548 | 0.532 | 0.000 | 0.092 | 0.056 | 0.156 | 1.044 |
| v3 + advance split (not adopted) | 0.500 | 0.922 | 0.225 | 0.430 | 0.395 | 0.000 | 0.042 | 0.067 | 0.089 | 0.947 |
| v3 + advance split (not adopted) | 0.750 | 1.000 | 0.108 | 0.190 | 0.261 | 0.000 | 0.002 | 0.000 | 0.000 | 0.612 |
| v3 + advance split (not adopted) | 0.950 | 1.000 | 0.047 | 0.112 | 0.114 | 0.000 | 0.001 | 0.000 | 0.000 | 0.383 |
| v3 + advance split (not adopted) | 1.000 | 1.000 | 0.027 | 0.087 | 0.066 | 0.000 | 0.001 | 0.000 | 0.000 | 0.281 |

### 2020 train: night order only (advance places first, then election day)

| variant | stage | cover | brier | ll | brier_nl | brier_most | ll_margin | pit_lo | pit_hi | worst_ll |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 0.050 | 1.000 | 0.577 | 1.406 | 0.959 | 0.216 | 0.638 | 0.000 | 0.000 | 2.693 |
| v1 | 0.250 | 1.000 | 0.630 | 1.500 | 0.961 | 0.120 | 0.528 | 0.000 | 0.000 | 2.502 |
| v1 | 0.500 | 1.000 | 0.599 | 1.393 | 0.941 | 0.074 | 0.468 | 0.000 | 0.000 | 2.411 |
| v1 | 0.750 | 1.000 | 0.599 | 1.281 | 0.859 | 0.030 | 0.434 | 0.000 | 0.000 | 2.013 |
| v1 | 0.950 | 1.000 | 0.456 | 0.837 | 0.671 | 0.000 | 0.246 | 0.000 | 0.000 | 1.367 |
| v1 | 1.000 | 0.600 | 0.456 | 0.569 | 1.140 | 0.000 | 0.000 | 0.200 | 0.200 | 1.605 |
| v3 | 0.050 | 0.800 | 0.349 | 0.596 | 0.497 | 0.000 | 0.075 | 0.000 | 0.200 | 1.121 |
| v3 | 0.250 | 0.800 | 0.144 | 0.315 | 0.186 | 0.000 | 0.013 | 0.000 | 0.200 | 0.557 |
| v3 | 0.500 | 1.000 | 0.095 | 0.218 | 0.081 | 0.000 | 0.003 | 0.000 | 0.000 | 0.501 |
| v3 | 0.750 | 1.000 | 0.019 | 0.083 | 0.022 | 0.000 | 0.001 | 0.000 | 0.000 | 0.176 |
| v3 | 0.950 | 1.000 | 0.018 | 0.074 | 0.042 | 0.000 | 0.001 | 0.000 | 0.000 | 0.220 |
| v3 | 1.000 | 1.000 | 0.027 | 0.087 | 0.066 | 0.000 | 0.001 | 0.000 | 0.000 | 0.281 |
| v3 + advance split (not adopted) | 0.050 | 0.800 | 0.341 | 0.588 | 0.528 | 0.000 | 0.087 | 0.000 | 0.200 | 1.051 |
| v3 + advance split (not adopted) | 0.250 | 0.800 | 0.132 | 0.303 | 0.184 | 0.000 | 0.013 | 0.000 | 0.200 | 0.518 |
| v3 + advance split (not adopted) | 0.500 | 1.000 | 0.087 | 0.209 | 0.081 | 0.000 | 0.003 | 0.000 | 0.000 | 0.456 |
| v3 + advance split (not adopted) | 0.750 | 1.000 | 0.018 | 0.082 | 0.024 | 0.000 | 0.001 | 0.000 | 0.000 | 0.170 |
| v3 + advance split (not adopted) | 0.950 | 1.000 | 0.019 | 0.075 | 0.044 | 0.000 | 0.001 | 0.000 | 0.000 | 0.225 |
| v3 + advance split (not adopted) | 1.000 | 1.000 | 0.027 | 0.087 | 0.066 | 0.000 | 0.001 | 0.000 | 0.000 | 0.281 |

### 2023 TEST: v1 vs v3 by share of voting places counted (mean over the six count orders)

| variant | stage | cover | brier | ll | brier_nl | brier_most | ll_margin | pit_lo | pit_hi | worst_ll |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 0.050 | 1.000 | 0.829 | 1.860 | 0.940 | 0.527 | 0.996 | 0.000 | 0.000 | 2.715 |
| v1 | 0.250 | 1.000 | 0.759 | 1.732 | 0.911 | 0.355 | 0.832 | 0.000 | 0.000 | 2.485 |
| v1 | 0.500 | 1.000 | 0.701 | 1.534 | 0.835 | 0.201 | 0.742 | 0.000 | 0.000 | 2.139 |
| v1 | 0.750 | 1.000 | 0.617 | 1.293 | 0.728 | 0.100 | 0.700 | 0.000 | 0.000 | 1.825 |
| v1 | 0.950 | 0.870 | 0.349 | 0.687 | 0.516 | 0.005 | 0.577 | 0.000 | 0.167 | 1.125 |
| v1 | 1.000 | 0.500 | 0.107 | 0.168 | 0.320 | 0.000 | 0.003 | 0.167 | 0.333 | 0.665 |
| v3 | 0.050 | 0.843 | 0.351 | 0.603 | 0.531 | 0.005 | 0.871 | 0.000 | 0.167 | 1.225 |
| v3 | 0.250 | 0.870 | 0.268 | 0.489 | 0.634 | 0.013 | 1.256 | 0.009 | 0.148 | 1.348 |
| v3 | 0.500 | 0.898 | 0.242 | 0.431 | 0.660 | 0.005 | 1.515 | 0.028 | 0.157 | 1.526 |
| v3 | 0.750 | 0.944 | 0.222 | 0.354 | 0.658 | 0.000 | 1.612 | 0.028 | 0.037 | 1.662 |
| v3 | 0.950 | 0.972 | 0.197 | 0.274 | 0.589 | 0.000 | 1.226 | 0.009 | 0.028 | 1.505 |
| v3 | 1.000 | 1.000 | 0.158 | 0.213 | 0.470 | 0.000 | 0.750 | 0.000 | 0.000 | 1.157 |
| v3 + advance split (not adopted) | 0.050 | 0.833 | 0.424 | 0.717 | 0.552 | 0.005 | 0.912 | 0.000 | 0.167 | 1.573 |
| v3 + advance split (not adopted) | 0.250 | 0.861 | 0.308 | 0.550 | 0.656 | 0.019 | 1.300 | 0.019 | 0.167 | 1.428 |
| v3 + advance split (not adopted) | 0.500 | 0.843 | 0.306 | 0.613 | 0.654 | 0.028 | 1.759 | 0.056 | 0.167 | 1.793 |
| v3 + advance split (not adopted) | 0.750 | 0.944 | 0.262 | 0.425 | 0.681 | 0.000 | 1.737 | 0.037 | 0.046 | 1.744 |
| v3 + advance split (not adopted) | 0.950 | 0.972 | 0.196 | 0.275 | 0.585 | 0.000 | 1.238 | 0.019 | 0.028 | 1.503 |
| v3 + advance split (not adopted) | 1.000 | 1.000 | 0.158 | 0.213 | 0.470 | 0.000 | 0.750 | 0.000 | 0.000 | 1.157 |

### 2023 TEST: night order only (advance places first, then election day)

| variant | stage | cover | brier | ll | brier_nl | brier_most | ll_margin | pit_lo | pit_hi | worst_ll |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 0.050 | 1.000 | 0.807 | 1.831 | 0.921 | 0.356 | 0.767 | 0.000 | 0.000 | 2.691 |
| v1 | 0.250 | 1.000 | 0.749 | 1.706 | 0.904 | 0.235 | 0.668 | 0.000 | 0.000 | 2.419 |
| v1 | 0.500 | 1.000 | 0.766 | 1.664 | 0.837 | 0.140 | 0.636 | 0.000 | 0.000 | 2.163 |
| v1 | 0.750 | 1.000 | 0.613 | 1.273 | 0.682 | 0.073 | 0.623 | 0.000 | 0.000 | 1.799 |
| v1 | 0.950 | 0.833 | 0.337 | 0.666 | 0.483 | 0.003 | 0.531 | 0.000 | 0.167 | 1.116 |
| v1 | 1.000 | 0.500 | 0.107 | 0.168 | 0.320 | 0.000 | 0.003 | 0.167 | 0.333 | 0.665 |
| v3 | 0.050 | 0.833 | 0.248 | 0.486 | 0.490 | 0.001 | 0.437 | 0.000 | 0.167 | 1.010 |
| v3 | 0.250 | 0.889 | 0.181 | 0.354 | 0.468 | 0.000 | 0.363 | 0.000 | 0.167 | 0.838 |
| v3 | 0.500 | 1.000 | 0.137 | 0.254 | 0.383 | 0.000 | 0.372 | 0.000 | 0.111 | 0.695 |
| v3 | 0.750 | 1.000 | 0.116 | 0.187 | 0.343 | 0.000 | 0.450 | 0.000 | 0.000 | 0.839 |
| v3 | 0.950 | 1.000 | 0.157 | 0.212 | 0.470 | 0.000 | 0.704 | 0.000 | 0.000 | 1.158 |
| v3 | 1.000 | 1.000 | 0.158 | 0.213 | 0.470 | 0.000 | 0.750 | 0.000 | 0.000 | 1.157 |
| v3 + advance split (not adopted) | 0.050 | 0.833 | 0.282 | 0.529 | 0.490 | 0.002 | 0.521 | 0.000 | 0.167 | 0.953 |
| v3 + advance split (not adopted) | 0.250 | 0.833 | 0.197 | 0.384 | 0.484 | 0.000 | 0.360 | 0.000 | 0.167 | 0.861 |
| v3 + advance split (not adopted) | 0.500 | 0.944 | 0.140 | 0.258 | 0.396 | 0.000 | 0.350 | 0.000 | 0.167 | 0.689 |
| v3 + advance split (not adopted) | 0.750 | 1.000 | 0.115 | 0.184 | 0.341 | 0.000 | 0.439 | 0.000 | 0.000 | 0.830 |
| v3 + advance split (not adopted) | 0.950 | 1.000 | 0.153 | 0.207 | 0.458 | 0.000 | 0.675 | 0.000 | 0.000 | 1.129 |
| v3 + advance split (not adopted) | 1.000 | 1.000 | 0.158 | 0.213 | 0.470 | 0.000 | 0.750 | 0.000 | 0.000 | 1.157 |

### 2023 TEST: before / at full ordinary count (special votes still to come)

| variant | part | cover | brier | ll | brier_nl | brier_most | ll_margin | pit_lo | pit_hi | worst_ll |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 100% of ordinary places | 0.500 | 0.107 | 0.168 | 0.320 | 0.000 | 0.003 | 0.167 | 0.333 | 0.665 |
| v1 | 5%-95% | 0.974 | 0.651 | 1.421 | 0.786 | 0.238 | 0.769 | 0.000 | 0.033 | 2.058 |
| tails only | 100% of ordinary places | 0.833 | 0.120 | 0.211 | 0.322 | 0.000 | 0.094 | 0.000 | 0.167 | 0.594 |
| tails only | 5%-95% | 0.870 | 0.361 | 0.703 | 0.599 | 0.180 | 1.046 | 0.020 | 0.157 | 1.596 |
| tails + special shift | 100% of ordinary places | 1.000 | 0.255 | 0.352 | 0.764 | 0.000 | 0.840 | 0.000 | 0.000 | 2.070 |
| tails + special shift | 5%-95% | 0.883 | 0.385 | 0.771 | 0.766 | 0.222 | 1.855 | 0.050 | 0.119 | 2.318 |
| tails + special + poll (as selected) | 100% of ordinary places | 1.000 | 0.249 | 0.340 | 0.744 | 0.000 | 0.828 | 0.000 | 0.000 | 1.983 |
| tails + special + poll (as selected) | 5%-95% | 0.889 | 0.273 | 0.462 | 0.670 | 0.004 | 1.484 | 0.033 | 0.113 | 1.721 |
| v3 | 100% of ordinary places | 1.000 | 0.158 | 0.213 | 0.470 | 0.000 | 0.750 | 0.000 | 0.000 | 1.157 |
| v3 | 5%-95% | 0.906 | 0.256 | 0.430 | 0.614 | 0.004 | 1.296 | 0.015 | 0.107 | 1.453 |
| v3 + advance split (not adopted) | 100% of ordinary places | 1.000 | 0.158 | 0.213 | 0.470 | 0.000 | 0.750 | 0.000 | 0.000 | 1.157 |
| v3 + advance split (not adopted) | 5%-95% | 0.891 | 0.299 | 0.516 | 0.626 | 0.010 | 1.389 | 0.026 | 0.115 | 1.608 |

## Live rehearsal

`analysis/nz_rehearsal.py build` writes a recorder-format archive in which the 2023 night is replayed through the 2026
official XML feed format (real 2026 reference files; advance places first at 06:04-06:14 UTC, election-day places to
09:40 UTC; 2023 results mapped by `CROSSWALK` to the 71 electorates). Running the production command on it,

```bash
uv run brunder nz live configs/nz2026_v3.toml --out <dir containing a copy of site/data/nz2026> --snap <archive> --interval 5 --until <time>
```

processes the whole night and rewrites `meta.json`, `timeline.json`, `timeline_tail.json`, `units.json` without errors. Because the
wall clock is before the real poll close, meta.json is labelled `pre-count`.

## Published retrospective replays (`nz2023`, `nz2020`)

`uv run python analysis/nz_export_replay.py 2023` (and `2020`) writes `site/data/nz<year>/` (about 1.3 MB each) and the dashboard
lists them as **retrospective, not pre-registered** replays. Each is the v3 model (`configs/nz2026_v3.toml` settings) run minute by
minute over the official voting-place results of that night.

* **Simulated count order.** The Commission keeps no timestamped count archive, so the clock is simulated: `nz_replay.Order(..., "night")`
  (seed 1) with `nz_rehearsal.release_minutes` (every advance place 4-14 minutes after the 19:00 close, election-day places to
  minute 225). The page title, notes and `elections.json` entry say so; nothing on it measures speed against the real night.
* **Settings source.** v3 settings were chosen on the 2017 and 2020 nights only. **2023 is out of sample** (poll prior = polls ending in
  the 21 days before 2023-10-14; special-vote shift = mean of 2017 and 2020; prior = 2020 electorates). **2020 is in sample**
  (evidence label `in-sample`; shift from 2017 only; prior = 2017 electorates). Truth is the FINAL official result, special votes included.
* **No market.** A Polymarket search (events closed before 2025, queries "new zealand 2023", "Luxon", "Hipkins", "new zealand election")
  and the Kalshi `KXNEWZEALANDPARLI` series returned no market on either election, so the pages are model only (evidence label
  `model-only` for 2023); the market columns are empty. The market list is the 2026 family evaluated on the replayed night.
* **No map.** The 2023 electorate boundaries are not in the repo (the geometry is the 2025 map), so `geometry` is `null`.
* **Licences.** Official results: Crown copyright, reproduction permitted with acknowledgement; raw files stay in `~/nz_v3_local`,
  only derived model series are published. Polls: Wikipedia, CC BY-SA.

Headline (2023, 46 markets, simulated clock): the poll-tilted model already gave National the most seats 94.8% before any count and
99.5% once 39% of places were in, 99.98% at the end of the night (final: National 48, Labour 34, Green 15, ACT 11, NZ First 8,
Te Pāti Māori 6). The mean Brier score over the 46 outcomes was 0.028 at 5% of places, 0.021 at 50%, 0.019 at 75% and 0.032 at 100% of
ordinary places (the last rise is the special-vote shift: the night count excludes special votes the final result includes).
Run `nz_export_replay.py` for the full table per stage.

## Pre-registration checks (2026-10-31)

Run before any 2026 count, on the unregistered v3 config; nothing in config or model code was changed
(`analysis/nz_v3_check.py 2023`; 2023 is out of sample: settings from 2017/2020, special shift from 2017+2020, poll = 2023 polls).

**(A) Prior mismatch: none found.** 2017, 2020 and 2023 all use the same construction as 2026: previous election's electorates tilted to
the mean of the polls ending in the final 21 days (`nz_backtest.poll_shares` → `tilt_to_poll`, shifted to the night scale by the special
shift), poll weight fading with `prior_votes`, error `count_log_sd = 0.12` at nothing counted (pooled historical poll RMS 0.112). The
2026 block has 4 polls (refresh will add more); the training windows had 8 (2017), 4 (2020) and 9 (2023), so the width is inside the tested
range. Re-running 2023 with a 2026-style as-of prior (only polls ending at least 12 days before election day) leaves the results unchanged.

**(B) Party vote shares vs party seats, 2023 test** (90% intervals, parties with >= 3% of the final vote for shares, all seat-winning parties
for seats; six count orders x 3 seeds x 2000 draws; share cover has only about 6 parties per cell, so it is noisy):

| places counted | share cover | seat cover | seat-bracket Brier | bracket log loss | most-seats Brier |
|---|---|---|---|---|---|
| 5% | 0.972 | 0.833 | 0.350 | 0.604 | 0.005 |
| 25% | 0.926 | 0.870 | 0.266 | 0.487 | 0.012 |
| 50% | 0.861 | 0.907 | 0.239 | 0.427 | 0.004 |
| 75% | 0.861 | 0.954 | 0.219 | 0.346 | 0.000 |
| 95% | 0.917 | 0.981 | 0.191 | 0.266 | 0.000 |
| 100% | 1.000 | 1.000 | 0.159 | 0.213 | 0.000 |
| all stages | 0.923 | 0.924 | 0.237 | 0.391 | 0.004 |

2026-style prior (as-of 12 days): share 0.931, seat 0.924, Brier 0.238, log loss 0.390, most-seats 0.002. Night order only: seat cover
0.83 / 0.89 at 5% / 25%, 1.0 from 50%. Seat coverage is at or above 0.85 from 25% counted, and 0.83 at the 5% stage; the overall figure
is above the 0.85 threshold, so v3 is left as drafted.
