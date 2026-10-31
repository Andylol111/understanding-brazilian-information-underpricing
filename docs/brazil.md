# Brazil retrospective sensitivity

The registered v1 replays remain the headline results. The v2 replays below are a retrospective
sensitivity: their settings were chosen after every replayed result was known, so they are not a new
pre-specified test.

## Calibration rule (written before the calibration was run)

For a 2022 target, use the four earlier presidential count nights in 2014 and 2018. For a 2026
target, add the two 2022 presidential count nights. Each night has equal weight. The prior for each
night is the most recent comparable result available before that night (the previous presidential
round for a runoff, otherwise the previous first round), reduced to the two candidates or party-side
proxies named in `analysis/br_calibrate.py`.

The 2022 BU-web files contain `DT_BU_RECEBIDO`, so their real TSE receipt order is replayed once per
minute. The official 2014 and 2018 section files contain final votes but no receipt timestamp (2018's
`DT_ENCERRAMENTO` is when a ballot box closed, not when TSE published it). The Internet Archive CDX
index has no captures of the documented 2018 national `dadosdivweb` JSON for election codes 295 or
296. Those four nights therefore use the project's existing simulated-order convention: four fixed
random draws, polling sections ordered from smaller to larger with independent Normal(0, 0.7) noise
on log valid votes, evaluated at 1, 2, 3, 5, 7.5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90 and 95%
of sections. Simulated frames within a night share that night's weight.

The grid is the existing Brazil tau grid
`{0, 0.0025, 0.005, 0.01, 0.015, 0.02, 0.03}`, crossed with the existing municipal-model grids
`kappa in {10,000, 100,000}` and `rho in {0.5, 0.8}`; the dispersion priors keep their code defaults.
At each grid point, compute the national final-share z-score and absolute projection error at frames
with more than 1% and at most 95% counted. Choose the smallest tau whose equally weighted pooled 90%
coverage is at least 0.90 and whose worst single-night coverage is at least 0.75. At that tau choose
the `(kappa, rho)` pair with the lowest equally weighted mean absolute projection error. If no tau
qualifies, use the largest grid value and disclose the miss. This rule is fixed here before looking
at the calibration results.

## Calibration result

The fixed rule qualified on the existing grid in both cases:

| target | earlier nights | tau | kappa | rho | pooled coverage | worst night | mean MAE |
|---|---|---:|---:|---:|---:|---:|---:|
| 2022 rounds | 2014 R1/R2, 2018 R1/R2 | 0.0025 | 100,000 | 0.5 | 99.6% | 98.3% | 0.42 pt |
| 2026 rounds and governors | those four plus 2022 R1/R2 | 0.005 | 100,000 | 0.5 | 100% | 100% | 0.36 pt |

These unusually high calibration coverages include four simulated nights and should not be read as
six independent demonstrations of live calibration. The settings are recorded in new v2 configs;
the registered v1 configs are unchanged.

## Replay results

Metrics use the minutes with more than 1% and at most 95% counted. Early error is the absolute
projection error in points at the first minute reaching 1 / 5 / 10 / 25 / 50% counted.

| race | version | 90% coverage | MAE (pt) | early error (pt) | Brier |
|---|---|---:|---:|---|---:|
| 2022 R1 president | v1 | 100.0% | 0.37 | 2.00 / 0.41 / 0.24 / 0.23 / 0.25 | 0.0008 |
| 2022 R1 president | v2 | 100.0% | 0.29 | 1.75 / 0.16 / 0.04 / 0.26 / 0.17 | 0.0008 |
| 2022 R2 president | v1 | 100.0% | 0.22 | 0.76 / 0.36 / 0.00 / 0.19 / 0.31 | 0.0373 |
| 2022 R2 president | v2 | 71.5% | 0.21 | 0.58 / 0.24 / 0.11 / 0.25 / 0.30 | 0.0010 |
| 2026 R1 president | v1 | 93.9% | 0.47 | 1.36 / 0.22 / 0.54 / 0.80 / 0.61 | 0.0228 |
| 2026 R1 president | v2 | 27.8% | 0.49 | 1.44 / 0.12 / 0.60 / 0.86 / 0.65 | 0.0216 |
| 2026 R2 president | v1 | 100.0% | 0.34 | 1.20 / 1.10 / 0.07 / 0.00 / 0.00 | 0.0025 |
| 2026 R2 president | v2 | 100.0% | 0.31 | 1.15 / 0.88 / 0.08 / 0.19 / 0.01 | 0.0000 |
| 2026 R2 governor, AC | v1 | 100.0% | 0.58 | 0.25 / 1.72 / 1.31 / 0.80 / 1.02 | 0.0000 |
| 2026 R2 governor, AC | v2 | 100.0% | 0.98 | 2.45 / 1.95 / 1.24 / 0.77 / 0.61 | 0.0000 |
| 2026 R2 governor, AM | v1 | 99.6% | 3.28 | 4.51 / 3.90 / 3.91 / 3.53 / 2.34 | 0.0000 |
| 2026 R2 governor, AM | v2 | 100.0% | 3.12 | 4.53 / 3.44 / 3.37 / 3.12 / 2.20 | 0.0000 |
| 2026 R2 governor, DF | v1 | 100.0% | 0.63 | 0.80 / 0.60 / 0.60 / 0.51 / 0.42 | 0.0179 |
| 2026 R2 governor, DF | v2 | 100.0% | 0.63 | 0.80 / 0.60 / 0.60 / 0.50 / 0.36 | 0.0196 |
| 2026 R2 governor, ES | v1 | 95.4% | 0.61 | 1.66 / 1.01 / 1.23 / 0.20 / 0.17 | 0.0049 |
| 2026 R2 governor, ES | v2 | 87.7% | 1.01 | 2.84 / 2.49 / 2.17 / 1.01 / 0.35 | 0.0017 |
| 2026 R2 governor, RN | v1 | 100.0% | 0.42 | 2.37 / 0.45 / 0.02 / 0.54 / 0.04 | 0.1076 |
| 2026 R2 governor, RN | v2 | 100.0% | 0.43 | 2.32 / 0.06 / 0.55 / 0.29 / 0.20 | 0.0813 |
| 2026 R2 governor, TO | v1 | 100.0% | 0.13 | 0.52 / 0.52 / 0.07 / 0.17 / 0.29 | 0.0000 |
| 2026 R2 governor, TO | v2 | 100.0% | 0.17 | 0.60 / 0.60 / 0.10 / 0.02 / 0.19 | 0.0000 |

The narrower v2 band failed badly in the 2026 first round (27.8% coverage), and also undercovered
the 2022 runoff and Espírito Santo governor race. The selection-night coverage therefore did not
transfer to every target night.

Minutes after the 1% counted gate until P or price stayed on the eventual winner (time before the
gate is shown as 0). "Decided before count" means Polymarket's first price before 1% counted was
already at least 0.90 on the eventual winner.

| race | decided before count | v1 90/95 | v2 90/95 | market 90/95 | v2 lead 90/95 |
|---|---|---:|---:|---:|---:|
| 2026 R1 president | no | 51 / 63 | 25 / 27 | 70 / 97 | 45 / 70 |
| 2026 R2 president | no | 0 / 65 | 0 / 0 | 80 / 98 | 80 / 98 |
| AC governor | yes | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| AM governor | yes | 0 / 0 | 0 / 0 | 0 / 160 | 0 / 160 |
| DF governor | yes | 136 / 144 | 134 / 144 | 0 / 0 | -134 / -144 |
| ES governor | no | 26 / 31 | 3 / 11 | 68 / 70 | 65 / 59 |
| RN governor | no | 86 / 91 | 84 / 88 | 107 / 108 | 23 / 20 |
| TO governor | yes | 0 / 0 | 0 / 0 | 0 / 9 | 0 / 9 |

For races decided before the count, a model lead is not evidence of later market learning. The 2022
rounds have no surviving market prices.

## Data and licence

The final section and municipality results and the 2022 receipt timestamps come from the TSE Open
Data Portal. Its 2014 and 2018 result datasets are labelled Creative Commons Attribution; the same
official result resources are reused here with attribution. The 2014/2018 simulated orders are a
methodological assumption, not recovered election-night histories. Market prices are from
Polymarket's public API and are shown only for research commentary.
