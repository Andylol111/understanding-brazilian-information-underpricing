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
4. The word "firewall" appears once, in the "How to read this page" explainer that defines it.

## Refresh

`analysis/us_ev_publish.sh` (the integrator schedules it, e.g. every 3 h at :40 after the collector's :20 pass) pulls
`ev.sqlite` and the newest PA extract from the server (read only), refreshes the NC file, then in the dedicated worktree
`~/.cache/brunder-ev-publish` (reset to origin/main each run) builds, runs `site_check.py --only earlyvote` (chromium and firefox),
commits **only** `site/data/ev2026`, rebases on origin/main and pushes. It refuses to publish a database older than the committed
data. `--no-push` stops after the local commit.

Build by hand: `BRUNDER_DATA=~/brunder-data-local uv run python analysis/us_ev_site.py --db <copy of ev.sqlite> [--no-daily] [--strict]`.
Server copy: `rsync -a --rsync-path="sudo -n rsync" oracle-slotbank:/srv/brunder/evdata/us_ev/ev.sqlite <path>`.
