# Fast public signals, scored separately from count-only models

This note inventories estimates released around poll close that can beat an official count. A fast-signal version is a **separate model version**, never a replacement for the pure-count headline. It is labelled **“with official quick estimate, scored separately.”** These retrospective choices were made after the target results were known and are sensitivity analyses, not preregistered evidence.

## Mexico 2024: INE Conteo Rápido

### Rule fixed before replay

The rule below was written before running `mx2024_qc_v1`.

1. The count-only model is exactly `mx2024_v1`; no count setting or market rule changes.
2. The INE estimate becomes available at **2024-06-03 05:50 UTC (23:50 central time)**. This is the minute of Guadalupe Taddei's national message reported contemporaneously by El Financiero; UnoTV's article containing the estimate is stamped 05:49:02 UTC. INE's official transcript and release confirm the figures but give only the date, not a clock time. Using 05:50 UTC avoids backdating to the article timestamp.
3. INE reported 95%-confidence vote-share ranges: Sheinbaum **58.3–60.7%**, Gálvez **26.6–28.6%**, Máynez **9.9–10.8%**, from 5,651 sampled casillas (74.3% of the planned sample). For the dashboard's Sheinbaum-versus-Gálvez share, use the conservative corner bounds
   `58.3/(58.3+28.6) = 67.0886%` and `60.7/(60.7+26.6) = 69.5304%`.
4. Represent that interval as a normal estimate with midpoint **68.3095%** and standard deviation `half_width / 1.96 = 0.6229 percentage point`. From the publication minute onward, combine it with the pure-count normal estimate by fixed inverse-variance weighting:

   `variance = 1 / (1 / count_variance + 1 / quick_count_variance)`

   `share = variance * (count_share / count_variance + quick_count_share / quick_count_variance)`.

   The combined win probability is the normal probability that this two-way share exceeds 50%. This treats the two estimates as independent, although sampled casillas can later overlap PREP actas; that limitation is reported rather than adjusted after seeing the result. Before 05:50 UTC, the path is bit-for-bit the pure-count path.
5. Score share error and 90% coverage on the same final cómputo and minutes as v1. Report first/staying-above 90% and 95% on the resolved side for every market, but call a market **contested** only if it had not already stayed at 90% before the count began. Thus the quick-count comparison cannot claim a lead on a race already decided before the count.

Sources: [INE official transcript](https://centralelectoral.ine.mx/2024/06/03/tercer-mensaje-en-cadena-nacional-de-la-consejera-presidenta-del-ine-guadalupe-taddei-con-motivo-de-la-jornada-electoral-2024/), [INE release 352](https://centralelectoral.ine.mx/2024/06/03/da-a-conocer-ine-resultados-de-conteo-rapido-del-proceso-electoral-federal-2023-2024/), and [contemporaneous timing report](https://www.elfinanciero.com.mx/elecciones-mexico-2024/2024/06/02/ine-declara-virtual-ganadora-a-claudia-sheinbaum-mensaje-de-guadalupe-taddei-elecciones-presidenciales-2024/). Licence: INE's *Términos de libre uso* and constitutional principle of *máxima publicidad*, as documented in `docs/mexico.md`: copying, publication, adaptation and derived datasets are permitted with attribution, no distortion and no implied endorsement.

### Results

The official cómputo's final two-way share was 68.523%. At the release minute, pure-count v1 was 1.617 points low; combining the INE estimate reduced the error to 0.783 point. Across all post-release minutes, mean absolute error fell from **0.795 to 0.655 point**. At 50% counted it fell from 1.550 to 1.009 points. Full-window (1–95% counted) 90% coverage changed only from **10.3% to 10.7%** (post-release: 0.0% to 0.4%), so the combined model remained badly overconfident; precision-weighting two partly overlapping estimates is not a calibration repair.

Both model versions assigned effectively 1.0 probability to Sheinbaum from PREP opening, 230 minutes before INE released the quick estimate. The two included Polymarket questions were already settled at 90% on their resolved side before the count, so there are **zero contested markets** and no lead claim:

| resolved side | stable 90%, pure / +quick / market (minutes from count opening) | stable 95%, pure / +quick / market |
|---|---|---|
| Sheinbaum YES | 0 / 0 / −2,994 | 0 / 0 / −15 |
| Gálvez NO | 0 / 0 / −148 | 0 / 0 / +150 |

The Gálvez market briefly remained below 95% certainty after count opening, but it is still classified “decided before the count” because it had stayed above 90% from 148 minutes before opening. Across the two non-contested market questions during their count-night overlap, Brier was 0 for both count models (the landslide probability rounded to 1.0) and 0.000060 for Polymarket; a contested-market Brier is undefined because the set is empty. No trading signal fired.

Published files: `configs/mx2024_qc_v1.toml`, `site/data/mx2024_qc/`, and the `mx2024_qc` landing entry. The pure-count `mx2024` files remain unchanged and remain the headline.

## Germany 2025: researched, not implemented

Wahlrecht.de preserves the ARD/Infratest dimap and ZDF/Forschungsgruppe Wahlen 18:00 Prognosen and timestamped Hochrechnungen. At 18:00 CET, ARD listed CDU/CSU 29.0%, AfD 19.5% and BSW 4.7%; ZDF listed 28.5%, 20.0% and 5.0%. These would directly inform the four contested markets (AfD bands, BSW 5%, and a Union band).

Reuse permission is **unclear/non-permissive**, so no `de2025_prognose_v1` is implemented. ARD's terms say its online information and data are copyrighted and that reproduction, recombination, republication, distribution or storage requires prior written consent; ZDF's terms state the same. Wahlrecht.de permits links but publishes no licence granting republication of its compiled table. Factual figures may not themselves be protected in every jurisdiction, but the requested public research display would copy a time series assembled by broadcasters/Wahlrecht.de. Without written permission, this project links to the archive and does not ingest or republish it.

Sources: [Wahlrecht.de archive](https://www.wahlrecht.de/news/2025/bundestagswahl-2025.html), [ARD terms](https://www.ard.de/die-ard/footer/nutzungsbedingungen-ard-online-100.html), [ZDF terms](https://www.zdf.de/nutzungsbedingungen), [Wahlrecht.de provider/contact page](https://www.wahlrecht.de/kontakt.htm).

## Inventory for every other dashboard election

“Official” means an estimate issued by the election authority or another public body, not a broadcaster's commissioned exit poll. Future-election rows describe the known institutional practice; they must be revisited on election day.

- `us2026` — **No official fast estimate.** US election offices publish counts; AP race calls and AP VoteCast are proprietary media products, not official signals.
- `nz2026` — **No official fast estimate known.** The Electoral Commission publishes preliminary count updates after 19:00, not an exit poll or quick estimate.
- `tw2026_taipei` — **No official fast estimate known.** The CEC publishes the official live count; television vote estimates are not CEC releases.
- `nz2023` — **None.** The Electoral Commission released only the preliminary count; no official exit poll/quick count.
- `nz2020` — **None.** Same Electoral Commission practice.
- `br2026r2` — **None separate from the count.** TSE live totals and its later mathematical declaration are official, but there is no official exit poll/quick estimate.
- `br2026r1` — **None separate from the count.** Same TSE practice.
- `br2026r2gov` — **None separate from the count.** Same TSE practice for governor races.
- `br2022r1` — **None separate from the count.** Broadcaster/Ipec exit polls were not official public-body estimates and are proprietary.
- `br2022r2` — **None separate from the count.** Same.
- `il2026` — **No official fast estimate known.** Broadcasters publish commissioned exit polls; they are not Central Elections Committee estimates and reuse rights are not established.
- `ca2025` — **None.** Elections Canada released official results, not an official exit poll/quick estimate.
- `nl2025` — **None official.** The 21:00 Ipsos I&O exit poll was commissioned by NOS, not issued by the Kiesraad, and is not openly licensed for republication.
- `qc2026` — **None.** Élections Québec publishes the count; no official quick estimate.
- `hu2026` — **No official fast estimate identified.** Any polling/exit-poll product is private, not a National Election Office estimate.
- `pl2025r2` — **None official.** Ipsos produced the exit poll for TVP, TVN and Polsat; the broadcasters/firm give no open reuse licence, so it is not implemented.
- `pl2025r1` — **None official.** Same Ipsos/broadcaster ownership and unclear reuse permission.
- `de2025` — **None official.** The ARD/ZDF products are broadcaster estimates, researched above, with no permission adequate for republication.
