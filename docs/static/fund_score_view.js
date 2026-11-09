/* 技术分 / 基本面分 / 综合分 展示（卖Put/卖Call，美股与国内期货共用；纯前端，兼容旧快照）。
 * 浏览器: window.fundScoreView；Node 测试: module.exports。
 * 旧快照行没有 fund_* 字段时如实显示「基本面未计算」，不在前端补算或插补。
 */
(function (root) {
  "use strict";

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

  function fmt(v, n) {
    const x = num(v);
    return x == null ? "—" : x.toFixed(n == null ? 1 : n);
  }

  function isLegacy(r) {
    return !!r && r.fund_status === undefined && r.combined_score === undefined && r.tech_score === undefined;
  }

  /** {tech, fund, combined, statusText, legacy, basis} — never invents a fundamental score. */
  function scores(r) {
    const row = r || {};
    const legacy = isLegacy(row);
    const tech = num(row.tech_score != null ? row.tech_score : row.score);
    const fund = legacy ? null : num(row.fund_score);
    const combined = legacy ? null : num(row.combined_score);
    let statusText;
    if (legacy) statusText = "基本面未计算（旧快照，重新扫描后生成）";
    else if (row.fund_status_cn) statusText = String(row.fund_status_cn);
    else if (fund == null) statusText = "基本面无数据";
    else statusText = "";
    return { tech: tech, fund: fund, combined: combined, statusText: statusText, legacy: legacy,
      basis: row.combined_basis || (legacy ? "legacy" : null) };
  }

  /** Main number on a card: combined when present, else technical (labelled). */
  function headline(r) {
    const s = scores(r);
    if (s.combined != null) return { value: s.combined, label: "综合分" };
    return { value: s.tech, label: "技术分" };
  }

  function earningsWarning(r) {
    const ew = r && r.earnings_window;
    if (!ew || !ew.in_window) return "";
    return `⚠ ${ew.text || "财报落在期权到期前"}`;
  }

  function factorLine(f) {
    const name = esc(f.name || f.key || "");
    if (f.status !== "ok") {
      return `<li class="fund-f missing"><span class="fund-fn">${name}</span>：无数据${f.reason ? `（${esc(f.reason)}）` : ""}</li>`;
    }
    const c = num(f.contribution);
    const cTxt = c == null ? "" : ` <span class="fund-fc" title="对本侧基本面分的贡献（相对中性50）">${c > 0 ? "+" : ""}${c.toFixed(1)}</span>`;
    const asof = f.asof ? ` · 截至 ${esc(f.asof)}` : "";
    const safeUrl = /^https?:\/\//i.test(String(f.source_url || ""));
    const src = f.source_url && safeUrl
      ? ` · <a href="${esc(f.source_url)}" target="_blank" rel="noopener noreferrer">${esc(f.source || "来源")}</a>`
      : f.source ? ` · ${esc(f.source)}` : "";
    return `<li class="fund-f"><span class="fund-fn">${name}</span>：${esc(f.signal_text || "")}${cTxt}` +
      `<div class="fund-fv">${esc(f.value_text || "")}${asof}${src}</div></li>`;
  }

  /** Score strip + per-factor reasons + earnings warning. */
  function blockHtml(r, opts) {
    const o = opts || {};
    const s = scores(r);
    const strip = `<div class="fund-strip">` +
      `<span class="fund-pill tech">技术分 ${fmt(s.tech, 1)}</span>` +
      `<span class="fund-pill fund">基本面分 ${s.fund == null ? (s.legacy ? "未计算" : "无数据") : fmt(s.fund, 1)}</span>` +
      `<span class="fund-pill comb">综合分 ${s.combined == null ? "—" : fmt(s.combined, 1)}</span>` +
      `</div>`;
    const status = s.statusText ? `<div class="fund-status">${esc(s.statusText)}</div>` : "";
    const facs = Array.isArray(r && r.fund_factors) ? r.fund_factors : [];
    const list = facs.length && !o.compact
      ? `<details class="fund-details"><summary>基本面理由（${facs.filter((f) => f.status === "ok").length}/${facs.length} 项有数据）</summary><ul class="fund-list">${facs.map(factorLine).join("")}</ul>` +
        (r.fund_penalty ? `<div class="fund-pen">财报窗口扣分 ${esc(fmt(r.fund_penalty.points, 1))}：${esc(r.fund_penalty.reason || "")}</div>` : "") +
        `</details>`
      : "";
    const ew = earningsWarning(r);
    const warn = ew ? `<div class="fund-earn-warn">${esc(ew)}</div>` : "";
    return `<div class="fund-block">${strip}${status}${warn}${list}</div>`;
  }

  /** Compact table cell: combined (tech/fund). */
  function cellHtml(r) {
    const s = scores(r);
    const main = s.combined != null ? fmt(s.combined, 1) : fmt(s.tech, 1);
    const sub = s.legacy
      ? "技术分 · 基本面未计算"
      : `技${fmt(s.tech, 1)} / 基${s.fund == null ? "无" : fmt(s.fund, 1)}`;
    return `<strong>${main}</strong><div class="fund-cell-sub" title="${esc(s.statusText)}">${esc(sub)}</div>`;
  }

  function methodLine(data) {
    const m = (data && data.method) || {};
    return m.fundamental ? String(m.fundamental) : "";
  }

  const api = { esc: esc, scores: scores, headline: headline, earningsWarning: earningsWarning,
    factorLine: factorLine, blockHtml: blockHtml, cellHtml: cellHtml, isLegacy: isLegacy, methodLine: methodLine };
  root.fundScoreView = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
