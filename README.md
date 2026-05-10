# ETF Strategy Tracker

用于将 `ETF量化交易策略 V3.2 正式执行版.md` 落地为云端每日追踪系统：

- GitHub Actions 每个交易日收盘后运行。
- 读取 ETF 行情和手机回填的成交记录。
- 生成加密后的策略日报。
- GitHub Pages 只托管解密页面和密文，正文需要访问密码在浏览器本地解密。

本工具只做记录、复盘和提醒，不连接券商，也不会自动下单。

## 目录结构

```text
.github/workflows/daily-report.yml  GitHub Actions 定时任务
config.example.json                 策略配置模板
data/sample_trades.csv              成交记录样例
docs/index.html                     GitHub Pages 解密页面
requirements.txt                    Python 依赖
src/etf_tracker/                    策略追踪代码
tests/                              样例测试
```

## 首次设置

1. 在 GitHub 新建仓库，例如 `etf-strategy-tracker`。
2. 将本目录文件推送到该仓库。
3. 复制 `config.example.json` 为 `config.json`，按实际情况填写：
   - A500 网格基准价、网格上下沿。
   - 估值数据源说明。
   - Google Sheets 表头字段。
4. 创建 Google 表单并绑定 Google Sheets，建议字段：
   - 日期
   - 标的
   - 方向
   - 策略模块
   - 成交价
   - 成交金额
   - 成交份额
   - 交易费用
   - 备注
5. 创建 Google Cloud 服务账号，将 Google Sheets 只读权限授权给服务账号邮箱。
6. 在 GitHub 仓库 `Settings -> Secrets and variables -> Actions` 添加：
   - `REPORT_PASSWORD`：日报解密密码。
   - `CONFIG_JSON`：完整策略配置 JSON。可先复制 `config.example.json` 内容再填写参数。
   - `GOOGLE_SERVICE_ACCOUNT_JSON`：服务账号 JSON 全文。
   - `GOOGLE_SHEET_ID`：成交记录表格 ID。
   - 可选 `GOOGLE_SHEET_RANGE`：默认 `Form Responses 1!A:Z`。

## GitHub Pages

在仓库 `Settings -> Pages` 中选择：

- Source: `Deploy from a branch`
- Branch: `gh-pages` / root

日报发布后，用手机打开 Pages 地址，输入 `REPORT_PASSWORD` 对应密码即可查看。

注意：GitHub Pages 页面本身可能被他人访问；正文已加密，但页面存在、更新时间等元信息仍可能可见。不要在报告里放券商账号、身份证号等高度敏感信息。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
$env:PYTHONPATH='src'
$env:REPORT_PASSWORD='local-test-password'
.\.venv\Scripts\python -m etf_tracker.cli --config config.json --trades data/sample_trades.csv --out docs/report.json
```

如果没有配置 Google 凭据，脚本会读取 `--trades` 指定的本地 CSV。

运行测试：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python -m pytest
```

## 推荐的 GitHub Secrets

| Secret | 是否必需 | 说明 |
| --- | --- | --- |
| `REPORT_PASSWORD` | 是 | 手机打开日报时输入的密码。 |
| `CONFIG_JSON` | 建议 | 策略配置全文，避免在公开仓库暴露网格参数。 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 是 | Google 服务账号凭据 JSON。 |
| `GOOGLE_SHEET_ID` | 是 | Google Sheets 文件 ID。 |
| `GOOGLE_SHEET_RANGE` | 否 | 默认 `Form Responses 1!A:Z`。 |

## 手机成交回填格式

推荐使用 Google 表单。可以复制 `scripts/create_google_form.gs` 到 Google Apps Script 中运行一次，它会自动创建表单和绑定表格，并在日志里输出：

- Form public URL：手机提交成交记录用。
- Sheet URL：查看成交记录用。
- GOOGLE_SHEET_ID：填入 GitHub Secret。

如果暂时不用 Google 表单，也可以按 CSV 表头手工维护：

```csv
日期,标的,方向,策略模块,成交价,成交金额,成交份额,交易费用,备注
2026-05-11,563360,买入,A500常规网格,1.032,600,500,0.06,A500第1格补仓
```

## 安全边界

- 密码只用于浏览器本地解密，不会提交到 GitHub Pages。
- GitHub Secrets 不会写入报告文件。
- 行情、估值源失败时，报告输出“数据不足/人工复核”，不会强行给交易动作。
