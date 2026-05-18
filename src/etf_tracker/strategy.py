from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

from .config import TrackerConfig
from .indicators import (
    annualized_volatility,
    consecutive_closes_below_ma,
    correlation,
    drawdown_from_high,
    is_20_day_new_low,
    moving_average,
    prior_moving_average,
)
from .models import PortfolioState, PriceBar
from .valuation import valuation_status


@dataclass(frozen=True)
class Signal:
    level: str
    title: str
    detail: str
    symbol: str = ""
    action: str = ""
    planned_amount: float = 0.0
    is_new_buy: bool = False
    rule_id: str = ""


@dataclass(frozen=True)
class StrategyReport:
    date: date | None
    signals: list[Signal]
    metrics: dict[str, str]
    warnings: list[str]


@dataclass(frozen=True)
class A500GridParameters:
    base_price: float
    lower: float
    upper: float
    source: str
    is_actual: bool
    suggested_spacing: float | None = None


A500_BASE_GRID_SPACING = 0.055
A500_SUGGESTED_FALLBACK_SPACING = 0.04


def evaluate(
    config: TrackerConfig,
    portfolio: PortfolioState,
    histories: dict[str, list[PriceBar]],
) -> StrategyReport:
    signals: list[Signal] = []
    warnings: list[str] = []
    metrics: dict[str, str] = {}

    a500_code = config.a500_code
    kc50_code = config.kc50_code
    a500 = histories.get(a500_code, [])
    kc50 = histories.get(kc50_code, [])
    latest_date = _latest_common_date(a500, kc50)

    if not a500:
        warnings.append("A500 行情数据不可用。")
    if not kc50:
        warnings.append("科创50 行情数据不可用。")

    if a500:
        _evaluate_a500(config, portfolio, a500, signals, warnings, metrics)
    if kc50:
        _evaluate_kc50(config, portfolio, kc50, signals, warnings, metrics)
    if a500 and kc50:
        _evaluate_global(config, portfolio, a500, kc50, signals, metrics)

    if a500 or kc50:
        _evaluate_lifecycle_and_long_term(config, portfolio, histories, signals, metrics)

    _audit_trade_records(portfolio, warnings)
    warnings.append(valuation_status())

    if latest_date is not None:
        today_cn = datetime.now(ZoneInfo(config.raw.get("timezone", "Asia/Shanghai"))).date()
        if latest_date < today_cn:
            warnings.append(f"行情最新日期为 {latest_date}，不是今天；若今天是交易日，请等待数据源更新。")

    if not signals:
        if not a500 or not kc50:
            signals.append(Signal("WARN", "数据不足，无法判断交易动作", "行情数据不完整，本次报告只更新记录，不给出买卖结论。"))
        else:
            signals.append(Signal("INFO", "今日无动作", "未发现明确买入、卖出、暂停或清仓触发。"))

    return StrategyReport(date=latest_date, signals=signals, metrics=metrics, warnings=warnings)


def _evaluate_a500(
    config: TrackerConfig,
    portfolio: PortfolioState,
    bars: list[PriceBar],
    signals: list[Signal],
    warnings: list[str],
    metrics: dict[str, str],
) -> None:
    symbol_config = config.symbols["a500"]
    latest = bars[-1]
    position = portfolio.position(config.a500_code)
    metrics["A500收盘价"] = f"{latest.close:.4f}"
    metrics["A500持仓份额"] = f"{position.shares:.0f}"

    grid_spacing = float(symbol_config.get("grid_spacing", A500_BASE_GRID_SPACING))
    max_grid_buys = int(symbol_config.get("max_grid_buys", 5))
    grid_parameters = _a500_grid_parameters(config, portfolio, bars, grid_spacing, max_grid_buys)
    open_grids = _a500_open_grid_positions(config, portfolio, grid_parameters.base_price, grid_spacing)
    downtrend_paused = grid_parameters.is_actual and _a500_downtrend_paused(open_grids, bars, grid_parameters)
    label = "实际" if grid_parameters.is_actual else "建议"
    metrics[f"A500{label}网格基准价"] = f"{grid_parameters.base_price:.3f}"
    metrics[f"A500{label}网格上沿"] = f"{grid_parameters.upper:.3f}"
    metrics[f"A500{label}网格下沿"] = f"{grid_parameters.lower:.3f}"
    if grid_parameters.is_actual:
        metrics["A500实际网格间距"] = f"{grid_spacing:.2%}"
        metrics["A500开放普通网格"] = _format_a500_open_grids(open_grids)
        metrics["A500单边下跌保护"] = "暂停普通网格补仓" if downtrend_paused else "未触发"
    if grid_parameters.suggested_spacing is not None:
        metrics["A500建议动态网格间距"] = f"{grid_parameters.suggested_spacing:.2%}"
    metrics["A500网格参数来源"] = grid_parameters.source

    if grid_parameters.is_actual:
        base_price = grid_parameters.base_price
        drawdown = (base_price - latest.close) / base_price if base_price > 0 else 0
        grid_level = int(drawdown / grid_spacing + 1e-12) if drawdown > 0 else 0
        metrics["A500相对基准回撤"] = f"{drawdown:.2%}"
        if grid_level > 0:
            if grid_level <= max_grid_buys:
                if grid_level not in open_grids:
                    level = "WARN" if downtrend_paused or latest.close < grid_parameters.lower else "REVIEW"
                    reason = "A500单边下跌保护触发，暂停普通网格补仓。" if downtrend_paused else "需结合风险闸门和 S1 限额确认是否下单。"
                    if latest.close < grid_parameters.lower:
                        reason = "A500跌破网格下沿，暂停普通网格补仓。"
                    signals.append(
                        Signal(
                            level,
                            f"A500可能触发第{grid_level}格补仓",
                            reason,
                            symbol=config.a500_code,
                            action="buy",
                            planned_amount=600,
                            is_new_buy=True,
                            rule_id="A500-GRID-BUY",
                        )
                    )
            else:
                signals.append(Signal("WARN", "A500超过最大普通补仓格数", "普通网格不应继续机械补仓。"))

        for level, item in sorted(open_grids.items()):
            target_price = item["price"] * (1 + grid_spacing)
            if latest.close >= target_price:
                signals.append(
                    Signal(
                        "ACTION",
                        f"A500第{level}格网格卖出",
                        f"该格买入价 {item['price']:.4f}，目标价 {target_price:.4f}，提示卖出剩余 {item['shares']:.0f}份。",
                        symbol=config.a500_code,
                        action="sell",
                        rule_id="A500-GRID-SELL",
                    )
                )

        base_state = _a500_base_state(config, portfolio)
        if base_state["shares"] > 0 and base_state["cost"] > 0:
            base_cost = base_state["cost"] / base_state["shares"]
            profit = (latest.close - base_cost) / base_cost
            metrics["A500持仓浮盈亏"] = f"{profit:.2%}"
            if not base_state["first_take_profit_done"] and profit >= 0.15:
                signals.append(
                    Signal(
                        "ACTION",
                        "A500底仓盈利达到15%",
                        "按策略卖出底仓的1/2，实际份额需人工确认。",
                        symbol=config.a500_code,
                        action="sell",
                        rule_id="A500-BASE-PROFIT-15",
                    )
                )
            if base_state["first_take_profit_done"] and base_state["first_take_profit_date"] is not None:
                stage_bars = [bar for bar in bars if bar.date >= base_state["first_take_profit_date"]]
                if stage_bars:
                    stage_high = max(bar.close for bar in stage_bars)
                    metrics["A500底仓阶段高点"] = f"{stage_high:.4f}"
                    if latest.close <= stage_high * 0.92:
                        signals.append(
                            Signal(
                                "ACTION",
                                "A500底仓回撤达到8%",
                                f"第一次底仓止盈后阶段高点 {stage_high:.4f}，当前回撤达到8%，提示卖出剩余底仓。",
                                symbol=config.a500_code,
                                action="sell",
                                rule_id="A500-BASE-TRAIL-8",
                            )
                        )

    if grid_parameters.is_actual and latest.close < grid_parameters.lower:
        signals.append(Signal("WARN", "A500跌破网格下沿", "暂停普通网格补仓；备用金只进入人工复核候选。"))
        _evaluate_a500_reserve(config, portfolio, bars, grid_parameters, signals, metrics)

    if grid_parameters.is_actual:
        _evaluate_a500_reserve_position(config, portfolio, latest.close, signals, metrics)

    if grid_parameters.is_actual and latest.close > grid_parameters.upper:
        recent = bars[-10:] if len(bars) >= 10 else bars
        if len(recent) == 10 and all(item.close > grid_parameters.upper for item in recent):
            signals.append(Signal("REVIEW", "A500连续10日高于网格上沿", "不追买，复核底仓趋势止盈或后续网格参数。"))


def _evaluate_kc50(
    config: TrackerConfig,
    portfolio: PortfolioState,
    bars: list[PriceBar],
    signals: list[Signal],
    warnings: list[str],
    metrics: dict[str, str],
) -> None:
    latest = bars[-1]
    position = portfolio.position(config.kc50_code)
    cash = portfolio.cash.get("kc50_wave", 0.0)
    net_value = position.shares * latest.close + cash
    metrics["科创50收盘价"] = f"{latest.close:.4f}"
    metrics["科创50持仓份额"] = f"{position.shares:.0f}"
    metrics["科创50波段账户净值"] = f"{net_value:.2f}"
    bought_amount = _symbol_buy_amount(portfolio, config.kc50_code)
    buy_count = _kc50_buy_count(config, portfolio)
    first_buy_date = _first_buy_date(portfolio, config.kc50_code)
    h3_recovered = _kc50_h3_recovered(config, portfolio, bars)
    if h3_recovered is not None:
        metrics["科创50 H3恢复状态"] = "已恢复" if h3_recovered else "未恢复"

    dd = drawdown_from_high(bars, 252)
    if dd is not None:
        metrics["科创50近12个月高点回撤"] = f"{dd:.2%}"
        steps = config.symbols["kc50"]["buy_steps"]
        first_step = steps[0]
        first_step_drawdown = float(first_step["drawdown"])
        first_step_amount = float(first_step["amount"])
        if bought_amount < first_step_amount and not _reaches_threshold(dd, first_step_drawdown):
            recent = bars[-252:] if len(bars) >= 252 else bars
            high_close = max(item.close for item in recent)
            trigger_close = high_close * (1 - first_step_drawdown)
            signals.append(
                Signal(
                    "INFO",
                    "科创50尚未触发第一笔买入",
                    f"尚未达到第一笔买入触发点，估算第一笔触发收盘价约 {trigger_close:.4f}。",
                )
            )
        cumulative = 0.0
        for index, step in enumerate(steps, start=1):
            cumulative += float(step["amount"])
            if _reaches_threshold(dd, float(step["drawdown"])) and bought_amount < cumulative:
                level = "REVIEW" if index in {1, 4} else "ACTION"
                detail = f"目标金额 {step['amount']} 元；需确认估值分位、已买档位和风险闸门。"
                if index == 1:
                    valuation_pass, valuation_detail = _valuation_reference_pass(config, "kc50")
                    if not valuation_pass and dd < 0.25:
                        signals.append(
                            Signal(
                                "REVIEW",
                                "科创50第一笔买入估值偏高延后",
                                f"22%回撤已触发，但科创50估值参考未通过：{valuation_detail}；等待估值回落至50%及以下或回撤达到25%。",
                                symbol=config.kc50_code,
                                action="buy",
                                planned_amount=float(step["amount"]),
                                is_new_buy=False,
                                rule_id="KC50-VALUATION-CROSS",
                            )
                        )
                        break
                    detail += " 估值交叉条件已满足。"
                if index == 4:
                    fourth_filter = _kc50_fourth_buy_filter(config, portfolio, bars)
                    metrics.update(fourth_filter["metrics"])
                    if not fourth_filter["allowed"]:
                        signals.append(
                            Signal(
                                "REVIEW",
                                "科创50第4笔买入等待过滤条件",
                                fourth_filter["detail"],
                                symbol=config.kc50_code,
                                action="buy",
                                planned_amount=float(step["amount"]),
                                is_new_buy=False,
                                rule_id="KC50-FOURTH-BUY",
                            )
                        )
                        break
                    detail += f" 第4笔过滤通过：{fourth_filter['detail']}"
                signals.append(
                    Signal(
                        level,
                        f"科创50可能触发第{index}笔买入",
                        detail,
                        symbol=config.kc50_code,
                        action="buy",
                        planned_amount=float(step["amount"]),
                        is_new_buy=True,
                        rule_id=f"KC50-BUY-{index}",
                    )
                )
                break

    new_low = is_20_day_new_low(bars)
    if new_low is not None:
        metrics["科创50是否20日新低"] = "是" if new_low else "否"

    if len(bars) >= 2 and bars[-1].pct_change <= -8:
        signals.append(Signal("WARN", "科创50单日收盘跌幅超过8%", "次日暂停科创50全部新增买入。"))
        _block_new_buy_candidates(signals, "科创50单日跌幅超过8%，次日暂停新增买入。")

    if net_value < 1400 and position.shares > 0:
        signals.append(Signal("ACTION", "科创50波段净值低于1400", "按H3清仓全部剩余科创50仓位。"))
        _block_new_buy_candidates(signals, "科创50波段净值低于1400。")
    elif net_value < 1500 and position.shares > 0:
        signals.append(Signal("ACTION", "科创50波段净值低于1500", "按H3卖出当前科创50仓位的1/2，并暂停新增买入。"))
        _block_new_buy_candidates(signals, "科创50波段净值低于1500。")
    elif net_value < 1700:
        signals.append(Signal("WARN", "科创50波段净值低于1700", "停止新增买入，进入观察。"))
        _block_new_buy_candidates(signals, "科创50波段净值低于1700。")
    elif h3_recovered is False:
        _block_new_buy_candidates(signals, "H3减仓后尚未连续5个交易日恢复至1800以上。")

    if position.shares > 0 and position.avg_cost > 0:
        profit = (latest.close - position.avg_cost) / position.avg_cost
        metrics["科创50相对持仓成本"] = f"{profit:.2%}"
        profit_stage = _kc50_profit_stage(portfolio, config.kc50_code)
        if profit_stage < 1 and profit >= 0.15:
            signals.append(Signal("ACTION", "科创50盈利达到15%", "次日卖出1/3仓位。", symbol=config.kc50_code, action="sell", rule_id="KC50-PROFIT-TAKE"))
        elif profit_stage < 2 and profit >= 0.30:
            signals.append(Signal("ACTION", "科创50盈利达到30%", "次日再卖出1/3仓位。", symbol=config.kc50_code, action="sell", rule_id="KC50-PROFIT-TAKE"))
        elif profit_stage >= 2:
            threshold = 0.13 if (annualized_volatility(bars, 20) or 0.0) > 0.50 else 0.12
            metrics["科创50移动止盈回撤阈值"] = f"{threshold:.2%}"
            trailing_high = _kc50_trailing_high(portfolio, config.kc50_code, bars)
            if trailing_high is not None:
                metrics["科创50移动止盈阶段高点"] = f"{trailing_high:.4f}"
                if latest.close <= trailing_high * (1 - threshold):
                    signals.append(
                        Signal(
                            "ACTION",
                            "科创50移动止盈触发",
                            f"第二次止盈后阶段高点 {trailing_high:.4f}，当前回撤达到 {threshold:.0%}。",
                            symbol=config.kc50_code,
                            action="sell",
                            rule_id="KC50-TRAILING-PROFIT",
                        )
                    )
            if _kc50_below_ma10_two_days(bars):
                signals.append(Signal("ACTION", "科创50跌破10日均线移动止盈", "连续2日收盘低于10日均线，提示卖出剩余仓位。", symbol=config.kc50_code, action="sell", rule_id="KC50-TRAILING-PROFIT"))

    below_20_for_10 = consecutive_closes_below_ma(bars, 20, 10)
    if below_20_for_10 and buy_count >= 2:
        signals.append(Signal("WARN", "科创50连续10日低于20日均线", "若已买入2笔，暂停新增买入。"))
        _block_new_buy_candidates(signals, "科创50已买入2笔且连续10日低于20日均线。")

    graded = _kc50_graded_risk(config, portfolio, bars, position)
    if graded is not None:
        signals.append(graded)

    if first_buy_date is not None and position.shares > 0 and position.avg_cost > 0:
        holding_days = _trading_days_since(bars, first_buy_date)
        metrics["科创50持仓交易日"] = str(holding_days)
        profit = (latest.close - position.avg_cost) / position.avg_cost
        ma20 = moving_average(bars, 20)
        if holding_days > 120 and profit < 0.05:
            signals.append(Signal("REVIEW", "科创50持仓超过120个交易日专项复核", "持仓超过120个交易日且盈利不足5%，需要专项复核。", rule_id="KC50-TIME-REVIEW"))
        elif holding_days > 60 and 0.05 <= profit <= 0.15 and ma20 is not None and latest.close < ma20:
            signals.append(Signal("REVIEW", "科创50持仓超过60个交易日复核", "持仓超过60个交易日、盈利5%-15%且跌破20日均线，可复核卖出一半或全部并记录理由。", rule_id="KC50-TIME-REVIEW"))

    if moving_average(bars, 5) is None or moving_average(bars, 20) is None:
        warnings.append("科创50历史数据不足，部分均线规则无法判断。")


def _evaluate_global(
    config: TrackerConfig,
    portfolio: PortfolioState,
    a500: list[PriceBar],
    kc50: list[PriceBar],
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    latest_prices = {config.a500_code: a500[-1].close, config.kc50_code: kc50[-1].close}
    total_cost = sum(position.cost for position in portfolio.positions.values())
    total_value = sum(position.shares * latest_prices.get(symbol, 0) for symbol, position in portfolio.positions.items())
    floating_loss_ratio = 0.0 if total_cost <= 0 else max(0.0, total_cost - total_value) / total_cost
    invested_ratio = total_cost / config.total_capital if config.total_capital > 0 else 0
    reserve_initial = config.funds.get("reserve", 0.0)
    reserve_cash = portfolio.cash.get("reserve", 0.0)
    reserve_deployed = max(0.0, reserve_initial - reserve_cash)
    metrics["总投入成本"] = f"{total_cost:.2f}"
    metrics["当前持仓市值"] = f"{total_value:.2f}"
    metrics["总投入成本比例"] = f"{invested_ratio:.2%}"
    metrics["总持仓浮亏比例"] = f"{floating_loss_ratio:.2%}"
    metrics["备用金未回收动用"] = f"{reserve_deployed:.2f}"
    metrics["备用金安全垫"] = "不足" if reserve_deployed > 2000 or reserve_cash < 1000 else "充足"

    if reserve_deployed > 2000 or reserve_cash < 1000:
        signals.append(
            Signal(
                "WARN",
                "备用金安全垫不足",
                "未回收备用金超过2000元或剩余备用金低于1000元，禁止新增备用金候选并要求复核。",
            )
        )

    if invested_ratio >= 0.85:
        signals.append(Signal("WARN", "总投入成本达到85%", "禁止新增买入，只允许止盈、减仓、复核和记录。"))
        _block_new_buy_candidates(signals, "总投入成本达到85%，禁止新增买入。")
    elif invested_ratio >= 0.70:
        gate = _risk_gate_result(config, portfolio, a500, kc50, floating_loss_ratio)
        metrics.update(gate["metrics"])
        if gate["allowed"]:
            signals.append(Signal("INFO", "总风险闸门通过", gate["detail"]))
        else:
            signals.append(Signal("WARN", "总风险闸门未通过", gate["detail"]))
            _block_new_buy_candidates(signals, "总风险闸门未通过。")

    corr = correlation(a500, kc50, 20)
    if corr is not None:
        metrics["20日收益相关系数"] = f"{corr:.3f}"
        if corr > 0.85:
            signals.append(Signal("REVIEW", "双标的20日相关系数大于0.85", "同日新增买入合计计划金额不超过800元，优先A500。"))
            _apply_s1_correlation_limit(signals)

    vol = annualized_volatility(kc50, 20)
    if vol is not None:
        metrics["科创5020日年化波动率"] = f"{vol:.2%}"
        if vol > 0.50:
            signals.append(Signal("INFO", "科创50处于高波动阶段", "剩余1/3仓位移动止盈回撤参数从12%放宽至13%。"))

    a500_weak = _is_weak(a500)
    kc50_weak = _is_weak(kc50)
    if a500_weak is not None and kc50_weak is not None:
        metrics["双标的趋势"] = "同时弱势" if a500_weak and kc50_weak else "非同时弱势"


def _evaluate_lifecycle_and_long_term(
    config: TrackerConfig,
    portfolio: PortfolioState,
    histories: dict[str, list[PriceBar]],
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    a500 = histories.get(config.a500_code, [])
    kc50 = histories.get(config.kc50_code, [])
    latest_prices = {
        symbol: bars[-1].close
        for symbol, bars in ((config.a500_code, a500), (config.kc50_code, kc50))
        if bars
    }
    latest_date = _latest_common_date(a500, kc50)

    if a500:
        _evaluate_restart_after_liquidation(config, portfolio, config.a500_code, "A500", a500, signals, metrics)
        _evaluate_h7_loss_buys(config, portfolio, config.a500_code, "A500", a500, signals, metrics)
        _evaluate_a500_grid_idle(config, portfolio, a500, signals, metrics)
    if kc50:
        _evaluate_restart_after_liquidation(config, portfolio, config.kc50_code, "科创50", kc50, signals, metrics)
        _evaluate_h7_loss_buys(config, portfolio, config.kc50_code, "科创50", kc50, signals, metrics)
        _evaluate_kc50_h3_round_pause(config, portfolio, kc50, signals, metrics)
        _evaluate_kc50_reserve_recovery(config, portfolio, kc50, signals, metrics)

    current_account_value = _account_current_value(config, portfolio, latest_prices)
    locked_cash = _account_float(config, "locked_cash", 0.0)
    peak_value = _account_float(config, "peak_value", config.total_capital)
    metrics["不可交易现金池"] = f"{locked_cash:.2f}"
    metrics["总账户当前价值"] = f"{current_account_value:.2f}"
    if peak_value > 0:
        drawdown = max(0.0, peak_value - current_account_value) / peak_value
        metrics["总账户最大回撤"] = f"{drawdown:.2%}"
        if drawdown > 0.12:
            signals.append(
                Signal(
                    "WARN",
                    "总账户最大回撤超过12%",
                    f"当前账户价值 {current_account_value:.2f}，历史峰值 {peak_value:.2f}，最大回撤 {drawdown:.2%}；暂停所有新增买入，只保留止盈、减仓和复核。",
                    rule_id="STRATEGY-INVALIDATION",
                )
            )
            _block_new_buy_candidates(signals, "总账户最大回撤超过12%。")

    _evaluate_profit_lock(config, current_account_value, locked_cash, signals, metrics)
    if latest_date is not None:
        _evaluate_strategy_benchmark_review(config, current_account_value, latest_date, signals, metrics)


def _evaluate_restart_after_liquidation(
    config: TrackerConfig,
    portfolio: PortfolioState,
    symbol: str,
    label: str,
    bars: list[PriceBar],
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    liquidation_dates = _symbol_liquidation_dates(portfolio, symbol)
    if not liquidation_dates:
        return
    last_liquidation = max(liquidation_dates)
    normal_first_amount = _normal_first_buy_amount(config, symbol)
    probe_limit = normal_first_amount * 0.5

    oversized_probe = [
        trade
        for trade in portfolio.trades
        if trade.symbol == symbol
        and trade.is_buy
        and (trade.execution_date or trade.date) > last_liquidation
        and "重启" in _trade_text(trade)
        and trade.amount > probe_limit + 1e-9
    ]
    if oversized_probe:
        signals.append(
            Signal(
                "WARN",
                f"{label}重启试探仓超过50%上限",
                f"清仓后重启首笔试探仓上限为 {probe_limit:.2f} 元；已记录试探仓超过50%，需标记为违规/复核事实成交。",
                symbol=symbol,
                action="buy",
                rule_id="RESTART-AFTER-LIQUIDATION",
            )
        )

    if portfolio.position(symbol).shares > 0:
        return

    days = _trading_days_between(bars, last_liquidation, bars[-1].date)
    metrics[f"{label}重启冷却交易日"] = f"{days}/5"
    _add_restart_relock_metrics(config, portfolio, symbol, label, bars, metrics)

    if days < 5:
        signals.append(
            Signal(
                "WARN",
                f"{label}清仓后重启冷却中",
                f"清仓后仅经过 {days} 个交易日，未满足至少5个交易日冷却；暂停该标的新增买入。",
                symbol=symbol,
                action="buy",
                rule_id="H6-RESTART",
            )
        )
        _block_new_buy_candidates(signals, "H6清仓后冷却期不足5个交易日。", symbol=symbol)
        return

    _block_new_buy_candidates(signals, "清仓后重启阶段仅允许试探仓复核候选。", symbol=symbol)
    signals.append(
        Signal(
            "REVIEW",
            f"{label}清仓后重启试探仓",
            f"已满足清仓后5个交易日冷却；重启前需重锁基准价、阶段高点、网格上下沿和资金池余额，首笔试探仓不超过 {probe_limit:.2f} 元。",
            symbol=symbol,
            action="buy",
            planned_amount=probe_limit,
            is_new_buy=True,
            rule_id="RESTART-AFTER-LIQUIDATION",
        )
    )


def _add_restart_relock_metrics(
    config: TrackerConfig,
    portfolio: PortfolioState,
    symbol: str,
    label: str,
    bars: list[PriceBar],
    metrics: dict[str, str],
) -> None:
    latest = bars[-1]
    metrics[f"{label}重启基准价"] = f"{latest.close:.4f}"
    metrics[f"{label}重启阶段高点"] = f"{max(bar.close for bar in bars):.4f}"
    if symbol == config.a500_code:
        grid_spacing = float(config.symbols["a500"].get("grid_spacing", A500_BASE_GRID_SPACING))
        max_grid_buys = int(config.symbols["a500"].get("max_grid_buys", 5))
        parameters = _a500_suggested_grid_parameters(bars)
        metrics[f"{label}重启网格下沿"] = f"{parameters.base_price * (1 - grid_spacing * max_grid_buys):.4f}"
        metrics[f"{label}重启网格上沿"] = f"{parameters.base_price * 1.15:.4f}"
        metrics[f"{label}重启资金池余额"] = f"{portfolio.cash.get('a500_grid', 0.0):.2f}"
    elif symbol == config.kc50_code:
        metrics[f"{label}重启资金池余额"] = f"{portfolio.cash.get('kc50_wave', 0.0):.2f}"


def _evaluate_h7_loss_buys(
    config: TrackerConfig,
    portfolio: PortfolioState,
    symbol: str,
    label: str,
    bars: list[PriceBar],
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    reviewed: list[Trade] = []
    current_close = bars[-1].close
    for trade in sorted((item for item in portfolio.trades if item.symbol == symbol and item.is_buy), key=lambda item: item.execution_date or item.date):
        review_bar = _bar_after_trading_days(bars, trade.execution_date or trade.date, 10)
        if review_bar is None:
            continue
        if review_bar.close < trade.price and current_close < trade.price:
            reviewed.append(trade)
        else:
            reviewed = []

    if len(reviewed) < 3:
        return

    metrics[f"{label}连续10日复核仍浮亏买入"] = f"{len(reviewed[-3:])}笔"
    signals.append(
        Signal(
            "WARN",
            f"{label}连续3笔买入10日复核仍浮亏",
            "同一标的连续3笔新增买入在第10个交易日复核仍浮亏，且当前仍浮亏；暂停新增买入并提示专项复核。",
            symbol=symbol,
            action="buy",
            rule_id="H7-THREE-LOSS-BUYS",
        )
    )
    _block_new_buy_candidates(signals, "H7连续3笔买入10日复核仍浮亏。", symbol=symbol)


def _evaluate_kc50_h3_round_pause(
    config: TrackerConfig,
    portfolio: PortfolioState,
    bars: list[PriceBar],
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    h3_dates = _kc50_h3_liquidation_dates(config, portfolio)
    metrics["科创50 H3应急轮次"] = str(len(h3_dates))
    if len(h3_dates) < 2:
        return
    days = _trading_days_between(bars, max(h3_dates), bars[-1].date)
    metrics["科创50 H3二次后暂停交易日"] = f"{days}/30"
    if days < 30:
        signals.append(
            Signal(
                "WARN",
                "科创50连续2轮H3应急风控",
                f"最近一次H3清仓后仅经过 {days} 个交易日；暂停科创50新增买入至少30个交易日。",
                symbol=config.kc50_code,
                action="buy",
                rule_id="KC50-H3-ROUNDS",
            )
        )
        _block_new_buy_candidates(signals, "科创50连续2轮H3应急风控后30个交易日内暂停。", symbol=config.kc50_code)


def _evaluate_a500_grid_idle(
    config: TrackerConfig,
    portfolio: PortfolioState,
    bars: list[PriceBar],
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    grid_trades = [
        trade
        for trade in portfolio.trades
        if trade.symbol == config.a500_code and "网格" in _trade_text(trade)
    ]
    if not grid_trades:
        return
    last_grid_date = max(trade.execution_date or trade.date for trade in grid_trades)
    days = _trading_days_between(bars, last_grid_date, bars[-1].date)
    metrics["A500网格无有效买卖交易日"] = str(days)
    if days >= 90:
        signals.append(
            Signal(
                "REVIEW",
                "A500网格90日无有效买卖",
                "A500普通网格连续90个交易日无有效买卖，需季度复核网格间距或暂停策略。",
                symbol=config.a500_code,
                rule_id="A500-GRID-IDLE-90",
            )
        )


def _evaluate_strategy_benchmark_review(
    config: TrackerConfig,
    current_account_value: float,
    latest_date: date,
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    raw_start = config.raw.get("account", {}).get("strategy_start_date")
    if not raw_start:
        return
    try:
        start = date.fromisoformat(str(raw_start))
    except ValueError:
        return
    if (latest_date - start).days < 183:
        return

    account_return = (current_account_value - config.total_capital) / config.total_capital if config.total_capital > 0 else 0.0
    if abs(account_return) < 0.00005:
        account_return = 0.0
    benchmarks = config.raw.get("benchmarks", {})
    hs300 = _float_or_none(benchmarks.get("hs300_return"))
    money = _float_or_none(benchmarks.get("money_fund_return"))
    if hs300 is None or money is None:
        detail = f"策略已运行满6个月，本账户收益 {account_return:.2%}；缺少沪深300或货币基金收益配置，需人工复核。"
        metrics["策略运行收益对比"] = f"本账户 {account_return:.2%}；基准缺失"
    else:
        detail = f"策略已运行满6个月，本账户收益 {account_return:.2%}，沪深300 {hs300:.2%}，货币基金 {money:.2%}；是否明显落后保留人工复核。"
        metrics["策略运行收益对比"] = f"本账户 {account_return:.2%}；沪深300 {hs300:.2%}；货币基金 {money:.2%}"
    signals.append(Signal("REVIEW", "策略运行满6个月收益对比复核", detail, rule_id="STRATEGY-INVALIDATION"))


def _evaluate_profit_lock(
    config: TrackerConfig,
    current_account_value: float,
    locked_cash: float,
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    profit = current_account_value - config.total_capital
    if config.total_capital <= 0 or profit < config.total_capital * 0.15:
        return
    suggested_lock = max(0.0, profit / 3)
    metrics["收益锁定建议金额"] = f"{suggested_lock:.2f}"
    signals.append(
        Signal(
            "REVIEW",
            "收益锁定触发",
            f"总账户累计收益已达到15%；建议至少提取收益的1/3，即 {suggested_lock:.2f} 元，进入不可交易现金池。当前不可交易现金池 {locked_cash:.2f} 元。",
            rule_id="PROFIT-LOCK",
        )
    )


def _evaluate_kc50_reserve_recovery(
    config: TrackerConfig,
    portfolio: PortfolioState,
    bars: list[PriceBar],
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    h3_dates = _kc50_h3_liquidation_dates(config, portfolio)
    if not h3_dates:
        return
    last_h3 = max(h3_dates)
    recovery = config.raw.get("reserve", {}).get("kc50_recovery", {})
    confirmed = bool(recovery.get("confirmed"))
    source = str(recovery.get("source") or "").strip()
    as_of = str(recovery.get("as_of") or "").strip()
    note = str(recovery.get("note") or "").strip()
    max_amount = float(recovery.get("max_amount", 1000) or 1000)
    max_amount = min(1000.0, max_amount)
    h3_recovered = _kc50_h3_recovered(config, portfolio, bars)
    reserve_count = sum(
        1
        for trade in portfolio.trades
        if trade.symbol == config.kc50_code
        and trade.is_buy
        and (trade.execution_date or trade.date) > last_h3
        and "备用" in _trade_text(trade)
    )
    reserve_cash = portfolio.cash.get("reserve", 0.0)
    reserve_initial = config.funds.get("reserve", 0.0)
    reserve_deployed = max(0.0, reserve_initial - reserve_cash)
    safety_ok = reserve_deployed <= 2000 and reserve_cash >= 1000

    metrics["科创50备用金回暖人工确认"] = "通过" if confirmed and source and as_of else "不通过"
    metrics["科创50备用金回暖H3恢复"] = _pass_label(h3_recovered is True)
    metrics["科创50备用金回暖已用份数"] = str(reserve_count)

    if not confirmed or not source or not as_of:
        signals.append(
            Signal(
                "REVIEW",
                "科创50备用金回暖等待人工确认",
                "H3清仓后备用金回暖软信号缺少人工确认、来源或日期；等待人工确认后再复核。",
                symbol=config.kc50_code,
                action="buy",
                rule_id="KC50-RESERVE-RECOVERY",
            )
        )
        return
    if h3_recovered is not True:
        signals.append(Signal("REVIEW", "科创50备用金回暖等待H3恢复", "H3后尚未连续5个交易日恢复至1800以上。", symbol=config.kc50_code, action="buy", rule_id="KC50-RESERVE-RECOVERY"))
        return
    if not safety_ok:
        signals.append(Signal("WARN", "科创50备用金回暖安全垫不足", "备用金安全垫不足，禁止新增科创50备用金回暖候选。", symbol=config.kc50_code, action="buy", rule_id="KC50-RESERVE-RECOVERY"))
        return
    if reserve_count >= 1:
        signals.append(
            Signal(
                "WARN",
                "科创50备用金回暖已使用1份",
                "H3清仓后的科创50备用金回暖已使用1份；禁止继续新增备用金候选，真实成交仅作为突破边界事实记录。",
                symbol=config.kc50_code,
                action="buy",
                rule_id="KC50-RESERVE-RECOVERY",
            )
        )
        return

    detail = f"H3恢复、备用金安全垫和人工回暖确认均满足；最多 {max_amount:.2f} 元，仅作为人工复核候选。"
    if note:
        detail += f" 人工备注：{note}"
    signals.append(
        Signal(
            "REVIEW",
            "科创50备用金回暖候选",
            detail,
            symbol=config.kc50_code,
            action="buy",
            planned_amount=max_amount,
            is_new_buy=True,
            rule_id="KC50-RESERVE-RECOVERY-BUY",
        )
    )


def _risk_gate_result(
    config: TrackerConfig,
    portfolio: PortfolioState,
    a500: list[PriceBar],
    kc50: list[PriceBar],
    floating_loss_ratio: float,
) -> dict[str, object]:
    a500_weak = _is_weak(a500)
    kc50_weak = _is_weak(kc50)
    loss_pass = floating_loss_ratio < 0.10
    trend_pass = a500_weak is not None and kc50_weak is not None and not (a500_weak and kc50_weak)
    a500_valuation = _valuation_reference_pass(config, "a500")
    kc50_valuation = _valuation_reference_pass(config, "kc50")
    cooldown = _cooldown_reference_pass(portfolio, _latest_common_date(a500, kc50), [a500, kc50])
    reference_passes = sum(1 for item in (a500_valuation, kc50_valuation, cooldown) if item[0])
    allowed = loss_pass and trend_pass and reference_passes >= 2
    metrics = {
        "H1硬条件-浮亏小于10%": _pass_label(loss_pass),
        "H1硬条件-至少一个标的非弱势": _pass_label(trend_pass),
        "H1参考-A500估值": a500_valuation[1],
        "H1参考-科创50估值": kc50_valuation[1],
        "H1参考-止损止盈间隔": cooldown[1],
        "H1参考条件通过数": f"{reference_passes}/3",
        "H1总风险闸门结论": "允许新增买入" if allowed else "不允许新增买入",
    }
    detail = (
        f"硬条件：浮亏小于10%={_pass_label(loss_pass)}，至少一个标的非弱势={_pass_label(trend_pass)}；"
        f"参考条件通过 {reference_passes}/3；结论：{'允许新增买入' if allowed else '不允许新增买入'}。"
    )
    return {"allowed": allowed, "metrics": metrics, "detail": detail}


def _valuation_reference_pass(config: TrackerConfig, symbol_key: str) -> tuple[bool, str]:
    valuation = config.raw.get("valuation", {})
    raw_value = valuation.get(f"{symbol_key}_percentile")
    source = str(valuation.get("source") or valuation.get(f"{symbol_key}_source") or "").strip()
    as_of = str(valuation.get("as_of") or valuation.get(f"{symbol_key}_as_of") or "").strip()
    try:
        percentile = float(raw_value)
    except (TypeError, ValueError):
        return False, "不通过（估值缺失）"
    if percentile > 1:
        percentile = percentile / 100
    if not source or source == "待填写" or not as_of:
        return False, f"不通过（{percentile:.2%}，来源或日期未锁定）"
    return (percentile <= 0.50, f"{_pass_label(percentile <= 0.50)}（{percentile:.2%}）")


def _cooldown_reference_pass(
    portfolio: PortfolioState,
    latest_date: date | None,
    histories: list[list[PriceBar]],
) -> tuple[bool, str]:
    action_trades = [
        trade
        for trade in portfolio.trades
        if any(word in trade.module or word in trade.trigger_rule for word in ("止损", "止盈"))
    ]
    if not action_trades:
        return True, "通过（无止损/止盈记录）"
    if latest_date is None:
        return False, "不通过（缺少行情日期）"

    last_action_date = max((trade.execution_date or trade.date) for trade in action_trades)
    trading_dates = sorted({bar.date for history in histories for bar in history})
    days = sum(1 for item in trading_dates if last_action_date < item <= latest_date)
    return (days >= 10, f"{_pass_label(days >= 10)}（{days}个交易日）")


def _account_float(config: TrackerConfig, key: str, default: float) -> float:
    try:
        return float(config.raw.get("account", {}).get(key, default))
    except (TypeError, ValueError):
        return default


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _account_current_value(
    config: TrackerConfig,
    portfolio: PortfolioState,
    latest_prices: dict[str, float],
) -> float:
    position_value = sum(position.shares * latest_prices.get(symbol, 0.0) for symbol, position in portfolio.positions.items())
    locked_cash = _account_float(config, "locked_cash", 0.0)
    return position_value + sum(portfolio.cash.values()) + locked_cash


def _normal_first_buy_amount(config: TrackerConfig, symbol: str) -> float:
    if symbol == config.a500_code:
        return float(config.symbols.get("a500", {}).get("base_position_amount", 1000))
    if symbol == config.kc50_code:
        steps = config.symbols.get("kc50", {}).get("buy_steps", [])
        if steps:
            return float(steps[0].get("amount", 0.0))
    return 0.0


def _symbol_liquidation_dates(portfolio: PortfolioState, symbol: str) -> list[date]:
    return [
        trade.execution_date or trade.date
        for trade in portfolio.trades
        if trade.symbol == symbol and trade.is_sell and "清仓" in _trade_text(trade)
    ]


def _kc50_h3_liquidation_dates(config: TrackerConfig, portfolio: PortfolioState) -> list[date]:
    return [
        trade.execution_date or trade.date
        for trade in portfolio.trades
        if trade.symbol == config.kc50_code
        and trade.is_sell
        and "H3" in _trade_text(trade)
        and ("清仓" in _trade_text(trade) or "1400" in _trade_text(trade))
    ]


def _bar_after_trading_days(bars: list[PriceBar], start: date, days: int) -> PriceBar | None:
    future = [bar for bar in bars if bar.date > start]
    if len(future) < days:
        return None
    return future[days - 1]


def _block_new_buy_candidates(signals: list[Signal], reason: str, symbol: str | None = None) -> None:
    for index, signal in enumerate(signals):
        if symbol is not None and signal.symbol != symbol:
            continue
        if _is_new_buy_candidate(signal):
            signals[index] = Signal(
                "WARN",
                signal.title,
                f"禁止新增买入：{reason}原提示：{signal.detail}",
                symbol=signal.symbol,
                action=signal.action,
                planned_amount=signal.planned_amount,
                is_new_buy=signal.is_new_buy,
                rule_id=signal.rule_id,
            )


def _is_new_buy_candidate(signal: Signal) -> bool:
    if signal.level not in {"ACTION", "REVIEW"}:
        return False
    if signal.is_new_buy:
        return True
    return any(keyword in signal.title for keyword in ("买入", "补仓"))


def _pass_label(value: bool) -> str:
    return "通过" if value else "不通过"


def _audit_trade_records(portfolio: PortfolioState, warnings: list[str]) -> None:
    incomplete = [
        trade
        for trade in portfolio.trades
        if not trade.trigger_rule or trade.cash_balance is None or (trade.risk_gate_triggered and not trade.risk_gate_snapshot)
    ]
    if incomplete:
        warnings.append(
            f"{len(incomplete)} 条成交记录缺少触发规则、现金余额或风险闸门快照；旧记录仍可读取，但复盘信息不完整。"
        )


def _a500_open_grid_positions(
    config: TrackerConfig,
    portfolio: PortfolioState,
    base_price: float,
    grid_spacing: float,
) -> dict[int, dict[str, float]]:
    open_items: dict[int, dict[str, float]] = {}
    for trade in portfolio.trades:
        if trade.symbol != config.a500_code:
            continue
        text = _trade_text(trade)
        if "网格" not in text or "底仓" in text or "备用" in text:
            continue
        level = _a500_grid_level(trade, base_price, grid_spacing)
        if level <= 0:
            continue
        if trade.is_buy:
            item = open_items.setdefault(level, {"shares": 0.0, "price": trade.price})
            total_shares = item["shares"] + trade.shares
            if total_shares > 0:
                item["price"] = (item["price"] * item["shares"] + trade.price * trade.shares) / total_shares
            item["shares"] = total_shares
        elif trade.is_sell:
            item = open_items.get(level)
            if item is not None:
                item["shares"] = max(0.0, item["shares"] - trade.shares)
    return {level: item for level, item in open_items.items() if item["shares"] > 1e-9}


def _a500_grid_level(trade: object, base_price: float, grid_spacing: float) -> int:
    text = _trade_text(trade)
    match = re.search(r"第\s*([0-9一二三四五六七八九十]+)\s*格", text)
    if match:
        return _zh_int(match.group(1))
    price = getattr(trade, "price", 0.0)
    if base_price <= 0 or grid_spacing <= 0 or price <= 0:
        return 0
    drawdown = (base_price - price) / base_price
    return max(0, int(drawdown / grid_spacing + 1e-12))


def _format_a500_open_grids(open_grids: dict[int, dict[str, float]]) -> str:
    if not open_grids:
        return "无"
    return "；".join(f"第{level}格 {item['shares']:.0f}份" for level, item in sorted(open_grids.items()))


def _a500_downtrend_paused(
    open_grids: dict[int, dict[str, float]],
    bars: list[PriceBar],
    grid_parameters: A500GridParameters,
) -> bool:
    if len(open_grids) >= 3:
        return True
    recent = bars[-10:] if len(bars) >= 10 else bars
    return len(recent) == 10 and all(item.close < grid_parameters.lower for item in recent)


def _a500_base_state(config: TrackerConfig, portfolio: PortfolioState) -> dict[str, object]:
    shares = 0.0
    cost = 0.0
    first_take_profit_date: date | None = None
    first_take_profit_done = False
    for trade in portfolio.trades:
        if trade.symbol != config.a500_code:
            continue
        text = _trade_text(trade)
        if "底仓" not in text:
            continue
        if trade.is_buy:
            shares += trade.shares
            cost += trade.amount + trade.fee
        elif trade.is_sell:
            if shares > 0:
                sold_ratio = min(1.0, trade.shares / shares)
                cost *= 1.0 - sold_ratio
                shares = max(0.0, shares - trade.shares)
            if "止盈" in text or "15%" in text:
                first_take_profit_done = True
                first_take_profit_date = trade.execution_date or trade.date
    return {
        "shares": shares,
        "cost": cost,
        "first_take_profit_done": first_take_profit_done,
        "first_take_profit_date": first_take_profit_date,
    }


def _evaluate_a500_reserve(
    config: TrackerConfig,
    portfolio: PortfolioState,
    bars: list[PriceBar],
    grid_parameters: A500GridParameters,
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    latest = bars[-1]
    drop_below_lower = (grid_parameters.lower - latest.close) / grid_parameters.lower if grid_parameters.lower > 0 else 0.0
    valuation_pass_20, valuation_detail_20 = _valuation_percentile_pass(config, "a500_percentile", 0.20)
    valuation_pass_10, valuation_detail_10 = _valuation_percentile_pass(config, "a500_percentile", 0.10)
    interval_pass = _a500_reserve_interval_pass(portfolio, bars)
    metrics["A500备用金A组"] = _pass_label(drop_below_lower >= 0.07 and valuation_pass_20 and interval_pass)
    metrics["A500备用金B组"] = _pass_label(valuation_pass_10 and interval_pass)
    metrics["A500备用金C组"] = _pass_label(drop_below_lower >= 0.10 and interval_pass)
    metrics["A500备用金下沿后跌幅"] = f"{max(0.0, drop_below_lower):.2%}"
    metrics["A500备用金估值20%"] = valuation_detail_20
    metrics["A500备用金估值10%"] = valuation_detail_10

    if drop_below_lower >= 0.07 and valuation_pass_20 and interval_pass:
        signals.append(
            Signal(
                "REVIEW",
                "A500备用金A组候选",
                "跌破网格下沿后继续下跌超过7%，且估值低于历史20%分位；备用金每次最多1000元，必须人工复核。",
                symbol=config.a500_code,
                action="buy",
                planned_amount=1000,
                is_new_buy=True,
                rule_id="A500-RESERVE-LIMITS",
            )
        )
    if valuation_pass_10 and interval_pass:
        signals.append(Signal("REVIEW", "A500备用金B组候选", "估值低于历史10%分位；备用金动作只作为人工复核候选。", symbol=config.a500_code, action="buy", planned_amount=1000, is_new_buy=True, rule_id="A500-RESERVE-LIMITS"))
    if drop_below_lower >= 0.10 and interval_pass:
        signals.append(Signal("REVIEW", "A500备用金C组候选", "跌破网格下沿后继续下跌10%以上；备用金动作只作为人工复核候选。", symbol=config.a500_code, action="buy", planned_amount=1000, is_new_buy=True, rule_id="A500-RESERVE-LIMITS"))


def _a500_reserve_interval_pass(portfolio: PortfolioState, bars: list[PriceBar]) -> bool:
    reserve_buys = [trade for trade in portfolio.trades if trade.is_buy and "备用" in _trade_text(trade)]
    if not reserve_buys:
        return True
    latest_date = bars[-1].date
    last_date = max(trade.execution_date or trade.date for trade in reserve_buys)
    return _trading_days_between(bars, last_date, latest_date) >= 20


def _evaluate_a500_reserve_position(
    config: TrackerConfig,
    portfolio: PortfolioState,
    latest_close: float,
    signals: list[Signal],
    metrics: dict[str, str],
) -> None:
    shares = 0.0
    cost = 0.0
    for trade in portfolio.trades:
        if trade.symbol != config.a500_code or "备用" not in _trade_text(trade):
            continue
        if trade.is_buy:
            shares += trade.shares
            cost += trade.amount + trade.fee
        elif trade.is_sell and shares > 0:
            sold_ratio = min(1.0, trade.shares / shares)
            cost *= 1.0 - sold_ratio
            shares = max(0.0, shares - trade.shares)
    if shares <= 0 or cost <= 0:
        return
    avg_cost = cost / shares
    profit = (latest_close - avg_cost) / avg_cost
    metrics["A500备用金持仓份额"] = f"{shares:.0f}"
    metrics["A500备用金持仓浮盈亏"] = f"{profit:.2%}"
    if profit >= 0.10:
        signals.append(Signal("REVIEW", "A500备用金盈利达到10%", "备用金仓位盈利达到10%-15%，分2次卖出锁定收益，需人工复核。", symbol=config.a500_code, action="sell", rule_id="A500-RESERVE-LIMITS"))
    if profit <= -0.08:
        signals.append(Signal("WARN", "A500备用金亏损超过8%", "暂停下一笔备用金投入，并复核是否继续持有。", rule_id="A500-RESERVE-LIMITS"))
    if profit <= -0.12:
        signals.append(Signal("REVIEW", "A500备用金亏损超过12%", "季度复核时需在认亏退出、降级长持或继续持有之间做人工决策。", rule_id="A500-RESERVE-LIMITS"))


def _kc50_buy_count(config: TrackerConfig, portfolio: PortfolioState) -> int:
    return sum(1 for trade in portfolio.trades if trade.symbol == config.kc50_code and trade.is_buy)


def _first_buy_date(portfolio: PortfolioState, symbol: str) -> date | None:
    dates = [trade.execution_date or trade.date for trade in portfolio.trades if trade.symbol == symbol and trade.is_buy]
    return min(dates) if dates else None


def _kc50_fourth_buy_filter(
    config: TrackerConfig,
    portfolio: PortfolioState,
    bars: list[PriceBar],
) -> dict[str, object]:
    has_third = any(
        trade.symbol == config.kc50_code
        and trade.is_buy
        and ("第3笔" in _trade_text(trade) or "第三笔" in _trade_text(trade))
        for trade in portfolio.trades
    )
    recent_indexes = list(range(max(0, len(bars) - 5), len(bars)))
    no_new_low = len(recent_indexes) == 5 and all(not _is_new_low_at(bars, index, 20) for index in recent_indexes)
    stood_above_ma5 = any(_close_above_ma_at(bars, index, 5) for index in recent_indexes)
    pe_pass, pe_detail = _valuation_percentile_pass(config, "kc50_pe_percentile", 0.25)
    pb_pass, pb_detail = _valuation_percentile_pass(config, "kc50_pb_percentile", 0.25)
    valuation_pass = pe_pass or pb_pass
    allowed = has_third and no_new_low and stood_above_ma5 and valuation_pass
    metrics = {
        "科创50第4笔-已有第三笔": _pass_label(has_third),
        "科创50第4笔-5日不创新低": _pass_label(no_new_low),
        "科创50第4笔-站上5日均线": _pass_label(stood_above_ma5),
        "科创50第4笔-PE/PB估值": "通过" if valuation_pass else f"不通过（PE {pe_detail}；PB {pb_detail}）",
    }
    detail = (
        f"已有第三笔={_pass_label(has_third)}，连续5日不创新低={_pass_label(no_new_low)}，"
        f"至少一日站上5日均线={_pass_label(stood_above_ma5)}，PE/PB低位={_pass_label(valuation_pass)}。"
    )
    return {"allowed": allowed, "detail": detail, "metrics": metrics}


def _kc50_profit_stage(portfolio: PortfolioState, symbol: str) -> int:
    stage = 0
    for trade in portfolio.trades:
        if trade.symbol != symbol or not trade.is_sell:
            continue
        text = _trade_text(trade)
        if "第二次止盈" in text or "第二档" in text or "30%" in text:
            stage = max(stage, 2)
        elif "第一次止盈" in text or "第一档" in text or "15%" in text or "止盈" in text:
            stage = max(stage, 1)
    return stage


def _kc50_trailing_high(portfolio: PortfolioState, symbol: str, bars: list[PriceBar]) -> float | None:
    second_dates = [
        trade.execution_date or trade.date
        for trade in portfolio.trades
        if trade.symbol == symbol and trade.is_sell and ("第二次止盈" in _trade_text(trade) or "30%" in _trade_text(trade))
    ]
    if not second_dates:
        return None
    start = max(second_dates)
    stage_bars = [bar for bar in bars if bar.date >= start]
    return max((bar.close for bar in stage_bars), default=None)


def _kc50_below_ma10_two_days(bars: list[PriceBar]) -> bool:
    if len(bars) < 12:
        return False
    for offset in (2, 1):
        subset = bars[: len(bars) - offset + 1]
        ma10 = moving_average(subset, 10)
        if ma10 is None or subset[-1].close >= ma10:
            return False
    return True


def _kc50_h3_recovered(config: TrackerConfig, portfolio: PortfolioState, bars: list[PriceBar]) -> bool | None:
    h3_dates = [
        trade.execution_date or trade.date
        for trade in portfolio.trades
        if trade.symbol == config.kc50_code and trade.is_sell and "H3" in _trade_text(trade)
    ]
    if not h3_dates:
        return None
    recent = [bar for bar in bars if bar.date > max(h3_dates)]
    if len(recent) < 5:
        return False
    position = portfolio.position(config.kc50_code)
    cash = portfolio.cash.get("kc50_wave", 0.0)
    return all(position.shares * bar.close + cash > 1800 for bar in recent[-5:])


def _kc50_graded_risk(
    config: TrackerConfig,
    portfolio: PortfolioState,
    bars: list[PriceBar],
    position: object,
) -> Signal | None:
    buy_count = _kc50_buy_count(config, portfolio)
    if buy_count >= 4 and consecutive_closes_below_ma(bars, 20, 20) and position.avg_cost > 0:
        loss = (bars[-1].close - position.avg_cost) / position.avg_cost
        if loss <= -0.15 and _ma_is_falling(bars, 20):
            return Signal("ACTION", "科创50满4笔完整风控减仓", "连续20日低于20日均线、20日均线下行且亏损超过15%，按周度计划分3次减仓。", symbol=config.kc50_code, action="sell", rule_id="KC50-GRADED-RISK")
    if buy_count >= 3 and consecutive_closes_below_ma(bars, 20, 15) and _ma_is_falling(bars, 20):
        return Signal("REVIEW", "科创50第4笔需分级风控复核", "已买入3笔且连续15日低于下行20日均线，第4笔必须等待过滤条件和人工复核。", rule_id="KC50-GRADED-RISK")
    return None


def _apply_s1_correlation_limit(signals: list[Signal]) -> None:
    candidates = [(index, signal) for index, signal in enumerate(signals) if _is_new_buy_candidate(signal)]
    if not candidates:
        return
    candidates.sort(key=lambda item: (0 if item[1].symbol == "563360" else 1, item[0]))
    planned = 0.0
    for index, signal in candidates:
        amount = signal.planned_amount or 0.0
        if planned + amount <= 800 + 1e-9:
            planned += amount
            continue
        signals[index] = Signal(
            "WARN",
            signal.title,
            f"S1相关性限制：双标的同日新增买入计划金额不得超过800元，优先A500；该候选延后1个交易日复核。原提示：{signal.detail}",
            symbol=signal.symbol,
            action=signal.action,
            planned_amount=signal.planned_amount,
            is_new_buy=signal.is_new_buy,
            rule_id=signal.rule_id or "S1-CORRELATION",
        )


def _valuation_percentile_pass(config: TrackerConfig, key: str, threshold: float) -> tuple[bool, str]:
    valuation = config.raw.get("valuation", {})
    raw_value = valuation.get(key)
    source = str(valuation.get("source") or valuation.get(key.replace("_percentile", "_source")) or "").strip()
    as_of = str(valuation.get("as_of") or valuation.get(key.replace("_percentile", "_as_of")) or "").strip()
    try:
        percentile = float(raw_value)
    except (TypeError, ValueError):
        return False, "估值缺失"
    if percentile > 1:
        percentile = percentile / 100
    if not source or source == "待填写" or not as_of:
        return False, f"{percentile:.2%}，来源或日期未锁定"
    return percentile <= threshold, f"{percentile:.2%}"


def _is_new_low_at(bars: list[PriceBar], index: int, window: int) -> bool:
    if index < window:
        return False
    return bars[index].close < min(bar.close for bar in bars[index - window : index])


def _close_above_ma_at(bars: list[PriceBar], index: int, window: int) -> bool:
    if index + 1 < window:
        return False
    avg = sum(bar.close for bar in bars[index + 1 - window : index + 1]) / window
    return bars[index].close > avg


def _ma_is_falling(bars: list[PriceBar], window: int) -> bool:
    current = moving_average(bars, window)
    previous = prior_moving_average(bars, window)
    return current is not None and previous is not None and current < previous


def _trading_days_since(bars: list[PriceBar], start: date) -> int:
    return sum(1 for bar in bars if bar.date > start)


def _trading_days_between(bars: list[PriceBar], start: date, end: date) -> int:
    return sum(1 for bar in bars if start < bar.date <= end)


def _trade_text(trade: object) -> str:
    return " ".join(str(getattr(trade, field, "") or "") for field in ("module", "trigger_rule", "note"))


def _zh_int(value: str) -> int:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if value.isdigit():
        return int(value)
    return digits.get(value, 0)


def _is_weak(bars: list[PriceBar]) -> bool | None:
    ma20 = moving_average(bars, 20)
    prior_ma20 = prior_moving_average(bars, 20)
    if ma20 is None or prior_ma20 is None:
        return None
    return bars[-1].close < ma20 and ma20 < prior_ma20


def _symbol_buy_amount(portfolio: PortfolioState, symbol: str) -> float:
    return sum(trade.amount for trade in portfolio.trades if trade.symbol == symbol and trade.is_buy)


def _a500_grid_parameters(
    config: TrackerConfig,
    portfolio: PortfolioState,
    bars: list[PriceBar],
    grid_spacing: float,
    max_grid_buys: int,
) -> A500GridParameters:
    actual = _a500_actual_base_price(config, portfolio)
    if actual is not None:
        base_price, source = actual
        base_price = _round_etf_price(base_price)
        return A500GridParameters(
            base_price=base_price,
            lower=_round_etf_price(base_price * (1 - grid_spacing * max_grid_buys)),
            upper=_round_etf_price(base_price * 1.15),
            source=source,
            is_actual=True,
        )

    return _a500_suggested_grid_parameters(bars)


def _a500_actual_base_price(config: TrackerConfig, portfolio: PortfolioState) -> tuple[float, str] | None:
    position = portfolio.positions.get(config.a500_code)
    if position is None or position.shares <= 0:
        return None

    buys = [trade for trade in portfolio.trades if trade.symbol == config.a500_code and trade.is_buy]
    base_buys = [trade for trade in buys if "底仓" in trade.module]
    if base_buys:
        shares = sum(trade.shares for trade in base_buys)
        cost = sum(trade.amount + trade.fee for trade in base_buys)
        if shares > 0 and cost > 0:
            return cost / shares, "实际：A500底仓成交均价"

    if position.avg_cost > 0:
        return position.avg_cost, "实际：A500当前持仓均价"

    return None


def _a500_suggested_grid_parameters(bars: list[PriceBar]) -> A500GridParameters:
    recent = bars[-20:] if len(bars) >= 20 else bars
    base_price = _round_etf_price(sum(item.close for item in recent) / len(recent))
    source = "建议：当前持仓为0，基准价为最近20日收盘均价"
    if len(bars) < 20:
        source = f"建议：当前持仓为0，基准价为最近{len(recent)}日收盘均价"

    return A500GridParameters(
        base_price=base_price,
        lower=_round_etf_price(base_price * 0.82),
        upper=_round_etf_price(base_price * 1.18),
        source=source,
        is_actual=False,
        suggested_spacing=_a500_dynamic_grid_spacing(bars),
    )


def _a500_dynamic_grid_spacing(bars: list[PriceBar]) -> float:
    latest_close = bars[-1].close
    atr20 = _average_true_range(bars, 20)
    if atr20 is None or latest_close <= 0:
        return A500_SUGGESTED_FALLBACK_SPACING
    return max(0.03, min(0.055, 0.8 * atr20 / latest_close))


def _average_true_range(bars: list[PriceBar], window: int) -> float | None:
    if len(bars) < window:
        return None

    values: list[float] = []
    first_index = len(bars) - window
    for index in range(first_index, len(bars)):
        current = bars[index]
        previous_close = bars[index - 1].close if index > 0 else current.close
        values.append(
            max(current.high - current.low, abs(current.high - previous_close), abs(current.low - previous_close))
        )

    return sum(values) / len(values) if values else None


def _round_etf_price(value: float) -> float:
    return round(value + 1e-12, 3)


def _reaches_threshold(value: float, threshold: float) -> bool:
    return value + 1e-12 >= threshold


def _latest_common_date(a: list[PriceBar], b: list[PriceBar]) -> date | None:
    if a and b:
        return min(a[-1].date, b[-1].date)
    if a:
        return a[-1].date
    if b:
        return b[-1].date
    return None
