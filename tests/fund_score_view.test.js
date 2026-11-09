const assert = require("assert");
const fv = require("../static/fund_score_view.js");
const cnView = require("../static/cn_option_candidates_view.js");

function testLegacyRowNotBackfilled() {
  const r = { symbol: "NVDA", score: 41.2, kind: "stock" };
  const s = fv.scores(r);
  assert.strictEqual(s.legacy, true);
  assert.strictEqual(s.fund, null);
  assert.strictEqual(s.combined, null);
  assert.strictEqual(s.tech, 41.2);
  const h = fv.blockHtml(r);
  assert.ok(h.includes("基本面分 未计算"));
  assert.ok(h.includes("综合分 —"));
  assert.ok(h.includes("基本面未计算"));
  assert.deepStrictEqual(fv.headline(r), { value: 41.2, label: "技术分" });
  assert.ok(fv.cellHtml(r).includes("基本面未计算"));
}

function testFullRowShowsThreeScoresFactorsAndDates() {
  const r = {
    symbol: "MA0", score: 34.7, tech_score: 34.7, fund_score: 79.1, combined_score: 52.5,
    fund_status: "ok", fund_status_cn: "基本面完整",
    fund_factors: [
      { key: "basis", name: "基差（现货vs主力）", status: "ok", signal_text: "明显偏多", contribution: 15,
        value_text: "现货升水 +20%", asof: "2026-11-09", source: "生意社", source_url: "https://www.100ppi.com/sf/" },
      { key: "inventory", name: "库存/仓单趋势", status: "missing", signal_text: "无数据", reason: "取数失败：<b>x</b>" },
      { key: "term", name: "期限", status: "ok", signal_text: "偏多", contribution: 2, value_text: "v",
        asof: "2026-11-09", source: "evil", source_url: "javascript:alert(1)" },
    ],
  };
  const h = fv.blockHtml(r);
  assert.ok(h.includes("技术分 34.7") && h.includes("基本面分 79.1") && h.includes("综合分 52.5"));
  assert.ok(h.includes("截至 2026-11-09"));
  assert.ok(h.includes("无数据（取数失败：&lt;b&gt;x&lt;/b&gt;）"), "reason escaped");
  assert.ok(!h.includes("<b>x</b>"));
  assert.ok(!h.includes("javascript:"), "non-http source urls are not linked");
  assert.ok(h.includes('href="https://www.100ppi.com/sf/"'));
  assert.deepStrictEqual(fv.headline(r), { value: 52.5, label: "综合分" });
  assert.ok(fv.cellHtml(r).includes("52.5") && fv.cellHtml(r).includes("技34.7 / 基79.1"));
}

function testNoDataRow() {
  const r = { symbol: "SPY", score: 45, tech_score: 45, fund_score: null, combined_score: 45,
    fund_status: "no_data", fund_status_cn: "基本面无数据（ETF 不适用个股基本面）", fund_factors: [] };
  const h = fv.blockHtml(r);
  assert.ok(h.includes("基本面分 无数据"));
  assert.ok(h.includes("ETF 不适用"));
  assert.ok(fv.cellHtml(r).includes("基无"));
}

function testEarningsWarning() {
  const r = { symbol: "NVDA", score: 40, tech_score: 40, fund_score: 60, combined_score: 48, fund_status: "ok",
    earnings_window: { in_window: true, text: "财报 2026-11-18（9天后）落在期权到期 2026-12-09（30天）之内，事件跳空风险" },
    fund_penalty: { points: -10, reason: "财报 2026-11-18" }, fund_factors: [{ key: "valuation", name: "估值", status: "ok", signal_text: "偏多", contribution: 5 }] };
  const h = fv.blockHtml(r);
  assert.ok(h.includes("⚠ 财报 2026-11-18"));
  assert.ok(h.includes("财报窗口扣分 -10.0"));
  const out = { ...r, earnings_window: { in_window: false, text: "之后" } };
  assert.ok(!fv.blockHtml(out).includes("⚠"));
}

function testCnCardUsesCombinedAndKeepsCnyNoUsd() {
  const r = { symbol: "MA0", name: "甲醇", kind: "cn_fut", market: "cn", price: 3194, score: 34.7, tech_score: 34.7,
    fund_score: 79.1, combined_score: 52.5, fund_status: "ok", fund_factors: [], asof: "2026-11-09",
    scanned_at: "2026-11-09T21:00:00+08:00" };
  const h = cnView.cardHtml(r, "put");
  assert.ok(h.includes(">52.5<"));
  assert.ok(h.includes("技术分 34.7"));
  assert.ok(!h.includes("$"));
  const legacy = cnView.cardHtml({ symbol: "MA0", kind: "cn_fut", market: "cn", score: 36.7 }, "call");
  assert.ok(legacy.includes(">36.7<") && legacy.includes("基本面未计算"));
}

testLegacyRowNotBackfilled();
testFullRowShowsThreeScoresFactorsAndDates();
testNoDataRow();
testEarningsWarning();
testCnCardUsesCombinedAndKeepsCnyNoUsd();
console.log("fund_score_view.test.js OK");
