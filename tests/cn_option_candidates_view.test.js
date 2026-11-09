const assert = require("assert");
const view = require("../static/cn_option_candidates_view.js");

function testNormalizeAbsent() {
  const n = view.normalizePayload({ picks: [{ symbol: "XLE" }] });
  assert.strictEqual(n.candidates.length, 0);
  assert.strictEqual(n.status, "absent_in_snapshot");
  assert.ok(String(n.note).includes("未伪补"));
}

function testFilterByKindExcludesCnFromUs() {
  const rows = [
    { symbol: "NVDA", kind: "stock" },
    { symbol: "SPY", kind: "etf" },
    { symbol: "M0", kind: "cn_fut", market: "cn", price: 3000 },
  ];
  assert.deepStrictEqual(view.filterByKind(rows, "all").map((r) => r.symbol), ["NVDA", "SPY"]);
  assert.deepStrictEqual(view.filterByKind(rows, "etf").map((r) => r.symbol), ["SPY"]);
  assert.deepStrictEqual(view.filterByKind(rows, "stock").map((r) => r.symbol), ["NVDA"]);
  assert.deepStrictEqual(view.filterByKind(rows, "cn_fut").map((r) => r.symbol), ["M0"]);
}

function testUsTableNeverRendersCnEvenIfMergedIntoAll() {
  const rows = [
    { symbol: "XLE", kind: "etf", put: { strike: 60, mid: 1.2 } },
    { symbol: "PG0", kind: "cn_fut", market: "cn", price: 6700, currency: "CNY" },
  ];
  for (const kind of ["all", "etf", "stock", "cn_fut"]) {
    const us = view.usTableRowsAfterKindFilter(rows, kind);
    assert.ok(us.every((r) => r.kind !== "cn_fut" && r.market !== "cn"), "kind=" + kind);
    assert.ok(!JSON.stringify(us).includes('"PG0"') || kind === "never");
  }
  // Regression the review caught: kind=all must not keep CN for dollar/stock renderer.
  const allUs = view.usTableRowsAfterKindFilter(rows, "all");
  assert.deepStrictEqual(allUs.map((r) => r.symbol), ["XLE"]);
}

function testCardShowsAsofAndScanSeparatelyNoUsd() {
  const html = view.cardHtml(
    {
      symbol: "CU0",
      name: "沪铜",
      exchange: "上期所",
      sector: "有色",
      price: 70000,
      hv20: 22,
      hv_pctile: 70,
      rsi14: 40,
      chg_5d: -3,
      score: 35,
      bias_cn: "观望等待（方向·研究）",
      asof: "2026-11-06",
      scanned_at: "2026-11-09T10:00:00+09:00",
      scanned_at_tz: "Asia/Tokyo (UTC+9)",
      action: "标的方向研究/观望候选（连续合约）；未核验期权链与流动性，不能下单。",
      risk: { flags: ["连续合约", "期权链未核验"], margin_note: "期货期权保证金" },
    },
    "put"
  );
  assert.ok(html.includes("行情截至：2026-11-06"));
  assert.ok(html.includes("扫描时间：2026-11-09T10:00:00+09:00"));
  assert.ok(html.includes("Asia/Tokyo"));
  assert.ok(html.includes("CNY") || html.includes("¥"));
  assert.ok(!html.includes("$"));
  assert.ok(!html.includes("CSP"));
}

function testCardUnknownAsof() {
  const html = view.cardHtml({ symbol: "M0", name: "豆粕", asof: "未知", score: 30, risk: {} }, "put");
  assert.ok(html.includes("行情截至：未知"));
}

testNormalizeAbsent();
testFilterByKindExcludesCnFromUs();
testUsTableNeverRendersCnEvenIfMergedIntoAll();
testCardShowsAsofAndScanSeparatelyNoUsd();
testCardUnknownAsof();
console.log("cn_option_candidates_view.test.js OK");
