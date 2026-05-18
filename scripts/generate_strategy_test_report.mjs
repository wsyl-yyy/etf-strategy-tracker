import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const STATUS = {
  pass: { label: "通过", className: "pass" },
  fail: { label: "失败", className: "fail" },
  uncovered: { label: "未覆盖", className: "uncovered" },
  manual: { label: "需人工复核/不适用", className: "manual" },
  blocked: { label: "环境阻塞", className: "blocked" },
};

const DEFAULT_MAPPING = {
  default: {
    status: "uncovered",
    evidence: ["未找到明确实现和自动化测试证据。"],
  },
  rules: [],
  sectionDefaults: [],
  commandResults: [],
};

export function parseStrategyDocument(markdown) {
  const lines = markdown.split(/\r?\n/);
  const items = [];
  let majorSection = "文档元信息";
  let subsection = "";
  let paragraph = [];

  const addItem = (type, text, lineNumber) => {
    const cleaned = normalizeText(text);
    if (!cleaned) return;
    items.push({
      id: `T${String(items.length + 1).padStart(3, "0")}`,
      line: lineNumber,
      type,
      majorSection,
      subsection,
      sectionPath: [majorSection, subsection].filter(Boolean).join(" / "),
      text: cleaned,
      original: text.trim(),
    });
  };

  const flushParagraph = (lineNumber) => {
    if (!paragraph.length) return;
    addItem("paragraph", paragraph.join(" "), lineNumber - paragraph.length);
    paragraph = [];
  };

  for (let index = 0; index < lines.length; index += 1) {
    const rawLine = lines[index];
    const line = rawLine.trim();
    const lineNumber = index + 1;

    if (!line) {
      flushParagraph(lineNumber);
      continue;
    }

    if (/^##\s+/.test(line)) {
      flushParagraph(lineNumber);
      if (/^###\s+/.test(line)) {
        subsection = line.replace(/^###\s+/, "").trim();
      } else {
        majorSection = line.replace(/^##\s+/, "").trim();
        subsection = "";
      }
      continue;
    }

    if (/^#\s+/.test(line)) {
      flushParagraph(lineNumber);
      majorSection = "文档元信息";
      subsection = "";
      continue;
    }

    if (isTableRow(line)) {
      flushParagraph(lineNumber);
      if (isTableSeparator(line) || isTableHeader(lines, index)) continue;
      addItem("table-row", parseTableCells(line).join(" | "), lineNumber);
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      flushParagraph(lineNumber);
      addItem("bullet", line.replace(/^[-*]\s+/, ""), lineNumber);
      continue;
    }

    paragraph.push(line);
  }

  flushParagraph(lines.length);
  return items;
}

export function applyMappings(items, mapping = DEFAULT_MAPPING) {
  const normalizedMapping = { ...DEFAULT_MAPPING, ...mapping };
  return items.map((item) => {
    const rule = (normalizedMapping.rules || []).find((candidate) => matches(item, candidate.match || {}));
    const sectionDefault = (normalizedMapping.sectionDefaults || []).find((candidate) =>
      matches(item, candidate.match || {})
    );
    const result = rule || sectionDefault || normalizedMapping.default || DEFAULT_MAPPING.default;
    const status = normalizeStatus(result.status);

    return {
      ...item,
      status,
      statusLabel: STATUS[status].label,
      ruleId: result.id || "",
      evidence: asArray(result.evidence),
      note: result.note || "",
    };
  });
}

export function buildSummary(items) {
  const summary = { total: items.length, pass: 0, fail: 0, uncovered: 0, manual: 0, blocked: 0 };
  for (const item of items) {
    summary[item.status] = (summary[item.status] || 0) + 1;
  }
  return summary;
}

export function renderHtml({ title, sourcePath, generatedAt, items, commandResults = [] }) {
  const summary = buildSummary(items);
  const sections = [...new Set(items.map((item) => item.majorSection))];
  const attentionItems = items.filter((item) => item.status === "fail" || item.status === "uncovered");
  const attentionListHtml = attentionItems.length
    ? attentionItems
        .map(
          (item) =>
            `<li><strong>${escapeHtml(item.statusLabel)}</strong> · ${escapeHtml(item.sectionPath)} · ${escapeHtml(item.text)}</li>`
        )
        .join("\n")
    : `<li>暂无失败或未覆盖项。</li>`;
  const titleText = title || "策略逐条测试反馈";

  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${escapeHtml(titleText)}</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #f4f6f8;
        --panel: #ffffff;
        --text: #17202a;
        --muted: #65717f;
        --line: #d9e1ea;
        --pass: #047857;
        --fail: #b91c1c;
        --uncovered: #b45309;
        --manual: #2563eb;
        --blocked: #6b7280;
        --soft-pass: #ecfdf5;
        --soft-fail: #fef2f2;
        --soft-uncovered: #fffbeb;
        --soft-manual: #eff6ff;
        --soft-blocked: #f3f4f6;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background: var(--bg);
        color: var(--text);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        line-height: 1.55;
      }
      main {
        max-width: 1180px;
        margin: 0 auto;
        padding: 28px 16px 52px;
      }
      header, section {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 18px;
        margin-bottom: 14px;
      }
      h1, h2, h3 { margin: 0; line-height: 1.25; }
      h1 { font-size: 28px; }
      h2 { font-size: 20px; margin-bottom: 12px; }
      h3 { font-size: 17px; margin: 18px 0 10px; }
      p { margin: 8px 0 0; }
      .muted { color: var(--muted); font-size: 14px; }
      .summary-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 10px;
      }
      .summary-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 13px;
      }
      .summary-card strong { display: block; font-size: 26px; }
      .filters {
        display: grid;
        grid-template-columns: minmax(200px, 1fr) minmax(220px, 1fr);
        gap: 12px;
      }
      select, input {
        width: 100%;
        border: 1px solid var(--line);
        border-radius: 7px;
        color: var(--text);
        font: inherit;
        padding: 10px 11px;
      }
      .status-tabs {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 12px;
      }
      .status-tabs button {
        border: 1px solid var(--line);
        border-radius: 999px;
        background: #fff;
        color: var(--text);
        cursor: pointer;
        font: inherit;
        padding: 8px 12px;
      }
      .status-tabs button[aria-pressed="true"] {
        border-color: #0f766e;
        background: #0f766e;
        color: #fff;
      }
      .command-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 10px;
      }
      .command-card, .item-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fff;
        padding: 12px;
      }
      .command-card { background: #fbfcfd; }
      .command-card code, .evidence code {
        white-space: normal;
        overflow-wrap: anywhere;
      }
      .section-title {
        border-top: 1px solid var(--line);
        color: var(--muted);
        font-size: 13px;
        font-weight: 750;
        margin-top: 18px;
        padding-top: 16px;
      }
      .item-list {
        display: grid;
        gap: 10px;
      }
      .item-card {
        display: grid;
        grid-template-columns: 168px minmax(0, 1fr);
        gap: 12px;
      }
      .item-meta {
        color: var(--muted);
        font-size: 13px;
      }
      .item-text {
        font-size: 15px;
        overflow-wrap: anywhere;
      }
      .badge {
        display: inline-flex;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 800;
        line-height: 1;
        margin-bottom: 8px;
        padding: 6px 8px;
      }
      .badge.pass { background: var(--soft-pass); color: var(--pass); }
      .badge.fail { background: var(--soft-fail); color: var(--fail); }
      .badge.uncovered { background: var(--soft-uncovered); color: var(--uncovered); }
      .badge.manual { background: var(--soft-manual); color: var(--manual); }
      .badge.blocked { background: var(--soft-blocked); color: var(--blocked); }
      .evidence {
        color: var(--muted);
        font-size: 13px;
        margin: 8px 0 0;
        padding-left: 18px;
      }
      .note {
        border-left: 3px solid var(--line);
        color: var(--muted);
        font-size: 13px;
        margin-top: 8px;
        padding-left: 10px;
      }
      .attention-list {
        display: grid;
        gap: 8px;
        margin: 0;
        padding-left: 18px;
      }
      .attention-list li { padding-left: 2px; }
      .hidden { display: none !important; }
      @media (max-width: 720px) {
        .filters, .item-card {
          grid-template-columns: 1fr;
        }
      }
    </style>
  </head>
  <body>
    <main>
      <header>
        <h1>${escapeHtml(titleText)}</h1>
        <p class="muted">来源：${escapeHtml(sourcePath)} · 生成时间：${escapeHtml(generatedAt)}</p>
        <p class="muted">判定口径：通过=已有实现和自动化测试；失败=实现缺失或与策略不一致；未覆盖=缺少足够测试证据；需人工复核/不适用=策略明确依赖人工确认或外部材料。</p>
      </header>

      <section>
        <h2>汇总页</h2>
        <div class="summary-grid">
          ${summaryCard("全部条目", summary.total, "blocked")}
          ${summaryCard("通过", summary.pass, "pass")}
          ${summaryCard("失败", summary.fail, "fail")}
          ${summaryCard("未覆盖", summary.uncovered, "uncovered")}
          ${summaryCard("需人工复核/不适用", summary.manual, "manual")}
        </div>
      </section>

      <section>
        <h2>测试执行基线</h2>
        <div class="command-grid">
          ${commandResults.map(renderCommandCard).join("\n")}
        </div>
      </section>

      <section>
        <h2>失败与未覆盖清单</h2>
        <ul class="attention-list">
          ${attentionListHtml}
        </ul>
      </section>

      <section>
        <h2>策略逐条测试报告</h2>
        <div class="filters">
          <label>
            <span class="muted">章节筛选</span>
            <select id="section-filter">
              <option value="">全部章节</option>
              ${sections.map((section) => `<option value="${escapeHtml(section)}">${escapeHtml(section)}</option>`).join("\n")}
            </select>
          </label>
          <label>
            <span class="muted">关键词搜索</span>
            <input id="search-filter" type="search" placeholder="输入规则编号、关键词或证据" />
          </label>
        </div>
        <div class="status-tabs" aria-label="测试状态筛选">
          ${statusButton("", "全部", true)}
          ${statusButton("pass", "通过", false)}
          ${statusButton("fail", "失败", false)}
          ${statusButton("uncovered", "未覆盖", false)}
          ${statusButton("manual", "需人工复核/不适用", false)}
        </div>
        <div class="item-list" id="item-list">
          ${renderGroupedItems(items)}
        </div>
      </section>
    </main>
    <script>
      const sectionFilter = document.getElementById("section-filter");
      const searchFilter = document.getElementById("search-filter");
      const statusButtons = Array.from(document.querySelectorAll("[data-status-filter]"));
      const cards = Array.from(document.querySelectorAll("[data-item-card]"));
      let currentStatus = "";

      function applyFilters() {
        const section = sectionFilter.value;
        const query = searchFilter.value.trim().toLowerCase();
        for (const card of cards) {
          const sectionMatches = !section || card.dataset.section === section;
          const statusMatches = !currentStatus || card.dataset.status === currentStatus;
          const queryMatches = !query || card.textContent.toLowerCase().includes(query);
          card.classList.toggle("hidden", !(sectionMatches && statusMatches && queryMatches));
        }
      }

      sectionFilter.addEventListener("change", applyFilters);
      searchFilter.addEventListener("input", applyFilters);
      statusButtons.forEach((button) => {
        button.addEventListener("click", () => {
          currentStatus = button.dataset.statusFilter;
          statusButtons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
          applyFilters();
        });
      });
    </script>
  </body>
</html>
`;
}

export function generateReport({
  strategyPath = "ETF量化交易策略 V3.2 正式执行版.md",
  mappingPath = "scripts/strategy_test_mapping.json",
  outPath = "test-reports/strategy-test-report.html",
  generatedAt = localTimestamp(),
} = {}) {
  const markdown = readFileSync(strategyPath, "utf8");
  const mapping = JSON.parse(readFileSync(mappingPath, "utf8"));
  const items = applyMappings(parseStrategyDocument(markdown), mapping);
  const html = renderHtml({
    title: mapping.title || "策略逐条测试反馈",
    sourcePath: strategyPath,
    generatedAt,
    items,
    commandResults: mapping.commandResults || [],
  });

  mkdirSync(path.dirname(outPath), { recursive: true });
  writeFileSync(outPath, html, "utf8");
  return { outPath, summary: buildSummary(items) };
}

function renderGroupedItems(items) {
  let currentSection = "";
  const parts = [];
  for (const item of items) {
    if (item.majorSection !== currentSection) {
      currentSection = item.majorSection;
      parts.push(`<div class="section-title">${escapeHtml(currentSection)}</div>`);
    }
    parts.push(renderItemCard(item));
  }
  return parts.join("\n");
}

function renderItemCard(item) {
  const evidence = item.evidence.length
    ? `<ul class="evidence">${item.evidence.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>`
    : "";
  const note = item.note ? `<div class="note">${escapeHtml(item.note)}</div>` : "";
  return `<article class="item-card" data-item-card data-section="${escapeHtml(item.majorSection)}" data-status="${escapeHtml(item.status)}">
    <div class="item-meta">
      <span class="badge ${escapeHtml(STATUS[item.status].className)}">${escapeHtml(item.statusLabel)}</span>
      <div>${escapeHtml(item.id)} · 第 ${escapeHtml(String(item.line))} 行</div>
      <div>${escapeHtml(item.type)}</div>
      ${item.subsection ? `<div>${escapeHtml(item.subsection)}</div>` : ""}
      ${item.ruleId ? `<div>映射：${escapeHtml(item.ruleId)}</div>` : ""}
    </div>
    <div>
      <div class="item-text">${escapeHtml(item.text)}</div>
      ${evidence}
      ${note}
    </div>
  </article>`;
}

function renderCommandCard(result) {
  const status = normalizeStatus(result.status);
  return `<div class="command-card">
    <span class="badge ${escapeHtml(STATUS[status].className)}">${escapeHtml(STATUS[status].label)}</span>
    <h3>${escapeHtml(result.name || "检查项")}</h3>
    <p><code>${escapeHtml(result.command || "")}</code></p>
    <p class="muted">${escapeHtml(result.detail || "")}</p>
  </div>`;
}

function summaryCard(label, value, status) {
  return `<div class="summary-card"><span class="badge ${escapeHtml(status)}">${escapeHtml(label)}</span><strong>${escapeHtml(
    String(value)
  )}</strong></div>`;
}

function statusButton(value, label, pressed) {
  return `<button type="button" data-status-filter="${escapeHtml(value)}" aria-pressed="${pressed}">${escapeHtml(label)}</button>`;
}

function matches(item, criteria) {
  const haystack = `${item.sectionPath} ${item.text}`.toLowerCase();
  const section = item.sectionPath.toLowerCase();

  if (criteria.type && item.type !== criteria.type) return false;
  if (criteria.sectionIncludes && !asArray(criteria.sectionIncludes).every((value) => section.includes(String(value).toLowerCase()))) {
    return false;
  }
  if (criteria.textIncludes && !asArray(criteria.textIncludes).every((value) => haystack.includes(String(value).toLowerCase()))) {
    return false;
  }
  if (criteria.textAny && !asArray(criteria.textAny).some((value) => haystack.includes(String(value).toLowerCase()))) {
    return false;
  }
  return true;
}

function normalizeStatus(status) {
  return STATUS[status] ? status : "uncovered";
}

function normalizeText(text) {
  return text
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function parseTableCells(line) {
  return line
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => normalizeText(cell));
}

function isTableRow(line) {
  return line.startsWith("|") && line.endsWith("|");
}

function isTableSeparator(line) {
  return /^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(line);
}

function isTableHeader(lines, index) {
  const next = (lines[index + 1] || "").trim();
  return isTableSeparator(next);
}

function asArray(value) {
  if (value === undefined || value === null) return [];
  return Array.isArray(value) ? value : [value];
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function localTimestamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(
    now.getMinutes()
  )}:${pad(now.getSeconds())}`;
}

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (key === "--strategy") {
      args.strategyPath = value;
      index += 1;
    } else if (key === "--mapping") {
      args.mappingPath = value;
      index += 1;
    } else if (key === "--out") {
      args.outPath = value;
      index += 1;
    }
  }
  return args;
}

const currentFile = fileURLToPath(import.meta.url);
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === pathToFileURL(currentFile).href) {
  const result = generateReport(parseArgs(process.argv.slice(2)));
  console.log(`Generated ${result.outPath}`);
  console.log(JSON.stringify(result.summary));
}
