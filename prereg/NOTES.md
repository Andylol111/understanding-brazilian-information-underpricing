# What each registration does and does not guarantee

`registry.jsonl` is append-only and records hashes; this file records what was already known when each
spec was registered, so readers can judge how out-of-sample each result is.

**History rewrite, 2026-10-28.** Before the repository was made public, its history was rewritten to
remove a personal email and to replace lines carrying figures derived from unlicensed third-party data
with a marker. Commit ids in `registry.jsonl`, `holdout_runs.jsonl` and the docs are the original ids;
`commit_map_2026-10-28.txt` maps each to its rewritten commit. Spec files and their SHA-256 hashes are
unchanged, so every registration still verifies.

**Look log repair, 2026-10-28.** The seven runoff looks (night_r2_v1 and the six governor replays, one
each, 2026-10-28 02:29–02:32 UTC) were appended to `holdout_runs.jsonl` but not committed, and the
history rewrite reset the working tree, discarding those seven lines. They were re-added, marked
`"reconstructed"`, from each run's own log (`logs/looks/<id>.log`, first line "registered test <id>,
look #1"; time = the log file's creation time). No look was repeated. Looks are now committed as soon
as they are made.

| id | registered | already known at registration | what registration guarantees |
|---|---|---|---|
| shock_depth_v1, favourite_tilt_v1, late_certainty_v1 | 2026-10-26, before any run on the cross-category universe | election markets of every period (from the earlier exploration) — the **elections** holdout is not clean | parameters fixed before any discovery or holdout run on the other 10 categories |
| forward_v1 | 2026-10-26, before recording started | nothing about the recorded signals (they are recorded live, before outcomes exist) | a genuine forward test; one look, after the stopping rule |
| night_r2_v1 | 2026-10-26, after the runoff, before its BU-web files were published | runoff outcome, all 27 state winners and totals, market prices, Poliwave's national raw-count path, an earlier reconstructed runoff nowcast | model, τ and trading rules cannot be retuned on the section-level timing. **Not a blind holdout.** Its config header calls it a "frozen test", which is accurate in that sense only; the header cannot be edited without breaking its registered hash. |
| night_gov_r2_{ac,am,df,es,rn,to}_v1 | 2026-10-26, after the runoff, before its BU-web files were published | each race's winner (and, when the configs were generated, the final market price); not the count path, not the intra-night price path; the method was never developed on governor races | proxy replays: settings copied unchanged from night_r2_v1 (chosen on the presidential first round), so they test the *method* on six races it was not built on. Pre-specified, not blind. |
| hist_2022_r1_v1, hist_2022_r2_v1 | 2026-10-28, before the 2022 section files were downloaded | both outcomes and totals (public since 2022); not the section-level count path; the method was never developed on 2022 | model-only replays (no market prices survive): settings copied unchanged from night_r2_v1. Pre-specified, not blind. |
| hist_2018_r1_v1, hist_2018_r2_v1 | 2026-10-28, same | as above | **cannot be run as specified**: the 2018 BU-web files have no receipt time (`DT_BU_RECEBIDO` absent; only `DT_ENCERRAMENTO`, when the ballot box closed). Replaying on closing times would invent a reporting order, so no look is taken and no substitute is used. Found from the file header before any count data was examined. |

**Data source for the seven runoff replays (recorded 2026-10-28, before any of the seven looks).**
The registrations assumed TSE's BU-web files for the runoff, which TSE had not published. The replays
instead use the same per-section facts taken from TSE's per-ballot-box files (arquivo-urna: each
section's `aux` receipt time and decoded votes), as published in rows by a third party under MIT
(github.com/cicerohellmann/eleicao-2026, `data/bu/`), converted by `bweb.from_arquivo_urna` into the
same layout (`data/raw/tse_open/bweb/2026_2t/`, provenance in `SOURCE.json`). Checks done before the
looks: on the first round, the `aux` time equals BU-web's DT_BU_RECEBIDO for 39 of 40 random sections
(the exception was re-sent two days later); no runoff section was re-sent; at 24 snapshots of TSE's
runoff state files the section counts match within 1; the national Lula share matches TSE's feed
within 0.05 pt; totals give Lula 51.785% two-party (official 51.79%) and RN governor 49.23% (live feed
49.23%). Configs, model, τ and trading rules are unchanged. When TSE publishes BU-web, the comparison
of the two sources will be reported; the looks are not repeated.

## US midterms 2026 (`configs/us2026_*_v1.toml`): forward test, registered 2026-10-28

Seven races (CA governor; TX, OH, GA governor and Senate), registered at commit `ed32ee9`, before any
2026 general-election count exists. Settings were chosen on certified 2020–2024 results only
(`docs/us_midterms.md`); an independent review before registration led to excluding Alaska (its markets
are candidate-specific under top-four ranked-choice voting; the model compares party totals) and to
robustness fixes in the live reader. Read, once, after the counts are final:
1. per race, the settle lead (model vs Polymarket first staying ≥ 0.9 on the winner), measured from 1%
   of expected votes counted;
2. calibration of the 90% interval along the count;
3. the 30-minute catch-up slope, pooled over the seven markets.
The Ohio prior file (MIT Election Lab county returns, downloaded by hand because the Ohio SoS site blocks
scripts) is input data, not a setting; if it is not in place before polls close, the Ohio races are
reported as not run rather than scored later. Parser fixes forced by the live feed format on the night are
allowed and will be listed here; model settings cannot change.

### US model versions and which one is primary (written 2026-10-29, before any 2026 result)

Four versions per race are registered: v1 (past results only), v2 (polling average as a prior on the
statewide swing), v3 (v2 with poll error set by the number of polls, from 538 history 2016–2022, and the
count-model uncertainty terms scaling in with the count instead of stacking on the poll prior), and for
Ohio v3/v4 (the same steps one version later, because Ohio's baseline arrived after v2).
**Primary test: v3 (Ohio: v4).** v1 and v2 are reported alongside as secondary results and will not be
substituted for the primary after the outcome. Moving from v2 to v3 changed only the size of the
uncertainty, not its centre (the polling average); win chances moved away from 0.5 toward the side the
polls favour, in some races away from the market. Known risks, uncorrected by design: polls overstated
Democrats by about 1.4 points on average in 2016–2022; polling errors are correlated across states; the
error model was checked on one held-out year (2022: 70% within 1 sd, 92% within the 90% band).
Also added to the US forward test after the first registration: SC Senate, SC governor and VT governor
(registered at `57b2e50`, before any 2026 result).
Count-time check of the primary (written 2026-10-29, before any 2026 result; `analysis/us_polls.py calib`):
Georgia 2022 governor and Senate, certified results by vote type, 40 simulated nights each with early and mail
votes posted first, poll priors from the same averaging rule on 538's 2022 polls. 90% coverage for v1/v2/v3 was
100% at 1, 5, 10, 25, 50 and 75% counted; mean margin error (points) v1/v2/v3: 6.3/3.6/2.0 at 5% counted,
7.0/4.9/3.5 at 25%, 5.8/4.6/3.6 at 50%. Two races only, so coverage is coarse; v3's mean |z| rises to 1.09 at
50% counted. The primary stays v3 (Ohio v4).
Secondary v4 (Ohio v5), written 2026-10-29 before any 2026 result: v3 with expected vote per county from each
county's own 2022/2020 midterm drop-off (the one change that won forward in time in `docs/us_model_analysis.md`).
On the Georgia 2022 replays it was not better than v3 (margin error 2.12 vs 1.98 at 5% counted, 3.24 vs 3.59 at 50%,
1.10 vs 0.23 at 75%; turnout error +21.5% vs +1.5%, because Georgia's 2018 midterm turnout was unusually high),
so v3 (Ohio v4) stays primary; v4 runs and is scored as a secondary model.
Added 2026-10-29, before any 2026 result: NC Senate, FL Senate and FL governor (v1–v3; official NCSBE and
Florida DOS election-night feeds, parsers tested against 2022 certified totals). Same rules as the other races;
v3 is their primary. PA, WI, NH and ME were left out because their official feeds block automated access.
Added 2026-10-30, before any 2026 result: PA governor, MN Senate and governor, NE Senate and governor, NM Senate and
governor, AZ governor (v1–v3; NM governor v1–v2, no polls). Official feeds; parsers reproduce certified 2022/2024
totals (NE parser tested on 2020 Senate; NE and AZ priors from MEDSL). Same rules; v3 primary (NM governor: v2).
US House, separate study, added 2026-10-30 before any result: 63 districts in TX, FL, NC, SC, OH, GA, AZ
(`configs/us2026_house/`, v1 = 2024 president on the 2026 lines, v2 = plus the generic-ballot swing), run as their
own process (40 most competitive live, the rest replayable from the archive). Replay coverage of the 90% band at
5/25/50% counted ≥ 0.87 except the three all-Maricopa districts (AZ-01/04/08), which under-covered; recorded here
before any result. Scored separately from the Senate/governor test.
Excluded 2026-10-30, before any 2026 result, after a one-poll check of every configured feed from the
recording server: South Carolina (its results host, enr-scvotes.org, answers the server with an empty HTTP 202
bot challenge, the same as Iowa; not circumvented) and Alaska (the server cannot connect). The registered SC
Senate and SC governor configs and the SC House district configs therefore have no count and are not scored;
Alaska had no model.
All other feeds returned data; Georgia's host is reachable and its 2026 general election is picked up once it is
listed.
Correction, 2026-10-30, still before any result: Alaska is reachable. The check above ran without the
recorder's certificate bundle (SSL_CERT_FILE, needed because Alaska's server omits an intermediate certificate);
with it the server answers normally, so the Alaska feed is recorded again. Only South Carolina is excluded.
Ohio, 2026-10-30, before any 2026 result: liveresults.ohiosos.gov answers the recording server with HTTP 403 since
about 18:24 UTC (it served the same files to the server earlier that day; a residential connection still gets 200).
Not circumvented; a request to allow the server was sent to Media@OhioSoS.gov, and the recorder keeps polling every
60 s with backoff. Scoring, fixed now: if the official Ohio feed records the count on the night, the registered Ohio
official-feed configs (Senate and governor, all versions, and the Ohio House districts) are scored as registered;
if it does not, they are not scored. In that case the civicAPI Ohio configs (oh-sen-civic, oh-gov-civic), if they
pass validation against Ohio's certified county results and are registered before any result, are scored as a
separate secondary-source set, never pooled with the official-feed test.

## Hungary 2026 (`configs/hu2026_v1.toml`): not pre-registered

Settings chosen on the 2022 count only (9 official night publications kept by the Internet Archive) and
written at 2026-10-28 18:59Z; one scoring run on the 2026 count at 19:01:53Z; nothing changed afterwards.
Before writing the settings, the 2026 market resolutions, the 02:05 national list summary and one district
row had been seen, and the config was first committed after scoring, so the result is **not
pre-registered**. Only the header comment was later corrected (it had said "pre-specified"); the sha256 of
the file as scored is in that header. Results: district projection held (100% interval coverage, seats
within one of the final from 22:30); list-vote model did not early; market Brier 0.007 vs model 0.079; the
frozen trading rule lost $16,618 on $78,364 of real fills (`docs/hungary.md`, `docs/edge_audit.md`).

## Mexico 2024 (`configs/mx2024_v1.toml`): not pre-registered

Settings (τ = 0.01) were chosen on the 2018 count only and written to the config before the 2024 scoring
run, but one exploratory 2024 run with τ = 0.015 was made while testing the code before the freeze, and
the config was first committed after scoring. Labelled **not pre-registered**. Result: the projection
beat the raw count from 5% counted, but the 90% interval covered the final share in only 10% of minutes
(1–95% counted): the model's uncertainty does not transfer from 2018 to 2024. No trade signal fired:
Polymarket was ≥ 0.9 on the winner about 50 hours before counting began (`docs/mexico.md`).

## Israel 2026 (`configs/il2026_v1.toml`): not pre-registered

Settings were chosen on the 2021 and 2022 official counts and written to the config at 2026-10-28
16:53 UTC, before the scoring script first read the 2026 count (attested by the agent's log, not by git).
The scoring script then got two data-handling fixes after its first run (ballot-box totals taken from the
polling-station list; prices bounded by the order book after one stale 0.999 trade), and a 1%-gate
variant was computed after the first result. The config was first committed after scoring, so the Israel
results are labelled **not pre-registered**: settings chosen on earlier elections, scored on 2026 once
with post-run data fixes. The only 2026 count available is third-party (private research use), so no
2026 count data or derived per-minute series is published; see `docs/israel.md`.

## Recording log (forward_v1)

Gaps in live recording, from the host's power log (`pmset -g log`) and the record timestamps. A gap
depends on whether the laptop was awake, not on any market outcome, so it costs signals, not validity.
Signals whose check window fell inside a gap were never recorded and are not back-filled.

| gap (UTC) | cause |
|---|---|
| 2026-10-27 ~03:08–05:40 and ~05:40–10:18 (≈ 7 h in total, with brief wakes) | host on battery; `caffeinate -is` only prevents sleep on AC power |
| 2026-10-27 14:13:29–14:13:38 (9 s) | handoff from the laptop to the always-on Oracle VM (`deploy/`): recorder stopped, `data/live/` copied, recorder started; same code commit (`0531d5c`), same spec |

Rate check, 2026-10-27 (counts only; no outcome, price or return was examined): in ~17.7 h of net
recording, 24 mentions late-certainty signals, ≈ 227/week vs the ~150/week the stopping rule assumed, so
1,800 would be reached around late December, before the 2027-02-28 deadline. Recorded in a pre-midterm
week heavy with rally "mentions" events; the rate is expected to fall after 2026-11-03.
