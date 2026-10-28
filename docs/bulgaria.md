# Bulgaria: presidential runoff, 2026-11-01 (`bg2026r2`)

Iliana Iotova (A) vs Andrey Gyurov (B), second round of the presidential election. The model is frozen in
`configs/bg2026r2_v1.toml` (forward test once registered before polls close). **No dashboard page is published**
(`site/data/bg2026r2/` doesn't exist). The official results site can't be read by any script, so the count can only
be captured by a person saving pages in a browser. The model then runs on those saved files afterwards.

Code: `src/brunder/bulgaria.py` (page and archive parsers, MIR prior, nowcast, `brunder bulgaria` command),
`analysis/bg_calibrate.py` (settings from the 2016 and 2021 runoffs), `tests/test_bulgaria.py` (real CIK
fixtures in `tests/fixtures/bulgaria/`).

## Date and poll close (verified)

- **Runoff on Sunday 1 November 2026.** On 27 October the CIK (Central Election Commission) announced the final
  first-round result and set the runoff between the top two tickets: Iotova/Vălchev 528,951 and Gyurov/Kandev
  486,232 valid votes. Source: BNR, 28 Oct 2026,
  https://bnrnews.bg/en/post/549167/election-commission-sets-1-november-for-second-round-of-presidential-election.
  The Constitution (art. 93) requires the runoff within seven days of the first round.
- **Polls close at 20:00 local time = 18:00 UTC.** The Electoral Code sets voting from 07:00 to 20:00, and
  Bulgaria has been on EET (UTC+2) since daylight saving time ended on 25 Oct. Sections abroad close at 20:00 in
  their own country, so the Americas close after 01:00 UTC on 2 Nov.
- The Kalshi event ticker `KXBULGARIAPRES-26NOV15` carries the event's *expected expiration* (2026-11-15), not the
  vote date. The market's rules say "If Iliana Yotova wins the 2026 Bulgarian presidential election…", and its
  settlement source is the CIK.

## Blocker: results.cik.bg cannot be read by scripts

On 2026-10-28, every request to `results.cik.bg` and `www.cik.bg` was answered with a Cloudflare managed challenge
(`HTTP 403`, header `cf-mitigated: challenge`). This held for a home connection, for the project VM
(oracle-slotbank, one curl), with an honest user agent, and for every path tried: `/`, `/pvr202610/index.html`,
`/pvr202610/rezultati/…`, `/pvrns2021/tur2/csv.zip`. Only `robots.txt` (`# Hi, robot!`, no disallow rules) and the
favicon came through. Getting past the challenge would mean evading bot protection, which project rules forbid,
so there is **no recorder** and **no live exporter**. The Bulgarian open-data portal `data.egov.bg` also returns
HTTP 403 to scripts.

Two consequences:
1. On the night, the count over time exists only if a person saves the result pages in a browser (below).
2. Earlier elections were read from Internet Archive captures of CIK files (official data, archived by the
   Internet Archive's own crawler). These were used for calibration and as test fixtures. Thirteen of the 32
   first-round MIR pages have no capture, so the 2026 first-round prior must also come from a browser download.

Bulgarian election results are official documents of a state body and are not protected by copyright (Copyright
and Neighbouring Rights Act, art. 4(1)). The CIK publishes its open-data archives under the EU open-data
directive (see the archive readme).

## What a person must download (in a normal browser)

Use an ordinary browser session. If Cloudflare shows its check, let it finish as a normal visitor would. Don't
script, automate or use browser extensions that reload pages. Keep the pace modest; one round of saves every 15
minutes is plenty.

Save pages as **"Save Page As… → Webpage, HTML Only"** (Chrome/Edge) or **"Web Page, HTML only"** (Firefox).
File names don't matter: every page carries the CIK's own stamp "Данни към HH:MM на DD.MM.YYYY при обработени
X% СИК протоколи" (data as of local time, X% of polling-station protocols processed). The parser reads that
stamp, the page's own region (MIR) and the vote table. Saving the same page twice does no harm.

### 1. First round (the prior), any time before polls close on Sunday

Option A (preferred): the first-round open-data archive.

| File | URL | Contents | Put it at |
|---|---|---|---|
| `export.zip` | https://results.cik.bg/pvr202610/opendata/index.html → "изтегли резултати в машинночетим вид" (`…/pvr202610/opendata/export.zip`) | Section-level final protocols (CSV, `;`): `readme`, `cik_candidates` (ballot no. → candidate pair), `sections` (section code, settlement), `protocols`, `votes` (votes per ballot no. per section protocol; paper, machine and abroad protocols are separate rows and their sum is the official result) | `$BRUNDER_DATA/raw/bulgaria/r1/export.zip` |

The archive is built from the database the CIK adopted with its final-result decision, so it appears only after
that decision (27 Oct for this round; for the 2021 runoff the archive followed the decision two days after the
vote). If the opendata page has no archive yet, use option B.

Option B: the 33 first-round result pages (final, "обработени 100.00%").

| Pages | URL | Contents | Put them in |
|---|---|---|---|
| national | https://results.cik.bg/pvr202610/rezultati/index.html | Votes per candidate pair, "Не подкрепям никого" (support no one), stamp | `$BRUNDER_DATA/raw/bulgaria/r1/` |
| 31 MIRs + abroad | https://results.cik.bg/pvr202610/rezultati/01.html … `31.html`, `32.html` (abroad) | The same, per multi-member electoral region (MIR); 23-25 = Sofia city, 16/17 = Plovdiv city/oblast | `$BRUNDER_DATA/raw/bulgaria/r1/` |

### 2. Runoff night (the count over time), from about 18:30 UTC until 100%

The runoff results will be published under a new path. The CIK's home page https://results.cik.bg/ links it:
likely `https://results.cik.bg/pvr202610/tur2/…` or a new `pvr202611/` folder (2021 used `pvrns2021/tur1/` and
`pvrns2021/tur2/`). From its "Резултати" (results) page:

| Pages | Contents | Put them in |
|---|---|---|
| `rezultati/index.html` (national) | National votes for the two pairs, stamp, % processed | `$BRUNDER_DATA/raw/bulgaria/r2/<HHMM>/` |
| `rezultati/01.html` … `31.html`, `32.html` | The same per MIR (the model's units) | `$BRUNDER_DATA/raw/bulgaria/r2/<HHMM>/` |

- Cadence: save one full round of 33 pages each time the national page's stamp changes, or every 15 minutes.
  The CIK refreshes the pages in batches, and the stamp only changes when a batch is published (the archived final
  first-round pages are stamped 10:00 and 15:00 on 26 Oct). Each round goes in its own folder, named after the
  clock time (e.g. `r2/2115/`). Keep going until the national page shows 100.00%. In the first round that
  happened the next morning: BNR reported 100% counted at 09:35 local on 26 Oct.
- If 33 pages per round is too much: save the national page every round and the MIR pages as often as you can.
  The model takes each MIR's latest page at every stamp, so missing MIR rounds make the model lag but never look
  ahead. With the national page alone, only the raw line exists and no model can run.
- Pages saved after the night show only the final state. A count over time can't be recovered later.

**Does the CIK publish intermediate machine-readable files during the night? No.** Here is what exists during
the count:
- The HTML result pages above, rewritten in batches, each with its own stamp. These are the only over-time
  record.
- One page per polling-station protocol, appearing once that protocol is processed
  (`protokoli/<election>/<MIR>/<section>.<form>.html`, listed in `protokoli/data.js`). There are about 12,000
  sections, which is impractical by hand and not needed by the model.
- The scanned protocols (`pdf/…`) and machine-voting files (`machine/…`).
The open-data `export.zip` is published only after the CIK's final decision, and it has no times. It can confirm
the outcome but can't give a count over time.

### 3. After the count (optional)

The runoff `export.zip` (`<runoff path>/opendata/export.zip`), once published, goes at
`$BRUNDER_DATA/raw/bulgaria/r2/export.zip`. `ingest` doesn't need it: it takes the outcome from the national page
at 100%. Keep it as the official final record.

## Commands

    # pre-count chance and per-MIR prior (run before polls close to check the first-round files)
    BRUNDER_DATA=~/brunder-data-local uv run brunder bulgaria prior configs/bg2026r2_v1.toml \
      --r1 ~/brunder-data-local/raw/bulgaria/r1/export.zip        # or --r1 the folder of 33 saved pages

    # after the night: replay the model on every saved stamp, add market prices, compute the standard statistics
    BRUNDER_DATA=~/brunder-data-local uv run brunder bulgaria ingest configs/bg2026r2_v1.toml \
      --r1 ~/brunder-data-local/raw/bulgaria/r1/export.zip --r2 ~/brunder-data-local/raw/bulgaria/r2

`ingest` writes the following (gitignored, never published):
- `results/bulgaria/bg2026r2_v1_replay.csv`: one row per distinct page stamp. Columns: `t`, `counted` (two-way
  votes counted / projected total), `raw` (Iotova share of the counted two-way vote), `share`, `sd`, `P`
  (P(Iotova wins)), `mirs` (MIRs with a page so far), `national_processed`, `national_raw`, `pm` (Polymarket
  P(Iotova), 1-minute CLOB history), `kalshi` (Kalshi P(Iotova): mid if the spread is ≤ 10c, else last trade).
- `results/bulgaria/bg2026r2_v1_pages.csv`: every parsed page.
- `results/bulgaria/bg2026r2_v1_replay.json`: counts, the pre-count chance, the outcome, and the standard
  statistics (90% coverage over 1-95% counted, mean z, settle times for model / raw / Polymarket / Kalshi, early
  error at 1/5/10/25% counted). These are labelled `forward` if the config was registered before 18:00 UTC.

`--no-markets` skips the two market APIs.

## Model (`configs/bg2026r2_v1.toml`)

`brunder.nowcast`, the same unit-level two-way model used for Brazil and the US:
- **Unit = MIR** (31 domestic + abroad), the finest level with a time stamp on the night. f = the MIR page's
  "обработени X% СИК протоколи", n = the two finalists' votes counted, a = Iotova's.
- **Pooling = the six NUTS-2 planning regions** (`bg31` … `bg42`). Oblasts would barely pool anything here:
  Sofia city is three MIRs and Plovdiv two, every other MIR is one oblast. Abroad (`zz`) is unpooled.
- **Prior per MIR** from the first round: s0 = (Iotova + ½ · all other candidates) / all candidate votes, and
  V0 = all candidate votes. "Support no one" votes are left out, and the model's turnout scale absorbs the change
  in turnout.

Settings, chosen on the 2016 and 2021 runoffs only (`analysis/bg_calibrate.py`, CIK `export.zip` archives via
the Internet Archive):

| Setting | Value | Why |
|---|---|---|
| transfer | even split | MIR pattern after removing the national swing, vote-weighted RMS: even 0.0425 / 0.0395 vs two-way 0.0631 / 0.0532 (2016 / 2021) |
| sigma_mun0 | 0.04 | sd of MIR swing within its region, 2016/2021 mean 0.0366, rounded up |
| sigma_uf0 | 0.02 | sd of regional swing around the national swing, mean 0.0168, rounded up |
| tau | 0.03 | smallest value with 90%-interval coverage ≥ 0.90 in both years under a villages-first order (1.00 / 0.975) |
| rho | 0.0 | lowest error at that tau (a MIR's early village sections say little about its towns) |
| kappa, nu_mun, nu_uf | defaults | not tuned |

**The count orders are simulated, not observed.** No count over time survives from earlier Bulgarian runoffs: the
CIK site keeps only the final state, and archive captures are hours apart. The calibration therefore replays the
2016 and 2021 final section results in two simulated orders. "Villages first" means that within each MIR, village
sections report before town sections, MIRs progress at random speeds, the big-city MIRs (Sofia ×3, Plovdiv city,
Varna, Burgas) are slower, and abroad comes last. This is the usual pattern, and the adverse case for an early
swing estimate. "Random" is the other order. There are 20 draws each, at checkpoints from 1% to 90% counted. At
the chosen settings:

| Order | Year | 90% coverage | Mean abs. error (pt) |
|---|---|---|---|
| villages first | 2016 | 1.000 | 0.93 |
| villages first | 2021 | 0.975 | 3.00 |
| random | 2016 | 1.000 | 0.42 |
| random | 2021 | 1.000 | 0.29 |

In 2021 the town-village gap was large. Under villages-first the error stays near 3 points until a quarter of the
vote is in (mean sd 2.3-4.0 points). Treat early projections on the night accordingly.

## Markets

| Platform | Market | YES = |
|---|---|---|
| Polymarket | event `bulgaria-presidential-election`, market `will-iliana-iotova-win-the-next-bulgarian-presidential-election` (YES token in the config); Gyurov's market is `will-ndrey-gyurov-win-the-next-bulgarian-presidential-election` | Iotova (A) |
| Kalshi | `KXBULGARIAPRES-26NOV15-IYOT` (Gyurov: `-AGYU`) | Iotova (A) |

On 2026-10-28 both venues priced Iotova at about 0.74. Polymarket's two markets had about $1M volume each.
Kalshi's had about 33k / 10k contracts traded. Price histories are read afterwards from the public APIs, so
nothing has to run on the night. Recording Kalshi order books on the night (for the capacity statistic) is
optional, and the integrator decides whether to install it:

    ExecStart=/srv/brunder/.local/bin/uv run --frozen brunder kalshi record bulgaria_2026 \
      --ticker KXBULGARIAPRES-26NOV15-IYOT --ticker KXBULGARIAPRES-26NOV15-AGYU --interval 30 --until 2026-11-02T12:00Z
    ExecStart=/srv/brunder/.local/bin/uv run --frozen brunder snap bulgaria_2026 \
      --pm-event bulgaria-presidential-election --interval 30 --until 2026-11-02T12:00Z

## Sources

| Data | URL | Licence |
|---|---|---|
| CIK result pages, 2026 first round (test fixtures: national, MIR 06, abroad) | https://web.archive.org/web/20261027102101/https://results.cik.bg/pvr202610/rezultati/index.html and the captures of `06.html` / `32.html` | Official documents, not protected by copyright (ZAPSP art. 4(1)) |
| CIK open data, 2021 presidential election, both rounds | https://web.archive.org/web/20260420220400/https://results.cik.bg/pvrns2021/tur1/export.zip, https://web.archive.org/web/20260420220447/https://results.cik.bg/pvrns2021/tur2/export.zip | Same; published under the EU open-data directive (archive readme) |
| CIK open data, 2016 presidential election (both rounds in one archive) | https://web.archive.org/web/20260420105116/https://results.cik.bg/pvrnr2016/tur1/export.zip | Same |
| Polymarket price history, Kalshi candlesticks | public APIs | Venue public data |
