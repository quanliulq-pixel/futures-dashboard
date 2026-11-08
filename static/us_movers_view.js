/* 美股涨跌榜按类别视图（与 us_movers.snapshot_view 同逻辑）。不修改输入。
 * 浏览器: window.usMoversView；Node 测试: module.exports。 */
(function (root) {
  "use strict";
  var KINDS = { all: "公司+现货ETF", stock: "公司", etf: "现货ETF", lev: "多倍ETF" };
  var own = Object.prototype.hasOwnProperty;

  function rows(lst) {
    return Array.isArray(lst) ? lst.filter(function (r) { return r && typeof r === "object"; })
      .map(function (r) { return Object.assign({}, r); }) : [];
  }

  function usMoversView(snap, kind, limit) {
    if (!snap || typeof snap !== "object") return snap;
    var k = typeof kind === "string" ? kind.toLowerCase() : "all";
    if (!own.call(KINDS, k)) k = "all";
    var lim = parseInt(limit, 10);
    if (!isFinite(lim)) lim = 20;
    lim = Math.max(5, Math.min(lim || 20, 50));

    var out = {};
    Object.keys(snap).forEach(function (key) { if (key !== "kinds" && key !== "periods") out[key] = snap[key]; });
    out.lev_gainers = rows(snap.lev_gainers);
    out.lev_losers = rows(snap.lev_losers);
    var notes = [snap.note || ""];
    var partial = false;
    var kinds = snap.kinds && typeof snap.kinds === "object" ? snap.kinds : {};
    var srcKind = snap.kind || "all";
    var g, l, uni;

    if (srcKind === k) {
      g = rows(snap.gainers); l = rows(snap.losers); uni = snap.universe;
      partial = snap.partial === true; // keep an upstream partial flag
    } else if (own.call(kinds, k) && kinds[k] && typeof kinds[k] === "object") {
      var sub = kinds[k];
      g = rows(sub.gainers); l = rows(sub.losers); uni = sub.universe;
      partial = sub.partial === true;
      if (sub.note) notes.push(String(sub.note));
      if (sub.asof) out.asof = sub.asof;
    } else if (k === "lev") {
      g = rows(snap.lev_gainers); l = rows(snap.lev_losers); uni = snap.lev_universe;
    } else if (srcKind === "all" && (k === "stock" || k === "etf")) {
      g = rows(snap.gainers).filter(function (r) { return r.kind === k; });
      l = rows(snap.losers).filter(function (r) { return r.kind === k; });
      uni = null;
      partial = true;
      var nSrc = (snap.gainers || []).length;
      notes.push("快照只含全部榜前 " + nSrc + " 名，从中筛出" + KINDS[k] + " 涨" + g.length + "/跌" + l.length + " 行，非完整" + KINDS[k] + "排名");
    } else {
      g = []; l = []; uni = null; partial = true;
      notes.push("快照无该类别数据");
    }
    // Completeness: each side must hold min(limit, known universe) rows; unknown universe is never complete.
    if (!partial) {
      var need = typeof uni === "number" && isFinite(uni) && uni >= 0 && Math.floor(uni) === uni ? Math.min(lim, uni) : null;
      if (need === null || g.length < need || l.length < need) {
        partial = true;
        notes.push("快照仅保存 涨" + g.length + "/跌" + l.length + " 行（请求 " + lim + "），不是完整排名");
      }
    }
    out.kind = k;
    out.kind_label = KINDS[k];
    out.limit = lim;
    out.gainers = g.slice(0, lim);
    out.losers = l.slice(0, lim);
    out.universe = uni === undefined ? null : uni;
    out.partial = partial;
    out.snapshot_scope = partial ? "partial" : "complete";
    out.note = notes.filter(Boolean).join(" · ");
    return out;
  }

  root.usMoversView = usMoversView;
  if (typeof module !== "undefined" && module.exports) module.exports = { usMoversView: usMoversView };
})(typeof window !== "undefined" ? window : this);
