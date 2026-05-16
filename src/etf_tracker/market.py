from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, timedelta

from .models import PriceBar


def load_history(symbol: str, lookback_days: int = 420) -> list[PriceBar]:
    """Load ETF daily history.

    AKShare is preferred because it is easier to maintain. Eastmoney's public
    kline endpoint and Sina's ETF history are fallbacks so the workflow can
    still run when one public data source is temporarily unavailable.
    """
    loaders = [
        ("akshare fund_etf_hist_em", _load_history_akshare),
        ("eastmoney kline", _load_history_eastmoney),
        ("akshare fund_etf_hist_sina", _load_history_sina),
    ]
    errors: list[str] = []
    for source, loader in loaders:
        try:
            bars = loader(symbol, lookback_days)
            if bars:
                return bars
            raise ValueError("returned no rows")
        except Exception as exc:
            errors.append(f"{source}: {exc}")

    raise RuntimeError(f"Unable to load ETF history for {symbol}; " + "; ".join(errors))


def _load_history_akshare(symbol: str, lookback_days: int) -> list[PriceBar]:
    import akshare as ak

    start = (date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = (date.today() + timedelta(days=7)).strftime("%Y%m%d")
    frame = ak.fund_etf_hist_em(
        symbol=_bare_symbol(symbol),
        period="daily",
        start_date=start,
        end_date=end,
        adjust="qfq",
    )
    bars: list[PriceBar] = []
    for _, row in frame.iterrows():
        bars.append(
            PriceBar(
                date=_to_date(row["日期"]),
                open=float(row["开盘"]),
                close=float(row["收盘"]),
                high=float(row["最高"]),
                low=float(row["最低"]),
                pct_change=float(row.get("涨跌幅", 0) or 0),
                amount=float(row.get("成交额", 0) or 0),
            )
        )
    return sorted(bars, key=lambda item: item.date)


def _load_history_eastmoney(symbol: str, lookback_days: int) -> list[PriceBar]:
    start = (date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = (date.today() + timedelta(days=7)).strftime("%Y%m%d")
    bare_symbol = _bare_symbol(symbol)
    params = {
        "secid": f"{_eastmoney_market_id(bare_symbol)}.{bare_symbol}",
        "klt": "101",
        "fqt": "1",
        "beg": start,
        "end": end,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    klines = payload.get("data", {}).get("klines") or []
    bars: list[PriceBar] = []
    for line in klines:
        parts = line.split(",")
        bars.append(
            PriceBar(
                date=_to_date(parts[0]),
                open=float(parts[1]),
                close=float(parts[2]),
                high=float(parts[3]),
                low=float(parts[4]),
                pct_change=float(parts[8]),
                amount=float(parts[6]),
            )
        )
    return sorted(bars, key=lambda item: item.date)


def _load_history_sina(symbol: str, lookback_days: int) -> list[PriceBar]:
    import akshare as ak

    start = date.today() - timedelta(days=lookback_days)
    frame = ak.fund_etf_hist_sina(symbol=_sina_symbol(symbol))
    bars: list[PriceBar] = []
    prior_close: float | None = None
    for _, row in frame.iterrows():
        current_date = _to_date(row["date"])
        close = float(row["close"])
        if current_date >= start:
            pct_change = 0.0
            if prior_close is not None and prior_close != 0:
                pct_change = (close - prior_close) / prior_close * 100
            bars.append(
                PriceBar(
                    date=current_date,
                    open=float(row["open"]),
                    close=close,
                    high=float(row["high"]),
                    low=float(row["low"]),
                    pct_change=pct_change,
                    amount=0.0,
                )
            )
        prior_close = close
    return sorted(bars, key=lambda item: item.date)


def _bare_symbol(symbol: str) -> str:
    text = symbol.strip().lower()
    if text.startswith(("sh", "sz")):
        return text[2:]
    return text


def _eastmoney_market_id(symbol: str) -> str:
    return "1" if _bare_symbol(symbol).startswith(("5", "6", "9")) else "0"


def _sina_symbol(symbol: str) -> str:
    bare_symbol = _bare_symbol(symbol)
    exchange = "sh" if _eastmoney_market_id(bare_symbol) == "1" else "sz"
    return f"{exchange}{bare_symbol}"


def _to_date(value: object) -> date:
    text = str(value)
    return date.fromisoformat(text[:10])
