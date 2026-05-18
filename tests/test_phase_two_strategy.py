from __future__ import annotations

from datetime import date, timedelta

from etf_tracker.config import TrackerConfig
from etf_tracker.models import PriceBar, Trade
from etf_tracker.portfolio import build_portfolio
from etf_tracker.strategy import evaluate


def test_a500_default_grid_spacing_is_five_point_five_percent_and_skips_executed_level() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000),
        Trade(
            date(2026, 1, 3),
            "563360",
            "买入",
            "A500常规网格",
            0.945,
            600,
            635,
            trigger_rule="A500第1格补仓",
        ),
    ]
    portfolio = build_portfolio(config, trades)
    report = evaluate(config, portfolio, {"563360": _bars(1.0, 0.945), "588000": _flat_bars(1.0)})

    titles = [signal.title for signal in report.signals]
    assert report.metrics["A500实际网格间距"] == "5.50%"
    assert "A500可能触发第1格补仓" not in titles
    assert report.metrics["A500开放普通网格"] == "第1格 635份"


def test_a500_open_grid_buy_generates_pair_sell_candidate() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000),
        Trade(
            date(2026, 1, 3),
            "563360",
            "买入",
            "A500常规网格",
            0.900,
            600,
            667,
            trigger_rule="A500第2格补仓",
        ),
    ]
    portfolio = build_portfolio(config, trades)
    report = evaluate(config, portfolio, {"563360": _bars(1.0, 0.950), "588000": _flat_bars(1.0)})

    signal = next(signal for signal in report.signals if signal.title == "A500第2格网格卖出")
    assert signal.level == "ACTION"
    assert "667份" in signal.detail


def test_a500_base_profit_and_trailing_profit_are_not_repeated() -> None:
    config = _config()
    first_profit = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000),
    ]
    first_report = evaluate(
        config,
        build_portfolio(config, first_profit),
        {"563360": _dated_bars([1.0] * 20 + [1.16], date(2026, 1, 1)), "588000": _flat_bars(1.0)},
    )
    assert any(signal.title == "A500底仓盈利达到15%" for signal in first_report.signals)

    after_take_profit = first_profit + [
        Trade(
            date(2026, 1, 5),
            "563360",
            "卖出",
            "A500底仓趋势止盈",
            1.15,
            575,
            500,
            trigger_rule="A500底仓第一次止盈15%",
        )
    ]
    trailing_report = evaluate(
        config,
        build_portfolio(config, after_take_profit),
        {"563360": _dated_bars([1.16, 1.20, 1.18, 1.10], date(2026, 1, 5)), "588000": _flat_bars(1.0)},
    )
    titles = [signal.title for signal in trailing_report.signals]
    assert "A500底仓盈利达到15%" not in titles
    assert "A500底仓回撤达到8%" in titles
    assert trailing_report.metrics["A500底仓阶段高点"] == "1.2000"


def test_a500_downtrend_protection_blocks_common_grid_buy() -> None:
    config = _config()
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000),
        Trade(date(2026, 1, 3), "563360", "买入", "A500常规网格", 0.945, 600, 635, trigger_rule="A500第1格补仓"),
        Trade(date(2026, 1, 4), "563360", "买入", "A500常规网格", 0.890, 600, 674, trigger_rule="A500第2格补仓"),
        Trade(date(2026, 1, 5), "563360", "买入", "A500常规网格", 0.835, 600, 719, trigger_rule="A500第3格补仓"),
    ]
    report = evaluate(config, build_portfolio(config, trades), {"563360": _bars(1.0, 0.779), "588000": _flat_bars(1.0)})

    candidate = next(signal for signal in report.signals if signal.title == "A500可能触发第4格补仓")
    assert candidate.level == "WARN"
    assert "A500单边下跌保护" in candidate.detail


def test_a500_reserve_candidate_requires_valuation_and_keeps_review_level() -> None:
    config = _config_with_valuation(a500_percentile=0.15, kc50_percentile=0.40)
    trades = [Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000)]
    report = evaluate(config, build_portfolio(config, trades), {"563360": _bars(1.0, 0.66), "588000": _flat_bars(1.0)})

    signal = next(signal for signal in report.signals if signal.title == "A500备用金A组候选")
    assert signal.level == "REVIEW"
    assert "人工复核" in signal.detail
    assert report.metrics["A500备用金A组"] == "通过"


def test_a500_reserve_tracks_b_c_candidates_and_position_limits() -> None:
    config = _config_with_valuation(a500_percentile=0.08, kc50_percentile=0.40)
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000),
        Trade(date(2026, 1, 3), "563360", "买入", "A500备用金A组", 0.80, 1000, 1250, trigger_rule="A500备用金A组"),
    ]
    a500_bars = _dated_bars([1.0] * 30 + [0.64], date(2026, 1, 4))
    report = evaluate(config, build_portfolio(config, trades), {"563360": a500_bars, "588000": _flat_bars(1.0)})

    titles = [signal.title for signal in report.signals]
    assert "A500备用金B组候选" in titles
    assert "A500备用金C组候选" in titles
    assert any(signal.title == "A500备用金亏损超过8%" for signal in report.signals)
    assert report.metrics["A500备用金持仓浮盈亏"] == "-20.00%"


def test_kc50_second_and_third_buy_use_locked_stage_high() -> None:
    config = _config_with_valuation(a500_percentile=0.40, kc50_percentile=0.40)
    first = [Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 400, 400, trigger_rule="科创50第1笔买入")]
    second_report = evaluate(
        config,
        build_portfolio(config, first),
        {"563360": _flat_bars(1.0), "588000": _bars_from_closes([1.0] * 252 + [0.70])},
    )
    assert any(signal.title == "科创50可能触发第2笔买入" and signal.level == "ACTION" for signal in second_report.signals)

    first_two = first + [
        Trade(date(2026, 1, 3), "588000", "买入", "科创50波段", 0.70, 500, 714, trigger_rule="科创50第2笔买入")
    ]
    third_report = evaluate(
        config,
        build_portfolio(config, first_two),
        {"563360": _flat_bars(1.0), "588000": _bars_from_closes([1.0] * 252 + [0.62])},
    )
    assert any(signal.title == "科创50可能触发第3笔买入" and signal.level == "ACTION" for signal in third_report.signals)


def test_kc50_first_buy_high_valuation_delays_until_deeper_drawdown() -> None:
    config = _config_with_valuation(a500_percentile=0.40, kc50_percentile=0.60)
    delayed = evaluate(config, build_portfolio(config, []), {"563360": _flat_bars(1.0), "588000": _bars_from_closes([1.0] * 252 + [0.78])})
    assert any(signal.title == "科创50第一笔买入估值偏高延后" for signal in delayed.signals)
    assert not any(signal.title == "科创50可能触发第1笔买入" for signal in delayed.signals)

    deeper = evaluate(config, build_portfolio(config, []), {"563360": _flat_bars(1.0), "588000": _bars_from_closes([1.0] * 252 + [0.75])})
    assert any(signal.title == "科创50可能触发第1笔买入" for signal in deeper.signals)


def test_kc50_fourth_buy_requires_third_buy_filters_and_low_pe_or_pb() -> None:
    raw = _config_with_valuation(a500_percentile=0.40, kc50_percentile=0.40, kc50_pe_percentile=0.24).raw.copy()
    raw["funds"] = {"a500_grid": 5000, "kc50_wave": 3000, "reserve": 3000}
    config = TrackerConfig(raw)
    trades = [
        Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 400, 400, trigger_rule="科创50第1笔买入"),
        Trade(date(2026, 1, 3), "588000", "买入", "科创50波段", 0.70, 500, 714, trigger_rule="科创50第2笔买入"),
        Trade(date(2026, 1, 4), "588000", "买入", "科创50波段", 0.62, 500, 806, trigger_rule="科创50第3笔买入"),
    ]
    kc50 = _dated_bars([1.0] * 230 + [0.54] * 20 + [0.55, 0.56, 0.57, 0.56, 0.55], date(2025, 1, 1))
    report = evaluate(config, build_portfolio(config, trades), {"563360": _flat_bars(1.0), "588000": kc50})

    signal = next(signal for signal in report.signals if signal.title == "科创50可能触发第4笔买入")
    assert signal.level == "REVIEW"
    assert "第4笔过滤通过" in signal.detail


def test_kc50_profit_take_and_trailing_profit_follow_recorded_sells() -> None:
    config = _config_with_valuation(a500_percentile=0.40, kc50_percentile=0.40)
    buy = [Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 900, 900)]
    first_report = evaluate(config, build_portfolio(config, buy), {"563360": _flat_bars(1.0), "588000": _bars(1.0, 1.16)})
    assert any(signal.title == "科创50盈利达到15%" for signal in first_report.signals)

    fixed_sells = buy + [
        Trade(date(2026, 1, 3), "588000", "卖出", "科创50波段止盈", 1.15, 345, 300, trigger_rule="科创50第一次止盈15%"),
        Trade(date(2026, 1, 4), "588000", "卖出", "科创50波段止盈", 1.30, 390, 300, trigger_rule="科创50第二次止盈30%"),
    ]
    trailing = _dated_bars([1.30, 1.40, 1.36, 1.22], date(2026, 1, 4))
    trailing_report = evaluate(config, build_portfolio(config, fixed_sells), {"563360": _flat_bars(1.0), "588000": trailing})
    titles = [signal.title for signal in trailing_report.signals]
    assert "科创50盈利达到15%" not in titles
    assert "科创50移动止盈触发" in titles
    assert trailing_report.metrics["科创50移动止盈回撤阈值"] == "12.00%"


def test_kc50_h3_recovery_requires_five_trading_days_above_1800() -> None:
    config = _config_with_valuation(a500_percentile=0.40, kc50_percentile=0.40)
    trades = [
        Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 2000, 2000),
        Trade(date(2026, 1, 8), "588000", "卖出", "科创50 H3减仓", 0.75, 750, 1000, trigger_rule="H3净值低于1500减仓"),
    ]
    four_days = evaluate(
        config,
        build_portfolio(config, trades),
        {"563360": _flat_bars(1.0), "588000": _dated_bars([1.06, 1.06, 1.06, 1.06], date(2026, 1, 9))},
    )
    assert four_days.metrics["科创50 H3恢复状态"] == "未恢复"

    five_days = evaluate(
        config,
        build_portfolio(config, trades),
        {"563360": _flat_bars(1.0), "588000": _dated_bars([1.06, 1.06, 1.06, 1.06, 1.06], date(2026, 1, 9))},
    )
    assert five_days.metrics["科创50 H3恢复状态"] == "已恢复"


def test_kc50_net_value_below_1700_blocks_new_buy() -> None:
    raw = _config_with_valuation(a500_percentile=0.40, kc50_percentile=0.40).raw.copy()
    raw["funds"] = {"a500_grid": 5000, "kc50_wave": 1800, "reserve": 3000}
    config = TrackerConfig(raw)
    trades = [Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 400, 400, trigger_rule="科创50第1笔买入")]
    report = evaluate(
        config,
        build_portfolio(config, trades),
        {"563360": _flat_bars(1.0), "588000": _bars_from_closes([1.0] * 252 + [0.70])},
    )

    assert any(signal.title == "科创50波段净值低于1700" for signal in report.signals)
    candidate = next(signal for signal in report.signals if signal.title == "科创50可能触发第2笔买入")
    assert candidate.level == "WARN"
    assert "净值低于1700" in candidate.detail


def test_kc50_time_review_and_two_buy_risk_pause() -> None:
    config = _config_with_valuation(a500_percentile=0.40, kc50_percentile=0.40)
    trades = [
        Trade(date(2025, 1, 2), "588000", "买入", "科创50波段", 1.0, 400, 400, trigger_rule="科创50第1笔买入"),
        Trade(date(2025, 1, 3), "588000", "买入", "科创50波段", 0.9, 500, 556, trigger_rule="科创50第2笔买入"),
    ]
    bars = _dated_bars([1.0] * 180 + [1.10] * 20 + [1.04] * 10, date(2025, 1, 1))
    report = evaluate(config, build_portfolio(config, trades), {"563360": _flat_bars(1.0), "588000": bars})

    assert any(signal.title == "科创50连续10日低于20日均线" for signal in report.signals)
    assert any(signal.title == "科创50持仓超过60个交易日复核" for signal in report.signals)


def test_kc50_graded_risk_covers_third_and_fourth_buy_stages() -> None:
    raw = _config_with_valuation(a500_percentile=0.40, kc50_percentile=0.40).raw.copy()
    raw["funds"] = {"a500_grid": 5000, "kc50_wave": 4000, "reserve": 3000}
    config = TrackerConfig(raw)
    first_three = [
        Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 400, 400, trigger_rule="科创50第1笔买入"),
        Trade(date(2026, 1, 3), "588000", "买入", "科创50波段", 0.9, 500, 556, trigger_rule="科创50第2笔买入"),
        Trade(date(2026, 1, 4), "588000", "买入", "科创50波段", 0.8, 500, 625, trigger_rule="科创50第3笔买入"),
    ]
    bars = _bars_from_closes([1.10] * 40 + [1.0 - index * 0.012 for index in range(30)])
    third_report = evaluate(config, build_portfolio(config, first_three), {"563360": _flat_bars(1.0), "588000": bars})
    assert any(signal.title == "科创50第4笔需分级风控复核" for signal in third_report.signals)

    first_four = first_three + [
        Trade(date(2026, 1, 5), "588000", "买入", "科创50波段", 0.7, 600, 857, trigger_rule="科创50第4笔买入")
    ]
    fourth_report = evaluate(config, build_portfolio(config, first_four), {"563360": _flat_bars(1.0), "588000": bars})
    assert any(signal.title == "科创50满4笔完整风控减仓" for signal in fourth_report.signals)


def test_s2_high_volatility_uses_thirteen_percent_trailing_profit_threshold() -> None:
    config = _config_with_valuation(a500_percentile=0.40, kc50_percentile=0.40)
    trades = [
        Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 900, 900),
        Trade(date(2026, 1, 3), "588000", "卖出", "科创50波段止盈", 1.15, 345, 300, trigger_rule="科创50第一次止盈15%"),
        Trade(date(2026, 1, 4), "588000", "卖出", "科创50波段止盈", 1.30, 390, 300, trigger_rule="科创50第二次止盈30%"),
    ]
    volatile_bars = _dated_bars([1.30, 1.45] + [1.25, 1.45] * 11 + [1.30], date(2026, 1, 4))
    report = evaluate(config, build_portfolio(config, trades), {"563360": _flat_bars(1.0), "588000": volatile_bars})

    assert report.metrics["科创50移动止盈回撤阈值"] == "13.00%"
    assert any(signal.title == "科创50处于高波动阶段" for signal in report.signals)


def test_s1_high_correlation_limits_same_day_new_buy_plan_and_prioritizes_a500() -> None:
    config = _config_with_valuation(a500_percentile=0.40, kc50_percentile=0.40)
    trades = [
        Trade(date(2026, 1, 2), "563360", "买入", "A500初始底仓", 1.0, 1000, 1000),
        Trade(date(2026, 1, 2), "588000", "买入", "科创50波段", 1.0, 400, 400, trigger_rule="科创50第1笔买入"),
    ]
    a500 = _bars_from_closes([1.0] * 240 + [1.0] * 20 + [0.945])
    kc50 = _bars_from_closes([1.0] * 240 + [0.7407] * 20 + [0.70])
    report = evaluate(config, build_portfolio(config, trades), {"563360": a500, "588000": kc50})

    assert float(report.metrics["20日收益相关系数"]) > 0.85
    kc50_signal = next(signal for signal in report.signals if signal.title == "科创50可能触发第2笔买入")
    assert kc50_signal.level == "WARN"
    assert "S1相关性限制" in kc50_signal.detail


def _config() -> TrackerConfig:
    return TrackerConfig(
        {
            "timezone": "Asia/Shanghai",
            "total_capital": 10000,
            "funds": {"a500_grid": 5000, "kc50_wave": 2000, "reserve": 3000},
            "symbols": {
                "a500": {
                    "code": "563360",
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


def _config_with_valuation(
    a500_percentile: float | None,
    kc50_percentile: float | None,
    kc50_pe_percentile: float | None = None,
    kc50_pb_percentile: float | None = None,
) -> TrackerConfig:
    raw = _config().raw.copy()
    raw["valuation"] = {
        "source": "manual",
        "as_of": "2026-01-10",
        "a500_percentile": a500_percentile,
        "kc50_percentile": kc50_percentile,
        "kc50_pe_percentile": kc50_pe_percentile,
        "kc50_pb_percentile": kc50_pb_percentile,
    }
    return TrackerConfig(raw)


def _bars(start: float, end: float, count: int = 260) -> list[PriceBar]:
    return _dated_bars([start + (end - start) * (index / (count - 1)) for index in range(count)], date(2025, 1, 1))


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
