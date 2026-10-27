import hashlib

import pytest

from brunder import integrity, paths

CFG = """id = "t"
[election]
cycle = "ele2026"
ele = 1
cand_a = "13"
cand_b = "22"
[prior]
kind = "open"
year = 2026
round = 1
[replay]
year = 2026
round = 2
[markets.national]
event = "ev"
market = "mk"
"""


def _data(tmp_path):
    root = tmp_path / "data"
    files = {"raw/tse_open/bweb/2026_2t/SP.parquet": b"sections",
             "raw/tse_open/presidente_by_municipality_2026.parquet": b"prior",
             "raw/polymarket/ev/prices/mk.parquet": b"prices"}
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(body)
    cfg = tmp_path / "cfg.toml"
    cfg.write_text(CFG)
    return root, files, cfg


def test_stage_verifies_against_manifest_and_refuses_a_corrupt_source(tmp_path, monkeypatch):
    root, files, cfg = _data(tmp_path)
    monkeypatch.setattr(paths, "DATA", root)
    good = {rel: hashlib.sha256(b).hexdigest() for rel, b in files.items()}
    lines = [f"{h}  ./{rel}" for rel, h in good.items()]
    (root / integrity.MANIFEST).write_text("\n".join(lines) + "\n")

    meta = integrity.stage(cfg, tmp_path / "stage")
    assert meta["files"] == good
    integrity.check_staged(cfg, tmp_path / "stage")                    # accepted

    (root / "raw/polymarket/ev/prices/mk.parquet").write_bytes(b"bit-flipped")   # source no longer matches
    with pytest.raises(OSError, match="verified copy"):
        integrity.stage(cfg, tmp_path / "stage2")


def test_new_files_join_the_manifest_and_the_gate_rejects_other_configs(tmp_path, monkeypatch):
    root, files, cfg = _data(tmp_path)
    monkeypatch.setattr(paths, "DATA", root)
    meta = integrity.stage(cfg, tmp_path / "stage")                     # no manifest yet: double-read path
    assert meta["new_manifest_entries"] == 3
    assert set(integrity.load_manifest(root)) == set(files)

    with pytest.raises(RuntimeError, match="not a verified stage"):
        integrity.check_staged(cfg, root)                              # the drive itself is never accepted
    cfg.write_text(CFG.replace('market = "mk"', 'market = "mk"\n# edited'))
    with pytest.raises(RuntimeError, match="different version"):
        integrity.check_staged(cfg, tmp_path / "stage")
