import subprocess

import pytest

from brunder import registry


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    spec = tmp_path / "s.toml"
    spec.write_text('id = "s1"\nkind = "late_certainty"\n')
    subprocess.run(["git", "add", "s.toml"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "spec"], cwd=tmp_path, check=True)
    return spec


def test_holdout_locked_until_registered(tmp_path):
    spec = _repo(tmp_path)
    reg, runs = tmp_path / "reg.jsonl", tmp_path / "runs.jsonl"
    with pytest.raises(registry.HoldoutLocked):
        registry.open_holdout(registry.load_spec(spec), reg, runs)
    registry.register(spec, reg)
    assert registry.open_holdout(registry.load_spec(spec), reg, runs)["look"] == 1
    assert registry.open_holdout(registry.load_spec(spec), reg, runs)["look"] == 2   # every look is counted


def test_editing_a_registered_spec_relocks_the_holdout(tmp_path):
    spec = _repo(tmp_path)
    reg, runs = tmp_path / "reg.jsonl", tmp_path / "runs.jsonl"
    registry.register(spec, reg)
    spec.write_text('id = "s1"\nkind = "late_certainty"\nthreshold = 0.2\n')
    with pytest.raises(registry.HoldoutLocked):                  # uncommitted change
        registry.open_holdout(registry.load_spec(spec), reg, runs)
    subprocess.run(["git", "commit", "-qam", "tweak"], cwd=tmp_path, check=True)
    with pytest.raises(registry.HoldoutLocked):                  # committed, but hash differs
        registry.open_holdout(registry.load_spec(spec), reg, runs)
    with pytest.raises(registry.HoldoutLocked):                  # cannot re-register the same id
        registry.register(spec, reg)
