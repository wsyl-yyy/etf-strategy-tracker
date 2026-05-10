from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import load_config
from .crypto import encrypt_report, write_encrypted_report
from .market import load_history
from .portfolio import build_portfolio
from .report import render_markdown
from .strategy import evaluate
from .trades import load_trades


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate encrypted ETF strategy report.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON.")
    parser.add_argument("--trades", default="data/sample_trades.csv", help="Fallback local trades CSV.")
    parser.add_argument("--out", default="docs/report.json", help="Encrypted report JSON output.")
    parser.add_argument("--markdown-out", default="", help="Optional plaintext markdown output for local debugging.")
    parser.add_argument("--allow-plaintext", action="store_true", help="Allow writing plaintext markdown output.")
    args = parser.parse_args()

    config = load_config(args.config)
    trades = load_trades(config, args.trades)
    portfolio = build_portfolio(config, trades)

    histories = {
        config.a500_code: _safe_load_history(config.a500_code),
        config.kc50_code: _safe_load_history(config.kc50_code),
    }
    strategy_report = evaluate(config, portfolio, histories)
    markdown = render_markdown(strategy_report, portfolio)

    if args.markdown_out and args.allow_plaintext:
        Path(args.markdown_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_out).write_text(markdown, encoding="utf-8")

    password = os.environ.get("REPORT_PASSWORD")
    if not password:
        raise RuntimeError("缺少 REPORT_PASSWORD 环境变量，无法生成加密日报。")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    payload = encrypt_report(markdown, password)
    write_encrypted_report(payload, args.out)

def _safe_load_history(symbol: str):
    try:
        return load_history(symbol)
    except Exception as exc:
        print(f"[WARN] {symbol} 行情数据获取失败: {exc}")
        return []


if __name__ == "__main__":
    main()
