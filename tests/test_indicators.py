from __future__ import annotations

from datetime import date, timedelta

import pytest

from etf_tracker.indicators import annualized_volatility, correlation, is_20_day_new_low
from etf_tracker.models import PriceBar


def test_is_20_day_new_low_requires_latest_close_below_prior_20_days() -> None:
    bars = _bars_from_closes([1.00 + index * 0.01 for index in range(20)] + [0.99])

    assert is_20_day_new_low(bars) is True


def test_is_20_day_new_low_is_false_when_latest_equals_prior_low() -> None:
    bars = _bars_from_closes([1.00] + [1.10] * 19 + [1.00])

    assert is_20_day_new_low(bars) is False


def test_correlation_uses_20_daily_returns() -> None:
    a = _bars_from_closes([100 + index for index in range(21)])
    b = _bars_from_closes([200 + index * 2 for index in range(21)])

    assert correlation(a, b, 20) == pytest.approx(1.0)


def test_annualized_volatility_marks_zero_when_returns_do_not_move() -> None:
    bars = _bars_from_closes([1.0] * 21)

    assert annualized_volatility(bars, 20) == pytest.approx(0.0)


def _bars_from_closes(closes: list[float]) -> list[PriceBar]:
    first = date(2026, 1, 1)
    return [
        PriceBar(
            date=first + timedelta(days=index),
            open=close,
            close=close,
            high=close,
            low=close,
            pct_change=0.0,
            amount=0.0,
        )
        for index, close in enumerate(closes)
    ]
