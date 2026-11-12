/* A股指数资金进出：主力净流入 / ETF 申赎（估算）/ 两融融资余额 / 北向（只展示，不计入评分）。
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

  /** Per-index block inside an A股指数 card. */
  function cardHtml(bundle, indexId) {
    const row = bundle && bundle.indices ? bundle.indices[indexId] : null;
    if (!row) {
      return `<div class="cnflow-card"><div class="cnflow-h">资金进出（亿元）</div><div class="cnflow-miss">${NO_DATA}（快照未包含资金流数据）</div></div>`;
    }
    const m = row.main || {};
    const e = row.etf || {};
    const mainLine = ok(m)
      ? `<div class="cnflow-row"><span class="l">主力净流入</span>今日 ${cell(m.net_1d)} · 5日 ${cell(m.net_5d)} · 20日 ${cell(m.net_20d)}</div>` +
        `<div class="cnflow-src">数据 ${esc(m.asof || "—")} · ${srcLink(m)}</div>`
      : `<div class="cnflow-row"><span class="l">主力净流入</span>${NO_DATA}${reasonTxt(m)}</div>`;
    const etfLine = ok(e)
      ? `<div class="cnflow-row"><span class="l">ETF净申赎<em>估算</em></span>1日 ${cell(e.net_1d)} · 5日 ${cell(e.net_5d)} · 20日 ${cell(e.net_20d)}</div>` +
        `<div class="cnflow-src">数据 ${esc(e.asof || "—")} · ${esc(e.coverage || "")} · ${(e.items || []).filter(ok).map((i) => esc(i.code)).join("/")} · ${srcLink(e)}</div>`
      : `<div class="cnflow-row"><span class="l">ETF净申赎</span>${NO_DATA}${reasonTxt(e)}</div>`;
    return `<div class="cnflow-card"><div class="cnflow-h">资金进出（亿元，红=流入 绿=流出，仅展示不计分）</div>${mainLine}${etfLine}</div>`;
  }

  function marginRow(label, o) {
    if (!ok(o)) {
      return `<tr><td>${esc(label)}</td><td class="num" colspan="4">${NO_DATA}${reasonTxt(o)}</td><td>${srcLink(o)}</td></tr>`;
    }
    return `<tr><td>${esc(label)}</td><td class="num">${o.balance == null ? NO_DATA : esc(Number(o.balance).toFixed(1))}</td>` +
      `<td class="num">${cell(o.chg_1d, 1)}</td><td class="num">${cell(o.chg_5d, 1)}</td><td class="num">${cell(o.chg_20d, 1)}</td>` +
      `<td>${esc(o.asof || "—")}${o.source ? " · " + srcLink(o) : ""}</td></tr>`;
  }

  /** Market summary + all-index table for the 资金进出 panel. */
  function summaryHtml(bundle) {
    if (!bundle || !bundle.indices) {
      return `<div class="empty-card">资金进出：${NO_DATA}（快照尚未包含，后台更新后显示）</div>`;
    }
    const mk = bundle.market || {};
    const mm = mk.main || {};
    const nb = mk.northbound || {};
    const mainTxt = ok(mm)
      ? `今日 ${cell(mm.net_1d)} · 5日 ${cell(mm.net_5d)} · 20日 ${cell(mm.net_20d)} 亿元 <span class="cnflow-src">数据 ${esc(mm.asof || "—")} · ${srcLink(mm)}</span>`
      : `${NO_DATA}${reasonTxt(mm)}`;
    const top = `<div class="cnflow-market">` +
      `<div><strong>沪深两市主力净流入</strong>：${mainTxt}</div>` +
      `<div><strong>北向资金净买额</strong>：${NO_DATA}${nb.reason ? `（${esc(nb.reason)}）` : ""}</div>` +
      `</div>`;
    const margin = `<table class="put-table cnflow-table"><thead><tr><th>两融·融资余额</th><th class="num">余额(亿元)</th>` +
      `<th class="num">1日变化</th><th class="num">5日变化</th><th class="num">20日变化</th><th>数据日期 · 来源</th></tr></thead><tbody>` +
      marginRow("上交所", mk.margin_sh) + marginRow("深交所", mk.margin_sz) + marginRow("沪深合计", mk.margin_total) +
      `</tbody></table>`;
    const order = Array.isArray(bundle.order) && bundle.order.length ? bundle.order : Object.keys(bundle.indices);
    const rows = order.map((id) => {
      const r = bundle.indices[id];
      if (!r) return "";
      const m = r.main || {};
      const e = r.etf || {};
      const d = [ok(m) ? m.asof : null, ok(e) ? e.asof : null].filter(Boolean);
      return `<tr><td><strong>${esc(r.name || id)}</strong>${r.tracked ? "" : ' <span class="tag-pill">总览</span>'}</td>` +
        `<td class="num">${ok(m) ? cell(m.net_1d) : NO_DATA}</td><td class="num">${ok(m) ? cell(m.net_5d) : NO_DATA}</td><td class="num">${ok(m) ? cell(m.net_20d) : NO_DATA}</td>` +
        `<td class="num">${ok(e) ? cell(e.net_1d) : NO_DATA}</td><td class="num">${ok(e) ? cell(e.net_5d) : NO_DATA}</td><td class="num">${ok(e) ? cell(e.net_20d) : NO_DATA}</td>` +
        `<td>${d.length ? esc(Array.from(new Set(d)).join(" / ")) : NO_DATA}</td></tr>`;
    }).join("");
    const table = `<table class="put-table cnflow-table"><thead><tr><th>指数</th>` +
      `<th class="num">主力今日</th><th class="num">主力5日</th><th class="num">主力20日</th>` +
      `<th class="num">ETF申赎1日</th><th class="num">ETF申赎5日</th><th class="num">ETF申赎20日</th><th>数据日期</th></tr></thead>` +
      `<tbody>${rows}</tbody></table>`;
    const notes = (bundle.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
    const meta = `<div class="cnflow-src">单位：亿元 · 取数 ${esc(bundle.fetched_at || "—")}</div>`;
    return `${top}<div class="table-wrap" tabindex="0" role="region" aria-label="指数资金进出表，可左右滚动">${table}</div>` +
      `<div class="table-wrap" tabindex="0" role="region" aria-label="两融融资余额表，可左右滚动">${margin}</div>${meta}` +
      (notes ? `<ul class="cnflow-notes">${notes}</ul>` : "");
  }

  const api = { esc: esc, cls: cls, signed: signed, cardHtml: cardHtml, summaryHtml: summaryHtml, NO_DATA: NO_DATA };
  root.cnIndexFlowView = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
