#!/bin/sh
# Real-speed rehearsals of the 2026 first-round night, run one after another (concurrent runs would
# compete for CPU and distort tick timing). Simulated-CDN settings are assumptions; the two sensitivity
# runs cover the peak window only.
#   baseline : whole count, cache 60 s, limit 20 req/s          (~4.7 h)
#   ttl30    : 20:00–22:00 UTC, cache 30 s, limit 20 req/s      (2 h)
#   limit10  : 20:00–22:00 UTC, cache 60 s, limit 10 req/s      (2 h)
# Keep the host on AC power: caffeinate does not prevent sleep on battery.
set -e
cd "$(dirname "$0")/.."
CFG=configs/brazil_2026_r1_dev_mun.toml
caffeinate -is uv run python analysis/rehearsal.py $CFG --speed 1
caffeinate -is uv run python analysis/rehearsal.py $CFG --speed 1 --start "2026-10-04 20:00" --end "2026-10-04 22:00" --ttl 30 --tag ttl30
caffeinate -is uv run python analysis/rehearsal.py $CFG --speed 1 --start "2026-10-04 20:00" --end "2026-10-04 22:00" --limit-rps 10 --tag limit10
