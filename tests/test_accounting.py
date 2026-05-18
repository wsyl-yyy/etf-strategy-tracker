from __future__ import annotations

from datetime import date

import pytest

from etf_tracker.config import TrackerConfig
from etf_tracker.models import Trade
from etf_tracker.portfolio import build_portfolio


def test_position_cost_excludes_buy_fee_but_cash_includes_fee() -> None:
    config = _config()
    portfolio = build_portfolio(
        config,
        [Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000, 1)],
    )

    position = portfolio.position("563360")
    assert position.cost == pytest.approx(1000)
    assert position.avg_cost == pytest.approx(1.0)
    assert portfolio.cash["a500_grid"] == pytest.approx(3999)


def test_symbol_sells_return_to_their_own_pool() -> None:
    config = _config()
    portfolio = build_portfolio(
        config,
        [
            Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 400, 400, 0),
            Trade(date(2026, 1, 3), "588000", "卖出", "科创50波段止盈", 1.1, 440, 400, 0),
        ],
    )

    assert portfolio.cash["kc50_wave"] == pytest.approx(2040)
    assert portfolio.cash["a500_grid"] == pytest.approx(5000)
    assert portfolio.cash["reserve"] == pytest.approx(3000)


def test_reserve_module_uses_reserve_pool_independently() -> None:
    config = _config()
    portfolio = build_portfolio(
        config,
        [Trade(date(2026, 1, 2), "563360", "买入", "A500备用金A组", 0.8, 1000, 1250, 0)],
    )

    assert portfolio.cash["reserve"] == pytest.approx(2000)
    assert portfolio.cash["a500_grid"] == pytest.approx(5000)


def _config() -> TrackerConfig:
    return TrackerConfig(
        {
            "timezone": "Asia/Shanghai",
            "total_capital": 10000,
            "funds": {"a500_grid": 5000, "kc50_wave": 2000, "reserve": 3000},
            "symbols": {
                "a500": {"code": "563360"},
                "kc50": {"code": "588000", "buy_steps": [{"drawdown": 0.22, "amount": 400}]},
            },
        }
    )
