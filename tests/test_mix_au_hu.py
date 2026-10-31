"""Pure helpers of the Australia / Hungary tau-mixture sensitivities (analysis/mix_au.py, analysis/mix_hu.py)."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

import mix_au  # noqa: E402
import mix_hu  # noqa: E402


def test_floor_adds_in_quadrature():
    assert mix_au.mix_sd(0.003, 0.004) == np.float64(0.005)
    assert mix_au.mix_sd(0.003, 0.0) == 0.003


def test_needed_floor_is_smallest_grid_value_with_90pct_coverage():
    # 100 frames, sd 0.002, error 0.004: covered once 1.645 sqrt(0.002^2 + f^2) > 0.004, i.e. f > 0.0014 -> grid value 0.0025
    n = 100
    f = mix_au.needed_floor(0.50, np.full(n, 0.496), np.full(n, 0.002), np.full(n, 0.5))
    assert f == 0.0025
    # frames with more than 95% counted are ignored even where the projection is badly off
    counted = np.r_[np.full(95, 0.5), np.full(5, 0.99)]
    proj = np.r_[np.full(95, 0.50), np.full(5, 0.40)]
    assert mix_au.needed_floor(0.50, proj, np.full(100, 0.002), counted) == 0.0


def test_needed_from_coverage_and_components():
    cov = pd.Series([0.5, 0.85, 0.91, 0.99], index=[0.0, 0.01, 0.02, 0.05])
    assert mix_hu.needed_from_coverage(cov) == 0.02
    assert mix_hu.needed_from_coverage(cov * 0.5) == 0.05            # none reaches 0.90 -> largest grid value
    assert mix_hu.components([0.0, 0.02], [0.05, 0.03, 0.03]) == [(0.0, 0.05), (0.0, 0.03), (0.0, 0.03), (0.02, 0.05), (0.02, 0.03), (0.02, 0.03)]


def test_pooled_moments_of_equal_weight_mixture():
    m, sd = mix_hu.pooled_moments(np.array([0.0, 2.0]), np.array([1.0, 1.0]))
    assert m == 1.0 and abs(sd - np.sqrt(2.0)) < 1e-12              # variance = mean sd^2 + variance of the means = 1 + 1
    m, sd = mix_hu.pooled_moments(np.array([0.3, 0.3]), np.array([0.02, 0.02]))
    assert abs(m - 0.3) < 1e-12 and abs(sd - 0.02) < 1e-12
