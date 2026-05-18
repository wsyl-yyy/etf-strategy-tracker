const TRADES_KEY = "trades";
const DEFAULT_REPO = "wsyl-yyy/etf-strategy-tracker";
const DEFAULT_ALLOWED_ORIGIN = "https://wsyl-yyy.github.io";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const tradeId = tradeIdFromPath(url.pathname);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(request, env) });
    }

    try {
      if (url.pathname === "/health" && request.method === "GET") {
        return jsonResponse(request, env, { ok: true });
      }
      if (url.pathname === "/trade" && request.method === "POST") {
        return await handleTradeSubmit(request, env);
      }
      if (url.pathname === "/trades" && request.method === "GET") {
        return await handleTradesRead(request, env);
      }
      if (url.pathname === "/trades/manage" && request.method === "POST") {
        return await handleTradesManage(request, env);
      }
      if (tradeId && request.method === "PUT") {
        return await handleTradeUpdate(request, env, tradeId);
      }
      if (tradeId && request.method === "DELETE") {
        return await handleTradeDelete(request, env, tradeId);
      }
      return jsonResponse(request, env, { ok: false, error: "Not found" }, 404);
    } catch (error) {
      return jsonResponse(request, env, { ok: false, error: error.message || "Worker error" }, error.status || 500);
    }
  },
};

async function handleTradeSubmit(request, env) {
  const body = await readSubmitBody(request, env);
  const trades = await readTrades(env);
  const trade = normalizeTrade(body);
  trades.push(trade);
  await writeTrades(env, trades);
  return dispatchResponse(request, env, "新增成交已保存", { ok: true, id: trade.id }, { action: "created", trade_id: trade.id });
}

async function handleTradesManage(request, env) {
  await readSubmitBody(request, env);
  return jsonResponse(request, env, { trades: await readTrades(env) }, 200, { "Cache-Control": "no-store" });
}

async function handleTradeUpdate(request, env, tradeId) {
  const body = await readSubmitBody(request, env);
  const trades = await readTrades(env);
  const index = trades.findIndex((trade) => trade.id === tradeId);
  if (index < 0) {
    return jsonResponse(request, env, { ok: false, error: "未找到这条成交。" }, 404);
  }
  trades[index] = {
    ...normalizeTrade(body),
    id: trades[index].id,
    created_at: trades[index].created_at,
    updated_at: new Date().toISOString(),
  };
  await writeTrades(env, trades);
  return dispatchResponse(request, env, "成交修改已保存", { ok: true, id: tradeId }, { action: "updated", trade_id: tradeId });
}

async function handleTradeDelete(request, env, tradeId) {
  await readSubmitBody(request, env);
  const trades = await readTrades(env);
  const nextTrades = trades.filter((trade) => trade.id !== tradeId);
  if (nextTrades.length === trades.length) {
    return jsonResponse(request, env, { ok: false, error: "未找到这条成交。" }, 404);
  }
  await writeTrades(env, nextTrades);
  return dispatchResponse(request, env, "成交已删除", { ok: true, id: tradeId }, { action: "deleted", trade_id: tradeId });
}

async function handleTradesRead(request, env) {
  ensureKv(env);
  const expected = `Bearer ${env.READ_TOKEN}`;
  if (!env.READ_TOKEN || request.headers.get("Authorization") !== expected) {
    return jsonResponse(request, env, { ok: false, error: "读取密钥错误。" }, 401);
  }
  return jsonResponse(request, env, { trades: await readTrades(env) }, 200, { "Cache-Control": "no-store" });
}

async function readSubmitBody(request, env) {
  if (!isAllowedOrigin(request, env)) {
    throw new HttpError("来源不允许提交。", 403);
  }
  ensureKv(env);
  const body = await request.json().catch(() => null);
  if (!body || body.submitPassword !== env.SUBMIT_PASSWORD) {
    throw new HttpError("提交密码错误。", 401);
  }
  return body;
}

async function readTrades(env) {
  ensureKv(env);
  const trades = (await env.TRADES.get(TRADES_KEY, "json")) || [];
  if (!Array.isArray(trades)) {
    throw new Error("KV 中的成交记录格式异常。");
  }
  return trades;
}

async function writeTrades(env, trades) {
  ensureKv(env);
  if (trades.length === 0) {
    await env.TRADES.delete(TRADES_KEY);
    return;
  }
  await env.TRADES.put(TRADES_KEY, JSON.stringify(trades));
}

async function dispatchResponse(request, env, successMessage, successBody, payload) {
  try {
    await triggerGithubDispatch(env, payload);
    return jsonResponse(request, env, { ...successBody, message: successMessage });
  } catch (error) {
    return jsonResponse(
      request,
      env,
      { ok: false, error: `${successMessage}，但日报刷新触发失败，请勿重复提交：${error.message}` },
      502
    );
  }
}

function normalizeTrade(body) {
  const side = String(body.side || "").trim();
  if (!["买入", "卖出"].includes(side)) {
    throw new HttpError("方向只能是买入或卖出。", 400);
  }

  const date = normalizeDate(body.date || body.execution_date || body.signal_date, "日期");
  const signalDate = normalizeOptionalDate(body.signal_date, "信号确认日") || date;
  const executionDate = normalizeOptionalDate(body.execution_date, "实际执行日") || date;
  const cashBalance = optionalFiniteNumber(body.cash_balance, "现金余额");

  if (!date) {
    throw new HttpError("日期格式应为 YYYY-MM-DD。", 400);
  }

  const symbol = normalizeSymbol(body.symbol);
  const price = requiredNumber(body.price, "成交价");
  const amount = requiredNumber(body.amount, "成交金额");
  const shares = requiredNumber(body.shares, "成交份额");
  const fee = optionalNumber(body.fee);
  const note = String(body.note || "").trim();
  const triggerRule = String(body.trigger_rule || "").trim();
  const module = String(body.module || "").trim();
  const complianceWarnings = complianceWarningsForTrade({ symbol, side, amount, shares, triggerRule, note });
  if (complianceWarnings.length > 0 && !note) {
    throw new HttpError("科创50买入偏离份额或目标金额时必须填写复盘备注。", 400);
  }

  return {
    id: crypto.randomUUID(),
    created_at: new Date().toISOString(),
    date,
    signal_date: signalDate,
    execution_date: executionDate,
    symbol,
    side,
    module,
    trigger_rule: triggerRule,
    price,
    amount,
    shares,
    fee,
    cash_balance: cashBalance,
    risk_gate_triggered: Boolean(body.risk_gate_triggered),
    risk_gate_snapshot: String(body.risk_gate_snapshot || "").trim(),
    compliance_warnings: complianceWarnings,
    note,
  };
}

function complianceWarningsForTrade({ symbol, side, amount, shares, triggerRule }) {
  if (symbol !== "588000" || side !== "买入") {
    return [];
  }

  const warnings = [];
  if (Math.abs(shares % 100) > 1e-9) {
    warnings.push("科创50买入份额不是100份整数倍。");
  }

  const target = kc50TargetAmount(triggerRule) || { index: 1, amount: 400 };
  if (target && amount > target.amount + 1e-9) {
    warnings.push(`科创50第${target.index}笔买入金额超过目标金额${target.amount}元。`);
  }
  return warnings;
}

function kc50TargetAmount(triggerRule) {
  const match = String(triggerRule || "").match(/第\s*([1-4一二三四])\s*笔/);
  if (!match) return null;
  const index = { 一: 1, 二: 2, 三: 3, 四: 4 }[match[1]] || Number(match[1]);
  const amounts = { 1: 400, 2: 500, 3: 500, 4: 600 };
  return amounts[index] ? { index, amount: amounts[index] } : null;
}

function normalizeDate(value, label) {
  const text = String(value || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    throw new HttpError(`${label}格式应为 YYYY-MM-DD。`, 400);
  }
  return text;
}

function normalizeOptionalDate(value, label) {
  if (value === undefined || value === null || value === "") return "";
  return normalizeDate(value, label);
}

function normalizeSymbol(value) {
  let symbol = String(value || "").trim();
  if (symbol.includes(".")) {
    symbol = symbol.split(".").pop();
  }
  symbol = symbol.padStart(6, "0");
  if (!/^\d{6}$/.test(symbol)) {
    throw new HttpError("标的代码应为 6 位数字。", 400);
  }
  return symbol;
}

function requiredNumber(value, label) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) {
    throw new HttpError(`${label}必须大于 0。`, 400);
  }
  return number;
}

function optionalNumber(value) {
  if (value === undefined || value === null || value === "") return 0;
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) {
    throw new HttpError("交易费用不能小于 0。", 400);
  }
  return number;
}

function optionalFiniteNumber(value, label) {
  if (value === undefined || value === null || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number)) {
    throw new HttpError(`${label}必须是有效数字。`, 400);
  }
  return number;
}

async function triggerGithubDispatch(env, payload) {
  if (!env.GITHUB_TOKEN) {
    throw new Error("GITHUB_TOKEN 未配置，无法触发日报刷新。");
  }

  const repo = env.GITHUB_REPO || DEFAULT_REPO;
  const response = await fetch(`https://api.github.com/repos/${repo}/dispatches`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "etf-strategy-worker",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({
      event_type: "trade-submitted",
      client_payload: payload,
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`GitHub Actions 触发失败：${response.status} ${text}`);
  }
}

function tradeIdFromPath(pathname) {
  if (!pathname.startsWith("/trade/")) return "";
  return decodeURIComponent(pathname.slice("/trade/".length));
}

function ensureKv(env) {
  if (!env.TRADES) {
    throw new Error("KV 绑定 TRADES 未配置。");
  }
}

function isAllowedOrigin(request, env) {
  const origin = request.headers.get("Origin");
  return !origin || origin === allowedOrigin(env);
}

function allowedOrigin(env) {
  return env.ALLOWED_ORIGIN || DEFAULT_ALLOWED_ORIGIN;
}

function corsHeaders(request, env) {
  const headers = {
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Max-Age": "86400",
  };
  const origin = request.headers.get("Origin");
  headers["Access-Control-Allow-Origin"] = origin && origin === allowedOrigin(env) ? origin : allowedOrigin(env);
  return headers;
}

function jsonResponse(request, env, data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...corsHeaders(request, env),
      ...extraHeaders,
    },
  });
}

class HttpError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}
