# AGENTS.md

## Project Summary

This repository implements an encrypted ETF strategy tracker for the strategy document `ETF量化交易策略 V3.2 正式执行版.md`.

Core flow:

- GitHub Actions runs on schedule, manual dispatch, push to `main`, and Cloudflare Worker `repository_dispatch`.
- Python reads strategy config, market data, and trade records, then generates an encrypted daily report.
- GitHub Pages publishes only the static decrypt UI and encrypted report payload.
- Cloudflare Worker + KV stores trade records and exposes mobile submit/manage APIs.
- The mobile page supports viewing the encrypted report, submitting trades, editing trades, and deleting trades.

This tool is for tracking, review, and reminders only. It does not connect to a broker and must not place trades.

## Project Structure

```text
.github/workflows/daily-report.yml
  GitHub Actions workflow. Runs tests, prepares config, generates encrypted report, publishes docs/ to gh-pages.

cloudflare-worker/
  Cloudflare Worker source and deployment notes. Worker stores trades in KV and triggers GitHub Actions.

data/sample_trades.csv
  Local fallback trade sample.

docs/index.html
  GitHub Pages UI. Contains local report decryption, trade submit form, and trade management UI.

docs/product-requirements/
  Product requirement documents derived from strategy coverage feedback. `strategy-test-report-prd.md` is the scope for closing failed/uncovered rules; `strategy-test-report-delivery.md` is the concise delivery summary and manual-review boundary list.

scripts/create_google_form.gs
  Legacy/backup Google Forms + Sheets helper. Current recommended flow is Cloudflare Worker + KV.

scripts/generate_strategy_test_report.mjs
  Local Node report generator for customer-facing strategy-document coverage feedback.

scripts/strategy_test_mapping.json
  Manual evidence map from strategy document clauses to pass/fail/uncovered/manual conclusions. Delivery reports should have zero failed and zero uncovered items.

src/etf_tracker/
  Python package for config loading, market data, portfolio state, strategy evaluation, report rendering, encryption, and CLI.

tests/
  Pytest coverage for strategy behavior and trade payload parsing, plus Node tests for Worker API behavior.

test-reports/
  Local generated delivery reports, including `strategy-test-report.html`.
```

Important generated/local files:

- `config.json` is local/secret and ignored.
- `docs/report.json` is generated and ignored locally; Actions publishes the generated version to `gh-pages`.
- `test-reports/strategy-test-report.html` is generated locally for delivery testing feedback; regenerate it from the mapping file before sharing.
- Fourth-phase strategy coverage archival requires `test-reports/strategy-test-report.html` to show `失败=0` and `未覆盖=0`; manual-review items must keep explicit evidence explaining why they are not automated.
- `docs/product-requirements/strategy-test-report-delivery.md` is the short handoff for this strategy coverage repair: update it when the delivered logic, verification commands, report counts, or manual-review boundary changes.
- `.venv/`, `.pytest_cache/`, `__pycache__/`, and original strategy docs are ignored.

## Run And Test Commands

Local setup on Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
```

Run tests:

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest
```

If `.venv` reports `No Python`, verify the host interpreter path before treating Python verification as blocked. Check `where.exe python` and `py -0p`; an older local fallback path may be:

```powershell
& 'C:\Users\berna\AppData\Local\Programs\Python\Python311\python.exe' -m pytest
```

Recreate `.venv` afterward if its stored absolute interpreter path is stale. In sandboxed sessions, running the host interpreter may require approval; do not mark Python tests as unverified until available host interpreters have been checked or dependency installation is explicitly blocked.

Generate a local encrypted report:

```powershell
$env:PYTHONPATH='src'
$env:REPORT_PASSWORD='local-test-password'
.\.venv\Scripts\python -m etf_tracker.cli --config config.json --trades data/sample_trades.csv --out docs/report.json
```

Generate a local embedded HTML preview with Worker submit URL:

```powershell
$env:PYTHONPATH='src'
$env:REPORT_PASSWORD='local-test-password'
.\.venv\Scripts\python -m etf_tracker.cli --config config.example.json --trades data/sample_trades.csv --out docs/report.json --html-template docs/index.html --html-out docs/report.tmp.html --worker-trades-url https://etf-trade-worker.wsyl-yyy-etf.workers.dev/trades
```

Check Worker JavaScript syntax:

```powershell
node --check cloudflare-worker\worker.js
```

Run Worker API tests:

```powershell
node --test tests\worker.test.mjs
```

Run strategy test report generator tests:

```powershell
node --test tests\strategy_report_generator.test.mjs
```

Generate the local strategy-document testing feedback webpage:

```powershell
node scripts\generate_strategy_test_report.mjs
```

Run only market data tests:

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest tests\test_market.py
```

Do not commit generated preview files such as `docs/report.tmp.html`.

## Current Deployment

GitHub repository:

- `wsyl-yyy/etf-strategy-tracker`
- Default branch: `main`
- Pages branch: `gh-pages`

GitHub Pages:

- Source: `gh-pages` branch, root.
- Public page hosts the decrypt UI and encrypted report only.

Cloudflare:

- Worker: `etf-trade-worker`
- Worker URL: `https://etf-trade-worker.wsyl-yyy-etf.workers.dev`
- KV namespace title: `etf-trades`
- KV binding name in Worker: `TRADES`

GitHub Secrets expected:

- `REPORT_PASSWORD`
- `CONFIG_JSON`
- `WORKER_TRADES_URL`
- `WORKER_READ_TOKEN`
- Optional legacy Google fallback: `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEET_ID`, `GOOGLE_SHEET_RANGE`

Cloudflare Worker secrets/variables expected:

- Secrets: `SUBMIT_PASSWORD`, `READ_TOKEN`, `GITHUB_TOKEN`
- Variables: `ALLOWED_ORIGIN=https://wsyl-yyy.github.io`, `GITHUB_REPO=wsyl-yyy/etf-strategy-tracker`

Never write actual secret values into tracked files.

## Architecture Notes

- Cloudflare Worker + KV is the preferred trade source. Google Sheets support remains only as a fallback.
- Worker APIs are `POST /trade`, `GET /trades`, `POST /trades/manage`, `PUT /trade/:id`, `DELETE /trade/:id`, and `GET /health`.
- Worker-triggered GitHub `repository_dispatch` refreshes the report after trade changes.
- Trade records support both legacy `date` and audit fields: `signal_date`, `execution_date`, `trigger_rule`, `cash_balance`, `risk_gate_triggered`, and `risk_gate_snapshot`. When new dates are missing, legacy `date` is treated as both signal confirmation date and execution date.
- Market data loading order is AKShare Eastmoney ETF history, Eastmoney public kline, then AKShare Sina ETF history.
- Eastmoney market IDs are inferred from ETF code: `5/6/9` prefixes use Shanghai `1`, all others use Shenzhen `0`.
- Sina ETF history uses `sh`/`sz` prefixed symbols and has no traded amount; amount is stored as `0`.
- Position invested cost excludes trade fees; fees remain separate and still affect cash balances. A500 grid parameters are generated in the strategy layer. With live 563360 holdings, the actual grid base is weighted bottom-position execution cost `(amount + fee) / shares`, falling back to current position average cost when no bottom-position buy is tagged. When current 563360 holdings are zero, the report shows suggested grid parameters only: base is the latest available 20-day closing-price average, upper/lower bounds are base price times `1.18` and `0.82`, and suggested dynamic spacing is `max(3%, min(5.5%, 0.8 * 20-day ATR / latest close))` with a 4% fallback.
- A500 actual grid spacing defaults to 5.5% unless config overrides it after review. Ordinary grid buy/sell state is derived from trade `trigger_rule` text such as `A500第2格补仓`; missing or unclear old records are treated conservatively.
- A500 reserve actions remain manual-review candidates. Strategy output distinguishes A/B/C reserve conditions, reserve interval, reserve position P/L, and loss/profit review boundaries, but still does not create orders.
- KC50 buy stages are derived from recorded buys and configured `buy_steps`. The first-buy valuation cross check uses manual `valuation.kc50_percentile`; fourth-buy filtering also reads `valuation.kc50_pe_percentile` and `valuation.kc50_pb_percentile`.
- KC50 H3 recovery, fixed profit-take, trailing profit, graded risk, and time review are all derived from trade history plus market bars; no separate persistent strategy-state file exists.
- Phase-three lifecycle controls are also derived from existing trades and config, not a new state file. H6 uses `清仓` sells to enforce a 5-trading-day restart cooldown, then only emits a review-level probe buy up to 50% of the normal first buy while showing relocked base/high/grid/cash metrics. H7 reviews each buy on its 10th trading day and pauses that symbol after 3 consecutive still-loss buys.
- Long-term risk controls read optional `account.locked_cash`, `account.peak_value`, `account.strategy_start_date`, `benchmarks.hs300_return`, and `benchmarks.money_fund_return`. Locked cash is included in total account value and reporting, but it is not added to trading cash pools or candidate sizing. A drawdown above 12% blocks all new buys; 6-month benchmark comparison remains a manual review prompt.
- KC50 reserve recovery after H3 reads `reserve.kc50_recovery.confirmed/source/as_of/note/max_amount`. Missing manual confirmation waits for review; when confirmed, H3 recovery, reserve safety, and the one-tranche limit must all pass before a max-1000-yuan review candidate is shown.
- S1 correlation limits and S2 volatility adaptation are report-level controls. S1 can downgrade same-day new-buy candidates by planned amount; S2 only changes the remaining-position trailing-profit drawdown threshold.
- H1 total risk gate runs when total invested cost is at least 70% and below 85% of total capital. It requires both hard conditions to pass: total floating loss ratio below 10%, and A500/KC50 not both weak. It also requires at least two of three reference conditions: A500 valuation percentile pass, KC50 valuation percentile pass, and at least 10 trading days since the latest stop-loss/take-profit record.
- Manual valuation for H1 is read from config fields such as `valuation.source`, `valuation.as_of`, `valuation.a500_percentile`, and `valuation.kc50_percentile`. Percentiles at or below 50% pass; missing source/date/value does not pass.
- Reserve safety is evaluated from the `reserve` cash pool. Unrecovered reserve use over 2000 yuan or reserve cash below 1000 yuan triggers a warning and should block new reserve candidates.
- Before the first 588000 buy is triggered, the report shows an INFO reminder with the estimated first-buy trigger close, calculated from the latest 252 closing-price high and the first configured `buy_steps` drawdown.

## Operational Notes

- The web page does not actively notify users. It shows the latest encrypted report when opened and decrypted.
- GitHub Actions schedule is `20 8,10,12,14 * * 1-5`, which is 16:20, 18:20, 20:20, and 22:20 Asia/Shanghai on weekdays.
- Multiple weekday schedules are intentional because GitHub Actions schedules may be delayed; any successful run overwrites the Pages report, so rely on the report update time shown in the page.
- Report stale-market warnings compare the latest market bar to the expected market date, not simply to the current calendar date. Before 16:00 Asia/Shanghai and on weekends, the expected date falls back to the previous weekday; market holidays still require manual review.
- Trade submit/edit/delete triggers a new GitHub Actions run through Cloudflare Worker.
- If a trade is deleted directly from KV, Actions is not triggered automatically; manually run the workflow or push an empty commit to refresh Pages.
- If Worker reports a dispatch failure after saving a trade, do not resubmit blindly; the trade may already be stored. Check KV/trade management first.
- PushPlus/WeChat push was evaluated as a backup only. It is not currently implemented because it would only remind the user to open the encrypted page unless plaintext summaries are sent to a third party.

## Security Notes

- Report password is used only for local browser decryption and must not be sent to GitHub Pages.
- Trade submit password is sent only to Cloudflare Worker for validation and is not saved in KV.
- Full plaintext reports, portfolio details, and trade records should not be committed to the public repository.
- Public metadata such as page existence, file names, and update time may still be visible.
- Do not include broker account numbers, ID numbers, or other high-sensitivity data in reports or trade notes.
- If changing Worker upload/deploy logic, preserve existing secret bindings or re-check that `GITHUB_TOKEN`, `READ_TOKEN`, and `SUBMIT_PASSWORD` remain present.

## Development Guidance

- Keep changes small and aligned with the current simple architecture.
- Prefer existing Python modules and plain static HTML/JS over adding new frameworks.
- `docs/index.html` is a single static page with built-in tabs for report, trade submit, and trade management. Keep existing form element IDs, `data-tab-target`/`data-tab-panel`, and `__ENCRYPTED_REPORT_JSON__` / `__WORKER_CONFIG_JSON__` placeholders stable because Actions and page JavaScript depend on them.
- The trade submit UI uses segmented direction buttons backed by hidden `#trade-side`; keep submitted values exactly `买入` / `卖出`.
- Client-side price/amount/share checks are advisory only. Do not make them block submission unless the Worker validation contract changes too.
- Worker currently enforces KC50 buy compliance server-side: 100-share lots and per-step target amount. Non-compliant KC50 buys require a review note; with a note, Worker saves the factual trade and stores `compliance_warnings`.
- If remembering the trade submit password in the page, keep it limited to browser `sessionStorage`; never persist it into generated reports, local files, KV, or tracked code.
- Do not add automatic trading or broker integration.
- Do not move sensitive state into the public repo.
- When changing trade schema, update all three places together:
  - Worker normalization/storage
  - Pages submit/manage UI
  - Python trade parser/tests
- Keep trade audit fields optional for old records, but preserve them end-to-end when present. Risk-gate overrides should include a review note or risk snapshot rather than relying only on the free-form note.
- When changing report generation, run pytest and a local encrypted report generation command.
- When changing Worker routes, run `node --check cloudflare-worker\worker.js`, `node --test tests\worker.test.mjs`, and test `/health` plus the relevant endpoint after deployment.
- When changing strategy logic or tests, update `scripts/strategy_test_mapping.json`, run `node --test tests\strategy_report_generator.test.mjs`, and regenerate `test-reports/strategy-test-report.html` so customer-facing coverage feedback reflects current evidence.
- When implementing failed or uncovered strategy-test-report items, use `docs/product-requirements/strategy-test-report-prd.md` as the product scope and keep its rule-ID traceability aligned with `scripts/strategy_test_mapping.json`.
- When closing a strategy-test-report delivery pass, keep `docs/product-requirements/strategy-test-report-delivery.md` current and verify the generated HTML still reports `失败=0` and `未覆盖=0`.
