/* 吸筹/诱多量价监控。研究参考，不能下单。 */
(function (root) {
  const NO_DATA = "无数据";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function isNum(v) {
    return typeof v === "number" && isFinite(v);
  }

  function pct(v, digits) {
    if (!isNum(v)) return NO_DATA;
    const n = (v * 100).toFixed(digits == null ? 1 : digits);
    return (v > 0 ? "+" : "") + n + "%";
  }

  function chgClass(v) {
    if (!isNum(v)) return "";
    if (v > 0) return "up";
    if (v < 0) return "down";
    return "";
  }

  function scoreTxt(v) {
    return isNum(v) ? String(v) : NO_DATA;
  }

  function labelClass(lab) {
    if (lab === "疑似吸筹") return "accum-lab accum-lab-in";
    if (lab === "疑似诱多") return "accum-lab accum-lab-trap";
    if (lab === "无数据") return "accum-lab accum-lab-miss";
    return "accum-lab accum-lab-mid";
  }

  function btCell(bt, side) {
    const row = (bt && bt[side]) || {};
    if (!row || (!row.n && row.n !== 0)) return NO_DATA;
    const n = row.n || 0;
    if (n <= 0) return `n=0 · 样本不足`;
    const a20 = isNum(row.ret_20_avg) ? `<span class="${chgClass(row.ret_20_avg)}">${esc(pct(row.ret_20_avg, 1))}</span>` : NO_DATA;
    const w20 = isNum(row.win_20) ? (row.win_20 * 100).toFixed(0) + "%" : NO_DATA;
    const a60 = isNum(row.ret_60_avg) ? `<span class="${chgClass(row.ret_60_avg)}">${esc(pct(row.ret_60_avg, 1))}</span>` : NO_DATA;
    const w60 = isNum(row.win_60) ? (row.win_60 * 100).toFixed(0) + "%" : NO_DATA;
    const warn = row.insufficient ? ` · ${esc(row.note || "样本不足")}` : "";
    return `n=${n} · 20日 ${a20} 胜率${esc(w20)} · 60日 ${a60} 胜率${esc(w60)}${warn}`;
  }

  function hitList(hits) {
    const rows = (hits || []);
    if (!rows.length) return `<p class="accum-miss">${NO_DATA}</p>`;
    return `<ul class="accum-hits">${rows.map((h) => {
      if (h.applicable === false) {
        return `<li class="accum-na"><span class="accum-flag">不适用</span>${esc(h.name)}：${esc(h.detail || "不适用")}</li>`;
      }
      const flag = h.hit ? "命中" : "未中";
      const cls = h.hit ? "accum-hit" : "accum-missed";
      return `<li class="${cls}"><span class="accum-flag">${flag}</span>${esc(h.name)}（${h.max_points}分）：${esc(h.detail || "")}</li>`;
    }).join("")}</ul>`;
  }

  function itemCard(it) {
    if (!it || it.status !== "ok") {
      const why = (it && (it.reason || it.label)) || NO_DATA;
      const ex = it && it.example ? `<span class="accum-example">示例</span>` : "";
      return `<article class="accum-card accum-card-miss">
        <header><h4>${esc((it && it.name) || "—")} ${ex}</h4>
        <span class="${labelClass("无数据")}">无数据</span></header>
        <p class="accum-miss">${esc(why)}</p>
      </article>`;
    }
    const pos = it.position || {};
    const rel = it.relative || {};
    const chg = it.chg_1d;
    const ex = it.example ? `<span class="accum-example">示例</span>` : "";
    const hold = it.holders || {};
    const holdTxt = hold.status === "not_applicable" ? "股东户数：不适用"
      : (hold.status === "ok"
        ? `股东户数：${hold.declining ? "近3期下降" : "未持续下降"}（截至 ${hold.asof || "—"}）`
        : `股东户数：${hold.reason || NO_DATA}`);
    const chip = it.chips || {};
    const chipTxt = chip.status === "ok"
      ? `筹码估算主峰 ${chip.peak_price != null ? chip.peak_price : "—"}（${chip.note || "估算，非真实持仓"}）`
      : `筹码分布：${chip.reason || NO_DATA}（${chip.note || "估算，非真实持仓"}）`;
    return `<article class="accum-card" data-id="${esc(it.id)}">
      <header>
        <h4>${esc(it.name)} <span class="accum-sym">${esc(it.symbol || it.code || "")}</span> ${ex}</h4>
        <span class="${labelClass(it.label)}">${esc(it.label)}</span>
      </header>
      <p class="accum-meta">截至 ${esc(it.asof || "—")} · 收盘 ${it.close != null ? esc(it.close) : NO_DATA}
        · 当日 <span class="${chgClass(chg)}">${esc(pct(chg, 2))}</span>
        · ${esc(pos.bucket || NO_DATA)}（250日分位 ${isNum(pos.range_pctile) ? (pos.range_pctile * 100).toFixed(0) + "%" : NO_DATA}，
        回撤 ${esc(pct(pos.drawdown_250, 1))}，距低 ${esc(pct(pos.rally_from_low, 1))}）</p>
      <div class="accum-scores">
        <div><span class="l">吸筹</span><strong>${esc(scoreTxt(it.accum_score))}</strong></div>
        <div><span class="l">诱多</span><strong>${esc(scoreTxt(it.trap_score))}</strong></div>
        <div><span class="l">20日相对${esc(rel.bench_name || "基准")}</span>
          <strong class="${chgClass(rel.rs_20)}">${esc(pct(rel.rs_20, 1))}</strong></div>
      </div>
      <p class="accum-chip">${esc(chipTxt)} · ${esc(holdTxt)}</p>
      <details class="accum-details">
        <summary>逐项命中与历史回看</summary>
        <h5>吸筹细则</h5>
        ${hitList((it.hits || {}).accum)}
        <h5>诱多细则</h5>
        ${hitList((it.hits || {}).trap)}
        <h5>历史回看（信号触发后，无前视）</h5>
        <p>疑似吸筹：${btCell(it.backtest, "accum")}</p>
        <p>疑似诱多：${btCell(it.backtest, "trap")}</p>
      </details>
    </article>`;
  }

  function summaryBlock(title, pack) {
    if (!pack) return "";
    return `<div class="accum-bt-sum"><h5>${esc(title)}</h5>
      <p>疑似吸筹：${btCell(pack, "accum")}</p>
      <p>疑似诱多：${btCell(pack, "trap")}</p></div>`;
  }

  function rulesHtml(rules) {
    rules = rules || {};
    return `<details class="accum-rules"><summary>规则与阈值（可计算、透明）</summary>
      <ul>
        <li>位置：收盘在近${rules.range_days || 250}日最高/最低区间的分位；
          ≤${((rules.low_pctile || 0.3) * 100).toFixed(0)}% 为低位，
          ≥${((rules.high_pctile || 0.7) * 100).toFixed(0)}% 为高位，其间为中位。
          距高点回撤 ≤${((rules.long_decline_dd || -0.2) * 100).toFixed(0)}% 视为长期下跌后。疑似吸筹必须低位，疑似诱多必须中高位。</li>
        <li>量能：20日均量/60日均量介于 ${rules.mild_vol_lo || 1.1}–${rules.mild_vol_hi || 1.8} 且当日量不超过 2 倍 20 日均量为温和放量；
          当日量 &gt; ${rules.sudden_vol || 2} 倍 20 日均量为突然放量；
          近${rules.consol_days || 20}日振幅（最高-最低）/均价 ≤${((rules.consol_amplitude || 0.08) * 100).toFixed(0)}% 且量比不高于 1 为缩量横盘。</li>
        <li>形态：上影/实体 ≥${rules.upper_shadow_body || 2} 且上影/振幅 ≥${((rules.upper_shadow_range || 0.5) * 100).toFixed(0)}% 为长上影；
          创${rules.pattern_lookback || 20}日新高后收在当日振幅下 40% 为冲高回落；
          价创 20 日新高而量低于 20 日均量为量价背离；
          近 3–${rules.weak_after_days || 5} 日曾突然放量且此后收盘无力。</li>
        <li>相对强度：近 20 日相对基准累计超额；近${rules.bench_down_lookback || 60}个交易日中，基准下跌日里标的不跌占比 ≥${((rules.hold_rate || 0.6) * 100).toFixed(0)}%（或平均超额为正且不跌占比≥50%）视为大盘跌它不跌。指数自身作基准时该项不适用。</li>
        <li>筹码：近${rules.chip_lookback || 60}日成交量按当日高低价均匀摊入价位（<b>估算，非真实持仓</b>）；主峰及左右各一档占总估算量 ≥${((rules.chip_single_share || 0.45) * 100).toFixed(0)}% 为单峰，主峰落在 250 日区间分位 ≤${((rules.chip_low_pctile || 0.3) * 100).toFixed(0)}% 为低位密集。</li>
        <li>股东户数：仅 A 股个股，东财数据中心季度数据，标注截止日期；最近连续 ${rules.holder_decline_n || 3} 个报告期下降为持续减少。指数与美股显示不适用。</li>
        <li>得分：适用项命中分之和 / 适用项满分 ×100。疑似吸筹：低位且吸筹分≥${rules.accum_label_min || 60} 且比诱多分至少高 ${rules.label_margin || 10}；疑似诱多对称。历史回看在信号首次触发后间隔至少 ${rules.signal_cooldown || 20} 日再计一次，前向 ${ (rules.fwd_days || [20, 60]).join("/") } 日收益与胜率；样本 &lt; ${rules.backtest_min_n || 8} 标明样本不足。</li>
      </ul>
    </details>`;
  }

  function render(data, market) {
    if (!data || (data.status !== "ok" && !(data.items && data.items.length))) {
      const err = (data && (data.error || data.reason || data.note)) || NO_DATA;
      const snap = data && data.from_snapshot
        ? `上次快照（${esc(data.snapshot_asof || (data.asof || "").slice(0, 10) || "—")}）`
        : "";
      return `<div class="accum-block"><p class="accum-miss">${esc(snap ? snap + "。" : "")}${esc(err)}</p></div>`;
    }
    const items = (data.items || []).filter((it) => it && it.market === market && it.kind === "index");
    const stocks = (data.items || []).filter((it) => it && it.market === market && it.kind === "stock");
    const snap = data.from_snapshot
      ? ` · 上次快照（${esc(data.snapshot_asof || (data.asof || "").slice(0, 10) || "—")}）`
      : "";
    const bench = market === "cn" ? "沪深300" : "标普500";
    const wl = data.watchlist || {};
    const wlNote = stocks.length
      ? (wl.note || "下列为观察名单中的代表股，不是推荐。A股基准沪深300，美股基准标普500。")
      : (wl.note || "尚未配置个股。可在仓库 data/accum_trap_watchlist.json 的 cn_stocks / us_stocks 加入 {symbol, name}，刷新后出现在本区。");
    const bt = (data.backtest_summary || {})[market] || (data.backtest_summary || {}).all_indices;
    return `<div class="accum-block">
      <p class="accum-disc">${esc(data.disclaimer || "研究参考，不能下单，不构成投资建议")}。基准为${esc(bench)}。缺项显示「无数据」，不编造。红＝上涨，绿＝下跌。${snap}</p>
      <p class="accum-theory">${esc(data.theory || "")}</p>
      ${rulesHtml(data.rules)}
      <div class="accum-grid">${items.map(itemCard).join("") || `<p class="accum-miss">${NO_DATA}</p>`}</div>
      <h4 class="accum-sub">个股自选</h4>
      <p class="accum-wl">${esc(wlNote)}</p>
      ${stocks.length ? `<div class="accum-grid">${stocks.map(itemCard).join("")}</div>` : ""}
      <h4 class="accum-sub">历史回看合计（本市场指数）</h4>
      <p class="accum-hint">用来观察这套规则触发后的平均收益与胜率，不是下单依据。样本不足会写明。</p>
      ${summaryBlock("指数合计", bt)}
    </div>`;
  }

  function mount(el, data, market) {
    if (!el) return;
    el.innerHTML = render(data, market || "cn");
  }

  root.AccumTrapView = {
    render: render,
    mount: mount,
    itemCard: itemCard,
    btCell: btCell,
    pct: pct,
    labelClass: labelClass,
  };
})(typeof window !== "undefined" ? window : globalThis);
