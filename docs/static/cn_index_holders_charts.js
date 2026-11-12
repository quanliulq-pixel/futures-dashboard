/* A股指数：机构ETF持仓 vs 指数涨跌（交互图，只展示不计分）。
 * 浏览器: window.cnIndexHoldersCharts；Node 测试: module.exports。
 * 红=涨/增，绿=跌/减。缺历史一律「无数据」，不编造。
 */
(function (root) {
  "use strict";

  const NO_DATA = "无数据";
  const UP = "#ef5350";
  const DOWN = "#26a69a";
  const FLAT = "#8b9bb0";
  const AXIS = "#8b9bb0";
  const TIP_BG = "#16202c";
  const TIP_FG = "#e5efff";
  const MARK = "2026-06-30";
  const INDEX_COLORS = {
    csi300: "#7eb6ff",
    sse50: "#c5a3ff",
    csi500: "#f0c674",
    csi1000: "#ffa07a",
    star50: "#9ad9c7",
    chinext: "#f5a6c8",
  };

  let _charts = [];
  let _ro = null;
  let _host = null;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function num(v) {
    if (v == null || v === "") return null;
    const x = Number(v);
    return isFinite(x) ? x : null;
  }

  function signedPct(v, n) {
    const x = num(v);
    if (x == null) return NO_DATA;
    const d = n == null ? 2 : n;
    return `${x > 0 ? "+" : ""}${(x * 100).toFixed(d)}%`;
  }

  function signedPp(v, n) {
    const x = num(v);
    if (x == null) return NO_DATA;
    const d = n == null ? 2 : n;
    return `${x > 0 ? "+" : ""}${x.toFixed(d)}`;
  }

  function barColor(v) {
    const x = num(v);
    if (x == null || x === 0) return FLAT;
    return x > 0 ? UP : DOWN;
  }

  function hasSeries(history) {
    if (!history || history.status === "missing") return false;
    const indices = history.indices || {};
    return Object.keys(indices).some((id) => (indices[id] && (indices[id].series || []).length));
  }

  function orderOf(history) {
    const fallback = ["csi300", "sse50", "csi500", "csi1000", "star50", "chinext"];
    const o = history && Array.isArray(history.order) && history.order.length ? history.order : fallback;
    return o.filter((id) => history && history.indices && history.indices[id]);
  }

  function seriesOf(history, indexId) {
    const rec = history && history.indices ? history.indices[indexId] : null;
    return rec && Array.isArray(rec.series) ? rec.series : [];
  }

  function allPoints(history, mode) {
    const out = [];
    orderOf(history).forEach((id) => {
      const rec = history.indices[id];
      (rec.series || []).forEach((row) => {
        const x = num(row.d_inst_pct);
        const y = mode === "next" ? num(row.next_ret) : num(row.same_ret);
        if (x == null || y == null) return;
        if (mode === "next" && row.next_ret_kind !== "full") return;
        out.push({
          id,
          name: rec.name || id,
          report_date: row.report_date,
          period: row.period,
          x,
          y,
          isH1: row.report_date === MARK || row.period === "2026H1",
        });
      });
    });
    return out;
  }

  function axisStyle() {
    return {
      axisLine: { lineStyle: { color: AXIS } },
      axisTick: { lineStyle: { color: AXIS } },
      axisLabel: { color: AXIS, fontSize: 10, hideOverlap: true },
      splitLine: { lineStyle: { color: "rgba(139,155,176,0.18)" } },
      nameTextStyle: { color: AXIS, fontSize: 11 },
    };
  }

  function tooltipBase() {
    return {
      trigger: "axis",
      backgroundColor: TIP_BG,
      borderColor: "transparent",
      textStyle: { color: TIP_FG, fontSize: 11 },
    };
  }

  function markLine2026() {
    return {
      symbol: "none",
      label: { color: AXIS, fontSize: 10, formatter: "2026-06-30" },
      lineStyle: { type: "dashed", color: "#f0c674", width: 1.2 },
      data: [{ xAxis: MARK }],
    };
  }

  function timeseriesOption(rows, metric) {
    const usePct = metric === "pct";
    const dates = (rows || []).map((r) => r.report_date);
    const left = (rows || []).map((r) => (usePct ? num(r.inst_pct) : num(r.inst_shares)));
    const close = (rows || []).map((r) => num(r.index_close));
    const leftName = usePct ? "机构占比（%）" : "机构份额（亿份）";
    return {
      backgroundColor: "transparent",
      animation: true,
      legend: { data: [leftName, "指数收盘"], textStyle: { color: AXIS, fontSize: 11 }, top: 2, right: 8 },
      grid: { left: 52, right: 52, top: 36, bottom: 36 },
      tooltip: Object.assign(tooltipBase(), {
        formatter: (items) => {
          if (!items || !items.length) return "";
          const i = items[0].dataIndex;
          const row = rows[i] || {};
          const lv = usePct
            ? (num(row.inst_pct) == null ? NO_DATA : `${num(row.inst_pct).toFixed(2)}%`)
            : (num(row.inst_shares) == null ? NO_DATA : `${num(row.inst_shares).toFixed(2)}亿份`);
          const ix = num(row.index_close) == null ? NO_DATA : num(row.index_close).toFixed(2);
          const ixd = row.index_close_date || row.report_date || "";
          return `<b>${esc(row.report_date || "")}</b>（${esc(row.period || "")}）<br/>` +
            `${esc(leftName)}：${esc(lv)}<br/>指数收盘：${esc(ix)}<span style="color:${AXIS}">（${esc(ixd)}）</span>`;
        },
      }),
      xAxis: Object.assign({ type: "category", data: dates, boundaryGap: false }, axisStyle()),
      yAxis: [
        Object.assign({ type: "value", name: leftName, scale: true, splitNumber: 4 }, axisStyle()),
        Object.assign({ type: "value", name: "指数收盘", scale: true, splitNumber: 4, splitLine: { show: false } }, axisStyle()),
      ],
      series: [
        {
          name: leftName,
          type: "line",
          yAxisIndex: 0,
          data: left,
          showSymbol: true,
          symbolSize: (v, p) => ((rows[p.dataIndex] || {}).report_date === MARK ? 10 : 6),
          itemStyle: { color: "#7eb6ff" },
          lineStyle: { width: 2, color: "#7eb6ff" },
          markLine: markLine2026(),
        },
        {
          name: "指数收盘",
          type: "line",
          yAxisIndex: 1,
          data: close,
          showSymbol: true,
          symbolSize: (v, p) => ((rows[p.dataIndex] || {}).report_date === MARK ? 10 : 6),
          itemStyle: { color: "#c5a3ff" },
          lineStyle: { width: 2, color: "#c5a3ff" },
        },
      ],
    };
  }

  function hatchDecal() {
    return {
      symbol: "rect",
      symbolSize: 1,
      rotation: Math.PI / 4,
      dashArrayX: [1, 0],
      dashArrayY: [3, 2],
      color: "rgba(255,255,255,0.45)",
    };
  }

  function barsOption(rows) {
    const pts = (rows || []).filter((r) => num(r.d_inst_pct) != null && num(r.same_ret) != null);
    const dates = pts.map((r) => r.report_date);
    const dPct = pts.map((r) => num(r.d_inst_pct));
    const same = pts.map((r) => {
      const v = num(r.same_ret);
      return v == null ? null : v * 100;
    });
    return {
      backgroundColor: "transparent",
      animation: true,
      legend: {
        data: ["重叠篮子占比变化（百分点）", "同期指数涨跌（%）"],
        textStyle: { color: AXIS, fontSize: 11 },
        top: 2,
        right: 8,
      },
      grid: { left: 48, right: 16, top: 36, bottom: 36 },
      tooltip: Object.assign(tooltipBase(), {
        formatter: (items) => {
          if (!items || !items.length) return "";
          const i = items[0].dataIndex;
          const row = pts[i] || {};
          const h1 = row.report_date === MARK ? "<br/><span style=\"color:#f0c674\">2026H1（反向样本）</span>" : "";
          return `<b>${esc(row.report_date || "")}</b>（${esc(row.period || "")}）<br/>` +
            `占比变化：${esc(signedPp(row.d_inst_pct))} 个百分点<br/>` +
            `同期涨跌：${esc(signedPct(row.same_ret))}${h1}`;
        },
      }),
      xAxis: Object.assign({ type: "category", data: dates }, axisStyle()),
      yAxis: Object.assign({ type: "value", name: "百分点 / %", scale: true }, axisStyle()),
      series: [
        {
          name: "重叠篮子占比变化（百分点）",
          type: "bar",
          data: dPct.map((v, i) => ({
            value: v,
            itemStyle: {
              color: barColor(v),
              borderColor: pts[i].report_date === MARK ? "#f0c674" : "transparent",
              borderWidth: pts[i].report_date === MARK ? 2 : 0,
            },
          })),
        },
        {
          name: "同期指数涨跌（%）",
          type: "bar",
          data: same.map((v, i) => ({
            value: v,
            itemStyle: {
              color: barColor(v),
              borderColor: pts[i].report_date === MARK ? "#f0c674" : "transparent",
              borderWidth: pts[i].report_date === MARK ? 2 : 0,
              decal: hatchDecal(),
            },
          })),
        },
      ],
    };
  }

  function scatterOption(points, mode, stats) {
    const groups = {};
    (points || []).forEach((p) => {
      (groups[p.id] || (groups[p.id] = [])).push(p);
    });
    const ids = Object.keys(groups);
    const yName = mode === "next" ? "下一完整半年涨跌（%）" : "同期指数涨跌（%）";
    const series = ids.map((id) => {
      const pts = groups[id];
      const name = (pts[0] && pts[0].name) || id;
      return {
        name,
        type: "scatter",
        data: pts.map((p) => ({
          value: [p.x, p.y * 100],
          point: p,
          symbol: p.isH1 ? "diamond" : "circle",
          symbolSize: p.isH1 ? 12 : 8,
          itemStyle: {
            color: INDEX_COLORS[id] || "#7eb6ff",
            borderColor: p.isH1 ? "#e5efff" : "transparent",
            borderWidth: p.isH1 ? 1.5 : 0,
          },
        })),
      };
    });
    const block = mode === "next"
      ? (stats && stats.next_period && stats.next_period.pooled)
      : (stats && stats.same_period && stats.same_period.pooled);
    return {
      backgroundColor: "transparent",
      animation: true,
      legend: { type: "scroll", textStyle: { color: AXIS, fontSize: 11 }, top: 2, right: 8 },
      grid: { left: 52, right: 16, top: 36, bottom: 42 },
      tooltip: {
        trigger: "item",
        backgroundColor: TIP_BG,
        borderColor: "transparent",
        textStyle: { color: TIP_FG, fontSize: 11 },
        formatter: (item) => {
          const p = item && item.data && item.data.point;
          if (!p) return "";
          const yv = mode === "next" ? "下一期" : "同期";
          const h1 = p.isH1 ? "<br/><span style=\"color:#f0c674\">2026H1</span>" : "";
          return `<b>${esc(p.name)}</b> ${esc(p.report_date || "")}<br/>` +
            `占比变化：${esc(signedPp(p.x))} 个百分点<br/>${esc(yv)}涨跌：${esc(signedPct(p.y))}${h1}`;
        },
      },
      xAxis: Object.assign({ type: "value", name: "重叠篮子占比变化（百分点）", scale: true }, axisStyle()),
      yAxis: Object.assign({ type: "value", name: yName, scale: true }, axisStyle()),
      series,
      _caption: block
        ? (mode === "next"
          ? `下一完整半年合样本 Pearson r=${signedPp(block.display_r != null ? block.display_r : block.pearson_r, 3)}（p=${block.display_p != null ? block.display_p : (num(block.pearson_p) != null ? num(block.pearson_p).toFixed(2) : "—")}，n=${block.n}）；未完结窗口已排除。`
          : `合样本同期 Pearson r=${signedPp(block.display_r != null ? block.display_r : block.pearson_r, 3)}（p=${block.display_p != null ? block.display_p : (num(block.pearson_p) != null ? num(block.pearson_p).toFixed(2) : "—")}，n=${block.n}，同向 ${block.same_dir || "—"}/${block.n_dir || block.n}）。相关很弱。`)
        : "",
    };
  }

  function conclusionHtml(history) {
    const notes = (history && history.notes) || [];
    if (!notes.length) return "";
    return `<ul class="cnhold-notes">${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`;
  }

  function panelHtml(history) {
    if (!hasSeries(history)) {
      return `<div class="cnhold-wrap"><div class="cnhold-h">机构持仓 vs 指数涨跌</div>` +
        `<div class="cnhold-miss">${NO_DATA}（快照未包含持仓对照历史）</div></div>`;
    }
    const order = orderOf(history);
    const btns = order.map((id, i) => {
      const name = (history.indices[id] && history.indices[id].name) || id;
      return `<button type="button" class="cnhold-btn${i === 0 ? " active" : ""}" data-index="${esc(id)}">${esc(name)}</button>`;
    }).join("");
    const stats = (history && history.stats) || {};
    const same = stats.same_period || {};
    const pooled = same.pooled || {};
    const avg = same.period_avg || {};
    const nxt = (stats.next_period || {}).period_avg || {};
    const cap = (pooled.n
      ? `合样本同期 r=+${Number(pooled.display_r != null ? pooled.display_r : pooled.pearson_r).toFixed(3)}（p=${pooled.display_p != null ? pooled.display_p : "—"}，n=${pooled.n}）；半年等权平均 r=+${Number(avg.display_r != null ? avg.display_r : avg.pearson_r).toFixed(3)}（p=${avg.display_p != null ? avg.display_p : "—"}，n=${avg.n}）。下一完整半年等权平均 r=+${Number(nxt.display_r != null ? nxt.display_r : nxt.pearson_r).toFixed(3)}（p=${nxt.display_p != null ? nxt.display_p : "—"}，n=${nxt.n}），仅略偏正。`
      : "");
    return `<div class="cnhold-wrap">` +
      `<div class="cnhold-h">机构持仓 vs 指数涨跌<em>半年报/年报 · 只展示不计分</em></div>` +
      `<div class="cnhold-switch" role="tablist" aria-label="选择指数">${btns}</div>` +
      `<div class="cnhold-toolbar">` +
        `<span class="cnhold-lab">左轴</span>` +
        `<button type="button" class="cnhold-btn active" data-metric="shares">机构份额（亿份）</button>` +
        `<button type="button" class="cnhold-btn" data-metric="pct">机构占比（%）</button>` +
      `</div>` +
      `<div class="cnhold-chart" id="cnhold-ts" role="img" aria-label="机构持仓与指数收盘双轴图"></div>` +
      `<p class="cnhold-cap">双轴图左右刻度不同，看起来同向并不代表相关；请以下方柱状图、散点图和相关系数为准。竖虚线为 2026-06-30。</p>` +
      `<div class="cnhold-chart" id="cnhold-bars" role="img" aria-label="占比变化与同期涨跌对照柱状图"></div>` +
      `<p class="cnhold-cap">实心柱为重叠篮子机构占比变化（百分点），斜线柱为同期指数涨跌（%）。红=涨/增，绿=跌/减。2026H1 以金色描边标出。</p>` +
      `<div class="cnhold-toolbar">` +
        `<span class="cnhold-lab">散点纵轴</span>` +
        `<button type="button" class="cnhold-btn active" data-scatter="same">同期涨跌</button>` +
        `<button type="button" class="cnhold-btn" data-scatter="next">下一完整半年（不含未完结窗口）</button>` +
      `</div>` +
      `<div class="cnhold-chart" id="cnhold-scatter" role="img" aria-label="占比变化与指数涨跌散点图"></div>` +
      `<p class="cnhold-cap" id="cnhold-scatter-cap">${esc(cap)}</p>` +
      conclusionHtml(history) +
      `</div>`;
  }

  function dispose() {
    _charts.forEach((ch) => {
      try { ch.dispose(); } catch (e) { /* ignore */ }
    });
    _charts = [];
    if (_ro) {
      try { _ro.disconnect(); } catch (e) { /* ignore */ }
      _ro = null;
    }
  }

  function resize() {
    _charts.forEach((ch) => {
      try { ch.resize(); } catch (e) { /* ignore */ }
    });
  }

  function _initChart(el, option) {
    if (!el || typeof root.echarts === "undefined" || !root.echarts.init) return null;
    const ch = root.echarts.init(el, null, { renderer: "canvas" });
    ch.setOption(option, true);
    _charts.push(ch);
    return ch;
  }

  function _currentState(host) {
    const indexBtn = host.querySelector(".cnhold-switch .cnhold-btn.active");
    const metricBtn = host.querySelector("[data-metric].active");
    const scatterBtn = host.querySelector("[data-scatter].active");
    return {
      indexId: indexBtn ? indexBtn.getAttribute("data-index") : "csi300",
      metric: metricBtn ? metricBtn.getAttribute("data-metric") : "shares",
      scatter: scatterBtn ? scatterBtn.getAttribute("data-scatter") : "same",
    };
  }

  function renderCharts(host, history) {
    if (!hasSeries(history)) return;
    const st = _currentState(host);
    const rows = seriesOf(history, st.indexId);
    dispose();
    _host = host;
    const tsEl = host.querySelector("#cnhold-ts");
    const barEl = host.querySelector("#cnhold-bars");
    const scEl = host.querySelector("#cnhold-scatter");
    _initChart(tsEl, timeseriesOption(rows, st.metric));
    _initChart(barEl, barsOption(rows));
    const pts = allPoints(history, st.scatter);
    const scOpt = scatterOption(pts, st.scatter, history.stats || {});
    _initChart(scEl, scOpt);
    const cap = host.querySelector("#cnhold-scatter-cap");
    if (cap && scOpt._caption) cap.textContent = scOpt._caption;
    if (typeof root.ResizeObserver !== "undefined") {
      _ro = new root.ResizeObserver(() => resize());
      [tsEl, barEl, scEl].forEach((el) => { if (el) _ro.observe(el); });
    }
  }

  function bind(host, history) {
    if (!host || host._cnholdBound) return;
    host._cnholdBound = true;
    host.addEventListener("click", (ev) => {
      const btn = ev.target && ev.target.closest && ev.target.closest(".cnhold-btn");
      if (!btn || !host.contains(btn)) return;
      if (btn.dataset.index) {
        host.querySelectorAll(".cnhold-switch .cnhold-btn").forEach((b) => b.classList.toggle("active", b === btn));
      } else if (btn.dataset.metric) {
        host.querySelectorAll("[data-metric]").forEach((b) => b.classList.toggle("active", b === btn));
      } else if (btn.dataset.scatter) {
        host.querySelectorAll("[data-scatter]").forEach((b) => b.classList.toggle("active", b === btn));
      } else {
        return;
      }
      renderCharts(host, history);
    });
  }

  function mount(el, history) {
    dispose();
    if (!el) return;
    el.innerHTML = panelHtml(history);
    if (!hasSeries(history)) return;
    bind(el, history);
    renderCharts(el, history);
  }

  const api = {
    NO_DATA: NO_DATA,
    UP: UP,
    DOWN: DOWN,
    esc: esc,
    barColor: barColor,
    hasSeries: hasSeries,
    seriesOf: seriesOf,
    allPoints: allPoints,
    timeseriesOption: timeseriesOption,
    barsOption: barsOption,
    scatterOption: scatterOption,
    panelHtml: panelHtml,
    mount: mount,
    dispose: dispose,
    resize: resize,
  };
  root.cnIndexHoldersCharts = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
