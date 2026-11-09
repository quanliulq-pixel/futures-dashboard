
  const STATIC_MODE = /github\.io$/i.test(location.hostname) || location.search.includes('static=1') || window.STATIC_MODE === true;
  function apiUrl(path) {
    if (!STATIC_MODE) return path;
    const clean = String(path).split('?')[0].replace(/\/+$/, '');
    const map = {
      '/api/quotes': './api/quotes.json',
      '/api/analysis/all': './api/analysis_all.json',
      '/api/supply': './api/supply.json',
      '/api/supply/refresh': './api/supply.json',
      '/api/ng/dashboard': './api/ng_dashboard.json',
      '/api/ng/refresh': './api/ng_dashboard.json',
      '/api/futures-movers': './api/futures_movers.json',
      '/api/us-movers': './api/us_movers.json',
      '/api/short-put/high-vol': './api/short_put.json',
      '/api/short-put/refresh': './api/short_put.json',
      '/api/short-call/high-vol': './api/short_call.json',
      '/api/short-call/refresh': './api/short_call.json',
      '/api/cn-indices': './api/cn_indices.json',
      '/api/cn-indices/refresh': './api/cn_indices.json',
      '/api/cn-index-options': './api/cn_index_options.json',
      '/api/cn-index-options/refresh': './api/cn_index_options.json',
      '/api/cn-index-advice': './api/cn_index_advice.json',
      '/api/health': './api/health.json',
      '/api/instruments': './api/instruments.json',
    };
    if (clean.startsWith('/api/history')) return './api/history_empty.json';
    if (clean.startsWith('/api/analysis') && clean !== '/api/analysis/all') return './api/analysis_one_empty.json';
    return map[clean] || path;
  }

/* Local futures dashboard — quotes, chart, extremes & cost analysis */
(() => {
  const state = {
    quotes: [],
    analysisMap: {}, // id -> stats from extremes all
    lows: [],
    highs: [],
    market: "all",
    sector: "all",
    search: "",
    selectedId: null,
    interval: "D",
    timer: null,
    refreshSec: (window.APP_CONFIG && window.APP_CONFIG.refreshSec) || 20,
    chart: null,
    loadingHistory: false,
    currentAnalysis: null,
    view: "watch",
    lastHistory: null,
    chartLarge: null,
    chartOpen: false,
    supplyKind: "all",
    supplyPayload: null,
    usPeriod: "W",
    usKind: "all",
    usPayload: null,
    usLoading: false,
    moversAsset: "fut",
    fxMarket: "all",
    fxPayload: null,
    // table sort: default none; click 5年分位 to sort
    sortKey: null,
    sortDir: "asc", // asc | desc
    // NG dedicated dashboard
    ngPayload: null,
    ngLoading: false,
    ngNewsCat: "all",
    ngCalcContract: "MNG",
    ngCalcLots: 1,
    ngCharts: {},
  };

  const NUMERIC_SORT_KEYS = new Set([
    "price",
    "change_pct",
    "pct_5y",
    "primary_cost",
    "vs_primary_pct",
    "volume",
  ]);

  const $ = (sel) => document.querySelector(sel);
  const quoteBody = $("#quote-body");
  const quoteMeta = $("#quote-meta");
  const statusText = $("#status-text");
  const liveDot = $("#live-dot");
  const chartTitle = $("#chart-title");
  const chartSub = $("#chart-sub");
  const chartFoot = $("#chart-foot");
  const sectorFilter = $("#sector-filter");
  const refreshSelect = $("#refresh-sec");
  const lowsCards = $("#lows-cards");
  const highsCards = $("#highs-cards");
  const analysisBody = $("#analysis-body");
  const analysisTag = $("#analysis-tag");

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmt(n, digits = 2) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
    const x = Number(n);
    if (Math.abs(x) >= 1000) return x.toLocaleString("en-US", { maximumFractionDigits: digits });
    return x.toFixed(digits);
  }

  function fmtVol(n) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
    const x = Number(n);
    if (x >= 1e8) return (x / 1e8).toFixed(2) + "亿";
    if (x >= 1e4) return (x / 1e4).toFixed(1) + "万";
    return x.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }

  function clsByChange(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v)) || Number(v) === 0) return "flat";
    return Number(v) > 0 ? "up" : "down";
  }

  function setStatus(ok, text) {
    liveDot.className = "dot " + (ok ? "ok" : "err");
    statusText.textContent = text;
  }

  function tagClass(tag) {
    if (!tag) return "mid";
    if (tag.includes("低位") || tag === "偏低") return "low";
    if (tag.includes("高位") || tag === "偏高") return "high";
    return "mid";
  }

  function cardTagClass(tag) {
    if (!tag) return "";
    if (tag.includes("破现金") || tag.includes("贴完全")) return "warn";
    if (tag.includes("高位")) return "hot";
    if (tag.includes("低位")) return "cool";
    return "";
  }

  function deflBadge(tag) {
    if (!tag) return "";
    let cls = "defl-res";
    if (tag.includes("升温") && tag.includes("配置")) cls = "defl-gold";
    else if (tag.includes("升温")) cls = "defl-rise";
    return `<span class="tag-label ${cls}" title="${tag}">${tag}</span>`;
  }

  function filteredQuotes() {
    return state.quotes.filter((q) => {
      if (state.market === "deflation") {
        if (!q.deflation_tag) return false;
      } else if (state.market !== "all" && q.market !== state.market) return false;
      if (state.sector !== "all" && q.sector !== state.sector) return false;
      if (state.search) {
        const s = state.search.toLowerCase();
        const hay = `${q.name} ${q.symbol} ${q.id}`.toLowerCase();
        if (!hay.includes(s)) return false;
      }
      return true;
    });
  }

  function sortQuotes(rows) {
    if (!state.sortKey) return rows;
    const key = state.sortKey;
    const dir = state.sortDir === "desc" ? -1 : 1;
    const numeric = NUMERIC_SORT_KEYS.has(key);
    const copy = rows.slice();
    copy.sort((a, b) => {
      let va = a[key];
      let vb = b[key];
      // empty / missing always sink to bottom
      const aMiss = va === null || va === undefined || va === "" || (numeric && Number.isNaN(Number(va)));
      const bMiss = vb === null || vb === undefined || vb === "" || (numeric && Number.isNaN(Number(vb)));
      if (aMiss && bMiss) return 0;
      if (aMiss) return 1;
      if (bMiss) return -1;
      if (numeric) {
        return (Number(va) - Number(vb)) * dir;
      }
      return String(va).localeCompare(String(vb), "zh") * dir;
    });
    return copy;
  }

  function updateSortHeaders() {
    const head = $("#quote-head");
    if (!head) return;
    head.querySelectorAll("th.sortable").forEach((th) => {
      const k = th.dataset.sort;
      th.classList.remove("sort-asc", "sort-desc", "sort-active");
      if (state.sortKey && k === state.sortKey) {
        th.classList.add("sort-active");
        th.classList.add(state.sortDir === "desc" ? "sort-desc" : "sort-asc");
      }
    });
  }

  function rebuildSectors() {
    if (!sectorFilter) return;
    const sectors = [...new Set(state.quotes.map((q) => q.sector).filter(Boolean))].sort();
    const current = state.sector;
    const keep = sectors.includes(current) ? current : "all";
    if (!sectors.includes(current)) state.sector = "all";
    sectorFilter.innerHTML =
      `<option value="all">全板块</option>` +
      sectors.map((s) => `<option value="${s}">${s}</option>`).join("");
    sectorFilter.value = keep;
  }

  function mergeQuoteRow(q) {
    const a = state.analysisMap[q.id] || {};
    return { ...q, ...a, price: q.price != null ? q.price : a.price };
  }

  function renderTable() {
    const rows = sortQuotes(filteredQuotes().map(mergeQuoteRow));
    updateSortHeaders();
    if (!rows.length) {
      quoteBody.innerHTML = `<tr><td colspan="13" class="empty">暂无匹配行情</td></tr>`;
      return;
    }

    quoteBody.innerHTML = rows
      .map((q) => {
        const chCls = clsByChange(q.change);
        const active = q.id === state.selectedId ? "active" : "";
        const mBadge = q.market === "cn" ? "cn" : "us";
        const mLabel = q.market === "cn" ? "国内" : "北美";
        const pct =
          q.change_pct === null || q.change_pct === undefined
            ? "—"
            : (q.change_pct > 0 ? "+" : "") + Number(q.change_pct).toFixed(2) + "%";
        const vsCost =
          q.vs_primary_pct === null || q.vs_primary_pct === undefined
            ? "—"
            : (q.vs_primary_pct > 0 ? "+" : "") + Number(q.vs_primary_pct).toFixed(1) + "%";
        const vsCls =
          q.vs_primary_pct == null
            ? "flat"
            : q.vs_primary_pct < 0
            ? "down"
            : "up";
        const tag = q.tag || "—";
        return `<tr data-id="${q.id}" class="${active}">
          <td title="${q.name}">${q.name}</td>
          <td>${q.symbol}</td>
          <td><span class="badge ${mBadge}">${mLabel}</span></td>
          <td>${q.exchange || "—"}</td>
          <td>${q.sector || "—"}</td>
          <td class="num ${chCls}">${fmt(q.price)}</td>
          <td class="num ${chCls}">${pct}</td>
          <td class="num">${q.pct_5y != null ? fmt(q.pct_5y, 1) + "%" : "—"}</td>
          <td class="num">${q.primary_cost != null ? fmt(q.primary_cost) : "—"}</td>
          <td class="num ${vsCls}">${vsCost}</td>
          <td><span class="tag-label ${tagClass(tag)}">${tag}</span></td>
          <td class="num">${fmtVol(q.volume)}</td>
          <td>${q.asof || "—"}</td>
        </tr>`;
      })
      .join("");
  }

  function putThemeForQuote(q) {
    const themes = (state.putPayload && state.putPayload.themes) || [];
    const blob = `${q.name || ""} ${q.symbol || ""} ${q.sector || ""} ${q.id || ""}`;
    for (const th of themes) {
      for (const kw of th.keywords || []) {
        if (kw && blob.includes(kw)) return th;
      }
    }
    // fallback keyword map without payload
    const fallback = [
      { id: "ng", label: "天然气", symbols: ["UNG"], keys: ["天然气", "NG"] },
      { id: "oil", label: "原油", symbols: ["USO", "XLE"], keys: ["原油", "石油"] },
      { id: "gold", label: "黄金", symbols: ["GLD", "GDX"], keys: ["黄金"] },
      { id: "silver", label: "白银", symbols: ["SLV"], keys: ["白银"] },
      { id: "copper", label: "铜", symbols: ["FCX"], keys: ["铜"] },
    ];
    for (const th of fallback) {
      if (th.keys.some((k) => blob.includes(k))) return th;
    }
    return null;
  }

  function renderExtremeCards(container, list, tone) {
    if (!list || !list.length) {
      container.innerHTML = `<div class="empty-card">暂无数据（历史不足或计算中）</div>`;
      return;
    }
    container.innerHTML = list
      .map((r) => {
        const active = r.id === state.selectedId ? "active" : "";
        const vs =
          r.vs_primary_pct == null
            ? "—"
            : (r.vs_primary_pct > 0 ? "+" : "") + Number(r.vs_primary_pct).toFixed(1) + "%";
        const pct5 = r.pct_5y != null ? Number(r.pct_5y).toFixed(1) + "%" : "—";
        const cost =
          r.primary_cost != null
            ? `${fmt(r.primary_cost)}${r.cost_unit ? " " + r.cost_unit : ""}`
            : "无成本锚";
        const th = putThemeForQuote(r);
        const putLink = th
          ? `<div class="ext-put-link"><button type="button" class="btn btn-tiny btn-ext-put" data-theme="${th.id}" data-symbols="${(th.symbols || []).join(",")}">卖Put · ${th.label}</button></div>`
          : "";
        return `<div class="xcard ${tone}-tone ${active}" data-id="${r.id}">
          <div class="xn">${r.name} ${deflBadge(r.deflation_tag)}</div>
          <div class="xs">${r.market === "cn" ? "国内" : "北美"} · ${r.exchange || r.sector} · ${r.symbol}</div>
          <div class="xp ${tone === "low" ? "down" : "up"}">${fmt(r.price)}</div>
          <div class="xrow"><span>5年分位</span><b>${pct5}</b></div>
          <div class="xrow"><span>成本中枢</span><b>${cost}</b></div>
          <div class="xrow"><span>vs成本</span><b class="${r.vs_primary_pct != null && r.vs_primary_pct < 0 ? "down" : "up"}">${vs}</b></div>
          <div class="xrow"><span>${tone === "low" ? "距高点" : "距低点"}</span><b>${
            tone === "low"
              ? r.from_max_pct != null
                ? Number(r.from_max_pct).toFixed(1) + "%"
                : "—"
              : r.from_min_pct != null
              ? Number(r.from_min_pct).toFixed(1) + "%"
              : "—"
          }</b></div>
          <div class="xtag ${cardTagClass(r.tag)}" title="${r.tag || ""}">${r.tag || ""}</div>
          ${putLink}
        </div>`;
      })
      .join("");
  }

  function renderAnalysis(data) {
    state.currentAnalysis = data;
    if (!data) {
      analysisBody.innerHTML = `<p class="muted">选择品种后显示历史分位、成本价与文字分析。</p>`;
      analysisTag.textContent = "—";
      return;
    }
    const tag = data.tag || (data.analysis && data.analysis.tag) || "—";
    const dtag = data.deflation_tag || "";
    analysisTag.innerHTML = `<span class="tag-label ${tagClass(tag)}">${tag}</span> ${deflBadge(dtag)}`;

    const cost = data.cost || (data.analysis && data.analysis.cost);
    const paras = (data.analysis && data.analysis.paragraphs) || [];

    let kpis = `
      <div class="analysis-kpis">
        <div class="akpi"><div class="l">最新价</div><div class="v">${fmt(data.price)}</div></div>
        <div class="akpi"><div class="l">全样本分位</div><div class="v">${fmt(data.pct_all, 1)}%</div></div>
        <div class="akpi"><div class="l">近5年分位</div><div class="v">${fmt(data.pct_5y, 1)}%</div></div>
        <div class="akpi"><div class="l">近10年分位</div><div class="v">${fmt(data.pct_10y, 1)}%</div></div>
        <div class="akpi"><div class="l">距历史高点</div><div class="v ${data.from_max_pct < 0 ? "down" : "up"}">${
          data.from_max_pct != null ? fmt(data.from_max_pct, 1) + "%" : "—"
        }</div></div>
        <div class="akpi"><div class="l">近1年涨跌</div><div class="v ${clsByChange(data.ret_1y)}">${
          data.ret_1y != null ? (data.ret_1y > 0 ? "+" : "") + fmt(data.ret_1y, 1) + "%" : "—"
        }</div></div>
      </div>`;

    let costHtml = "";
    if (cost) {
      costHtml = `
        <div class="akpi" style="margin-bottom:10px">
          <div class="l">成本锚定 · ${cost.label || ""}</div>
          <div class="v" style="font-size:0.85rem;font-weight:600;margin-top:6px;line-height:1.5">
            现金成本 <b class="down">${fmt(cost.cash_cost)}</b>
            · 完全成本中枢 <b>${fmt(cost.primary_cost)}</b>
            · 激励上沿 <b class="up">${fmt(cost.comfort_high)}</b>
            <span style="color:var(--muted)"> ${cost.unit || ""}</span><br/>
            现价 vs 完全成本
            <b class="${cost.vs_primary_pct < 0 ? "down" : "up"}">${
              cost.vs_primary_pct != null
                ? (cost.vs_primary_pct > 0 ? "+" : "") + fmt(cost.vs_primary_pct, 1) + "%"
                : "—"
            }</b>
            · 位置：${cost.position_vs_band || "—"}
          </div>
        </div>`;
      if (cost.notes && cost.notes.length) {
        costHtml += `<table class="cost-table"><thead><tr><th>成本/参照项</th><th class="num">水平</th><th>说明</th></tr></thead><tbody>`;
        for (const n of cost.notes) {
          costHtml += `<tr><td>${n.name}</td><td class="num">${fmt(n.value)}</td><td>${n.note}</td></tr>`;
        }
        costHtml += `</tbody></table>`;
        costHtml += `<p class="muted" style="font-size:0.75rem">来源：${cost.source || "行业估计"}。成本为静态研究锚，非实时工厂成本。</p>`;
      }
    } else {
      costHtml = `<p class="muted">该品种暂无配置生产成本锚（如股指/汇率），高低位主要依据历史价格分位。</p>`;
    }

    const text = paras.map((p) => `<p>${p}</p>`).join("");
    let cycleHtml = "";
    const cy = data.analysis && data.analysis.cycle;
    if (cy && (cy.lag || cy.status)) {
      cycleHtml = `<div class="akpi" style="margin-bottom:10px">
        <div class="l">减产周期 · 当前供给</div>
        <div class="v" style="font-size:0.82rem;font-weight:600;margin-top:6px;line-height:1.55">
          亏损→可见减产：${cy.lag || "—"}<br/>
          升值更常见于：${cy.to_price || "—"}<br/>
          ${cy.status ? `当前：${cy.status}` : "当前：该品种暂无专项开工跟踪"}
        </div>
      </div>`;
    }
    analysisBody.innerHTML = kpis + costHtml + cycleHtml + text;
  }

  async function loadQuotes(force = false) {
    try {
      setStatus(true, "刷新中…");
      const url = apiUrl(force ? "/api/quotes?force=1" : "/api/quotes");
      const res = await fetch(url, { signal: AbortSignal.timeout(60000) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "quotes failed");
      state.quotes = data.items || [];
      rebuildSectors();
      renderTable();
      renderExtremeCards(lowsCards, state.lows, "low");
      renderExtremeCards(highsCards, state.highs, "high");
      const withPx = data.with_price != null ? data.with_price : state.quotes.filter((q) => q.price != null).length;
      const cnN = state.quotes.filter((q) => q.market === "cn").length;
      const usN = state.quotes.filter((q) => q.market === "us").length;
      const qMeta = `共 ${data.count || state.quotes.length} 品种（国内${cnN}/北美${usN}）· 有价 ${withPx} · ${data.updated_at || "—"}`;
      quoteMeta.textContent = qMeta;
      quoteMeta.title = data.note ? qMeta + " · " + data.note : qMeta;
      setStatus(true, "已连接 · 定时刷新");
      if (!state.selectedId && state.quotes.length) {
        selectInstrument(state.quotes[0].id);
      } else if (state.selectedId) {
        renderTable();
      }
    } catch (err) {
      console.error(err);
      const timedOut = err && (err.name === "TimeoutError" || err.name === "AbortError");
      setStatus(false, timedOut ? "冷启动较慢，请再点一次刷新" : "刷新失败");
      quoteMeta.textContent = timedOut
        ? "行情接口超时（冷启动/Yahoo 限流），请点「立即刷新」或稍后再试"
        : String(err.message || err);
    }
  }

  async function loadExtremes(force = false) {
    try {
      const url = apiUrl(force ? "/api/analysis/all?force=1" : "/api/analysis/all");
      const res = await fetch(url, { signal: AbortSignal.timeout(60000) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "extremes failed");

      state.lows = (data.lows || []).map(slimFromFull);
      state.highs = (data.highs || []).map(slimFromFull);

      const map = {};
      for (const r of data.all || []) map[r.id] = slimFromFull(r);
      // ensure lows/highs present even if all missing fields
      for (const r of data.lows || []) map[r.id] = { ...map[r.id], ...slimFromFull(r) };
      for (const r of data.highs || []) map[r.id] = { ...map[r.id], ...slimFromFull(r) };
      state.analysisMap = map;

      $("#lows-meta").textContent = `${state.lows.length} 个 · 更新 ${data.updated_at || "—"}`;
      $("#highs-meta").textContent = `${state.highs.length} 个 · 更新 ${data.updated_at || "—"}`;
      renderExtremeCards(lowsCards, state.lows, "low");
      renderExtremeCards(highsCards, state.highs, "high");
      renderTable();
    } catch (err) {
      console.error(err);
      lowsCards.innerHTML = `<div class="empty-card">高低位加载失败：${err.message || err}</div>`;
      highsCards.innerHTML = `<div class="empty-card">高低位加载失败：${err.message || err}</div>`;
    }
  }

  function slimFromFull(r) {
    const c = r.cost || (r.analysis && r.analysis.cost) || {};
    return {
      id: r.id,
      name: r.name,
      symbol: r.symbol,
      market: r.market,
      sector: r.sector,
      price: r.price,
      tag: r.tag,
      deflation_tag: r.deflation_tag,
      deflation_note: r.deflation_note,
      pct_all: r.pct_all,
      pct_5y: r.pct_5y,
      pct_10y: r.pct_10y,
      low_score: r.low_score,
      high_score: r.high_score,
      from_max_pct: r.from_max_pct,
      from_min_pct: r.from_min_pct,
      ret_1y: r.ret_1y,
      primary_cost: firstDef(c.primary_cost, r.primary_cost),
      cash_cost: firstDef(c.cash_cost, r.cash_cost),
      comfort_high: firstDef(c.comfort_high, r.comfort_high),
      cost_unit: c.unit || r.cost_unit,
      vs_primary_pct: firstDef(c.vs_primary_pct, r.vs_primary_pct),
      vs_cash_pct: firstDef(c.vs_cash_pct, r.vs_cash_pct),
      cost_band: c.position_vs_band || r.cost_band,
      cost: Object.keys(c).length ? c : r.cost || null,
      analysis: r.analysis,
    };
  }

  function firstDef(a, b) {
    return a !== null && a !== undefined ? a : b;
  }

  async function loadAnalysis(id) {
    try {
      const res = await fetch(apiUrl(`/api/analysis?id=${encodeURIComponent(id)}`));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "analysis failed");
      // update map
      state.analysisMap[id] = slimFromFull(data);
      renderAnalysis(data);
      renderTable();
      return data;
    } catch (err) {
      analysisBody.innerHTML = `<p class="muted">分析加载失败：${err.message || err}</p>`;
      return null;
    }
  }

  function resizeOne(chart, el) {
    if (!chart || !el) return;
    const w = el.clientWidth;
    const h = el.clientHeight;
    if (w > 40 && h > 80) chart.resize({ width: w, height: h });
  }

  function resizeChart() {
    resizeOne(state.chart, $("#chart"));
    if (state.chartOpen) resizeOne(state.chartLarge, $("#chart-large"));
  }

  function buildChartOption(payload, large) {
    const bars = payload.bars || [];
    const cats = bars.map((b) => b.date);
    const ohlc = bars.map((b) => [b.open, b.close, b.low, b.high]);
    const vols = bars.map((b) => (b.volume == null || Number.isNaN(Number(b.volume)) ? 0 : Number(b.volume)));
    const closes = bars.map((b) => b.close);
    const volColors = bars.map((b) =>
      b.close >= b.open ? "rgba(239,83,80,0.75)" : "rgba(38,166,154,0.75)"
    );
    const cost =
      state.currentAnalysis &&
      (state.currentAnalysis.cost || (state.currentAnalysis.analysis && state.currentAnalysis.analysis.cost));
    const markLines = [];
    const legendData = large ? ["K线", "收盘", "成交量"] : ["K线", "收盘"];
    if (cost) {
      markLines.push(
        { yAxis: cost.cash_cost, name: "现金成本", lineStyle: { color: "#26a69a", type: "dashed", width: 1.2 } },
        { yAxis: cost.primary_cost, name: "完全成本", lineStyle: { color: "#e67e22", type: "solid", width: 1.4 } },
        { yAxis: cost.comfort_high, name: "激励上沿", lineStyle: { color: "#ef5350", type: "dotted", width: 1.2 } }
      );
    }
    const zoomStart = Math.max(0, 100 - (state.interval === "Y" ? 100 : state.interval === "M" ? 80 : 45));
    const grids = large
      ? [
          { left: 68, right: 18, top: 36, bottom: 168 },
          { left: 68, right: 18, height: 110, bottom: 46 },
        ]
      : [{ left: 56, right: 12, top: 28, bottom: 28 }];
    const xAxis = [
      {
        type: "category",
        data: cats,
        boundaryGap: true,
        axisLine: { lineStyle: { color: "#243041" } },
        axisLabel: { show: !large, color: "#8b9bb0", hideOverlap: true, fontSize: 10 },
        axisTick: { show: false },
        splitLine: { show: false },
        min: "dataMin",
        max: "dataMax",
      },
    ];
    const yAxis = [
      {
        scale: true,
        axisLine: { show: false },
        axisLabel: { color: "#8b9bb0", fontSize: 10 },
        splitLine: { lineStyle: { color: "#1a2330" } },
      },
    ];
    const series = [
      {
        name: "K线",
        type: "candlestick",
        data: ohlc,
        itemStyle: {
          color: "#ef5350",
          color0: "#26a69a",
          borderColor: "#ef5350",
          borderColor0: "#26a69a",
        },
        markLine: markLines.length
          ? { symbol: "none", label: { color: "#c5d4e8", fontSize: 10, position: "insideEndTop" }, data: markLines }
          : undefined,
      },
      {
        name: "收盘",
        type: "line",
        data: closes,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1, color: "rgba(61,156,240,0.55)" },
        z: 1,
      },
    ];
    if (large) {
      xAxis.push({
        type: "category",
        gridIndex: 1,
        data: cats,
        boundaryGap: true,
        axisLabel: { color: "#8b9bb0", hideOverlap: true, fontSize: 11 },
        axisLine: { lineStyle: { color: "#243041" } },
        axisTick: { show: false },
        splitLine: { show: false },
        min: "dataMin",
        max: "dataMax",
      });
      yAxis.push({
        scale: false,
        min: 0,
        gridIndex: 1,
        splitNumber: 2,
        name: "成交量",
        nameTextStyle: { color: "#8b9bb0", fontSize: 11 },
        axisLabel: {
          show: true,
          color: "#8b9bb0",
          fontSize: 10,
          formatter: (v) => (v >= 1e8 ? (v / 1e8).toFixed(1) + "亿" : v >= 1e4 ? (v / 1e4).toFixed(0) + "万" : v),
        },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
      });
      series.push({
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: vols.map((v, i) => ({ value: v, itemStyle: { color: volColors[i] } })),
      });
    }
    return {
      backgroundColor: "transparent",
      animation: false,
      legend: { data: legendData, textStyle: { color: "#8b9bb0" }, top: 2, right: 8 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: "rgba(18,24,33,0.95)",
        borderColor: "#243041",
        textStyle: { color: "#e8eef7" },
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: grids,
      xAxis,
      yAxis,
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: large ? [0, 1] : [0],
          start: zoomStart,
          end: 100,
        },
        {
          show: true,
          xAxisIndex: large ? [0, 1] : [0],
          type: "slider",
          bottom: 6,
          height: large ? 22 : 16,
          borderColor: "#243041",
          backgroundColor: "#121821",
          fillerColor: "rgba(61,156,240,0.15)",
          handleStyle: { color: "#3d9cf0" },
          textStyle: { color: "#8b9bb0", fontSize: 10 },
        },
      ],
      series,
    };
  }

  function ensureChart() {
    const el = $("#chart");
    if (!el) return null;
    if (!state.chart) {
      state.chart = echarts.init(el, null, { renderer: "canvas" });
      window.addEventListener("resize", resizeChart);
      if (typeof ResizeObserver !== "undefined") {
        const ro = new ResizeObserver(() => resizeChart());
        ro.observe(el);
        if (el.parentElement) ro.observe(el.parentElement);
      }
    }
    resizeChart();
    return state.chart;
  }

  async function loadHistory() {
    if (!state.selectedId || state.loadingHistory) return;
    state.loadingHistory = true;
    const chart = ensureChart();
    if (!chart) {
      state.loadingHistory = false;
      return;
    }
    chart.showLoading("default", { text: "加载 K 线…", maskColor: "rgba(11,15,20,0.55)", color: "#3d9cf0" });
    try {
      const url = `/api/history?id=${encodeURIComponent(state.selectedId)}&interval=${state.interval}`;
      const res = await fetch(apiUrl(url));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "history failed");

      const meta = state.quotes.find((q) => q.id === state.selectedId);
      chartTitle.textContent = `${data.name} (${data.symbol})`;
      const ivLabel = { D: "日线", W: "周线", M: "月线", Y: "年线" }[data.interval] || data.interval;
      chartSub.textContent = `${data.market === "cn" ? "国内" : "外盘"} · ${ivLabel} · ${data.bars.length} 根K线`;

      const cost =
        state.currentAnalysis &&
        (state.currentAnalysis.cost || (state.currentAnalysis.analysis && state.currentAnalysis.analysis.cost));
      if (cost) {
        chartFoot.textContent = `点击放大看成交量 · 成本：现金 ${fmt(cost.cash_cost)} / 完全 ${fmt(cost.primary_cost)} / 上沿 ${fmt(cost.comfort_high)} ${cost.unit || ""}`;
      } else {
        chartFoot.textContent = `点击图或「放大看全」可看成交量 · 更新 ${data.updated_at || "—"}`;
      }
      state.lastHistory = data;
      chart.setOption(buildChartOption(data, false), true);
      resizeChart();
      if (state.chartOpen) renderLargeChart();
      if (meta) chartTitle.textContent = `${data.name} (${data.symbol})`;

    } catch (err) {
      console.error(err);
      chartFoot.textContent = "K线加载失败: " + (err.message || err);
      chart.clear();
    } finally {
      chart.hideLoading();
      state.loadingHistory = false;
    }
  }

  function renderLargeChart() {
    const el = $("#chart-large");
    if (!el || !state.lastHistory) return;
    if (!state.chartLarge) {
      state.chartLarge = echarts.init(el, null, { renderer: "canvas" });
    }
    state.chartLarge.setOption(buildChartOption(state.lastHistory, true), true);
    resizeOne(state.chartLarge, el);
  }

  function openChartModal() {
    const modal = $("#chart-modal");
    if (!modal) return;
    if (!state.lastHistory) {
      if (state.selectedId) loadHistory();
      return;
    }
    modal.hidden = false;
    state.chartOpen = true;
    const t = $("#chart-modal-title");
    const s = $("#chart-modal-sub");
    if (t) t.textContent = `${state.lastHistory.name} (${state.lastHistory.symbol})`;
    const ivLabel = { D: "日线", W: "周线", M: "月线", Y: "年线" }[state.interval] || state.interval;
    if (s) s.textContent = `${ivLabel} · ${state.lastHistory.bars.length} 根 · 含成交量`;
    document.querySelectorAll("#interval-seg-modal button").forEach((b) => {
      b.classList.toggle("active", b.dataset.interval === state.interval);
    });
    setTimeout(() => {
      renderLargeChart();
      resizeOne(state.chartLarge, $("#chart-large"));
    }, 40);
  }

  function closeChartModal() {
    const modal = $("#chart-modal");
    if (modal) modal.hidden = true;
    state.chartOpen = false;
  }

  async function selectInstrument(id) {
    state.selectedId = id;
    renderTable();
    renderExtremeCards(lowsCards, state.lows, "low");
    renderExtremeCards(highsCards, state.highs, "high");
    if (state.view !== "watch") switchView("watch");
    // analysis first so chart can draw cost lines
    await loadAnalysis(id);
    await loadHistory();
  }

  function switchView(name) {
    state.view = name;
    document.querySelectorAll(".view").forEach((el) => {
      el.classList.toggle("active", el.id === "view-" + name);
    });
    document.querySelectorAll("#view-tabs button").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === name);
    });
    if (name === "watch") {
      setTimeout(resizeChart, 50);
      setTimeout(resizeChart, 250);
    }
    if (name === "movers") {
      loadMoversBoard(false);
    }
    if (name === "ng") {
      state.ngLoading = false;
      loadNgDashboard(false);
      setTimeout(resizeNgCharts, 60);
      setTimeout(resizeNgCharts, 250);
    }
    if (name === "put") {
      loadShortPut(false);
    }
    if (name === "call") {
      loadShortCall(false);
    }
    if (name === "cnidx") {
      state.cnidxLoading = false;
      loadCnIndices(false);
    }
  }

  function bindEvents() {
    const tabs = $("#view-tabs");
    if (tabs) {
      tabs.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-view]");
        if (!btn) return;
        switchView(btn.dataset.view);
      });
    }

    $("#market-filter").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-market]");
      if (!btn) return;
      state.market = btn.dataset.market;
      [...$("#market-filter").querySelectorAll("button")].forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      renderTable();
    });

    if (sectorFilter) {
      sectorFilter.addEventListener("change", () => {
        state.sector = sectorFilter.value || "all";
        renderTable();
      });
    }

    function bindIntervalBar(sel) {
      const bar = $(sel);
      if (!bar) return;
      bar.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-interval]");
        if (!btn) return;
        state.interval = btn.dataset.interval;
        document.querySelectorAll("#interval-seg button, #interval-seg-modal button").forEach((b) =>
          b.classList.toggle("active", b.dataset.interval === state.interval)
        );
        loadHistory();
      });
    }
    bindIntervalBar("#interval-seg");
    bindIntervalBar("#interval-seg-modal");

    const stage = $("#chart-stage");
    if (stage) stage.addEventListener("click", openChartModal);
    const btnExp = $("#btn-chart-expand");
    if (btnExp) btnExp.addEventListener("click", (e) => { e.stopPropagation(); openChartModal(); });
    const btnClose = $("#btn-chart-close");
    if (btnClose) btnClose.addEventListener("click", closeChartModal);
    const modal = $("#chart-modal");
    if (modal) {
      modal.addEventListener("click", (e) => {
        if (e.target === modal) closeChartModal();
      });
    }
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && state.chartOpen) closeChartModal();
    });

    quoteBody.addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-id]");
      if (!tr) return;
      selectInstrument(tr.dataset.id);
    });

    const quoteHead = $("#quote-head");
    if (quoteHead) {
      quoteHead.addEventListener("click", (e) => {
        const th = e.target.closest("th.sortable[data-sort]");
        if (!th) return;
        const key = th.dataset.sort;
        if (state.sortKey === key) {
          // cycle: asc -> desc -> none
          if (state.sortDir === "asc") {
            state.sortDir = "desc";
          } else {
            state.sortKey = null;
            state.sortDir = "asc";
          }
        } else {
          state.sortKey = key;
          // 5年分位 default: low first (find historical lows quickly)
          state.sortDir = key === "pct_5y" ? "asc" : "desc";
        }
        renderTable();
      });
    }

    function setPct5Sort(dir) {
      if (dir === null) {
        state.sortKey = null;
        state.sortDir = "asc";
      } else {
        state.sortKey = "pct_5y";
        state.sortDir = dir;
      }
      renderTable();
    }
    const btnAsc = $("#btn-sort-pct5-asc");
    const btnDesc = $("#btn-sort-pct5-desc");
    const btnClear = $("#btn-sort-clear");
    if (btnAsc) btnAsc.addEventListener("click", () => setPct5Sort("asc"));
    if (btnDesc) btnDesc.addEventListener("click", () => setPct5Sort("desc"));
    if (btnClear) btnClear.addEventListener("click", () => setPct5Sort(null));

    lowsCards.addEventListener("click", (e) => {
      const card = e.target.closest(".xcard[data-id]");
      if (!card) return;
      selectInstrument(card.dataset.id);
    });
    highsCards.addEventListener("click", (e) => {
      const card = e.target.closest(".xcard[data-id]");
      if (!card) return;
      selectInstrument(card.dataset.id);
    });

    $("#search").addEventListener("input", (e) => {
      state.search = e.target.value.trim();
      renderTable();
    });

    const kindBar = $("#supply-kind");
    if (kindBar) {
      kindBar.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-kind]");
        if (!btn) return;
        state.supplyKind = btn.dataset.kind;
        [...kindBar.querySelectorAll("button")].forEach((b) => b.classList.toggle("active", b === btn));
        if (state.supplyPayload) renderSupply(state.supplyPayload);
      });
    }
    const btnSupply = $("#btn-supply");
    if (btnSupply) btnSupply.addEventListener("click", () => loadSupply(true));

    const usPeriod = $("#us-period");
    if (usPeriod) {
      usPeriod.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-period]");
        if (!btn) return;
        state.usPeriod = btn.dataset.period;
        [...usPeriod.querySelectorAll("button")].forEach((b) =>
          b.classList.toggle("active", b === btn)
        );
        loadMoversBoard(false);
      });
    }
    const moversAsset = $("#movers-asset");
    if (moversAsset) {
      moversAsset.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-asset]");
        if (!btn) return;
        state.moversAsset = btn.dataset.asset;
        [...moversAsset.querySelectorAll("button")].forEach((b) =>
          b.classList.toggle("active", b === btn)
        );
        loadMoversBoard(false);
      });
    }
    const fxMarket = $("#fx-market");
    if (fxMarket) {
      fxMarket.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-market]");
        if (!btn) return;
        state.fxMarket = btn.dataset.market;
        [...fxMarket.querySelectorAll("button")].forEach((b) =>
          b.classList.toggle("active", b === btn)
        );
        loadFutMovers(false);
      });
    }
    const usKind = $("#us-kind");
    if (usKind) {
      usKind.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-kind]");
        if (!btn) return;
        state.usKind = btn.dataset.kind;
        [...usKind.querySelectorAll("button")].forEach((b) =>
          b.classList.toggle("active", b === btn)
        );
        loadUsMovers(false);
      });
    }
    const btnUs = $("#btn-us-refresh");
    if (btnUs) btnUs.addEventListener("click", () => loadUsMovers(true));
    function bindUsTable(sel) {
      const body = $(sel);
      if (!body) return;
      body.addEventListener("click", (e) => {
        const tr = e.target.closest("tr[data-id], tr[data-symbol]");
        if (!tr) return;
        if (tr.dataset.id) {
          selectInstrument(tr.dataset.id);
          return;
        }
        if (tr.dataset.symbol) {
          window.open(
            "https://finance.yahoo.com/quote/" + encodeURIComponent(tr.dataset.symbol),
            "_blank",
            "noopener"
          );
        }
      });
    }
    bindUsTable("#us-gainers");
    bindUsTable("#us-losers");
    bindUsTable("#us-lev-gainers");
    bindUsTable("#us-lev-losers");
    const supplyBody = $("#supply-body");
    if (supplyBody) {
      supplyBody.addEventListener("click", (e) => {
        const tr = e.target.closest("tr[data-id]");
        if (!tr || !tr.dataset.id) return;
        selectInstrument(tr.dataset.id);
      });
    }

    const btnNgRefresh = $("#btn-ng-refresh");
    if (btnNgRefresh) {
      btnNgRefresh.addEventListener("click", () => loadNgDashboard(true));
    }
    const btnPutRefresh = $("#btn-put-refresh");
    if (btnPutRefresh) {
      btnPutRefresh.addEventListener("click", () => loadShortPut(true));
    }
    const putKind = $("#put-kind-filter");
    if (putKind) {
      putKind.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-kind]");
        if (!btn) return;
        state.putKind = btn.dataset.kind || "all";
        [...putKind.querySelectorAll("button")].forEach((b) =>
          b.classList.toggle("active", b === btn)
        );
        if (state.putPayload) renderShortPut(state.putPayload);
      });
    }
    const putFilters = $("#put-filters");
    if (putFilters) {
      putFilters.addEventListener("change", () => {
        if (state.putPayload) renderShortPut(state.putPayload);
      });
      putFilters.addEventListener("input", () => {
        if (state.putPayload) renderShortPut(state.putPayload);
      });
    }
    const btnReset = $("#btn-put-filter-reset");
    if (btnReset) {
      btnReset.addEventListener("click", () => {
        state.putFilters = putFilterDefaults();
        state.putKind = "all";
        const map = {
          "#put-f-hv": 50,
          "#put-f-rsi-lo": 25,
          "#put-f-rsi-hi": 65,
          "#put-f-oi": 50,
          "#put-f-yield": 0,
          "#put-f-otm": 15,
        };
        Object.entries(map).forEach(([sel, v]) => {
          const el = $(sel);
          if (el) el.value = v;
        });
        if ($("#put-f-core")) $("#put-f-core").checked = false;
        if ($("#put-f-noearn")) $("#put-f-noearn").checked = true;
        if ($("#put-f-ivrich")) $("#put-f-ivrich").checked = false;
        if (putKind) {
          [...putKind.querySelectorAll("button")].forEach((b) =>
            b.classList.toggle("active", b.dataset.kind === "all")
          );
        }
        if (state.putPayload) renderShortPut(state.putPayload);
      });
    }
    const themeBar = $("#put-theme-bar");
    if (themeBar) {
      themeBar.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-theme]");
        if (!btn) return;
        const id = btn.dataset.theme;
        if (id === "all") {
          state.putFilters = state.putFilters || putFilterDefaults();
          state.putFilters.themeSymbols = null;
          if (state.putPayload) renderShortPut(state.putPayload);
          return;
        }
        const th = ((state.putPayload && state.putPayload.themes) || []).find((x) => x.id === id);
        openPutTheme(id, th ? th.symbols : null);
      });
    }
    document.addEventListener("click", (e) => {
      const w = e.target.closest(".btn-put-watch");
      if (w && w.dataset.symbol) {
        addPutWatch(w.dataset.symbol);
        return;
      }
      const c = e.target.closest(".btn-put-close");
      if (c && c.dataset.id) {
        closePutWatch(c.dataset.id);
        return;
      }
      const d = e.target.closest(".btn-put-drop");
      if (d && d.dataset.id) {
        savePutWatch(loadPutWatch().filter((x) => x.id !== d.dataset.id));
        renderPutWatch();
        return;
      }
      if (e.target.id === "btn-ng-to-put" || e.target.closest("#btn-ng-to-put")) {
        openPutTheme("ng", ["UNG"]);
        return;
      }
      const ep = e.target.closest(".btn-ext-put");
      if (ep) {
        const syms = (ep.dataset.symbols || "").split(",").filter(Boolean);
        openPutTheme(ep.dataset.theme || "theme", syms);
        return;
      }
      const ch = e.target.closest(".btn-put-chain");
      if (ch && ch.dataset.symbol) {
        openPutChain(ch.dataset.symbol);
        return;
      }

      const cw = e.target.closest(".btn-call-watch");
      if (cw && cw.dataset.symbol) {
        addCallWatch(cw.dataset.symbol);
        return;
      }
      const cc = e.target.closest(".btn-call-close");
      if (cc && cc.dataset.id) {
        closeCallWatch(cc.dataset.id);
        return;
      }
      const cd = e.target.closest(".btn-call-drop");
      if (cd && cd.dataset.id) {
        saveCallWatch(loadCallWatch().filter((x) => x.id !== cd.dataset.id));
        renderCallWatch();
        return;
      }
      const cch = e.target.closest(".btn-call-chain");
      if (cch && cch.dataset.symbol) {
        openCallChain(cch.dataset.symbol);
        return;
      }
    });
    const btnPutWatchClear = $("#btn-put-watch-clear");
    if (btnPutWatchClear) {
      btnPutWatchClear.addEventListener("click", () => {
        savePutWatch(loadPutWatch().filter((x) => x.status === "open"));
        renderPutWatch();
      });
    }
    const putWeights = $("#put-weights");
    if (putWeights) {
      putWeights.addEventListener("input", () => {
        readEdgeWeights();
        if (state.putPayload) renderShortPut(state.putPayload);
      });
    }
    const btnWReset = $("#btn-put-weights-reset");
    if (btnWReset) {
      btnWReset.addEventListener("click", () => {
        const def = defaultEdgeWeights();
        const box = $("#put-weights");
        if (box) {
          box.querySelectorAll("input[data-w]").forEach((inp) => {
            inp.value = def[inp.dataset.w];
          });
        }
        readEdgeWeights();
        if (state.putPayload) renderShortPut(state.putPayload);
      });
    }
    $("#btn-put-chain-close")?.addEventListener("click", closePutChain);
    $("#put-chain-modal")?.addEventListener("click", (e) => {
      if (e.target.id === "put-chain-modal") closePutChain();
    });

    const btnCallRefresh = $("#btn-call-refresh");
    if (btnCallRefresh) {
      btnCallRefresh.addEventListener("click", () => loadShortCall(true));
    }
    const callKind = $("#call-kind-filter");
    if (callKind) {
      callKind.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-kind]");
        if (!btn) return;
        state.callKind = btn.dataset.kind || "all";
        [...callKind.querySelectorAll("button")].forEach((b) =>
          b.classList.toggle("active", b === btn)
        );
        if (state.callPayload) renderShortCall(state.callPayload);
      });
    }
    const callFilters = $("#call-filters");
    if (callFilters) {
      callFilters.addEventListener("change", () => {
        if (state.callPayload) renderShortCall(state.callPayload);
      });
      callFilters.addEventListener("input", () => {
        if (state.callPayload) renderShortCall(state.callPayload);
      });
    }
    const btnCallReset = $("#btn-call-filter-reset");
    if (btnCallReset) {
      btnCallReset.addEventListener("click", () => {
        state.callFilters = callFilterDefaults();
        state.callKind = "all";
        const map = {
          "#call-f-hv": 50,
          "#call-f-rsi-lo": 35,
          "#call-f-rsi-hi": 80,
          "#call-f-oi": 50,
          "#call-f-yield": 0,
          "#call-f-otm": 15,
        };
        Object.entries(map).forEach(([sel, v]) => {
          const el = $(sel);
          if (el) el.value = v;
        });
        if ($("#call-f-core")) $("#call-f-core").checked = false;
        if ($("#call-f-noearn")) $("#call-f-noearn").checked = true;
        if ($("#call-f-ivrich")) $("#call-f-ivrich").checked = false;
        if (callKind) {
          [...callKind.querySelectorAll("button")].forEach((b) =>
            b.classList.toggle("active", b.dataset.kind === "all")
          );
        }
        if (state.callPayload) renderShortCall(state.callPayload);
      });
    }
    const callThemeBar = $("#call-theme-bar");
    if (callThemeBar) {
      callThemeBar.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-theme]");
        if (!btn) return;
        const id = btn.dataset.theme;
        if (id === "all") {
          state.callFilters = state.callFilters || callFilterDefaults();
          state.callFilters.themeSymbols = null;
          if (state.callPayload) renderShortCall(state.callPayload);
          return;
        }
        const th = ((state.callPayload && state.callPayload.themes) || []).find((x) => x.id === id);
        openCallTheme(id, th ? th.symbols : null);
      });
    }
    const callWeights = $("#call-weights");
    if (callWeights) {
      callWeights.addEventListener("input", () => {
        readCallEdgeWeights();
        if (state.callPayload) renderShortCall(state.callPayload);
      });
    }
    const btnCWReset = $("#btn-call-weights-reset");
    if (btnCWReset) {
      btnCWReset.addEventListener("click", () => {
        const def = defaultEdgeWeights();
        const box = $("#call-weights");
        if (box) {
          box.querySelectorAll("input[data-w]").forEach((inp) => {
            inp.value = def[inp.dataset.w];
          });
        }
        readCallEdgeWeights();
        if (state.callPayload) renderShortCall(state.callPayload);
      });
    }
    $("#btn-call-chain-close")?.addEventListener("click", closeCallChain);
    $("#call-chain-modal")?.addEventListener("click", (e) => {
      if (e.target.id === "call-chain-modal") closeCallChain();
    });
    const btnCallWatchClear = $("#btn-call-watch-clear");
    if (btnCallWatchClear) {
      btnCallWatchClear.addEventListener("click", () => {
        saveCallWatch(loadCallWatch().filter((x) => x.status === "open"));
        renderCallWatch();
      });
    }
    const btnCnidxRefresh = $("#btn-cnidx-refresh");
    if (btnCnidxRefresh) {
      btnCnidxRefresh.addEventListener("click", () => loadCnIndices(true));
    }

    const newsFilter = $("#ng-news-filter");
    if (newsFilter) {
      newsFilter.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-cat]");
        if (!btn) return;
        state.ngNewsCat = btn.dataset.cat;
        [...newsFilter.querySelectorAll("button")].forEach((b) =>
          b.classList.toggle("active", b === btn)
        );
        if (state.ngPayload) renderNgNews(state.ngPayload.news);
      });
    }
    const calcContract = $("#ng-calc-contract");
    if (calcContract) {
      calcContract.addEventListener("change", (e) => {
        state.ngCalcContract = e.target.value;
        renderNgCalculator(state.ngPayload);
      });
    }
    const calcLots = $("#ng-calc-lots");
    if (calcLots) {
      calcLots.addEventListener("input", (e) => {
        state.ngCalcLots = Math.max(1, parseInt(e.target.value, 10) || 1);
        renderNgCalculator(state.ngPayload);
      });
    }

    $("#btn-refresh").addEventListener("click", () => loadQuotes(true));
    $("#btn-extremes").addEventListener("click", async () => {
      setStatus(true, "重算高低位…");
      await loadExtremes(true);
      setStatus(true, "高低位已更新");
      if (state.selectedId) await loadAnalysis(state.selectedId);
    });

    refreshSelect.value = String(state.refreshSec);
    refreshSelect.addEventListener("change", () => {
      state.refreshSec = Number(refreshSelect.value) || 20;
      startTimer();
    });
  }

  function startTimer() {
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(() => loadQuotes(false), state.refreshSec * 1000);
  }

  function sigClass(sig) {
    if (!sig) return "mid";
    if (/去化|去库|偏紧|亏损|低于/.test(sig)) return "low";
    if (/累库|回升|偏多|覆盖/.test(sig)) return "high";
    return "mid";
  }

  function supplyKindMatch(it, k) {
    if (!k || k === "all") return true;
    if (k === "fx") return it.kind === "fx" || it.kind === "macro";
    if (k === "inventory") return it.kind === "inventory" || it.kind === "cover" || it.kind === "ng";
    return it.kind === k;
  }

  function renderSupply(data) {
    const body = $("#supply-body");
    const meta = $("#supply-meta");
    if (!body) return;
    if (!data || data.error) {
      body.innerHTML = `<tr><td colspan="8" class="empty">供给数据加载失败 ${data && data.error ? data.error : ""}</td></tr>`;
      return;
    }
    state.supplyPayload = data;
    const all = data.items || [];
    const items = all.filter((it) => supplyKindMatch(it, state.supplyKind));
    if (meta) {
      meta.textContent = `显示 ${items.length}/${all.length} · 抓取 ${data.fetched_at || "—"} · ${data.note || ""}`;
    }
    if (!items.length) {
      body.innerHTML = `<tr><td colspan="8" class="empty">该分类暂无数据，请点「立即抓取」</td></tr>`;
      return;
    }
    body.innerHTML = items
      .map((it) => {
        const wow =
          it.wow == null
            ? "—"
            : (Number(it.wow) > 0 ? "+" : "") + fmt(it.wow, 2);
        const d4 =
          it.d4w == null
            ? "—"
            : (Number(it.d4w) > 0 ? "+" : "") + fmt(it.d4w, 2);
        const wowCls = it.wow == null || Number(it.wow) === 0 ? "flat" : Number(it.wow) > 0 ? "up" : "down";
        const d4Cls = it.d4w == null || Number(it.d4w) === 0 ? "flat" : Number(it.d4w) > 0 ? "up" : "down";
        return `<tr data-id="${it.id || ""}">
          <td>${it.name || "—"}</td>
          <td>${it.metric || "—"}</td>
          <td class="num">${fmt(it.value, 2)}${it.unit ? " " + it.unit : ""}</td>
          <td class="num ${wowCls}">${wow}</td>
          <td class="num ${d4Cls}">${d4}</td>
          <td><span class="tag-label ${sigClass(it.signal)}">${it.signal || "—"}</span></td>
          <td>${it.asof || "—"}</td>
          <td>${it.source || "—"}</td>
        </tr>`;
      })
      .join("");
  }

  function kindBadge(kind, leverage) {
    if (kind === "lev") {
      const n = leverage == null || leverage === "" ? "多倍" : Number(leverage) + "x";
      return `<span class="badge lev">${n}</span>`;
    }
    if (kind === "etf") return '<span class="badge etf">现货</span>';
    return '<span class="badge stock">公司</span>';
  }

  function futKindBadge(r) {
    const m = r.market === "cn" ? '<span class="badge cn">国内</span>' : '<span class="badge us">北美</span>';
    return m + (r.sector ? ` <span class="badge">${r.sector}</span>` : "");
  }

  function moverPct(v) {
    if (v === null || v === undefined) return "—";
    return (Number(v) > 0 ? "+" : "") + fmt(v, 2) + "%";
  }

  function pickMoversPeriod(data, period) {
    if (!data) return data;
    const p = (period || "W").toUpperCase();
    const periods = data.periods;
    if (periods && typeof periods === "object") {
      const chosen = periods[p] || periods.W || periods[data.default_period] || data;
      return chosen && typeof chosen === "object" ? chosen : data;
    }
    return data;
  }

  function moverRows(list, mode) {
    if (!list || !list.length) {
      return `<tr><td colspan="5" class="empty">暂无数据</td></tr>`;
    }
    return list
      .map((r, i) => {
        const cls = clsByChange(r.change_pct);
        const pct = moverPct(r.change_pct);
        const name = r.name || r.symbol || "—";
        if (mode === "fut") {
          const badges = futKindBadge(r);
          return `<tr data-id="${r.id}" title="打开 ${name} 走势">
            <td>${i + 1}</td>
            <td class="mover-name" title="${name} ${r.symbol || ""}">
              <div class="mover-title">${name}</div>
              <div class="mover-sub mono">${r.symbol || ""} · ${badges}</div>
            </td>
            <td class="num mover-pct ${cls}">${pct}</td>
            <td class="num">${fmt(r.price, 2)}</td>
          </tr>`;
        }
        const badge = kindBadge(r.kind, r.leverage);
        return `<tr data-symbol="${r.symbol}" title="打开 Yahoo 看 ${r.symbol}">
          <td>${i + 1}</td>
          <td class="mover-name" title="${name} ${r.symbol || ""}">
            <div class="mover-title">${name}</div>
            <div class="mover-sub mono">${r.symbol || ""} · ${badge}</div>
          </td>
          <td class="num mover-pct ${cls}">${pct}</td>
          <td class="num">${fmt(r.price, 2)}</td>
        </tr>`;
      })
      .join("");
  }

  function syncMoversToolbar() {
    const view = $("#view-movers");
    if (view) view.dataset.asset = state.moversAsset || "fut";
    const grid = document.querySelector(".movers-grid");
    if (grid) {
      grid.classList.toggle(
        "no-lev-col",
        state.moversAsset === "fut" || state.usKind === "lev"
      );
    }
  }

  function loadMoversBoard(force = false) {
    syncMoversToolbar();
    if (state.moversAsset === "us") loadUsMovers(force);
    else loadFutMovers(force);
  }

  function renderUsMovers(data) {
    const gain = $("#us-gainers");
    const lose = $("#us-losers");
    const levG = $("#us-lev-gainers");
    const levL = $("#us-lev-losers");
    const meta = $("#us-meta");
    const gMeta = $("#us-gain-meta");
    const lMeta = $("#us-lose-meta");
    const levMeta = $("#us-lev-meta");
    const grid = document.querySelector(".movers-grid");
    if (grid) grid.classList.toggle("no-lev-col", state.usKind === "lev");
    if (data && data.error) {
      const msg = `<tr><td colspan="4" class="empty">加载失败：${data.error}</td></tr>`;
      if (gain) gain.innerHTML = msg;
      if (lose) lose.innerHTML = msg;
      if (levG) levG.innerHTML = msg;
      if (levL) levL.innerHTML = msg;
      if (meta) meta.textContent = "美股涨跌榜加载失败";
      return;
    }
    state.usPayload = data;
    if (gain) gain.innerHTML = moverRows(data.gainers, "us");
    if (lose) lose.innerHTML = moverRows(data.losers, "us");
    if (levG) levG.innerHTML = moverRows(data.lev_gainers, "us");
    if (levL) levL.innerHTML = moverRows(data.lev_losers, "us");
    const label = data.period_label || "";
    const kindLabel = data.kind_label || "";
    if (meta) {
      const uni = typeof data.universe === "number" ? data.universe : "—";
      const scope = data.partial ? " · ⚠ 快照范围有限，非完整排名" : "";
      meta.textContent = `${label} · ${kindLabel} · 样本 ${uni} · 多倍 ${data.lev_universe || 0} · ${data.asof || "—"}${scope}`;
      meta.title = data.note || meta.textContent;
    }
    const partialTag = data.partial ? "（快照仅部分）" : "";
    if (gMeta) gMeta.textContent = `${label} ${kindLabel} 涨幅前 ${ (data.gainers || []).length }${partialTag}`;
    if (lMeta) lMeta.textContent = `${label} ${kindLabel} 跌幅前 ${ (data.losers || []).length }${partialTag}`;
    if (levMeta) levMeta.textContent = `${label} · ${data.lev_universe || 0} 只 2x/3x`;
  }

  function renderFutMovers(data) {
    const gain = $("#us-gainers");
    const lose = $("#us-losers");
    const meta = $("#us-meta");
    const gMeta = $("#us-gain-meta");
    const lMeta = $("#us-lose-meta");
    syncMoversToolbar();
    if (data && data.error) {
      const msg = `<tr><td colspan="4" class="empty">加载失败：${data.error}</td></tr>`;
      if (gain) gain.innerHTML = msg;
      if (lose) lose.innerHTML = msg;
      if (meta) meta.textContent = "期货涨跌榜加载失败";
      return;
    }
    state.fxPayload = data;
    if (gain) gain.innerHTML = moverRows(data.gainers, "fut");
    if (lose) lose.innerHTML = moverRows(data.losers, "fut");
    const label = data.period_label || "";
    const mlabel = { all: "全部", cn: "国内", us: "北美" }[data.market] || data.market;
    if (meta) {
      meta.textContent = `期货 · ${mlabel} · ${label} · ${data.universe || 0} 只 · ${data.asof || "—"}`;
      meta.title = data.note || meta.textContent;
    }
    if (gMeta) gMeta.textContent = `${label} 涨幅前 ${(data.gainers || []).length}`;
    if (lMeta) lMeta.textContent = `${label} 跌幅前 ${(data.losers || []).length}`;
  }

  async function loadFutMovers(force = false) {
    const gain = $("#us-gainers");
    const lose = $("#us-losers");
    const meta = $("#us-meta");
    const wait = "正在计算期货涨跌榜…";
    const waitRow = `<tr><td colspan="4" class="empty">${wait}</td></tr>`;
    if (gain) gain.innerHTML = waitRow;
    if (lose) lose.innerHTML = waitRow;
    if (meta) meta.textContent = wait;
    syncMoversToolbar();
    try {
      const q = new URLSearchParams({
        period: state.usPeriod,
        market: state.fxMarket,
        limit: "20",
      });
      const res = await fetch(apiUrl("/api/futures-movers?" + q.toString()), { signal: AbortSignal.timeout(60000) });
      let data = await res.json();
      if (!res.ok) throw new Error(data.error || "futures movers failed");
      data = pickMoversPeriod(data, state.usPeriod);
      renderFutMovers(data);
    } catch (err) {
      const msg = `<tr><td colspan="4" class="empty">加载失败：${err.message || err}</td></tr>`;
      if (gain) gain.innerHTML = msg;
      if (lose) lose.innerHTML = msg;
      if (meta) meta.textContent = "期货涨跌榜加载失败";
    }
  }

  async function loadUsMovers(force = false) {
    const gain = $("#us-gainers");
    const lose = $("#us-losers");
    const levG = $("#us-lev-gainers");
    const levL = $("#us-lev-losers");
    const meta = $("#us-meta");
    const wait = force
      ? "正在重新从 Yahoo 拉取美股/ETF 行情…"
      : "正在计算涨跌榜（首次拉取约需 1–2 分钟）…";
    const waitRow = `<tr><td colspan="4" class="empty">${wait}</td></tr>`;
    if (gain) gain.innerHTML = waitRow;
    if (lose) lose.innerHTML = waitRow;
    if (levG) levG.innerHTML = waitRow;
    if (levL) levL.innerHTML = waitRow;
    if (meta) meta.textContent = wait;
    state.usLoading = true;
    // Request sequence: a slower older response/error/finally must not overwrite a newer one.
    const seq = (state.usReqSeq = (state.usReqSeq || 0) + 1);
    const reqKind = state.usKind;
    const reqPeriod = state.usPeriod;
    try {
      const q = new URLSearchParams({
        period: reqPeriod,
        kind: reqKind,
        limit: "20",
      });
      if (force) q.set("force", "1");
      const res = await fetch(apiUrl("/api/us-movers?" + q.toString()), { signal: AbortSignal.timeout(60000) });
      let data = await res.json();
      if (seq !== state.usReqSeq) return;
      if (!res.ok) throw new Error(data.error || "us movers failed");
      data = pickMoversPeriod(data, reqPeriod);
      // Static ./api/us_movers.json and old backends ignore kind: derive the same view client-side.
      if (typeof window.usMoversView === "function") data = window.usMoversView(data, reqKind, 20);
      renderUsMovers(data);
    } catch (err) {
      if (seq !== state.usReqSeq) return;
      const msg = `<tr><td colspan="4" class="empty">加载失败：${err.message || err}</td></tr>`;
      if (gain) gain.innerHTML = msg;
      if (lose) lose.innerHTML = msg;
      if (levG) levG.innerHTML = msg;
      if (levL) levL.innerHTML = msg;
      if (meta) meta.textContent = "美股涨跌榜加载失败";
    } finally {
      if (seq === state.usReqSeq) state.usLoading = false;
    }
  }

  async function loadSupply(force = false) {
    const body = $("#supply-body");
    try {
      const url = force ? "/api/supply/refresh" : "/api/supply";
      if (force && body) body.innerHTML = `<tr><td colspan="8" class="empty">正在抓取仓单/能繁/气库存…</td></tr>`;
      const res = await fetch(apiUrl(url));
      const data = await res.json();
      renderSupply(data);
    } catch (err) {
      if (body) body.innerHTML = `<tr><td colspan="8" class="empty">供给抓取失败：${err.message || err}</td></tr>`;
    }
  }

  /* ============================================================
   * NYMEX NG (Henry Hub) Dedicated Dashboard
   * ============================================================ */

  function getNgChart(id) {
    const el = $(`#${id}`);
    if (!el) return null;
    if (!state.ngCharts[id]) {
      state.ngCharts[id] = echarts.init(el, null, { renderer: "canvas" });
    }
    return state.ngCharts[id];
  }

  function resizeNgCharts() {
    if (!state.ngCharts) return;
    Object.keys(state.ngCharts).forEach((id) => {
      const el = $(`#${id}`);
      const chart = state.ngCharts[id];
      if (el && chart) resizeOne(chart, el);
    });
  }

  async function loadNgDashboard(force = false) {
    if (state.ngLoading && !force) return;
    state.ngLoading = true;
    const btn = $("#btn-ng-refresh");
    if (btn) {
      btn.disabled = true;
      btn.textContent = force ? "排队更新…" : "加载中…";
    }
    const tryOnce = async (url, ms) => {
      const res = await fetch(apiUrl(url), { signal: AbortSignal.timeout(ms) });
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || "NG dashboard failed");
      return data;
    };
    try {
      // Prefer lightweight seed endpoint; refresh only queues bg work on Render.
      let data;
      try {
        data = await tryOnce(force ? "/api/ng/refresh" : "/api/ng/dashboard", force ? 45000 : 25000);
      } catch (err1) {
        console.warn("ng first fetch failed, retry dashboard", err1);
        data = await tryOnce("/api/ng/dashboard", 25000);
      }
      renderNgDashboard(data);
    } catch (err) {
      console.error("loadNgDashboard error:", err);
      const sum = $("#ng-advice-summary");
      if (sum) sum.textContent = `加载失败：${err.message || err}（可强刷页面或稍后再试）`;
      const elScore = $("#ng-composite-score");
      if (elScore && elScore.textContent === "0") elScore.textContent = "—";
    } finally {
      state.ngLoading = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = "全网抓取 & 重算";
      }
    }
  }

  function renderNgDashboard(data) {
    if (!data) return;
    state.ngPayload = data;
    renderNgBanner(data);
    renderNgMeter(data);
    renderNgAdvice(data.advice, data.price);
    renderNgPutLinks();
    renderNgEvents(data.events);
    renderNgCalculator(data);
    renderNgFactors(data.factors);
    renderNgNews(data.news);
    renderNgCharts(data);
  }

  function renderNgBanner(data) {
    const pill = $("#ng-season-pill");
    if (pill) {
      pill.textContent = data.season_cn || (data.season === "winter" ? "采暖季 (11–3月)" : "天然气季节");
    }

    const p = data.price || {};
    const elPrice = $("#ng-kpi-price");
    const elChg1d = $("#ng-kpi-chg1d");
    const elChg5d = $("#ng-kpi-chg5d");
    const elChg21d = $("#ng-kpi-chg21d");
    const elPct52 = $("#ng-kpi-pct52");
    const elRange52 = $("#ng-kpi-range52");
    const elAtr = $("#ng-kpi-atr");
    const elVol = $("#ng-kpi-vol");

    if (elPrice) elPrice.textContent = p.price != null ? `$${fmt(p.price, 3)}` : "—";
    if (elChg1d) {
      const v = p.chg_1d;
      if (v != null) {
        const cls = v > 0 ? "up" : v < 0 ? "down" : "flat";
        elChg1d.innerHTML = `<span class="${cls}">${v > 0 ? "+" : ""}${fmt(v, 2)}%</span> (日)`;
      } else {
        elChg1d.textContent = "—";
      }
    }
    if (elChg5d) {
      const v = p.chg_5d;
      const cls = v > 0 ? "up" : v < 0 ? "down" : "flat";
      elChg5d.innerHTML = v != null ? `1周: <span class="${cls}">${v > 0 ? "+" : ""}${fmt(v, 2)}%</span>` : "—";
    }
    if (elChg21d) {
      const v = p.chg_21d;
      const cls = v > 0 ? "up" : v < 0 ? "down" : "flat";
      elChg21d.innerHTML = v != null ? `1月: <span class="${cls}">${v > 0 ? "+" : ""}${fmt(v, 2)}%</span>` : "—";
    }
    if (elPct52) elPct52.textContent = p.pct52 != null ? `${fmt(p.pct52, 1)}%` : "—";
    if (elRange52) {
      elRange52.textContent = (p.lo52 != null && p.hi52 != null)
        ? `$${fmt(p.lo52, 2)} ~ $${fmt(p.hi52, 2)}`
        : "—";
    }
    if (elAtr) elAtr.textContent = p.atr14 != null ? `$${fmt(p.atr14, 3)}` : "—";
    if (elVol) elVol.textContent = p.vol20 != null ? `${fmt(p.vol20, 1)}%` : "—";
  }

  function renderNgMeter(data) {
    const score = data.composite_100 != null ? data.composite_100 : (data.composite ? data.composite * 100 : 0);
    const conf = data.confidence != null ? data.confidence : 0;
    const bias = (data.advice && data.advice.bias_cn) || "中性震荡";

    const elConf = $("#ng-conf-badge");
    const elScore = $("#ng-composite-score");
    const elBias = $("#ng-bias-tag");
    const elBar = $("#ng-meter-bar");

    if (elConf) elConf.textContent = `置信度 ${fmt(conf, 0)}%`;
    if (elScore) {
      elScore.textContent = `${score > 0 ? "+" : ""}${fmt(score, 1)}`;
      elScore.className = `meter-score-large mono ${score > 5 ? "up" : score < -5 ? "down" : "flat"}`;
    }
    if (elBias) {
      elBias.textContent = bias;
      elBias.className = `meter-bias-tag ${score > 5 ? "bull" : score < -5 ? "bear" : "neutral"}`;
    }
    if (elBar) {
      const capped = Math.max(-100, Math.min(100, score));
      if (capped >= 0) {
        const w = (capped / 100) * 50;
        elBar.style.left = "50%";
        elBar.style.width = `${w}%`;
        elBar.style.backgroundColor = "#ef5350";
      } else {
        const w = (Math.abs(capped) / 100) * 50;
        elBar.style.left = `${50 - w}%`;
        elBar.style.width = `${w}%`;
        elBar.style.backgroundColor = "#26a69a";
      }
    }
  }

  function renderNgAdvice(advice, price) {
    if (!advice) return;
    const elHorizon = $("#ng-advice-horizon");
    const elAction = $("#ng-action-pill");
    const elSummary = $("#ng-advice-summary");
    const elEntry = $("#ng-level-entry");
    const elStop = $("#ng-level-stop");
    const elTarget = $("#ng-level-target");
    const elRr = $("#ng-level-rr");
    const elInval = $("#ng-inval-list");

    if (elHorizon) elHorizon.textContent = advice.horizon || "1–3周波段 · 动态ATR风险预算";
    if (elAction) {
      elAction.textContent = advice.bias_cn || "观望";
      const b = advice.bias || "";
      const cls = b.includes("long") ? "high" : b.includes("short") ? "low" : "mid";
      elAction.className = `tag-pill ${cls}`;
    }
    if (elSummary) {
      elSummary.textContent = advice.summary || "—";
      renderNgOptionJudgment(elSummary, state.ngPayload);
    }
    if (elEntry) {
      elEntry.textContent = (advice.entry && advice.entry.length === 2)
        ? `$${fmt(advice.entry[0], 3)} ~ $${fmt(advice.entry[1], 3)}`
        : "观望区间无开仓建议";
    }
    if (elStop) {
      elStop.textContent = advice.stop != null ? `$${fmt(advice.stop, 3)}` : "—";
    }
    if (elTarget) {
      elTarget.textContent = (advice.targets && advice.targets.length)
        ? advice.targets.map((t, i) => `T${i + 1}: $${fmt(t, 3)}`).join(" / ")
        : "—";
    }
    if (elRr) {
      const rrTxt = advice.rr != null ? `1 : ${fmt(advice.rr, 1)}` : "—";
      const szTxt = advice.size_mult != null ? `${(advice.size_mult * 100).toFixed(0)}%` : "—";
      elRr.textContent = `${rrTxt} | 仓位: ${szTxt}`;
    }
    if (elInval) {
      const items = advice.invalidation || [];
      if (items.length) {
        elInval.innerHTML = items.map((inv) => `<li>${inv}</li>`).join("");
      } else {
        elInval.innerHTML = `<li>依日线收盘均线与 ATR 防守。</li>`;
      }
    }
  }

  // 卖Put/Call 研究性判断（纯前端派生，兼容旧快照；全部 textContent，无 HTML 注入）
  function renderNgOptionJudgment(container, data) {
    if (!container || typeof window.ngOptionJudgment !== "function" || !data) return;
    const j = window.ngOptionJudgment(data);
    const box = document.createElement("div");
    box.className = "ng-option-judgment";
    box.style.marginTop = "8px";
    const head = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = `期权方向：${j.label}${j.reference_only ? "（仅参考）" : ""}`;
    head.appendChild(strong);
    box.appendChild(head);
    [`Put：${j.put.text}`, `Call：${j.call.text}`].concat(j.notes).forEach((t, i) => {
      const line = document.createElement("div");
      line.className = i < 2 ? "" : "sub";
      line.textContent = (i < 2 ? "" : "· ") + t;
      box.appendChild(line);
    });
    container.appendChild(box);
  }

  function renderNgEvents(events) {
    const list = $("#ng-events-list");
    if (!list) return;
    if (!events || !events.length) {
      list.innerHTML = `<div class="empty">暂无近期重大事件</div>`;
      return;
    }
    list.innerHTML = events
      .map((ev) => {
        const isUrgent = ev.in_hours <= 24;
        const hoursTxt = ev.in_hours < 24
          ? `${fmt(ev.in_hours, 1)}小时后`
          : `${fmt(ev.in_hours / 24, 1)}天后`;
        return `
          <div class="event-item">
            <div>
              <div class="event-name">${ev.name}</div>
              <div class="event-note">${ev.time_bj || ev.time_et} · ${ev.note}</div>
            </div>
            <span class="event-badge ${isUrgent ? "urgent" : "normal"}">${hoursTxt}</span>
          </div>
        `;
      })
      .join("");
  }

  function renderNgCalculator(data) {
    const p = (data && data.price && data.price.price) || 2.8;
    const atr = (data && data.price && data.price.atr14) || 0.14;
    const multMap = { NG: 10000, QG: 2500, MNG: 1000 };
    const contract = state.ngCalcContract || "MNG";
    const mult = multMap[contract] || 1000;
    const lots = state.ngCalcLots || 1;
    const notional = p * mult * lots;

    const elNotional = $("#ng-calc-notional");
    if (elNotional) elNotional.textContent = `名义价值: $${fmt(notional, 0)}`;

    const tickVal = 0.001 * mult * lots;
    const atrVal = atr * mult * lots;
    let stopVal = atrVal * 2;
    if (data && data.advice && data.advice.stop != null) {
      stopVal = Math.abs(p - data.advice.stop) * mult * lots;
    }

    const elMetrics = $("#ng-calc-metrics");
    if (elMetrics) {
      elMetrics.innerHTML = `
        <div class="calc-m-item">每 0.001 波动<b>$${fmt(tickVal, 2)}</b></div>
        <div class="calc-m-item">单日 ATR 波动<b>±$${fmt(atrVal, 1)}</b></div>
        <div class="calc-m-item">建议止损风控额<b>-$${fmt(stopVal, 1)}</b></div>
      `;
    }
  }

  function renderNgFactors(factors) {
    const body = $("#ng-factors-body");
    if (!body) return;
    if (!factors || !factors.length) {
      body.innerHTML = `<tr><td colspan="4" class="empty">暂无因子数据</td></tr>`;
      return;
    }
    body.innerHTML = factors
      .map((f) => {
        const score = f.score != null ? f.score * 100 : null;
        const weight = f.weight != null ? (f.weight * 100).toFixed(0) + "%" : "0%";
        const contrib = f.contribution != null ? (f.contribution * 100).toFixed(1) : null;
        const scoreCls = score != null ? (score > 10 ? "up" : score < -10 ? "down" : "flat") : "muted";
        const contribCls = contrib != null ? (contrib > 1 ? "up" : contrib < -1 ? "down" : "flat") : "muted";

        return `
          <tr>
            <td class="factor-name-cell">${f.name}</td>
            <td class="num mono">${weight}</td>
            <td class="num mono ${scoreCls}">${score != null ? (score > 0 ? "+" : "") + fmt(score, 1) : "—"}</td>
            <td class="num mono ${contribCls}"><strong>${contrib != null ? (contrib > 0 ? "+" : "") + fmt(contrib, 1) : "—"}</strong></td>
            <td class="factor-read-cell">${f.reading || "—"}</td>
            <td class="factor-detail-cell">${[f.why, f.detail].filter(Boolean).join("<br/><br/>") || "—"}</td>
          </tr>
        `;
      })
      .join("");
  }

  function renderNgNews(news) {
    const list = $("#ng-news-list");
    if (!list) return;
    const items = (news && news.items) || [];
    const cat = state.ngNewsCat || "all";
    const filtered = cat === "all" ? items : items.filter((n) => n.cat_key === cat || n.category === cat);

    if (!filtered.length) {
      list.innerHTML = `<div class="empty">暂无该分类的情报</div>`;
      return;
    }

    list.innerHTML = filtered
      .map((n) => {
        const dirCls = n.direction > 0 ? "bull" : n.direction < 0 ? "bear" : "neutral";
        const dirText = n.direction > 0 ? "利多 Bullish" : n.direction < 0 ? "利空 Bearish" : "中性 Neutral";
        const weightPct = n.weight != null ? Math.round(n.weight * 100) : 50;
        const catName = n.category || n.cat_key || "情报";

        return `
          <div class="news-item-card">
            <div class="news-item-top">
              <div class="news-tags">
                <span class="news-cat-pill">${catName}</span>
                <span class="news-impact-pill ${dirCls}">${dirText}</span>
                <span class="tag-pill">影响权重 ${weightPct}%</span>
              </div>
              <span class="news-time mono">${n.time || (n.age_h != null ? fmt(n.age_h, 1) + "小时前" : "")}</span>
            </div>
            <a class="news-title-link" href="${n.link}" target="_blank" rel="noopener">${n.title}</a>
            <div class="news-item-source">来源: ${n.source || "网络检索"}</div>
            <div class="news-explain-box">
              <b>传导机制与影响分析：</b>${n.explain || n.reason || "综合供需与宏观传导"}
            </div>
          </div>
        `;
      })
      .join("");
  }

  function renderNgCharts(data) {
    if (!data) return;
    renderNgCurveChart(data.curve);
    renderNgStorageChart(data.storage);
    renderNgWeatherChart(data.weather);
    renderNgLngChart(data.lng);
    const ms = (data.monthly_supply && data.monthly_supply.series) || {};
    renderNgMonthlyChart("chart-ng-dryprod", "ng-dryprod-hint", ms.dry_production, "#4fc3f7");
    renderNgMonthlyChart("chart-ng-lngexp", "ng-lngexp-hint", ms.lng_exports, "#f6c945");
  }

  const NG_MONTHLY_STATUS = {
    ok: "正常",
    seed: "种子数据（官方XLS构造）",
    stale: "最近抓取失败，显示上次成功数据",
    unavailable: "暂不可用",
  };

  function renderNgMonthlyChart(chartId, hintId, block, color) {
    const chart = getNgChart(chartId);
    const hint = $(`#${hintId}`);
    const pts = (block && block.points) || [];
    const status = (block && block.status) || "unavailable";
    if (hint) {
      if (!pts.length) {
        hint.textContent = `数据${NG_MONTHLY_STATUS.unavailable}`;
      } else {
        const last = block.latest || pts[pts.length - 1];
        const fetched = block.fetched_at ? ` · 抓取 ${String(block.fetched_at).replace("T", " ").replace("+00:00", " UTC")}` : "";
        hint.textContent = `截至 ${block.asof} · ${fmt(last.bcfd, 2)} Bcf/d（${fmt(last.mmcf, 0)} MMcf/月）· ${NG_MONTHLY_STATUS[status] || status}${block.seed && status === "stale" ? "（种子）" : ""}${block.cache_expired && status !== "stale" ? " · 缓存已超24小时" : ""}${fetched} · 约2个月滞后`;
        hint.title = `${block.series} ${block.scope || ""}\n${block.page_url || block.source_url || ""}`;
      }
    }
    if (!chart) return;
    if (!pts.length) {
      chart.clear();
      return;
    }
    chart.setOption(
      {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "axis",
          backgroundColor: "#16202c",
          borderColor: "#2a3a4f",
          textStyle: { color: "#e5efff", fontSize: 11 },
          formatter: (params) => {
            const p = pts[params[0].dataIndex];
            return `<b>${p.month}</b>（${p.days} 天）<br/>${fmt(p.bcfd, 2)} Bcf/d<br/><span style="color:#8b9bb0">${fmt(p.mmcf, 0)} MMcf/月 · EIA ${block.series}</span>`;
          },
        },
        grid: { left: 45, right: 15, top: 20, bottom: 25 },
        xAxis: {
          type: "category",
          data: pts.map((p) => p.month.slice(2)),
          axisLabel: { color: "#8b9bb0", fontSize: 10 },
          axisLine: { lineStyle: { color: "#243041" } },
        },
        yAxis: {
          type: "value",
          scale: true,
          axisLabel: { color: "#8b9bb0", fontSize: 10, formatter: (v) => v.toFixed(1) },
          splitLine: { lineStyle: { color: "#192230" } },
        },
        series: [
          {
            name: "Bcf/d",
            type: "line",
            data: pts.map((p) => p.bcfd),
            smooth: 0.2,
            symbolSize: 3,
            itemStyle: { color },
            lineStyle: { width: 2, color },
          },
        ],
      },
      true
    );
  }

  function renderNgCurveChart(curve) {
    const chart = getNgChart("chart-ng-curve");
    if (!chart) return;
    const pts = (curve && curve.points) || [];
    if (!pts.length) {
      chart.clear();
      return;
    }
    const cats = pts.map((p) => p.label || `${p.year % 100}${String(p.month).padStart(2, "0")}`);
    const vals = pts.map((p) => p.price);
    const hint = $("#ng-curve-hint");
    if (hint && curve.m1_m2_pct != null) {
      const isContango = curve.m1_m2_pct > 0;
      hint.textContent = `${isContango ? "Contango (次月升水 " : "Backwardation (次月贴水 "}${fmt(Math.abs(curve.m1_m2_pct), 1)}%)`;
      hint.className = `hint ${isContango ? "low" : "high"}`;
    }

    const opt = {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "#16202c",
        borderColor: "#2a3a4f",
        textStyle: { color: "#e5efff", fontSize: 11 },
        formatter: (params) => {
          const pt = pts[params[0].dataIndex];
          if (!pt) return "";
          return `<b>${pt.ticker}</b> (${pt.label})<br/>价格: <b>$${fmt(pt.price, 3)}</b>/MMBtu<br/>到期: ${pt.expiry || "—"}`;
        },
      },
      grid: { left: 45, right: 15, top: 20, bottom: 25 },
      xAxis: {
        type: "category",
        data: cats,
        axisLabel: { color: "#8b9bb0", fontSize: 10 },
        axisLine: { lineStyle: { color: "#243041" } },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { color: "#8b9bb0", fontSize: 10, formatter: (v) => `$${v.toFixed(2)}` },
        splitLine: { lineStyle: { color: "#192230" } },
      },
      series: [
        {
          name: "远期曲线",
          type: "line",
          data: vals,
          smooth: 0.25,
          symbolSize: 6,
          itemStyle: { color: "#3d9cf0" },
          lineStyle: { width: 2, color: "#3d9cf0" },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(61,156,240,0.3)" },
              { offset: 1, color: "rgba(61,156,240,0.0)" },
            ]),
          },
        },
      ],
    };
    chart.setOption(opt, true);
  }

  function renderNgStorageChart(storage) {
    const chart = getNgChart("chart-ng-storage");
    if (!chart) return;
    const s = storage && storage.seasonal;
    if (!s || !s.weeks) {
      chart.clear();
      return;
    }
    const weeks = s.weeks;
    const min5 = s.min5 || [];
    const max5 = s.max5 || [];
    const avg5 = s.avg5 || [];
    const diff = max5.map((v, i) => (v != null && min5[i] != null ? v - min5[i] : 0));
    const thisYear = weeks.map((w) => (s.this_year && s.this_year[w] != null ? s.this_year[w] : null));
    const lastYear = weeks.map((w) => (s.last_year && s.last_year[w] != null ? s.last_year[w] : null));

    const opt = {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "#16202c",
        borderColor: "#2a3a4f",
        textStyle: { color: "#e5efff", fontSize: 11 },
        formatter: (params) => {
          const w = params[0].name;
          const ty = thisYear[w - 1];
          const ly = lastYear[w - 1];
          const a5 = avg5[w - 1];
          let h = `<b>第 ${w} 周 EIA 库存储备 (Bcf)</b><br/>`;
          if (ty != null) h += `当年(${s.year}): <b style="color:#ef5350">${ty} Bcf</b><br/>`;
          if (ly != null) h += `去年(${s.year - 1}): <b style="color:#26a69a">${ly} Bcf</b><br/>`;
          if (a5 != null) h += `5年均值: ${a5} Bcf<br/>`;
          if (min5[w - 1] != null && max5[w - 1] != null) h += `5年范围: ${min5[w - 1]} ~ ${max5[w - 1]} Bcf`;
          return h;
        },
      },
      legend: {
        data: [`${s.year}年`, `${s.year - 1}年`, "5年均值", "5年区间"],
        textStyle: { color: "#8b9bb0", fontSize: 10 },
        top: 0,
        right: 10,
      },
      grid: { left: 45, right: 15, top: 28, bottom: 25 },
      xAxis: {
        type: "category",
        data: weeks,
        axisLabel: {
          color: "#8b9bb0",
          fontSize: 10,
          formatter: (v) => (v % 8 === 1 ? `W${v}` : ""),
        },
        axisLine: { lineStyle: { color: "#243041" } },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { color: "#8b9bb0", fontSize: 10 },
        splitLine: { lineStyle: { color: "#192230" } },
      },
      series: [
        {
          name: "区间底",
          type: "line",
          data: min5,
          stack: "band",
          lineStyle: { opacity: 0 },
          showSymbol: false,
        },
        {
          name: "5年区间",
          type: "line",
          data: diff,
          stack: "band",
          lineStyle: { opacity: 0 },
          areaStyle: { color: "rgba(61, 156, 240, 0.12)" },
          showSymbol: false,
        },
        {
          name: "5年均值",
          type: "line",
          data: avg5,
          lineStyle: { type: "dashed", width: 1.5, color: "#8b9bb0" },
          showSymbol: false,
        },
        {
          name: `${s.year - 1}年`,
          type: "line",
          data: lastYear,
          lineStyle: { width: 1.2, color: "rgba(38, 166, 154, 0.6)" },
          showSymbol: false,
        },
        {
          name: `${s.year}年`,
          type: "line",
          data: thisYear,
          lineStyle: { width: 2.2, color: "#ef5350" },
          symbolSize: 4,
          itemStyle: { color: "#ef5350" },
        },
      ],
    };
    chart.setOption(opt, true);
  }

  function renderNgWeatherChart(weather) {
    const chart = getNgChart("chart-ng-weather");
    if (!chart) return;
    const daily = (weather && weather.daily) || [];
    if (!daily.length) {
      chart.clear();
      return;
    }
    const dates = daily.map((d) => d.date.slice(5));
    const tdd = daily.map((d) => d.tdd);
    const normal = daily.map((d) => d.normal_tdd);
    const diff = daily.map((d) => (d.tdd != null && d.normal_tdd != null ? Number((d.tdd - d.normal_tdd).toFixed(1)) : 0));

    const opt = {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "#16202c",
        borderColor: "#2a3a4f",
        textStyle: { color: "#e5efff", fontSize: 11 },
        formatter: (params) => {
          const idx = params[0].dataIndex;
          const d = daily[idx];
          return `<b>${d.date} 需求度日预报</b><br/>预报总度日 (TDD): <b>${d.tdd}</b> (HDD ${d.hdd} / CDD ${d.cdd})<br/>近5年常态均值: ${d.normal_tdd || "—"}<br/>偏差: <b style="color:${diff[idx] > 0 ? "#ef5350" : "#26a69a"}">${diff[idx] > 0 ? "+" : ""}${diff[idx]} 度日</b>`;
        },
      },
      legend: {
        data: ["预报度日(TDD)", "常态度日", "度日偏差"],
        textStyle: { color: "#8b9bb0", fontSize: 10 },
        top: 0,
        right: 10,
      },
      grid: { left: 35, right: 35, top: 28, bottom: 25 },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: { color: "#8b9bb0", fontSize: 10 },
        axisLine: { lineStyle: { color: "#243041" } },
      },
      yAxis: [
        {
          type: "value",
          scale: true,
          axisLabel: { color: "#8b9bb0", fontSize: 10 },
          splitLine: { lineStyle: { color: "#192230" } },
        },
        {
          type: "value",
          scale: true,
          axisLabel: { color: "#8b9bb0", fontSize: 10 },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "预报度日(TDD)",
          type: "bar",
          data: tdd,
          itemStyle: { color: "rgba(61, 156, 240, 0.75)" },
        },
        {
          name: "常态度日",
          type: "line",
          data: normal,
          lineStyle: { width: 1.5, type: "dashed", color: "#f6c945" },
          itemStyle: { color: "#f6c945" },
          symbolSize: 4,
        },
        {
          name: "度日偏差",
          type: "bar",
          yAxisIndex: 1,
          data: diff.map((v) => ({
            value: v,
            itemStyle: { color: v >= 0 ? "rgba(239, 83, 80, 0.65)" : "rgba(38, 166, 154, 0.65)" },
          })),
        },
      ],
    };
    chart.setOption(opt, true);
  }

  function renderNgLngChart(lng) {
    const chart = getNgChart("chart-ng-lng");
    if (!chart) return;
    const hist = (lng && lng.history) || [];
    if (!hist.length) {
      chart.clear();
      return;
    }
    const dates = hist.map((h) => h.date.slice(5));
    const spreads = hist.map((h) => h.spread);

    const hint = $("#ng-lng-hint");
    if (hint && lng.spread != null) {
      hint.textContent = `当前价差 $${fmt(lng.spread, 2)}/MMBtu (完全成本线 ~$4.00)`;
    }

    const opt = {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "#16202c",
        borderColor: "#2a3a4f",
        textStyle: { color: "#e5efff", fontSize: 11 },
        formatter: (params) => {
          const idx = params[0].dataIndex;
          const h = hist[idx];
          return `<b>${h.date}</b><br/>跨大西洋价格套利代理 (TTF − HH):<br/><b>$${fmt(h.spread, 3)}</b> / MMBtu<br/><span style="color:#8b9bb0">出口成本盈亏平衡线: ~$4.00/MMBtu</span>`;
        },
      },
      grid: { left: 45, right: 15, top: 20, bottom: 25 },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: { color: "#8b9bb0", fontSize: 10 },
        axisLine: { lineStyle: { color: "#243041" } },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { color: "#8b9bb0", fontSize: 10, formatter: (v) => `$${v.toFixed(1)}` },
        splitLine: { lineStyle: { color: "#192230" } },
      },
      series: [
        {
          name: "TTF - HH 套利价差",
          type: "line",
          data: spreads,
          smooth: 0.2,
          symbolSize: 4,
          itemStyle: { color: "#f6c945" },
          lineStyle: { width: 2, color: "#f6c945" },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(246, 201, 69, 0.25)" },
              { offset: 1, color: "rgba(246, 201, 69, 0.0)" },
            ]),
          },
          markLine: {
            symbol: "none",
            data: [
              {
                yAxis: 4.0,
                lineStyle: { color: "#ef5350", type: "dashed", width: 1.5 },
                label: {
                  formatter: "出口盈亏平衡线 ($4.00)",
                  position: "insideEndTop",
                  color: "#ef5350",
                  fontSize: 10,
                },
              },
            ],
          },
        },
      ],
    };
    chart.setOption(opt, true);
  }


  const PUT_WATCH_KEY = "futures_put_watch_v1";

  function loadPutWatch() {
    try {
      return JSON.parse(localStorage.getItem(PUT_WATCH_KEY) || "[]");
    } catch (e) {
      return [];
    }
  }

  function savePutWatch(list) {
    localStorage.setItem(PUT_WATCH_KEY, JSON.stringify(list || []));
  }

  function putFilterDefaults() {
    return {
      kind: "all",
      hv: 50,
      rsiLo: 25,
      rsiHi: 65,
      oi: 50,
      yieldMin: 0,
      otmMax: 15,
      coreOnly: false,
      noEarn7: true,
      ivRich: false,
      themeSymbols: null,
    };
  }

  function readPutFiltersFromDom() {
    const f = state.putFilters || putFilterDefaults();
    f.kind = state.putKind || f.kind || "all";
    const num = (id, d) => {
      const el = $(id);
      if (!el || el.value === "") return d;
      const v = Number(el.value);
      return Number.isFinite(v) ? v : d;
    };
    f.hv = num("#put-f-hv", 50);
    f.rsiLo = num("#put-f-rsi-lo", 25);
    f.rsiHi = num("#put-f-rsi-hi", 65);
    f.oi = num("#put-f-oi", 50);
    f.yieldMin = num("#put-f-yield", 0);
    f.otmMax = num("#put-f-otm", 15);
    f.coreOnly = !!( $("#put-f-core") && $("#put-f-core").checked );
    f.noEarn7 = !!( $("#put-f-noearn") && $("#put-f-noearn").checked );
    f.ivRich = !!( $("#put-f-ivrich") && $("#put-f-ivrich").checked );
    state.putFilters = f;
    return f;
  }


  function themeRowsFromPayload(data, symbols) {
    const syms = symbols || [];
    if (!syms.length) return [];
    const want = new Set(syms);
    const bySym = new Map();
    const themes = (data && data.themes) || [];
    for (const th of themes) {
      for (const r of th.items || []) {
        if (want.has(r.symbol)) bySym.set(r.symbol, r);
      }
    }
    for (const r of [...((data && data.picks) || []), ...((data && data.all) || []), ...((data && data.watchlist) || [])]) {
      if (want.has(r.symbol)) bySym.set(r.symbol, { ...bySym.get(r.symbol), ...r });
    }
    // Keep theme symbol order
    return syms.map((s) => bySym.get(s)).filter(Boolean);
  }

  function applyPutFilters(rows) {
    const f = readPutFiltersFromDom();
    const core = new Set((state.putPayload && state.putPayload.core_symbols) || []);
    const view = window.cnOptionCandidatesView;
    let list = rows || [];
    // US tables never render CN; cn_fut lives only in the dedicated CNY panel.
    if (view && view.filterByKind) list = view.filterByKind(list, f.kind || "all");
    else {
      list = list.filter((r) => {
        if ((r.kind === "cn_fut" || r.market === "cn")) return (f.kind || "all") === "cn_fut";
        if ((f.kind || "all") === "cn_fut") return false;
        if (f.kind !== "all" && (r.kind || "stock") !== f.kind) return false;
        return true;
      });
    }
    return list.filter((r) => {
      if (r.kind === "cn_fut" || r.market === "cn") return false; // belt-and-suspenders: US grid excludes CN
      if (f.themeSymbols && f.themeSymbols.length && !f.themeSymbols.includes(r.symbol)) return false;
      if (f.coreOnly && !core.has(r.symbol)) return false;
      if ((r.hv_pctile == null ? 0 : r.hv_pctile) < f.hv) return false;
      if (r.rsi14 != null && (r.rsi14 < f.rsiLo || r.rsi14 > f.rsiHi)) return false;
      const put = r.put || {};
      if ((put.open_interest || 0) < f.oi && put.strike != null) return false;
      if ((put.yield_ann_pct || 0) < f.yieldMin) return false;
      if (put.otm_pct != null && Math.abs(put.otm_pct) > f.otmMax) return false;
      const earnDays = r.earnings && r.earnings.days;
      if (f.noEarn7 && earnDays != null && earnDays <= 7) return false;
      const ivTag = (r.risk && r.risk.iv_tag) || "";
      if (f.ivRich && ivTag !== "IV偏贵") return false;
      return true;
    });
  }



  function defaultEdgeWeights() {
    return { vrp: 30, iv_pctile: 25, term: 20, rv_stable: 15, skew: 10 };
  }

  function readEdgeWeights() {
    const box = $("#put-weights");
    const w = defaultEdgeWeights();
    if (!box) return w;
    box.querySelectorAll("input[data-w]").forEach((inp) => {
      w[inp.dataset.w] = Number(inp.value) || 0;
      const sp = box.querySelector(`[data-wv="${inp.dataset.w}"]`);
      if (sp) sp.textContent = String(inp.value);
    });
    state.putWeights = w;
    return w;
  }

  function recomputeEdgeClient(row, weights) {
    const m = (row.edge && row.edge.metrics) || {};
    const iv = m.iv, hv20 = m.hv20, hv60 = m.hv60;
    const ivp = m.iv_pctile;
    const near = m.near_iv != null ? m.near_iv : iv;
    const far = m.far_iv;
    const atm = m.atm_iv != null ? m.atm_iv : iv;
    const otmIv = m.otm_put_iv != null ? m.otm_put_iv : iv;
    const wraw = weights || readEdgeWeights();
    const sum = Object.values(wraw).reduce((a, b) => a + Math.max(0, b), 0) || 100;
    const wn = {};
    Object.keys(wraw).forEach((k) => (wn[k] = (Math.max(0, wraw[k]) / sum) * 100));

    // VRP 0-30
    let vrpPts = 0, vrpNote = "VRP未知", vrpSpread = null;
    if (iv != null && hv20 != null && hv20 > 0) {
      vrpSpread = iv - hv20;
      const ratio = iv / hv20;
      if (vrpSpread <= 0 || ratio < 1) { vrpPts = Math.max(0, 8 + vrpSpread); vrpNote = "负VRP"; }
      else if (ratio < 1.15) { vrpPts = Math.min(30, 10 + Math.min(8, vrpSpread * 0.7)); vrpNote = "VRP偏薄"; }
      else { vrpPts = Math.min(30, 18 + Math.min(12, vrpSpread * 0.85 + (ratio - 1.15) * 25)); vrpNote = "VRP偏厚"; }
    }
    // IV pctile 0-25
    let ivpPts = 0, ivpNote = "IV分位未知";
    if (ivp != null) {
      if (ivp >= 80) { ivpPts = 25; ivpNote = "IV分位很高"; }
      else if (ivp >= 60) { ivpPts = 18 + (ivp - 60) * 0.35; ivpNote = "IV分位偏高"; }
      else if (ivp >= 40) { ivpPts = 10 + (ivp - 40) * 0.3; ivpNote = "IV分位中性"; }
      else { ivpPts = Math.max(0, ivp * 0.2); ivpNote = "IV分位偏低"; }
    }
    // term 0-20
    let termPts = 8, termNote = "期限结构未知", termReg = "unknown";
    if (near != null && far != null && near > 0) {
      const slope = ((far - near) / near) * 100;
      if (slope >= 5) { termPts = 20; termNote = "期限结构contango"; termReg = "contango"; }
      else if (slope >= 0) { termPts = 14; termNote = "期限结构温和contango"; termReg = "contango"; }
      else if (slope > -8) { termPts = 6; termNote = "期限结构偏平/轻倒挂"; termReg = "flat"; }
      else { termPts = 0; termNote = "期限结构倒挂(危险)"; termReg = "backwardation"; }
    }
    // rv 0-15
    let rvPts = 7, rvNote = "RV稳定性未知", accel = null;
    if (hv20 != null && hv60 != null && hv60 > 0) {
      accel = hv20 / hv60;
      if (accel <= 0.9) { rvPts = 15; rvNote = "RV在回落"; }
      else if (accel <= 1.05) { rvPts = 11; rvNote = "RV平稳"; }
      else if (accel <= 1.25) { rvPts = 5; rvNote = "RV温和加速"; }
      else if (accel <= 1.35) { rvPts = 2; rvNote = "RV偏快加速"; }
      else { rvPts = 0; rvNote = "RV加速中(风暴)"; }
    }
    // skew 0-10
    let skPts = 4, skNote = "Skew未知", skew = null;
    if (atm != null && otmIv != null && atm > 0) {
      skew = otmIv - atm;
      if (skew >= 4) { skPts = 10; skNote = "Put偏斜丰厚"; }
      else if (skew >= 1) { skPts = 7; skNote = "Put偏斜正常"; }
      else if (skew >= -1) { skPts = 4; skNote = "Skew平坦"; }
      else { skPts = 1; skNote = "Skew反向"; }
    }
    const parts = {
      vrp: +((vrpPts / 30) * wn.vrp).toFixed(1),
      iv_pctile: +((ivpPts / 25) * wn.iv_pctile).toFixed(1),
      term: +((termPts / 20) * wn.term).toFixed(1),
      rv_stable: +((rvPts / 15) * wn.rv_stable).toFixed(1),
      skew: +((skPts / 10) * wn.skew).toFixed(1),
    };
    let edge = +(Object.values(parts).reduce((a, b) => a + b, 0).toFixed(1));
    let posture = "观望", posture_en = "WATCH";
    if (termReg === "backwardation" || (accel != null && accel > 1.35)) {
      posture = "回避"; posture_en = "AVOID";
    } else if (vrpSpread != null && vrpSpread < 0) {
      posture = "观望"; posture_en = "WATCH"; edge = Math.min(edge, 54);
    } else if (edge >= 70) { posture = "积极收租"; posture_en = "AGGRESSIVE"; }
    else if (edge >= 55) { posture = "可做"; posture_en = "NORMAL"; }
    else if (edge >= 40) { posture = "仅限定义风险"; posture_en = "DEFINED_RISK"; }

    return {
      edge,
      parts,
      weights: wn,
      notes: { vrp: vrpNote, iv_pctile: ivpNote, term: termNote, rv_stable: rvNote, skew: skNote },
      metrics: { ...(m || {}), vrp_spread: vrpSpread, term_regime: termReg, rv_accel: accel, skew },
      posture,
      posture_en,
    };
  }

  function edgePill(edge) {
    if (!edge) return "";
    const en = edge.posture_en || "";
    const cls = en === "AGGRESSIVE" || en === "NORMAL" ? "agg" : en === "AVOID" ? "avoid" : "warn";
    return `<span class="put-edge-pill ${cls}">Edge ${fmt(edge.edge, 1)} · ${esc(edge.posture || "")}</span>`;
  }

  function checklistMini(ch) {
    if (!ch) return "";
    const cls = ch.severity === "pass" ? "agg" : ch.severity === "block" ? "avoid" : "warn";
    return `<span class="put-edge-pill ${cls}">检查 ${ch.passed}/${ch.total} · ${esc(ch.verdict || "")}</span>`;
  }


  function openPutChain(symbol) {
    const data = state.putPayload;
    if (!data) return;
    const row = [...(data.picks || []), ...(data.all || [])].find((x) => x.symbol === symbol);
    if (!row) return;
    const weights = readEdgeWeights();
    const edge = row.edge && row.edge.metrics ? recomputeEdgeClient(row, weights) : row.edge;
    const put = row.put || {};
    const modal = $("#put-chain-modal");
    const title = $("#put-chain-title");
    const sub = $("#put-chain-sub");
    const edgeEl = $("#put-chain-edge");
    const checkEl = $("#put-chain-check");
    const body = $("#put-chain-body");
    if (title) title.textContent = `${row.symbol} · 期权链下钻`;
    if (sub) sub.textContent = `${row.name || ""} · 到期 ${put.expiry || "—"} · 建议 K=${put.strike != null ? "$"+put.strike : "—"}`;
    if (edgeEl && edge) {
      const p = edge.parts || {};
      edgeEl.innerHTML = `${edgePill(edge)}
        <div style="margin-top:6px;">VRP ${p.vrp} · IV分位 ${p.iv_pctile} · 期限 ${p.term} · RV ${p.rv_stable} · Skew ${p.skew}</div>
        <div class="put-check-mini">${esc((edge.notes && edge.notes.vrp) || "")} · ${esc((edge.notes && edge.notes.term) || "")} · ${esc((edge.notes && edge.notes.rv_stable) || "")}</div>`;
    }
    if (checkEl && row.checklist) {
      const ch = row.checklist;
      checkEl.innerHTML = `<div><strong>${esc(ch.verdict)}</strong> (${ch.passed}/${ch.total})</div>
        <div class="put-check-list">${(ch.checks || []).map((c) => {
          const cls = c.ok ? "ok" : c.hard ? "fail" : "soft";
          return `<div class="put-check-item ${cls}"><strong>${esc(c.title)}</strong><div class="put-check-mini">${esc(c.detail)}</div></div>`;
        }).join("")}</div>`;
    }
    if (body) {
      const rows = put.chain || [];
      if (!rows.length) body.innerHTML = `<tr><td colspan="8" class="empty">暂无链数据</td></tr>`;
      else body.innerHTML = rows.map((c) => `<tr class="${c.selected ? "selected" : ""}">
        <td>$${fmt(c.strike, 2)}</td>
        <td class="num">${fmt(c.otm_pct, 1)}%</td>
        <td class="num">${fmt(c.bid, 2)}</td>
        <td class="num">${fmt(c.ask, 2)}</td>
        <td class="num">${fmt(c.mid, 2)}</td>
        <td class="num">${fmt(c.iv, 1)}%</td>
        <td class="num">${c.open_interest ?? "—"}</td>
        <td class="num">${c.volume ?? "—"}</td>
      </tr>`).join("");
    }
    if (modal) modal.classList.remove("hidden");
  }

  function closePutChain() {
    const modal = $("#put-chain-modal");
    if (modal) modal.classList.add("hidden");
  }

  // Render serves a seed/cache immediately and rebuilds in the background;
  // re-poll the read endpoint so a finished scan replaces the old snapshot.
  const SCREEN_REPOLL_MS = 45000;
  function scheduleScreenRepoll(side, data, isRepoll) {
    const view = window.cnOptionCandidatesView;
    state.screenRepoll = state.screenRepoll || { put: { n: 0, t: null }, call: { n: 0, t: null } };
    const slot = state.screenRepoll[side];
    if (!isRepoll) slot.n = 0;
    if (slot.t) {
      clearTimeout(slot.t);
      slot.t = null;
    }
    if (!view || !view.shouldRepoll || !view.shouldRepoll(data, slot.n)) {
      slot.n = 0;
      return;
    }
    slot.n += 1;
    slot.t = setTimeout(() => {
      slot.t = null;
      if (side === "call") loadShortCall(false, true);
      else loadShortPut(false, true);
    }, SCREEN_REPOLL_MS);
  }

  async function loadShortPut(force = false, repoll = false) {
    if (state.putLoading) return;
    state.putLoading = true;
    const btn = $("#btn-put-refresh");
    if (btn) {
      btn.disabled = true;
      btn.textContent = force ? "扫描期权中…" : "加载中…";
    }
    try {
      const url = force ? "/api/short-put/refresh" : "/api/short-put/high-vol";
      const res = await fetch(apiUrl(url));
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || "short put failed");
      renderShortPut(data);
      scheduleScreenRepoll("put", data, repoll);
    } catch (err) {
      console.error("loadShortPut error:", err);
      const picks = $("#put-picks");
      if (picks) picks.innerHTML = `<div class="empty-card">加载失败：${esc(err.message || String(err))}</div>`;
      const body = $("#put-body");
      if (body) body.innerHTML = `<tr><td colspan="18" class="empty">加载失败</td></tr>`;
    } finally {
      state.putLoading = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = "重新扫描期权";
      }
    }
  }

  function openPutTheme(themeId, symbols) {
    state.putFilters = state.putFilters || putFilterDefaults();
    state.putFilters.themeSymbols = symbols || null;
    switchView("put");
    const bar = $("#put-theme-bar");
    if (bar && themeId) {
      [...bar.querySelectorAll("button[data-theme]")].forEach((b) =>
        b.classList.toggle("active", b.dataset.theme === themeId)
      );
    }
    if (state.putPayload) renderShortPut(state.putPayload);
    else loadShortPut(false);
  }

  function renderShortPut(data) {
    if (!data) return;
    state.putPayload = data;
    if (!state.putFilters) state.putFilters = putFilterDefaults();
    const meta = $("#put-meta");
    if (meta) {
      const n = (data.picks || []).length;
      const u = data.universe_size || 0;
      const acc = data.account_defaults || {};
      meta.textContent = `更新 ${data.updated_at || "—"} · 扫描 ${u} 只 · 推荐 ${n} 只 · 默认账户 $${acc.budget_usd || 50000} / 单标的≤${acc.max_name_pct || 15}%`;
    }
    const pill = $("#put-count-pill");
    const method = $("#put-method");
    if (method && data.method) {
      const m = data.method;
      method.innerHTML = [
        `<strong>目标</strong>：${esc(m.goal || "")}`,
        `<strong>波动</strong>：${esc(m.vol || "")}`,
        `<strong>期权</strong>：${esc(m.option || "")}`,
        `<strong>打分</strong>：${esc(m.score || "")}`,
        m.fundamental ? `<strong>基本面</strong>：${esc(m.fundamental)}` : "",
        `<strong>风险</strong>：${esc(m.risk || "")}`,
        m.cn_futures ? `<strong>国内期货</strong>：${esc(m.cn_futures)}` : "",
        data.disclaimer ? `<em>${esc(data.disclaimer)}</em>` : "",
      ].filter(Boolean).join("<br/>");
    }
    renderPutThemes(data.themes);
    const weights = readEdgeWeights();
    const enrich = (rows) => (rows || []).map((r) => {
      const copy = { ...r };
      if (r.edge && r.edge.metrics) copy.edge = recomputeEdgeClient(r, weights);
      return copy;
    });
    const themeSyms = state.putFilters && state.putFilters.themeSymbols;
    let filteredPicks;
    let filteredAll;
    if (themeSyms && themeSyms.length) {
      // Theme browse: show linked names even if they failed CSP gates / missing from picks.
      const themeRows = enrich(themeRowsFromPayload(data, themeSyms));
      filteredPicks = themeRows;
      filteredAll = themeRows;
      if (pill) pill.textContent = `主题 ${themeRows.length} 只`;
      const note = $("#put-theme-note");
      if (note) {
        note.hidden = false;
        note.textContent = "当前为主题成分明细（含未过推荐门槛的标的，如期权链暂不可用）。";
      }
    } else {
      filteredPicks = applyPutFilters(enrich(data.picks));
      filteredAll = applyPutFilters(enrich(data.all || data.picks));
      if (pill) pill.textContent = `${filteredPicks.length} 推荐`;
      const note = $("#put-theme-note");
      if (note) {
        note.hidden = true;
        note.textContent = "";
      }
    }
    renderPutCards(filteredPicks);
    renderPutTable(filteredAll);
    renderPutWatch();
    renderNgPutLinks();
    if (window.cnOptionCandidatesView) {
      window.cnOptionCandidatesView.renderInto($("#put-cn-picks"), data, "put");
      const panel = $("#put-cn-panel");
      if (panel) {
        const kind = (state.putFilters && state.putFilters.kind) || state.putKind || "all";
        panel.hidden = false;
        panel.style.opacity = kind === "stock" || kind === "etf" ? "0.55" : "1";
      }
    }
  }

  function renderPutThemes(themes) {
    const bar = $("#put-theme-bar");
    if (!bar) return;
    const list = themes || [];
    bar.innerHTML =
      `<span class="put-theme-label">主题联动</span>` +
      `<button type="button" data-theme="all" class="${!(state.putFilters && state.putFilters.themeSymbols) ? "active" : ""}">全部主题</button>` +
      list
        .map((th) => {
          const active = state.putFilters && state.putFilters.themeSymbols && state.putFilters.themeSymbols.join() === (th.symbols || []).join();
          return `<button type="button" data-theme="${esc(th.id)}" class="${active ? "active" : ""}">${esc(th.label)} <small>${(th.symbols || []).length}</small></button>`;
        })
        .join("");
  }

  function riskBadge(risk) {
    if (!risk) return "";
    const lvl = risk.level || "中";
    const cls = lvl === "高" || lvl === "中高" ? "risk-hi" : lvl === "低" ? "risk-lo" : "risk-mid";
    const flags = (risk.flags || []).join(" · ");
    return `<span class="risk-badge ${cls}" title="${esc(flags)}">${esc(risk.iv_tag || "")} · 风险${esc(lvl)}</span>`;
  }

  // 技术分/基本面分/综合分（static/fund_score_view.js；缺失时只显示技术分，不补算）
  function fundHeadline(r) {
    const v = window.fundScoreView;
    return v ? v.headline(r) : { value: r && r.score, label: "技术分" };
  }

  function fundBlock(r) {
    const v = window.fundScoreView;
    return v ? v.blockHtml(r) : "";
  }

  function fundCell(r) {
    const v = window.fundScoreView;
    return v ? v.cellHtml(r) : `<strong>${fmt(r && r.score, 1)}</strong>`;
  }

  function renderPutCards(picks) {
    const el = $("#put-picks");
    if (!el) return;
    if (!picks || !picks.length) {
      el.innerHTML = `<div class="empty-card">${
        state.putFilters && state.putFilters.themeSymbols
          ? "该主题暂无成分明细（数据里没有对应标的）"
          : "当前筛选下暂无推荐（放宽条件或切换主题）"
      }</div>`;
      return;
    }
    el.innerHTML = picks.slice(0, 12).map((r) => {
      const put = r.put || {};
      const risk = r.risk || {};
      const chg = r.chg_5d;
      const chgCls = chg > 0 ? "up" : chg < 0 ? "down" : "flat";
      const chgTxt = chg == null ? "—" : `${chg > 0 ? "+" : ""}${fmt(chg, 1)}%`;
      const earn = r.earnings ? `${r.earnings.days}天` : "—";
      return `<article class="put-card" data-symbol="${esc(r.symbol)}">
        <div class="put-card-top">
          <div>
            <div class="put-sym">${esc(r.symbol)} <span class="tag-pill">${esc(r.kind === "etf" ? "ETF" : "股票")}</span></div>
            <div class="put-name">${esc(r.name || "")} · ${esc(r.sector || "")}</div>
          </div>
          <div class="put-score" title="${esc(fundHeadline(r).label)}">${fmt(fundHeadline(r).value, 1)}</div>
        </div>
        <div class="put-edge-bar">${edgePill(r.edge)} ${checklistMini(r.checklist)} ${riskBadge(risk)}</div>
        ${fundBlock(r)}
        <div class="put-metrics">
          <div class="put-metric"><span class="l">现价</span><span class="v">$${fmt(r.price, 2)}</span></div>
          <div class="put-metric"><span class="l">HV20</span><span class="v">${fmt(r.hv20, 1)}%</span></div>
          <div class="put-metric"><span class="l">RSI</span><span class="v">${fmt(r.rsi14, 1)}</span></div>
          <div class="put-metric"><span class="l">5日</span><span class="v ${chgCls}">${chgTxt}</span></div>
        </div>
        <div class="put-metrics">
          <div class="put-metric"><span class="l">行权价</span><span class="v">${put.strike != null ? "$" + fmt(put.strike, 2) : "—"}</span></div>
          <div class="put-metric"><span class="l">到期</span><span class="v">${esc(put.expiry || "—")} (${put.dte != null ? put.dte + "d" : "—"})</span></div>
          <div class="put-metric"><span class="l">权利金</span><span class="v">${put.mid != null ? "$" + fmt(put.mid, 2) : "—"}</span></div>
          <div class="put-metric"><span class="l">年化</span><span class="v">${put.yield_ann_pct != null ? fmt(put.yield_ann_pct, 1) + "%" : "—"}</span></div>
        </div>
        <div class="put-metrics">
          <div class="put-metric"><span class="l">CSP占用</span><span class="v">${risk.capital_per_contract != null ? "$" + fmt(risk.capital_per_contract, 0) : "—"}</span></div>
          <div class="put-metric"><span class="l">建议张数</span><span class="v">${risk.max_contracts_hint != null ? risk.max_contracts_hint : "—"}</span></div>
          <div class="put-metric"><span class="l">财报</span><span class="v">${esc(earn)}</span></div>
          <div class="put-metric"><span class="l">OI</span><span class="v">${put.open_interest != null ? put.open_interest : "—"}</span></div>
        </div>
        <div class="put-action"><strong>${esc(r.bias_cn || "")}</strong> · ${esc(r.action || "")}</div>
        <div class="put-card-actions">
          <button type="button" class="btn btn-tiny btn-put-watch" data-symbol="${esc(r.symbol)}">加入观察</button>
          <button type="button" class="btn btn-tiny btn-put-chain" data-symbol="${esc(r.symbol)}">期权链/检查</button>
        </div>
      </article>`;
    }).join("");
  }

  function renderPutTable(rows) {
    const body = $("#put-body");
    if (!body) return;
    if (!rows || !rows.length) {
      body.innerHTML = `<tr><td colspan="18" class="empty">暂无数据</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((r) => {
      const put = r.put || {};
      const risk = r.risk || {};
      const putTxt = put.strike != null ? `$${fmt(put.strike, 2)} · ${esc(put.expiry || "")}` : "—";
      const chg = r.chg_5d;
      const chgCls = chg > 0 ? "up" : chg < 0 ? "down" : "flat";
      const chgTxt = chg == null ? "—" : `${chg > 0 ? "+" : ""}${fmt(chg, 1)}%`;
      const earn = r.earnings ? `${r.earnings.days}d` : "—";
      return `<tr>
        <td><strong>${esc(r.symbol)}</strong></td>
        <td>${esc(r.name || "")}</td>
        <td>${esc(r.kind === "etf" ? "ETF" : "股票")}</td>
        <td class="num">$${fmt(r.price, 2)}</td>
        <td class="num">${fmt(r.hv20, 1)}%</td>
        <td class="num">${fmt(r.hv_pctile, 0)}</td>
        <td class="num">${fmt(r.rsi14, 1)}</td>
        <td class="num ${chgCls}">${chgTxt}</td>
        <td class="num">${fundCell(r)}</td>
        <td>${esc((risk.iv_tag || "").replace("IV", ""))}</td>
        <td>${esc(risk.level || "—")}</td>
        <td>${esc(earn)}</td>
        <td>${esc(r.bias_cn || "")}</td>
        <td>${putTxt}</td>
        <td class="num">${put.mid != null ? "$" + fmt(put.mid, 2) : "—"}</td>
        <td class="num">${put.yield_ann_pct != null ? fmt(put.yield_ann_pct, 1) + "%" : "—"}</td>
        <td class="num">${risk.capital_per_contract != null ? "$" + fmt(risk.capital_per_contract, 0) : "—"}</td>
        <td>
          <button type="button" class="btn btn-tiny btn-put-watch" data-symbol="${esc(r.symbol)}">观察</button>
          <button type="button" class="btn btn-tiny btn-put-chain" data-symbol="${esc(r.symbol)}">链</button>
        </td>
      </tr>`;
    }).join("");
  }

  function addPutWatch(symbol) {
    const data = state.putPayload;
    if (!data) return;
    const row = (data.all || []).concat(data.picks || []).find((x) => x.symbol === symbol) ||
      (data.picks || []).find((x) => x.symbol === symbol);
    if (!row || !row.put) {
      alert("该标的暂无期权报价，无法加入");
      return;
    }
    const list = loadPutWatch();
    if (list.some((x) => x.symbol === symbol && x.status === "open")) {
      alert("观察池已有未平仓 " + symbol);
      return;
    }
    const put = row.put;
    list.unshift({
      id: `${symbol}-${Date.now()}`,
      symbol,
      name: row.name,
      status: "open",
      opened_at: new Date().toISOString().slice(0, 10),
      expiry: put.expiry,
      strike: put.strike,
      entry_mid: put.mid,
      exit_mid: null,
      closed_at: null,
      contracts: Math.max(1, riskContracts(row)),
      note: row.bias_cn || "",
    });
    savePutWatch(list);
    renderPutWatch();
  }

  function riskContracts(row) {
    const n = row.risk && row.risk.max_contracts_hint;
    return n != null && n > 0 ? n : 1;
  }

  function closePutWatch(id, exitMid) {
    const list = loadPutWatch();
    const item = list.find((x) => x.id === id);
    if (!item) return;
    const mid = exitMid != null ? Number(exitMid) : prompt(`输入 ${item.symbol} 平仓权利金（买回价）`, "0.05");
    if (mid == null || mid === "") return;
    item.exit_mid = Number(mid);
    item.status = "closed";
    item.closed_at = new Date().toISOString().slice(0, 10);
    savePutWatch(list);
    renderPutWatch();
  }

  function renderPutWatch() {
    const body = $("#put-watch-body");
    const meta = $("#put-watch-meta");
    if (!body) return;
    const list = loadPutWatch();
    const openN = list.filter((x) => x.status === "open").length;
    const closed = list.filter((x) => x.status === "closed");
    let pnl = 0;
    closed.forEach((x) => {
      const c = x.contracts || 1;
      pnl += ((x.entry_mid || 0) - (x.exit_mid || 0)) * 100 * c;
    });
    if (meta) {
      meta.textContent = `持仓 ${openN} · 已平 ${closed.length} · 已实现约 $${fmt(pnl, 0)}（卖Put：开仓权利金 − 平仓买回）`;
    }
    if (!list.length) {
      body.innerHTML = `<tr><td colspan="10" class="empty">暂无记录 · 在推荐卡片点「加入观察」</td></tr>`;
      return;
    }
    body.innerHTML = list.map((x) => {
      const c = x.contracts || 1;
      let mark = x.exit_mid;
      if (mark == null && state.putPayload) {
        const live = (state.putPayload.all || []).find((r) => r.symbol === x.symbol);
        if (live && live.put) mark = live.put.mid;
      }
      const pnlOne = mark != null ? ((x.entry_mid || 0) - mark) * 100 * c : null;
      const pct = mark != null && x.entry_mid ? ((x.entry_mid - mark) / x.entry_mid) * 100 : null;
      const cls = pnlOne == null ? "" : pnlOne >= 0 ? "up" : "down";
      const ops =
        x.status === "open"
          ? `<button type="button" class="btn btn-tiny btn-put-close" data-id="${esc(x.id)}">平仓</button>
             <button type="button" class="btn btn-tiny btn-put-drop" data-id="${esc(x.id)}">删除</button>`
          : `<button type="button" class="btn btn-tiny btn-put-drop" data-id="${esc(x.id)}">删除</button>`;
      return `<tr>
        <td><strong>${esc(x.symbol)}</strong></td>
        <td>${esc(x.status === "open" ? "持仓" : "已平")}</td>
        <td>${esc(x.opened_at || "")}</td>
        <td>$${fmt(x.strike, 2)} · ${esc(x.expiry || "")}</td>
        <td class="num">$${fmt(x.entry_mid, 2)}</td>
        <td class="num">${mark != null ? "$" + fmt(mark, 2) : "—"}</td>
        <td class="num">${c}</td>
        <td class="num ${cls}">${pnlOne != null ? fmt(pnlOne, 0) : "—"}</td>
        <td class="num ${cls}">${pct != null ? fmt(pct, 1) + "%" : "—"}</td>
        <td>${ops}</td>
      </tr>`;
    }).join("");
  }

  function renderNgPutLinks() {
    const el = $("#ng-put-links");
    if (!el) return;
    el.innerHTML = `<button type="button" class="btn btn-tiny" id="btn-ng-to-put">卖Put：天然气相关 UNG</button>
      <span class="sub">从美气情绪跳到高波动 CSP 候选</span>`;
  }


  const CALL_WATCH_KEY = "fd_call_watch_v1";

  function loadCallWatch() {
    try {
      return JSON.parse(localStorage.getItem(CALL_WATCH_KEY) || "[]");
    } catch (e) {
      return [];
    }
  }
  function saveCallWatch(list) {
    localStorage.setItem(CALL_WATCH_KEY, JSON.stringify(list || []));
  }
  function callFilterDefaults() {
    return {
      kind: "all",
      hv: 50,
      rsiLo: 35,
      rsiHi: 80,
      oi: 50,
      yieldMin: 0,
      otmMax: 15,
      coreOnly: false,
      noEarn7: true,
      ivRich: false,
      themeSymbols: null,
    };
  }
  function readCallFiltersFromDom() {
    const f = state.callFilters || callFilterDefaults();
    f.kind = state.callKind || f.kind || "all";
    const num = (id, d) => {
      const el = $(id);
      if (!el || el.value === "") return d;
      const v = Number(el.value);
      return Number.isFinite(v) ? v : d;
    };
    f.hv = num("#call-f-hv", 50);
    f.rsiLo = num("#call-f-rsi-lo", 35);
    f.rsiHi = num("#call-f-rsi-hi", 80);
    f.oi = num("#call-f-oi", 50);
    f.yieldMin = num("#call-f-yield", 0);
    f.otmMax = num("#call-f-otm", 15);
    f.coreOnly = !!( $("#call-f-core") && $("#call-f-core").checked );
    f.noEarn7 = !!( $("#call-f-noearn") && $("#call-f-noearn").checked );
    f.ivRich = !!( $("#call-f-ivrich") && $("#call-f-ivrich").checked );
    state.callFilters = f;
    return f;
  }
  function applyCallFilters(rows) {
    const f = readCallFiltersFromDom();
    const core = new Set((state.callPayload && state.callPayload.core_symbols) || []);
    const view = window.cnOptionCandidatesView;
    let list = rows || [];
    if (view && view.filterByKind) list = view.filterByKind(list, f.kind || "all");
    else {
      list = list.filter((r) => {
        if ((r.kind === "cn_fut" || r.market === "cn")) return (f.kind || "all") === "cn_fut";
        if ((f.kind || "all") === "cn_fut") return false;
        if (f.kind !== "all" && (r.kind || "stock") !== f.kind) return false;
        return true;
      });
    }
    return list.filter((r) => {
      // US Call table must never render CN rows (dedicated CNY panel only).
      if (r.kind === "cn_fut" || r.market === "cn") return false;
      if (f.themeSymbols && f.themeSymbols.length && !f.themeSymbols.includes(r.symbol)) return false;
      if (f.coreOnly && !core.has(r.symbol)) return false;
      if ((r.hv_pctile == null ? 0 : r.hv_pctile) < f.hv) return false;
      if (r.rsi14 != null && (r.rsi14 < f.rsiLo || r.rsi14 > f.rsiHi)) return false;
      const call = r.call || {};
      if ((call.open_interest || 0) < f.oi && call.strike != null) return false;
      if ((call.yield_ann_pct || 0) < f.yieldMin) return false;
      if (call.otm_pct != null && Math.abs(call.otm_pct) > f.otmMax) return false;
      const earnDays = r.earnings && r.earnings.days;
      if (f.noEarn7 && earnDays != null && earnDays <= 7) return false;
      const ivTag = (r.risk && r.risk.iv_tag) || "";
      if (f.ivRich && ivTag !== "IV偏贵") return false;
      return true;
    });
  }
  function readCallEdgeWeights() {
    const box = $("#call-weights");
    const w = defaultEdgeWeights();
    if (!box) return w;
    box.querySelectorAll("input[data-w]").forEach((inp) => {
      w[inp.dataset.w] = Number(inp.value) || 0;
      const sp = box.querySelector(`[data-wv="${inp.dataset.w}"]`);
      if (sp) sp.textContent = String(inp.value);
    });
    state.callWeights = w;
    return w;
  }
  function openCallChain(symbol) {
    const data = state.callPayload;
    if (!data) return;
    const row = [...(data.picks || []), ...(data.all || [])].find((x) => x.symbol === symbol);
    if (!row) return;
    const weights = readCallEdgeWeights();
    const edge = row.edge && row.edge.metrics ? recomputeEdgeClient(row, weights) : row.edge;
    const call = row.call || {};
    const modal = $("#call-chain-modal");
    const title = $("#call-chain-title");
    const sub = $("#call-chain-sub");
    const edgeEl = $("#call-chain-edge");
    const checkEl = $("#call-chain-check");
    const body = $("#call-chain-body");
    if (title) title.textContent = `${row.symbol} · 看涨期权链`;
    if (sub) sub.textContent = `${row.name || ""} · 到期 ${call.expiry || "—"} · 建议 K=${call.strike != null ? "$"+call.strike : "—"}`;
    if (edgeEl && edge) {
      const p = edge.parts || {};
      edgeEl.innerHTML = `${edgePill(edge)}
        <div style="margin-top:6px;">VRP ${p.vrp} · IV分位 ${p.iv_pctile} · 期限 ${p.term} · RV ${p.rv_stable} · Skew ${p.skew}</div>
        <div class="put-check-mini">${esc((edge.notes && edge.notes.vrp) || "")} · ${esc((edge.notes && edge.notes.term) || "")} · ${esc((edge.notes && edge.notes.rv_stable) || "")}</div>`;
    }
    if (checkEl && row.checklist) {
      const ch = row.checklist;
      checkEl.innerHTML = `<div><strong>${esc(ch.verdict)}</strong> (${ch.passed}/${ch.total})</div>
        <div class="put-check-list">${(ch.checks || []).map((c) => {
          const cls = c.ok ? "ok" : c.hard ? "fail" : "soft";
          return `<div class="put-check-item ${cls}"><strong>${esc(c.title)}</strong><div class="put-check-mini">${esc(c.detail)}</div></div>`;
        }).join("")}</div>`;
    }
    if (body) {
      const rows = call.chain || [];
      if (!rows.length) body.innerHTML = `<tr><td colspan="8" class="empty">暂无链数据</td></tr>`;
      else body.innerHTML = rows.map((c) => `<tr class="${c.selected ? "selected" : ""}">
        <td>$${fmt(c.strike, 2)}</td>
        <td class="num">${fmt(c.otm_pct, 1)}%</td>
        <td class="num">${fmt(c.bid, 2)}</td>
        <td class="num">${fmt(c.ask, 2)}</td>
        <td class="num">${fmt(c.mid, 2)}</td>
        <td class="num">${fmt(c.iv, 1)}%</td>
        <td class="num">${c.open_interest ?? "—"}</td>
        <td class="num">${c.volume ?? "—"}</td>
      </tr>`).join("");
    }
    if (modal) modal.classList.remove("hidden");
  }
  function closeCallChain() {
    const modal = $("#call-chain-modal");
    if (modal) modal.classList.add("hidden");
  }
  async function loadShortCall(force = false, repoll = false) {
    if (state.callLoading) return;
    state.callLoading = true;
    const btn = $("#btn-call-refresh");
    if (btn) {
      btn.disabled = true;
      btn.textContent = force ? "扫描期权中…" : "加载中…";
    }
    try {
      const url = force ? "/api/short-call/refresh" : "/api/short-call/high-vol";
      const res = await fetch(apiUrl(url));
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || "short call failed");
      renderShortCall(data);
      scheduleScreenRepoll("call", data, repoll);
    } catch (err) {
      console.error("loadShortCall error:", err);
      const picks = $("#call-picks");
      if (picks) picks.innerHTML = `<div class="empty-card">加载失败：${esc(err.message || String(err))}</div>`;
      const body = $("#call-body");
      if (body) body.innerHTML = `<tr><td colspan="18" class="empty">加载失败</td></tr>`;
    } finally {
      state.callLoading = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = "重新扫描期权";
      }
    }
  }
  function openCallTheme(themeId, symbols) {
    state.callFilters = state.callFilters || callFilterDefaults();
    state.callFilters.themeSymbols = symbols || null;
    switchView("call");
    const bar = $("#call-theme-bar");
    if (bar && themeId) {
      [...bar.querySelectorAll("button[data-theme]")].forEach((b) =>
        b.classList.toggle("active", b.dataset.theme === themeId)
      );
    }
    if (state.callPayload) renderShortCall(state.callPayload);
    else loadShortCall(false);
  }
  function renderShortCall(data) {
    if (!data) return;
    state.callPayload = data;
    if (!state.callFilters) state.callFilters = callFilterDefaults();
    const meta = $("#call-meta");
    if (meta) {
      const n = (data.picks || []).length;
      const u = data.universe_size || 0;
      const acc = data.account_defaults || {};
      meta.textContent = `更新 ${data.updated_at || "—"} · 扫描 ${u} 只 · 推荐 ${n} 只 · 默认账户 $${acc.budget_usd || 50000} / 单标的≤${acc.max_name_pct || 15}%`;
    }
    const pill = $("#call-count-pill");
    const method = $("#call-method");
    if (method && data.method) {
      const m = data.method;
      method.innerHTML = [
        `<strong>目标</strong>：${esc(m.goal || "")}`,
        `<strong>波动</strong>：${esc(m.vol || "")}`,
        `<strong>期权</strong>：${esc(m.option || "")}`,
        `<strong>打分</strong>：${esc(m.score || "")}`,
        m.fundamental ? `<strong>基本面</strong>：${esc(m.fundamental)}` : "",
        `<strong>风险</strong>：${esc(m.risk || "")}`,
        m.cn_futures ? `<strong>国内期货</strong>：${esc(m.cn_futures)}` : "",
        data.disclaimer ? `<em>${esc(data.disclaimer)}</em>` : "",
      ].filter(Boolean).join("<br/>");
    }
    renderCallThemes(data.themes);
    const weights = readCallEdgeWeights();
    const enrich = (rows) => (rows || []).map((r) => {
      const copy = { ...r };
      if (r.edge && r.edge.metrics) copy.edge = recomputeEdgeClient(r, weights);
      return copy;
    });
    const themeSyms = state.callFilters && state.callFilters.themeSymbols;
    let filteredPicks;
    let filteredAll;
    if (themeSyms && themeSyms.length) {
      const themeRows = enrich(themeRowsFromPayload(data, themeSyms));
      filteredPicks = themeRows;
      filteredAll = themeRows;
      if (pill) pill.textContent = `主题 ${themeRows.length} 只`;
      const note = $("#call-theme-note");
      if (note) {
        note.hidden = false;
        note.textContent = "当前为主题成分明细（含未过推荐门槛的标的）。";
      }
    } else {
      filteredPicks = applyCallFilters(enrich(data.picks));
      filteredAll = applyCallFilters(enrich(data.all || data.picks));
      if (pill) pill.textContent = `${filteredPicks.length} 推荐`;
      const note = $("#call-theme-note");
      if (note) {
        note.hidden = true;
        note.textContent = "";
      }
    }
    renderCallCards(filteredPicks);
    renderCallTable(filteredAll);
    renderCallWatch();
    if (window.cnOptionCandidatesView) {
      window.cnOptionCandidatesView.renderInto($("#call-cn-picks"), data, "call");
      const panel = $("#call-cn-panel");
      if (panel) {
        const kind = (state.callFilters && state.callFilters.kind) || state.callKind || "all";
        panel.hidden = false;
        panel.style.opacity = kind === "stock" || kind === "etf" ? "0.55" : "1";
      }
    }
  }
  function renderCallThemes(themes) {
    const bar = $("#call-theme-bar");
    if (!bar) return;
    const list = themes || [];
    bar.innerHTML =
      `<span class="put-theme-label">主题联动</span>` +
      `<button type="button" data-theme="all" class="${!(state.callFilters && state.callFilters.themeSymbols) ? "active" : ""}">全部主题</button>` +
      list
        .map((th) => {
          const active = state.callFilters && state.callFilters.themeSymbols && state.callFilters.themeSymbols.join() === (th.symbols || []).join();
          return `<button type="button" data-theme="${esc(th.id)}" class="${active ? "active" : ""}">${esc(th.label)} <small>${(th.symbols || []).length}</small></button>`;
        })
        .join("");
  }
  function renderCallCards(picks) {
    const el = $("#call-picks");
    if (!el) return;
    if (!picks || !picks.length) {
      el.innerHTML = `<div class="empty-card">${
        state.callFilters && state.callFilters.themeSymbols
          ? "该主题暂无成分明细（数据里没有对应标的）"
          : "当前筛选下暂无推荐（放宽条件或切换主题）"
      }</div>`;
      return;
    }
    el.innerHTML = picks.slice(0, 12).map((r) => {
      const call = r.call || {};
      const risk = r.risk || {};
      const chg = r.chg_5d;
      const chgCls = chg > 0 ? "up" : chg < 0 ? "down" : "flat";
      const chgTxt = chg == null ? "—" : `${chg > 0 ? "+" : ""}${fmt(chg, 1)}%`;
      const earn = r.earnings ? `${r.earnings.days}天` : "—";
      return `<article class="put-card" data-symbol="${esc(r.symbol)}">
        <div class="put-card-top">
          <div>
            <div class="put-sym">${esc(r.symbol)} <span class="tag-pill">${esc(r.kind === "etf" ? "ETF" : "股票")}</span></div>
            <div class="put-name">${esc(r.name || "")} · ${esc(r.sector || "")}</div>
          </div>
          <div class="put-score" title="${esc(fundHeadline(r).label)}">${fmt(fundHeadline(r).value, 1)}</div>
        </div>
        <div class="put-edge-bar">${edgePill(r.edge)} ${checklistMini(r.checklist)} ${riskBadge(risk)}</div>
        ${fundBlock(r)}
        <div class="put-metrics">
          <div class="put-metric"><span class="l">现价</span><span class="v">$${fmt(r.price, 2)}</span></div>
          <div class="put-metric"><span class="l">HV20</span><span class="v">${fmt(r.hv20, 1)}%</span></div>
          <div class="put-metric"><span class="l">RSI</span><span class="v">${fmt(r.rsi14, 1)}</span></div>
          <div class="put-metric"><span class="l">5日</span><span class="v ${chgCls}">${chgTxt}</span></div>
        </div>
        <div class="put-metrics">
          <div class="put-metric"><span class="l">行权价</span><span class="v">${call.strike != null ? "$" + fmt(call.strike, 2) : "—"}</span></div>
          <div class="put-metric"><span class="l">到期</span><span class="v">${esc(call.expiry || "—")} (${call.dte != null ? call.dte + "d" : "—"})</span></div>
          <div class="put-metric"><span class="l">权利金</span><span class="v">${call.mid != null ? "$" + fmt(call.mid, 2) : "—"}</span></div>
          <div class="put-metric"><span class="l">年化</span><span class="v">${call.yield_ann_pct != null ? fmt(call.yield_ann_pct, 1) + "%" : "—"}</span></div>
        </div>
        <div class="put-metrics">
          <div class="put-metric"><span class="l">备兑占用</span><span class="v">${risk.capital_per_contract != null ? "$" + fmt(risk.capital_per_contract, 0) : "—"}</span></div>
          <div class="put-metric"><span class="l">建议张数</span><span class="v">${risk.max_contracts_hint != null ? risk.max_contracts_hint : "—"}</span></div>
          <div class="put-metric"><span class="l">财报</span><span class="v">${esc(earn)}</span></div>
          <div class="put-metric"><span class="l">OI</span><span class="v">${call.open_interest != null ? call.open_interest : "—"}</span></div>
        </div>
        <div class="put-action"><strong>${esc(r.bias_cn || "")}</strong> · ${esc(r.action || "")}</div>
        <div class="put-card-actions">
          <button type="button" class="btn btn-tiny btn-call-watch" data-symbol="${esc(r.symbol)}">加入观察</button>
          <button type="button" class="btn btn-tiny btn-call-chain" data-symbol="${esc(r.symbol)}">期权链/检查</button>
        </div>
      </article>`;
    }).join("");
  }
  function renderCallTable(rows) {
    const body = $("#call-body");
    if (!body) return;
    if (!rows || !rows.length) {
      body.innerHTML = `<tr><td colspan="18" class="empty">暂无数据</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((r) => {
      const call = r.call || {};
      const risk = r.risk || {};
      const callTxt = call.strike != null ? `$${fmt(call.strike, 2)} · ${esc(call.expiry || "")}` : "—";
      const chg = r.chg_5d;
      const chgCls = chg > 0 ? "up" : chg < 0 ? "down" : "flat";
      const chgTxt = chg == null ? "—" : `${chg > 0 ? "+" : ""}${fmt(chg, 1)}%`;
      const earn = r.earnings ? `${r.earnings.days}d` : "—";
      return `<tr>
        <td><strong>${esc(r.symbol)}</strong></td>
        <td>${esc(r.name || "")}</td>
        <td>${esc(r.kind === "etf" ? "ETF" : "股票")}</td>
        <td class="num">$${fmt(r.price, 2)}</td>
        <td class="num">${fmt(r.hv20, 1)}%</td>
        <td class="num">${fmt(r.hv_pctile, 0)}</td>
        <td class="num">${fmt(r.rsi14, 1)}</td>
        <td class="num ${chgCls}">${chgTxt}</td>
        <td class="num">${fundCell(r)}</td>
        <td>${esc((risk.iv_tag || "").replace("IV", ""))}</td>
        <td>${esc(risk.level || "—")}</td>
        <td>${esc(earn)}</td>
        <td>${esc(r.bias_cn || "")}</td>
        <td>${callTxt}</td>
        <td class="num">${call.mid != null ? "$" + fmt(call.mid, 2) : "—"}</td>
        <td class="num">${call.yield_ann_pct != null ? fmt(call.yield_ann_pct, 1) + "%" : "—"}</td>
        <td class="num">${risk.capital_per_contract != null ? "$" + fmt(risk.capital_per_contract, 0) : "—"}</td>
        <td>
          <button type="button" class="btn btn-tiny btn-call-watch" data-symbol="${esc(r.symbol)}">观察</button>
          <button type="button" class="btn btn-tiny btn-call-chain" data-symbol="${esc(r.symbol)}">链</button>
        </td>
      </tr>`;
    }).join("");
  }
  function addCallWatch(symbol) {
    const data = state.callPayload;
    if (!data) return;
    const row = (data.all || []).concat(data.picks || []).find((x) => x.symbol === symbol) ||
      (data.picks || []).find((x) => x.symbol === symbol);
    if (!row || !row.call) {
      alert("该标的暂无期权报价，无法加入");
      return;
    }
    const list = loadCallWatch();
    if (list.some((x) => x.symbol === symbol && x.status === "open")) {
      alert("观察池已有未平仓 " + symbol);
      return;
    }
    const call = row.call;
    list.unshift({
      id: `${symbol}-${Date.now()}`,
      symbol,
      name: row.name,
      status: "open",
      opened_at: new Date().toISOString().slice(0, 10),
      expiry: call.expiry,
      strike: call.strike,
      entry_mid: call.mid,
      exit_mid: null,
      closed_at: null,
      contracts: Math.max(1, riskContracts(row)),
      note: row.bias_cn || "",
    });
    saveCallWatch(list);
    renderCallWatch();
  }
  function closeCallWatch(id, exitMid) {
    const list = loadCallWatch();
    const item = list.find((x) => x.id === id);
    if (!item) return;
    const mid = exitMid != null ? Number(exitMid) : prompt(`输入 ${item.symbol} 平仓权利金（买回价）`, "0.05");
    if (mid == null || mid === "") return;
    item.exit_mid = Number(mid);
    item.status = "closed";
    item.closed_at = new Date().toISOString().slice(0, 10);
    saveCallWatch(list);
    renderCallWatch();
  }
  function renderCallWatch() {
    const body = $("#call-watch-body");
    const meta = $("#call-watch-meta");
    if (!body) return;
    const list = loadCallWatch();
    const openN = list.filter((x) => x.status === "open").length;
    const closed = list.filter((x) => x.status === "closed");
    let pnl = 0;
    closed.forEach((x) => {
      const c = x.contracts || 1;
      pnl += ((x.entry_mid || 0) - (x.exit_mid || 0)) * 100 * c;
    });
    if (meta) {
      meta.textContent = `持仓 ${openN} · 已平 ${closed.length} · 已实现约 $${fmt(pnl, 0)}（卖Call：开仓权利金 − 平仓买回）`;
    }
    if (!list.length) {
      body.innerHTML = `<tr><td colspan="10" class="empty">暂无记录 · 在推荐卡片点「加入观察」</td></tr>`;
      return;
    }
    body.innerHTML = list.map((x) => {
      const c = x.contracts || 1;
      let mark = x.exit_mid;
      if (mark == null && state.callPayload) {
        const live = (state.callPayload.all || []).find((r) => r.symbol === x.symbol);
        if (live && live.call) mark = live.call.mid;
      }
      const pnlOne = mark != null ? ((x.entry_mid || 0) - mark) * 100 * c : null;
      const pct = mark != null && x.entry_mid ? ((x.entry_mid - mark) / x.entry_mid) * 100 : null;
      const cls = pnlOne == null ? "" : pnlOne >= 0 ? "up" : "down";
      const ops =
        x.status === "open"
          ? `<button type="button" class="btn btn-tiny btn-call-close" data-id="${esc(x.id)}">平仓</button>
             <button type="button" class="btn btn-tiny btn-call-drop" data-id="${esc(x.id)}">删除</button>`
          : `<button type="button" class="btn btn-tiny btn-call-drop" data-id="${esc(x.id)}">删除</button>`;
      return `<tr>
        <td><strong>${esc(x.symbol)}</strong></td>
        <td>${esc(x.status === "open" ? "持仓" : "已平")}</td>
        <td>${esc(x.opened_at || "")}</td>
        <td>$${fmt(x.strike, 2)} · ${esc(x.expiry || "")}</td>
        <td class="num">$${fmt(x.entry_mid, 2)}</td>
        <td class="num">${mark != null ? "$" + fmt(mark, 2) : "—"}</td>
        <td class="num">${c}</td>
        <td class="num ${cls}">${pnlOne != null ? fmt(pnlOne, 0) : "—"}</td>
        <td class="num ${cls}">${pct != null ? fmt(pct, 1) + "%" : "—"}</td>
        <td>${ops}</td>
      </tr>`;
    }).join("");
  }

  async function loadCnIndices(force = false) {
    if (state.cnidxLoading && !force) return;
    state.cnidxLoading = true;
    const btn = $("#btn-cnidx-refresh");
    if (btn) {
      btn.disabled = true;
      btn.textContent = force ? "排队更新…" : "加载中…";
    }
    try {
      const url = force ? "/api/cn-indices/refresh" : "/api/cn-indices";
      let res;
      try {
        res = await fetch(apiUrl(url), { signal: AbortSignal.timeout(force ? 45000 : 25000) });
      } catch (e1) {
        res = await fetch(apiUrl("/api/cn-indices"), { signal: AbortSignal.timeout(25000) });
      }
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || "cn indices failed");
      // Options chain is independent — don't block HV cards if it fails.
      let opt = null;
      try {
        const ou = force ? "/api/cn-index-options/refresh" : "/api/cn-index-options";
        const ores = await fetch(apiUrl(ou), { signal: AbortSignal.timeout(20000) });
        if (ores.ok) opt = await ores.json();
      } catch (oe) {
        console.warn("cn index options:", oe);
      }
      state.cnidxOptions = opt;
      renderCnIndices(data, opt);
    } catch (err) {
      console.error("loadCnIndices error:", err);
      const el = $("#cnidx-cards");
      if (el) el.innerHTML = `<div class="empty-card">加载失败：${esc(err.message || String(err))}（请强刷后再试）</div>`;
    } finally {
      state.cnidxLoading = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = "重新拉取指数";
      }
    }
  }
  function _cnOptBoard(optPayload, indexId) {
    if (!optPayload) return null;
    const by = optPayload.by_index || {};
    if (by[indexId]) return by[indexId];
    const list = optPayload.products || [];
    return list.find((b) => b.index_id === indexId) || null;
  }
  function _cnOptSnippet(board) {
    if (!board) {
      return `<div class="cnidx-opt">期权链：暂无快照（中金所官网海外常不可达，可稍后重试）</div>`;
    }
    const months = board.months || [];
    const m0 = months[0];
    if (!m0) {
      const err = (board.errors || []).slice(0, 1).join("；") || "无近月合约";
      return `<div class="cnidx-opt">期权链：${esc(board.short || "")} 暂无数据（${esc(err)}）</div>`;
    }
    const band = m0.atm_band || [];
    const src = board.source === "sina" || String(board.source || "").includes("sina")
      ? "中金所（新浪转播）"
      : esc(board.source || "—");
    const head =
      `期权链：${src} · 近月 <strong>${esc(m0.code || "")}</strong>` +
      ` · 到期 ${esc(m0.expire || "—")}（${m0.days != null ? m0.days + "天" : "—"}）` +
      (m0.spot != null ? ` · 标的约 ${fmt(m0.spot, 2)}` : "") +
      (m0.atm_iv_est != null ? ` · ATM IV≈${fmt(m0.atm_iv_est, 1)}%` : "");
    if (!band.length) {
      return `<div class="cnidx-opt"><div class="cnidx-opt-head">${head}</div></div>`;
    }
    const rows = band.map((s) => {
      const c = s.call || {};
      const p = s.put || {};
      const isAtm = m0.atm_strike != null && Number(s.strike) === Number(m0.atm_strike);
      return `<tr class="${isAtm ? "atm" : ""}">
        <td class="num">${fmt(s.strike, 0)}</td>
        <td class="num">${c.last != null ? fmt(c.last, 1) : "—"}</td>
        <td class="num">${c.oi != null ? fmt(c.oi, 0) : "—"}</td>
        <td class="num">${p.last != null ? fmt(p.last, 1) : "—"}</td>
        <td class="num">${p.oi != null ? fmt(p.oi, 0) : "—"}</td>
      </tr>`;
    }).join("");
    return `<div class="cnidx-opt">
      <div class="cnidx-opt-head">${head}</div>
      <table class="cnidx-opt-table">
        <thead><tr><th>行权价</th><th>Call</th><th>C持仓</th><th>Put</th><th>P持仓</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  function _cnAdviceSnippet(advice) {
    if (!advice) {
      return `<div class="cnidx-advice empty">交易建议：暂无（刷新后由指数+近月期权链生成）</div>`;
    }
    const stance = advice.stance_cn || "—";
    const score = advice.score;
    const conf = advice.confidence;
    const bias = advice.option_bias_cn || "—";
    const stanceCls = ({
      "卖波动": "stance-sellvol",
      "防守区间": "stance-range",
      "观望高波": "stance-wait",
      "偏多": "stance-bull",
      "偏空": "stance-bear",
    })[stance] || "stance-range";
    const bullets = (advice.bullets_cn || []).slice(0, 4)
      .map((b) => `<li>${esc(b)}</li>`).join("");
    const lv = advice.levels || {};
    const supp = (lv.support || []).map((x) => fmt(x, 0)).join(" / ") || "—";
    const res = (lv.resistance || []).map((x) => fmt(x, 0)).join(" / ") || "—";
    const scoreTxt = score == null ? "—" : `${score > 0 ? "+" : ""}${score}`;
    return `<div class="cnidx-advice ${stanceCls}">
      <div class="cnidx-advice-top">
        <span class="cnidx-stance">${esc(stance)}</span>
        <span class="cnidx-score">评分 ${esc(scoreTxt)} · 置信 ${conf != null ? conf : "—"}</span>
      </div>
      <div class="cnidx-opt-bias">期权偏向：<strong>${esc(bias)}</strong></div>
      <ul class="cnidx-bullets">${bullets || "<li>暂无细则</li>"}</ul>
      <div class="cnidx-levels">
        <span class="lvl support">支撑 ${esc(supp)}</span>
        <span class="lvl resist">阻力 ${esc(res)}</span>
      </div>
      <div class="cnidx-disc">${esc(advice.disclaimer_cn || "研究用途，非投资建议")}</div>
    </div>`;
  }

  function renderCnIndices(data, optPayload) {
    if (!data) return;
    state.cnidxPayload = data;
    if (optPayload === undefined) optPayload = state.cnidxOptions;
    const meta = $("#cnidx-meta");
    const frontCodes = [];
    if (optPayload && optPayload.products) {
      for (const b of optPayload.products) {
        const m0 = (b.months || [])[0];
        if (m0 && m0.code) frontCodes.push(m0.code);
      }
    }
    const optLine = frontCodes.length
      ? `期权链：中金所（新浪转播）· ${frontCodes.join("/")}`
      : "期权链快照待更新";
    if (meta) meta.textContent = `更新 ${data.updated_at || "—"} · ${optLine}`;
    const method = $("#cnidx-method");
    if (method && data.method) {
      const m = data.method;
      const optMethod = (optPayload && optPayload.method && optPayload.method.live)
        ? optPayload.method.live
        : (m.options || "");
      const am = data.advice_method || {};
      method.innerHTML = [
        `<strong>目标</strong>：${esc(m.goal || "")}`,
        `<strong>数据</strong>：${esc(m.source || "")}`,
        `<strong>期权</strong>：${esc(optMethod || "中金所 IO/HO/MO（新浪转播延时）")}`,
        `<strong>指标</strong>：${esc(m.stats || "")}`,
        am.score ? `<strong>建议评分</strong>：${esc(am.score)}` : "",
        data.disclaimer ? `<em>${esc(data.disclaimer)}</em>` : "",
      ].filter(Boolean).join("<br/>");
    }
    const el = $("#cnidx-cards");
    const rows = data.indices || [];
    if (el) {
      if (!rows.length) el.innerHTML = `<div class="empty-card">暂无指数数据</div>`;
      else el.innerHTML = rows.map((r) => {
        const chg = r.chg_1d;
        const chgCls = chg > 0 ? "up" : chg < 0 ? "down" : "flat";
        const chgTxt = chg == null ? "—" : `${chg > 0 ? "+" : ""}${fmt(chg, 2)}%`;
        const fut = r.futures;
        const futTxt = fut && fut.price != null
          ? `${esc(fut.name || fut.id)} 约 ${fmt(fut.price, 2)}${fut.change_pct != null ? "（" + fmt(fut.change_pct, 2) + "%）" : ""}`
          : `${esc(r.futures_name || r.futures_id || "股指期货")}：当前看板商品池未纳入报价`;
        const board = _cnOptBoard(optPayload, r.id);
        const optHtml = _cnOptSnippet(board);
        const note = board && (board.months || []).length
          ? `场内期权：${esc((board.months[0] || {}).code || board.short || "")}（新浪转播中金所）`
          : (r.options_note || "场内期权链见下方近月摘要");
        return `<article class="put-card">
          <div class="put-card-top">
            <div>
              <div class="put-sym">${esc(r.name)} <span class="tag-pill">${esc(r.short || "")}</span></div>
              <div class="put-name">${esc(r.yahoo || "")}</div>
            </div>
            <div class="put-score">${esc(r.bias_cn || "中性")}</div>
          </div>
          <div class="put-metrics">
            <div class="put-metric"><span class="l">现价</span><span class="v">${fmt(r.price, 2)}</span></div>
            <div class="put-metric"><span class="l">今日</span><span class="v ${chgCls}">${chgTxt}</span></div>
            <div class="put-metric"><span class="l">5日</span><span class="v">${r.chg_5d == null ? "—" : fmt(r.chg_5d, 2) + "%"}</span></div>
            <div class="put-metric"><span class="l">21日</span><span class="v">${r.chg_21d == null ? "—" : fmt(r.chg_21d, 2) + "%"}</span></div>
          </div>
          <div class="put-metrics">
            <div class="put-metric"><span class="l">HV20</span><span class="v">${fmt(r.hv20, 1)}%</span></div>
            <div class="put-metric"><span class="l">HV60</span><span class="v">${fmt(r.hv60, 1)}%</span></div>
            <div class="put-metric"><span class="l">HV分位</span><span class="v">${fmt(r.hv_pctile, 0)}</span></div>
            <div class="put-metric"><span class="l">RSI14</span><span class="v">${fmt(r.rsi14, 1)}</span></div>
          </div>
          <div class="put-metrics">
            <div class="put-metric"><span class="l">相对MA20</span><span class="v">${r.vs_ma20_pct == null ? "—" : fmt(r.vs_ma20_pct, 2) + "%"}</span></div>
            <div class="put-metric"><span class="l">相对MA60</span><span class="v">${r.vs_ma60_pct == null ? "—" : fmt(r.vs_ma60_pct, 2) + "%"}</span></div>
            <div class="put-metric"><span class="l">距52周高</span><span class="v">${r.from_52w_high_pct == null ? "—" : fmt(r.from_52w_high_pct, 2) + "%"}</span></div>
            <div class="put-metric"><span class="l">距52周低</span><span class="v">${r.from_52w_low_pct == null ? "—" : fmt(r.from_52w_low_pct, 2) + "%"}</span></div>
          </div>
          <div class="cnidx-fut">${futTxt}</div>
          ${_cnAdviceSnippet(r.advice || (data.advices || {})[r.id])}
          ${optHtml}
          <div class="put-action">${esc(note)}</div>
        </article>`;
      }).join("");
    }
    const satBody = $("#cnidx-sat-body");
    if (satBody) {
      const sats = data.satellites || [];
      if (!sats.length) satBody.innerHTML = `<tr><td colspan="10" class="empty">暂无对照 ETF</td></tr>`;
      else satBody.innerHTML = sats.map((r) => {
        const chg = r.chg_1d;
        const chgCls = chg > 0 ? "up" : chg < 0 ? "down" : "flat";
        return `<tr>
          <td><strong>${esc(r.symbol)}</strong></td>
          <td>${esc(r.name || "")}</td>
          <td class="num">${fmt(r.price, 2)}</td>
          <td class="num ${chgCls}">${chg == null ? "—" : fmt(chg, 2) + "%"}</td>
          <td class="num">${r.chg_5d == null ? "—" : fmt(r.chg_5d, 2) + "%"}</td>
          <td class="num">${r.chg_21d == null ? "—" : fmt(r.chg_21d, 2) + "%"}</td>
          <td class="num">${fmt(r.hv20, 1)}%</td>
          <td class="num">${fmt(r.hv60, 1)}%</td>
          <td class="num">${fmt(r.rsi14, 1)}</td>
          <td class="num">${r.vs_ma20_pct == null ? "—" : fmt(r.vs_ma20_pct, 2) + "%"}</td>
        </tr>`;
      }).join("");
    }
  }

  async function boot() {
    bindEvents();
    ensureChart();
    window.addEventListener("resize", resizeNgCharts);
    // Load extremes in parallel with quotes (extremes may take longer first time)
    const pExt = loadExtremes(false);
    const pSup = loadSupply(false);
    await loadQuotes(false);
    await pExt;
    await pSup;
    startTimer();
  }

  boot();
})();
