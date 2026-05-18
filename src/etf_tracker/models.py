from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class PriceBar:
    date: date
    open: float
    close: float
    high: float
    low: float
    pct_change: float = 0.0
    amount: float = 0.0


@dataclass(frozen=True)
class Trade:
    date: date
    symbol: str
    side: str
    module: str
    price: float
    amount: float
    shares: float
    fee: float = 0.0
    note: str = ""
    signal_date: date | None = None
    execution_date: date | None = None
    trigger_rule: str = ""
    cash_balance: float | None = None
    risk_gate_triggered: bool = False
    risk_gate_snapshot: str = ""
    compliance_warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.signal_date is None:
            object.__setattr__(self, "signal_date", self.date)
        if self.execution_date is None:
            object.__setattr__(self, "execution_date", self.date)

    @property
    def is_buy(self) -> bool:
        return self.side in {"买入", "buy", "BUY", "Buy"}

    @property
    def is_sell(self) -> bool:
        return self.side in {"卖出", "sell", "SELL", "Sell"}


@dataclass
class Position:
    symbol: str
    shares: float = 0.0
    cost: float = 0.0
    realized_sell_amount: float = 0.0

    @property
    def avg_cost(self) -> float:
        if self.shares <= 0:
            return 0.0
        return self.cost / self.shares

    def apply(self, trade: Trade) -> None:
        if trade.is_buy:
            self.shares += trade.shares
            self.cost += trade.amount
            return

        if trade.is_sell:
            if self.shares <= 0:
                self.realized_sell_amount += trade.amount - trade.fee
                return
            sold_ratio = min(1.0, trade.shares / self.shares)
            self.cost *= 1.0 - sold_ratio
            self.shares = max(0.0, self.shares - trade.shares)
            self.realized_sell_amount += trade.amount - trade.fee


@dataclass
class PortfolioState:
    cash: dict[str, float]
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)

    def position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]
