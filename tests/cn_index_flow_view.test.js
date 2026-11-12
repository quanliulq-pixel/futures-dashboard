// node tests/cn_index_flow_view.test.js
const assert = require("assert");
const v = require("../static/cn_index_flow_view.js");

assert.strictEqual(v.signed(null), "无数据");
assert.strictEqual(v.signed(1.234), "+1.23");
assert.strictEqual(v.cls(3), "up");
assert.strictEqual(v.cls(-3), "down");
assert.strictEqual(v.cls(null), "nodata");

// 无 bundle → 无数据，不补算
assert.ok(v.cardHtml(null, "csi300").includes("无数据"));
assert.ok(v.summaryHtml(null).includes("无数据"));

const b = {
  fetched_at: "2026-11-12T09:00:00+08:00",
  order: ["csi300", "sse50"],
  notes: ["只展示<不计分>"],
  indices: {
    csi300: { name: "沪深300", tracked: true,
      main: { status: "ok", asof: "2026-11-11", net_1d: 110.13, net_5d: -20.5, net_20d: null, source: "东方财富", source_url: "https://data.eastmoney.com/zjlx/dpzjlx.html" },
      etf: { status: "ok", asof: "2026-11-11", net_1d: -20.66, net_5d: 5, net_20d: 1749.99, coverage: "3/3 只有数据", items: [{ status: "ok", code: "510300" }], source: "上交所", source_url: "javascript:alert(1)" } },
    sse50: { name: "上证50", tracked: true, main: { status: "missing", reason: "限流" }, etf: { status: "missing" } },
  },
  market: {
    main: { status: "ok", asof: "2026-11-11", net_1d: 198.47, net_5d: null, net_20d: null },
    northbound: { status: "missing", reason: "停止披露" },
    margin_sh: { status: "ok", asof: "2026-11-11", balance: 11767.5, chg_1d: 23.6, chg_5d: -8.6, chg_20d: -1033.3 },
    margin_sz: { status: "missing", reason: "接口无返回" },
    margin_total: { status: "missing" },
  },
};
const card = v.cardHtml(b, "csi300");
assert.ok(card.includes('class="up">+110.13'));
assert.ok(card.includes('class="down">-20.50'));
assert.ok(card.includes("2026-11-11"));
assert.ok(card.includes("估算"));
assert.ok(!card.includes("javascript:"), "unsafe URLs are not linked");
const miss = v.cardHtml(b, "sse50");
assert.ok(miss.includes("无数据（限流）"));
const sum = v.summaryHtml(b);
assert.ok(sum.includes("北向资金净买额</strong>：无数据（停止披露）"));
assert.ok(sum.includes("沪深两市主力净流入"));
assert.ok(sum.includes("-1033.3"));
assert.ok(sum.includes("&lt;不计分&gt;"), "notes escaped");
assert.ok(sum.includes("深交所") && sum.includes("无数据（接口无返回）"));
console.log("cn_index_flow_view tests passed");
