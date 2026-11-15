/* A股指数资金进出：主力净流入 / ETF 申赎（估算）/ 两融融资余额 / 北向 / 机构ETF持仓（只展示，不计入评分）。
 * 浏览器: window.cnIndexFlowView；Node 测试: module.exports。
 * 红=流入/增加，绿=流出/减少（沿用 .up/.down）。缺数据一律显示「无数据」，前端不补算、不插补。
 */
(function (root) {
  "use strict";

  const NO_DATA = "无数据";

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

  function cls(v) {
    const x = num(v);
    if (x == null) return "nodata";
    return x > 0 ? "up" : x < 0 ? "down" : "flat";
  }

  /** "+12.34" / "-3.20" / 无数据 — unit is shown by the caller. */
  function signed(v, n) {
    const x = num(v);
    if (x == null) return NO_DATA;
    return `${x > 0 ? "+" : ""}${x.toFixed(n == null ? 2 : n)}`;
  }

  function cell(v, n) {
    return `<span class="${cls(v)}">${esc(signed(v, n))}</span>`;
  }

  function safeUrl(u) {
    return /^https?:\/\//i.test(String(u || ""));
  }

  function srcLink(o) {
    if (!o || !o.source) return "";
    return safeUrl(o.source_url)
      ? `<a href="${esc(o.source_url)}" target="_blank" rel="noopener noreferrer">${esc(o.source)}</a>`
      : esc(o.source);
  }

  function ok(o) {
    return !!o && o.status === "ok";
  }

  function reasonTxt(o) {
    return o && o.reason ? `（${esc(o.reason)}）` : "";
  }

  function fromSnapshot(o) {
    return !!(o && (o.from_snapshot || o.from_seed));
  }

  /** Short note when 主力 is served from the last snapshot rather than a live push2 refresh. */
  function snapNote(o) {
    if (!fromSnapshot(o)) return "";
    const asof = o && o.asof ? String(o.asof) : "";
    return asof ? `上次快照 ${esc(asof)}` : "上次快照";
  }

  function dataSrcLine(o) {
    const bits = [];
    if (o && o.asof) bits.push("数据 " + esc(o.asof));
    if (fromSnapshot(o)) bits.push("上次快照");
    const src = srcLink(o);
    if (src) bits.push(src);
    return bits.length ? `<div class="cnflow-src">${bits.join(" · ")}</div>` : "";
  }

  /** Per-index block inside an A股指数 card. */
  function cardHtml(bundle, indexId) {
    const row = bundle && bundle.indices ? bundle.indices[indexId] : null;
    if (!row) {
      return `<div class="cnflow-card"><div class="cnflow-h">资金进出（亿元）</div><div class="cnflow-miss">${NO_DATA}（快照未包含资金流数据）</div></div>`;
    }
    const m = row.main || {};
    const e = row.etf || {};
    const h = row.holders || {};
    const mainLine = ok(m)
      ? `<div class="cnflow-row"><span class="l">主力净流入</span>今日 ${cell(m.net_1d)} · 5日 ${cell(m.net_5d)} · 20日 ${cell(m.net_20d)}</div>` +
        dataSrcLine(m)
      : `<div class="cnflow-row"><span class="l">主力净流入</span>${NO_DATA}${reasonTxt(m)}</div>`;
    const etfLine = ok(e)
      ? `<div class="cnflow-row"><span class="l">ETF净申赎<em>估算</em></span>1日 ${cell(e.net_1d)} · 5日 ${cell(e.net_5d)} · 20日 ${cell(e.net_20d)}</div>` +
        `<div class="cnflow-src">数据 ${esc(e.asof || "—")}${fromSnapshot(e) ? " · 上次快照" : ""} · ${esc(e.coverage || "")} · ${(e.items || []).filter(ok).map((i) => esc(i.code)).join("/")} · ${srcLink(e)}</div>`
      : `<div class="cnflow-row"><span class="l">ETF净申赎</span>${NO_DATA}${reasonTxt(e)}</div>`;
    let holdersLine;
    if (ok(h)) {
      const shares = h.inst_shares == null ? NO_DATA : `${Number(h.inst_shares).toFixed(2)}亿份`;
      const pct = h.inst_pct == null ? "" : `（占${Number(h.inst_pct).toFixed(2)}%）`;
      const chg = (h.chg_shares == null && h.chg_pct_pt == null)
        ? ` · 较上期 ${NO_DATA}`
        : ` · 较上期${h.prev_date ? " " + esc(h.prev_date) : ""} ${cell(h.chg_shares)}亿份 / ${cell(h.chg_pct_pt)}个百分点`;
      const codes = (h.codes && h.codes.length) ? h.codes.map(esc).join("/") : esc(h.coverage || "");
      holdersLine =
        `<div class="cnflow-row"><span class="l">机构ETF持仓<em>半年报/年报</em></span>${esc(shares)}${esc(pct)}</div>` +
        `<div class="cnflow-src">报告期 ${esc(h.report_date || "—")}${chg}</div>` +
        `<div class="cnflow-src">${codes} · ${srcLink(h)}</div>`;
    } else {
      holdersLine = `<div class="cnflow-row"><span class="l">机构ETF持仓<em>半年报/年报</em></span>${NO_DATA}${reasonTxt(h)}</div>`;
    }
    return `<div class="cnflow-card"><div class="cnflow-h">资金进出（亿元，红=流入 绿=流出，仅展示不计分）</div>${mainLine}${etfLine}${holdersLine}</div>`;
  }

  /** 亿元：大额 1 位小数，起步阶段的极小值保留 4 位，避免最低点显示成 0.0。 */
  function fmtYi(v) {
    const x = num(v);
    if (x == null) return null;
    if (Math.abs(x) >= 10) return x.toFixed(1);
    if (Math.abs(x) >= 1) return x.toFixed(2);
    return x.toFixed(4);
  }

  function histBalanceCell(pt) {
    if (!pt || pt.balance == null) return `<td class="num">${NO_DATA}</td>`;
    const d = pt.date ? `<span class="cnflow-hist">${esc(pt.date)}</span>` : "";
    return `<td class="num">${esc(fmtYi(pt.balance))}${d}</td>`;
  }

  function histIndexCell(pt) {
    const close = pt && pt.index_close;
    if (close == null) return `<td class="num">${NO_DATA}</td>`;
    const name = pt.index_name ? `<span class="cnflow-hist">${esc(pt.index_name)}</span>` : "";
    return `<td class="num">${esc(Number(close).toFixed(2))}${name}</td>`;
  }

  function indexNowCell(o) {
    const now = o && o.index_now;
    if (!now || now.close == null) return `<td class="num">${NO_DATA}</td>`;
    const name = now.index_name || (o && o.index_name) || "";
    const date = now.date ? `<span class="cnflow-hist">${esc(now.date)}</span>` : "";
    const parts = [];
    function vs(pt, label) {
      const cur = num(now.close);
      const ref = pt ? num(pt.index_close) : null;
      if (cur == null || ref == null || ref === 0) return;
      parts.push(`<span class="cnflow-hist">${esc(label)} ${cell((cur / ref - 1) * 100, 1)}%</span>`);
    }
    vs(o && o.hist_high, "较高日");
    vs(o && o.hist_low, "较低日");
    return `<td class="num">${esc(name ? name + " " : "")}${esc(Number(now.close).toFixed(2))}${date}${parts.join("")}</td>`;
  }

  function histCols(o) {
    return histBalanceCell(o && o.hist_high) + histIndexCell(o && o.hist_high) +
      histBalanceCell(o && o.hist_low) + histIndexCell(o && o.hist_low) + indexNowCell(o);
  }

  function histSnapNote(o) {
    if (!o || !o.hist_from_snapshot) return "";
    return o.hist_snapshot_asof ? `上次快照（${esc(o.hist_snapshot_asof)}）` : "上次快照";
  }

  function marginRow(label, o) {
    const hist = histCols(o);
    const snap = histSnapNote(o);
    const histSnap = snap ? `<span class="cnflow-hist">${snap}</span>` : "";
    if (!ok(o)) {
      return `<tr><td>${esc(label)}</td><td class="num" colspan="4">${NO_DATA}${reasonTxt(o)}</td>${hist}` +
        `<td>${srcLink(o)}${histSnap}</td></tr>`;
    }
    return `<tr><td>${esc(label)}</td><td class="num">${o.balance == null ? NO_DATA : esc(Number(o.balance).toFixed(1))}</td>` +
      `<td class="num">${cell(o.chg_1d, 1)}</td><td class="num">${cell(o.chg_5d, 1)}</td><td class="num">${cell(o.chg_20d, 1)}</td>` +
      hist +
      `<td>${esc(o.asof || "—")}${fromSnapshot(o) ? " · 上次快照" : ""}${o.source ? " · " + srcLink(o) : ""}${snap ? " · " + snap : ""}</td></tr>`;
  }

  /** Market summary + all-index table for the 资金进出 panel. */
  function summaryHtml(bundle) {
    if (!bundle || !bundle.indices) {
      return `<div class="empty-card">资金进出：${NO_DATA}（快照尚未包含，后台更新后显示）</div>`;
    }
    const mk = bundle.market || {};
    const mm = mk.main || {};
    const nb = mk.northbound || {};
    const mainSnap = fromSnapshot(mm) || !!(bundle && bundle.main_from_snapshot && ok(mm));
    const mainTxt = ok(mm)
      ? `今日 ${cell(mm.net_1d)} · 5日 ${cell(mm.net_5d)} · 20日 ${cell(mm.net_20d)} 亿元 <span class="cnflow-src">数据 ${esc(mm.asof || "—")}${mainSnap ? " · 上次快照" : ""}${mm.source ? " · " + srcLink(mm) : ""}</span>`
      : `${NO_DATA}${reasonTxt(mm)}`;
    const top = `<div class="cnflow-market">` +
      `<div><strong>沪深两市主力净流入</strong>：${mainTxt}</div>` +
      `<div><strong>北向资金净买额</strong>：${NO_DATA}${nb.reason ? `（${esc(nb.reason)}）` : ""}</div>` +
      `</div>`;
    const highStart = (mk.margin_sh && mk.margin_sh.hist_coverage_start)
      || (mk.margin_sz && mk.margin_sz.hist_coverage_start)
      || (mk.margin_total && mk.margin_total.hist_coverage_start)
      || bundle.margin_history_coverage_start || "2010-03-31";
    const lowStart = (mk.margin_sh && mk.margin_sh.hist_low_coverage_start)
      || (mk.margin_sz && mk.margin_sz.hist_low_coverage_start)
      || (mk.margin_total && mk.margin_total.hist_low_coverage_start)
      || bundle.margin_history_low_coverage_start || "2014-09-22";
    const rangeTxt = `历史最高区间自 ${highStart}；历史最低区间自 ${lowStart}（两融扩容后）`;
    const highTip = `融资余额历史最高（亿元）及该值首次出现的日期。历史最高区间自 ${highStart}。`;
    const lowTip = `融资余额历史最低（亿元）及该值首次出现的日期。历史最低区间自 ${lowStart}（两融扩容后，扩容至900只标的之日起计）。`;
    const highIdxTip = "历史最高日的对应指数收盘：上交所对照上证指数(000001)，深交所对照深证成指(399001)，沪深合计对照沪深300(000300)。缺当日收盘显示无数据，不拿邻日顶替。";
    const lowIdxTip = "历史最低日的对应指数收盘：上交所对照上证指数(000001)，深交所对照深证成指(399001)，沪深合计对照沪深300(000300)。缺当日收盘显示无数据，不拿邻日顶替。";
    const nowIdxTip = "最近一笔对应指数收盘，便于对照最高/最低日指数。上交所=上证指数，深交所=深证成指，沪深合计=沪深300。下方百分比为相对最高日、最低日指数的涨跌幅（红涨绿跌）。";
    const margin = `<table class="put-table cnflow-table"><thead><tr><th>两融·融资余额</th><th class="num">余额(亿元)</th>` +
      `<th class="num">1日变化</th><th class="num">5日变化</th><th class="num">20日变化</th>` +
      `<th class="num" title="${esc(highTip)}">历史最高(亿元)</th>` +
      `<th class="num" title="${esc(highIdxTip)}">最高日指数</th>` +
      `<th class="num" title="${esc(lowTip)}">历史最低(亿元)</th>` +
      `<th class="num" title="${esc(lowIdxTip)}">最低日指数</th>` +
      `<th class="num" title="${esc(nowIdxTip)}">当前指数</th>` +
      `<th>数据日期 · 来源</th></tr></thead><tbody>` +
      marginRow("上交所", mk.margin_sh) + marginRow("深交所", mk.margin_sz) + marginRow("沪深合计", mk.margin_total) +
      `</tbody></table>`;
    const order = Array.isArray(bundle.order) && bundle.order.length ? bundle.order : Object.keys(bundle.indices);
    const rows = order.map((id) => {
      const r = bundle.indices[id];
      if (!r) return "";
      const m = r.main || {};
      const e = r.etf || {};
      const d = [ok(m) ? m.asof : null, ok(e) ? e.asof : null].filter(Boolean);
      const dateTxt = d.length ? esc(Array.from(new Set(d)).join(" / ")) : NO_DATA;
      const snap = (ok(m) && fromSnapshot(m)) || (ok(e) && fromSnapshot(e)) ? " · 上次快照" : "";
      return `<tr><td><strong>${esc(r.name || id)}</strong>${r.tracked ? "" : ' <span class="tag-pill">总览</span>'}</td>` +
        `<td class="num">${ok(m) ? cell(m.net_1d) : NO_DATA}</td><td class="num">${ok(m) ? cell(m.net_5d) : NO_DATA}</td><td class="num">${ok(m) ? cell(m.net_20d) : NO_DATA}</td>` +
        `<td class="num">${ok(e) ? cell(e.net_1d) : NO_DATA}</td><td class="num">${ok(e) ? cell(e.net_5d) : NO_DATA}</td><td class="num">${ok(e) ? cell(e.net_20d) : NO_DATA}</td>` +
        `<td>${dateTxt}${snap}</td></tr>`;
    }).join("");
    const table = `<table class="put-table cnflow-table"><thead><tr><th>指数</th>` +
      `<th class="num">主力今日</th><th class="num">主力5日</th><th class="num">主力20日</th>` +
      `<th class="num">ETF申赎1日</th><th class="num">ETF申赎5日</th><th class="num">ETF申赎20日</th><th>数据日期</th></tr></thead>` +
      `<tbody>${rows}</tbody></table>`;
    const hrows = order.map((id) => {
      const r = bundle.indices[id];
      if (!r) return "";
      const h = r.holders || {};
      if (!ok(h)) {
        return `<tr><td><strong>${esc(r.name || id)}</strong></td><td colspan="6">${NO_DATA}${reasonTxt(h)}</td></tr>`;
      }
      const shares = h.inst_shares == null ? NO_DATA : Number(h.inst_shares).toFixed(2);
      const pct = h.inst_pct == null ? NO_DATA : `${Number(h.inst_pct).toFixed(2)}%`;
      const codes = (h.codes && h.codes.length) ? h.codes.map(esc).join("/") : esc(h.coverage || "");
      return `<tr><td><strong>${esc(r.name || id)}</strong></td>` +
        `<td>${esc(h.report_date || "—")}</td>` +
        `<td class="num">${esc(shares)}</td>` +
        `<td class="num">${esc(pct)}</td>` +
        `<td class="num">${cell(h.chg_shares)}</td>` +
        `<td class="num">${cell(h.chg_pct_pt)}</td>` +
        `<td>${codes}</td></tr>`;
    }).join("");
    const htable = `<table class="put-table cnflow-table"><thead><tr>` +
      `<th>机构ETF持仓（半年报/年报）</th><th>报告期</th>` +
      `<th class="num">机构持仓(亿份)</th><th class="num">占总份额</th>` +
      `<th class="num">较上期(亿份)</th><th class="num">较上期(百分点)</th><th>覆盖ETF</th>` +
      `</tr></thead><tbody>${hrows}</tbody></table>`;
    const notes = (bundle.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
    const snapMeta = bundle.main_from_snapshot
      ? ` · 主力为上次快照（${esc(bundle.main_snapshot_asof || (mk.main && mk.main.asof) || "—")}）`
      : "";
    const etfSnapMeta = bundle.etf_from_snapshot
      ? ` · ETF申赎为上次快照（${esc(bundle.etf_snapshot_asof || "—")}）`
      : "";
    const histSnapMeta = bundle.margin_history_from_snapshot
      ? ` · 两融历史高低为上次快照（${esc(bundle.margin_history_snapshot_asof || highStart || "—")}）`
      : "";
    const histCovMeta = ` · ${esc(rangeTxt)}`;
    const meta = `<div class="cnflow-src">单位：亿元（机构持仓为亿份） · 取数 ${esc(bundle.fetched_at || "—")}${snapMeta}${etfSnapMeta}${histCovMeta}${histSnapMeta}</div>`;
    return `${top}<div class="table-wrap" tabindex="0" role="region" aria-label="指数资金进出表，可左右滚动">${table}</div>` +
      `<div class="table-wrap" tabindex="0" role="region" aria-label="机构ETF持仓表，可左右滚动">${htable}</div>` +
      `<div class="table-wrap" tabindex="0" role="region" aria-label="两融融资余额表，可左右滚动">${margin}</div>` +
      chartPanelHtml() + meta +
      (notes ? `<ul class="cnflow-notes">${notes}</ul>` : "");
  }

  const RANGE_DAYS = { "1y": 365, "3y": 365 * 3, "5y": 365 * 5 };

  function addDaysIso(iso, n) {
    const parts = String(iso || "").split("-").map(Number);
    if (parts.length < 3 || !parts[0]) return iso;
    const dt = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2] + n));
    return dt.toISOString().slice(0, 10);
  }

  /** ISO 年-周，用于按周抽样实际点。 */
  function isoWeekKey(iso) {
    const parts = String(iso || "").split("-").map(Number);
    if (parts.length < 3 || !parts[0]) return "";
    const dt = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
    const day = dt.getUTCDay() || 7;
    dt.setUTCDate(dt.getUTCDate() + 4 - day);
    const year = dt.getUTCFullYear();
    const jan1 = new Date(Date.UTC(year, 0, 1));
    const week = Math.ceil((((dt - jan1) / 86400000) + 1) / 7);
    return year + "-W" + String(week).padStart(2, "0");
  }

  function downsampleWeekly(points) {
    const by = Object.create(null);
    const keys = [];
    (points || []).forEach((p) => {
      if (!p || !p[0]) return;
      const k = isoWeekKey(p[0]);
      if (!k) return;
      if (!(k in by)) keys.push(k);
      by[k] = p;
    });
    return keys.sort().map((k) => by[k]);
  }

  function filterChartSeries(series, range, high, low) {
    const daily = (series || []).filter((p) => p && p[0]);
    if (!daily.length) return [];
    const end = daily[daily.length - 1][0];
    let start = daily[0][0];
    if (range !== "all" && RANGE_DAYS[range]) start = addDaysIso(end, -RANGE_DAYS[range]);
    let pts = daily.filter((p) => p[0] >= start && p[0] <= end);
    if (range === "all") pts = downsampleWeekly(pts);
    const want = [];
    if (high && high.date) want.push(high.date);
    if (low && low.date) want.push(low.date);
    const have = new Set(pts.map((p) => p[0]));
    daily.forEach((p) => {
      if (want.indexOf(p[0]) >= 0 && !have.has(p[0]) && p[0] >= start && p[0] <= end) {
        pts.push(p);
        have.add(p[0]);
      }
    });
    pts.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    return pts;
  }

  function chartPanelHtml() {
    return `<div class="cnflow-margin-chart" data-margin-chart>` +
      `<div class="cnflow-chart-toolbar">` +
      `<div class="seg cnflow-chart-mkt" role="tablist" aria-label="两融市场">` +
      `<button type="button" data-mkt="margin_sh">上交所</button>` +
      `<button type="button" data-mkt="margin_sz">深交所</button>` +
      `<button type="button" data-mkt="margin_total" class="active">沪深合计</button>` +
      `</div>` +
      `<div class="seg cnflow-chart-range" role="tablist" aria-label="时间区间">` +
      `<button type="button" data-range="1y">1年</button>` +
      `<button type="button" data-range="3y">3年</button>` +
      `<button type="button" data-range="5y">5年</button>` +
      `<button type="button" data-range="all" class="active">全部</button>` +
      `</div></div>` +
      `<div class="cnflow-margin-chart-el" role="img" aria-label="融资余额与对应指数走势"></div>` +
      `<div class="cnflow-src cnflow-chart-note">左轴融资余额（亿元），右轴对应指数收盘。历史最低仅标注 2014-09-22 起窗口内的最低点。全部区间按周保留实际交易日（不插补），高低点日期始终保留。</div>` +
      `</div>`;
  }

  const AXIS = "#a0afc3";
  function axisStyle() {
    return {
      axisLine: { lineStyle: { color: "#4b5d78" } },
      axisLabel: { color: AXIS },
      splitLine: { lineStyle: { color: "rgba(160,175,195,0.12)" } },
    };
  }

  function chartOption(market, range) {
    market = market || {};
    const pts = filterChartSeries(market.series, range || "all", market.hist_high, market.hist_low);
    const balName = "融资余额";
    const idxName = market.index_name || "指数";
    const bal = pts.map((p) => [p[0], p[1] == null ? null : p[1]]);
    const idx = pts.map((p) => [p[0], p[2] == null ? null : p[2]]);
    const marks = [];
    function addMark(pt, name, color, pos) {
      if (!pt || !pt.date || pt.balance == null) return;
      if (!pts.length || pt.date < pts[0][0] || pt.date > pts[pts.length - 1][0]) return;
      marks.push({
        name: name,
        coord: [pt.date, pt.balance],
        value: fmtYi(pt.balance),
        itemStyle: { color: color },
        label: { formatter: name, color: "#e2e8f0", fontSize: 10, position: pos },
      });
    }
    addMark(market.hist_high, "历史最高", "#ef5350", "top");
    addMark(market.hist_low, "历史最低", "#26a69a", "bottom");
    return {
      backgroundColor: "transparent",
      animation: false,
      legend: { data: [balName, idxName], textStyle: { color: AXIS, fontSize: 11 }, top: 0 },
      grid: { left: 64, right: 56, top: 36, bottom: 36 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: "rgba(15,23,42,0.94)",
        borderColor: "#334155",
        textStyle: { color: "#e2e8f0", fontSize: 12 },
        formatter: (items) => {
          if (!items || !items.length) return "";
          const i = items[0].dataIndex;
          const p = pts[i] || [];
          const balTxt = p[1] == null ? NO_DATA : (fmtYi(p[1]) + " 亿元");
          const idxTxt = p[2] == null ? NO_DATA : Number(p[2]).toFixed(2);
          return `<b>${esc(p[0] || "")}</b><br/>融资余额：${esc(balTxt)}<br/>${esc(idxName)}：${esc(idxTxt)}`;
        },
      },
      xAxis: Object.assign({ type: "time" }, axisStyle()),
      yAxis: [
        Object.assign({ type: "value", name: "融资余额(亿元)", scale: true, splitNumber: 4 }, axisStyle()),
        Object.assign({ type: "value", name: idxName, scale: true, splitNumber: 4, splitLine: { show: false } }, axisStyle()),
      ],
      series: [
        {
          name: balName, type: "line", yAxisIndex: 0, showSymbol: false, connectNulls: false,
          data: bal, lineStyle: { width: 1.6, color: "#f0a15a" }, itemStyle: { color: "#f0a15a" },
          markPoint: marks.length ? { symbolSize: 10, data: marks } : undefined,
        },
        {
          name: idxName, type: "line", yAxisIndex: 1, showSymbol: false, connectNulls: false,
          data: idx, lineStyle: { width: 1.4, color: "#7eb6ff" }, itemStyle: { color: "#7eb6ff" },
        },
      ],
    };
  }

  let chartInst = null;
  const chartState = { mkt: "margin_total", range: "all", payload: null };

  function showChartMiss(el, msg) {
    if (chartInst) {
      try { chartInst.dispose(); } catch (e) { /* ignore */ }
      chartInst = null;
    }
    if (el) el.innerHTML = `<div class="cnflow-miss">${esc(msg || NO_DATA)}</div>`;
  }

  function paintChart(root) {
    const el = root && root.querySelector && root.querySelector(".cnflow-margin-chart-el");
    if (!el) return;
    const markets = (chartState.payload && chartState.payload.markets) || {};
    const market = markets[chartState.mkt];
    if (!market || !market.series || !market.series.length) {
      showChartMiss(el, NO_DATA);
      return;
    }
    const echarts = rootObj.echarts;
    if (!echarts || !echarts.init) {
      showChartMiss(el, "图表库未加载");
      return;
    }
    if (!chartInst || (chartInst.isDisposed && chartInst.isDisposed())) {
      el.innerHTML = "";
      chartInst = echarts.init(el, null, { renderer: "canvas" });
    }
    chartInst.setOption(chartOption(market, chartState.range), true);
  }

  const rootObj = root;

  function mountChart(host, payload) {
    const rootEl = host && host.querySelector ? host.querySelector("[data-margin-chart]") : null;
    if (!rootEl) return;
    chartState.payload = payload && payload.markets ? payload : null;
    const snap = rootEl.querySelector(".cnflow-chart-note");
    if (snap && chartState.payload && chartState.payload.from_snapshot) {
      const asof = chartState.payload.snapshot_asof || "";
      const label = asof ? `上次快照（${asof}）。` : "上次快照。";
      if (snap.getAttribute("data-base-note") == null) snap.setAttribute("data-base-note", snap.textContent);
      snap.textContent = label + (snap.getAttribute("data-base-note") || snap.textContent);
    }
    if (!rootEl._bound) {
      rootEl._bound = true;
      rootEl.addEventListener("click", (e) => {
        const btn = e.target.closest && e.target.closest("button[data-mkt], button[data-range]");
        if (!btn) return;
        if (btn.dataset.mkt) {
          chartState.mkt = btn.dataset.mkt;
          rootEl.querySelectorAll(".cnflow-chart-mkt button").forEach((b) => {
            b.classList.toggle("active", b === btn);
          });
        }
        if (btn.dataset.range) {
          chartState.range = btn.dataset.range;
          rootEl.querySelectorAll(".cnflow-chart-range button").forEach((b) => {
            b.classList.toggle("active", b === btn);
          });
        }
        paintChart(rootEl);
      });
    }
    // restore active buttons for current state
    rootEl.querySelectorAll(".cnflow-chart-mkt button").forEach((b) => {
      b.classList.toggle("active", b.dataset.mkt === chartState.mkt);
    });
    rootEl.querySelectorAll(".cnflow-chart-range button").forEach((b) => {
      b.classList.toggle("active", b.dataset.range === chartState.range);
    });
    paintChart(rootEl);
  }

  function resizeChart() {
    if (chartInst && !(chartInst.isDisposed && chartInst.isDisposed())) {
      try { chartInst.resize(); } catch (e) { /* ignore */ }
    }
  }

  function disposeChart() {
    if (chartInst) {
      try { chartInst.dispose(); } catch (e) { /* ignore */ }
      chartInst = null;
    }
  }

  const api = {
    esc: esc, cls: cls, signed: signed, fmtYi: fmtYi, cardHtml: cardHtml, summaryHtml: summaryHtml,
    chartPanelHtml: chartPanelHtml, downsampleWeekly: downsampleWeekly, filterChartSeries: filterChartSeries,
    chartOption: chartOption, mountChart: mountChart, resizeChart: resizeChart, disposeChart: disposeChart,
    NO_DATA: NO_DATA,
  };
  root.cnIndexFlowView = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
