# Data requests: runoff count path, 25 Oct 2026

## Status (2026-10-26)

| request | status |
|---|---|
| Poliwave: stored state-level history; were stored projections live? | **Sent** 2026-10-26 via poliwave.com/feedback (HTTP 200, "Thank you!") |
| Poliwave: licence to use their stored national history in the paper | **Needed.** Their footer says "Poliwave data may not be scraped (terms)"; see their Rights & licensing page and Results API before publishing anything built on `data/raw/poliwave/` |
| Newsroom data teams (G1/Globo, Folha, Estadão, Nexo) | **Not sent, no longer needed** — see below |
| Public call on social media | Not needed |

## Why the newsroom requests are no longer needed

TSE publishes, per state, the *Boletim de Urna na Web* files (dadosabertos.tse.jus.br, dataset
`resultados-2026-boletim-de-urna`). Each polling section's report carries `DT_BU_RECEBIDO`, the time
TSE received it, and its votes by candidate. Cumulating sections by receipt time rebuilds the count
section by section (`brunder.bweb`). The first-round files are published; the runoff files are not yet
(in 2014 they appeared two days after the vote). `brunder bweb 2026 2 --wait` polls the portal hourly
and downloads them when they appear.

## Draft: Poliwave licensing follow-up (send manually)

> **Subject:** Licence to cite Poliwave's stored results history in an academic paper
>
> Hi Poliwave team, following up on my feedback-form message about the Brazil 2026 runoff: I'd like to
> cite figures derived from the stored national count history on /br/live/presidente-2026 in an
> academic paper (non-commercial). What licence or attribution do you require, and is the Results API
> the right way to obtain that history? — André Costa
