import pytest

from dcf.reverse_dcf import implied_growth
from dcf.sensitivity import centered_range, sensitivity_grid


def test_centered_range():
    assert centered_range(0.09, 0.005, 2) == pytest.approx([0.08, 0.085, 0.09, 0.095, 0.10])


def test_grid_shape_and_undefined_cells():
    grid = sensitivity_grid(lambda w, g: 100 / (w - g), [0.03, 0.08], [0.02, 0.03, 0.04])
    assert len(grid) == 2 and len(grid[0]) == 3
    assert grid[0][1] is None and grid[0][2] is None  # WACC <= g -> undefined
    assert grid[1][0] == pytest.approx(100 / 0.06)


def test_value_falls_with_higher_wacc_and_rises_with_growth():
    grid = sensitivity_grid(lambda w, g: 100 / (w - g), [0.08, 0.09], [0.02, 0.03])
    assert grid[0][0] > grid[1][0]  # higher WACC -> lower value
    assert grid[0][1] > grid[0][0]  # higher growth -> higher value


def test_implied_growth_solves_simple_function():
    # value(g) = 100 * (1 + g)^5 -> price 161.05 implies g = 10%
    assert implied_growth(lambda g: 100 * (1 + g) ** 5, 100 * 1.1**5) == pytest.approx(0.10)


def test_implied_growth_returns_none_without_sign_change():
    assert implied_growth(lambda g: 1_000.0, 50.0) is None
