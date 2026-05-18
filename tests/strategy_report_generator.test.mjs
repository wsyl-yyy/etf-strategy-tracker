import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";

import {
  applyMappings,
  buildSummary,
  parseStrategyDocument,
  renderHtml,
} from "../scripts/generate_strategy_test_report.mjs";

test("strategy report generator parses markdown items and summarizes mapped statuses", () => {
  const markdown = [
    "# ETF量化交易策略",
    "",
    "版本：V3.2 正式执行版",
    "",
    "## 三、第一层：硬规则",
    "",
    "| 编号 | 硬规则 | 执行动作 |",
    "| --- | --- | --- |",
    "| H2 | 总投入成本达到总资金85%后 | 不得新增买入。 |",
    "",
    "- 科创50第4笔必须满足止跌过滤条件。",
  ].join("\n");

  const items = parseStrategyDocument(markdown);
  const assessed = applyMappings(items, {
    rules: [
      {
        id: "H2",
        status: "pass",
        match: { textIncludes: ["H2", "85%"] },
        evidence: ["策略层已有 85% 信号测试。"],
      },
      {
        id: "KC50-4",
        status: "fail",
        match: { textIncludes: ["第4笔", "过滤"] },
        evidence: ["当前仅提示人工确认，未自动校验过滤条件。"],
      },
    ],
    default: {
      status: "uncovered",
      evidence: ["未找到自动化证据。"],
    },
  });

  assert.equal(assessed.length, 3);
  assert.equal(assessed[1].status, "pass");
  assert.equal(assessed[2].status, "fail");

  const summary = buildSummary(assessed);
  assert.equal(summary.total, 3);
  assert.equal(summary.pass, 1);
  assert.equal(summary.fail, 1);
  assert.equal(summary.uncovered, 1);

  const html = renderHtml({
    title: "策略逐条测试反馈",
    sourcePath: "ETF量化交易策略 V3.2 正式执行版.md",
    generatedAt: "2026-05-18 12:00:00",
    items: assessed,
    commandResults: [],
  });

  assert.match(html, /策略逐条测试反馈/);
  assert.match(html, /总投入成本达到总资金85%后/);
  assert.match(html, /data-status="fail"/);
});

test("live strategy mapping has no failed or uncovered delivery items", () => {
  const strategyPath = readdirSync(".").find((name) => name.endsWith(".md") && name.includes("ETF"));
  assert.ok(strategyPath, "strategy markdown document should exist");

  const mapping = JSON.parse(readFileSync("scripts/strategy_test_mapping.json", "utf8"));
  const items = applyMappings(parseStrategyDocument(readFileSync(strategyPath, "utf8")), mapping);
  const summary = buildSummary(items);

  assert.equal(summary.fail, 0);
  assert.equal(summary.uncovered, 0);

  for (const item of items) {
    if (item.status === "pass" || item.status === "manual") {
      assert.ok(item.evidence.length > 0, `${item.id} ${item.status} item should include evidence`);
    }
  }
});

test("report shows an empty attention state when all items are archived", () => {
  const html = renderHtml({
    title: "Strategy report",
    sourcePath: "strategy.md",
    generatedAt: "2026-05-18 12:00:00",
    items: [
      {
        id: "T001",
        line: 1,
        type: "paragraph",
        majorSection: "Section",
        subsection: "",
        sectionPath: "Section",
        text: "covered behavior",
        original: "covered behavior",
        status: "pass",
        statusLabel: "通过",
        ruleId: "COVERED",
        evidence: ["tests/example.test.mjs covers this behavior."],
        note: "",
      },
      {
        id: "T002",
        line: 2,
        type: "paragraph",
        majorSection: "Section",
        subsection: "",
        sectionPath: "Section",
        text: "manual behavior",
        original: "manual behavior",
        status: "manual",
        statusLabel: "需人工复核/不适用",
        ruleId: "MANUAL",
        evidence: ["This depends on manual execution."],
        note: "",
      },
    ],
    commandResults: [],
  });

  assert.match(html, /暂无失败或未覆盖项/);
});
