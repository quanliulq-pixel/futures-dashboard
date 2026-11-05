# -*- coding: utf-8 -*-
"""
Typical production-cut lags and latest public supply-cut snapshots.

lag_weeks: time from sustained cash-cost loss to visible supply drop
cut_type: how supply actually shrinks
asof / status: research snapshot (not live API); update periodically
"""

from __future__ import annotations

# Generic by sector if a symbol has no dedicated row
SECTOR_CYCLE = {
    "化工": {
        "lag": "2–8 周",
        "to_price": "减产后 1–3 个月",
        "cut": "降负荷、检修、停车；头部低成本厂常不减",
        "watch": "周度开工率、厂库",
    },
    "黑色": {
        "lag": "2–6 周",
        "to_price": "铁水回落 4–8 周",
        "cut": "高炉检修、电炉停产；螺纹产量先于铁水掉",
        "watch": "螺纹产能利用率、铁水日产、钢厂盈利率",
    },
    "有色": {
        "lag": "1–6 个月",
        "to_price": "冶炼减产 1–2 季",
        "cut": "冶炼先减、矿山更慢；电解槽停槽成本高",
        "watch": "开工、TC/RC、仓单",
    },
    "能源": {
        "lag": "4–16 周",
        "to_price": "库存拐头后 1–3 个月",
        "cut": "气：停钻 8–16 周见产量；油品：炼厂降量更快",
        "watch": "钻机、产量、库存",
    },
    "农产品": {
        "lag": "1 个播种季（6–12 个月）",
        "to_price": "下一作物年度",
        "cut": "面积下调；在地作物几乎不能减产",
        "watch": "种植意向、库存、进口",
    },
    "畜牧": {
        "lag": "能繁去化 10–12 个月传到出栏",
        "to_price": "去化确认后 2–4 季",
        "cut": "先淘母猪，出栏滞后约一年",
        "watch": "能繁母猪、新生仔猪、屠宰量",
    },
    "贵金属": {
        "lag": "6–18 个月",
        "to_price": "慢",
        "cut": "矿山关停极慢；价格更多看货币/实际利率",
        "watch": "AISC、资本开支，而非周度开工",
    },
    "软商品": {
        "lag": "1 个生长季",
        "to_price": "下一榨季/产季",
        "cut": "面积与单产；天气常比成本更关键",
        "watch": "主产国产量预估、库存消费比",
    },
    "林产品": {
        "lag": "4–12 周",
        "to_price": "库存去化后",
        "cut": "锯厂减班、停产",
        "watch": "开工、库存、新屋开工",
    },
    "轻工": {
        "lag": "4–8 周",
        "to_price": "1–3 个月",
        "cut": "纸厂降负、停机",
        "watch": "开工、纸浆库存",
    },
}

# Symbol-specific: cycle + latest observed cut status (as of ~2026-08)
# status: 已减产 / 刚开始 / 未减产 / 混杂 / 数据不足
BY_ID = {
    "CN:FG0": {
        "name": "玻璃",
        "lag": "2–6 周（冷修）",
        "to_price": "日熔量连续下滑 4–8 周后",
        "cut": "产线冷修；多为阶段性停产，不是永久退出",
        "status": "已减产（未扭转过剩）",
        "evidence": "2026-08 前后：浮法在产日熔约 14.28 万吨、开工约 66.6%，近三年偏低，环比回落；煤制气仍有韧性。地产竣工 1–6 月同比约 −23.7%，需求弱。",
        "asof": "2026-08",
        "source": "期货公司8月玻璃供需点评（瑞达等）",
    },
    "CN:SA0": {
        "name": "纯碱",
        "lag": "3–8 周（高成本厂降负）",
        "to_price": "产量趋势性下降 + 厂库去化",
        "cut": "氨碱/高成本联碱被动降负；天然碱与一体化厂往往不减",
        "status": "未趋势性减产",
        "evidence": "8月：周产量约 76.85 万吨、环比仍升；夏季检修减量弱。仅高成本氨碱/联碱因亏损降负，头部维持高开工。出口占比不足约 8%，难扭转过剩。",
        "asof": "2026-08",
        "source": "瑞达等8月纯碱点评；东财8月5日投研摘要",
    },
    "CN:V0": {
        "name": "PVC",
        "lag": "3–8 周",
        "to_price": "开工回落 1–2 个月",
        "cut": "电石法亏损后降负、检修",
        "status": "数据不足（疑被动降负）",
        "evidence": "破现金成本，电石法有检修弹性；未见2026-08统一的全国开工已趋势性大幅下降的权威周报。",
        "asof": "2026-08",
        "source": "成本位置+行业惯例",
    },
    "CN:SH0": {
        "name": "烧碱",
        "lag": "2–6 周",
        "to_price": "氯碱降负后 1–2 个月",
        "cut": "离子膜装置降负；受液氯平衡制约，不一定跟烧碱价走",
        "status": "数据不足",
        "evidence": "价格贴近现金成本；氯碱开工还受液氯/PVC 利润牵制，不能单看烧碱价判断减产。",
        "asof": "2026-08",
        "source": "氯碱联动逻辑",
    },
    "CN:RB0": {
        "name": "螺纹钢",
        "lag": "2–4 周（螺纹产量）/ 4–8 周（铁水）",
        "to_price": "产量与库存同时降 4–8 周",
        "cut": "电炉先停，高炉后检修；建材淡季产量可先掉",
        "status": "产量已降，库存仍增",
        "evidence": "约2026-08初：螺纹周产下调，产能利用率约 43.9%；但表观需求平、库存继续增加。8月3–7日高炉开工/铁水小幅回升，钢厂盈利率走低，长短流程检修增加。",
        "asof": "2026-08",
        "source": "瑞达螺纹点评；铁水/高炉周度报道",
    },
    "CN:HC0": {
        "name": "热卷",
        "lag": "与螺纹类似，略慢",
        "to_price": "铁水回落 + 板材去库",
        "cut": "板材产线检修；出口可对冲内需",
        "status": "弱减产/混杂",
        "evidence": "贴完全成本；热卷通常强于螺纹。未见明确的板材趋势性大幅减产，更多是淡季产量波动。",
        "asof": "2026-08",
        "source": "黑色产业链周度",
    },
    "CN:UR0": {
        "name": "尿素",
        "lag": "1–4 周（停车）",
        "to_price": "需出口或农需跟上，否则减产也难涨",
        "cut": "煤头装置故障/检修，高开工时减产意愿弱",
        "status": "未减产（高开工）",
        "evidence": "截至2026-07-30：当周产量约 150.8 万吨，产能利用率约 90.3% 仍升。8月停车与复产并存，淡季农需弱，累库放缓主要靠出口预期。",
        "asof": "2026-08",
        "source": "广发/瑞达尿素开工库存",
    },
    "CN:LH0": {
        "name": "生猪",
        "lag": "能繁去化后 10–12 个月出栏才降",
        "to_price": "新生仔猪同比下降后约 6–10 个月",
        "cut": "淘母猪；出栏体重上升会抵消部分去化",
        "status": "能繁温和去化，出栏尚未收缩",
        "evidence": "2026Q1 末能繁约 3904 万头，同比 −3.3%，环比 −1.5%；政策目标曾指向 3900/或更低。一季度出栏、猪肉产量仍同比增。农业农村部称至2026-04 能繁已连续约 9 个月下降，3 月新生仔猪 17 个月来首次同比降——对应供给收缩最早约 2026 下半年至 2027 初。",
        "asof": "2026-04/08",
        "source": "国家统计局/农业农村部能繁数据",
    },
    "CN:SI0": {
        "name": "工业硅",
        "lag": "2–8 周（电炉开停）",
        "to_price": "开工持续低于同期 + 去库",
        "cut": "高电价区先停；丰水期西南仍可能复产",
        "status": "开工偏低（供给收缩进行中）",
        "evidence": "硅业分会 2026-08：供给收缩强化，开工显著低于历史同期、小幅再降。价格在成本线附近盘整。丰水期西南有复产扰动。",
        "asof": "2026-08",
        "source": "硅业分会/期货日报类报道",
    },
    "CN:PS0": {
        "name": "多晶硅",
        "lag": "4–12 周",
        "to_price": "行业自律减产持续一个季度以上",
        "cut": "基地减产、爬产不及预期；库存仍可能高",
        "status": "部分减产，产量仍可能环比增",
        "evidence": "2026-06/07：新疆、青海部分基地减产，月产量增量不及预期；但环比未必下降。行业深度亏损，减产是“少增产”多于“大幅关停”。",
        "asof": "2026-07",
        "source": "多晶硅月度产量点评",
    },
    "CN:LC0": {
        "name": "碳酸锂",
        "lag": "盐湖季节性 1–2 季；矿石厂 4–8 周",
        "to_price": "矿石端持续亏损后 1–2 季",
        "cut": "外购矿高成本厂先停；盐湖现金成本低、减产慢",
        "status": "价格高于完全成本，非减产驱动",
        "evidence": "现价远离成本带下方；供给仍受盐湖与矿石投放影响，不是“破成本减产”叙事。",
        "asof": "2026-08",
        "source": "盘面 vs 成本锚",
    },
    "US:NG=F": {
        "name": "天然气NG",
        "lag": "停钻后 8–16 周产量放缓",
        "to_price": "库存相对五年均值回落之后",
        "cut": "气钻机下降；伴生气（石油钻机）不受气价约束",
        "status": "未减产（产量仍高、库存偏多）",
        "evidence": "EIA：2026 干气产量仍望创纪录（约 111 Bcf/d 量级）；10 月末库存或处高位。气钻机 2026-08-07 约 124 座，周环比 −3，同比大致持平。现价贴完全成本但远高于现金成本，停钻弹性有限。",
        "asof": "2026-08",
        "source": "EIA STEO / Baker Hughes 气钻机",
    },
    "US:HE=F": {
        "name": "瘦肉猪",
        "lag": "存栏调整 2–4 季",
        "to_price": "能繁/存栏报告确认后",
        "cut": "利润决定补栏；饲料便宜会拖慢去化",
        "status": "非深度破成本，去化未确认",
        "evidence": "期价贴近完全成本中枢、高于现金成本；饲料偏低支撑利润，未必去产能。",
        "asof": "2026-08",
        "source": "成本位置+养猪周期",
    },
    "US:OJ=F": {
        "name": "橙汁",
        "lag": "下一产季（病害减产是慢变量）",
        "to_price": "主产区产量预估下调时",
        "cut": "黄龙病导致产能永久性下降，不是主动关厂",
        "status": "结构性减产（病害），非成本关停",
        "evidence": "佛州产能因黄龙病长期下滑；价格低于完全成本中枢、高于现金成本。供给故事是病害而非“跌破成本才减产”。",
        "asof": "2026-08",
        "source": "FCOJ/佛州减产背景",
    },
    "CN:SR0": {
        "name": "白糖",
        "lag": "下一榨季（约 12 个月）",
        "to_price": "种植面积/收购价下调后的下一季",
        "cut": "甘蔗收购价刚性，本榨季几乎不能减",
        "status": "本季难减产",
        "evidence": "贴完全成本；减产要看下榨季面积与糖醇比，不是周度开工。",
        "asof": "2026-08",
        "source": "榨季规律",
    },
    "CN:C0": {
        "name": "玉米",
        "lag": "下一播种季",
        "to_price": "新作面积报告",
        "cut": "在地作物无法减产",
        "status": "看面积，不看周度减产",
        "evidence": "贴完全成本；供给调节窗口是春播/临储/进口，不是工厂开工。",
        "asof": "2026-08",
        "source": "种植周期",
    },
}


def _live_overlay(instrument_id: str) -> str | None:
    """Append latest weekly snapshot line if we have one."""
    try:
        from supply_fetch import load_latest

        data = load_latest()
        if not data:
            return None
        bits = []
        for it in data.get("items") or []:
            if it.get("id") != instrument_id:
                continue
            val = it.get("value")
            sig = it.get("signal") or ""
            asof = it.get("asof") or ""
            metric = it.get("metric") or ""
            wow = it.get("wow")
            wow_s = f"，周变化 {wow:+.2g}" if isinstance(wow, (int, float)) else ""
            bits.append(f"{metric} {val}{wow_s}（{sig}，{asof}）")
        if not bits:
            return None
        fetched = data.get("fetched_at", "")[:10]
        return "周更快照：" + "；".join(bits) + (f"。抓取 {fetched}" if fetched else "")
    except Exception:
        return None


def get_cycle(instrument_id: str, sector: str | None = None) -> dict:
    row = BY_ID.get(instrument_id)
    live = _live_overlay(instrument_id)
    if row:
        evidence = row.get("evidence") or ""
        if live:
            evidence = (evidence + " " if evidence else "") + live
        return {
            "lag": row["lag"],
            "to_price": row["to_price"],
            "cut": row["cut"],
            "status": row.get("status"),
            "evidence": evidence,
            "asof": row.get("asof"),
            "source": row.get("source"),
            "specific": True,
        }
    gen = SECTOR_CYCLE.get(sector or "", {})
    return {
        "lag": gen.get("lag", "视工艺，通常数周到一个生产季"),
        "to_price": gen.get("to_price", "库存拐头之后"),
        "cut": gen.get("cut", "高成本产能先退出"),
        "status": None,
        "evidence": live,
        "asof": None,
        "source": "板块经验规律",
        "specific": False,
    }
