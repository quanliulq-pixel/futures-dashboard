// node tests/cn_index_holders_charts.test.js
const assert = require("assert");
const v = require("../static/cn_index_holders_charts.js");

assert.strictEqual(v.barColor(1), "#ef5350", "red = up/increase");
assert.strictEqual(v.barColor(-1), "#26a69a", "green = down/decrease");
assert.strictEqual(v.barColor(0), "#8b9bb0");
assert.strictEqual(v.UP, "#ef5350");
assert.strictEqual(v.DOWN, "#26a69a");

assert.ok(v.panelHtml(null).includes("无数据"));
assert.ok(v.panelHtml({ status: "missing", indices: {} }).includes("无数据"));
assert.ok(!v.hasSeries(null));
assert.ok(!v.hasSeries({ indices: { csi300: { series: [] } } }));

const history = {
  status: "ok",
  order: ["csi300", "sse50"],
  mark_date: "2026-06-30",
  notes: ["没有稳定关系", "不能用来证明「减持预示下跌」"],
  stats: {
    same_period: { pooled: { n: 154, display_r: 0.074, display_p: 0.36, same_dir: 87, n_dir: 154, pearson_r: 0.074 }, period_avg: { n: 42, display_r: 0.087, display_p: 0.58, pearson_r: 0.087 } },
    next_period: { exclude_unfinished: true, period_avg: { n: 41, display_r: 0.307, display_p: 0.051, pearson_r: 0.307 }, pooled: { n: 148, display_r: 0.133, display_p: 0.11, pearson_r: 0.133 } },
  },
  indices: {
    csi300: {
      id: "csi300", name: "沪深300",
      series: [
        { report_date: "2025-12-31", period: "2025H2", inst_shares: 2317.64, inst_pct: 93.88, index_close: 4629.94, d_inst_pct: null, same_ret: null, next_ret: 0.0755, next_ret_kind: "full" },
        { report_date: "2026-06-30", period: "2026H1", inst_shares: 348.75, inst_pct: 72.76, index_close: 4979.43, d_inst_pct: -21.12, d_inst_shares: -1968.88, same_ret: 0.0755, next_ret: -0.1815, next_ret_kind: "partial", next_end_date: "2026-11-11" },
      ],
    },
    sse50: {
      id: "sse50", name: "上证50",
      series: [
        { report_date: "2025-12-31", period: "2025H2", inst_shares: 533.53, inst_pct: 91.16, index_close: 100, d_inst_pct: 1, same_ret: 0.02, next_ret: -0.0138, next_ret_kind: "full" },
        { report_date: "2026-06-30", period: "2026H1", inst_shares: 30.39, inst_pct: 39.41, index_close: 98.62, d_inst_pct: -51.75, same_ret: -0.0138, next_ret: -0.0749, next_ret_kind: "partial" },
      ],
    },
  },
};

const html = v.panelHtml(history);
assert.ok(html.includes("沪深300") && html.includes("上证50"));
assert.ok(html.includes("data-index=\"csi300\""));
assert.ok(html.includes("机构份额（亿份）") && html.includes("机构占比"));
assert.ok(html.includes("下一完整半年"));
assert.ok(html.includes("没有稳定关系"));
assert.ok(html.includes("减持预示下跌"));
assert.ok(html.includes("+0.074") && html.includes("n=154"));
assert.ok(html.includes("&lt;") || !html.includes("<b>注入")); // notes escaped if any tags
assert.ok(!html.includes("无数据") || html.includes("机构持仓 vs 指数涨跌"));

const ts = v.timeseriesOption(history.indices.csi300.series, "shares");
assert.strictEqual(ts.backgroundColor, "transparent");
assert.strictEqual(ts.tooltip.backgroundColor, "#16202c");
assert.strictEqual(ts.tooltip.textStyle.color, "#e5efff");
assert.strictEqual(ts.xAxis.axisLabel.color, "#8b9bb0");
assert.strictEqual(ts.yAxis.length, 2);
assert.ok(ts.series[0].markLine.data.some((d) => d.xAxis === "2026-06-30"));
assert.deepStrictEqual(ts.xAxis.data, ["2025-12-31", "2026-06-30"]);
assert.strictEqual(ts.series[0].data[1], 348.75);

const tsPct = v.timeseriesOption(history.indices.csi300.series, "pct");
assert.ok(tsPct.series[0].name.includes("占比"));
assert.strictEqual(tsPct.series[0].data[1], 72.76);

const bars = v.barsOption(history.indices.csi300.series);
// first period has no d_inst_pct — dropped
assert.deepStrictEqual(bars.xAxis.data, ["2026-06-30"]);
assert.strictEqual(bars.series[0].data[0].itemStyle.color, "#26a69a"); // 占比下降 = 绿
assert.strictEqual(bars.series[1].data[0].itemStyle.color, "#ef5350"); // 指数上涨 = 红
assert.ok(bars.series[1].data[0].itemStyle.decal, "index return bars are hatched");
assert.ok(!bars.series[0].data[0].itemStyle.decal, "change bars stay solid");
assert.strictEqual(bars.series[0].data[0].itemStyle.borderColor, "#f0c674");

const samePts = v.allPoints(history, "same");
assert.strictEqual(samePts.length, 3); // csi 2026H1 + sse 2025H2 and 2026H1
assert.ok(samePts.some((p) => p.isH1 && p.id === "csi300"));
const nextPts = v.allPoints(history, "next");
assert.ok(nextPts.every((p) => history.indices[p.id].series.find((r) => r.report_date === p.report_date).next_ret_kind === "full"));
assert.ok(!nextPts.some((p) => p.report_date === "2026-06-30"), "unfinished 2026H1 window excluded");
assert.ok(nextPts.some((p) => p.report_date === "2025-12-31"));

const sc = v.scatterOption(samePts, "same", history.stats);
assert.ok(sc.series.some((s) => s.data.some((d) => d.symbol === "diamond" && d.symbolSize === 12)));
assert.ok(sc._caption.includes("0.074") && sc._caption.includes("154"));

const scNext = v.scatterOption(nextPts, "next", history.stats);
assert.ok(scNext._caption.includes("未完结") || scNext._caption.includes("148") || scNext._caption.includes("下一"));

// notes escaped
const sneaky = { status: "ok", order: ["csi300"], indices: { csi300: { name: "沪深300", series: history.indices.csi300.series } }, notes: ["只展示<不计分>"] };
assert.ok(v.panelHtml(sneaky).includes("&lt;不计分&gt;"));

// dispose is safe without echarts
v.dispose();
v.resize();
assert.strictEqual(typeof v.mount, "function");

console.log("cn_index_holders_charts tests passed");
