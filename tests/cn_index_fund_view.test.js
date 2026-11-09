const assert = require("assert");
const v = require("../static/cn_index_fund_view.js");

function testLegacyAdviceNotBackfilled() {
  const a = { stance_cn: "防守区间", score: -35, option_bias_cn: "偏卖Put" };
  const s = v.scores(a);
  assert.strictEqual(s.legacy, true);
  assert.strictEqual(s.fund, null);
  assert.strictEqual(s.combined, null);
  assert.strictEqual(s.tech, 32.5);
  const h = v.blockHtml(a);
  assert.ok(h.includes("基本面分 未计算"));
  assert.ok(h.includes("综合分 —"));
  assert.ok(h.includes("基本面未计算"));
}

function testFullAdvicePolicyFirstEscapedLinks() {
  const a = {
    score: -35, tech_score: 32.5, fund_score: 45.4, combined_score: 37.7, fund_status: "ok",
    fund_status_cn: "基本面完整", fund_asof: "2026-11-09T23:35:46+08:00",
    option_bias_cn: "铁秃鹰/价差", option_bias_tech_cn: "偏卖Put", fund_adjust_cn: "基本面分 39.0≤40（偏空）<b>",
    fund_groups: [
      { key: "policy", name: "政策", weight_nominal: 0.35, weight: 0.35, contribution: 2.6, scope: "market", emphasis: true },
      { key: "valuation", name: "估值", weight_nominal: 0.25, weight: 0.25, contribution: 2.5, scope: "index" },
      { key: "liquidity", name: "流动性", weight_nominal: 0.25, weight: 0.25, contribution: null, scope: "market" },
    ],
    fund_factors: [
      { key: "pe_pctile", group: "valuation", scope: "index", name: "PE(TTM) 分位", status: "ok", signal_text: "偏空",
        contribution: -1.8, weight: 0.1, value_text: "PE 12.72 <x>", asof: "2026-11-09",
        source: "乐咕乐股", source_url: "https://legulegu.com/stockdata/hs300-ttm-lyr" },
      { key: "lpr", group: "policy", scope: "market", name: "LPR", status: "ok", signal_text: "中性",
        contribution: 0, weight: 0.122, value_text: "3.00%", asof: "2026-10-20",
        source: "东方财富", source_url: "javascript:alert(1)" },
      { key: "margin", group: "liquidity", scope: "market", name: "两融", status: "missing", reason: "超时" },
    ],
    policy_event_window: { in_window: true, text: "政策事件落在近月期权到期 2026-11-20之前：2026-11-20 LPR",
      events: [{ date: "2026-11-20", name: "LPR", date_basis: "按规则推算",
        sources: [{ label: "人民银行公告", url: "https://www.pbc.gov.cn/x" }] }] },
  };
  const h = v.blockHtml(a);
  assert.ok(h.includes("技术分 32.5") && h.includes("基本面分 45.4") && h.includes("综合分 37.7"));
  assert.ok(h.indexOf("政策") < h.indexOf("估值"), "policy group first");
  assert.ok(h.includes("权重最高"));
  assert.ok(h.includes("名义 35%"));
  assert.ok(h.includes("数据截至 2026-10-20"));
  assert.ok(h.includes('href="https://legulegu.com/stockdata/hs300-ttm-lyr"'));
  assert.ok(!h.includes("javascript:"), "unsafe url not linked");
  assert.ok(h.includes("&lt;x&gt;") && h.includes("&lt;b&gt;") && !h.includes("<x>"));
  assert.ok(h.includes("无数据（超时）"));
  assert.ok(h.includes("⚠") && h.includes('href="https://www.pbc.gov.cn/x"'));
  assert.ok(h.includes("技术面原为「偏卖Put」"));
}

function testNotComputedNewSeed() {
  const a = { score: 10, tech_score: 55, fund_score: null, combined_score: 55, fund_status: "not_computed",
    fund_status_cn: "基本面未计算", fund_factors: [] };
  const h = v.blockHtml(a);
  assert.ok(h.includes("基本面分 未计算"));
  assert.ok(h.includes("综合分 55.0"));
}

testLegacyAdviceNotBackfilled();
testFullAdvicePolicyFirstEscapedLinks();
testNotComputedNewSeed();
console.log("cn_index_fund_view tests passed");
