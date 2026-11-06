/* 国内期权卖方候选：卖Put / 卖Call 页的市场切换（美股 / 国内股指 / 国内商品）
 * 独立脚本：自带静态模式判断（不依赖 app.js 内部 apiUrl）。所有行情为延时转播，仅供参考。 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else { root.cnOptions = api; api.init(); }
})(typeof window !== "undefined" ? window : globalThis, function () {
  const FETCH_TIMEOUT_MS = 15000;
  const MARKET_LABEL = { cn_index: "国内股指期权", cn_commodity: "国内商品期权" };
  const STATUS_LABEL = {
    delayed: "延时（30分钟内）", last_session: "上一交易时段快照", stale: "过期（>24小时，仅参考）",
    unknown: "时效未知", invalid: "时间异常",
  };
  const UI_DEFAULTS = { otm_min_pct: 3, otm_max_pct: 15, min_oi: 100, dte_min: 7, dte_max: 60 };

  function isStaticMode(loc, win) {
    return /github\.io$/i.test(loc.hostname || "") || String(loc.search || "").includes("static=1") || (win && win.STATIC_MODE === true);
  }
  function resolveUrl(side, loc, win) {
    if (side !== "put" && side !== "call") throw new Error("bad side");
    return isStaticMode(loc, win) ? `./api/cn_short_${side}.json` : `/api/cn-short-${side}`;
  }
  function cstToday(now) {
    const d = new Date(now.getTime() + 8 * 3600 * 1000);
    return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, iso: d.toISOString().slice(0, 10) };
  }
  function isExpiredRow(r, now) {
    const t = cstToday(now);
    if (r.expiry) return String(r.expiry).slice(0, 10) < t.iso;
    if (r.market === "cn_commodity") {
      const m = String(r.contract_month || "").match(/(\d{4})$/);
      if (!m) return true;
      return Number(m[1]) <= (t.y % 100) * 100 + t.m;
    }
    return true;
  }
  function filterRows(rows, f, market, now) {
    return (rows || []).filter((r) => {
      if (isExpiredRow(r, now)) return false;
      if (r.freshness === "invalid") return false;
      if (!f.includeStale && (r.freshness === "stale" || r.freshness === "unknown")) return false;
      if (f.product && f.product !== "all" && r.product !== f.product) return false;
      if (r.otm_pct == null || r.otm_pct < f.otm_min_pct || r.otm_pct > f.otm_max_pct) return false;
      if ((r.oi || 0) < f.min_oi) return false;
      if (market === "cn_index" && (r.dte == null || r.dte < f.dte_min || r.dte > f.dte_max)) return false;
      return true;
    });
  }

  // ---------------- DOM ----------------
  const cache = {};
  const state = { put: null, call: null };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d));
  const badge = (txt, warn) => `<span class="cn-badge${warn ? " warn" : ""}">${esc(txt)}</span>`;

  function filtersFor(side, market) {
    const k = `${side}:${market}`;
    if (!state[k]) state[k] = { ...UI_DEFAULTS, product: "all", includeStale: false };
    return state[k];
  }
  function activeMarket(side) {
    const b = document.querySelector(`.cn-market-bar[data-side="${side}"] button.active`);
    return b ? b.dataset.market : "us";
  }

  function setMarket(side, market) {
    const bar = document.querySelector(`.cn-market-bar[data-side="${side}"]`);
    if (bar) bar.querySelectorAll("button[data-market]").forEach((b) => b.classList.toggle("active", b.dataset.market === market));
    const us = document.getElementById(`${side}-us-layout`);
    const cn = document.getElementById(`${side}-cn-panel`);
    if (!us || !cn) return;
    us.classList.toggle("hidden", market !== "us");
    cn.classList.toggle("hidden", market === "us");
    if (market !== "us") load(side, false);
  }

  async function load(side, force) {
    const panel = document.getElementById(`${side}-cn-panel`);
    if (force) delete cache[side];
    if (cache[side] && cache[side].data) return render(side);
    if (cache[side] && cache[side].pending) return;
    panel.innerHTML = `<div class="empty-card">加载${esc(MARKET_LABEL[activeMarket(side)] || "")}快照…</div>`;
    const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = setTimeout(() => ctrl && ctrl.abort(), FETCH_TIMEOUT_MS);
    cache[side] = { pending: true };
    try {
      let u = resolveUrl(side, location, window);
      if (force) u += (u.includes("?") ? "&" : "?") + "t=" + Date.now();
      const res = await fetch(u, { cache: force ? "no-store" : "default", signal: ctrl ? ctrl.signal : undefined });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      cache[side] = { data };
      if (activeMarket(side) !== "us") render(side);
    } catch (err) {
      delete cache[side];
      const msg = err && err.name === "AbortError" ? "请求超时" : (err.message || String(err));
      panel.innerHTML = `<div class="empty-card">加载失败：${esc(msg)} <button type="button" class="btn btn-tiny" data-cn-refresh>重试</button></div>`;
    } finally {
      clearTimeout(timer);
    }
  }

  function controls(side, market, f, products) {
    const opts = ['<option value="all">全部品种</option>']
      .concat(products.map((p) => `<option value="${esc(p.key)}"${f.product === p.key ? " selected" : ""}>${esc(p.label)}</option>`)).join("");
    const n = (name, v, step) => `<input type="number" data-f="${name}" value="${esc(v)}" step="${step || 1}" style="width:4.5em">`;
    return `<div class="cn-opt-controls">
      <label>品种 <select data-f="product">${opts}</select></label>
      <label>虚值% ${n("otm_min_pct", f.otm_min_pct, 0.5)}–${n("otm_max_pct", f.otm_max_pct, 0.5)}</label>
      <label>持仓≥ ${n("min_oi", f.min_oi)}</label>
      ${market === "cn_index" ? `<label>剩余天数 ${n("dte_min", f.dte_min)}–${n("dte_max", f.dte_max)}</label>` : ""}
      <label><input type="checkbox" data-f="includeStale"${f.includeStale ? " checked" : ""}> 含过期/时效未知行</label>
      <button type="button" class="btn btn-tiny" data-cn-refresh>重新加载快照</button>
    </div>`;
  }

  function render(side) {
    const market = activeMarket(side);
    const panel = document.getElementById(`${side}-cn-panel`);
    const data = (cache[side] || {}).data;
    if (!panel || !data || market === "us") return;
    const blk = (data.markets || {})[market];
    const sideCn = side === "put" ? "卖Put" : "卖Call";
    let html = `<h2>${MARKET_LABEL[market]} · ${sideCn}筛选</h2>`;
    if (!blk) {
      panel.innerHTML = `${html}<div class="empty-card">${esc(data.error || "暂无该市场快照")} <button type="button" class="btn btn-tiny" data-cn-refresh>重新加载快照</button></div>`;
      return;
    }
    const f = filtersFor(side, market);
    const rng = data.fetch_range || {};
    const products = [];
    const seen = new Set();
    (blk.rows || []).forEach((r) => { if (!seen.has(r.product)) { seen.add(r.product); products.push({ key: r.product, label: `${r.exchange} ${r.product_name || r.product}` }); } });
    html += controls(side, market, f, products);
    if (blk.stale) html += `<div class="cn-stale-banner">⚠ ${esc(blk.stale_reason || "刷新失败，显示上次成功结果")}（上次成功 ${esc(blk.last_good_at || "—")}，失败于 ${esc(blk.refresh_failed_at || "—")}）。以下数据已过时。</div>`;
    const notes = data.notes || {};
    html += `<p class="sub">快照生成 ${esc(data.updated_at || "—")} · 本市场抓取 ${esc(blk.fetched_at || "—")} · 快照抓取范围：虚值 ${num((rng.otm_min || 0) * 100, 0)}–${num((rng.otm_max || 0) * 100, 0)}%${market === "cn_index" ? `、剩余 ${esc(rng.index_dte_min)}–${esc(rng.index_dte_max)} 天` : ""}，超出此范围的合约不在快照中。</p>`;
    html += `<div class="cn-opt-notes">${[notes.premium, market === "cn_commodity" ? notes.commodity : "", notes.freshness, notes.iv, notes.margin].filter(Boolean).map(esc).join("<br/>")}<br/>仅供研究筛选，非交易建议。</div>`;
    const rows = filterRows(blk.rows, f, market, new Date());
    if (!rows.length) {
      html += `<div class="empty-card">当前筛选下无候选。${esc(blk.empty_reason || "")}</div>`;
    } else {
      const isCom = market === "cn_commodity";
      const th = ["品种", "期权合约", isCom ? "标的期货" : "标的指数", "标的价", "标的时间/状态", "行权价", "虚值%", "买一", "卖一", "持仓",
        isCom ? "乘数" : "乘数(元/点)", "每手权利金参考(买一)", isCom ? "到期" : "到期/天", "交割", "期权报价时间/状态"];
      const body = rows.map((r) => {
        const st = r.freshness;
        return `<tr>
        <td>${esc(r.exchange)} ${esc(r.product_name || r.product)}${isCom ? "<br/>" + badge("观察候选·到期未核验", true) : ""}</td>
        <td>${esc(r.option_code)}</td>
        <td>${esc(r.underlying_contract)}${r.underlying_name ? " " + esc(r.underlying_name) : ""}</td>
        <td class="num">${num(r.underlying_price, 2)}</td>
        <td>${esc(r.underlying_time || (r.underlying_snapshot_at ? "快照抓取 " + r.underlying_snapshot_at : "无"))} ${badge(STATUS_LABEL[r.underlying_status] || "时效未知", r.underlying_status !== "delayed")}</td>
        <td class="num">${num(r.strike, 2)}</td>
        <td class="num">${num(r.otm_pct, 1)}</td>
        <td class="num">${num(r.bid, 2)}</td>
        <td class="num">${num(r.ask, 2)}</td>
        <td class="num">${r.oi ?? "—"}</td>
        <td class="num">${r.multiplier ? num(r.multiplier, 0) : "未核验"}</td>
        <td class="num">${r.premium_ref_per_lot_bid != null ? "¥" + num(r.premium_ref_per_lot_bid, 0) : "—"}</td>
        <td>${r.expiry ? esc(r.expiry) + (r.dte != null ? ` / ${r.dte}` : "") : "未核验"}${r.near_expiry_risk ? " " + badge("待核验到期风险", true) : ""}</td>
        <td>${esc(r.settlement || "")}</td>
        <td>${esc(r.quote_time || "无")} ${badge(STATUS_LABEL[st] || "时效未知", st !== "delayed")}</td>
      </tr>`;
      }).join("");
      html += `<p class="sub">显示 ${rows.length} / 快照 ${(blk.rows || []).length} 行</p><div class="cn-opt-scroll"><table class="cn-opt-table"><thead><tr>${th.map((t) => `<th>${t}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
    }
    panel.innerHTML = html;
  }

  function onPanelEvent(side, e) {
    if (e.target.closest("[data-cn-refresh]")) return load(side, true);
    const el = e.target.closest("[data-f]");
    if (!el || e.type === "click" && el.tagName !== "INPUT") return;
    const f = filtersFor(side, activeMarket(side));
    const k = el.dataset.f;
    if (k === "includeStale") f[k] = el.checked;
    else if (k === "product") f[k] = el.value;
    else { const v = Number(el.value); if (!Number.isNaN(v)) f[k] = v; }
    if (e.type === "change") render(side);
  }

  function init() {
    if (typeof document === "undefined") return;
    document.querySelectorAll(".cn-market-bar").forEach((bar) => {
      bar.addEventListener("click", (e) => {
        const b = e.target.closest("button[data-market]");
        if (b) setMarket(bar.dataset.side, b.dataset.market);
      });
    });
    ["put", "call"].forEach((side) => {
      const p = document.getElementById(`${side}-cn-panel`);
      if (!p) return;
      p.addEventListener("click", (e) => { if (e.target.closest("[data-cn-refresh]")) onPanelEvent(side, e); });
      p.addEventListener("change", (e) => onPanelEvent(side, e));
    });
  }

  return { init, isStaticMode, resolveUrl, isExpiredRow, filterRows, UI_DEFAULTS };
});
