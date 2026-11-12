// node tests/cn_index_option_levels_view.test.js
const assert = require("assert");
const v = require("../static/cn_index_option_levels_view.js");

assert.ok(v.blockHtml(null).includes("无数据"));
assert.ok(v.blockHtml({ option_levels: { status: "nodata", reason_cn: "无数据（期权链快照缺失）" } }).includes("期权链快照缺失"));
assert.strictEqual(v.pct(-6.78), '<span class="down">-6.78%</span>');
assert.strictEqual(v.pct(6.71), '<span class="up">+6.71%</span>');

const leg = { side_cn: "卖Put", status: "ok", code: "io2612P3800", strike: 3800, premium: 18.4, bid: 18, ask: 18.8,
  premium_basis: "买卖中间价", premium_yuan: 1840, breakeven: 3781.6, be_dist_pct: -7.2, dist_pct: -6.78, delta: -0.132,
  stop_cn: "标的收盘跌破支撑 4000，或权利金升至 36.8 点（2倍）", take_profit_cn: "权利金回落至 9.2 点附近（衰减50%）买回",
  spread: { buy_code: "io2612P3700", buy_strike: 3700, buy_premium: 9.5, net_credit: 8.9, width: 100, max_loss_pts: 91.1, max_loss_yuan: 9110, breakeven: 3791.1 } };
const html = v.blockHtml({ option_levels: { status: "ok", label_cn: "参考价位，不构成下单建议", product: "IO", month: "io2612",
  expire: "2026-12-18", dte: 37, spot: 4076.48, atm_iv_pct: 17.4, asof: "2026-11-12 09:39:36 CST", source_cn: "新浪<延时>",
  legs: { put: leg, call: { side_cn: "卖Call", status: "none", reason_cn: "无同时满足条件的挂牌行权价" } },
  rules_cn: ["规则<1>"] } });
assert.ok(html.includes("参考价位，不构成下单建议"));
assert.ok(html.includes("io2612P3800") && html.includes("3800") && html.includes("18.4"));
assert.ok(html.includes("3781.6") && html.includes("io2612P3700"));
assert.ok(html.includes("2026-11-12 09:39:36 CST"));
assert.ok(html.includes("无同时满足条件"));
assert.ok(html.includes('class="down">-6.78%'));
assert.ok(!html.includes("<延时>") && !html.includes("规则<1>"), "escaped");
assert.ok(!/T型|cnopt-table/.test(html), "no T-table chain");
console.log("cn_index_option_levels_view tests passed");
