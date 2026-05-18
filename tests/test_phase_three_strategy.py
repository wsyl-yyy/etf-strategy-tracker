from __future__ import annotations

from datetime import date, timedelta

from etf_tracker.config import TrackerConfig
from etf_tracker.models import PriceBar, Trade
from etf_tracker.portfolio import build_portfolio
from etf_tracker.report import render_markdown
from etf_tracker.strategy import evaluate


def test_h6_blocks_restart_before_five_trading_days_and_adds_probe_after_cooldown() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000),
        Trade(date(2026, 1, 10), "563360", "卖出", "A500清仓", 1.0, 1000, 1000, trigger_rule="H6清仓"),
    ]

    cooling = evaluate(
        config,
        build_portfolio(config, trades),
        {"563360": _dated_bars([1.0, 1.0, 1.0, 1.0], date(2026, 1, 11)), "588000": _flat_bars(1.0)},
    )
    assert any(signal.rule_id == "H6-RESTART" and signal.level == "WARN" for signal in cooling.signals)
    assert cooling.metrics["A500重启冷却交易日"] == "4/5"

    ready = evaluate(
        config,
        build_portfolio(config, trades),
        {"563360": _dated_bars([1.0, 1.0, 1.0, 1.0, 1.0], date(2026, 1, 11)), "588000": _flat_bars(1.0)},
    )
    probe = next(signal for signal in ready.signals if signal.rule_id == "RESTART-AFTER-LIQUIDATION")
    assert probe.level == "REVIEW"
    assert probe.planned_amount == 500
    assert ready.metrics["A500重启基准价"] == "1.0000"
    assert "重锁" in probe.detail


def test_h6_flags_restart_probe_trade_above_half_first_buy() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000),
        Trade(date(2026, 1, 10), "563360", "卖出", "A500清仓", 1.0, 1000, 1000, trigger_rule="H6清仓"),
        Trade(date(2026, 1, 16), "563360", "买入", "A500重启试探仓", 1.0, 600, 600),
    ]

    report = evaluate(config, build_portfolio(config, trades), {"563360": _dated_bars([1.0] * 8, date(2026, 1, 11)), "588000": _flat_bars(1.0)})

    assert any(signal.rule_id == "RESTART-AFTER-LIQUIDATION" and "超过50%" in signal.detail for signal in report.signals)


def test_h7_three_tenth_day_loss_buys_pause_symbol_new_buys() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500普通网格", 1.00, 600, 600),
        Trade(date(2026, 1, 3), "563360", "买入", "A500普通网格", 0.98, 600, 612),
        Trade(date(2026, 1, 4), "563360", "买入", "A500普通网格", 0.96, 600, 625),
    ]
    a500 = _dated_bars([1.0] * 10 + [0.90] * 20, date(2026, 1, 1))

    report = evaluate(config, build_portfolio(config, trades), {"563360": a500, "588000": _flat_bars(1.0)})

    assert report.metrics["A500连续10日复核仍浮亏买入"] == "3笔"
    assert any(signal.rule_id == "H7-THREE-LOSS-BUYS" for signal in report.signals)


def test_strategy_invalidation_blocks_new_buys_on_account_drawdown() -> None:
    raw = _config_with_valuation().raw.copy()
    raw["account"] = {"peak_value": 12000, "strategy_start_date": "2025-01-01"}
    config = TrackerConfig(raw)
    kc50 = _bars_from_closes([1.0] * 252 + [0.70])

    report = evaluate(config, build_portfolio(config, []), {"563360": _flat_bars(1.0), "588000": kc50})

    assert report.metrics["总账户最大回撤"] == "16.67%"
    assert any(signal.rule_id == "STRATEGY-INVALIDATION" and signal.level == "WARN" for signal in report.signals)
    candidate = next(signal for signal in report.signals if signal.rule_id == "KC50-BUY-1")
    assert candidate.level == "WARN"
    assert "最大回撤" in candidate.detail


def test_strategy_invalidation_tracks_h3_rounds_a500_idle_and_benchmark_review() -> None:
    raw = _config_with_valuation().raw.copy()
    raw["account"] = {"strategy_start_date": "2025-01-01"}
    raw["benchmarks"] = {"hs300_return": 0.08, "money_fund_return": 0.015}
    config = TrackerConfig(raw)
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000),
        Trade(date(2026, 1, 3), "563360", "买入", "A500普通网格", 0.95, 600, 632, trigger_rule="A500第1格补仓"),
        Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 2000, 2000),
        Trade(date(2026, 1, 3), "588000", "卖出", "科创50 H3清仓", 1.0, 2000, 2000, trigger_rule="H3净值低于1400清仓"),
        Trade(date(2026, 1, 4), "588000", "买入", "科创50波段", 1.0, 2000, 2000),
        Trade(date(2026, 1, 5), "588000", "卖出", "科创50 H3清仓", 1.0, 2000, 2000, trigger_rule="H3净值低于1400清仓"),
    ]
    a500 = _dated_bars([0.980392] * 100, date(2026, 1, 4))
    kc50 = _dated_bars([1.0] * 25, date(2026, 1, 6))

    report = evaluate(config, build_portfolio(config, trades), {"563360": a500, "588000": kc50})

    rule_ids = {signal.rule_id for signal in report.signals}
    assert "KC50-H3-ROUNDS" in rule_ids
    assert "A500-GRID-IDLE-90" in rule_ids
    assert "STRATEGY-INVALIDATION" in rule_ids
    assert report.metrics["策略运行收益对比"] == "本账户 0.00%；沪深300 8.00%；货币基金 1.50%"


def test_profit_lock_warns_and_locked_cash_is_not_a_trading_pool() -> None:
    raw = _config().raw.copy()
    raw["funds"] = {"a500_grid": 7000, "kc50_wave": 3000, "reserve": 2500}
    raw["account"] = {"locked_cash": 500, "peak_value": 12500}
    config = TrackerConfig(raw)
    portfolio = build_portfolio(config, [])

    report = evaluate(config, portfolio, {"563360": _flat_bars(1.0), "588000": _flat_bars(1.0)})

    assert "locked_cash" not in portfolio.cash
    assert report.metrics["不可交易现金池"] == "500.00"
    assert report.metrics["收益锁定建议金额"] == "1000.00"
    assert any(signal.rule_id == "PROFIT-LOCK" and "至少提取" in signal.detail for signal in report.signals)


def test_kc50_reserve_recovery_waits_for_manual_confirmation_then_adds_review_candidate() -> None:
    missing = _config_with_recovery(confirmed=False)
    trades = _h3_clear_trades()
    histories = {"563360": _flat_bars(1.0), "588000": _dated_bars([1.0] * 5, date(2026, 1, 4))}

    waiting = evaluate(missing, build_portfolio(missing, trades), histories)
    assert any(signal.rule_id == "KC50-RESERVE-RECOVERY" and "等待人工确认" in signal.detail for signal in waiting.signals)
    assert not any(signal.rule_id == "KC50-RESERVE-RECOVERY-BUY" for signal in waiting.signals)

    confirmed = _config_with_recovery(confirmed=True)
    ready = evaluate(confirmed, build_portfolio(confirmed, trades), histories)
    candidate = next(signal for signal in ready.signals if signal.rule_id == "KC50-RESERVE-RECOVERY-BUY")
    assert candidate.level == "REVIEW"
    assert candidate.planned_amount == 1000


def test_kc50_reserve_recovery_blocks_when_one_reserve_tranche_already_exists() -> None:
    config = _config_with_recovery(confirmed=True)
    trades = _h3_clear_trades() + [
        Trade(date(2026, 1, 5), "588000", "买入", "科创50备用金回暖", 1.0, 1000, 1000),
    ]

    report = evaluate(config, build_portfolio(config, trades), {"563360": _flat_bars(1.0), "588000": _dated_bars([1.0] * 6, date(2026, 1, 4))})

    assert any(signal.rule_id == "KC50-RESERVE-RECOVERY" and "已使用1份" in signal.detail for signal in report.signals)


def test_phase_three_lifecycle_evidence_is_rendered_in_markdown() -> None:
    raw = _config().raw.copy()
    raw["funds"] = {"a500_grid": 7000, "kc50_wave": 3000, "reserve": 2500}
    raw["account"] = {"locked_cash": 500}
    config = TrackerConfig(raw)
    report = evaluate(config, build_portfolio(config, []), {"563360": _flat_bars(1.0), "588000": _flat_bars(1.0)})

    markdown = render_markdown(report, build_portfolio(config, []))

    assert "不可交易现金池" in markdown
    assert "收益锁定" in markdown


def _config() -> TrackerConfig:
    return TrackerConfig(
        {
            "timezone": "Asia/Shanghai",
            "total_capital": 10000,
            "funds": {"a500_grid": 5000, "kc50_wave": 2000, "reserve": 3000},
            "symbols": {
                "a500": {
                    "code": "563360",
                    "base_position_amount": 1000,
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


def _config_with_valuation() -> TrackerConfig:
    raw = _config().raw.copy()
    raw["valuation"] = {
        "source": "manual",
        "as_of": "2026-01-10",
        "a500_percentile": 0.40,
        "kc50_percentile": 0.40,
    }
    return TrackerConfig(raw)


def _config_with_recovery(confirmed: bool) -> TrackerConfig:
    raw = _config_with_valuation().raw.copy()
    raw["reserve"] = {
        "kc50_recovery": {
            "confirmed": confirmed,
            "source": "manual review",
            "as_of": "2026-01-04",
            "note": "H3后回暖软信号人工确认",
            "max_amount": 1000,
        }
    }
    return TrackerConfig(raw)


def _h3_clear_trades() -> list[Trade]:
    return [
        Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 2000, 2000),
        Trade(date(2026, 1, 3), "588000", "卖出", "科创50 H3清仓", 1.0, 2000, 2000, trigger_rule="H3净值低于1400清仓"),
    ]


def _flat_bars(close: float, count: int = 260) -> list[PriceBar]:
    return _dated_bars([close] * count, date(2025, 1, 1))


def _bars_from_closes(closes: list[float]) -> list[PriceBar]:
    return _dated_bars(closes, date(2025, 1, 1))


def _dated_bars(closes: list[float], first: date) -> list[PriceBar]:
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
