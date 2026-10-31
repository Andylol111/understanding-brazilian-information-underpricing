import asyncio
import json
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brunder import spain as es

F = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
CFG = ROOT / "configs" / "es2026_v1.toml"


@pytest.fixture(scope="module")
def cfg():
    return es.load_config(CFG)


def test_public_history_reproduces_official_totals():
    h = es.load_history()
    t = h[h["election"] == "2023-07-23"]
    assert len(t) == 52 and t["valid"].sum() == 24_688_087
    assert t[[f"seats_{k}" for k in es.HIST_LANES]].to_numpy().sum() == 350
    assert t["seats_PP"].sum() == 137 and t["seats_PSOE"].sum() == 121 and t["seats_VOX"].sum() == 33
    assert (t[list(es.HIST_LANES)].sum(axis=1) + t["blank"] == t["valid"]).all()


@pytest.mark.parametrize("date", ["2016-06-26", "2023-07-23"])
def test_dhondt_reproduces_official_seats(date):
    d = es.load_history()
    d = d[d["election"] == date]
    seats = np.zeros(len(es.HIST_LANES))
    skip = (es.HIST_LANES.index("OTHER"),)
    for _, r in d.iterrows():
        s = int(sum(r[f"seats_{k}"] for k in es.HIST_LANES))
        seats += es.dhondt(r[list(es.HIST_LANES)].to_numpy(float)[None, :], s, np.array([float(r["valid"])]), skip)[0]
    official = d[[f"seats_{k}" for k in es.HIST_LANES]].sum().to_numpy()
    assert (seats == official).all(), dict(zip(es.HIST_LANES, zip(seats, official)))


@pytest.mark.parametrize("date", ["2016-06-26", "2019-04-28", "2019-11-10", "2023-07-23"])
def test_dhondt_on_official_candidacies_reproduces_every_list_seat_in_all_four_elections(date):
    # The lane table cannot reproduce 2019 (lanes merge Más País/Compromís with Unidas Podemos; PRC and Teruel Existe sit in OTHER).
    d = es.dhondt_official(date)
    assert d["prov"].nunique() == 52 and d["seats"].sum() == 350
    bad = d[d["dhondt"] != d["seats"]]
    assert bad.empty, bad[["prov", "list", "votes", "seats", "dhondt"]]
    h = es.load_history()
    h = h[h["election"] == date].set_index("prov")
    assert (d.groupby("prov")["seats"].sum() == h[[f"seats_{k}" for k in es.HIST_LANES]].sum(axis=1)).all()
    by_lane = d.groupby("lane")["seats"].sum()
    for k in es.HIST_LANES:
        assert by_lane.get(k, 0) == h[f"seats_{k}"].sum(), k


def test_lane_patterns_do_not_pull_unrelated_lists_into_the_big_lanes():
    assert es.hist_lane("CONVERGENCIA ANDALUZA - CAnda") == "OTHER"
    assert es.hist_lane("CONVERGÈNCIA DEMOCRÀTICA DE CATALUNYA - CDC") == "JUNTS"
    assert es.hist_lane("COMPROMISO POR GALICIA - CxG") == "OTHER"
    assert es.hist_lane("MÉS COMPROMÍS - MÉS COMPROMÍS") == "LEFT"
    assert es.hist_lane("UNIDOS POR LA SOLIDARIDAD INTERNACIONAL - UNIDOS SI") == "OTHER"
    assert es.hist_lane("UNIDOS PODEMOS - PODEMOS-IU-EQUO") == "LEFT"


def test_seats_2026_total_and_changes_against_2023():
    assert sum(es.SEATS_2026.values()) == 350 and len(es.SEATS_2026) == 52
    old = es.history_units("2023-07-23")["seats"]
    diff = {u: (int(old[u]), es.SEATS_2026[u.split("-")[1]]) for u in old.index if int(old[u]) != es.SEATS_2026[u.split("-")[1]]}
    assert diff == {"es-11": (9, 8), "es-28": (37, 38)}


def test_lane_classification():
    assert es.hist_lane("PARTIT DELS SOCIALISTES DE CATALUNYA (PSC-PSOE) - PSC") == "PSOE"
    assert es.hist_lane("PARTIDO POPULAR EN COALICIÓN CON EL PARTIDO ARAGONÉS - PP-PAR") == "PP"
    assert es.hist_lane("SUMAR - EN COMÚ PODEM - SUMAR - ECP") == "LEFT"
    assert es.hist_lane("PARTIDO ANIMALISTA CON EL MEDIO AMBIENTE - PACMA") == "OTHER"
    assert es.lane_2026("FA FRENTE AMPLIO") == "FA" and es.lane_2026("SUMAR SUMAR") == "FA"
    assert es.lane_2026("PODEMOS PODEMOS") == "PODEMOS" and es.lane_2026("Cs CIUDADANOS") == "OTHER"
    assert es.lane_2026("XYZ NEW LIST") == "OTHER"
    assert es.lane_2026("XYZ", {"PODEMOS": "XYZ"}) == "PODEMOS"


def test_real_feed_province_file_parser():
    x = es.parse_province((F / "es_feed_hist_prov28.json").read_bytes())
    assert x["unit_id"] == "es-28" and x["tables"] == x["total_tables"] == 7118
    assert x["votes"]["PP"] == 1_463_183 and x["blank"] == 28_960 and x["valid"] == 3_608_673
    assert sum(x["votes"].values()) + x["blank"] == x["valid"]


def test_feed_parser_rejects_inconsistent_files():
    d = json.loads((F / "es_feed_hist_prov28.json").read_text())
    bad = json.loads(json.dumps(d)); bad["totals"]["valid"] += 5
    with pytest.raises(ValueError):
        es.parse_province(json.dumps(bad))
    bad = json.loads(json.dumps(d)); bad["counted"]["tables"] = bad["counted"]["total_tables"] + 1
    with pytest.raises(ValueError):
        es.parse_province(json.dumps(bad))
    with pytest.raises(ValueError):
        es.parse_province((F / "es_feed_pre_manifest_es.json").read_bytes())      # a national file


def test_feed_ready_follows_manifest():
    pre = json.loads((F / "es_feed_manifest_pre.json").read_text())
    assert pre["embargo"] is True and not es.feed_ready(pre)
    assert not es.feed_ready({**pre, "embargo": False})
    assert es.feed_ready({**pre, "embargo": False, "phase": "live", "counted_pct": 1.2})
    assert es.feed_ready({**pre, "embargo": False, "counted_pct": 3.0})


def test_frozen_prior_has_52_provinces_exact_poll_target_and_350_seats(cfg):
    units = es.prior_units(cfg)
    assert len(units) == 52 and units["seats"].sum() == 350 and units["region"].nunique() == 18
    med = es.poll_medians(cfg)
    nat = (units[list(es.LANES)].to_numpy() * units["V0"].to_numpy()[:, None]).sum(axis=0) / units["V0"].sum()
    blank = float((units["blank_frac"] * units["V0"]).sum() / units["V0"].sum())
    got = dict(zip(es.LANES, nat * (1 - blank)))
    for lane in ("PP", "PSOE", "VOX", "FA", "PODEMOS"):
        assert abs(got[lane] - med[lane]) < 1e-6, lane
    assert abs(units[list(es.LANES)].sum(axis=1) - 1).max() < 1e-9


def test_poll_medians_are_the_frozen_values(cfg):
    med = es.poll_medians(cfg)
    assert {k: round(v, 4) for k, v in med.items()} == {"PP": .315, "PSOE": .264, "VOX": .189, "FA": .065, "PODEMOS": .027}


def test_projection_without_counts_is_the_prior_and_complete_count_is_exact():
    units = pd.DataFrame({"V0": [100.0, 100.0], "PP": [.6, .4], "PSOE": [.4, .6], "blank_frac": [0, 0], "seats": [3, 3]},
                         index=["es-01", "es-02"])
    st = es.Settings(cera_frac=0.0)
    p = es.project(units, es.empty_counts(units, ("PP", "PSOE")), st, ("PP", "PSOE"))
    assert np.allclose(p.votes, [[60, 40], [40, 60]]) and p.counted_frac == 0
    count = pd.DataFrame({"PP": [55.0, 35.0], "PSOE": [45.0, 65.0], "f": [1.0, 1.0]}, index=units.index)
    p = es.project(units, count, st, ("PP", "PSOE"))
    assert p.counted_frac == 1 and np.allclose(p.votes, [[55, 45], [35, 65]])


def test_counted_votes_are_kept_in_every_draw(cfg):
    units = es.prior_units(cfg)
    count = es.empty_counts(units)
    count["PP"], count["PSOE"], count["f"] = 30_000.0, 20_000.0, 0.1
    st = es.Settings(draws=200)
    p = es.project(units, count, st)
    assert (p.votes >= p.counted - 1e-6).all() and abs(p.counted_frac - p.counted.sum() / p.votes.sum()) < 1e-12
    d = es.simulate(p, st, 1)
    assert (d.votes.sum(axis=1) > p.counted.sum()).all()


def test_tau_mixture_config_and_simulation_between_components(cfg):
    st = es.settings(cfg)
    assert st.tau_mixture == (0.06, 0.045, 0.06) and st.tau_nat == 0.06 and st.tau_prov == 0.12 and st.tau_prov_ratio == 2.0
    units = es.prior_units(cfg)
    count = es.empty_counts(units)
    count["PP"], count["PSOE"], count["f"] = 60_000.0, 40_000.0, 0.5
    base = es.Settings(draws=900, precount_sd=0.12)
    p = es.project(units, count, base)

    def sd_pp(s):
        return es.simulate(p, s, 3).share[:, es.LANES.index("PP")].std()
    lo, hi = sd_pp(es.Settings(**{**base.__dict__, "tau_nat": .01, "tau_prov": .02})), sd_pp(es.Settings(**{**base.__dict__, "tau_nat": .09, "tau_prov": .18}))
    mix = sd_pp(es.Settings(**{**base.__dict__, "tau_mixture": (.01, .09)}))
    assert lo < mix < hi
    # a one-member mixture is the point model with tau_prov = ratio x tau_nat, draw for draw
    one = es.simulate(p, es.Settings(**{**base.__dict__, "tau_mixture": (.045,)}), 3)
    pt = es.simulate(p, es.Settings(**{**base.__dict__, "tau_nat": .045, "tau_prov": .09}), 3)
    assert np.allclose(one.votes, pt.votes) and np.array_equal(one.seats, pt.seats)


def test_precount_probabilities_are_complete_and_sensible(cfg):
    units, p, d, probs = es._initial(cfg)
    assert set(probs) == {r["id"] for r in cfg["races"]} and len(cfg["races"]) == 22
    for party in ("pp", "psoe", "vox"):
        assert abs(sum(probs[f"es-{k}-{party}"] for k in ("first", "second", "third")) - 1 + 0) <= .08 or party == "vox"
    assert abs(sum(probs[f"es-first-{x}"] for x in ("pp", "psoe", "vox")) - 1) < .01
    assert .7 < probs["es-first-pp"] < .95 and probs["es-first-vox"] < .02
    assert p.counted_frac == 0 and d.seats.sum(axis=1).max() <= 350


def test_races_cover_every_market_in_the_config(cfg):
    tickers = [r["kalshi"] for r in cfg["races"]]
    assert len(tickers) == len(set(tickers))
    assert sum(1 for r in cfg["races"] if r.get("polymarket")) == 10
    for r in cfg["races"]:
        if r.get("polymarket"):
            assert r["pm_event"] in cfg["markets"]["polymarket_events"] and r["pm_token"].isdigit()
        assert re.match(r"^KX[A-Z0-9]+-", r["kalshi"])


def test_spain_map_matches_model_units(cfg):
    meta = es.meta(cfg, "pre-count", {r["id"]: 0.5 for r in cfg["races"]})
    path = ROOT / "site" / "data" / meta["geometry"]
    geo = json.loads(path.read_text())
    ids = [g["properties"]["unit_id"] for g in geo["objects"][meta["geometry_object"]]["geometries"]]
    assert len(ids) == len(set(ids)) == 52 and set(ids) == set(es.prior_units(cfg).index)
    assert path.stat().st_size <= 1_000_000


def test_public_labels_name_spain_and_markets():
    e = es.entry()
    assert e["id"] == "es2026" and e["country"] == "ES" and e["status"] == "scheduled" and e["unit_level"] == "province"


def _small_cfg(tmp_path: Path, night: str) -> Path:
    import es_rehearsal as rh
    path = rh.rehearsal_config(night, tmp_path)
    path.write_text(re.sub(r"draws = \d+", "draws = 300", path.read_text()))
    return path


def test_rehearsal_replay_exports_and_scores_the_known_night(tmp_path):
    import es_rehearsal as rh
    info = rh.build("2023-07-23", tmp_path / "r")
    cfg_path = Path(info["config"])
    cfg_path.write_text(re.sub(r"draws = \d+", "draws = 300", cfg_path.read_text()))
    site = tmp_path / "site"
    es.export(cfg_path, site, info["snap"])
    meta = json.loads((site / "es2026" / "meta.json").read_text())
    tl = json.loads((site / "es2026" / "timeline.json").read_text())
    assert meta["phase"] == "replay" and len(tl["es-first-pp"]) >= 100
    last = tl["es-first-pp"][-1]
    assert last["counted"] > .98 and last["P"] > .99               # PP did win 2023 and the count is nearly complete
    assert tl["es-first-pp"][0]["counted"] < .05
    frames = json.loads((site / "es2026" / "units.json").read_text())["frames"]
    assert np.mean(frames[-1]["f"]) > 900 and frames[0]["f"] != frames[-1]["f"]
    shares = [r["share"] for r in tl["es-ppvox-50"] if r["share"] is not None]
    assert shares and all(0 < s < 1 for s in shares) and tl["es-first-pp"][-1]["share"] is None   # most questions carry chances only
    assert abs(tl["es-ppvox-50"][-1]["raw"] - .4546) < .005                                         # PP + Vox won 45.5% in 2023


def test_recorder_fetches_every_publication_from_a_feed_server(tmp_path):
    import es_rehearsal as rh
    steps = rh.schedule("2019-11-10")[:2]
    state = {"i": 0}
    base = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            p = self.path
            body = None
            if p.endswith("/manifest.json"):
                body = rh.manifest(steps[state["i"]], base["url"])
            else:
                m = re.search(r"/(\d+)/(es|prov/(\d\d))\.json$", p)
                if m:
                    st = steps[int(m.group(1)) - 1]
                    body = st["es"] if m.group(2) == "es" else st["prov"].get(m.group(3))
            if body is None:
                self.send_response(404); self.end_headers(); return
            data = json.dumps(body).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    base["url"] = f"http://127.0.0.1:{srv.server_address[1]}/v1/congreso-2026-11"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = {"feed": {"base": base["url"], "request_delay_seconds": 0.0}}

    async def run():
        task = asyncio.create_task(es.record_feed(cfg, interval=0.2, until=datetime.now(timezone.utc) + timedelta(seconds=6), root=tmp_path))
        await asyncio.sleep(2.0)
        state["i"] = 1
        await task

    try:
        asyncio.run(run())
    finally:
        srv.shutdown()
    lines = [json.loads(x) for x in (tmp_path / "index.jsonl").read_text().splitlines()]
    keys = {x["key"] for x in lines}
    assert "manifest" in keys and "es" in keys and {f"prov/es-{i:02d}" for i in range(1, 53)} <= keys
    assert sum(1 for x in lines if x["key"] == "prov/es-28") == 2           # one file per publication, none repeated
    arc = es.Archive(tmp_path)
    t2 = max(t for t, _ in arc.files["prov/es-28"])
    got = es.parse_province(arc.at("prov/es-28", t2).read_bytes())
    assert got["tables"] == steps[1]["prov"]["28"]["counted"]["tables"]


def test_embargoed_feed_is_never_fetched_beyond_the_manifest(tmp_path):
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.path)
            data = (F / "es_feed_manifest_pre.json").read_bytes() if self.path.endswith("manifest.json") else b"{}"
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = {"feed": {"base": f"http://127.0.0.1:{srv.server_address[1]}/v1/congreso-2026-11", "request_delay_seconds": 0.0}}
    try:
        asyncio.run(es.record_feed(cfg, interval=0.1, until=datetime.now(timezone.utc) + timedelta(seconds=1), root=tmp_path))
    finally:
        srv.shutdown()
    assert seen and all(p.endswith("manifest.json") for p in seen)


def test_only_the_fifty_percent_line_carries_a_share(cfg):
    units, p, d, probs = es._initial(cfg)
    when = pd.Timestamp("2026-11-29T20:00:00Z")
    race = next(r for r in cfg["races"] if r["id"] == "es-ppvox-50")
    row = es._timeline_row(cfg, race, when, p, d, probs, {}, {}, counted=False)
    mean, sd = es.race_share(d, race)
    assert abs(row["share"] - mean) < 1e-4 and abs(row["sd"] - sd) < 1e-4 and .4 < row["share"] < .6
    for rid in ("es-first-pp", "es-vox-15", "es-pp-31"):
        r = next(x for x in cfg["races"] if x["id"] == rid)
        got = es._timeline_row(cfg, r, when, p, d, probs, {}, {}, counted=False)
        assert got["share"] is None and got["sd"] is None and got["raw"] is None and got["P"] is not None


def test_settle_reads_the_real_2023_national_file(cfg):
    import es_settle
    national = json.loads((F / "es_feed_hist_es.json").read_text())
    won = es_settle.outcomes(national, cfg)
    assert won["es-first-pp"] and won["es-second-psoe"] and won["es-third-vox"] and not won["es-first-psoe"]
    assert won["es-pp-31"] and not won["es-pp-36"] and won["es-psoe-30"] and not won["es-ppvox-50"]     # PP+Vox got 45.4% in 2023
    assert won["es-vox-13"] is False and won["es-vox-15"] is False


def test_map_values_are_pp_share_of_pp_plus_psoe(cfg):
    units = es.prior_units(cfg)
    two = es._two_way(units)
    assert len(two) == 52 and (two > 0).all() and (two < 1).all()
    assert two[list(units.index).index("es-28")] > .5 > two[list(units.index).index("es-08")]     # Madrid leans PP, Barcelona PSOE
