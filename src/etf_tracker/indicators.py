from __future__ import annotations

import math
from statistics import mean, stdev

from .models import PriceBar


def moving_average(bars: list[PriceBar], window: int) -> float | None:
    if len(bars) < window:
        return None
    return mean(item.close for item in bars[-window:])


def prior_moving_average(bars: list[PriceBar], window: int) -> float | None:
    if len(bars) <= window:
        return None
    return mean(item.close for item in bars[-window - 1 : -1])


def is_20_day_new_low(bars: list[PriceBar]) -> bool | None:
    if len(bars) < 21:
        return None
    return bars[-1].close < min(item.close for item in bars[-21:-1])


def drawdown_from_high(bars: list[PriceBar], lookback: int = 252) -> float | None:
    if not bars:
        return None
    window = bars[-lookback:] if len(bars) >= lookback else bars
    high = max(item.close for item in window)
    if high <= 0:
        return None
    return (high - bars[-1].close) / high


def correlation(a: list[PriceBar], b: list[PriceBar], window: int = 20) -> float | None:
    if len(a) < window + 1 or len(b) < window + 1:
        return None
    returns_a = _returns(a[-window - 1 :])
    returns_b = _returns(b[-window - 1 :])
    avg_a = mean(returns_a)
    avg_b = mean(returns_b)
    denominator_a = sum((item - avg_a) ** 2 for item in returns_a)
    denominator_b = sum((item - avg_b) ** 2 for item in returns_b)
    if denominator_a <= 0 or denominator_b <= 0:
        return None
    numerator = sum((x - avg_a) * (y - avg_b) for x, y in zip(returns_a, returns_b))
    return numerator / math.sqrt(denominator_a * denominator_b)


def annualized_volatility(bars: list[PriceBar], window: int = 20) -> float | None:
    if len(bars) < window + 1:
        return None
    values = _returns(bars[-window - 1 :])
    if len(values) < 2:
        return None
    return stdev(values) * math.sqrt(252)


def consecutive_closes_below_ma(bars: list[PriceBar], ma_window: int, count: int) -> bool | None:
    if len(bars) < ma_window + count:
        return None
    for offset in range(count, 0, -1):
        slice_end = len(bars) - offset + 1
        subset = bars[:slice_end]
        ma = moving_average(subset, ma_window)
        if ma is None or subset[-1].close >= ma:
            return False
    return True


def _returns(bars: list[PriceBar]) -> list[float]:
    values: list[float] = []
    for previous, current in zip(bars, bars[1:]):
        if previous.close > 0:
            values.append((current.close - previous.close) / previous.close)
    return values

