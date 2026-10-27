# What each registration does and does not guarantee

`registry.jsonl` is append-only and records hashes; this file records what was already known when each
spec was registered, so readers can judge how out-of-sample each result is.

| id | registered | already known at registration | what registration guarantees |
|---|---|---|---|
| shock_depth_v1, favourite_tilt_v1, late_certainty_v1 | 2026-10-26, before any run on the cross-category universe | election markets of every period (from the earlier exploration) — the **elections** holdout is not clean | parameters fixed before any discovery or holdout run on the other 10 categories |
| forward_v1 | 2026-10-26, before recording started | nothing about the recorded signals (they are recorded live, before outcomes exist) | a genuine forward test; one look, after the stopping rule |
| night_r2_v1 | 2026-10-26, after the runoff, before its BU-web files were published | runoff outcome, all 27 state winners and totals, market prices, Poliwave's national raw-count path, an earlier reconstructed runoff nowcast | model, τ and trading rules cannot be retuned on the section-level timing. **Not a blind holdout.** Its config header calls it a "frozen test", which is accurate in that sense only; the header cannot be edited without breaking its registered hash. |
| night_gov_r2_{ac,am,df,es,rn,to}_v1 | 2026-10-26, after the runoff, before its BU-web files were published | each race's winner (and, when the configs were generated, the final market price); not the count path, not the intra-night price path; the method was never developed on governor races | proxy replays: settings copied unchanged from night_r2_v1 (chosen on the presidential first round), so they test the *method* on six races it was not built on. Pre-specified, not blind. |

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
