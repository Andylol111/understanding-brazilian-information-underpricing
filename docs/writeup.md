# The count is public. Is the market listening?

*A study of election-night prices on Polymarket, using Brazil's 2026 presidential election, and a test
of whether the pattern holds across 69,828 other markets.*

## The question

On election night the official count is free, public and refreshed every few seconds. A prediction
market should absorb it almost instantly: anyone can read the count, and anyone can trade. If prices
lag what the count already implies, that lag is information the market is under-pricing, and in
principle someone could trade it.

Brazil is an unusually good place to test this. Its electoral court (TSE) publishes the count live,
state by state and municipality by municipality, and afterwards releases a file with every one of
roughly 500,000 polling sections and the minute its result arrived. That lets you rebuild, after the
fact, exactly what the public could see at every moment of the night, and line it up against
Polymarket's prices minute by minute.

## Why the raw count misleads early

The runoff on 25 October 2026 was close: Lula 51.79%, Flávio Bolsonaro 48.21%. Early in any count the
national running tally is a poor guide, because states don't report in random order: some finish
their counting much faster than others, and they don't vote alike.

That early lean is predictable. A model that weights each state by how much of its vote is still to
come, using only information available before the night, removes most of it. On the first round,
measured against the electoral court's own files, it cut the error of the early count (1.7% of
sections in) from −2.97 to +0.50 percentage points.

On runoff night that lean was visible in the market. Rebuilt from the per-section receipt times (from
the electoral court's per-ballot-box files) and lined up with Polymarket minute by minute:

- the market went above 50% for Lula at 19:10 (1.6% counted) and above 70% at 19:23;
- from 19:45 to 20:23 the raw national tally showed Flávio ahead (14% → 46% counted), and the market
  fell from 0.86 back to 0.62;
- it settled above 90% only at 20:24, with 47% of sections counted. Lula won 51.79–48.21.

Through that stretch the model below stayed at or above 0.9 for Lula.

## A model that reads the count properly

The tool built here, `brunder night`, works at the level of Brazil's ~5,570 municipalities. For each
one it compares what has been counted so far with how that municipality voted last time, and projects
the rest, with an uncertainty that shrinks as the count fills in. Summed up, that gives a probability
for each market (national winner, each state) at every moment.

On the first-round night, which is where its settings were chosen, it was confident about the national
outcome **28 minutes before the market**, and up to about three hours earlier in some state markets.
A simple rule (trade only the side the model favours, only once 1% of a market is counted, only when the
edge beats costs by 5¢) produced 7 trades: 5 won, +16.6% per dollar.

![First-round night: model vs market](figures/r1_dev_national_replay.png)

That number is in-sample. The settings were tuned on that same night, so it shows the method *can* work,
not that it does.

## How much money was actually there?

Returns per dollar assume you can buy any amount at the quoted price. You can't. Using Polymarket's
public trade history, I counted how many dollars traded at the signal's price or better in the 30
minutes after each signal:

- **$2,741 in total**, across all 7 signals;
- 98% of it in the national market, all within about 4 minutes, after which the price moved past the
  signal;
- the state markets offered $0 to $50 each. Three of the four winning state signals couldn't have been
  filled at all.

At a $100 stake per signal, that's about $135 of profit for the night. The gap between count and price
is real, but it's small in dollars and short-lived.

## Does the pattern generalise?

One election is one case. To ask whether Polymarket systematically mis-weights information, I sampled
markets across 11 categories (politics, sports, crypto, economy, "mentions" and others), and fixed
three tests in advance: their exact definitions were written down and hashed before the data they'd be
tested on was opened, and each held-out sample was looked at once. Every rule uses only information
available at the time, with a 5-minute delay, real spreads and fees.

What held up:

- **Thin markets over-react.** After a sharp one-hour move in a market that had traded under $10k in
  the previous week, the price tends to come back. Significant in 6 of 10 categories after correcting
  for multiple tests, same direction in all 10.
- **"Mentions" markets under-price the likely outcome.** In markets on what a public figure will say,
  sides priced 80–97¢ a few hours before the event returned +2.1% per dollar after costs.
- **But both are hard to trade.** Across 1,851 qualifying mentions trades, only $8.3k traded at those
  prices within 30 minutes.

What didn't hold up: the idea that deep, liquid markets react too slowly in general. Brazil's
count-night lag is a case study, not a pattern this sample detects.

## Testing the tool honestly

The first-round result was in-sample, so the next steps are about evidence that isn't:

1. **Rehearsal.** The live code was run against a simulated version of the electoral court's website
   and its CDN, replaying the first-round night in real time. It produced the same 7 signals as the
   replay, with its view of the count a median 47 seconds behind the truth (under assumed CDN
   limits).
2. **Frozen replays.** The settings were frozen and registered before the runoff's section-level data
   was available, along with six governor runoffs held the same day: races the method was never tuned
   on. Each was then run exactly once, on the runoff's per-section receipt times and votes (from the
   electoral court's per-ballot-box files, validated against its own state files). The outcomes were
   known, so this tests the method, not the edge:
   - **Where the race was still open, the model got there first.** President: about 80 minutes ahead
     of the market from the first moment the rule could trade. Espírito Santo: 42 minutes. Rio Grande
     do Norte, where the first-round leader lost: 22 minutes, calling the upset first.
   - **Where the market was already sure, there was nothing to beat** (Acre, Amazonas, Tocantins).
   - **It was slow in the Federal District**, 140 minutes behind a market that was sure before counting
     began. Its starting point was fine (first-round share 59%, final 57%), but Brasília is a single
     municipality, so its only uncertainty is the order in which that municipality's sections report,
     which it narrows slowly: ±6 points at 18% counted against a 7-point final margin.
   - Its 90% uncertainty bands contained the final result through 97–100% of the count in every race.

   ![Runoff night: model vs market](figures/r2_national_replay.png)

   *Runoff night, national market. The model (red) was confident from the first handful of sections,
   when under 1% was counted, and stayed right; the market (black) only settled after 20:24.*
3. **Forward tests.** The cross-category recorder has been running live since 26 October, logging every
   signal before outcomes exist; its single evaluation happens once a pre-set number of signals has
   resolved. Upcoming election nights (Israel, 27 October; the US midterms, 3 November) are being
   recorded so the model can be developed and then tested on counts it has never seen.

## Limitations

- One country, two nights of development. The first-round numbers are in-sample.
- The model is overconfident late in the count (above ~90% counted): it treats a municipality's last
  sections like its earlier ones, and they differ. This was found and written down before any runoff
  result was examined.
- Capacity is a lower bound: it counts trades that happened, not resting orders nobody took.
- Speed matters at the margin: the gap between the count and the price lasts minutes, and the
  electoral court's CDN rate-limits heavy readers, so the tool stays within a polite request budget.

## Takeaways

- Prediction markets do lag a public count, measurably, on the nights it matters most.
- The tradable part is small and fleeting; most of the apparent edge disappears once you ask how much
  you could actually buy.
- Across markets, the robust inefficiency is over-reaction where little money trades, not slow
  reaction where a lot does.

*Code, data pipeline, pre-registrations and every number above:
[README](../README.md), [cross-category results](cross_category.md),
[election-night tool](edge_tool.md).*
