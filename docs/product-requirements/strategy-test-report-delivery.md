# 策略修复交付说明

日期：2026-05-18

## 交付状态

- 策略评估逻辑、成交记录链路、自动化测试、`scripts/strategy_test_mapping.json` 和 `test-reports/strategy-test-report.html` 已按 `docs/product-requirements/strategy-test-report-prd.md` 补齐。
- 最新策略测试反馈报告摘要：全部条目 298，通过 212，失败 0，未覆盖 0，需人工复核/不适用 86。
- 本系统仍只用于策略记录、复盘和提醒，不连接券商，不自动下单。

## 策略评估逻辑

- 会计口径改为投入成本不含交易费，交易费单独记录并影响现金；总浮亏比例按非负口径展示。
- 资金池保持 A500 常规网格、科创50波段、全局备用金独立归属，备用金安全垫和未回收动用会阻断相关候选。
- H1/H2 总风险闸门、双标的趋势、估值分位、止盈/止损冷却期、85%绝对上限均进入策略候选过滤。
- A500 支持实际网格基准、默认 5.5% 间距、普通网格买卖配对、底仓趋势止盈、单边保护和备用金人工候选。
- 科创50支持买入阶梯、估值交叉验证、第4笔过滤、固定/移动止盈、H3净值规则、分级风控、时间复核和H3后备用金回暖候选。
- 生命周期与长期风控覆盖清仓后5日冷却、试探仓重启、连续3笔10日浮亏暂停、12%最大回撤暂停、收益锁定和6个月收益对比复核。

## 成交记录链路

- `Trade` 模型和 Python 解析器支持 `signal_date`、`execution_date`、`trigger_rule`、`cash_balance`、`risk_gate_triggered`、`risk_gate_snapshot`、`compliance_warnings`。
- 旧记录仍兼容 `date`；缺少新日期字段时，`date` 同时作为信号确认日和实际执行日。
- Worker normalization 保存新增审计字段，校验日期和现金余额，并对科创50买入的100份整数倍和目标金额偏差生成合规警告；无复核备注时阻断不合规买入。
- 页面提交和管理表单已能维护新增字段，管理列表展示触发规则、风险闸门快照和合规警告。

## 自动化测试与报告

- `C:\Users\berna\AppData\Local\Programs\Python\Python311\python.exe -m pytest`：65 个 Python 测试通过。
- `node --check cloudflare-worker\worker.js`：Worker 语法检查通过。
- `node --test tests\worker.test.mjs`：7 个 Worker API 测试通过。
- `node --test tests\strategy_report_generator.test.mjs`：3 个报告生成器测试通过，包含真实策略文档失败/未覆盖归零门禁。
- `node scripts\generate_strategy_test_report.mjs` 已重新生成 `test-reports/strategy-test-report.html`。

## 仍保留人工复核

- 季度复核：网格上下沿、网格间距、机动资金调整和策略参数继续由人工确认。
- 估值平台/估值截图：估值来源、截图存档、查询时点和估值分位录入仍需人工维护。
- A500 备用金候选：系统只提示 A/B/C 条件、间隔、安全垫和仓位盈亏，不自动执行备用金买入或卖出。
- KC50 备用金回暖确认：H3恢复后是否启用一份备用金仍依赖人工 `reserve.kc50_recovery.confirmed`。
- 策略6个月有效性判断：系统展示账户收益、沪深300和货币基金对比，不自动判定策略失效。
- 跨池例外和不可交易现金池调拨：只作为复核事项展示，不静默发生。
- 券商实际执行：下单、成交截图、异常滑点和流动性判断均在系统外人工完成。
