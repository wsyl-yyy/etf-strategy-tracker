from __future__ import annotations

from datetime import date

from etf_tracker.models import PortfolioState, Trade
from etf_tracker.report import render_markdown
from etf_tracker.strategy import Signal, StrategyReport


def test_recent_trades_show_fee_and_audit_fields() -> None:
    report = StrategyReport(
        date=date(2026, 5, 18),
        signals=[Signal("INFO", "今日无动作", "未发现明确动作。")],
        metrics={},
        warnings=[],
    )
    portfolio = PortfolioState(
        cash={"a500_grid": 4380.0},
        trades=[
            Trade(
                date=date(2026, 5, 18),
                symbol="563360",
                side="买入",
                module="A500网格",
                price=1.032,
                amount=600,
                shares=500,
                fee=0.06,
                signal_date=date(2026, 5, 17),
                execution_date=date(2026, 5, 18),
                trigger_rule="A500第2格补仓",
                cash_balance=4380,
                risk_gate_triggered=True,
                risk_gate_snapshot="H1: allow=false",
                compliance_warnings=["科创50买入份额不是100份整数倍。"],
            )
        ],
    )

    markdown = render_markdown(report, portfolio)

    assert "交易费用：0.06" in markdown
    assert "模块：A500网格" in markdown
    assert "触发规则：A500第2格补仓" in markdown
    assert "现金余额：4380.00" in markdown
    assert "闸门快照：H1: allow=false" in markdown
    assert "合规警告：科创50买入份额不是100份整数倍。" in markdown
