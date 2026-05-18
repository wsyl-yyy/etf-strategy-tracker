from __future__ import annotations

from datetime import datetime

from .models import PortfolioState
from .strategy import StrategyReport


def render_markdown(strategy_report: StrategyReport, portfolio: PortfolioState) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_date = strategy_report.date.isoformat() if strategy_report.date else "无行情日期"
    lines: list[str] = [
        "# ETF量化策略日报",
        "",
        f"- 报告日期：{report_date}",
        f"- 生成时间：{generated_at}",
        "- 说明：本报告只用于策略记录和提醒，不构成投资建议，也不会自动下单。",
        "",
        "## 今日结论",
    ]

    for signal in strategy_report.signals:
        lines.append(f"- **[{signal.level}] {signal.title}**：{signal.detail}")

    if strategy_report.warnings:
        lines.extend(["", "## 数据与配置提醒"])
        for warning in strategy_report.warnings:
            lines.append(f"- {warning}")

    lines.extend(["", "## 关键指标"])
    for key, value in strategy_report.metrics.items():
        lines.append(f"- {key}：{value}")

    lines.extend(["", "## 资金池现金余额"])
    for key, value in portfolio.cash.items():
        lines.append(f"- {key}：{value:.2f}")

    lines.extend(["", "## 当前持仓"])
    if portfolio.positions:
        for symbol, position in portfolio.positions.items():
            lines.append(
                f"- {symbol}：份额 {position.shares:.0f}，已投入成本 {position.cost:.2f}，均价 {position.avg_cost:.4f}"
            )
    else:
        lines.append("- 暂无持仓记录。")

    lines.extend(["", "## 最近成交"])
    recent_trades = portfolio.trades[-5:]
    if recent_trades:
        for trade in recent_trades:
            audit_parts = [f"模块：{trade.module}", f"交易费用：{trade.fee:.2f}"]
            if trade.trigger_rule:
                audit_parts.append(f"触发规则：{trade.trigger_rule}")
            if trade.cash_balance is not None:
                audit_parts.append(f"现金余额：{trade.cash_balance:.2f}")
            if trade.risk_gate_triggered:
                audit_parts.append("已触发风险闸门")
            if trade.risk_gate_snapshot:
                audit_parts.append(f"闸门快照：{trade.risk_gate_snapshot}")
            if trade.compliance_warnings:
                audit_parts.append(f"合规警告：{'；'.join(trade.compliance_warnings)}")
            lines.append(
                f"- 信号日 {trade.signal_date} / 执行日 {trade.execution_date}，{trade.symbol} {trade.side} "
                f"{trade.amount:.2f}元 @ {trade.price:.4f}，份额 {trade.shares:.0f}，{'；'.join(audit_parts)}"
            )
    else:
        lines.append("- 暂无成交记录。")

    return "\n".join(lines) + "\n"
