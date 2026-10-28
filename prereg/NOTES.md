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
