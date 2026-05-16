from __future__ import annotations

import json
import sys
from datetime import date
from types import SimpleNamespace

import pytest

from etf_tracker import market


def test_shanghai_etf_symbols_use_eastmoney_shanghai_market() -> None:
    assert market._eastmoney_market_id("563360") == "1"
    assert market._eastmoney_market_id("588000") == "1"
    assert market._sina_symbol("563360") == "sh563360"
    assert market._sina_symbol("588000") == "sh588000"


def test_eastmoney_history_parses_price_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            payload = {
                "data": {
                    "klines": [
                        "2026-05-14,1.010,1.020,1.030,1.000,1000,2000000,2.97,1.49,0.015,3.1",
                        "2026-05-15,1.020,1.025,1.040,1.015,1200,2400000,2.45,0.49,0.005,3.4",
                    ]
                }
            }
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request: object, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["timeout"] = str(timeout)
        return FakeResponse()

    monkeypatch.setattr(market.urllib.request, "urlopen", fake_urlopen)

    bars = market._load_history_eastmoney("563360", 10)

    assert "secid=1.563360" in captured["url"]
    assert captured["timeout"] == "20"
    assert len(bars) == 2
    assert bars[0].date == date(2026, 5, 14)
    assert bars[0].open == pytest.approx(1.010)
    assert bars[0].close == pytest.approx(1.020)
    assert bars[0].high == pytest.approx(1.030)
    assert bars[0].low == pytest.approx(1.000)
    assert bars[0].pct_change == pytest.approx(1.49)
    assert bars[0].amount == pytest.approx(2000000)


def test_load_history_falls_back_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fail_akshare(symbol: str, lookback_days: int) -> list[market.PriceBar]:
        calls.append("akshare")
        raise RuntimeError("akshare down")

    def fail_eastmoney(symbol: str, lookback_days: int) -> list[market.PriceBar]:
        calls.append("eastmoney")
        raise RuntimeError("eastmoney down")

    def succeed_sina(symbol: str, lookback_days: int) -> list[market.PriceBar]:
        calls.append("sina")
        return [market.PriceBar(date(2026, 5, 15), 1.0, 1.1, 1.2, 0.9)]

    monkeypatch.setattr(market, "_load_history_akshare", fail_akshare)
    monkeypatch.setattr(market, "_load_history_eastmoney", fail_eastmoney)
    monkeypatch.setattr(market, "_load_history_sina", succeed_sina)

    bars = market.load_history("563360")

    assert calls == ["akshare", "eastmoney", "sina"]
    assert bars[0].close == pytest.approx(1.1)


def test_load_history_reports_all_source_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(source: str):
        def loader(symbol: str, lookback_days: int) -> list[market.PriceBar]:
            raise RuntimeError(f"{source} failed")

        return loader

    monkeypatch.setattr(market, "_load_history_akshare", fail("akshare"))
    monkeypatch.setattr(market, "_load_history_eastmoney", fail("eastmoney"))
    monkeypatch.setattr(market, "_load_history_sina", fail("sina"))

    with pytest.raises(RuntimeError) as exc_info:
        market.load_history("563360")

    message = str(exc_info.value)
    assert "akshare fund_etf_hist_em" in message
    assert "eastmoney kline" in message
    assert "akshare fund_etf_hist_sina" in message


def test_sina_history_generates_price_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeFrame:
        def iterrows(self):
            rows = [
                {"date": "2026-05-14", "open": 1.00, "high": 1.10, "low": 0.99, "close": 1.00},
                {"date": "2026-05-15", "open": 1.01, "high": 1.20, "low": 1.00, "close": 1.10},
            ]
            return iter(enumerate(rows))

    calls: dict[str, str] = {}

    def fake_fund_etf_hist_sina(symbol: str) -> FakeFrame:
        calls["symbol"] = symbol
        return FakeFrame()

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(fund_etf_hist_sina=fake_fund_etf_hist_sina))

    bars = market._load_history_sina("588000", 100000)

    assert calls["symbol"] == "sh588000"
    assert len(bars) == 2
    assert bars[1].date == date(2026, 5, 15)
    assert bars[1].open == pytest.approx(1.01)
    assert bars[1].high == pytest.approx(1.20)
    assert bars[1].low == pytest.approx(1.00)
    assert bars[1].close == pytest.approx(1.10)
    assert bars[1].pct_change == pytest.approx(10.0)
    assert bars[1].amount == pytest.approx(0.0)
