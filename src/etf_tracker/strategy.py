from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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
from .models import PortfolioState, PriceBar, Trade
from .valuation import valuation_status


@dataclass(frozen=True)
class Signal:
    level: str
    title: str
    detail: str


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

    grid_spacing = float(symbol_config.get("grid_spacing", 0.058))
    max_grid_buys = int(symbol_config.get("max_grid_buys", 5))
    grid_parameters = _a500_grid_parameters(config, portfolio, bars, grid_spacing, max_grid_buys)
    label = "实际" if grid_parameters.is_actual else "建议"
    metrics[f"A500{label}网格基准价"] = f"{grid_parameters.base_price:.3f}"
    metrics[f"A500{label}网格上沿"] = f"{grid_parameters.upper:.3f}"
    metrics[f"A500{label}网格下沿"] = f"{grid_parameters.lower:.3f}"
    metrics["A500网格参数来源"] = grid_parameters.source

    if grid_parameters.is_actual:
        base_price = grid_parameters.base_price
        drawdown = (base_price - latest.close) / base_price if base_price > 0 else 0
        grid_level = int(drawdown // grid_spacing) if drawdown > 0 else 0
        metrics["A500相对基准回撤"] = f"{drawdown:.2%}"
        if grid_level > 0:
            if grid_level <= max_grid_buys:
                signals.append(
                    Signal(
                        "REVIEW",
                        f"A500可能触发第{grid_level}格补仓",
                        "需结合已成交网格档位、风险闸门和 S1 限额确认是否下单。",
                    )
                )
            else:
                signals.append(Signal("WARN", "A500超过最大普通补仓格数", "普通网格不应继续机械补仓。"))

        if position.shares > 0 and position.avg_cost > 0:
            profit = (latest.close - position.avg_cost) / position.avg_cost
            metrics["A500持仓浮盈亏"] = f"{profit:.2%}"
            if profit >= 0.15:
                signals.append(Signal("ACTION", "A500底仓盈利达到15%", "按策略卖出底仓的1/2，实际份额需人工确认。"))

    if grid_parameters.is_actual and latest.close < grid_parameters.lower:
        signals.append(Signal("WARN", "A500跌破网格下沿", "暂停普通网格补仓；备用金只进入人工复核候选。"))

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

    dd = drawdown_from_high(bars, 252)
    if dd is not None:
        metrics["科创50近12个月高点回撤"] = f"{dd:.2%}"
        steps = config.symbols["kc50"]["buy_steps"]
        bought_amount = _symbol_buy_amount(portfolio, config.kc50_code)
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
                if index == 4:
                    detail += " 第4笔还必须满足连续5日不创新低且站上5日均线。"
                signals.append(Signal(level, f"科创50可能触发第{index}笔买入", detail))
                break

    new_low = is_20_day_new_low(bars)
    if new_low is not None:
        metrics["科创50是否20日新低"] = "是" if new_low else "否"

    if len(bars) >= 2 and bars[-1].pct_change <= -8:
        signals.append(Signal("WARN", "科创50单日收盘跌幅超过8%", "次日暂停科创50全部新增买入。"))

    if net_value < 1400 and position.shares > 0:
        signals.append(Signal("ACTION", "科创50波段净值低于1400", "按H3清仓全部剩余科创50仓位。"))
    elif net_value < 1500 and position.shares > 0:
        signals.append(Signal("ACTION", "科创50波段净值低于1500", "按H3卖出当前科创50仓位的1/2，并暂停新增买入。"))
    elif net_value < 1700:
        signals.append(Signal("WARN", "科创50波段净值低于1700", "停止新增买入，进入观察。"))

    if position.shares > 0 and position.avg_cost > 0:
        profit = (latest.close - position.avg_cost) / position.avg_cost
        metrics["科创50相对持仓成本"] = f"{profit:.2%}"
        if profit >= 0.30:
            signals.append(Signal("ACTION", "科创50盈利达到30%", "次日再卖出1/3仓位；若已执行过第二档，则检查移动止盈。"))
        elif profit >= 0.15:
            signals.append(Signal("ACTION", "科创50盈利达到15%", "次日卖出1/3仓位。"))

    below_20_for_10 = consecutive_closes_below_ma(bars, 20, 10)
    if below_20_for_10:
        signals.append(Signal("WARN", "科创50连续10日低于20日均线", "若已买入2笔，暂停新增买入。"))

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
    floating_loss_ratio = 0.0 if total_cost <= 0 else min(0.0, total_value - total_cost) / total_cost
    invested_ratio = total_cost / config.total_capital if config.total_capital > 0 else 0
    metrics["总投入成本比例"] = f"{invested_ratio:.2%}"
    metrics["总持仓浮亏比例"] = f"{floating_loss_ratio:.2%}"

    if invested_ratio >= 0.85:
        signals.append(Signal("WARN", "总投入成本达到85%", "禁止新增买入，只允许止盈、减仓、复核和记录。"))
    elif invested_ratio >= 0.70:
        signals.append(Signal("REVIEW", "总投入成本达到70%", "新增买入必须通过总风险闸门。"))

    corr = correlation(a500, kc50, 20)
    if corr is not None:
        metrics["20日收益相关系数"] = f"{corr:.3f}"
        if corr > 0.85:
            signals.append(Signal("REVIEW", "双标的20日相关系数大于0.85", "同日新增买入合计计划金额不超过800元，优先A500。"))

    vol = annualized_volatility(kc50, 20)
    if vol is not None:
        metrics["科创5020日年化波动率"] = f"{vol:.2%}"
        if vol > 0.50:
            signals.append(Signal("INFO", "科创50处于高波动阶段", "剩余1/3仓位移动止盈回撤参数从12%放宽至13%。"))

    a500_weak = _is_weak(a500)
    kc50_weak = _is_weak(kc50)
    if a500_weak is not None and kc50_weak is not None:
        metrics["双标的趋势"] = "同时弱势" if a500_weak and kc50_weak else "非同时弱势"


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
    base_trade = _a500_base_trade(config, portfolio)
    if base_trade is not None:
        base_price = _round_etf_price(base_trade.price)
        source = f"实际：{base_trade.date} 底仓买入价"
        if "底仓" not in base_trade.module:
            source = f"实际：{base_trade.date} 首笔A500买入价"
        return A500GridParameters(
            base_price=base_price,
            lower=_round_etf_price(base_price * (1 - grid_spacing * max_grid_buys)),
            upper=_round_etf_price(base_price * 1.15),
            source=source,
            is_actual=True,
        )

    recent = bars[-20:] if len(bars) >= 20 else bars
    base_price = _round_etf_price(max(item.close for item in recent))
    return A500GridParameters(
        base_price=base_price,
        lower=_round_etf_price(base_price * (1 - grid_spacing * max_grid_buys)),
        upper=base_price,
        source=f"建议：最近{len(recent)}个交易日收盘高点",
        is_actual=False,
    )


def _a500_base_trade(config: TrackerConfig, portfolio: PortfolioState) -> Trade | None:
    buys = [trade for trade in portfolio.trades if trade.symbol == config.a500_code and trade.is_buy]
    base_buys = [trade for trade in buys if "底仓" in trade.module]
    if base_buys:
        return base_buys[0]
    if buys:
        return buys[0]
    return None


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
