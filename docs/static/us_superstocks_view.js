/* 美股 Superstocks 研究页：规则筛选 + 周线图。研究展示，不能下单。
 * 浏览器: window.USSuperstocksView；Node 测试: module.exports。 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.USSuperstocksView = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";
  const WATCH_KEY = "us-superstocks-watch";
  const NONE = "无数据";

  function esc(value) {
    return String(value == null ? NONE : value).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  function missingText(value) {
    if (value == null || value === "" || (typeof value === "number" && !isFinite(value))) return NONE;
    return value;
  }

  function fmtNum(value, digits) {
    const n = Number(value);
    if (value == null || value === "" || !isFinite(n)) return NONE;
    return n.toLocaleString("en-US", { maximumFractionDigits: digits == null ? 2 : digits, minimumFractionDigits: 0 });
  }

  function fmtPrice(value) {
    const n = Number(value);
    if (value == null || !isFinite(n)) return NONE;
    return "$" + n.toFixed(n >= 10 ? 2 : 3);
  }

  function fmtCap(value) {
    const n = Number(value);
    if (value == null || !isFinite(n)) return NONE;
    if (n >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
    return "$" + Math.round(n).toLocaleString("en-US");
  }

  function fmtPct(value, alreadyPct) {
    const n = Number(value);
    if (value == null || !isFinite(n)) return '<span class="ss-na">' + NONE + "</span>";
    const pct = alreadyPct ? n : n * 100;
    const cls = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
    const sign = pct > 0 ? "+" : "";
    return '<span class="' + cls + '">' + sign + pct.toFixed(1) + "%</span>";
  }

  function stateClass(state) {
    return ({
      "买点区": "ss-st-buy",
      "已延伸勿追": "ss-st-ext",
      "退出信号": "ss-st-exit",
      "观察": "ss-st-watch"
    })[state] || "ss-st-watch";
  }

  function loadWatch() {
    if (typeof localStorage === "undefined") return [];
    try {
      const raw = localStorage.getItem(WATCH_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr.map(function (s) { return String(s).toUpperCase(); }).filter(Boolean) : [];
    } catch (e) {
      return [];
    }
  }

  function saveWatch(list) {
    if (typeof localStorage === "undefined") return;
    try { localStorage.setItem(WATCH_KEY, JSON.stringify(list)); } catch (e) { /* ignore */ }
  }

  function toggleWatch(symbol) {
    const sym = String(symbol || "").toUpperCase();
    if (!sym) return loadWatch();
    let list = loadWatch();
    if (list.indexOf(sym) >= 0) list = list.filter(function (s) { return s !== sym; });
    else list.push(sym);
    saveWatch(list);
    return list;
  }

  function applyWatch(snap, watch) {
    const view = snap && typeof snap === "object" ? JSON.parse(JSON.stringify(snap)) : snap;
    if (!view || typeof view !== "object") return view;
    const set = {};
    (watch || []).forEach(function (s) { set[String(s).toUpperCase()] = true; });
    function one(row) {
      if (!row || typeof row !== "object") return row;
      const sym = String(row.symbol || "").toUpperCase();
      row.watched = !!set[sym];
      const exits = (row.exit && row.exit.signals) || [];
      if (row.watched && exits.length) row.state = "退出信号";
      return row;
    }
    view.candidates = (view.candidates || []).map(one);
    view.near = (view.near || []).map(one);
    return view;
  }

  function field(label, value) {
    return '<div class="ss-kv"><span class="ss-k">' + esc(label) + '</span><span class="ss-v">' + (String(value).indexOf("<") === 0 ? value : esc(missingText(value))) + "</span></div>";
  }

  function insiderBlock(ins) {
    if (!ins || ins.status === "无数据" || !(ins.open_market_buys || []).length) {
      return '<p class="ss-na">内部人公开市场买入：' + NONE + (ins && ins.note ? "（" + esc(ins.note) + "）" : "") + "</p>";
    }
    const rows = ins.open_market_buys.map(function (t) {
      const flag = t.token_buy ? " · 可能是象征性小额" : "";
      return "<tr><td>" + esc(missingText(t.date)) + "</td><td>" + esc(missingText(t.owner)) +
        "</td><td>" + esc(missingText(t.title)) + "</td><td class=\"num\">" + esc(fmtNum(t.shares, 0)) +
        "</td><td class=\"num\">" + esc(fmtPrice(t.price)) + "</td><td class=\"num\">" +
        (t.value_usd == null ? NONE : "$" + fmtNum(t.value_usd, 0)) + "</td><td>" + esc(t.code || NONE) + flag + "</td></tr>";
    }).join("");
    return '<p class="sub">来源：' + esc(ins.source || "SEC EDGAR Form 4") +
      " · 仅计代码 P 的公开市场买入 · C 级/董事人数 " + esc(missingText(ins.c_level_buyer_count)) +
      (ins.timing && ins.timing !== "无数据" ? " · 时间窗口 " + esc(ins.timing) : "") +
      '</p><div class="table-wrap" tabindex="0" role="region" aria-label="内部人买入表格，可左右滚动"><table class="ss-ins-table"><thead><tr><th>日期</th><th>姓名</th><th>职务</th><th class="num">股数</th><th class="num">价格</th><th class="num">金额</th><th>代码</th></tr></thead><tbody>' +
      rows + "</tbody></table></div>";
  }

  function checklistHtml(items) {
    const lis = (items || []).map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("");
    return "<ul class=\"ss-check\">" + lis + "</ul>";
  }

  function rowHtml(r) {
    const tech = r.technicals || {};
    const fund = r.fundamentals || {};
    const st = r.state || "观察";
    const reason = (r.near_miss_reasons || []).concat(r.fail_reasons || []).slice(0, 2).join("；") || "";
    const watchLab = r.watched ? "移出纸面观察" : "加入纸面观察";
    return "<tr data-symbol=\"" + esc(r.symbol) + "\">" +
      "<td><button type=\"button\" class=\"ss-sym\" data-open=\"" + esc(r.symbol) + "\">" + esc(r.symbol) + "</button></td>" +
      "<td>" + esc(missingText(r.name)) + "</td>" +
      "<td><span class=\"ss-state " + stateClass(st) + "\">" + esc(st) + "</span>" +
        (r.price_caution ? '<span class="ss-flag">$2–$4 谨慎</span>' : "") +
        (r.prior_runner ? '<span class="ss-flag">此前突破观察</span>' : "") + "</td>" +
      "<td class=\"num\">" + esc(fmtPrice(r.price)) + "</td>" +
      "<td class=\"num\">" + esc(fmtCap(fund.market_cap)) + "</td>" +
      "<td class=\"num\">" + esc(fmtNum(r.score, 1)) + "</td>" +
      "<td class=\"num\">" + (tech.vol_expand == null ? NONE : esc(fmtNum(tech.vol_expand, 1) + "x")) + "</td>" +
      "<td class=\"num\">" + fmtPct(tech.angle_pct_per_week, true) + "</td>" +
      "<td class=\"num\">" + (fund.pe_run_rate == null ? NONE : esc(fmtNum(fund.pe_run_rate, 1))) + "</td>" +
      "<td>" + ((r.insiders && r.insiders.status === "ok" && (r.insiders.open_market_buys || []).length) ? esc(String(r.insiders.buyer_count || 0) + " 人") : NONE) + "</td>" +
      "<td class=\"ss-reason\">" + esc(reason || "—") + "</td>" +
      "<td><button type=\"button\" class=\"btn btn-tiny ss-watch-btn\" data-watch=\"" + esc(r.symbol) + "\">" + esc(watchLab) + "</button></td>" +
      "</tr>";
  }

  function tableHtml(rows, emptyText) {
    if (!rows || !rows.length) return '<p class="ss-na">' + esc(emptyText || ("当前名单为空，" + NONE + "候选。")) + "</p>";
    return '<div class="table-wrap" tabindex="0" role="region" aria-label="Superstocks 名单，可左右滚动"><table class="ss-table"><thead><tr>' +
      "<th>代码</th><th>名称</th><th>状态</th><th class=\"num\">价格</th><th class=\"num\">市值</th><th class=\"num\">软分数</th>" +
      "<th class=\"num\">量能倍数</th><th class=\"num\">攻角%/周</th><th class=\"num\">PE run-rate</th><th>内部人买入</th><th>说明</th><th>纸面观察</th>" +
      "</tr></thead><tbody>" + rows.map(rowHtml).join("") + "</tbody></table></div>";
  }

  function detailHtml(r, payload) {
    if (!r) return '<p class="ss-na">点选一行查看周线、买入/退出与基本面。缺字段显示' + NONE + "。</p>";
    const tech = r.technicals || {};
    const fund = r.fundamentals || {};
    const entry = r.entry || {};
    const exits = (r.exit && r.exit.signals) || [];
    return '<div class="ss-detail-grid">' +
      "<div>" +
      "<h4>" + esc(r.symbol) + " · " + esc(missingText(r.name)) + ' <span class="ss-state ' + stateClass(r.state) + '">' + esc(r.state) + "</span></h4>" +
      field("交易所", missingText(r.exchange)) +
      field("价格", fmtPrice(r.price) + (r.price_caution ? "（$2–$4 谨慎）" : "")) +
      field("市值", fmtCap(fund.market_cap) + (fund.market_cap_tier ? " · " + fund.market_cap_tier : "")) +
      field("流通股本", fund.float_shares == null ? NONE : fmtNum(fund.float_shares, 0)) +
      field("10 周均 / 30 周均", (tech.wma10 == null ? NONE : fmtPrice(tech.wma10)) + " / " + (tech.wma30 == null ? NONE : fmtPrice(tech.wma30))) +
      field("相对魔法线", tech.pct_above_magic == null ? NONE : fmtNum(tech.pct_above_magic, 1) + "%") +
      field("平台周数 / 突破日", (missingText(tech.base_weeks)) + " / " + missingText(tech.breakout_date)) +
      field("突破量能", tech.vol_expand == null ? NONE : fmtNum(tech.vol_expand, 2) + "x（" + missingText(tech.vol_expand_pass) + "）") +
      field("攻角%/周", tech.angle_pct_per_week == null ? NONE : fmtNum(tech.angle_pct_per_week, 2)) +
      field("PE run-rate / TTM", (fund.pe_run_rate == null ? NONE : fmtNum(fund.pe_run_rate, 2)) + " / " + (fund.pe_ttm == null ? NONE : fmtNum(fund.pe_ttm, 2))) +
      field("长期负债", fund.long_term_debt == null ? NONE : fmtCap(fund.long_term_debt)) +
      field("空头占比", fund.short_interest_pct == null ? NONE : fmtNum(fund.short_interest_pct * 100, 2) + "%") +
      field("分析师数量", missingText(fund.analyst_count)) +
      field("内部人持股", fund.insider_ownership_pct == null ? NONE : fmtNum((fund.insider_ownership_pct <= 1.5 ? fund.insider_ownership_pct * 100 : fund.insider_ownership_pct), 1) + "%") +
      field("商品业务标记", fund.commodity_flag == null ? NONE : (fund.commodity_flag ? "是（仅标记）" : "否")) +
      field("预设止损", entry.stop_note || (payload && payload.stop_note) || NONE) +
      field("止损参考（10 周均）", entry.stop_level == null ? NONE : fmtPrice(entry.stop_level)) +
      field("买点结构", (entry.setups || []).join("，") || "无") +
      field("次要结构", (entry.secondary || []).length ? (entry.secondary || []).join("，") : NONE) +
      field("退出信号（纸面观察才改状态）", exits.length ? exits.join("，") : "无") +
      "</div><div><div id=\"ss-chart\" class=\"ss-chart\" role=\"img\" aria-label=\"周线蜡烛图\"></div>" +
      insiderBlock(r.insiders) +
      "<details class=\"ss-method\"><summary>人工核对清单（不计分）</summary>" + checklistHtml(r.manual_checklist || (payload && payload.manual_checklist) || []) + "</details>" +
      "</div></div>";
  }

  function chartOption(bars) {
    const cats = [];
    const ohlc = [];
    const vol = [];
    const ma10 = [];
    const ma30 = [];
    const breakout = [];
    (bars || []).forEach(function (b) {
      cats.push(b.date);
      ohlc.push([b.open, b.close, b.low, b.high]);
      vol.push(b.volume);
      ma10.push(b.wma10);
      ma30.push(b.wma30);
      if (b.breakout) breakout.push({ name: "突破", coord: [b.date, b.close], value: "突破" });
    });
    return {
      animation: false,
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      legend: { data: ["周线", "10周均", "30周均"], textStyle: { color: "#b8c7dd" }, top: 0 },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 50, right: 16, top: 28, height: "58%" },
        { left: 50, right: 16, top: "78%", height: "16%" }
      ],
      xAxis: [
        { type: "category", data: cats, gridIndex: 0, axisLabel: { color: "#8b9bb0" }, axisLine: { lineStyle: { color: "#2b3b52" } } },
        { type: "category", data: cats, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#2b3b52" } } }
      ],
      yAxis: [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: "rgba(160,175,195,0.12)" } }, axisLabel: { color: "#8b9bb0" } },
        { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { color: "#8b9bb0", formatter: function (v) { return v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v; } }, splitLine: { show: false } }
      ],
      series: [
        { name: "周线", type: "candlestick", data: ohlc, itemStyle: { color: "#ef5350", color0: "#26a69a", borderColor: "#ef5350", borderColor0: "#26a69a" } },
        { name: "10周均", type: "line", data: ma10, showSymbol: false, lineStyle: { width: 1.6, color: "#f5cb7a" } },
        { name: "30周均", type: "line", data: ma30, showSymbol: false, lineStyle: { width: 1.6, color: "#79b8ff" } },
        { name: "成交量", type: "bar", data: vol, xAxisIndex: 1, yAxisIndex: 1, itemStyle: { color: "rgba(121,184,255,0.35)" } },
        { type: "scatter", data: [], markPoint: { data: breakout, itemStyle: { color: "#ef5350" }, label: { formatter: "突破", color: "#fff" } } }
      ]
    };
  }

  function renderChart(el, bars) {
    if (!el) return;
    if (!bars || !bars.length) {
      el.textContent = "周线" + NONE;
      return;
    }
    if (typeof echarts === "undefined") {
      el.textContent = "图表库未加载，周线仍见下方表格字段。";
      return;
    }
    if (!el._ssChart) el._ssChart = echarts.init(el);
    el._ssChart.setOption(chartOption(bars), true);
  }

  function metaText(data) {
    const c = (data && data.counts) || {};
    const asof = data && data.asof_session ? ("最近完整美股周线日 " + data.asof_session + "（" + (data.asof_session_tz || "America/New_York") + "）") : ("交易日" + NONE);
    const upd = data && data.updated_at ? ("更新 " + data.updated_at) : ("更新时间" + NONE);
    return upd + " · " + asof + " · 宇宙 " + missingText(c.universe) + " / 已扫描 " + missingText(c.scanned) +
      " · 严格候选 " + missingText(c.candidates) + " · 接近 " + missingText(c.near);
  }

  const POLL_MS = 1500;

  function emptyProgress() {
    return { ok: true, state: "idle", phase: null, phase_label: null, percent: 0, processed: 0, total: 0, message: "", error: null };
  }

  function normalizeProgress(data) {
    if (!data || typeof data !== "object") return emptyProgress();
    if (data.progress && typeof data.progress === "object") return normalizeProgress(data.progress);
    if (data.state && (data.state === "running" || data.state === "done" || data.state === "error" || data.state === "idle" || Object.prototype.hasOwnProperty.call(data, "percent") || data.phase || data.phase_label || data.last_run_at || data.error || data.message)) {
      const out = emptyProgress();
      Object.keys(out).forEach(function (k) { if (data[k] != null) out[k] = data[k]; });
      out.state = data.state;
      out.message = data.message || "";
      out.error = data.error || null;
      out.percent = data.percent;
      out.processed = data.processed;
      out.total = data.total;
      out.phase = data.phase;
      out.phase_label = data.phase_label;
      out.last_run_at = data.last_run_at;
      out.last_run_state = data.last_run_state;
      return out;
    }
    return emptyProgress();
  }

  function progressPercent(p) {
    const prog = normalizeProgress(p);
    if (prog.state === "done") return 100;
    const n = Number(prog.percent);
    if (!isFinite(n)) return 0;
    return Math.max(0, Math.min(100, Math.round(n)));
  }

  function shouldShowProgressBar(p) {
    return normalizeProgress(p).state === "running";
  }

  function isButtonLatched(p, loadingFlag) {
    return shouldShowProgressBar(p) || !!loadingFlag;
  }

  function shouldKeepPolling(p, expectJob) {
    const st = normalizeProgress(p).state;
    if (st === "running") return true;
    if (expectJob && st === "idle") return true;
    return false;
  }

  function progressLine(p) {
    const prog = normalizeProgress(p);
    if (prog.state === "idle" && !prog.last_run_at) return "";
    if (prog.state === "error") return prog.message || prog.error || "扫描失败";
    const bits = [];
    if (prog.state === "running") bits.push("扫描进行中 " + progressPercent(prog) + "%");
    if (prog.state === "done") bits.push("上次扫描已完成");
    if (prog.state === "idle" && prog.last_run_at) {
      bits.push("上次扫描 " + prog.last_run_at + (prog.last_run_state === "error" ? "（失败）" : ""));
    }
    if (prog.phase_label || prog.phase) bits.push(prog.phase_label || prog.phase);
    if (prog.state === "running" && prog.total) bits.push((prog.processed || 0) + "/" + prog.total);
    if (prog.message && prog.state === "running") bits.push(prog.message);
    return bits.join(" · ");
  }

  let payload = null;
  let loading = false;
  let bound = false;
  let selected = null;
  let pollTimer = null;
  let jobLatch = false;
  let lastProgress = emptyProgress();

  function $(id) { return typeof document === "undefined" ? null : document.getElementById(id); }

  function allRows(data) {
    return [].concat(data.candidates || [], data.near || []);
  }

  function filteredRows(data) {
    const sel = $("ss-filter");
    const key = sel ? sel.value : "all";
    const rows = allRows(data);
    if (key === "all") return rows;
    if (key === "candidates") return rows.filter(function (r) { return r.hard_pass; });
    if (key === "near") return rows.filter(function (r) { return r.near || r.prior_runner; });
    return rows.filter(function (r) { return r.state === key; });
  }

  function paint() {
    if (!payload || !$("ss-content")) return;
    const data = applyWatch(payload, loadWatch());
    $("ss-meta").textContent = metaText(data);
    $("ss-note").textContent = data.note || "";
    const weekly = $("ss-weekly-note");
    if (weekly) weekly.textContent = (data.weekly_scan && data.weekly_scan.note) || weekly.textContent;
    renderProgress(data.progress || lastProgress);
    $("ss-disclaimer").textContent = data.disclaimer || "";
    $("ss-table").innerHTML = tableHtml(filteredRows(data), "当前筛选下没有名字。空名单不是编造的收益。");
    const pick = selected && allRows(data).filter(function (r) { return r.symbol === selected; })[0];
    $("ss-detail").innerHTML = detailHtml(pick, data);
    if (pick) renderChart($("ss-chart"), pick.weekly_bars || []);
    $("ss-edge").textContent = data.historical_edge || "";
    $("ss-sizing").textContent = (data.sizing && data.sizing.note) || "";
    $("ss-angle").textContent = data.angle_formula || "";
    $("ss-stop").textContent = data.stop_note || "";
    $("ss-insider-src").textContent = data.insider_source || "";
    $("ss-limits").innerHTML = "<ul>" + (data.limitations || []).map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>";
    $("ss-defaults").innerHTML = defaultsHtml(data.defaults || {});
    $("ss-content").hidden = false;
  }

  function defaultsHtml(d) {
    const items = [
      ["严格平台周数", d.base_weeks_strict],
      ["弱平台周数", d.base_weeks_weak],
      ["平台振幅上限（区间/中位收盘）", d.tightness_pass],
      ["严格突破量能倍数", d.vol_expand_strict],
      ["接近名单量能倍数", d.vol_expand_soft],
      ["魔法线 / 长均线", (d.ma_magic || "") + " / " + (d.ma_long || "")],
      ["攻角阈值（%/周）", d.angle_pct_per_week],
      ["核心价格上限", d.price_max_core],
      ["甜区", (d.price_sweet_low || "") + "–" + (d.price_sweet_high || "")],
      ["谨慎低价", (d.price_caution_low || "") + "–" + (d.price_sweet_low || "")],
      ["最少交易历史（周）", d.min_history_weeks],
      ["PE run-rate 加分上限", d.pe_run_rate_max],
      ["默认止损", d.stop_primary],
      ["研究仓位", ((d.sizing_sleeve_min || 0) * 100) + "%–" + ((d.sizing_sleeve_max || 0) * 100) + "%，最多 " + (d.sizing_max_names || 1) + " 只"]
    ];
    return "<ul>" + items.map(function (it) { return "<li>" + esc(it[0]) + "：" + esc(missingText(it[1])) + "</li>"; }).join("") + "</ul>";
  }

  function bind() {
    if (bound || typeof document === "undefined") return;
    bound = true;
    const filter = $("ss-filter");
    if (filter) filter.addEventListener("change", paint);
    const retry = $("ss-retry");
    if (retry) retry.addEventListener("click", function () { payload = null; load(false); });
    const refresh = $("ss-refresh");
    if (refresh) refresh.addEventListener("click", function () {
      if (jobLatch || shouldShowProgressBar(lastProgress)) {
        setButtonLatched(true);
        renderProgress(lastProgress);
        startProgressPoll();
        return;
      }
      load(true);
    });
    const content = $("ss-content");
    if (content) {
      content.addEventListener("click", function (e) {
        const open = e.target.closest("[data-open]");
        if (open) {
          selected = open.getAttribute("data-open");
          paint();
          return;
        }
        const w = e.target.closest("[data-watch]");
        if (w) {
          toggleWatch(w.getAttribute("data-watch"));
          paint();
        }
      });
    }
    if (typeof window !== "undefined") {
      window.addEventListener("resize", function () {
        const el = $("ss-chart");
        if (el && el._ssChart) el._ssChart.resize();
      });
    }
  }

  function setButtonLatched(on) {
    jobLatch = !!on;
    const btn = $("ss-refresh");
    if (!btn) return;
    btn.disabled = jobLatch;
    btn.setAttribute("aria-disabled", jobLatch ? "true" : "false");
    btn.textContent = jobLatch ? "后台重算进行中…" : "后台重算（不阻塞本页）";
  }

  function renderProgress(p) {
    const prog = normalizeProgress(p);
    lastProgress = prog;
    const wrap = $("ss-progress");
    const fill = $("ss-progress-fill");
    const bar = $("ss-progress-bar");
    const text = $("ss-progress-text");
    const show = shouldShowProgressBar(prog);
    if (wrap) wrap.hidden = !show;
    const pct = show ? progressPercent(prog) : 0;
    if (fill) fill.style.width = pct + "%";
    if (bar) {
      bar.setAttribute("aria-valuenow", String(pct));
      bar.setAttribute("aria-valuetext", progressLine(prog) || "无进行中的扫描");
    }
    if (text) {
      if (show || prog.state === "error") text.textContent = progressLine(prog);
      else if (prog.last_run_at && prog.state !== "running") text.textContent = progressLine({ state: "idle", last_run_at: prog.last_run_at, last_run_state: prog.last_run_state });
      else text.textContent = "";
    }
    // Stay latched while a poll is in flight (queued but progress file not yet running).
    if (show || pollTimer) setButtonLatched(true);
    else setButtonLatched(false);
    return prog;
  }

  function stopProgressPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function handleProgressTick(p, afterJob) {
    const prog = renderProgress(p);
    if (prog.state === "running") return "running";
    setButtonLatched(false);
    if (prog.state === "error") return "error";
    if (prog.state === "done" && afterJob) return "done";
    return prog.state || "idle";
  }

  async function pollProgressOnce() {
    const path = typeof apiUrl === "function" ? apiUrl("/api/us-superstocks/progress") : "/api/us-superstocks/progress";
    const res = await fetch(path, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return normalizeProgress(await res.json());
  }

  async function refreshSnapshotAfterJob() {
    const path = typeof apiUrl === "function" ? apiUrl("/api/us-superstocks") : "/api/us-superstocks";
    const res = await fetch(path, { signal: AbortSignal.timeout(20000) });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    if (!data || typeof data !== "object") throw new Error("bad payload");
    payload = data;
    paint();
    return data;
  }

  function startProgressPoll() {
    if (pollTimer) return;
    pollTimer = setInterval(function () {
      pollProgressOnce().then(function (p) {
        const st = handleProgressTick(p, true);
        if (shouldKeepPolling(p, true)) return;
        stopProgressPoll();
        renderProgress(p);
        if (st === "done") {
          refreshSnapshotAfterJob().then(function () {
            if ($("ss-status")) $("ss-status").textContent = "后台扫描已完成，已刷新名单。研究展示，不能下单。";
            renderProgress(p);
          }).catch(function (e) {
            if ($("ss-status")) $("ss-status").textContent = "扫描已结束，但刷新名单失败：" + (e && e.message ? e.message : "无数据") + "。";
          });
        } else if (st === "error") {
          if ($("ss-status")) $("ss-status").textContent = (p.message || p.error || "扫描失败") + " 未编造候选名单。研究展示，不能下单。";
        }
      }).catch(function () {
        /* keep polling; a single failed tick is not a finished job */
      });
    }, POLL_MS);
  }

  async function load(force) {
    if (typeof document === "undefined" || !$("ss-content")) return;
    bind();
    if (loading) return;
    if (payload && !force) {
      paint();
      if (shouldShowProgressBar(payload.progress || lastProgress)) startProgressPoll();
      return;
    }
    loading = true;
    $("ss-status").textContent = force ? "已请求后台重算，先展示快照…" : "打开本页加载研究快照…";
    $("ss-retry").hidden = true;
    try {
      const pathBase = force ? "/api/us-superstocks/refresh" : "/api/us-superstocks";
      const path = typeof apiUrl === "function" ? apiUrl(pathBase) : pathBase;
      const res = await fetch(path, { signal: AbortSignal.timeout(20000) });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      if (!data || typeof data !== "object") throw new Error("bad payload");
      payload = data;
      paint();
      const prog = normalizeProgress(data);
      renderProgress(prog);
      if (prog.state === "running" || (data.refresh && data.refresh.queued)) {
        setButtonLatched(true);
        $("ss-status").textContent = "后台扫描进行中，先展示当前快照。研究展示，不能下单。";
        startProgressPoll();
      } else if (prog.state === "error") {
        $("ss-status").textContent = (prog.message || prog.error || "扫描失败") + " 研究展示，不能下单。";
      } else {
        $("ss-status").textContent = (data.source === "seed" ? "已加载快照（可能不是此刻实时扫描）。" : "已加载研究数据。") + "研究展示，不能下单。";
      }
    } catch (e) {
      $("ss-status").textContent = "无数据：" + (e && e.message ? e.message : "加载失败") + "。";
      $("ss-retry").hidden = false;
    } finally {
      loading = false;
    }
  }

  return {
    missingText: missingText,
    esc: esc,
    fmtPrice: fmtPrice,
    fmtCap: fmtCap,
    fmtPct: fmtPct,
    fmtNum: fmtNum,
    applyWatch: applyWatch,
    rowHtml: rowHtml,
    tableHtml: tableHtml,
    detailHtml: detailHtml,
    insiderBlock: insiderBlock,
    chartOption: chartOption,
    metaText: metaText,
    emptyProgress: emptyProgress,
    normalizeProgress: normalizeProgress,
    progressPercent: progressPercent,
    shouldShowProgressBar: shouldShowProgressBar,
    isButtonLatched: isButtonLatched,
    shouldKeepPolling: shouldKeepPolling,
    progressLine: progressLine,
    handleProgressTick: handleProgressTick,
    renderProgress: renderProgress,
    load: load,
    paint: paint
  };
});
