from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import TrackerConfig
from .models import Trade


FIELD_ALIASES = {
    "date": ["日期", "信号确认日", "成交日期", "Timestamp", "date"],
    "symbol": ["标的", "代码", "ETF代码", "symbol"],
    "side": ["方向", "买卖方向", "买入/卖出", "side"],
    "module": ["策略模块", "模块", "module"],
    "price": ["成交价", "执行价格", "price"],
    "amount": ["成交金额", "执行金额", "金额", "amount"],
    "shares": ["成交份额", "执行份额", "份额", "shares"],
    "fee": ["交易费用", "费用", "交易成本", "fee"],
    "note": ["备注", "复盘备注", "note"],
}


def load_trades(config: TrackerConfig, csv_path: str | Path | None = None) -> list[Trade]:
    if _google_is_configured():
        try:
            return _load_google_sheet_trades(config)
        except Exception as exc:
            print(f"[WARN] Google Sheets 成交记录读取失败，改用本地 CSV: {exc}")

    if csv_path and Path(csv_path).exists():
        return _load_csv_trades(csv_path)

    return []


def _google_is_configured() -> bool:
    credentials = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    sheet_id = (os.environ.get("GOOGLE_SHEET_ID") or "").strip()
    if not credentials or not sheet_id:
        return False
    return credentials.startswith("{")


def _load_google_sheet_trades(config: TrackerConfig) -> list[Trade]:
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Google Sheets 依赖未安装，请先安装 requirements.txt") from exc

    service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    service = build("sheets", "v4", credentials=credentials)
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    range_name = os.environ.get("GOOGLE_SHEET_RANGE", config.google_sheet_range)
    result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute()
    values = result.get("values", [])
    if not values:
        return []

    headers = [str(item).strip() for item in values[0]]
    rows = [dict(zip(headers, row)) for row in values[1:] if any(str(cell).strip() for cell in row)]
    return [_row_to_trade(row, headers) for row in rows]


def _load_csv_trades(path: str | Path) -> list[Trade]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        headers = reader.fieldnames or []
    return [_row_to_trade(row, headers) for row in rows if any(str(value).strip() for value in row.values())]


def _row_to_trade(row: dict[str, Any], headers: list[str]) -> Trade:
    mapped = {field: _read_alias(row, headers, aliases) for field, aliases in FIELD_ALIASES.items()}
    return Trade(
        date=_parse_date(mapped["date"]),
        symbol=_normalize_symbol(mapped["symbol"]),
        side=str(mapped["side"]).strip(),
        module=str(mapped.get("module") or "").strip(),
        price=_to_float(mapped["price"]),
        amount=_to_float(mapped["amount"]),
        shares=_to_float(mapped["shares"]),
        fee=_to_float(mapped.get("fee") or 0),
        note=str(mapped.get("note") or "").strip(),
    )


def _read_alias(row: dict[str, Any], headers: list[str], aliases: list[str]) -> Any:
    for alias in aliases:
        if alias in row and str(row[alias]).strip() != "":
            return row[alias]
    for header in headers:
        normalized = str(header).strip().lower()
        if normalized in {alias.lower() for alias in aliases}:
            return row[header]
    return ""


def _parse_date(value: Any) -> date:
    text = str(value).strip()
    if " " in text and "/" in text:
        text = text.split(" ")[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"无法解析成交日期: {value!r}")


def _normalize_symbol(value: Any) -> str:
    text = str(value).strip()
    if "." in text:
        text = text.split(".")[-1]
    return text.zfill(6)


def _to_float(value: Any) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    return float(str(value).replace(",", "").strip())
