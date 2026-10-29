# Poll crosstabs: recalled 2024 vote and a recalled-vote reweighting (display only)

Built by `analysis/us_crosstabs.py` → `site/data/us2026/hub/crosstabs.json`; per-poll inputs in
`data_public/us2026_crosstabs.csv`. **Display only:** no registered config or model changes. All margins are two-party, the
Democratic side minus the opponent, in points.

## Why

Some 2026 Senate and governor polls look strongly Democratic. One check that needs no model is the poll's own crosstab: if a
sample recalls voting for Harris in 2024 at a higher rate than the state actually did, the sample may lean Democratic. This
page measures that gap for every poll in our averages that publishes it. It then shows each topline as published (raw) and
reweighted to the certified 2024 result (adjusted).

## Sources and licences

- Polls and weights: `site/data/us2026/hub/polls.json` (VoteHub Polling API, CC BY 4.0), as of 2026-11-02.
- Crosstabs: each poll's release page (the VoteHub `url`) and the PDF/XLSX/Google-Sheet files it links, read on
  2026-10-29. We read only what robots.txt allows, with one plain request per URL from a research user agent. Any
  4xx answer (Cloudflare/bot walls, paywalls, DocumentCloud 403s) was recorded as `blocked` and **not retried**. No
  log-ins, no data requests. Each poll's `source_url` is in the CSV and the JSON. We quote only facts (percentages).
- Certified 2024 president by state: MIT Election Data and Science Lab, `2024-president-state.csv` (CC0), via
  `data_public/us2026_state_pres24.csv`.
- History: FiveThirtyEight pollster ratings `raw_polls.csv` (CC BY 4.0). It covers Senate and governor general elections
  2016-2022: polls of the final 21 days, D vs R.

### How the crosstab values were read

A script downloaded the release pages and linked files and turned them into text (pypdf for PDFs, openpyxl for XLSX
exports). A language model then transcribed the printed values for that poll into fixed fields: recalled 2024 vote shares,
this race's vote among recalled Harris and Trump voters, party mix, 4-year-degree share, white share, 65+ and 18-29 shares,
LV screen and mode. It was told to return null for anything not printed and to ignore other polls in the same file. Checks:

- every non-null value used for an adjustment was searched for in the source text as a printed number: recalled Harris
  and Trump shares, plus both candidates among Harris and Trump voters (38 polls × 6 fields). 3 values in 2 polls did not
  match literally, and all 3 were checked by hand. Emerson Iowa: Hinson 0.8327 among Trump voters, transcribed as 83.
  Noble Predictive Nevada: RV crosstab cells, plausible but not exact;
- the Emerson Iowa sheet was also checked by hand: recall Harris 41.6 / Trump 51.6 / other 3.9 / did not vote 2.9, and
  Senate Turek 88.6–3.6 among Harris voters, 9.6–83.3 among Trump voters;
- the same poll used in two races must give identical sample shares. One conflict was found and fixed: MSU/YouGov
  Michigan party ID, where one row counted leaners and the other did not. The no-leaner figures are kept for both rows.

Party mix uses whatever the pollster printed: party ID or party registration (`party_kind`), and with or without leaners
as published. It is not comparable across pollsters.

## Coverage

Of the **254 polls** (78 pollsters) in our current averages:

| status | polls | meaning |
|---|---:|---|
| recall_xtab | 11 | prints recalled 2024 vote **and** this race's vote by recalled vote (exact reweighting) |
| recall | 27 | prints recalled 2024 vote shares only (approximate reweighting) |
| composition | 34 | prints sample composition (party / education / race / age) but not recalled vote |
| no_crosstabs | 98 | release page read, but no crosstabs for this poll found in it or its linked files |
| blocked | 65 | release page answered 4xx (bot protection / paywall); not retried |
| robots | 19 | robots.txt disallows the release URL (x.com, Washington Post, Star Tribune, Mailchimp, …) |

72 polls have some crosstab, and **38** report recalled 2024 vote. Those 38 carry
13 % of the total weight in our averages.

- **Blocked, nothing readable:** Alaska Survey Research, Beacon Research, Bowling Green State University/YouGov, Bravo Group, DHM Research, Echelon Insights, Embold Research, GQR, Glengariff Group, Inc., Grayhouse, Guidant Polling and Strategy, MDW, MDW Communications, Marist University, Mason-Dixon, New River, New River Strategies/GS Strategy Group, RMG Research, Saint Anselm, The Alabama Poll, The New York Times/Siena University, The Washington Post/George Mason University School of Policy and Government, University of California Berkeley Jack Citrin Center for Public Opinion Research/Verasight/TrueDot, University of Maryland Baltimore County Institute of Politics, Z to A Research. Marist, NYT/Siena (nytimes.com and sri.siena.edu),
  Echelon (via NetChoice) and Saint Anselm usually publish full crosstabs, but their servers refused our client.
- **Release read, no crosstabs found:** American Pulse Research & Polling, Beacon Research/Shaw & Co. Research, Big Data Poll, CNN/SSRS, Cygnal, East Carolina University, Elon University/YouGov, Equis Research, GreatBlue Research, Hart Research Associates, Hendrix College, InsiderAdvantage, Marquette University Law School, Mitchell Research & Communications, Quantus Insights, ReconMR/Siena University, Slingshot Strategies, Spry Strategies, Stetson University Center for Public Opinion Research, SurveyUSA, Targoz Market Research, Texas Southern University Barbara Jordan Public Policy Research and Survey Center, The Tyson Group, Trafalgar Group, UC Berkeley Institute of Governmental Studies, University of Massachusetts Lowell Center for Public Opinion/YouGov, University of New Hampshire, Wedgewood Polls. This covers several cases: some pollsters
  publish only toplines (InsiderAdvantage, Quantus, Big Data Poll, Trafalgar memos, sponsor memos). For others the
  crosstabs sit on a host that refused us (CNN/SSRS on DocumentCloud) or are not linked from the article VoteHub cites
  (Fox News/Beacon-Shaw). Some releases print crosstabs without a recalled-vote banner (UNH, SurveyUSA, Marquette's
  readable files).

## Method

- **Recall gap** = the recalled Harris-minus-Trump two-party margin minus the certified 2024 two-party margin in the state
  (+ = the sample remembers more Harris votes than the state cast).
- **Adjusted topline:** a one-variable post-stratification. Recalled Harris and Trump voters are reweighted so that their
  two-party split equals the certified 2024 result. Their combined share and the other/non-voter group stay fixed. The
  change in the margin is $(s_H+s_T)(h^*-\hat h)(m_H-m_T)$, where $\hat h$ and $h^*$ are the recalled and certified
  Harris two-party shares and $m_H$, $m_T$ are the candidate margins within recalled Harris and Trump voters. The change
  is then rescaled to two-party points.
  - `cells` (11 polls): $m_H$ and $m_T$ are the poll's own crosstab.
  - `approx` (the other 27): $m_H-m_T$ is set to the median of the `cells`
    polls, 167 points (range 124-175).
- **Race averages:** the weights are exactly those of `polls.json`. In the adjusted average, polls without recalled vote
  keep their raw margin. The "recall polls only" columns re-normalise the weights over the polls that print recalled vote.
- **Caveat:** recalled-vote weighting has known biases:
  - some voters misreport or forget their 2024 vote, with drift towards the winner;
  - low-propensity Trump voters often fail to recall a vote or say they did not vote;
  - "shy" respondents;
  - a 2026 midterm electorate is not the 2024 electorate. A Harris-heavy recall among *likely* midterm voters can be a real
    turnout difference, not a skewed sample.

  Many of these pollsters already weight on recalled vote, so a gap that remains can come from the LV screen. Both
  numbers are shown, and neither is a forecast.

## Results (2026, as of 2026-11-02)

Across the 38 polls with recalled vote, the mean recall gap is **+1.6** (median
+1.5). 25 polls lean D and 13 lean R. The
adjustment moves those polls by **-1.3** on average. Race averages move by at most about 2 points
(NV-gov, VT-gov: one poll each with a +5.5 / +6.6 gap). Most races move by less than 1 point, because only
13 % of the poll weight can be adjusted. The pollster-level recall gap does not line up with the
pollster's 2016-2022 house effect (6 pollsters with both, r = -0.41).

### Per race

| race | polls | with crosstabs | with recalled vote | raw avg | adjusted avg | change | raw, recall polls only | adjusted, recall polls only | mean recall gap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ak-gov | 5 | 1 | 1 | +11.9 | +12.0 | +0.1 | +6.0 | +6.2 | -0.3 |
| ak-sen | 3 | 1 | 1 | +0.8 | +0.9 | +0.1 | +2.0 | +2.2 | -0.3 |
| al-gov | 2 | 0 | 0 | -14.1 | -14.1 | +0.0 | – | – | – |
| al-sen | 3 | 0 | 0 | -21.6 | -21.6 | +0.0 | – | – | – |
| ar-sen | 2 | 0 | 0 | -11.0 | -11.0 | +0.0 | – | – | – |
| az-gov | 2 | 1 | 1 | +6.4 | +5.0 | -1.4 | +6.2 | +3.4 | +3.5 |
| ca-gov | 5 | 1 | 1 | +25.8 | +24.9 | -0.9 | +28.0 | +24.6 | +4.4 |
| ct-gov | 4 | 2 | 1 | +20.1 | +19.1 | -0.9 | +15.2 | +11.5 | +4.5 |
| fl-gov | 11 | 7 | 4 | -4.8 | -5.6 | -0.8 | -2.8 | -4.9 | +3.1 |
| fl-sen | 10 | 5 | 2 | -6.6 | -7.1 | -0.5 | -3.5 | -5.4 | +3.2 |
| ga-gov | 8 | 2 | 0 | -1.3 | -1.3 | +0.0 | – | – | – |
| ga-sen | 7 | 2 | 0 | +10.1 | +10.1 | +0.0 | – | – | – |
| ia-gov | 11 | 4 | 3 | +7.0 | +6.5 | -0.5 | +5.6 | +4.1 | +1.9 |
| ia-sen | 11 | 4 | 3 | -0.0 | -0.4 | -0.4 | -0.7 | -2.2 | +1.9 |
| il-gov | 2 | 2 | 0 | +20.1 | +20.1 | +0.0 | – | – | – |
| il-sen | 2 | 2 | 0 | +16.1 | +16.1 | +0.0 | – | – | – |
| ks-gov | 2 | 0 | 0 | -4.7 | -4.7 | +0.0 | – | – | – |
| ks-sen | 2 | 0 | 0 | +0.5 | +0.5 | +0.0 | – | – | – |
| ma-sen | 4 | 3 | 2 | +30.8 | +29.8 | -1.0 | +31.1 | +29.1 | +2.5 |
| md-gov | 1 | 0 | 0 | +36.0 | +36.0 | +0.0 | – | – | – |
| me-gov | 9 | 2 | 1 | +10.4 | +10.4 | +0.0 | +14.3 | +14.6 | -0.4 |
| me-sen | 11 | 2 | 1 | +2.4 | +2.4 | +0.0 | +3.1 | +3.4 | -0.4 |
| mi-gov | 9 | 3 | 1 | +8.6 | +8.7 | +0.1 | +13.2 | +13.7 | -0.7 |
| mi-sen | 14 | 3 | 1 | +3.1 | +3.2 | +0.1 | +2.7 | +3.2 | -0.7 |
| mn-gov | 2 | 0 | 0 | +8.9 | +8.9 | +0.0 | – | – | – |
| mn-sen | 3 | 0 | 0 | +4.8 | +4.8 | +0.0 | – | – | – |
| ms-sen | 1 | 0 | 0 | -4.0 | -4.0 | +0.0 | – | – | – |
| mt-sen | 2 | 0 | 0 | -32.0 | -32.0 | +0.0 | – | – | – |
| nc-sen | 8 | 2 | 1 | +11.6 | +11.6 | +0.0 | +11.4 | +11.5 | -0.2 |
| ne-gov | 3 | 2 | 2 | -7.0 | -6.9 | +0.2 | -6.0 | -5.7 | -0.2 |
| ne-sen | 3 | 2 | 2 | -2.1 | -1.9 | +0.2 | -3.1 | -2.8 | -0.2 |
| nh-gov | 3 | 0 | 0 | -8.3 | -8.3 | +0.0 | – | – | – |
| nh-sen | 3 | 0 | 0 | +7.2 | +7.2 | +0.0 | – | – | – |
| nm-sen | 1 | 0 | 0 | +20.0 | +20.0 | +0.0 | – | – | – |
| nv-gov | 2 | 1 | 1 | +1.5 | -0.5 | -2.0 | -1.3 | -5.3 | +5.5 |
| ny-gov | 5 | 1 | 0 | +15.8 | +15.8 | +0.0 | – | – | – |
| oh-gov | 13 | 3 | 1 | +2.9 | +2.7 | -0.2 | +0.9 | -1.6 | +3.0 |
| oh-sen | 13 | 2 | 1 | +3.9 | +3.7 | -0.2 | +4.3 | +1.8 | +3.0 |
| or-gov | 1 | 0 | 0 | +2.2 | +2.2 | +0.0 | – | – | – |
| pa-gov | 4 | 1 | 0 | +24.6 | +24.6 | +0.0 | – | – | – |
| ri-gov | 2 | 0 | 0 | +22.0 | +22.0 | +0.0 | – | – | – |
| ri-sen | 2 | 0 | 0 | +24.9 | +24.9 | +0.0 | – | – | – |
| sc-gov | 2 | 0 | 0 | -14.4 | -14.4 | +0.0 | – | – | – |
| sc-sen | 2 | 0 | 0 | -1.1 | -1.1 | +0.0 | – | – | – |
| sd-gov | 1 | 1 | 0 | -18.8 | -18.8 | +0.0 | – | – | – |
| tn-gov | 1 | 0 | 0 | -15.8 | -15.8 | +0.0 | – | – | – |
| tx-gov | 16 | 4 | 3 | -2.8 | -2.9 | -0.1 | -2.3 | -2.6 | +0.2 |
| tx-sen | 17 | 5 | 3 | +2.5 | +2.4 | -0.1 | +4.1 | +3.8 | +0.2 |
| vt-gov | 2 | 1 | 1 | +1.7 | -0.2 | -1.9 | -3.2 | -7.0 | +6.6 |
| wi-gov | 2 | 0 | 0 | +4.3 | +4.3 | +0.0 | – | – | – |

### Per pollster (polls with recalled vote)

| pollster | polls in averages | with recalled vote | mean recall gap | mean shift of its polls | 538 house effect 2016-22 |
|---|---:|---:|---:|---:|---:|
| Emerson College | 8 | 6 | +3.0 | -2.4 | -1.2 |
| National Public Affairs | 6 | 6 | +0.3 | -0.2 | – |
| YouGov | 5 | 5 | +0.5 | -0.4 | +0.2 |
| Change Research | 4 | 3 | +5.2 | -4.4 | -1.2 |
| Suffolk University | 4 | 1 | +4.9 | -4.0 | -0.8 |
| VoteHub/Verasight | 4 | 2 | +3.0 | -2.4 | – |
| Data for Progress | 2 | 2 | -0.3 | +0.2 | -0.3 |
| Fabrizio, Lee & Associates/Impact Research | 2 | 2 | -0.4 | +0.3 | – |
| Michigan State University Institute for Public Policy and Social Research/YouGov | 2 | 2 | -0.7 | +0.5 | – |
| Pollfinity | 2 | 2 | -0.7 | +0.6 | – |
| University of North Florida | 2 | 2 | +1.6 | -1.2 | – |
| GrayHouse | 1 | 1 | +3.5 | -2.8 | – |
| Harper Polling | 1 | 1 | -0.2 | +0.2 | – |
| Noble Predictive Insights | 1 | 1 | +5.5 | -3.9 | -0.0 |
| State Navigate | 1 | 1 | +6.6 | -3.8 | – |
| University of Massachusetts Department of Political Science/YouGov | 1 | 1 | +0.1 | -0.1 | – |

The 538 house effect is the pollster's mean poll error minus the mean error of all polls of the same race, 2016-2022
(+ = more Democratic than the field).

## Historical test: did D-leaning polls miss more?

There is no open archive of recalled-2020-vote crosstabs for 2018-2022 polls. Pollster-level 2024 Senate and governor polls
are also missing from every open-licence file we have: 538's `raw_polls.csv` stops at 2023, and VoteHub serves only the
current cycle. So recalled vote cannot be tested directly. We use **the pollster's own past lean as a proxy**, out of
sample: for each cycle, a lean is computed from all earlier cycles (≥ 5 polls, shrunk n/(n+10)) and compared with the
pollster's error in that cycle. The error is poll minus result, in two-party margin points, + = overstated the Democrat.
Two leans are tried:

- *absolute*: the pollster's mean error. In practice this mostly measures the industry-wide misses of 2016 and 2020.
- *relative*: its error minus its race's mean poll error, i.e. the house effect.

"Corrected" means the shrunk lean is subtracted from each rated poll.

Absolute prior lean:

| cycle | polls | industry mean error | rated polls (pollsters) | D-lean: n / mean err / RMSE | neutral | R-lean | corr(prior lean, error) | corr(prior lean, house effect) | poll RMSE raw → corrected | poll MSE removed | race-avg RMSE raw → corrected |
|---|---:|---:|---:|---|---|---|---:|---:|---|---:|---|
| 2018 | 340 | +0.5 | 111 (11) | 38 / +1.9 / 4.6 | 51 / +0.7 / 5.2 | 22 / +0.9 / 6.1 | +0.11 | +0.31 | 5.21 → 5.18 | +1.1 % | 5.47 → 5.62 |
| 2020 | 307 | +5.2 | 132 (24) | 72 / +5.2 / 6.8 | 42 / +5.3 / 7.2 | 18 / +0.9 / 4.4 | +0.16 | +0.20 | 6.63 → 6.02 | +17.5 % | 7.85 → 7.59 |
| 2022 | 434 | -0.6 | 272 (24) | 195 / -0.9 / 4.8 | 45 / -1.1 / 4.0 | 32 / -5.8 / 7.2 | +0.20 | +0.22 | 5.06 → 5.52 | -18.9 % | 5.40 → 5.49 |

Relative prior lean (house effect):

| cycle | polls | industry mean error | rated polls (pollsters) | D-lean: n / mean err / RMSE | neutral | R-lean | corr(prior lean, error) | corr(prior lean, house effect) | poll RMSE raw → corrected | poll MSE removed | race-avg RMSE raw → corrected |
|---|---:|---:|---:|---|---|---|---:|---:|---|---:|---|
| 2018 | 340 | +0.5 | 111 (11) | 18 / +2.4 / 4.9 | 20 / +1.3 / 4.3 | 73 / +0.8 / 5.5 | +0.15 | +0.21 | 5.21 → 5.27 | -2.3 % | 5.47 → 5.44 |
| 2020 | 307 | +5.2 | 132 (24) | 24 / +4.0 / 6.1 | 67 / +5.5 / 7.0 | 41 / +3.8 / 6.2 | +0.07 | +0.16 | 6.63 → 6.70 | -1.9 % | 7.85 → 7.89 |
| 2022 | 434 | -0.6 | 272 (24) | 15 / -1.0 / 3.4 | 161 / -0.9 / 5.0 | 96 / -2.7 / 5.3 | +0.22 | +0.26 | 5.06 → 4.85 | +8.1 % | 5.40 → 5.45 |

Variance decomposition of all 1491 polls 2016-2022 (RMSE 6.8):

| component | share of squared error |
|---|---:|
| cycle-wide mean error | 22 % |
| race within cycle | 38 % |
| poll within race (house effect + sampling noise) | 40 % |

The cycle means were 2016 +4.2, 2018 +0.5,
2020 +5.2 and 2022 -0.6.

**Answer, honestly.**

1. Polls from D-leaning pollsters did not consistently miss more. D-lean pollsters missed more in 2018 (+1.9 / +2.4 vs
   +0.7 to +1.3 for the rest), but not in 2020 (everyone overstated Democrats by about 5) or 2022 (the R-lean pollsters
   missed most, by 2.7 to 5.8 towards the Republican).
2. House effects persist only weakly from cycle to cycle (r = +0.16 to +0.26 with the next cycle's house effect).
   Removing them changed poll-level squared error by −2 % to +8 %, and race-average RMSE by ±0.1. Removing absolute lean
   helped in 2020 (−17.5 % MSE) and hurt by as much in 2022 (+18.9 %). That is a bet on the direction of the industry
   error, not a fix.
3. How much of the 2016-2024 error a recalled-vote adjustment would have removed **cannot be measured with open data**.
   The part it could plausibly reach is differential partisan non-response: the cycle-wide component, about
   22 % of squared error in 2016-2022 and concentrated in 2016 and 2020. The
   race-within-cycle and poll-within-race parts, about 78 %, are mostly out of its reach.
   [Pew Research Center (2026)](https://www.pewresearch.org/methods/2026/09/16/weighting-polls-on-party-affiliation-and-past-vote/)
   reports that weighting its 2020 and 2024 post-election polls on party improved accuracy by about 2-3 points on the
   margin, and calls the evidence (citing the AAPOR 2024 task force) "modestly more accurate". [INFERENCE] In 2022 the
   industry error ran the other way (−0.6), so a uniform shift towards Republicans would probably have hurt that year.

**Recommendation.** The data do not show that the adjustment clearly helps, so it stays **display only**. A candidate for a
future version would be a recalled-vote adjustment applied only to polls that print recall *and* do not already weight on
it. That candidate needs a test on 2026 results first: compare the raw and adjusted columns of this file with the
certified 2026 margins after November.

## Reproduce

```
BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_crosstabs.py
uv run pytest -q tests/test_us.py -k "crosstabs or recall or lean_test"
```
