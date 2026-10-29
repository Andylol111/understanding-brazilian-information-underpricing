# State subreddit sentiment vs 2026 polling misses: terms review and status (2026-10-29)

Planned: a forward-only experiment. Record hourly per-subreddit aggregates (volume, unique-author count,
VADER and a transformer sentiment score, candidate/party mentions) for each state's main subreddit and up to
three city subreddits from 2026-10-30 to 2026-11-04 via the Reddit Data API. Then test after certification
whether the sentiment shift toward a party predicts the sign of the state's poll-average miss. Not a model input.

**Status: not built, not run, no Reddit request made.** The terms below rule out the forward collection,
the public export and the subreddit verification calls for this use case. Per the project rule ("if any
planned step conflicts, don't do it"), none of the following exist: `src/brunder/reddit.py`, the
`brunder reddit collect|export` CLI, `configs/reddit_subs.toml`, `configs/us2026_reddit_v1.toml`,
`deploy/brunder-reddit.{service,timer}`, `site/data/us2026/hub/reddit.json`.

## Sources read (2026-10-29)

- Data API Terms, effective 2023-06-19, revised 2026-07-20: <https://redditinc.com/policies/data-api-terms>
- Developer Terms, effective 2024-09-24, revised 2026-03-24: <https://redditinc.com/policies/developer-terms>
- Responsible Builder Policy, updated 2026-10-05: <https://support.reddithelp.com/hc/articles/42728983564564>
- Reddit Data API Wiki (rules, rate limits), updated 2026-05-11:
  <https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki>
- Reddit for Researchers Program (RFR), updated 2026-10-05:
  <https://support.reddithelp.com/hc/articles/49381918834964-Reddit-for-Researchers-Program>

## Obligations

Access and identity
- Get explicit approval before accessing any Reddit data through the API. Merely registering an OAuth app
  is not enough (RBP "Approval is required"). Keep contact details accurate (DAT 1.3, DT 1.3).
- Authenticate with a registered OAuth token; never mask the OAuth identity or User-Agent (DAT 2.8). The
  User-Agent must be `<platform>:<app ID>:<version> (by /u/<username>)` and must be honest (Wiki).
- One app per use case; never misrepresent how or why data is accessed (RBP; DT 4.2). Keep credentials
  secret and never share them (DT 1.4, 7.4).

Rate limits
- Free tier: 100 queries/min per OAuth client id, averaged over 10 minutes. Watch `X-Ratelimit-Used`,
  `-Remaining` and `-Reset`. Never circumvent or exceed the limits (Wiki; DAT 3.2). Research beyond the
  rate limits, or any use the terms do not expressly permit, needs a separate agreement (DAT 3.1; DT 4.1).

Use restrictions
- No commercial use or monetisation, including of data derived from Reddit data (DT 4.1; DAT 3.2; RBP).
- No training of ML/AI models on User Content or Reddit data without permission (DAT 2.4, 3.2; DT 4.2, 5.2; RBP).
- Do not derive or infer sensitive characteristics of users, political affiliation named explicitly.
  Never re-identify or de-anonymise users (RBP "Zero Tolerance").
- Do not share Reddit Services and Data with any third party (DT 7.2). "Reddit Services and Data" includes
  data derived from it (DT 2.2). Do not redistribute or syndicate it (DT 4.2). Do not use it for surveillance (DT 4.2).
- Retain only what the approved use case needs and delete the rest at once (DAT 3.2; DT 7.3). Delete
  everything, derived data included, on termination (DAT 6; DT 8.2).

Deletion handling
- Delete content that was deleted on Reddit, along with related data. Delete user IDs and author references
  when an account is deleted. Reddit recommends purging stored data within 48 h. Keeping deleted content in
  any form, even "disassociated, de-identified or anonymized", violates the terms (Wiki; DT 3.3).

Research specifically
- "Any research that uses Reddit data collected outside of the RFR Program is in violation of this policy"
  (RBP, Researchers).
- RFR is open only to sponsored researchers at accredited universities; rare exceptions exist for
  non-profit or government researchers. Applications need an IRB or ethics approval and a sponsor.
- RFR access is through BigQuery Analytics Hub. The data are 5 years of history with a 6-month delay, and
  deleted content is excluded.
- Under RFR: hold no offline copies beyond what the project strictly needs, and delete all data and
  derivatives at the end. Do not redistribute data. Make sure findings contain no deleted data before
  publishing, and send Reddit a preprint.

## Conflicts with the plan

| planned step | conflict | outcome |
|---|---|---|
| Hourly API collection 10-30→11-04 (and backfill from 10-23) for a research question | RBP: research on data collected outside RFR is a violation; approval is required before any API access. RFR has no live API, so forward collection cannot be done inside it | **Not built** |
| Verify subreddits exist and are public (`/about`) for `configs/reddit_subs.toml` | Same: these are API calls for the research use case | **Not done**; no list was written, since an unverified list would not meet the brief |
| `brunder reddit export` → public `site/data/us2026/hub/reddit.json` | DT 7.2/2.2: derived data may not be shared with third parties; RFR forbids redistribution | **Not built** |
| Per-item party-stance scoring (transformer stance model) | RBP forbids inferring users' political affiliation. Scoring each comment for party stance comes close even if only hourly aggregates are stored | Risk; would need Reddit's explicit sign-off in any approval |
| Store only aggregates; raw responses never persisted | Consistent with the retention rules. The Wiki still treats retained aggregates of later-deleted items as a gray area ("even if … anonymized") | n/a while not collecting |
| `configs/us2026_reddit_v1.toml` pre-registration | No conflict in itself, but it would register a sample that cannot be collected | **Not written** |

## Routes that stay compliant

1. **RFR, retrospective.** The 2026-10-30→11-04 window should appear in the RFR dataset about 6 months
   later (around May 2027), with deletions already removed. The hypothesis could still be pre-registered
   before anyone looks at those data. This needs an accredited-university PI, an IRB letter and a sponsor.
   Inside BigQuery, the text-based scores (VADER or a transformer) would have to be computed without
   exporting text. Whether RFR allows UDFs or model inference there is unverified.
   - Only findings are published. RFR-derived rows and aggregates never go into `site/` or `data_public/`,
     or to anyone outside the approved team.
   - Any aggregates kept offline must use the schema planned for the forward series: subreddit × hour;
     posts; comments; unique-author count; mean and sd of compound sentiment, overall and per race keyword
     set; mention counts. Historical and forward series then stay comparable.
2. **Written approval for this exact use case** (Reddit support form, "researcher" or "developer") that
   covers live API collection, rate, retention and publication of aggregates. If granted, the collector can
   be built as specified: app-only OAuth, ≤60 req/min, aggregates only, raw cache deleted after each hour.
   Under this route the transformer scorer must be inference-only. Use a permissively licensed checkpoint
   fine-tuned on non-Reddit data, and never train on Reddit content (DAT 2.4).
