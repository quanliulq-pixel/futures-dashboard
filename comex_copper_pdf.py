# -*- coding: utf-8 -*-
"""Build COMEX copper research PDF with Chinese fonts."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path(r"C:\Users\lq\Desktop\lq\COMEX铜期货调研.pdf")
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BD = r"C:\Windows\Fonts\msyhbd.ttc"

pdfmetrics.registerFont(TTFont("YaHei", FONT_REG, subfontIndex=0))
pdfmetrics.registerFont(TTFont("YaHeiBd", FONT_BD, subfontIndex=0))

NAVY = colors.HexColor("#1a365d")
NAVY2 = colors.HexColor("#245a93")
TEAL = colors.HexColor("#1d6b63")
RULE = colors.HexColor("#c5d0dc")
ROW = colors.HexColor("#f4f7fb")
HEAD_BG = colors.HexColor("#1a365d")
MUTED = colors.HexColor("#4a5b6e")
WARN_BG = colors.HexColor("#fff6e5")


def styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle(
        name="CoverKicker", fontName="YaHei", fontSize=10, textColor=NAVY2,
        alignment=TA_LEFT, spaceAfter=4, tracking=0.4,
    ))
    ss.add(ParagraphStyle(
        name="CoverTitle", fontName="YaHeiBd", fontSize=22, textColor=NAVY,
        leading=30, spaceAfter=8, alignment=TA_LEFT,
    ))
    ss.add(ParagraphStyle(
        name="CoverSub", fontName="YaHei", fontSize=11, textColor=MUTED,
        leading=16, spaceAfter=10,
    ))
    ss.add(ParagraphStyle(
        name="H1", fontName="YaHeiBd", fontSize=13.5, textColor=NAVY,
        spaceBefore=14, spaceAfter=7, leading=18,
    ))
    ss.add(ParagraphStyle(
        name="H2", fontName="YaHeiBd", fontSize=11.5, textColor=NAVY2,
        spaceBefore=10, spaceAfter=5, leading=16,
    ))
    ss.add(ParagraphStyle(
        name="Body", fontName="YaHei", fontSize=9.5, textColor=colors.HexColor("#1c2430"),
        leading=15.5, alignment=TA_LEFT, spaceAfter=6,
    ))
    ss.add(ParagraphStyle(
        name="Note", fontName="YaHei", fontSize=8.5, textColor=MUTED,
        leading=13, spaceAfter=6, spaceBefore=2,
    ))
    ss.add(ParagraphStyle(
        name="Item", fontName="YaHei", fontSize=9.2, textColor=colors.HexColor("#1c2430"),
        leading=14.5, leftIndent=12, spaceAfter=3,
    ))
    ss.add(ParagraphStyle(
        name="Th", fontName="YaHeiBd", fontSize=8.2, textColor=colors.white,
        leading=12, alignment=TA_CENTER,
    ))
    ss.add(ParagraphStyle(
        name="Td", fontName="YaHei", fontSize=8.2, textColor=colors.HexColor("#1c2430"),
        leading=12, alignment=TA_LEFT,
    ))
    ss.add(ParagraphStyle(
        name="TdC", fontName="YaHei", fontSize=8.2, textColor=colors.HexColor("#1c2430"),
        leading=12, alignment=TA_CENTER,
    ))
    ss.add(ParagraphStyle(
        name="TdR", fontName="YaHei", fontSize=8.2, textColor=colors.HexColor("#1c2430"),
        leading=12, alignment=TA_RIGHT,
    ))
    ss.add(ParagraphStyle(
        name="Src", fontName="YaHei", fontSize=7.6, textColor=MUTED,
        leading=11.5, leftIndent=14, firstLineIndent=-14, spaceAfter=3,
    ))
    ss.add(ParagraphStyle(
        name="Footer", fontName="YaHei", fontSize=8, textColor=MUTED, alignment=TA_CENTER,
    ))
    return ss


S = styles()
W = A4[0] - 36 * mm


def P(text, style="Body"):
    return Paragraph(text, S[style])


def th(*cells):
    return [Paragraph(c, S["Th"]) for c in cells]


def td(*cells, align=None):
    out = []
    for i, c in enumerate(cells):
        if align == "c":
            out.append(Paragraph(str(c), S["TdC"]))
        elif align == "r" or (align and align[i] == "r"):
            sty = S["TdR"] if (align == "r" or align[i] == "r") else S["Td"]
            out.append(Paragraph(str(c), sty))
        else:
            out.append(Paragraph(str(c), S["Td"]))
    return out


def grid(rows, col_widths, header=True):
    t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
    ]
    if header:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ]
        for i in range(1, len(rows)):
            if i % 2 == 0:
                cmds.append(("BACKGROUND", (0, i), (-1, i), ROW))
    t.setStyle(TableStyle(cmds))
    return t


def header_footer(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 14 * mm, w, 14 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("YaHei", 8.5)
    canvas.drawString(18 * mm, h - 9 * mm, "COMEX 铜期货调研备忘")
    canvas.setFont("YaHei", 8)
    canvas.drawRightString(w - 18 * mm, h - 9 * mm, "2026-08-16")
    canvas.setFillColor(NAVY2)
    canvas.rect(0, 0, w, 11 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("YaHei", 8)
    canvas.drawString(18 * mm, 4.2 * mm, "研究状态：部分完成  ·  行情为转载，非 CME 日度公告原文")
    canvas.drawRightString(w - 18 * mm, 4.2 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def build():
    story = []
    story.append(Spacer(1, 4))
    story.append(P("大宗商品研究备忘  ·  期货本地看板", "CoverKicker"))
    story.append(P("COMEX 铜期货（HG）", "CoverTitle"))
    story.append(P(
        "纽约商品交易所高品位铜实物交割合约。本备忘整理合约规则、近月价格与持仓、"
        "COMEX / LME / 上期所库存口径、三地合约差异，以及 2026 年 8 月纪录高位背后的供需与宏观背景。"
        "覆盖状态为<strong>部分完成</strong>：CME 官方结算/持仓页与上期所原表未能直接抓取。",
        "CoverSub",
    ))

    # ---- 1 合约 ----
    story.append(P("一、合约要点", "H1"))
    story.append(P(
        "COMEX 铜期货合约代码 <b>HG</b>，是纽约商品交易所的高品位铜实物交割合约。"
        "每手 25,000 磅，交割重量允许上下浮动 2%；价格以美元及美分/磅报价，"
        "最小变动 0.0005 美元/磅，合每手 12.50 美元。"
        "2026 年 8 月中旬，多数行情源把 <b>9 月合约</b>当作近月，8 月合约仍挂牌但流动性已明显转弱。"
        "交割只能在美国指定仓库进行，最后交易日为交割月倒数第三个营业日，"
        "剩余头寸须在该月内完成交割或通过许可的期转现了结。",
    ))

    spec = [
        th("项目", "内容"),
        td("代码 / 交易所", "HG  ·  COMEX（CME Group）"),
        td("交易单位", "25,000 磅/手（重量可 ±2%）"),
        td("报价与最小变动", "美元/磅；0.0005 美元/磅 = 每手 12.50 美元"),
        td("交割品", "ASTM B115 一级电解铜阴极，整板或切割；单仓单仅对应一个认可品牌"),
        td("交割地", "仅美国指定仓库；交割不含运费补贴"),
        td("最后交易日", "交割月倒数第三个营业日（8 月合约约 2026-08-27）"),
        td("电子盘时间", "周日–周五，美东 18:00–次日 17:00；每天 17:00 起休 60 分钟"),
        td("挂牌（2026-02-23 起）", "连续 37 个月度合约 + 63 个月内所有 3/5/7/9/12 月合约"),
        td("到期后了结", "交割月任一营业日交割，或按规则 538 做期转现"),
    ]
    story.append(grid(spec, [38 * mm, W - 38 * mm]))
    story.append(P(
        "说明：Chapter 111 未写最后交易钟点。CME 产品页有来源称美中午盘 12:00 终止，但未能从完整页面或 PDF 独立抽出。"
        "交易时间来自将 HGS 与 HG 等同的 FAQ；节假日与提前收盘可能不同。",
        "Note",
    ))

    # ---- 2 价格 ----
    story.append(P("二、近月价格与持仓", "H1"))
    story.append(P(
        "沿近月曲线，结算价由 8 月到 10 月依次上移，呈小幅升水（contango）。"
        "成交与持仓高度集中在 <b>9 月 HGU26</b>。8 月 HGQ26 仍在挂牌，到期约 2026-08-27，但量仓已显著萎缩。",
    ))
    px = [
        th("合约", "最新价（美元/磅）", "2026-08-14 结算", "成交量", "持仓量"),
        td("2026年9月 HGU26", "6.6065", "6.6130", "37,260", "98,031", align="c"),
        td("2026年8月 HGQ26", "6.5890", "6.5995", "214", "2,239", align="c"),
        td("2026年10月 HGV26", "6.636", "6.6470", "1,063", "11,533", align="c"),
    ]
    story.append(grid(px, [42 * mm, 38 * mm, 38 * mm, 28 * mm, 28 * mm]))
    story.append(P(
        "数字来自 CNBC / MarketWatch 转载，不是 CME 日度公告原文。同一合约最新价与结算价会有差异"
        "（如 HGU26 最新 6.6065、结算 6.6130）；网页持仓常滞后一个交易日。"
        "8 月量仓（214 / 2,239）仅在 MarketWatch 交叉核对。",
        "Note",
    ))

    # ---- 3 库存 ----
    story.append(P("三、交割标准与可见库存", "H1"))
    story.append(P(
        "可交割铜须为整板或切割的一级电解铜阴极，符合 ASTM B115-00 或交易所采纳的最新修订；"
        "须存放在指定仓库并由过磅师称重。三家交易所库存口径不同，<b>不能直接相加</b>。",
    ))
    inv = [
        th("市场", "时点", "口径", "数量", "备注"),
        td("COMEX", "2026-08-13", "注册仓单", "447,816 短吨", "已签发仓单"),
        td("COMEX", "2026-08-13", "合格未注册", "287,654 短吨", "eligible，亦视为可交割供给"),
        td("COMEX", "2026-08-13", "仓库合计", "735,470 短吨", "约合 66.7 万吨；新奥尔良 486,039"),
        td("LME", "2026-07-01→31", "官方收市库存", "322,350 → 244,025 吨", "7 月明显去库"),
        td("上期所", "2026-08-14", "仓单库存", "33,676 吨", "前一日 27,208；CEIC 转载"),
        td("上期所", "2026-08-14", "可交割库存", "69,731 吨", "大于仓单，口径不同"),
    ]
    story.append(grid(inv, [24 * mm, 32 * mm, 32 * mm, 36 * mm, W - 124 * mm]))
    story.append(P(
        "COMEX 把持牌仓库金属分为已注册与合格未注册，并均视为可交割供给；"
        "LME 公布开/收市库存、有效与注销仓单，以及仓外持有；"
        "上期所同时给出仓单库存和更大的可交割库存。"
        "COMEX 用短吨，LME/上期所用公吨。735,470 短吨 ≈ 667,000 公吨是换算结果，并非 CME 印刷数字。"
        "LME 8 月日度库存拆分官方工作簿未取到；上期所原表被站点拦截。",
        "Note",
    ))

    # ---- 4 对比 ----
    story.append(P("四、与 LME、上期所的差异", "H1"))
    cmp_rows = [
        th("项目", "COMEX HG", "LME 铜", "上期所沪铜"),
        td("交易单位", "25,000 磅/手", "25 吨/手", "5 吨交易，25 吨仓单交割"),
        td("品级", "ASTM B115 一级电解铜阴极", "A 级整板；BS EN 1978 / GB/T 467 / ASTM B115", "GB/T 467-2010 或 BS EN 1978:1998 A 级（Cu-CATH-1）"),
        td("交割地", "仅美国指定仓库", "全球约 32 地、约 400 座认可仓库", "上期所指定库"),
        td("到期地点", "美国交割点", "卖方分配仓单，买方不能自选地点", "国内指定库"),
    ]
    story.append(grid(cmp_rows, [24 * mm, 48 * mm, 58 * mm, W - 130 * mm]))
    story.append(P(
        "因此 COMEX 相对 LME 的升水，经常是「美国关税 + 美国交割点」的区域价差，而不是全球只有一张铜价。"
        "LME 认可地点名单会变（2017 年资料曾写 34 地，2026 年页面写 32 地）。",
        "Note",
    ))

    # ---- 5 高价 ----
    story.append(P("五、本轮纪录高位的背景", "H1"))
    story.append(P(
        "2026 年 8 月初 COMEX 铜创下纪录高位，被归因于矿山供给受限、美国关税不确定，"
        "以及电网、电气化和人工智能相关需求，而不是全球增长全面加速。",
    ))
    story.append(P("1. 美国库存堆积、全球可见库存地域分裂", "H2"))
    story.append(P(
        "关税套利把大量精炼铜抽入美国及 CME/COMEX 仓库：2025 年前 11 个月美国精炼铜进口 145 万吨，"
        "同比增加逾 60 万吨；CME 仓库库存由 2025 年初约 8.5 万吨升至 53.6 万吨。"
        "背景是关税威胁后 CME 完税价相对 LME 出现大幅升水。外面紧、美国仓库胀，是这轮结构的核心。",
    ))
    story.append(P("2. 矿山慢、精矿比精炼更紧", "H2"))
    story.append(P(
        "全球平均入选品位自 1991 年以来下降约 40%，发现至投产约 17 年。"
        "按现有项目管道，到 2035 年铜供应缺口或达 30%。"
        "精矿端更紧：2026 年安托法加斯塔与中国主要冶炼厂的年度加工精炼费（TC/RC）基准为每吨 <b>0 美元</b>，"
        "为历年谈判最低；现货 TC/RC 自 2024 年起已为负值，反映中国冶炼产能扩张快于精矿增产。",
    ))
    story.append(P("3. 需求叙事与金融放大", "H2"))
    story.append(P(
        "电网、电动车、工业、建筑和数据中心构成纪录价格下的需求叙事。"
        "较低利率、偏弱美元、资金转向实物资产以及金融投机进一步放大了 2026 年这轮上涨。",
    ))
    story.append(P("需要同时记住的反证", "H2"))
    story.append(P(
        "可见库存有时并不少。路透 2026 年 2 月曾指出，主要交易所合计库存一度超过 110 万吨，"
        "价格在「定价稀缺」，不等于全球马上没铜。"
        "ICSG 对 2026 年精炼平衡说法不一：有转载称 2025 年 10 月预计短缺 15 万吨，"
        "也有 2026 年 4 月转载称过剩约 9.6 万吨，官方 PDF 未取到。"
        "中国约占精炼消费 58%–60%，近期证据在逢低补库与高价抑制现货采购之间摇摆。",
    ))

    # ---- 6 观察 ----
    story.append(P("六、后续可盯的变量", "H1"))
    bullets = [
        "COMEX–LME 升水：衡量关税与美国交割溢价是否还在扩张。",
        "月差：8/9/10 月已是小幅升水，若近月转贴水，往往对应美库开始流出或近月挤仓。",
        "新奥尔良库存：COMEX 仓库最大集中地，流出比总量更敏感。",
        "美国精炼铜关税：15%/30% 方案在 2026-06-30 复审后仍未落地。",
        "TC/RC 与精矿干扰：负加工费说明冶炼端利润薄，矿山扰动会更快传到阴极。",
        "本地看板对应品种：北美 HG=F（COMEX 铜）。",
    ]
    for b in bullets:
        story.append(P("•  " + b, "Item"))

    # ---- 7 不确定 ----
    story.append(P("七、未完全核实与口径限制", "H1"))
    story.append(P(
        "本备忘覆盖状态为部分完成。以下条目在原文核验中被明确标为不确定或未采用：",
    ))
    gaps = [
        "最后交易具体钟点未能从规则 PDF 独立抽出；交割出库（仓单注销后约 5 个营业日）因 Chapter 7 PDF 截断而未展开。",
        "CME 官方报价、结算、成交与持仓表为 JavaScript 渲染且抓取被拦，近月数字为行情商转载。",
        "LME 2026 年 8 月日度库存拆分官方文件未取到；有效仓单与注销仓单拆分缺失，可交割量可能小于收市库存。",
        "上期所仓单与可交割库存来自 CEIC 转载，不是交易所原表。",
        "COMEX eligible、LME off-warrant、上期所「可交割」法律含义不同，三地库存不可简单加总。",
        "美国是否落实精炼铜 15%/30% 关税仍未定。刚果精矿出口禁令的实际冲击有限——多数刚果铜已以阴极出口，且存在一年豁免。",
        "「232 条款半成品铜税是 COMEX 特有政策驱动」这一表述未获可核来源支持，故未写入正文。",
    ]
    for g in gaps:
        story.append(P("•  " + g, "Item"))

    # ---- 8 来源 ----
    story.append(P("八、主要来源", "H1"))
    srcs = [
        ("[S1][S5][S6] COMEX Rulebook Chapter 111 — Copper Futures",
         "www.cmegroup.com/rulebook/COMEX/1a/111.pdf"),
        ("[S2] CME Micro Copper Futures FAQ",
         "www.cmegroup.com/articles/faqs/micro-copper-futures-faq.html"),
        ("[S3] CME Copper Financial Futures FAQ",
         "www.cmegroup.com/education/articles-and-reports/copper-financial-futures-faq"),
        ("[S4] CME Globex Notice, 2026-02-02",
         "www.cmegroup.com/notices/electronic-trading/2026/02/20260202.html"),
        ("[S7] MarketWatch HGU26", "www.marketwatch.com/investing/future/hgu26"),
        ("[S8] CNBC @HG.1", "www.cnbc.com/quotes/@HG.1"),
        ("[S9] MarketWatch HGQ26", "www.marketwatch.com/investing/future/hgq26"),
        ("[S10] CNBC @HG.2", "www.cnbc.com/quotes/@HG.2"),
        ("[S11] TradingView COMEX:HG1!", "www.tradingview.com/symbols/COMEX-HG1!/"),
        ("[S12] Barchart HGQ26", "www.barchart.com/futures/quotes/HGQ26"),
        ("[S13] CME COMEX Copper Warehouse Stocks",
         "www.cmegroup.com/delivery_reports/Copper_Stocks.xls"),
        ("[S14] LME Monthly Stock Report — July 2026", ""),
        ("[S15] CEIC：上期所铜库存", "www.ceicdata.com"),
        ("[S17] LME Digest：LME 与 CME 铜套利",
         "www.lme.com/education/online-resources/lme-digest/lme-and-cme-copper-arbitrage"),
        ("[S18] COMEX Submission No. 14-073 Appendix B（可交割供给口径）", ""),
        ("[S19] CNBC, 2026-08-06：铜创历史新高",
         "www.cnbc.com/2026/08/06/copper-jumps-to-its-highest-level-ever-what-the-metal-is-telling-us-.html"),
        ("[S20] Reuters, 2026-02-13：在库存充裕时定价稀缺",
         "www.reuters.com/markets/commodities/copper-is-pricing-scarcity-time-plenty-2026-02-13/"),
        ("[S21][S22][S23] IEA：铜价创新高，冶炼厂承压",
         "www.iea.org/commentaries/copper-prices-have-hit-record-highs-but-smelters-face-mounting-strategic-pressures"),
    ]
    for title, url in srcs:
        line = title if not url else f"{title}<br/><font size='7' color='#5a6b7e'>{url}</font>"
        story.append(P(line, "Src"))

    story.append(Spacer(1, 8))
    story.append(P(
        "免责声明：本文仅整理公开规则与二手行情，供本地研究备忘，不构成投资建议。"
        "连续合约与主力合约可能因换月出现价差；库存单位与口径见正文。",
        "Note",
    ))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=16 * mm,
        title="COMEX铜期货调研",
        author="期货本地看板",
        subject="COMEX HG copper futures research memo",
    )
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    print("wrote", OUT, "bytes", OUT.stat().st_size)


if __name__ == "__main__":
    build()
