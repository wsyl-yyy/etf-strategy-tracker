# ETF Strategy Tracker

用于把 `ETF量化交易策略 V3.2 正式执行版.md` 落地为云端每日追踪系统：

- GitHub Actions 在收盘后或成交提交后运行。
- 读取 ETF 行情和手机回填的成交记录。
- 生成加密后的策略日报。
- GitHub Pages 只托管解密页面和密文，正文需要访问密码在浏览器本地解密。

本工具只做记录、复盘和提醒，不连接券商，也不会自动下单。

## 目录结构

```text
.github/workflows/daily-report.yml  GitHub Actions 定时任务
cloudflare-worker/                  成交回填 Worker
config.example.json                 策略配置模板
data/sample_trades.csv              本地成交记录样例
docs/index.html                     GitHub Pages 解密和回填页面
requirements.txt                    Python 依赖
scripts/create_google_form.gs       Google 表单备选创建脚本
src/etf_tracker/                    策略追踪代码
tests/                              测试
```

## 推荐方案

现在推荐使用 `GitHub Pages + Cloudflare Worker + KV`：

- 手机在同一个 Pages 日报页里查看日报和提交成交。
- 输入提交密码后，可以在页面里加载、修改、删除已有成交。
- 成交记录保存在 Cloudflare KV，不进入公开仓库。
- Worker 收到成交后触发 GitHub Actions，日报会自动刷新。
- 本机不需要 24 小时在线。

Google 表单和 Sheets 仍保留为备选回退方案。

## GitHub Secrets

在仓库 `Settings -> Secrets and variables -> Actions` 添加：

| Secret | 是否必需 | 说明 |
| --- | --- | --- |
| `REPORT_PASSWORD` | 是 | 手机查看日报时输入的解密密码。 |
| `CONFIG_JSON` | 建议 | 策略配置全文，避免公开仓库暴露网格参数。 |
| `WORKER_TRADES_URL` | Worker 方案必需 | Worker 读取地址，例如 `https://你的-worker.workers.dev/trades`。 |
| `WORKER_READ_TOKEN` | Worker 方案必需 | 与 Cloudflare Worker 的 `READ_TOKEN` 保持一致。 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google 备选 | Google 服务账号 JSON。 |
| `GOOGLE_SHEET_ID` | Google 备选 | Google Sheets 文件 ID。 |
| `GOOGLE_SHEET_RANGE` | 否 | 默认 `Form Responses 1!A:Z`。 |

## Cloudflare Worker

Worker 代码在 `cloudflare-worker/`。部署时需要：

1. 创建 Cloudflare Worker。
2. 创建 KV namespace，并绑定到 Worker，绑定名为 `TRADES`。
3. 在 Worker 设置：
   - `SUBMIT_PASSWORD`：手机提交成交时输入。
   - `READ_TOKEN`：GitHub Actions 读取成交记录用。
   - `GITHUB_TOKEN`：GitHub fine-grained token，用于触发 `repository_dispatch`。
   - `ALLOWED_ORIGIN`：填 `https://wsyl-yyy.github.io`。
4. 在 GitHub Secrets 设置 `WORKER_TRADES_URL` 和 `WORKER_READ_TOKEN`。

更细的部署说明见 `cloudflare-worker/README.md`。

## GitHub Pages

在仓库 `Settings -> Pages` 中选择：

- Source: `Deploy from a branch`
- Branch: `gh-pages` / root

日报发布后，用手机打开 Pages 地址，输入 `REPORT_PASSWORD` 对应密码即可查看。页面本身可能被他人访问；正文已加密，但页面存在、更新时间等元信息仍可能可见。不要在报告里放券商账号、身份证号等高度敏感信息。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
$env:PYTHONPATH='src'
$env:REPORT_PASSWORD='local-test-password'
.\.venv\Scripts\python -m etf_tracker.cli --config config.json --trades data/sample_trades.csv --out docs/report.json
```

运行测试：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest
```

## 成交字段

页面和数据读取都使用这些字段：

```csv
日期,标的,方向,策略模块,成交价,成交金额,成交份额,交易费用,备注
2026-05-11,563360,买入,A500网格,1.032,600,500,0.06,A500补仓测试
```

## 安全边界

- 日报密码只用于浏览器本地解密，不提交到 GitHub Pages。
- 成交提交密码只发给 Cloudflare Worker 校验，不保存到 KV。
- GitHub Secrets 和 Worker Secrets 不会写入公开 Pages。
- 行情、估值源失败时，报告输出“数据不足/人工复核”，不会强行给出交易动作。
