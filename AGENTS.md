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

scripts/create_google_form.gs
  Legacy/backup Google Forms + Sheets helper. Current recommended flow is Cloudflare Worker + KV.

src/etf_tracker/
  Python package for config loading, market data, portfolio state, strategy evaluation, report rendering, encryption, and CLI.

tests/
  Pytest coverage for strategy behavior and trade payload parsing.
```

Important generated/local files:

- `config.json` is local/secret and ignored.
- `docs/report.json` is generated and ignored locally; Actions publishes the generated version to `gh-pages`.
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
- Market data loading order is AKShare Eastmoney ETF history, Eastmoney public kline, then AKShare Sina ETF history.
- Eastmoney market IDs are inferred from ETF code: `5/6/9` prefixes use Shanghai `1`, all others use Shenzhen `0`.
- Sina ETF history uses `sh`/`sz` prefixed symbols and has no traded amount; amount is stored as `0`.

## Operational Notes

- The web page does not actively notify users. It shows the latest encrypted report when opened and decrypted.
- GitHub Actions schedule is `10 8 * * 1-5`, which is 16:10 Asia/Shanghai on weekdays.
- GitHub Actions schedules may be delayed by GitHub; rely on the report update time shown in the page.
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
- Do not add automatic trading or broker integration.
- Do not move sensitive state into the public repo.
- When changing trade schema, update all three places together:
  - Worker normalization/storage
  - Pages submit/manage UI
  - Python trade parser/tests
- When changing report generation, run pytest and a local encrypted report generation command.
- When changing Worker routes, run `node --check cloudflare-worker\worker.js` and test `/health` plus the relevant endpoint after deployment.
