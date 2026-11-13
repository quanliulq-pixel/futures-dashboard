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

// 主力来自上次快照：仍显示红绿数字，并注明上次快照与 asof；北向/创业板申赎仍为无数据
const snap = JSON.parse(JSON.stringify(b));
snap.main_from_snapshot = true;
snap.main_snapshot_asof = "2026-11-11";
snap.indices.csi300.main.from_snapshot = true;
snap.market.main.from_snapshot = true;
snap.indices.sse50.main = { status: "ok", asof: "2026-11-11", net_1d: -0.45, net_5d: 1.2, net_20d: null, from_snapshot: true };
const snapCard = v.cardHtml(snap, "csi300");
assert.ok(snapCard.includes('class="up">+110.13'));
assert.ok(snapCard.includes("上次快照"));
assert.ok(snapCard.includes("2026-11-11"));
assert.ok(!snapCard.includes("无数据（限流）"));
const snapSum = v.summaryHtml(snap);
assert.ok(snapSum.includes("沪深两市主力净流入"));
assert.ok(snapSum.includes('class="up">+198.47') || snapSum.includes('class="up">+198.47'.replace("+198.47", "+198.47")));
assert.ok(snapSum.includes("+198.47"));
assert.ok(snapSum.includes("上次快照"));
assert.ok(snapSum.includes("主力为上次快照（2026-11-11）"));
assert.ok(snapSum.includes("北向资金净买额</strong>：无数据（停止披露）"));
assert.ok(snapSum.includes('class="down">-0.45'));
const cxSnap = JSON.parse(JSON.stringify(cx));
cxSnap.indices.chinext.main = { status: "ok", asof: "2026-11-11", net_1d: 92.89, from_snapshot: true };
const cxSnapCard = v.cardHtml(cxSnap, "chinext");
assert.ok(cxSnapCard.includes('class="up">+92.89') && cxSnapCard.includes("上次快照"));
assert.ok(cxSnapCard.includes("ETF净申赎") && cxSnapCard.includes("无数据（深交所不提供历史份额）"));

// ETF申赎来自上次快照：显示数字 + 「数据 asof · 上次快照」；总览表日期列与元信息也注明
const etfSnap = JSON.parse(JSON.stringify(b));
etfSnap.etf_from_snapshot = true;
etfSnap.etf_snapshot_asof = "2026-11-12";
etfSnap.indices.sse50.main = { status: "ok", asof: "2026-11-12", net_1d: -4.02, net_5d: -28.87, net_20d: -150.88 };
etfSnap.indices.sse50.etf = { status: "ok", asof: "2026-11-12", net_1d: -9.7, net_5d: 5.68, net_20d: 255.44,
  coverage: "2/2 只有数据", items: [{ status: "ok", code: "510050" }, { status: "ok", code: "510100" }],
  source: "上交所 ETF 规模（份额）+ 新浪日线收盘价", source_url: "https://www.sse.com.cn/assortment/fund/etf/list/scale/",
  from_snapshot: true };
const etfSnapCard = v.cardHtml(etfSnap, "sse50");
assert.ok(etfSnapCard.includes('class="down">-9.70') && etfSnapCard.includes('class="up">+255.44'));
assert.ok(etfSnapCard.includes("数据 2026-11-12 · 上次快照 · 2/2 只有数据"));
assert.ok(!etfSnapCard.includes("上交所ETF份额接口无返回"));
const etfSnapSum = v.summaryHtml(etfSnap);
assert.ok(etfSnapSum.includes("ETF申赎为上次快照（2026-11-12）"));
// 未标快照的实时 ETF 不出现「上次快照」
const etfLive = JSON.parse(JSON.stringify(etfSnap));
delete etfLive.indices.sse50.etf.from_snapshot;
assert.ok(!v.cardHtml(etfLive, "sse50").includes("上次快照"));

console.log("cn_index_flow_view tests passed");
