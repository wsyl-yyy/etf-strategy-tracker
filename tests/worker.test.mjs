import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const workerSource = await readFile(new URL("../cloudflare-worker/worker.js", import.meta.url), "utf8");
const worker = new Function(`${workerSource.replace("export default", "const worker =")}; return worker;`)();

test("worker returns json errors for submit validation failures", async () => {
  const env = makeEnv();

  const wrongPassword = await callWorker(env, "/trade", {
    method: "POST",
    body: validTrade({ submitPassword: "wrong" }),
  });
  assert.equal(wrongPassword.status, 401);
  assert.match(wrongPassword.headers.get("content-type"), /application\/json/);
  assert.equal(wrongPassword.json.ok, false);

  const badOrigin = await callWorker(env, "/trade", {
    method: "POST",
    origin: "https://example.invalid",
    body: validTrade(),
  });
  assert.equal(badOrigin.status, 403);
  assert.equal(badOrigin.json.ok, false);

  const badSymbol = await callWorker(env, "/trade", {
    method: "POST",
    body: validTrade({ symbol: "abc" }),
  });
  assert.equal(badSymbol.status, 400);
  assert.equal(badSymbol.json.ok, false);

  const badDate = await callWorker(env, "/trade", {
    method: "POST",
    body: validTrade({ date: "2026/05/17" }),
  });
  assert.equal(badDate.status, 400);
  assert.equal(badDate.json.ok, false);

  const badAmount = await callWorker(env, "/trade", {
    method: "POST",
    body: validTrade({ amount: 0 }),
  });
  assert.equal(badAmount.status, 400);
  assert.equal(badAmount.json.ok, false);

  const badFee = await callWorker(env, "/trade", {
    method: "POST",
    body: validTrade({ fee: -1 }),
  });
  assert.equal(badFee.status, 400);
  assert.equal(badFee.json.ok, false);
});

test("worker supports trade create, manage, update, delete flow", async () => {
  const env = makeEnv();
  const created = await callWorker(env, "/trade", { method: "POST", body: validTrade() });

  assert.equal(created.status, 200);
  assert.equal(created.json.ok, true);
  assert.equal(env.TRADES.dump().length, 1);
  assert.equal(env.dispatches.length, 1);

  const tradeId = created.json.id;
  const managed = await callWorker(env, "/trades/manage", {
    method: "POST",
    body: { submitPassword: "pw" },
  });
  assert.equal(managed.status, 200);
  assert.equal(managed.json.trades.length, 1);
  assert.equal(managed.headers.get("cache-control"), "no-store");

  const updated = await callWorker(env, `/trade/${tradeId}`, {
    method: "PUT",
    body: validTrade({ note: "CODEx_TEST_updated" }),
  });
  assert.equal(updated.status, 200);
  assert.equal(env.TRADES.dump()[0].note, "CODEx_TEST_updated");

  const deleted = await callWorker(env, `/trade/${tradeId}`, {
    method: "DELETE",
    body: { submitPassword: "pw" },
  });
  assert.equal(deleted.status, 200);
  assert.equal(env.TRADES.dump().length, 0);
  assert.deepEqual(
    env.dispatches.map((item) => item.client_payload.action),
    ["created", "updated", "deleted"],
  );
});

test("worker preserves new audit fields and backfills old date fields", async () => {
  const env = makeEnv();
  const created = await callWorker(env, "/trade", {
    method: "POST",
    body: validTrade({
      date: "2026-05-17",
      signal_date: "2026-05-16",
      execution_date: "2026-05-17",
      trigger_rule: "A500第2格补仓",
      cash_balance: 4380,
      risk_gate_triggered: true,
      risk_gate_snapshot: "H1: allow=false",
    }),
  });

  assert.equal(created.status, 200);
  const trade = env.TRADES.dump()[0];
  assert.equal(trade.date, "2026-05-17");
  assert.equal(trade.signal_date, "2026-05-16");
  assert.equal(trade.execution_date, "2026-05-17");
  assert.equal(trade.trigger_rule, "A500第2格补仓");
  assert.equal(trade.cash_balance, 4380);
  assert.equal(trade.risk_gate_triggered, true);
  assert.equal(trade.risk_gate_snapshot, "H1: allow=false");

  const updated = await callWorker(env, `/trade/${created.json.id}`, {
    method: "PUT",
    body: validTrade({ date: "2026-05-18", trigger_rule: "A500第3格补仓" }),
  });
  assert.equal(updated.status, 200);
  const updatedTrade = env.TRADES.dump()[0];
  assert.equal(updatedTrade.signal_date, "2026-05-18");
  assert.equal(updatedTrade.execution_date, "2026-05-18");
  assert.equal(updatedTrade.trigger_rule, "A500第3格补仓");
});

test("worker validates optional audit dates and cash balance", async () => {
  const badSignalDate = await callWorker(makeEnv(), "/trade", {
    method: "POST",
    body: validTrade({ signal_date: "2026/05/17" }),
  });
  assert.equal(badSignalDate.status, 400);

  const badCashBalance = await callWorker(makeEnv(), "/trade", {
    method: "POST",
    body: validTrade({ cash_balance: "not-a-number" }),
  });
  assert.equal(badCashBalance.status, 400);
});

test("worker flags invalid kc50 buy lot and target amount with required review note", async () => {
  const missingNote = await callWorker(makeEnv(), "/trade", {
    method: "POST",
    body: validTrade({
      symbol: "588000",
      module: "科创50波段",
      amount: 450,
      shares: 150,
      note: "",
      trigger_rule: "科创50第1笔买入",
    }),
  });
  assert.equal(missingNote.status, 400);
  assert.match(missingNote.json.error, /复盘备注/);

  const env = makeEnv();
  const saved = await callWorker(env, "/trade", {
    method: "POST",
    body: validTrade({
      symbol: "588000",
      module: "科创50波段",
      amount: 450,
      shares: 150,
      note: "真实成交，复盘确认偏离档位。",
      trigger_rule: "科创50第1笔买入",
    }),
  });
  assert.equal(saved.status, 200);
  assert.equal(saved.json.ok, true);
  assert.deepEqual(env.TRADES.dump()[0].compliance_warnings, [
    "科创50买入份额不是100份整数倍。",
    "科创50第1笔买入金额超过目标金额400元。",
  ]);
});

test("worker protects read endpoint and handles cors preflight", async () => {
  const env = makeEnv();

  const unauthorized = await callWorker(env, "/trades");
  assert.equal(unauthorized.status, 401);
  assert.equal(unauthorized.json.ok, false);

  const authorized = await callWorker(env, "/trades", {
    headers: { Authorization: "Bearer read" },
  });
  assert.equal(authorized.status, 200);
  assert.deepEqual(authorized.json.trades, []);
  assert.equal(authorized.headers.get("cache-control"), "no-store");

  const preflight = await callWorker(env, "/trade", { method: "OPTIONS" });
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get("access-control-allow-origin"), "https://wsyl-yyy.github.io");
  assert.match(preflight.headers.get("access-control-allow-methods"), /POST/);
});

test("worker reports missing dependencies as json errors", async () => {
  const missingKv = await callWorker({ ...makeEnv(), TRADES: undefined }, "/trade", {
    method: "POST",
    body: validTrade(),
  });
  assert.equal(missingKv.status, 500);
  assert.equal(missingKv.json.ok, false);

  const missingGithubToken = await callWorker({ ...makeEnv(), GITHUB_TOKEN: "" }, "/trade", {
    method: "POST",
    body: validTrade(),
  });
  assert.equal(missingGithubToken.status, 502);
  assert.equal(missingGithubToken.json.ok, false);
});

function validTrade(overrides = {}) {
  return {
    submitPassword: "pw",
    date: "2026-05-17",
    symbol: "563360",
    side: "买入",
    module: "A500网格",
    price: 1.032,
    amount: 600,
    shares: 500,
    fee: 0.06,
    note: "CODEx_TEST_worker",
    ...overrides,
  };
}

function makeEnv() {
  const dispatches = [];
  return {
    TRADES: makeKv(),
    SUBMIT_PASSWORD: "pw",
    READ_TOKEN: "read",
    GITHUB_TOKEN: "gh",
    GITHUB_REPO: "owner/repo",
    ALLOWED_ORIGIN: "https://wsyl-yyy.github.io",
    dispatches,
  };
}

function makeKv() {
  let data = null;
  return {
    async get(_key, type) {
      return type === "json" && data ? JSON.parse(data) : data;
    },
    async put(_key, value) {
      data = value;
    },
    async delete() {
      data = null;
    },
    dump() {
      return data ? JSON.parse(data) : [];
    },
  };
}

async function callWorker(env, path, options = {}) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_url, init) => {
    env.dispatches?.push(JSON.parse(init.body));
    return new Response(null, { status: 204 });
  };
  try {
    const headers = new Headers(options.headers || {});
    if (options.origin !== null) {
      headers.set("Origin", options.origin || "https://wsyl-yyy.github.io");
    }
    if (options.body) {
      headers.set("Content-Type", "application/json");
    }
    const response = await worker.fetch(
      new Request(`https://worker.test${path}`, {
        method: options.method || "GET",
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined,
      }),
      env,
    );
    const text = await response.text();
    return {
      status: response.status,
      headers: response.headers,
      json: text ? JSON.parse(text) : null,
      text,
    };
  } finally {
    globalThis.fetch = originalFetch;
  }
}
