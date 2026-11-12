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
      etf: { status: "ok", asof: "2026-11-11", net_1d: -20.66, net_5d: 5, net_20d: 1749.99, coverage: "3/3 只有数据", items: [{ status: "ok", code: "510300" }], source: "上交所", source_url: "javascript:alert(1)" },
      holders: { status: "ok", report_date: "2026-06-30", prev_date: "2025-12-31", inst_shares: 348.75, inst_pct: 72.76, chg_shares: -1968.88, chg_pct_pt: -21.12, codes: ["510300","510310"], source: "天天基金 F10 持有人结构", source_url: "javascript:alert(2)" } },
    sse50: { name: "上证50", tracked: true, main: { status: "missing", reason: "限流" }, etf: { status: "missing" }, holders: { status: "missing", reason: "无完整持有人结构" } },
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
assert.ok(card.includes("机构ETF持仓") && card.includes("半年报/年报"));
assert.ok(card.includes("报告期 2026-06-30"));
assert.ok(card.includes("348.75亿份") && card.includes("占72.76%"));
assert.ok(card.includes('class="down">-1968.88') && card.includes('class="down">-21.12'));
assert.ok(card.includes("510300/510310"));
assert.ok(!card.includes("javascript:"), "unsafe URLs are not linked");
const miss = v.cardHtml(b, "sse50");
assert.ok(miss.includes("无数据（限流）"));
assert.ok(miss.includes("机构ETF持仓") && miss.includes("无数据（无完整持有人结构）"));
const sum = v.summaryHtml(b);
assert.ok(sum.includes("北向资金净买额</strong>：无数据（停止披露）"));
assert.ok(sum.includes("沪深两市主力净流入"));
assert.ok(sum.includes("-1033.3"));
assert.ok(sum.includes("&lt;不计分&gt;"), "notes escaped");
assert.ok(sum.includes("深交所") && sum.includes("无数据（接口无返回）"));
assert.ok(sum.includes("机构ETF持仓（半年报/年报）"));
assert.ok(sum.includes("2026-06-30") && sum.includes("348.75") && sum.includes("72.76%"));
assert.ok(sum.includes('class="down">-1968.88'));
assert.ok(sum.includes("无数据（无完整持有人结构）"));
// 创业板：ETF净申赎缺失时，机构持仓仍可展示
const cx = {
  fetched_at: b.fetched_at, order: ["chinext"], indices: {
    chinext: { name: "创业板指", tracked: false,
      main: { status: "missing" }, etf: { status: "missing", reason: "深交所不提供历史份额" },
      holders: { status: "ok", report_date: "2026-06-30", prev_date: "2025-12-31", inst_shares: 104.86, inst_pct: 57.47, chg_shares: 1.2, chg_pct_pt: 0.5, codes: ["159915"], source: "天天基金 F10 持有人结构", source_url: "https://fundf10.eastmoney.com/cyrjg_159915.html" } },
  }, market: b.market, notes: [],
};
const cxCard = v.cardHtml(cx, "chinext");
assert.ok(cxCard.includes("ETF净申赎") && cxCard.includes("无数据（深交所不提供历史份额）"));
assert.ok(cxCard.includes("104.86亿份") && cxCard.includes("报告期 2026-06-30"));
assert.ok(cxCard.includes('class="up">+1.20') && cxCard.includes('href="https://fundf10.eastmoney.com/cyrjg_159915.html"'));
console.log("cn_index_flow_view tests passed");
