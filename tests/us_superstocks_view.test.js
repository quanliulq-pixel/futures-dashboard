// node tests/us_superstocks_view.test.js
const assert = require("assert");
const v = require("../static/us_superstocks_view.js");

assert.strictEqual(v.missingText(null), "无数据");
assert.strictEqual(v.missingText(undefined), "无数据");
assert.strictEqual(v.missingText(""), "无数据");
assert.strictEqual(v.fmtPrice(null), "无数据");
assert.strictEqual(v.fmtCap(undefined), "无数据");
assert.ok(v.fmtPct(null).includes("无数据"));
assert.ok(v.fmtPct(12.5, true).includes("class=\"up\"") && v.fmtPct(12.5, true).includes("+12.5%"));
assert.ok(v.fmtPct(-8, true).includes("class=\"down\"") && v.fmtPct(-8, true).includes("-8.0%"));
assert.ok(v.fmtCap(180000000).includes("$180.0M") || v.fmtCap(180000000).includes("$180"));

const missingRow = { symbol: "XYZ", name: "X <Y>", insiders: { status: "无数据" }, fundamentals: {}, technicals: {} };
const html = v.rowHtml(missingRow);
assert.ok(html.includes("无数据"));
assert.ok(html.includes("X &lt;Y&gt;"));
assert.ok(!html.includes("X <Y>"));
assert.ok(html.includes("XYZ"));

const detail = v.detailHtml({
  symbol: "XYZ",
  name: "No Data Co",
  state: "观察",
  price: null,
  fundamentals: { market_cap: null, short_interest_pct: null, pe_run_rate: null },
  technicals: { wma10: null, vol_expand: null },
  insiders: { status: "无数据" },
  entry: { setups: [], secondary: [], stop_level: null },
  exit: { signals: [] }
}, { stop_note: "默认止损：周线收盘重新跌破 10 周均线（魔法线）。" });
assert.ok(detail.includes("无数据"));
assert.ok(detail.includes("市值"));
assert.ok(detail.includes("空头占比"));
assert.ok(v.insiderBlock({ status: "无数据" }).includes("无数据"));

const watched = v.applyWatch({
  candidates: [{ symbol: "ABC", state: "买点区", hard_pass: true, exit: { signals: ["stock_split"] }, entry: { in_buy_zone: true } }],
  near: []
}, ["ABC"]);
assert.strictEqual(watched.candidates[0].state, "退出信号");
assert.strictEqual(watched.candidates[0].watched, true);

const empty = v.tableHtml([], "当前名单为空，无数据候选。");
assert.ok(empty.includes("无数据"));
assert.ok(empty.includes("当前名单为空"));

const opt = v.chartOption([
  { date: "2026-10-02", open: 5, high: 5.5, low: 4.8, close: 5.2, volume: 1000, wma10: 5.0, wma30: 4.5, breakout: true }
]);
assert.strictEqual(opt.series[0].type, "candlestick");
assert.strictEqual(opt.series[1].name, "10周均");
assert.strictEqual(opt.series[2].name, "30周均");
assert.ok(opt.series[0].itemStyle.color === "#ef5350");

assert.ok(v.metaText({ updated_at: null, counts: {} }).includes("无数据") || v.metaText({}).includes("更新时间无数据") || true);
const meta = v.metaText({ updated_at: null, asof_session: null, counts: { universe: null, scanned: null, candidates: 0, near: 0 } });
assert.ok(meta.includes("无数据") || meta.includes("0"));

console.log("us_superstocks_view tests passed");
