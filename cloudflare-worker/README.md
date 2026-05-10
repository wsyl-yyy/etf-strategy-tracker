# Cloudflare Worker 成交回填

这个 Worker 负责两件事：

- `POST /trade`：接收 Pages 页面提交的成交，写入 Cloudflare KV，并触发 GitHub Actions 刷新日报。
- `GET /trades`：给 GitHub Actions 读取全部成交记录。
- `POST /trades/manage`：输入提交密码后读取成交记录，供 Pages 页面管理使用。
- `PUT /trade/:id`：修改一条成交，并触发日报刷新。
- `DELETE /trade/:id`：删除一条成交，并触发日报刷新。

## 需要创建的资源

1. Cloudflare Worker。
2. Cloudflare KV namespace，并绑定到 Worker，绑定名必须是 `TRADES`。
3. GitHub fine-grained token，只授权 `wsyl-yyy/etf-strategy-tracker` 这个仓库，权限至少包含 `Contents: Read and write`，用于调用 `repository_dispatch`。

## Worker 环境变量和密钥

在 Cloudflare Worker 的 Settings 里添加：

| 名称 | 类型 | 说明 |
| --- | --- | --- |
| `SUBMIT_PASSWORD` | Secret | 手机页面提交成交时输入的密码。 |
| `READ_TOKEN` | Secret | GitHub Actions 读取成交记录用的密钥。 |
| `GITHUB_TOKEN` | Secret | GitHub fine-grained token。 |
| `ALLOWED_ORIGIN` | Variable | GitHub Pages 源，填 `https://wsyl-yyy.github.io`。 |
| `GITHUB_REPO` | Variable，可选 | 默认 `wsyl-yyy/etf-strategy-tracker`。 |

## GitHub Secrets

在仓库 `Settings -> Secrets and variables -> Actions` 添加：

| 名称 | 说明 |
| --- | --- |
| `WORKER_TRADES_URL` | Worker 读取地址，例如 `https://你的-worker.workers.dev/trades`。 |
| `WORKER_READ_TOKEN` | 与 Worker 里的 `READ_TOKEN` 保持一致。 |

部署完成后，下一次 GitHub Actions 会把 `/trade` 提交地址写入 Pages 页面。

## 验证

1. 打开 Pages 日报页，确认“成交回填”不再显示未配置。
2. 填一条小额测试成交并提交。
3. 到 GitHub Actions 查看是否出现新的 `trade-submitted` 运行。
4. 等 Pages 刷新后，用日报密码解密，确认成交进入持仓计算。

如果页面提示“已保存成交，但触发日报失败”一类错误，优先检查 Worker 的 `GITHUB_TOKEN` 权限和仓库名。
