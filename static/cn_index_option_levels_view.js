/* A股指数「期权倾向」→ 参考价位（合约 / 行权价 / 权利金 / 盈亏平衡 / 止损止盈 / 保护腿）。
 * 浏览器: window.cnIndexOptionLevelsView；Node 测试: module.exports。
 * 数据来自后端 advice.option_levels（只读期权链快照）；缺数据一律「无数据」，前端不补算、不编造。
 * 不显示期权T型报价表。红=涨 绿=跌（距现价：上方 .up，下方 .down）。
 */
(function (root) {
  "use strict";

  const NO_DATA = "无数据";
  const LABEL = "参考价位，不构成下单建议";

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

  function f(v, n) {
    const x = num(v);
    if (x == null) return NO_DATA;
    return x.toFixed(n == null ? 1 : n);
  }

  function k(v) {
    const x = num(v);
    if (x == null) return NO_DATA;
    return Number.isInteger(x) ? String(x) : x.toFixed(2);
  }

  function pct(v) {
    const x = num(v);
    if (x == null) return `<span class="nodata">${NO_DATA}</span>`;
    const c = x > 0 ? "up" : x < 0 ? "down" : "flat";
    return `<span class="${c}">${x > 0 ? "+" : ""}${x.toFixed(2)}%</span>`;
  }

  function legHtml(leg) {
    if (!leg) return "";
    const side = esc(leg.side_cn || "");
    if (leg.status !== "ok") {
      return `<div class="optlv-leg miss"><span class="optlv-side">${side}</span>${esc(leg.reason_cn || NO_DATA)}</div>`;
    }
    const delta = leg.delta == null ? NO_DATA : f(leg.delta, 2);
    const bidask = leg.bid != null && leg.ask != null ? `（买${f(leg.bid)} / 卖${f(leg.ask)}）` : "";
    const thin = leg.thin_cn ? `<div class="optlv-warn">${esc(leg.thin_cn)}</div>` : "";
    const sp = leg.spread;
    const spread = sp
      ? `<div class="optlv-row"><span class="l">保护腿</span>买 <code>${esc(sp.buy_code)}</code> 行权 ${esc(k(sp.buy_strike))} @ ${esc(f(sp.buy_premium))}；净收 ${esc(f(sp.net_credit))} 点，最大亏损 ${esc(f(sp.max_loss_pts))} 点（约 ${esc(f(sp.max_loss_yuan, 0))} 元/组），盈亏平衡 ${esc(k(sp.breakeven))}${sp.stop_cn ? "；" + esc(sp.stop_cn) : ""}</div>`
      : `<div class="optlv-row"><span class="l">保护腿</span>${NO_DATA}</div>`;
    return `<div class="optlv-leg">
      <div class="optlv-row"><span class="optlv-side">${side}</span><code>${esc(leg.code || NO_DATA)}</code> 行权 <strong>${esc(k(leg.strike))}</strong> · 距现价 ${pct(leg.dist_pct)} · Δ≈${esc(delta)}</div>
      <div class="optlv-row"><span class="l">参考权利金</span>${esc(f(leg.premium))} 点${esc(bidask)} · ${esc(leg.premium_basis || "")} · 约 ${esc(f(leg.premium_yuan, 0))} 元/张</div>
      <div class="optlv-row"><span class="l">盈亏平衡</span>${esc(k(leg.breakeven))}（距现价 ${pct(leg.be_dist_pct)}）</div>
      <div class="optlv-row"><span class="l">止损</span>${esc(leg.stop_cn || NO_DATA)}</div>
      <div class="optlv-row"><span class="l">止盈</span>${esc(leg.take_profit_cn || NO_DATA)}</div>
      ${spread}${thin}
    </div>`;
  }

  function blockHtml(advice) {
    const o = advice && advice.option_levels;
    const head = `<div class="optlv-h">期权参考价位 <em>${LABEL}</em></div>`;
    if (!o) return `<div class="optlv-card">${head}<div class="optlv-miss">${NO_DATA}</div></div>`;
    const etf = o.etf_note_cn ? `<div class="optlv-src">${esc(o.etf_note_cn)}</div>` : "";
    if (o.status === "nodata" || !o.legs) {
      return `<div class="optlv-card">${head}<div class="optlv-miss">${esc(o.reason_cn || NO_DATA)}</div>${etf}</div>`;
    }
    const meta = `<div class="optlv-meta">${esc(o.product || "")} ${esc(o.month || "")} · 到期 ${esc(o.expire || NO_DATA)}（剩 ${esc(o.dte == null ? NO_DATA : o.dte)} 天）· 标的 ${esc(f(o.spot, 2))} · ATM IV ${esc(f(o.atm_iv_pct))}%</div>`;
    const structure = o.structure_cn ? `<div class="optlv-struct">${esc(o.structure_cn)}</div>` : "";
    const order = ["put", "call"].filter((s) => o.legs[s]);
    const legs = order.map((s) => legHtml(o.legs[s])).join("");
    const c = o.condor;
    const condor = c
      ? `<div class="optlv-leg condor"><div class="optlv-row"><span class="optlv-side">铁秃鹰合计</span>净收 ${esc(f(c.net_credit))} 点（约 ${esc(f(c.net_credit_yuan, 0))} 元）· 最大亏损 ${esc(f(c.max_loss_pts))} 点（约 ${esc(f(c.max_loss_yuan, 0))} 元）· 盈亏区间 ${esc(k(c.breakeven_low))}–${esc(k(c.breakeven_high))}</div>
         <div class="optlv-row"><span class="l">止损</span>${esc(c.stop_cn || "")}</div><div class="optlv-row"><span class="l">止盈</span>${esc(c.take_profit_cn || "")}</div></div>`
      : "";
    const src = `<div class="optlv-src">报价时间 ${esc(o.asof || NO_DATA)} · ${esc(o.source_cn || "")}</div>`;
    const rules = (o.rules_cn || []).length
      ? `<details class="optlv-rules"><summary>选档规则</summary><ul>${o.rules_cn.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></details>`
      : "";
    return `<div class="optlv-card">${head}${meta}${structure}${legs}${condor}${src}${etf}${rules}</div>`;
  }

  const api = { blockHtml, legHtml, pct, f, k, NO_DATA, LABEL };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.cnIndexOptionLevelsView = api;
})(typeof window !== "undefined" ? window : null);
