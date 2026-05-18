from __future__ import annotations

from datetime import date, timedelta

import pytest

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


def test_kc50_before_first_buy_shows_estimated_trigger_close() -> None:
    config = _config()
    portfolio = build_portfolio(config, [])
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 1.0),
            "588000": _bars_from_closes([1.0] * 252 + [0.90]),
        },
    )
    signal = next(signal for signal in report.signals if signal.title == "科创50尚未触发第一笔买入")
    assert signal.level == "INFO"
    assert signal.detail == "尚未达到第一笔买入触发点，估算第一笔触发收盘价约 0.7800。"


def test_kc50_first_buy_trigger_hides_estimated_trigger_close() -> None:
    config = _config_with_valuation(a500_percentile=0.45, kc50_percentile=0.40)
    portfolio = build_portfolio(config, [])
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 1.0),
            "588000": _bars_from_closes([1.0] * 252 + [0.78]),
        },
    )
    titles = [signal.title for signal in report.signals]
    assert "科创50可能触发第1笔买入" in titles
    assert "科创50尚未触发第一笔买入" not in titles


def test_a500_without_trade_shows_suggested_grid_only() -> None:
    config = _config()
    portfolio = build_portfolio(config, [])
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.2, 1.0, 20),
            "588000": _bars(1.0, 1.0),
        },
    )
    titles = [signal.title for signal in report.signals]
    assert report.metrics["A500建议网格基准价"] == "1.100"
    assert report.metrics["A500建议网格上沿"] == "1.298"
    assert report.metrics["A500建议网格下沿"] == "0.902"
    assert report.metrics["A500建议动态网格间距"] == "3.00%"
    assert report.metrics["A500网格参数来源"] == "建议：当前持仓为0，基准价为最近20日收盘均价"
    assert "A500可能触发第2格补仓" not in titles
    assert "A500跌破网格下沿" not in titles


def test_a500_suggested_grid_uses_available_average_when_history_is_short() -> None:
    config = _config()
    portfolio = build_portfolio(config, [])
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.2, 1.0, 10),
            "588000": _bars(1.0, 1.0),
        },
    )
    assert report.metrics["A500建议网格基准价"] == "1.100"
    assert report.metrics["A500建议动态网格间距"] == "4.00%"
    assert report.metrics["A500网格参数来源"] == "建议：当前持仓为0，基准价为最近10日收盘均价"


def test_a500_suggested_dynamic_spacing_caps_high_atr() -> None:
    config = _config()
    portfolio = build_portfolio(config, [])
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars_with_ranges([1.0] * 20, low=0.80, high=1.20),
            "588000": _bars(1.0, 1.0),
        },
    )
    assert report.metrics["A500建议动态网格间距"] == "5.50%"


def test_a500_closed_position_shows_suggested_grid() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 1), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000, 0),
        Trade(date(2026, 1, 2), "563360", "卖出", "A500底仓趋势止盈", 1.1, 1100, 1000, 0),
    ]
    portfolio = build_portfolio(config, trades)
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.2, 1.0, 20),
            "588000": _bars(1.0, 1.0),
        },
    )
    assert report.metrics["A500持仓份额"] == "0"
    assert report.metrics["A500建议网格基准价"] == "1.100"
    assert "A500实际网格基准价" not in report.metrics


def test_a500_base_trade_generates_actual_grid() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 800, 0),
        Trade(date(2026, 1, 3), "563360", "买入", "A500初始底仓", 1.0, 500, 400, 0.6),
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
    assert report.metrics["A500实际网格基准价"] == "1.251"
    assert report.metrics["A500实际网格上沿"] == "1.439"
    assert report.metrics["A500实际网格下沿"] == "1.001"
    assert report.metrics["A500网格参数来源"] == "实际：A500底仓成交均价"
    assert "A500建议网格基准价" not in report.metrics
    assert "A500建议动态网格间距" not in report.metrics


def test_a500_grid_prefers_base_trade_over_other_buys() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 1), "563360", "买入", "A500常规网格", 1.400, 600, 429, 0),
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.100, 1000, 909, 0),
        Trade(date(2026, 1, 3), "563360", "买入", "A500常规网格", 0.900, 600, 667, 0),
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
    assert report.metrics["A500实际网格基准价"] == "1.100"
    assert report.metrics["A500网格参数来源"] == "实际：A500底仓成交均价"


def test_a500_grid_falls_back_to_position_cost_without_base_module() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 1), "563360", "买入", "A500网格", 1.050, 600, 571, 0),
        Trade(date(2026, 1, 2), "563360", "买入", "A500网格", 1.000, 600, 600, 0),
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
    assert report.metrics["A500实际网格基准价"] == "1.025"
    assert report.metrics["A500网格参数来源"] == "实际：A500当前持仓均价"


def test_a500_actual_lower_edge_warns_pause_grid() -> None:
    config = _config()
    trades = [Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000, 0)]
    portfolio = build_portfolio(config, trades)
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 0.70),
            "588000": _bars(1.0, 1.0),
        },
    )
    assert any(signal.title == "A500跌破网格下沿" for signal in report.signals)


def test_a500_actual_upper_edge_warns_after_10_days() -> None:
    config = _config()
    trades = [Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000, 0)]
    portfolio = build_portfolio(config, trades)
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars_from_closes([1.0] * 250 + [1.16] * 10),
            "588000": _bars(1.0, 1.0),
        },
    )
    assert any(signal.title == "A500连续10日高于网格上沿" for signal in report.signals)


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


def test_global_floating_loss_ratio_is_non_negative() -> None:
    config = _config()
    trades = [Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000, 1)]
    portfolio = build_portfolio(config, trades)
    loss_report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 0.90),
            "588000": _bars(1.0, 1.0),
        },
    )
    profit_report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 1.10),
            "588000": _bars(1.0, 1.0),
        },
    )

    assert loss_report.metrics["总投入成本"] == "1000.00"
    assert loss_report.metrics["当前持仓市值"] == "900.00"
    assert loss_report.metrics["总持仓浮亏比例"] == "10.00%"
    assert profit_report.metrics["总持仓浮亏比例"] == "0.00%"


def test_a500_base_trade_still_uses_fee_in_actual_grid_base() -> None:
    config = _config()
    trades = [Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000, 1)]
    portfolio = build_portfolio(config, trades)
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 1.0),
            "588000": _bars(1.0, 1.0),
        },
    )

    assert portfolio.position("563360").cost == pytest.approx(1000)
    assert report.metrics["A500实际网格基准价"] == "1.001"


def test_reserve_safety_cushion_blocks_when_reserved_cash_is_too_low() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500备用金A组", 0.8, 2100, 2625, 0),
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

    assert report.metrics["备用金未回收动用"] == "2100.00"
    assert any(signal.title == "备用金安全垫不足" for signal in report.signals)


def test_h1_risk_gate_allows_when_hard_conditions_and_two_references_pass() -> None:
    config = _config_with_valuation(a500_percentile=0.45, kc50_percentile=0.40)
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500常规网格", 1.0, 5000, 5000, 0),
        Trade(date(2026, 1, 3), "588000", "买入", "科创50波段", 1.0, 2000, 2000, 0),
    ]
    portfolio = build_portfolio(config, trades)
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _flat_bars(1.0),
            "588000": _flat_bars(1.0),
        },
    )

    assert any(signal.title == "总风险闸门通过" for signal in report.signals)
    assert report.metrics["H1硬条件-浮亏小于10%"] == "通过"
    assert report.metrics["H1参考条件通过数"] == "3/3"


def test_h1_risk_gate_blocks_when_reference_conditions_are_not_enough() -> None:
    config = _config_with_valuation(a500_percentile=None, kc50_percentile=None)
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000, 0),
        Trade(date(2026, 1, 3), "563360", "买入", "A500常规网格", 1.0, 5000, 5000, 0),
        Trade(date(2026, 1, 4), "588000", "买入", "科创50波段", 1.0, 1000, 1000, 0),
    ]
    portfolio = build_portfolio(config, trades)
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 0.90),
            "588000": _flat_bars(1.0),
        },
    )

    candidate = next(signal for signal in report.signals if signal.title == "A500可能触发第2格补仓")
    assert candidate.level == "WARN"
    assert "总风险闸门未通过" in candidate.detail
    assert report.metrics["H1参考条件通过数"] == "1/3"


def test_h1_risk_gate_blocks_when_both_symbols_are_weak() -> None:
    config = _config_with_valuation(a500_percentile=0.45, kc50_percentile=0.40)
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500常规网格", 1.0, 5000, 5000, 0),
        Trade(date(2026, 1, 3), "588000", "买入", "科创50波段", 1.0, 2000, 2000, 0),
    ]
    portfolio = build_portfolio(config, trades)
    weak_bars = _bars(1.2, 0.9)
    report = evaluate(config, portfolio, {"563360": weak_bars, "588000": weak_bars})

    assert report.metrics["H1硬条件-至少一个标的非弱势"] == "不通过"
    assert any(signal.title == "总风险闸门未通过" for signal in report.signals)


def test_h2_85_percent_converts_buy_candidates_to_blocked() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000, 0),
        Trade(date(2026, 1, 3), "563360", "买入", "A500常规网格", 1.0, 5000, 5000, 0),
        Trade(date(2026, 1, 4), "588000", "买入", "科创50波段", 1.0, 2500, 2500, 0),
    ]
    portfolio = build_portfolio(config, trades)
    report = evaluate(config, portfolio, {"563360": _bars(1.0, 0.90), "588000": _flat_bars(1.0)})

    candidate = next(signal for signal in report.signals if signal.title == "A500可能触发第2格补仓")
    assert candidate.level == "WARN"
    assert "总投入成本达到85%" in candidate.detail


def test_kc50_h3_signal_when_net_value_below_1400_takes_priority() -> None:
    config = _config()
    trades = [Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 2000, 2000, 0)]
    portfolio = build_portfolio(config, trades)
    report = evaluate(
        config,
        portfolio,
        {
            "563360": _bars(1.0, 1.0),
            "588000": _bars(1.0, 0.60),
        },
    )
    titles = [signal.title for signal in report.signals]
    assert "科创50波段净值低于1400" in titles
    assert "科创50波段净值低于1500" not in titles


def test_h4_single_day_drop_over_8_percent_warns() -> None:
    config = _config()
    portfolio = build_portfolio(config, [])
    kc50 = _flat_bars(1.0)
    kc50[-1] = PriceBar(kc50[-1].date, 0.91, 0.91, 0.91, 0.91, -8.1, 0)
    report = evaluate(config, portfolio, {"563360": _flat_bars(1.0), "588000": kc50})

    assert any(signal.title == "科创50单日收盘跌幅超过8%" for signal in report.signals)


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
                    "grid_spacing": 0.04,
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


def _config_with_valuation(a500_percentile: float | None, kc50_percentile: float | None) -> TrackerConfig:
    raw = _config().raw.copy()
    raw["valuation"] = {
        "source": "manual",
        "as_of": "2026-01-10",
        "a500_percentile": a500_percentile,
        "kc50_percentile": kc50_percentile,
    }
    return TrackerConfig(raw)


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


def _flat_bars(close: float, count: int = 260) -> list[PriceBar]:
    return _bars_from_closes([close] * count)


def _bars_from_closes(closes: list[float]) -> list[PriceBar]:
    first = date(2025, 1, 1)
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


def _bars_with_ranges(closes: list[float], low: float, high: float) -> list[PriceBar]:
    first = date(2025, 1, 1)
    return [
        PriceBar(
            date=first + timedelta(days=index),
            open=close,
            close=close,
            high=high,
            low=low,
            pct_change=0.0,
            amount=0.0,
        )
        for index, close in enumerate(closes)
    ]
