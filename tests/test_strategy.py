from __future__ import annotations

from datetime import date, timedelta

from etf_tracker.config import TrackerConfig
from etf_tracker.models import PriceBar, Trade
from etf_tracker.portfolio import build_portfolio
from etf_tracker.strategy import evaluate


def test_kc50_h3_signal_when_net_value_below_1500() -> None:
    config = _config()
    trades = [Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 2000, 2000, 0)]
    portfolio = build_portfolio(config, trades)
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 1.0),
            "588000": _bars(1.0, 0.70),
        },
    )
    titles = [signal.title for signal in report.signals]
    assert "科创50波段净值低于1500" in titles


def test_a500_lower_edge_warns_pause_grid() -> None:
    config = _config()
    portfolio = build_portfolio(config, [])
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 0.80),
            "588000": _bars(1.0, 1.0),
        },
    )
    assert any(signal.title == "A500跌破网格下沿" for signal in report.signals)


def test_global_85_percent_blocks_new_buys() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500常规网格", 1.0, 5000, 5000, 0),
        Trade(date(2026, 1, 3), "588000", "买入", "科创50波段", 1.0, 3600, 3600, 0),
    ]
    portfolio = build_portfolio(config, trades)
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 1.0),
            "588000": _bars(1.0, 1.0),
        },
    )
    assert any(signal.title == "总投入成本达到85%" for signal in report.signals)


def _config() -> TrackerConfig:
    return TrackerConfig(
        {
            "timezone": "Asia/Shanghai",
            "total_capital": 10000,
            "funds": {"a500_grid": 5000, "kc50_wave": 2000, "reserve": 3000},
            "symbols": {
                "a500": {
                    "code": "563360",
                    "grid_base_price": 1.0,
                    "grid_spacing": 0.058,
                    "grid_lower": 0.85,
                    "grid_upper": 1.2,
                    "max_grid_buys": 5,
                },
                "kc50": {
                    "code": "588000",
                    "buy_steps": [
                        {"drawdown": 0.22, "amount": 400},
                        {"drawdown": 0.30, "amount": 500},
                        {"drawdown": 0.38, "amount": 500},
                        {"drawdown": 0.45, "amount": 600},
                    ],
                },
            },
        }
    )


def _bars(start: float, end: float, count: int = 260) -> list[PriceBar]:
    first = date(2025, 1, 1)
    bars: list[PriceBar] = []
    for index in range(count):
        ratio = index / (count - 1)
        close = start + (end - start) * ratio
        bars.append(
            PriceBar(
                date=first + timedelta(days=index),
                open=close,
                close=close,
                high=close,
                low=close,
                pct_change=0.0,
                amount=0.0,
            )
        )
    return bars

