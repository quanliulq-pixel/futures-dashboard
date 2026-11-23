const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const code = fs.readFileSync(path.join(__dirname, "../static/accum_trap_view.js"), "utf8");
const ctx = { window: {}, globalThis: {} };
ctx.window = ctx;
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(code, ctx);
const v = ctx.AccumTrapView;
assert.ok(v && v.render);

const missing = v.render({ status: "no_data", error: "无数据" }, "cn");
assert.ok(missing.includes("无数据"));

const snap = v.render({
  status: "ok", from_snapshot: true, snapshot_asof: "2026-11-20",
  disclaimer: "研究参考，不能下单，不构成投资建议",
  theory: "位置决定一切",
  items: [],
}, "cn");
assert.ok(snap.includes("上次快照（2026-11-20）"));
assert.ok(snap.includes("研究参考，不能下单"));

const item = {
  id: "sse", name: "上证指数", symbol: "sh000001", market: "cn", kind: "index",
  status: "ok", asof: "2026-11-20", close: 3533.79, chg_1d: 0.012,
  accum_score: 72, trap_score: 18, label: "疑似吸筹",
  position: { bucket: "低位", range_pctile: 0.22, drawdown_250: -0.25, rally_from_low: 0.08 },
  relative: { bench_name: "沪深300", rs_20: 0.03 },
  chips: { status: "ok", peak_price: 3200, note: "估算，非真实持仓" },
  holders: { status: "not_applicable", reason: "不适用" },
  hits: {
    accum: [{ id: "low_zone", name: "低位", hit: true, applicable: true, max_points: 25, detail: "分位 22%" }],
    trap: [{ id: "mid_or_high", name: "中高位", hit: false, applicable: true, max_points: 25, detail: "低位" }],
  },
  backtest: {
    accum: { n: 12, ret_20_avg: 0.021, win_20: 0.58, ret_60_avg: 0.04, win_60: 0.6, insufficient: false },
    trap: { n: 3, ret_20_avg: -0.01, win_20: 0.33, insufficient: true, note: "样本不足（n=3，至少 8）" },
  },
};
const html = v.render({
  status: "ok", disclaimer: "研究参考，不能下单，不构成投资建议",
  theory: "真正吸筹：低位",
  rules: { range_days: 250, low_pctile: 0.3 },
  items: [item, {
    id: "foo", name: "示例股", symbol: "600000", market: "cn", kind: "stock",
    example: true, status: "missing", reason: "无数据", label: "无数据",
  }],
  backtest_summary: { cn: { accum: item.backtest.accum, trap: item.backtest.trap } },
}, "cn");
assert.ok(html.includes("上证指数") && html.includes("疑似吸筹"));
assert.ok(html.includes("class=\"up\"") && html.includes("+1.20%"));
assert.ok(html.includes("估算，非真实持仓"));
assert.ok(html.includes("股东户数：不适用"));
assert.ok(html.includes("命中") && html.includes("未中"));
assert.ok(html.includes("样本不足"));
assert.ok(html.includes("示例") && html.includes("无数据"));
assert.ok(html.includes("研究参考，不能下单"));

const usMiss = v.itemCard({ name: "标普500", status: "missing", reason: "无数据" });
assert.ok(usMiss.includes("无数据"));

assert.ok(v.labelClass("疑似诱多").includes("trap"));
assert.ok(v.pct(-0.012, 1) === "-1.2%");
console.log("accum_trap_view tests passed");
