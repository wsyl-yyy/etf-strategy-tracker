from __future__ import annotations

from .config import TrackerConfig
from .models import PortfolioState, Trade


def build_portfolio(config: TrackerConfig, trades: list[Trade]) -> PortfolioState:
    state = PortfolioState(cash=config.funds.copy(), trades=sorted(trades, key=lambda item: item.date))
    for trade in state.trades:
        pool = _pool_for_trade(config, trade)
        if trade.is_buy:
            state.cash[pool] = state.cash.get(pool, 0.0) - trade.amount - trade.fee
        elif trade.is_sell:
            state.cash[pool] = state.cash.get(pool, 0.0) + trade.amount - trade.fee
        state.position(trade.symbol).apply(trade)
    return state


def _pool_for_trade(config: TrackerConfig, trade: Trade) -> str:
    module = trade.module.lower()
    if "备用" in trade.module or "reserve" in module:
        return "reserve"
    if trade.symbol == config.a500_code:
        return "a500_grid"
    if trade.symbol == config.kc50_code:
        return "kc50_wave"
    return "reserve"

