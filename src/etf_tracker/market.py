from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, timedelta

from .models import PriceBar


def load_history(symbol: str, lookback_days: int = 420) -> list[PriceBar]:
    """Load ETF daily history.

    AKShare is preferred because it is easier to maintain. Eastmoney's public
    kline endpoint is kept as a fallback so the workflow can still run when
    AKShare changes packaging or is temporarily unavailable.
    """
    try:
        return _load_history_akshare(symbol, lookback_days)
    except Exception:
        return _load_history_eastmoney(symbol, lookback_days)


def _load_history_akshare(symbol: str, lookback_days: int) -> list[PriceBar]:
    import akshare as ak

    start = (date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = (date.today() + timedelta(days=7)).strftime("%Y%m%d")
    frame = ak.fund_etf_hist_em(symbol=symbol, period="daily", start_date=start, end_date=end, adjust="qfq")
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
    params = {
        "secid": f"1.{symbol}",
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


def _to_date(value: object) -> date:
    text = str(value)
    return date.fromisoformat(text[:10])

