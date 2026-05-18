from __future__ import annotations

from datetime import date

import pytest

from etf_tracker.cli import _derive_worker_submit_url
from etf_tracker.trades import _trades_from_worker_payload


def test_worker_payload_maps_trade_fields() -> None:
    trades = _trades_from_worker_payload(
        {
            "trades": [
                {
                    "date": "2026-05-11",
                    "symbol": "563360",
                    "side": "买入",
                    "module": "A500网格",
                    "price": 1.032,
                    "amount": 600,
                    "shares": 500,
                    "fee": 0.06,
                    "note": "测试成交",
                }
            ]
        }
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade.date == date(2026, 5, 11)
    assert trade.symbol == "563360"
    assert trade.is_buy
    assert trade.module == "A500网格"
    assert trade.price == pytest.approx(1.032)
    assert trade.amount == pytest.approx(600)
    assert trade.shares == pytest.approx(500)
    assert trade.fee == pytest.approx(0.06)


def test_worker_payload_maps_audit_fields_and_keeps_date_compatibility() -> None:
    trades = _trades_from_worker_payload(
        {
            "trades": [
                {
                    "date": "2026-05-12",
                    "signal_date": "2026-05-11",
                    "execution_date": "2026-05-12",
                    "symbol": "563360",
                    "side": "买入",
                    "module": "A500网格",
                    "trigger_rule": "A500第2格补仓",
                    "price": 1.032,
                    "amount": 600,
                    "shares": 500,
                    "fee": 0.06,
                    "cash_balance": 4380,
                    "risk_gate_triggered": True,
                    "risk_gate_snapshot": "H1: allow=false",
                    "compliance_warnings": ["科创50买入份额不是100份整数倍。"],
                    "note": "测试成交",
                }
            ]
        }
    )

    trade = trades[0]
    assert trade.date == date(2026, 5, 12)
    assert trade.signal_date == date(2026, 5, 11)
    assert trade.execution_date == date(2026, 5, 12)
    assert trade.trigger_rule == "A500第2格补仓"
    assert trade.cash_balance == pytest.approx(4380)
    assert trade.risk_gate_triggered is True
    assert trade.risk_gate_snapshot == "H1: allow=false"
    assert trade.compliance_warnings == ["科创50买入份额不是100份整数倍。"]


def test_worker_payload_old_date_fills_signal_and_execution_dates() -> None:
    trades = _trades_from_worker_payload(
        {
            "trades": [
                {
                    "date": "2026-05-12",
                    "symbol": "563360",
                    "side": "买入",
                    "module": "A500网格",
                    "price": 1.032,
                    "amount": 600,
                    "shares": 500,
                }
            ]
        }
    )

    trade = trades[0]
    assert trade.signal_date == date(2026, 5, 12)
    assert trade.execution_date == date(2026, 5, 12)


def test_worker_payload_rejects_unexpected_shape() -> None:
    with pytest.raises(ValueError):
        _trades_from_worker_payload({"trades": {"date": "2026-05-11"}})


def test_worker_submit_url_is_derived_from_trades_url() -> None:
    assert _derive_worker_submit_url("https://example.workers.dev/trades") == "https://example.workers.dev/trade"
    assert _derive_worker_submit_url("https://example.workers.dev") == "https://example.workers.dev/trade"
