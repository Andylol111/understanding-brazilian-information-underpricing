# State subreddits and the 2026 polling miss (`brunder reddit`)

This is a forward-only experiment. The question is whether the way a state's own subreddits talk about each
side of a race in the last days before 2026-11-03 predicts which way that race's final poll average misses
the certified result.

- It is research only. It is never an input to any `us2026_*` model config.
- It was pre-registered in `configs/reddit_us2026_v1.toml`, which holds the hypothesis, keyword sets,
  selection, metric, sample and tests.
- Collection runs under the Reddit API approval the user obtained for this use (user-stated 2026-10-29;
  approval correspondence not in the repo). The Data API Terms and Developer Terms still apply in full.
- **Nothing Reddit-derived is published.** No site export, no `data_public/` files, no sharing; only
  findings are published.
- There is **no user-level inference.** Every score is about an item's text toward a named candidate or
  party, and only per-hour aggregates are kept.

```sh
uv run brunder reddit verify                                     # check candidates, select subreddits
uv run brunder reddit collect                                    # every finished hour not yet recorded
uv run brunder reddit collect --sub texas --max-hours 1          # smoke test: one subreddit, one hour
uv run brunder reddit bench [--absa]                             # scorer throughput / peak RSS (synthetic text)
```

Tests: `tests/test_reddit.py`. They are behavioural and use a mocked Reddit. They cover:
- keyword matching;
- aggregation and the per-author cap;
- pagination stopping at the hour floor;
- idempotency;
- the listing-cap floor;
- selection;
- pacing, rate-limit headers and 429 backoff;
- the absence of usernames, text, ids and credentials in every output file and log line.

## Terms and how the collector complies (read 2026-10-29)

Sources:
- [Data API Terms](https://redditinc.com/policies/data-api-terms) (revised 2026-07-20)
- [Developer Terms](https://redditinc.com/policies/developer-terms) (revised 2026-03-24)
- [Responsible Builder Policy](https://support.reddithelp.com/hc/articles/42728983564564) (2026-10-05)
- [Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki) (2026-05-11)
- [Reddit for Researchers](https://support.reddithelp.com/hc/articles/49381918834964-Reddit-for-Researchers-Program) (2026-10-05)

| obligation | source | here |
|---|---|---|
| Explicit approval before any API access; one app per use case; never misrepresent how or why data are accessed | RBP; DT 4.2 | User-stated approval for this use. One app, one use case |
| Registered OAuth token; never mask the OAuth identity or User-Agent | Wiki; DAT 2.8 | App-only OAuth (`client_credentials`). UA `<platform>:org.brunder.us2026reddit:v<version> (by /u/<REDDIT_USERNAME>)`; the version is bumped on every change |
| Keep credentials secret, never share them | DT 1.4, 7.4 | Credentials live only in `/etc/brunder/reddit.env` (root:brunder 0640) or `~/.config/brunder/reddit.env` (0600). They are never in the repo, unit files, output or logs; error messages name the missing key only |
| 100 QPM per client id (10-min average); never circumvent or exceed limits | Wiki; DAT 3.2 | ≥ 1 s between requests (≤ 60/min). `X-Ratelimit-Used/-Remaining/-Reset` read on every response; when < 20 % of the window is left, wait for the reset. Exponential backoff on 429/5xx (`Retry-After` honoured). One token reused until 5 min before expiry. A lock file prevents concurrent runs |
| No ML/AI training on User Content | DAT 2.4, 3.2; DT 4.2, 5.2; RBP | Inference only. VADER is a fixed lexicon; the optional transformer is a published checkpoint, never fine-tuned here |
| No inferring sensitive characteristics of users (political affiliation is named); no re-identification | RBP | Scores are per item toward a named target. They are reduced at once to subreddit-hour aggregates; nothing is linked to or stored about any account |
| Retain only what the use needs; delete the rest at once | DAT 3.2; DT 7.3 | Listings are held in memory for one subreddit at a time and dropped after aggregation. No raw cache exists. Stored: counts, unique-author counts, engagement sums, sentiment mean/sd per target, subscriber and active-user counts per day |
| Remove deleted content; deleted data may not be retained "even if … anonymized" | Wiki; DT 3.3 | No content, ids or authors are stored, so nothing can be re-linked. An hour is aggregated once, from what exists about 2–60 min after it ends. Items deleted later still count in that hour's totals (see Risks) |
| No commercial use, no monetisation, including of derived data | DT 4.1; DAT 3.2 | Non-commercial research, not monetised |
| Do not share Reddit Services and Data, which includes data derived from them; do not redistribute | DT 7.2, 2.2, 4.2 | Output stays in the gitignored `data/processed/reddit/us2026/`. No `brunder reddit export`; nothing goes to `site/` |
| Delete all data, including derived data, on termination or on Reddit's request | DAT 6; DT 8.2 | `rm -r data/processed/reddit/` on the VM and laptop |
| Reddit trademarks / endorsement | DAT 4 | Findings only state that the data came from the Reddit Data API |

**Reddit for Researchers (future history).** Any longer history will come through Reddit for Researchers
(RFR), which works through BigQuery. Only aggregates may leave it. RFR-derived data are **never
published**: only findings are, after the pre-publication deletion check and the preprint to Reddit that
the programme requires. Any RFR aggregates must use the `hourly.jsonl` schema below so that historical and
forward series are comparable.

## Selection (`brunder reddit verify`)

`configs/reddit_subs.toml` lists 183 candidates for the 45 states with a 2026 Senate or governor race: the
state subreddit plus city candidates. Names were chosen from knowledge, since no credentials were available
on 2026-10-29.

For each candidate, `verify` calls `/about` and `/new` (100 posts), which is about 370 requests (~6 min).
Per state it then selects:
- the state subreddit, if it is public, not NSFW and not quarantined;
- the **3 city candidates with the most subscribers** among those that pass the same checks and average
  **≥ 3 posts/day** over the last 7 days.

The minimum volume is set at 3 posts/day because a city subreddit below that yields almost no race
mentions in any window. The state subreddit is kept at any volume so that every state has a series.
Results, including type, subscribers, posts/day and `selected`, are written to `subs.json`. That file
freezes the selection: `collect` only verifies names missing from it.

## Collection (`brunder reddit collect`, hourly)

For each selected subreddit, `collect` builds every finished, unrecorded hour since 2026-10-23T00:00Z, up to
2026-11-04T12:00Z. It pages `/r/<sub>/new` and `/r/<sub>/comments` (limit 100) only until it passes the
oldest hour needed.

- **Idempotent** per subreddit-hour: the keys already in `hourly.jsonl` are skipped.
- **Backfill is capped.** Reddit listings stop at about 1000 items. For a busy subreddit, the first run
  reaches back only a few hours, sometimes a day. Hours older than the oldest item returned are not written
  and are never retried (`state.json` floor). The summary reports them as `hour(s) beyond the listing cap`.
- **Daily metadata.** Once per UTC day per subreddit, `/about` subscriber and active-user counts go to
  `subs_daily.jsonl`.
- **Cost.**
  - The first run is the whole backfill, about 1 h: on the order of 10 pages × 2 listings for each of
    ≤ 180 subreddits, at 1 request/s.
  - After that, a run takes 2–4 requests per subreddit, about 5–8 min per hour.

Row schema, one per subreddit × UTC hour:

```
hour, subreddit, state, role (state|city), posts, comments, authors (unique, deleted excluded),
engagement {score_sum, post_comments}, vader_all {n, mean, sd, wmean, amean},
targets {"party/D"|"party/R"|"<race>/dem"|"<race>/rep"|"<race>/dem_cand"|"<race>/rep_cand":
         {n, authors, vader {n, mean, sd, wmean, amean}, absa {…} if enabled}},
scorers, lag_h (hours between the hour's end and collection), spec
```

- `wmean` is score-weighted, with weight 1 + ln(1 + max(score, 0)). Scores are taken at collection time,
  so backfilled hours carry older, higher scores; `lag_h` records the lag.
- `amean` is the mean of per-author means. Each author counts once per subreddit-hour; a deleted author
  counts as its own author per item. This is the only per-author cap possible without storing identities.

Keyword rules are in `[keywords]` of the pre-registration:
- full name;
- first + last name;
- registered nicknames;
- last name alone, unless it is a common word or a name shared with another well-known person, in which
  case only after a title ("Sen. Collins", "Governor Moore");
- party terms, which count toward the dem side only when that side is a Democrat (not the MT/NE
  independents).

`overview.json` lists the Kentucky Senate candidates (Booker, Barr) under `la-sen` and McConnell under
`ky-sen`. The pre-registration assigns them to `ky-sen` and leaves `la-sen` party-only. Neither race is
polled, so neither is in the sample.

## Sentiment

- **VADER** (vaderSentiment 3.3.2, MIT) is the primary scorer. It scores the whole post (title + body) or
  comment and is not target-aware, which is why the targets are defined by keyword. It is fast: ~28 k
  short items/s, ~5.6 k long items/s on one laptop core. Peak RSS of the CLI process is 159 MB.
- **Target-based transformer (optional, `--absa`).**
  - Checkpoint: `yangheng/deberta-v3-base-absa-v1.1` at revision 10c9dff3. MIT licence, per the model
    card and the HF API (checked 2026-10-29). It is fine-tuned with PyABSA on public ABSA benchmarks; the
    card does not list them, and we have not verified that they contain no Reddit data.
  - It scores (text, matched term) as P(positive) − P(negative), batched, truncated to 512 tokens.
  - `HFTarget` takes any sentence-pair checkpoint with positive/negative labels, so a ModernBERT
    checkpoint can be added later with `--absa <repo>@<rev>`.
  - `--absa-budget` (default 1200 s per run) stops scoring when spent; rows then carry `absa: null`.
  - **Not enabled on the VM.** The weights alone are 738 MB (fp32), more than the unit's `MemoryMax=300M`.
  - It could not be benchmarked locally either, because the laptop disk was full and the download failed.
  - It falls back to **VADER-only**, as planned. To try it, raise the memory cap on a copy of the unit and
    first measure it under the VM's caps:
    `sudo systemd-run --uid=brunder -p CPUQuota=10% -p MemoryMax=1500M --wait --pipe -p WorkingDirectory=/srv/brunder/repo /srv/brunder/.local/bin/uv run --frozen --extra absa brunder reddit bench --absa`.
    Enable it only if items/s × 3600 comfortably exceeds the target mentions per hour that `collect`
    reports.

## Risks and limits

- **Deleted items.** Items deleted after their hour was aggregated stay in that hour's counts and means.
  Nothing traceable is kept, but under a strict reading of the Wiki ("even if … anonymized") Reddit could
  still ask for deletion. If it does, delete `data/processed/reddit/`.
- **Backfill floors differ** between busy and quiet subreddits, so the "first week" baseline is short for
  large subreddits. The pre-registration's shrinkage toward the state mean and its `min_author_hours`
  exclusion handle this; the `short_window` secondary checks it.
- **Keyword noise.** "democratic" also matches non-party uses, and names may be mentioned in other
  states' subreddits; only the state's own subreddits are matched against its races.

## Install on the VM (integrator)

```sh
sudo install -d -m 0750 -o root -g brunder /etc/brunder
sudo install -m 0640 -o root -g brunder /dev/stdin /etc/brunder/reddit.env <<'EOF'
REDDIT_ID=...
REDDIT_SECRET=...
REDDIT_USERNAME=...
EOF
sudo -u brunder git -C /srv/brunder/repo pull
sudo -u brunder sh -c 'cd /srv/brunder/repo && ~/.local/bin/uv sync --frozen'
sudo cp /srv/brunder/repo/deploy/brunder-reddit.service /srv/brunder/repo/deploy/brunder-reddit.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start --no-block brunder-reddit.service   # first run now: verify + backfill (~1 h)
journalctl -u brunder-reddit -f                  # counts only: rows, requests, "not selected", failed
sudo systemctl enable --now brunder-reddit.timer # hourly at :05 until 2026-11-04 12:05 UTC
systemctl list-timers brunder-reddit.timer
```
