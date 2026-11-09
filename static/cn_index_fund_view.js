/* A股指数：技术分 / 基本面分 / 综合分 + 分组基本面理由（政策组置顶、权重最高）+ 政策事件窗口提醒。
 * 浏览器: window.cnIndexFundView；Node 测试: module.exports。
 * 旧快照没有 fund_* 字段时如实显示「基本面未计算」，前端不补算、不插补。
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

  function safeUrl(u) {
    return /^https?:\/\//i.test(String(u || ""));
  }

  function isLegacy(a) {
    return !!a && a.fund_status === undefined && a.tech_score === undefined && a.combined_score === undefined;
  }

  /** {tech, fund, combined, statusText, legacy} — never invents a fundamental score. */
  function scores(a) {
    const adv = a || {};
    const legacy = isLegacy(adv);
    const tech = legacy
      ? (num(adv.score) == null ? null : Math.max(0, Math.min(100, 50 + num(adv.score) / 2)))
      : num(adv.tech_score);
    const fund = legacy ? null : num(adv.fund_score);
    const combined = legacy ? null : num(adv.combined_score);
    let statusText;
    if (legacy) statusText = "基本面未计算（旧快照，重新拉取后生成）";
    else if (adv.fund_status_cn) statusText = String(adv.fund_status_cn);
    else if (fund == null) statusText = "基本面无数据";
    else statusText = "";
    return { tech: tech, fund: fund, combined: combined, statusText: statusText, legacy: legacy };
  }

  function factorLine(f) {
    const name = esc(f.name || f.key || "");
    const scope = f.scope === "index" ? "" : `<span class="cnf-scope" title="全市场因子，三只指数相同">全市场</span>`;
    if (f.status !== "ok") {
      return `<li class="fund-f missing"><span class="fund-fn">${name}</span>${scope}：无数据${f.reason ? `（${esc(f.reason)}）` : ""}</li>`;
    }
    const c = num(f.contribution);
    const w = num(f.weight);
    const cTxt = c == null ? "" : ` <span class="fund-fc" title="对基本面分的贡献（相对中性50，正=偏多）">${c > 0 ? "+" : ""}${c.toFixed(1)}</span>`;
    const wTxt = w == null ? "" : ` <span class="cnf-w">权重 ${(w * 100).toFixed(1)}%</span>`;
    const asof = f.asof ? ` · 数据截至 ${esc(f.asof)}` : "";
    const src = f.source_url && safeUrl(f.source_url)
      ? ` · <a href="${esc(f.source_url)}" target="_blank" rel="noopener noreferrer">${esc(f.source || "来源")}</a>`
      : f.source ? ` · ${esc(f.source)}` : "";
    return `<li class="fund-f"><span class="fund-fn">${name}</span>${scope}：${esc(f.signal_text || "")}${cTxt}${wTxt}` +
      `<div class="fund-fv">${esc(f.value_text || "")}${asof}${src}</div></li>`;
  }

  const GROUP_ORDER = ["policy", "valuation", "liquidity", "macro"];
  const GROUP_NAMES = { policy: "政策", valuation: "估值", liquidity: "流动性", macro: "宏观" };

  function groupsHtml(a) {
    const facs = Array.isArray(a && a.fund_factors) ? a.fund_factors : [];
    if (!facs.length) return "";
    const groups = Array.isArray(a.fund_groups) ? a.fund_groups : [];
    const gmap = {};
    for (const g of groups) gmap[g.key] = g;
    const parts = [];
    for (const key of GROUP_ORDER) {
      const fs = facs.filter((f) => f.group === key);
      if (!fs.length) continue;
      const g = gmap[key] || {};
      const nominal = num(g.weight_nominal);
      const eff = num(g.weight);
      const wTxt = nominal == null ? "" : `名义 ${(nominal * 100).toFixed(0)}%` +
        (eff != null && Math.abs(eff - nominal) > 0.0005 ? ` / 实际 ${(eff * 100).toFixed(1)}%` : "");
      const contrib = num(g.contribution);
      const cTxt = contrib == null ? "（无数据）" : `，贡献 ${contrib > 0 ? "+" : ""}${contrib.toFixed(1)}`;
      const emph = key === "policy" ? ` <span class="cnf-emph">权重最高</span>` : "";
      const scope = (g.scope || (key === "valuation" ? "index" : "market")) === "index" ? "按指数" : "全市场";
      parts.push(`<div class="cnf-group ${key === "policy" ? "policy" : ""}">` +
        `<div class="cnf-gh">${esc(g.name || GROUP_NAMES[key])}${emph} <span class="cnf-gw">${esc(wTxt)} · ${esc(scope)}${esc(cTxt)}</span></div>` +
        `<ul class="fund-list">${fs.map(factorLine).join("")}</ul></div>`);
    }
    const okN = facs.filter((f) => f.status === "ok").length;
    return `<details class="fund-details"><summary>基本面理由（${okN}/${facs.length} 项有数据，政策优先）</summary>${parts.join("")}</details>`;
  }

  function eventHtml(a) {
    const ew = a && a.policy_event_window;
    if (!ew || !ew.in_window) return "";
    const evs = Array.isArray(ew.events) ? ew.events : [];
    const links = [];
    for (const e of evs) {
      for (const s of (e.sources || [])) {
        if (safeUrl(s.url)) links.push(`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label || "来源")}</a>`);
      }
      if (links.length) break;
    }
    const basis = evs.length && evs[0].date_basis ? `<div class="cnf-ev-basis">${esc(evs[0].date_basis)}${links.length ? " · " + links.join(" · ") : ""}</div>` : "";
    return `<div class="fund-earn-warn cnf-event">⚠ ${esc(ew.text || "政策事件落在近月期权到期前")}</div>${basis}`;
  }

  function biasNote(a) {
    if (!a || !a.fund_adjust_cn) return "";
    const orig = a.option_bias_tech_cn && a.option_bias_tech_cn !== a.option_bias_cn
      ? `技术面原为「${esc(a.option_bias_tech_cn)}」：` : "";
    return `<div class="cnf-adjust">${orig}${esc(a.fund_adjust_cn)}</div>`;
  }

  function blockHtml(a) {
    const s = scores(a);
    const strip = `<div class="fund-strip">` +
      `<span class="fund-pill tech" title="50 + 原评分/2；越高越偏多">技术分 ${fmt(s.tech, 1)}</span>` +
      `<span class="fund-pill fund" title="政策35%/估值25%/流动性25%/宏观15%；越高越偏多">基本面分 ${s.fund == null ? (s.legacy || (a && a.fund_status === "not_computed") ? "未计算" : "无数据") : fmt(s.fund, 1)}</span>` +
      `<span class="fund-pill comb" title="0.6×技术 + 0.4×基本面；≥50 偏利于卖Put，<50 偏利于卖Call">综合分 ${s.combined == null ? "—" : fmt(s.combined, 1)}</span>` +
      `</div>`;
    const status = s.statusText ? `<div class="fund-status">${esc(s.statusText)}${a && a.fund_asof ? ` · 基本面取数 ${esc(a.fund_asof)}` : ""}</div>` : "";
    return `<div class="fund-block cnidx-fund">${strip}${status}${biasNote(a)}${eventHtml(a)}${groupsHtml(a)}</div>`;
  }

  const api = { esc: esc, scores: scores, isLegacy: isLegacy, factorLine: factorLine, groupsHtml: groupsHtml,
    eventHtml: eventHtml, biasNote: biasNote, blockHtml: blockHtml };
  root.cnIndexFundView = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
