/* 国内期货卖Put/卖Call 方向候选展示辅助（纯前端派生；兼容旧快照缺字段）。
 * 浏览器: window.cnOptionCandidatesView；Node 测试: module.exports。
 */
(function (root) {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmt(v, n) {
    if (v == null || v !== v) return "—";
    const x = Number(v);
    if (!isFinite(x)) return "—";
    return x.toFixed(n == null ? 2 : n);
  }

  function isCnRow(r) {
    return !!(r && (r.kind === "cn_fut" || r.market === "cn"));
  }

  /** US put/call tables must never include CN rows. */
  function excludeCnFromUsRows(rows) {
    return (rows || []).filter((r) => !isCnRow(r));
  }

  function filterByKind(rows, kind) {
    const list = rows || [];
    const k = kind || "all";
    if (k === "cn_fut") return list.filter(isCnRow);
    // all / stock / etf: US-only
    const us = excludeCnFromUsRows(list);
    if (k === "all") return us;
    return us.filter((r) => (r.kind || "stock") === k);
  }

  function normalizePayload(data) {
    const d = data && typeof data === "object" ? data : {};
    const candidates = Array.isArray(d.cn_candidates) ? d.cn_candidates : [];
    const meta = d.cn_candidate_meta && typeof d.cn_candidate_meta === "object" ? d.cn_candidate_meta : null;
    const status = (meta && meta.status) || (candidates.length ? "ok" : "absent_in_snapshot");
    const note =
      (meta && meta.note) ||
      (status === "absent_in_snapshot"
        ? "当前快照未包含国内期货方向候选；重新扫描或导出后才会生成。未伪补信号。"
        : status === "empty" || status === "below_threshold"
          ? "白名单品种暂无达标方向候选（研究/观望门槛）。"
          : "");
    return {
      candidates: candidates,
      meta: meta || { status: status, note: note },
      status: status,
      note: note,
      scanned_at: (meta && meta.scanned_at) || d.updated_at || null,
      scanned_at_tz: (meta && meta.scanned_at_tz) || "Asia/Tokyo (UTC+9)",
    };
  }

  function cardHtml(r, side) {
    const chg = r.chg_5d;
    const chgCls = chg > 0 ? "up" : chg < 0 ? "down" : "flat";
    const chgTxt = chg == null ? "—" : `${chg > 0 ? "+" : ""}${fmt(chg, 1)}%`;
    const risk = r.risk || {};
    const flags = (risk.flags || []).join(" · ");
    const sideLabel = side === "call" ? "卖Call方向" : "卖Put方向";
    const asof = r.asof || "未知";
    const asofLabel = asof === "未知" ? "行情截至：未知" : `行情截至：${asof}（日线日历日）`;
    const scanned = r.scanned_at || "";
    const scannedLabel = scanned
      ? `扫描时间：${scanned}${r.scanned_at_tz ? " · " + r.scanned_at_tz : " · Asia/Tokyo (UTC+9)"}`
      : "扫描时间：—";
    return `<article class="put-card cn-fut-card" data-symbol="${esc(r.symbol)}">
      <div class="put-card-top">
        <div>
          <div class="put-sym">${esc(r.symbol)}
            <span class="badge cn">国内</span>
            <span class="tag-pill">连续合约</span>
            <span class="tag-pill">${esc(sideLabel)}</span>
          </div>
          <div class="put-name">${esc(r.name || "")} · ${esc(r.exchange || "")} · ${esc(r.sector || "")} · CNY</div>
        </div>
        <div class="put-score">${fmt(r.score, 1)}</div>
      </div>
      <div class="put-metrics">
        <div class="put-metric"><span class="l">现价</span><span class="v">¥${fmt(r.price, 2)}</span></div>
        <div class="put-metric"><span class="l">HV20</span><span class="v">${fmt(r.hv20, 1)}%</span></div>
        <div class="put-metric"><span class="l">HV分位</span><span class="v">${fmt(r.hv_pctile, 0)}</span></div>
        <div class="put-metric"><span class="l">RSI14</span><span class="v">${fmt(r.rsi14, 1)}</span></div>
        <div class="put-metric"><span class="l">5日</span><span class="v ${chgCls}">${chgTxt}</span></div>
        <div class="put-metric"><span class="l">偏置</span><span class="v">${esc(r.bias_cn || "—")}</span></div>
      </div>
      <div class="cn-asof">${esc(asofLabel)}</div>
      <div class="cn-scanned">${esc(scannedLabel)}</div>
      <div class="put-action">${esc(r.action || "仅标的方向研究/观望候选；未核验期权链与流动性，不能下单。")}</div>
      <div class="put-risk cn-fut-risk" title="${esc(flags)}">${esc(risk.margin_note || "期货期权保证金；行权后期货持仓风险")}</div>
      <div class="put-flags">${esc(flags || "连续合约 · 期权链未核验 · 仅方向研究")}</div>
    </article>`;
  }

  function emptyHtml(note) {
    return `<div class="empty-card">${esc(note || "暂无国内期货方向候选")}</div>`;
  }

  function renderInto(el, data, side) {
    if (!el) return normalizePayload(data);
    const norm = normalizePayload(data);
    const pillId = side === "call" ? "call-cn-count-pill" : "put-cn-count-pill";
    const metaId = side === "call" ? "call-cn-meta" : "put-cn-meta";
    const pill = typeof document !== "undefined" ? document.getElementById(pillId) : null;
    const metaEl = typeof document !== "undefined" ? document.getElementById(metaId) : null;
    if (pill) pill.textContent = `${norm.candidates.length} 只`;
    if (metaEl) {
      const m = norm.meta || {};
      const bits = [
        m.note || norm.note || "连续合约标的方向研究 · 未核验期权链/流动性 · 人民币 · 不可下单",
        norm.scanned_at ? `扫描 ${norm.scanned_at} (${norm.scanned_at_tz || "Asia/Tokyo"})` : null,
      ].filter(Boolean);
      metaEl.textContent = bits.join(" · ");
    }
    if (!norm.candidates.length) {
      el.innerHTML = emptyHtml(norm.note);
      return norm;
    }
    el.innerHTML = norm.candidates.slice(0, 12).map((r) => cardHtml(r, side)).join("");
    return norm;
  }

  /**
   * Simulate Put/Call US-table filter kind gate used by static/app.js apply*Filters.
   * CN never survives into the US dollar/stock renderer.
   */
  function usTableRowsAfterKindFilter(rows, kind) {
    return excludeCnFromUsRows(filterByKind(rows, kind));
  }

  const api = {
    normalizePayload: normalizePayload,
    filterByKind: filterByKind,
    excludeCnFromUsRows: excludeCnFromUsRows,
    isCnRow: isCnRow,
    usTableRowsAfterKindFilter: usTableRowsAfterKindFilter,
    cardHtml: cardHtml,
    emptyHtml: emptyHtml,
    renderInto: renderInto,
  };
  root.cnOptionCandidatesView = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
