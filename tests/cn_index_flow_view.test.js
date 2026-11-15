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
    main_total: { status: "ok", asof: "2026-11-13", net_1d: -215.9, net_5d: 1138.12, net_20d: 6436.14,
      source: "新浪财经 板块资金流向", source_url: "https://vip.stock.finance.sina.com.cn/moneyflow/" },
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
assert.ok(sum.includes('title="') && sum.includes("特大单"));
assert.ok(sum.includes('class="down">-215.9'));
assert.ok(sum.includes('class="up">+1138.1'));
assert.ok(sum.includes('class="up">+6436.1'));
assert.ok(sum.includes("2026-11-13"));
assert.ok(sum.includes("新浪财经"));
assert.ok(!sum.includes("+198.47"), "top line must not use Eastmoney market.main");
assert.ok(sum.includes("-1033.3"));
assert.ok(sum.includes("&lt;不计分&gt;"), "notes escaped");
assert.ok(sum.includes("深交所") && sum.includes("无数据（接口无返回）"));
assert.ok(sum.includes("机构ETF持仓（半年报/年报）"));
const noSina = JSON.parse(JSON.stringify(b));
delete noSina.market.main_total;
const noSinaSum = v.summaryHtml(noSina);
assert.ok(noSinaSum.includes("沪深两市主力净流入"));
assert.ok(noSinaSum.includes("无数据"));
assert.ok(!noSinaSum.includes("+198.47"));
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
snap.market.main_total = Object.assign({}, snap.market.main_total, { from_snapshot: true });
const snapSum = v.summaryHtml(snap);
assert.ok(snapSum.includes("沪深两市主力净流入"));
assert.ok(snapSum.includes('class="down">-215.9'));
assert.ok(!snapSum.includes("+198.47"), "top line must not use Eastmoney market.main");
assert.ok(snapSum.includes("上次快照"));
assert.ok(snapSum.includes("主力净流入为上次快照") || snapSum.includes("数据 2026-11-13 · 上次快照"));
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

// 两融来自快照：数据日期后注明 上次快照；有变化时显示红绿数字
const mgSnap = JSON.parse(JSON.stringify(b));
mgSnap.market.margin_sz = { status: "ok", asof: "2026-11-12", balance: 10883.9, chg_1d: -5.0, chg_5d: 16.4, chg_20d: -1290.1,
  source: "深交所 融资融券汇总", source_url: "https://www.szse.cn/disclosure/margin/margin/index.html", from_snapshot: true };
const mgSum = v.summaryHtml(mgSnap);
assert.ok(mgSum.includes("10883.9") && mgSum.includes('class="down">-5.0') && mgSum.includes('class="up">+16.4'));
assert.ok(mgSum.includes("2026-11-12 · 上次快照 · "));

console.log("cn_index_flow_view tests passed");

// 两融历史最高/最低 + 对应指数
assert.strictEqual(v.fmtYi(15211.1408), "15211.1");
assert.strictEqual(v.fmtYi(0.0582), "0.0582");
assert.strictEqual(v.fmtYi(0.0067), "0.0067");
assert.strictEqual(v.fmtYi(null), null);

const histB = JSON.parse(JSON.stringify(b));
histB.margin_history_coverage_start = "2010-03-31";
histB.margin_history_low_coverage_start = "2014-09-22";
histB.market.margin_sh = {
  status: "ok", asof: "2026-11-13", balance: 11692.6, chg_1d: -86.7, chg_5d: 10.0, chg_20d: -20.0,
  source: "上交所 融资融券汇总", source_url: "https://www.sse.com.cn/market/othersdata/margin/sum/",
  hist_coverage_start: "2010-03-31", hist_low_coverage_start: "2014-09-22",
  index_name: "上证指数", index_code: "000001",
  hist_high: { date: "2026-06-25", balance: 15211.1408, index_close: 4120.28, index_name: "上证指数", index_code: "000001" },
  hist_low: { date: "2014-09-22", balance: 3776.4166, index_close: 2289.87, index_name: "上证指数", index_code: "000001" },
  index_now: { date: "2026-11-13", close: 3634.35, index_name: "上证指数", index_code: "000001" },
};
histB.market.margin_sz = {
  status: "missing", reason: "接口无返回",
  hist_coverage_start: "2010-03-31", hist_low_coverage_start: "2014-09-22",
  index_name: "深证成指", index_code: "399001",
  hist_high: { date: "2026-06-25", balance: 14798.2858, index_close: 16344.08, index_name: "深证成指", index_code: "399001" },
  hist_low: { date: "2014-09-22", balance: 2133.8169, index_close: 7895.87, index_name: "深证成指", index_code: "399001" },
  index_now: { date: "2026-11-13", close: 10968.79, index_name: "深证成指", index_code: "399001" },
};
histB.market.margin_total = {
  status: "ok", asof: "2026-11-12", balance: 22576.6, chg_1d: 7.0, chg_5d: 8.0, chg_20d: -9.0,
  hist_coverage_start: "2010-03-31", hist_low_coverage_start: "2014-09-22",
  index_name: "沪深300", index_code: "000300",
  hist_high: { date: "2026-06-25", balance: 30009.4267, index_close: 5020.1, index_name: "沪深300", index_code: "000300" },
  hist_low: { date: "2014-09-22", balance: 5910.2335, index_close: 2378.92, index_name: "沪深300", index_code: "000300" },
  index_now: { date: "2026-11-13", close: 4016.29, index_name: "沪深300", index_code: "000300" },
};
const histSum = v.summaryHtml(histB);
assert.ok(histSum.includes("历史最高(亿元)") && histSum.includes("历史最低(亿元)"));
assert.ok(histSum.includes("最高日指数") && histSum.includes("最低日指数") && histSum.includes("当前指数"));
assert.ok(histSum.includes("历史最高区间自 2010-03-31；历史最低区间自 2014-09-22（两融扩容后）"));
assert.ok(histSum.includes("上证指数(000001)") && histSum.includes("深证成指(399001)") && histSum.includes("沪深300(000300)"));
assert.ok(histSum.includes("15211.1") && histSum.includes("2026-06-25"));
assert.ok(histSum.includes("3776.4") && histSum.includes("2014-09-22"));
assert.ok(histSum.includes("4120.28") && histSum.includes("2289.87"));
assert.ok(histSum.includes("上证指数 3634.35"));
assert.ok(histSum.includes("沪深300 4016.29"));
assert.ok(histSum.includes("30009.4") && histSum.includes("5910.2"));
// 深市当日无数据时，历史高低仍展示
assert.ok(histSum.includes("无数据（接口无返回）") && histSum.includes("14798.3") && histSum.includes("2133.8"));
assert.ok(histSum.includes("16344.08") && histSum.includes("7895.87") && histSum.includes("深证成指 10968.79"));
// 当前指数相对最高日下跌=绿，相对最低日上涨=红
assert.ok(histSum.includes("较高日") && histSum.includes('class="down">'));
assert.ok(histSum.includes("较低日") && histSum.includes('class="up">'));
// 缺历史时显示无数据，不编造
const noHist = v.summaryHtml(b);
assert.ok(noHist.includes("历史最高(亿元)"));
assert.ok((noHist.match(/无数据/g) || []).length >= 5);

const histSnap = JSON.parse(JSON.stringify(histB));
histSnap.margin_history_from_snapshot = true;
histSnap.margin_history_snapshot_asof = "2026-11-12";
histSnap.market.margin_sh.hist_from_snapshot = true;
histSnap.market.margin_sh.hist_snapshot_asof = "2026-11-12";
const histSnapSum = v.summaryHtml(histSnap);
assert.ok(histSnapSum.includes("两融历史高低为上次快照（2026-11-12）"));
assert.ok(histSnapSum.includes("上次快照（2026-11-12）"));
assert.ok(histSnapSum.includes("15211.1"));

// 两融历史走势图：选择器、按周抽样实际点、高低标注、tooltip 字段
const chartSum = v.summaryHtml(histB);
assert.ok(chartSum.includes("data-margin-chart"));
assert.ok(chartSum.includes("data-mkt=\"margin_sh\"") && chartSum.includes("上交所"));
assert.ok(chartSum.includes("data-mkt=\"margin_sz\"") && chartSum.includes("深交所"));
assert.ok(chartSum.includes("data-mkt=\"margin_total\"") && chartSum.includes("沪深合计"));
assert.ok(chartSum.includes("data-range=\"1y\"") && chartSum.includes("1年"));
assert.ok(chartSum.includes("data-range=\"3y\"") && chartSum.includes("3年"));
assert.ok(chartSum.includes("data-range=\"5y\"") && chartSum.includes("5年"));
assert.ok(chartSum.includes("data-range=\"all\"") && chartSum.includes("全部"));

const daily = [
  ["2014-09-22", 100.0, 2289.87],
  ["2014-09-23", 110.0, 2300.0],
  ["2014-09-26", 120.0, 2400.0],
  ["2026-06-24", 190.0, 4100.0],
  ["2026-06-25", 200.0, 4120.28],
  ["2026-06-26", 198.0, 4100.0],
];
const weekly = v.downsampleWeekly(daily);
assert.deepStrictEqual(weekly.map((p) => p[0]), ["2014-09-26", "2026-06-26"]);
const allPts = v.filterChartSeries(daily, "all", { date: "2026-06-25", balance: 200 }, { date: "2014-09-22", balance: 100 });
assert.deepStrictEqual(allPts.map((p) => p[0]), ["2014-09-22", "2014-09-26", "2026-06-25", "2026-06-26"]);
// 1 年窗口不含 2014 最低点，且保持日频（不周抽样）
const y1 = v.filterChartSeries(daily, "1y", { date: "2026-06-25", balance: 200 }, { date: "2014-09-22", balance: 100 });
assert.deepStrictEqual(y1.map((p) => p[0]), ["2026-06-24", "2026-06-25", "2026-06-26"]);

const opt = v.chartOption({
  index_name: "沪深300",
  series: daily,
  hist_high: { date: "2026-06-25", balance: 200, index_close: 4120.28 },
  hist_low: { date: "2014-09-22", balance: 100, index_close: 2289.87 },
}, "all");
assert.strictEqual(opt.yAxis[0].name, "融资余额(亿元)");
assert.strictEqual(opt.yAxis[1].name, "沪深300");
assert.strictEqual(opt.series[0].yAxisIndex, 0);
assert.strictEqual(opt.series[1].yAxisIndex, 1);
assert.strictEqual(opt.series[0].connectNulls, false);
assert.strictEqual(opt.series[1].connectNulls, false);
const markNames = (opt.series[0].markPoint.data || []).map((m) => m.name);
assert.ok(markNames.includes("历史最高") && markNames.includes("历史最低"));
const tip = opt.tooltip.formatter([{ dataIndex: allPts.findIndex((p) => p[0] === "2026-06-25") }]);
assert.ok(tip.includes("2026-06-25") && tip.includes("融资余额") && tip.includes("沪深300") && tip.includes("200") && tip.includes("4120.28"));
// 缺指数当天 tooltip 显示无数据，不插补
const missing = v.chartOption({
  index_name: "上证指数",
  series: [["2026-06-25", 200, null]],
  hist_high: { date: "2026-06-25", balance: 200 },
  hist_low: null,
}, "1y");
const missTip = missing.tooltip.formatter([{ dataIndex: 0 }]);
assert.ok(missTip.includes("无数据") && missTip.includes("上证指数"));
// 1 年范围不标窗口外最低点
const y1opt = v.chartOption({
  index_name: "沪深300",
  series: daily,
  hist_high: { date: "2026-06-25", balance: 200 },
  hist_low: { date: "2014-09-22", balance: 100 },
}, "1y");
assert.deepStrictEqual((y1opt.series[0].markPoint.data || []).map((m) => m.name), ["历史最高"]);

// 两融区：新浪口径主力净流入表 + 图上可选柱
const flowB = JSON.parse(JSON.stringify(histB));
flowB.main_flow_definition = "主力净流入=特大单（r0）净额，为新浪按成交单规模划分的估算口径，不是交易所官方数据；与东方财富「主力=超大单+大单」不同。";
flowB.market.main_sh = {
  status: "ok", asof: "2026-11-13", net_1d: -82.5, net_5d: 409.4, net_20d: 2366.6,
  source: "新浪财经 板块资金流向", source_url: "https://vip.stock.finance.sina.com.cn/moneyflow/",
};
flowB.market.main_sz = {
  status: "ok", asof: "2026-11-13", net_1d: -133.4, net_5d: 728.8, net_20d: 4069.5,
  source: "新浪财经 板块资金流向", source_url: "https://vip.stock.finance.sina.com.cn/moneyflow/",
};
flowB.market.main_total = {
  status: "ok", asof: "2026-11-13", net_1d: -215.9, net_5d: 1138.1, net_20d: 6436.1,
  source: "新浪财经 板块资金流向", source_url: "https://vip.stock.finance.sina.com.cn/moneyflow/",
};
const flowSum = v.summaryHtml(flowB);
assert.ok(flowSum.includes("主力净流入（新浪口径）"));
assert.ok(flowSum.includes("特大单") && flowSum.includes("不是交易所官方数据"));
assert.ok(flowSum.includes("当日(亿元)") && flowSum.includes("5日累计") && flowSum.includes("20日累计"));
assert.ok(flowSum.includes("沪市") && flowSum.includes("深市") && flowSum.includes("两市合计"));
assert.ok(flowSum.includes('class="down">-82.5') && flowSum.includes('class="down">-133.4') && flowSum.includes('class="down">-215.9'));
assert.ok(flowSum.includes('class="up">+409.4') && flowSum.includes('class="up">+1138.1'));
assert.ok(flowSum.includes("叠加主力净流入") && flowSum.includes("data-flow-toggle"));
assert.ok(flowSum.includes("href=\"https://vip.stock.finance.sina.com.cn/moneyflow/\""));
// 缺数据不编造
const noFlow = v.summaryHtml(histB);
assert.ok(noFlow.includes("主力净流入（新浪口径）"));
assert.ok((noFlow.match(/无数据/g) || []).length >= 3);

const flowSnap = JSON.parse(JSON.stringify(flowB));
flowSnap.main_flow_from_snapshot = true;
flowSnap.main_flow_snapshot_asof = "2026-11-13";
flowSnap.market.main_sh.from_snapshot = true;
assert.ok(v.summaryHtml(flowSnap).includes("主力净流入为上次快照（2026-11-13）"));

const flowSeries = [
  ["2026-06-24", 10.0],
  ["2026-06-25", -20.0],
  ["2026-06-26", 5.0],
];
const flowOpt = v.chartOption({
  index_name: "沪深300",
  series: daily,
  hist_high: { date: "2026-06-25", balance: 200 },
  hist_low: { date: "2014-09-22", balance: 100 },
}, "1y", { series: flowSeries }, true);
assert.ok(flowOpt.series.length === 3 && flowOpt.series[2].type === "bar");
assert.strictEqual(flowOpt.series[2].yAxisIndex, 2);
assert.strictEqual(flowOpt.yAxis[2].name, "主力净流入(亿元)");
const flowTip = flowOpt.tooltip.formatter([{ dataIndex: 1, axisValue: "2026-06-25", seriesName: "融资余额" }]);
assert.ok(flowTip.includes("主力净流入") && flowTip.includes("-20") && flowTip.includes("2026-06-25"));
const hidden = v.chartOption({
  index_name: "沪深300",
  series: daily,
  hist_high: { date: "2026-06-25", balance: 200 },
  hist_low: { date: "2014-09-22", balance: 100 },
}, "1y", { series: flowSeries }, false);
assert.strictEqual(hidden.series.length, 2);
assert.ok(!hidden.tooltip.formatter([{ dataIndex: 1, axisValue: "2026-06-25", seriesName: "融资余额" }]).includes("主力净流入"));
