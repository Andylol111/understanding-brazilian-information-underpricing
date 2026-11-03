# US early-vote page (`site/earlyvote.html`)

A public page of ballots already cast for the 2026 US election, from official state publications only. It counts ballots; it has no
forecast, no win probability and no model. Data: [us_ev_db.md](us_ev_db.md) (the collector and its SQLite database, which runs on
the election server).

| piece | file |
|---|---|
| builder (database -> JSON) | `analysis/us_ev_site.py` |
| one source per series, audit | `analysis/us_ev_canon.py` |
| day-by-day rebuild from return dates (NC, PA) | `analysis/us_ev_daily.py` |
| refresh + checks + push | `analysis/us_ev_publish.sh` |
| page | `site/earlyvote.html`, `earlyvote.js`, `earlyvote.css` (own `__BUILD`) |
| data (committed, aggregates only) | `site/data/ev2026/index.json`, `state_<st>.json`, `counties_<st>.json` |
| tests | `tests/test_us_ev_site.py`, `tests/test_us_ev_src_c.py` (PA), `analysis/site_check.py --only earlyvote` |

## The Pennsylvania double count, and the rule it led to

`mail_returned` for PA once showed 2,086,905 against the official 1,041,756 (requested 2,646,487 against 1,315,103 approved). The
xlsx parser was not the cause: it reads the `Total` tab only, once (the `Total` tab is `Mail-In` + `Absentee`, checked on the raw
file: 1,025,407 + 16,349 = 1,041,756). The 2,086,905 is exactly the DOS xlsx (1,041,756) **plus** the data.pa.gov extract
(1,045,149): two sources for the same ballots, summed. The database keeps every source's rows side by side, so any query that
sums over `source_id` does this, for PA, Hawaii (two reports), Maryland (two files) and every state that also has a pre-database
legacy panel. Fixes:

* `us_ev_src_c.parse_pa_daily_xlsx` now also refuses a workbook whose county rows do not add up to its TOTAL row or whose `Total`
  tab is not `Mail-In` + `Absentee` (regression tests with a three-tab fixture: `tests/test_us_ev_src_c.py`).
* `us_ev_canon.canonical()` is the only way the site reads the database: for every state x measure x date it keeps **one**
  source (explicit order, then daily files, live collectors, legacy panels; more dates first) and takes all parties from that same
  source. A lower-ranked source that disagrees with the top one on a shared date by more than 2% is dropped for that state and
  measure (2026-10-31: Oregon's legacy panel, 9% below the live PDF). `audit()` checks per source that county rows add up to the state
  row (clean for every source on 2026-10-31) and that no canonical series has two sources. `us_ev_site.py --strict` exits 1 on a
  failure; the publish script uses it.
* The UF Election Lab tracker (`uf_election_lab`, kind `secondary`) is kept apart from the official rows (`split_uf`): it never
  enters an official series. Used with the permission of Dr. Michael McDonald (CC BY-NC-ND 4.0; displayed with the permission of
  Dr. Michael McDonald, granted to the project, recorded 2026-11-01), it (a) fills states with no official count, shown with a third
  status **compiled (UF Election Lab)** and the label "UF Election Lab (compiled from official state sources), used with
  permission", with the state's official status (blocked / not published / county offices only) beside it; party detail only where
  the Lab publishes party registration counts (D, R, none = unaffiliated), no modelling; and (b) next to an official figure appears
  only as a cross-check with the difference (`uf_check`). Past cycles for compiled states: the Lab's 2020 and 2022 files answer 404;
  its 2024 file (`us_ev_daily.py refresh-uf2024`, one request) is one snapshot per state dated 0-5 days before Election Day, so it is
  a same-day comparison only when that date falls within a day of this year's. Counties: official county offices only (the
  county-only sources of `us_ev_src_e.py` are shown as "county only", never as a state total).

## Day-by-day history of 2026

The collector stores one cumulative figure per official report. Two states publish the date every ballot came back, so their
whole series is rebuilt from today's files (`us_ev_daily.py`; pseudo sources `daily_nc_voter_file`, `daily_pa_open_data`, ranked
first):

* **NC**: the State Board's per-voter absentee/one-stop file (public record, `dl.ncsbe.gov`; one conditional request per refresh,
  honest user agent). Accepted ballots (`ACCEPTED`, `ACCEPTED - CURED`, `ACCEPTED - EXCEPTION`: the Board's own definition) are
  counted by county x return date x method x party; the voter file stays on the SSD, only the aggregate parquet next to the
  database and the site JSON leave it. Check: 2,612,249 on 2026-10-30 against the Board's published count of 2,612,249.
* **PA**: the `bydate` block that `pa_mail_socrata` already stores in its snapshot (a server-side aggregate of the data.pa.gov
  per-voter file); the collector only decoded the last 14 days into the database, the page uses all of it. Ballots later cancelled
  stay on their return day.
* **FL**: the Division of Elections page keeps only the latest compilation (past files are one end-of-period compilation per
  election), so FL has the collector's dated snapshots only. TX, CO, CT, DC, HI, ID, MD, MS, OK, OR, SC, SD, WV: the dated
  reports/files the collector saved (one point per report; weekend gaps are spread evenly in the added-per-day chart and shown lighter).
  Other states publish no history.

A reconstruction by return date is "what the file says today about each day", like the past-cycle panels it is compared with.

## What the page shows

1. Coverage of all 51 (map and sortable table): data level (county / state / county only), party detail, latest official as-of date,
   last fetch time, status (collected / compiled (UF Election Lab) / blocked / not published, with the reason from the source registry) and links to the offices.
2. Per state: cumulative ballots and ballots added per day (mail vs in person) by days before Election Day; party lead by
   registration (`D minus R` = (D - R) / (D + R + unaffiliated + other) of returned ballots, percentage points; the page says that
   registration is not a vote, and that unaffiliated and crossover voting are large); the same day before 2018, 2020, 2022 and 2024
   from `ev_hist_state.parquet` (ballots then, as % of that election's final votes, 2026 so far as % of the same, party lead then),
   marking which cycles each state has and why not (`HIST_CAVEAT`: TX's past files cover only the largest counties; FL's are one
   file). "Final votes" = the largest office total per county of the county panel, summed. Presidential and midterm years are
   marked and are not comparable with each other.
3. County map and table for states with county data: ballots, mail, in person, added since the previous report, % of 2022 votes,
   party lead.
4. **Pace vs 2022 (same day)** and **% of 2022 total votes** (coverage table, sortable) and a national list of states by pace
   (`analysis/us_ev_pace.py`, `state_pace` in `us_ev_site.py`; `index.json` `states[].pace`, `index.json` `pace`). See below.
5. The word "firewall" appears once, in the "How to read this page" explainer that defines it.

## Pace vs 2022

For every state with a count: **now** = this year's ballots so far (official if collected, else the UF Election Lab's compiled
figure; the in-person count for Maryland, whose past file is in-person only), with its days before Election Day `d0`;
**then** = the 2022 figure nearest `d0` days before Election Day (2022: Nov 8; 2026: Nov 3): the official past series
(`ev_hist_state.parquet`, same rules and `HIST_CAVEAT` as the past-cycle table) where the state has a day-by-day one, else an
archived 2022 UF tracker page; **pace** = now / then; **% of 2022 total votes** = now / the certified 2022 total (largest statewide
office total in `results_state.csv`: governor, Senate, or all U.S. House votes). A tie in distance takes the earlier day, never a
later count. A 2022 figure 0-1 day from `d0` is treated as the same day; 2-3 days is used but flagged `gap` (marked * on the page,
with how many days earlier or later); more than 3 days is not used (no pace, the reason is in `pace.why`). A mismatch between the
2022 page's measure and this year's (e.g. 2022 mail only, 2026 mail + in person) is flagged `definition`. Every value carries its
source label: *official*, *compiled (UF)* (2026 side only) or *archived UF 2022*.

**Where 2022 lived, and what the Internet Archive holds.** The 2022 UF tracker was not on election.lab.ufl.edu (its own tracker
starts in 2024; the 2022 and 2020 data files answer 404) but was a set of RPubs documents by Michael McDonald:
`rpubs.com/ElectProject/early_vote_2022` (national, aggregate counts only, many captures) and one page per state,
`rpubs.com/ElectProject/<ST>_early_vote_2022`, each an iframe onto `rstudio-pubs-static.s3.amazonaws.com/<id>_<hash>.html` with a
"Last update" date, the state's total / in-person / mail counts and a county table (no daily series). `analysis/us_ev_uf2022.py
survey` finds, through the CDX API, every captured static state page from 2022-09-20 to 2022-11-08 and stores the raw HTML under
`$BRUNDER_DATA/raw/us/uf_2022_wayback/` (with `survey.json`: the id and query per state, including states with nothing, and
`state_ids.json`); `parse` writes `$BRUNDER_DATA/processed/us2026/uf_2022_wayback.csv` (state, capture time, the as-of date the
page states, days before Election Day, total / in-person / mail, and what the total is made of). It is never mixed with
official rows. Result of the survey (2026-11-01): the Archive captured the RPubs state pages mostly on Nov 7 and Nov 10, but the
static files behind them are almost never archived; only Georgia (as of Oct 9), Pennsylvania (Oct 7) and South Dakota (Oct 7)
have a page before Election Day, 29-32 days before it, and Florida's only copy is the final one (Nov 8, captured Nov 14). So no
state's pace uses an archived UF 2022 page for 2026's days 3-8; the page says so, and only states with an official 2022
day-by-day series (CO, MD, NC, OK, PA) get a pace. The code picks up any archived page that falls within the allowed gap.
The national page's 2022 daily totals (aggregate over the reporting states, not by state) are not used: the state sets differ.
Access: web.archive.org only (CDX + `id_` captures), at most one request a second, user agent `brunder/0.1 (research; recorder)`.

## Refresh

`analysis/us_ev_publish.sh` (the integrator schedules it, e.g. every 3 h at :40 after the collector's :20 pass) pulls
`ev.sqlite` and the newest PA extract from the server (read only), refreshes the NC file, then in the dedicated worktree
`~/.cache/brunder-ev-publish` (reset to origin/main each run) builds, runs `site_check.py --only earlyvote` (chromium and firefox),
commits **only** `site/data/ev2026`, rebases on origin/main and pushes. It refuses to publish a database older than the committed
data. `--no-push` stops after the local commit.

Build by hand: `BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_ev_site.py --db <copy of ev.sqlite> [--no-daily] [--strict]`.
Server copy: `rsync -a --rsync-path="sudo -n rsync" oracle-slotbank:/srv/brunder/evdata/us_ev/ev.sqlite <path>`.

## Turnout today (`site/turnout.html`)

Election Day 2026 only; turnout, not vote choice. **Unvalidated context, not used by any model.** The early-vote page keeps a compact summary of
`data/ev2026/turnout_day.json` (+ `turnout_volume.json`) and links here.

- **Files.** `turnout_day.json` / `turnout_volume.json` come from `analysis/us_turnout_publish.py` (rows of the research file; kinds never mixed).
  `turnout_model.json` comes from `analysis/us_turnout_model.py`, which `analysis/us_turnout_publish.sh` runs from the publish worktree after the
  publisher; the three files are the commit allowlist of the loop. The page reads only `turnout_model.json`, `turnout_volume.json`, the geometry and
  `ev2026/state_<st>.json` (daily series), and re-reads every 5 minutes.
- **Banked ballots** (one rule): all pre-Election Day ballots (+ Election Day check-ins when a whole-state row has them), else a total of all methods,
  else mail + early in person (+ Election Day); a row that only sums some counties is never added and makes the figure a lower bound.
- **Expected final votes:** the registered live models' county inputs where a statewide race is modelled (`site/data/us2026/units/*.json`, `V0`,
  expected two-party votes; mean of a state's races), otherwise `EST_TURNOUT` (0.72, `brunder.us`) x 2024 president two-party votes
  (`data_public/us2026_county_baseline.csv`; state total from `us2026_state_pres24.csv`). Ballots include third-party and blank votes, so a share of
  the two-party expectation runs a little high.
- **Outstanding mail ballots** (`states.<st>.outstanding`, county `mail`): ballots sent (else requested) minus ballots returned, from one source with
  both figures (WI WEC, IA SOS, MT SOS, KS SOS via IKE Lab, MI SOS, a state-file mail row such as PA's; else the UF requested-vs-mail-accepted pair).
  Not computed when a figure is missing, returned exceeds sent, or the two are as of dates more than a day apart. In a state that mails every active
  voter (CO, NV, WA, OR, UT, CA, HI, VT, DC) the same difference is labelled "not returned yet", not outstanding, and is left out of the early-vote total.
- **Vs 2022 final pre-election report** (`vs_2022_final`): latest 2026 count of the same kind over the 2022 figure at the last report before Election
  Day (`ev_found.json`: IA 2022-11-07, ME, MI mail, KS E-1, OH, TX last early-vote day), always shown with both dates and "not same day". Alaska's 2022
  figure is flagged do-not-use and shows no ratio.
- **Who is voting** (`who`): age bands from `us2026_context/ev_groups.json` (only where >= 90% of the ballots have an age; NC and CO with their 2022 and
  2024 same-day shares) or the IKE Lab age counts for Kansas (2026 / 2024 / 2022). The new-voter share is null: no vote history is recorded.
- **County rows and county party.** `counties_<st>.json` for NC, CO, TN, WI; the research extras for IA, MT, KS (all counties); county party
  (registration-based) from `counties_<st>.json` (NC, CO, FL, PA) and the extras (IA, KS). The extras are
  `brunder-research/data/turnout_extra.json` (`scripts/live/turnout_extra.py`, aggregates only; `--extra` overrides the path).
- **Composition shift** (states with >= 10 counties with a count): statewide Democratic two-party share if every county voted like its 2024 president
  two-party share (+ the mean poll-implied swing of the state's Senate / governor races, `overview.json`), weighted by banked ballots minus weighted by
  expected votes, over the same counties; 95% interval from 2000 bootstrap resamples of the counties (seed 2026).
- **Count order:** the first clause of the `[count]` comment of the state's newest registered config; no config, no sentence (unknown). "First returns
  likely lean D/R" only for states whose config says early/mail is posted first AND a registration-based party split of banked ballots is published
  (early-vote files or a party row in the turnout file), at least 2 points from even.
- **History:** every run appends the banked total to `history` (read back from the committed file), drawn with the official daily series.

## Projected final turnout (`analysis/us_turnout_proj.py`, on `site/turnout.html` and one card on `site/earlyvote.html`)

Unregistered, updates with incoming data; the registered models do not use it. `us_turnout_model.py` calls it on every refresh; the output is `states.<st>.proj` and `proj_summary` in `turnout_model.json`.

- **A**: pre-Election Day ballots / the state's early share of its final total in the previous same-type cycle (2022, else 2018; fallback: the previous cycle of the other type), share clipped to 0.15 to 0.98. A floor applies: the projection is never below the ballots already banked (incl. Election Day check-ins) nor below banked + r x mail not yet returned (r = 0.5; no state late-return rate is on disk; this floor is not backtested).
- **B**: A blended in log space with the model's expected votes (two-party, scaled to ballots by the state's 2024 total/two-party ratio) by precision, weight on A = (1/vA) / (1/vA + 1/vM) from the mean squared log errors of A and the naive baseline over earlier backtest cycles only.
- **C** (Election Day check-ins x 1 / share in by this clock hour): not available. No prior-cycle hour curve is on disk, so none is invented. County projections: not available (no county early-share history on disk).
- **Backtest** (`ev_history_all.json` + the county panel's president totals): 2022 on 2018 shares, 2024 on 2020, 2020 on 2016 (no 2016 file: n = 0), plus fallback pairs one cycle back. Error = log(projected / actual final). Baseline = 0.72 x the previous presidential total (midterms) or the previous same-type total (president years). B, its weight and the 80 % interval (exp(+-1.2816 x sd)) are fitted on earlier cycles only, so B is tested on 2024 only (weights from 2022). Selection: lowest out-of-sample MAE of A and B.
- **Result at build**: 2024 out of sample, 42 states: A 25.7 %, B 6.7 %, naive baseline 4.8 %; 80 % interval coverage of B 95 %. The projection does not beat the naive baseline; the page says so and presents the projection as a cross-check on the model's expected total. Early-vote definitions differ between years, which is most of A's error.
- Per state: `proj_total` with `low`/`high`, `vs_expected` (ratio to the ballot-scaled expected votes) and `heading` text, `banked_share`, `delta` against the previous refresh (the `history` array of `{t, v, b, e}`, capped at 120), `inputs`. States without history: `no history for this state`.
- Page: map measure "Projected final turnout vs model expected" (diverging around 100 %), a state-panel block with a sparkline and a backtest details block; earlyvote Key findings card "Turnout heading vs the model (unregistered)".

## Has the early vote tracked the result? (`site/earlyvote.html`, section `#eh-sec`)

Past elections only: how well the registered-party mix and the size of the early vote tracked the certified result, every state the UF Election Lab /
US Elections Project trackers covered. **Descriptive, registration-based, not a projection, not used by any model.** The registered early-vote-gap test
([us_ev_gap.md](us_ev_gap.md): 24 races, direction 14/23 = 61 %, Wilson lower bound 0.41, polls made worse out of sample) still stands; the section
says so next to every 2026 range.

| piece | file |
|---|---|
| collection: raw pages -> one row per state x cycle | `brunder-research/scripts/session/ev_history_fetch.py`, `ev_history_all.py` -> `data/ev_history_all.csv` (local only; raw pages under `data/raw/ev_history/`) |
| analysis + publisher (no network) | `analysis/us_ev_history_all.py --csv <ev_history_all.csv>` -> `site/data/ev2026/ev_history_all.json` |
| page | `site/earlyvote.history.js` (own module; talks to `earlyvote.js` only through the `ev:state` / `ev:pick` events, never scrolls), block at the end of `earlyvote.css` |
| tests | `tests/test_us_ev_history_all.py` (gap, winner, demeaning, direction counts, change pairs, row building), `analysis/site_check.py --only earlyvote` |

**Sources** (secondary, aggregates only; honest user agent, <= 1 request/s, robots honoured; licence line on the page): 2024 `election.lab.ufl.edu/data-downloads/earlyvote/2024/US.csv`
(one snapshot per state); 2022 the Lab's RPubs state pages; 2020 `electproject.github.io/Early-Vote-2020G`; 2018 the Internet Archive copy of the 2018 Early Vote
sheet (2018-11-06 19:33 UTC; the only archived copy of its data tab). **2016: no data.** The 2016 sheet's data tab is not in the Internet Archive (only the empty shell), and
the Lab and the Elections Project publish no other 2016 file, so the section covers 2018-2024. For every state-cycle the report used is the last one dated on or before Election Day
(a live page when its own "Last update" is on or before Election Day, else the latest archived copy that is; the date and days before are in the file). Reports dated on Election Day
itself count ballots through the previous day or that morning (2018: 9 of 49, 2020: 12 of 51, 2022: 12 of 45, 2024: 20 of 49).

**Definitions.** `ev_d2` = registered D / (D + R) among early ballots (only where the source gives party for the whole early vote: 84 state-cycles); `act` = mean D two-party
share of the state-cycle's certified polled Senate / governor / president races (`us_ev_party_misses.csv`; the county panel fills the president where that table has none, and
every race of a state-cycle it does not cover, never a race with a D share outside 5-95 %); `gap` = `ev_d2` - `act`. 78 state-cycles in 27 states have both. Early % of final votes
= total early ballots / the largest statewide race total of the county panel (not in it: AK, CT, DC), left blank when the report is more than 7 days before Election Day, covers
part of a state (ID 2020: Ada County only; OH 2020: 80 of 88 counties; five partial 2018 reports) or exceeds the final total. Changes between cycles are compared only where the
tracker's early-vote definition (mail only / in-person only / both) is the same; the cycle-demeaned version subtracts each (cycle pair)'s mean change over the states first.
Age: share of the oldest group (65+ in 2020 and 2022, "60+" in 2018, which has two states); 2024's file does not state its four age groups, so none is taken from it.

**Headline (generated numbers, `pooled` in the file).** Gap: n = 78, mean +3.8 pt, mean absolute 7.0 pt, same side of 50 as the result in 67 of 78; cycle means 2018 +1.7, 2020 +6.6, 2022 +6.3, 2024 +0.9.
Change against change (same type, 23 states): direction agrees 18/23, 15/23 with the cycle average removed (95 % interval 45-81 %), correlation +0.28; against any previous cycle 25/36 after
the adjustment, +0.31. Early-vote share of the final vote (same type, 44 pairs): 25/44 after the adjustment (42-70 %), correlation +0.17; any previous cycle 40/72, -0.06. Age: 3 states (GA, MI, PA, 2020 to 2022).
