# US midterms 2026: audit of the registered and deployed model (2026-10-31, before any 2026 count)

Read-only audit of `origin/main` @ a9c860a. No registered or frozen file was touched; nothing here is a model change. Method:
every `configs/us2026*` file parsed and cross-checked (273 files, 110 races incl. 75 House districts and the civicAPI set), SHA-256 of
each config against `prereg/registry.jsonl`, `configs/pairs/us2026.toml` (113 pairs), `site/data/us2026/*` (precount timeline,
overview, units, House markets), polls re-fetched from VoteHub on 2026-10-31 and run through `brunder.us.poll_rows/poll_average`,
unit lists against `site/data/geo/us_counties.topo.json`, candidate names spot-checked against Ballotpedia/AP/NBC (AZ-01 Shah–Feely,
AZ-05 Lamb, FL-02 Rogers, FL-Sen Moody–Nixon, CO-Gov Weiser–Marx, MI-Sen El-Sayed–Rogers, CA-Gov Becerra–Hilton, GA-Gov Bottoms–Jackson).

## Findings, ranked

### 1. HIGH – House v1/v2: no candidate/incumbent effect, and the swing prior rests on two polls
- Baseline P(D) = 2024 president on the 2026 lines + generic-ballot swing; candidates enter only through the count.
  Against Polymarket/Kalshi mid on the 75 configured districts: corr 0.81, mean |gap| 0.151; 8 districts gap ≤ −0.25, 27 districts ≥ +0.15.
- **PA-01 (model 0.863, market 0.472)**: the gap is *explained by data*, not a bug. Fitzpatrick (R) ran 6.6 pts ahead of president in 2024
  (D two-party 0.436 vs 0.502) and 9.7 pts in 2022 (0.451 vs 0.524 for the 2020 president); the model's σ(House−president)=0.029 is applied as noise, its mean as −0.001. Same pattern: TX-28 (Cuellar, 0.52 vs 0.93),
  TX-15 (0.23 vs 0.87), OH-07 (0.48 vs 0.86), NC-01 (0.48 vs 0.83), PA-08 (0.59 vs 0.87), OH-09 (0.50 vs 0.77). The opposite sign: FL/TX/NC/OH R seats where the market is 5–10 % and the model 20–40 %
  (FL-08 0.36 vs 0.05, TX-24 0.36 vs 0.06, TX-38 0.28 vs 0.05, NC-08 0.23 vs 0.05).
- Why it matters on the night: 5 districts have one county unit (AZ-01, AZ-08, FL-14, FL-27, TX-38) and 18 have ≤ 2, so the prior is never corrected by between-county information; for those P is prior-driven until ~all votes are in.
- The generic-ballot input (`markets.json generic_ballot`): `n_polls = 2`, last 2026-10-04, avg 0.5477 (D+9.5 two-party) for all 75 districts. VoteHub's generic-ballot feed has 4 polls since 09-18; two were dropped for lacking a sample size
  (HarrisX 51–49, The Argument 55–45), the two kept (YouGov/CBS 54–45, Pew 44–36 → both ≈0.55). The feed has nothing after 10-04. `swing_prior_sd` 0.052 is not wide enough to make this irrelevant before ~25 % counted.
- No Kalshi ticker in any House config (`ticker = ""`), NC-11 has no Kalshi market, and no House district is in `configs/pairs/us2026.toml`; any consumer that needs a pair entry for the Kalshi leg has none (markets.json carries Kalshi prices for 74/75).

### 2. HIGH – Statewide accuracy evidence is one state, simulated, and in-sample; bands are far too wide to test calibration
- Count-model settings (τ, ρ, σ_mun0, early_share, f_complete) were chosen on Georgia 2022/2024 certified results with simulated count orders (30–40 nights ≈ 2 races; nights are not independent). The same GA 2022 data
  then validate v3 ("`calib`": 90 % coverage 100 % at every checkpoint). `us_midterms.md` itself labels this in-sample. 11 of 21 state races use the GA *no-split* setting (τ 0.06, ρ 0.3) on feeds never replayed
  (SC, FL, MN, NE, NM, VT, IA/KS/MI civic …); CA τ=0.08 is "a judgment, not calibrated"; AZ is "as CA". The hisp-slope backtest reports 99–100 % pooled coverage at every checkpoint.
  Coverage of 99–100 % at a 90 % level means the bands are conservative; it cannot show P is calibrated and it makes "settle lead" a test of speed, not accuracy.
- Known directional bias that v3 does not remove: simulated GA nights give margin bias +3.6 pts toward the Democrat at 25–50 % counted (v3), +2.0 at 5 % (`us_model_analysis.md` §5). Applies wherever early/mail posts first (GA, NC, TX, PA).
- Polls: sd bins from 538 2016–2022 (192 races, 4 cycles; 11+ polls → 0.0228 from 57 races), polls overstate D by 1.4 pts (cycle means +1.9/−0.1/+3.5/+0.7), not corrected; common component sd 1.5 pts (ρ 0.21 from 4 cycles). Held-out 2022 check: 70 % within 1 sd, 92 % within 1.645 sd.
  Sensitivity: a −1.4 pt shift moves TX-Sen v3 P from 0.686 to ≈0.48 and OH-Gov v4 from 0.70 to ≈0.51 (Φ of the config swing-prior mean less 0.014 over the timeline sd). 
- The 11+-poll bin is applied to TX/FL/OH/IA/MI/ME (11–22 polls from VoteHub in a 45-day window), while 538's bin counted polls under a different window/pollster mix.

### 3. MED-HIGH – Largest statewide disagreements with markets (primary model vs mean Polymarket, 2026-10-30 22:00Z)
| race | model | market | explanation |
|---|---|---|---|
| VT-gov | 0.565 (v1 0.0001) | 0.236 | data: 2 polls in the window (UNH 09-21 Janoo +6; State Navigate 10-25 Scott +3). Scott +10 (Vermont Public/Seven Days) and the July UNH Scott +11/+15 are not in VoteHub's window. Config `swing_prior_mean` = **+0.279**, sd 0.043: a uniform +28-pt swing off Scott's 2024 landslide. Structural, not a code bug, but the model's P here is the least trustworthy. |
| NM-gov | 0.671 | 0.959 | no polls: v1 prior only (2022 gov) — v2 ≡ v1; `poll_model()` returns None so scoring falls back to v1 (NOTES say "NM governor: v2"). |
| AZ-gov | 0.693 | 0.930 | τ 0.08 (CA-style), f_max 0.90, 3–4 polls; sd_v3 0.060 vs 0.034 for GA. Wide by construction. |
| GA-gov | 0.425 | 0.655 | 8 polls avg 0.493 (AJC/UGA "dead heat"); market prices more D than the poll mean. |
| MN-sen | 0.723 | 0.915 | 3 polls, prior is the 2022 *governor* race (also used for MN-gov: identical v1 P 0.7118). |
| SC-sen | 0.454 | 0.275 | swing +0.124 off Tim Scott's 2022 landslide; 2 polls (3 in civic). |
| FL-sen | 0.186 | 0.095 | 10 polls 0.467, sd 0.037; the market is more R than the polls. |
| OH-gov | 0.702 | 0.810 | v4; feed blocked; prior is president 2024 × 0.72. |
Mean |gap| over 21 races 0.111, mean −0.031 (model below market). CA-gov and PA-gov P (0.999, 1.000) exceed the market (0.976, 0.987): poll lead 13–25 pts with an uncorrected pro-D poll bias.
None of the gaps looks like a mapping error: Polymarket and Kalshi agree to ≤ 2 pts everywhere, `yes_is` is `a` on every config and pair, and `same_side` is true.

### 4. MED – Senate control is not the race P
`brunder.us.senate_control` uses each race's **poll average and bin sd** (or, with no polls, 0.5 + Φ⁻¹(market)·0.039) with holdovers 34 (32 D + 2 I). It does not use v3/v4 P, and the live refresh keeps the pre-election poll mean all night
(`ov["offices"]["senate"]["races"][].polls`). Before the count it equals the v3 P for polled races (e.g. TX-Sen 0.686) — consistent; after the first returns it is not. One- and two-poll states drive it (MS-Sen avg 0.48 from 1 poll → P≈0.32 vs market 0.125; NM-Sen 1 poll; IL/RI 2).
Independent Osborn (NE) is counted in the D caucus. Current: control 0.771 (ρ 0.21) / 0.893 (indep.) vs Polymarket 0.705; summing race markets gives 52.4 expected D-caucus seats vs 52.6 from the polls rule.
Scoring (`us_score.senate_control`) uses the pre-count forecast at 2026-11-03 23:00Z, so the registered Senate-control score is unaffected; the displayed number is.

### 5. MED – Consistency of versions and sets
- v1–v5 and the 2026 pairs are structurally consistent (race_id, names, feed, poll close, Polymarket slug/`yes_is`, Kalshi ticker identical across versions; 0 mismatches). Every config hash matches `registry.jsonl`
  (the only unregistered file is the unregistered `us2026_national_v1`). v-to-v differences are exactly those in the headers (v2 swing prior; v3 sd by n polls + `count_scaled_terms`; v4 `expected_votes` file; v5 `hisp_slope`).
- **Version labels differ between Ohio's official and civic sets**: official OH v1 (no polls), v2 ≡ v1, v3 (poll sd 0.0393, no count scaling), v4 (sd 0.0228, scaled; primary), v5 secondary; civic OH v1/v2 (sd 0.0394)/v3 (sd 0.0228, scaled). Civic "v3" ≡ official "v4"; a pooled reader of "v3" would mix different models.
- SC-Sen: official v3 poll set is 2 polls (avg 0.4946, sd 0.0429, swing +0.1239), civic v3 is 3 polls (0.4896, sd 0.0334, +0.1189) for the same race on the same day.
- Poll snapshots are stale versus VoteHub today: KS-Gov 0.4756 (3 polls) vs 0.489 (5); KS-Sen 0.5087 vs 0.520; TX-Sen 17 → 22 polls, TX-Gov 16 → 20, SC-Sen 2 → 3. Frozen by design, but `poll_asof = 2026-11-02` is a convention date, not the fetch date.
- Poll-to-race matching reproduces the registered averages for every polled race where the VoteHub set is unchanged (differences only from polls added since: KS, TX, SC) (surname match on the D side; opponent = config R if named, else the highest other answer, which can be an "undecided"-type row — not seen in the 27 races).
- Units: all 21 statewide unit sets equal the geometry's counties (AZ 15, CA 58, FL 67, GA 159, MN 87, NC 100, NE 93, NM 33, OH 88, PA 67, SC 46, TX 254, VT 14); House priors' 1,213 unit ids all exist in the geometry. `expected votes` totals (e.g. TX 7.99 M, CA 10.93 M, OH 4.11 M) equal the stated priors.
- Pairs: `ky-sen` → `SENATELA-26-D` and `ks-gov` → `GOVPARTYKS-27-D` are documented Kalshi oddities (checked 10-29); poll closes are right for every state under EST (DST ended 11-01).
- Precount ordering: v1 < v2 < v3 is not monotone by design (v2 widens, v3 narrows): FL-gov 0.079/0.382/0.196; TX-sen 0.190/0.581/0.686. Reasonable; only VT-gov (0.0001/0.540/0.565) and OH (0.206/0.206/0.574→v4 0.702) show large jumps from adding polls.

### 6. MED – Overfitting / multiplicity
- Tuned or judged quantities: τ, ρ (2 settings from a 4-cell grid on GA), CA/AZ τ, early_share (GA 0.64 from certified, TX 0.6 "an assumption", NC 0.46), poll sd bins (3), House τ/ρ/σ_uf0 per feed group × version (grid on the 2024 replay, `n_sim = 6`, 34–68 districts of one year — and the 2026 lines in TX/FL/NC/OH are redrawn), `hisp_slope_tau` (0.2, tuned on 2012→16 and 2016→20), stacking weights (0/1), v3 count scaling chosen after v2 double-counted on GA 2022.
- Effective forward sample: 21 statewide races but one national polling error (ρ 0.21 per 538 + shared early-vote regime) and ~8 within ±15 pts of 50 %; House ~40 live districts share one generic swing. Five versions per race (4–5 scored × 21 races × 3 metrics) make an apparent win of a secondary version likely by chance;
  the pre-declared primary (v3, OH v4) mitigates this only if reported first.
- Simulated vs real: no recorded real 2026 night; the "execution" results use frozen Brazil-era edge rule (`night_r2_v1`, min_edge 0.05) on simulated books/replays; the rehearsal (10-30) exercised plumbing, not accuracy.
- v5 versus v3: pooled margin error 2.445 vs 2.466; harms TX hisp_last +0.74/+0.33 pts; stacking weights 0/1 — noted in NOTES; correctly secondary.

### 7. MED – Operational risks for 2026-11-03
- **Ohio** official feed 403 since 10-30 ~18:24Z (NOTES covers scoring); **South Carolina** 202 bot challenge — SC-Sen/Gov (v1–v4) and 4 SC House configs unscorable; civic substitutes exist and are scored separately. **WI, NH, ME** none. 4 of 21 registered statewide races (OH ×2, SC ×2) may produce no scored count.
- Expected vote = 2022 (or 2024 × 0.72) with ratio 1.0/0.72; a high-turnout midterm (2018 was far above 2014) raises counted/expected above `f_max` (0.95; AZ/CA 0.90 with `learn_turnout = false`), so the remaining vote is understated and P becomes overconfident late; documented only for CA.
- Alaska: no model; markets exist (ak-sen 5 pairs, ak-gov 17); top-four RCV general, resolution waits for the tabulation; do not score or compare. Maine (RCV), WI, NH likewise.
- Thin markets: House district Polymarket volumes are often < $60k (PA-08 58k, OH-09 58k, TX-28 22k; Kalshi 100k–3.7M); `catchup`/slope tests on these are wide; settle-lead is dominated by a handful of liquid races.
- MI-Gov: Duggan (I) is on the ballot; the two-party model ignores him (civic set only) while the market resolves on plurality.
- Calibration of the live timing: 30 s snapshots, minute timeline, ~90 s model lag; PA/NC/FL/CO/IA feeds are `--url` plain HTTP with no bot-challenge circumvention — any challenge on the night ends the series.

## Proposed text for `prereg/NOTES.md` (do not apply from this audit; the integrator edits the file)

> **Known limitations disclosed 2026-10-31 (before any result).** (a) The House v2 prior is the 2024 presidential vote on the 2026 lines plus a generic-ballot swing of +5.5 points taken from two VoteHub polls (last 2026-10-04); candidates and incumbency enter only through the count. It therefore disagrees strongly with markets in incumbent-effect districts (e.g. PA-01: model 0.86, market 0.47) and for districts with one or two county units the prior is not corrected during the night. (b) The statewide count-model settings come from Georgia 2022/2024 replays with simulated count orders; coverage figures (99–100 % at a 90 % level) show the bands are wide, not that probabilities are calibrated. v3 leans toward the Democrat by ~2–4 points of margin at 5–50 % counted in those replays. (c) Poll averages are not corrected for the 1.4-point pro-Democratic poll error of 2016–2022; the poll sd for ≥ 11 polls (0.0228) rests on 57 races. (d) Races with large swing priors (VT governor +0.28, SC Senate +0.12, NE governor +0.09) and races with ≤ 3 polls or none (NM governor) are poll-driven and the model's P there is expected to differ from markets. (e) Senate control is computed from poll averages (market price where unpolled), not from the live nowcast. (f) Ohio's civicAPI configs number versions differently from the official Ohio configs (civic v3 ≡ official v4). (g) NM governor has no poll model; v2 ≡ v1, scored as v1. (h) Alaska and Maine are not modeled. (i) Poll snapshots are as of 2026-10-29/30 and are not refreshed on the night.

## Recommended before polls close (no registered file needs changing)
1. Add the NOTES text above; state the primary for NM-Gov as v1 (v2 identical).
2. Do not read v1–v5 and civic "v3" as the same model in any pooled table; keep civic separate (already so).
3. When reporting, show the House as a descriptive study; flag districts with ≤ 2 units and incumbents who ran > 5 points ahead of president.
4. Treat VT-gov, NM-gov, AZ-gov as low-information P in any narrative; do not headline a disagreement there.
