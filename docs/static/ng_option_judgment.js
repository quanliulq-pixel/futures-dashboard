/* NG 卖Put/卖Call 方向研究提示：纯派生自已有 NG 看板字段（advice.bias、confidence、
 * events、source、errors），不新增评分、不联网、不生成行权价/收益/仓位，
 * 也不代表期权条件已核验或可开仓。
 * 浏览器: window.ngOptionJudgment；Node 测试: module.exports。 */
(function (root) {
  "use strict";

  var CORE_SOURCES = ["price", "storage", "weather"];
  var PUT_BIASES = { long: 1, lean_long: 1 };
  var CALL_BIASES = { short: 1, lean_short: 1 };
  var NEUTRAL_BIASES = { neutral: 1 };

  function isObj(x) {
    return x !== null && typeof x === "object" && !Array.isArray(x);
  }

  function flaggedStale(o) {
    return o.stale === true || o.seed === true ||
      (typeof o.status === "string" && o.status !== "ok") ||
      (typeof o.source === "string" && o.source !== "live");
  }

  // 只有后端明确标记 source === "live"（本进程实时重算）才视为可确认的新鲜数据；
  // 缺失/未知/seed/stale/cache/snapshot 一律无法确认。
  function freshness(data) {
    if (!isObj(data)) return { ok: false, why: "无数据" };
    if (data.source !== "live") {
      var src = typeof data.source === "string" && data.source ? data.source : "未标注";
      return { ok: false, why: "数据来源为 " + src + "，无法确认是最新重算" };
    }
    if (data.stale === true) return { ok: false, why: "数据已标记为过期" };
    var errs = isObj(data.errors) ? data.errors : null;
    if (!errs) return { ok: false, why: "缺少数据源错误状态，无法确认" };
    var bad = CORE_SOURCES.filter(function (k) {
      var o = data[k];
      return errs[k] || !isObj(o) || Object.keys(o).length === 0 || flaggedStale(o);
    });
    if (bad.length) return { ok: false, why: "核心数据缺失、出错或非最新（" + bad.join("/") + "）" };
    return { ok: true, why: "" };
  }

  // 复用 _advice 的禁开仓/降仓条件：EIA 24 小时内、置信度 < 45；无法判定的一律视为阻断
  function blockers(data) {
    var out = [];
    var conf = data.confidence;
    if (typeof conf !== "number" || !isFinite(conf)) {
      out.push("置信度缺失或无效");
    } else if (conf < 45) {
      out.push("模型置信度 " + Math.round(conf) + "/100 偏低");
    }
    if (!Array.isArray(data.events)) {
      out.push("事件日历缺失，无法排除 EIA 发布窗口");
    } else {
      data.events.forEach(function (e) {
        if (!isObj(e)) return;
        var isEia = String(e.name || "").indexOf("EIA") >= 0;
        if (!isEia) return;
        var h = e.in_hours;
        if (typeof h !== "number" || !isFinite(h)) {
          out.push("EIA 事件时间无效，无法排除发布窗口");
        } else if (h >= 0 && h <= 24) {
          out.push(String(e.name) + "将在24小时内发布，发布前不新开仓");
        }
      });
    }
    return out;
  }

  function ngOptionJudgment(data) {
    var adv = isObj(data) && isObj(data.advice) ? data.advice : {};
    var bias = typeof adv.bias === "string" ? adv.bias : "";
    var dir = Object.prototype.hasOwnProperty.call(PUT_BIASES, bias) ? "put" : Object.prototype.hasOwnProperty.call(CALL_BIASES, bias) ? "call" : Object.prototype.hasOwnProperty.call(NEUTRAL_BIASES, bias) ? "none" : "unknown";
    var fr = freshness(data);
    var blk = isObj(data) ? blockers(data) : [];
    var cn = typeof adv.bias_cn === "string" ? adv.bias_cn : "";

    var res = {
      state: "no_sell",
      label: "暂不卖",
      reference_only: !fr.ok,
      direction_hint: dir,
      put: { ok: false, text: "" },
      call: { ok: false, text: "" },
      notes: [],
    };

    var canAct = fr.ok && !blk.length && (dir === "put" || dir === "call");
    if (canAct) {
      res.state = dir === "put" ? "sell_put" : "sell_call";
      res.label = dir === "put" ? "偏向卖Put（方向研究）" : "偏向卖Call（方向研究）";
    }
    res.put.ok = res.state === "sell_put";
    res.call.ok = res.state === "sell_call";

    var stopReason = !fr.ok ? "数据未确认为最新" : blk.length ? "风险条件未解除" : "";
    function side(which) {
      var favored = dir === which;
      var opposite = (which === "put" && dir === "call") || (which === "call" && dir === "put");
      if (dir === "unknown") return "暂不判断：模型方向缺失或无效";
      if (dir === "none") {
        return "暂不卖：模型" + (cn || "观望/区间") + "，无方向优势" +
          (which === "call" ? "；区间不自动双卖或裸卖Call" : "");
      }
      if (opposite) return "暂不卖：模型" + cn + "，与该方向相反";
      if (favored && canAct) return "方向偏向（尚未核验期权条件，非开仓建议）：模型" + cn;
      return "暂不卖：" + stopReason + "；次级研究提示：模型" + cn + "方向与此一致";
    }
    res.put.text = side("put");
    res.call.text = side("call");

    if (!fr.ok) res.notes.push(fr.why + "，仅作方向研究参考，等待新数据");
    blk.forEach(function (b) { res.notes.push(b); });
    res.notes.push("以上不是可交易信号：未核验期权链 IV、权利金、买卖价差、流动性与到期日，不给行权价、收益与仓位");
    res.notes.push("卖Put须现金担保、能承受接货；卖Call仅限备兑或定义风险价差");
    res.notes.push("UNG 与 NG 期货相关但标的不同，不能用 NG 价格当 UNG 行权价");
    return res;
  }

  root.ngOptionJudgment = ngOptionJudgment;
  if (typeof module !== "undefined" && module.exports) module.exports = { ngOptionJudgment: ngOptionJudgment };
})(typeof window !== "undefined" ? window : this);
