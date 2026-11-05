# -*- coding: utf-8 -*-
"""Household-consumption tags for deflation: volume resilient vs may rise."""

# label: 通缩抗跌 | 通缩可能升温 | 通缩配置升温
# note: short reason shown in analysis
DEFLATION_TAGS: dict[str, dict] = {
    # 用量可能升高：最便宜蛋白
    "CN:JD0": {
        "label": "通缩可能升温",
        "note": "最便宜动物蛋白，猪肉贵或钱包紧时家庭会加鸡蛋；在家做饭刚需。",
    },
    # 主食：用量极稳，低收入时主食份额升
    "CN:RR0": {"label": "通缩抗跌", "note": "粳米主食，生存需求，用量对收入极不敏感。"},
    "CN:JR0": {"label": "通缩抗跌", "note": "粳稻对应口粮，通缩期主食份额通常上升。"},
    "CN:RI0": {"label": "通缩抗跌", "note": "早籼稻口粮，需求刚性。"},
    "CN:LR0": {"label": "通缩抗跌", "note": "晚籼稻口粮，需求刚性。"},
    "CN:WH0": {"label": "通缩抗跌", "note": "强麦/面粉主食，家庭消费抗跌。"},
    "CN:C0": {
        "label": "通缩抗跌",
        "note": "口粮部分刚需；饲料部分会随养猪去产能走弱，整体偏抗跌而非升温。",
    },
    "CN:CS0": {"label": "通缩抗跌", "note": "淀粉多用于食品加工，需求相对平稳。"},
    "US:ZC=F": {"label": "通缩抗跌", "note": "美玉米食用刚需稳，饲料随养殖波动。"},
    "US:ZW=F": {"label": "通缩抗跌", "note": "小麦主食，用量抗跌。"},
    "US:KE=F": {"label": "通缩抗跌", "note": "硬红冬麦，面粉链条刚需。"},
    "US:ZO=F": {"label": "通缩抗跌", "note": "燕麦食品需求相对稳定。"},
    "US:ZR=F": {"label": "通缩抗跌", "note": "糙米主食属性，用量抗跌。"},
    # 食用油：在家做饭升、餐饮降，净效果偏抗跌
    "CN:Y0": {"label": "通缩抗跌", "note": "家庭炒菜刚需，可部分对冲餐饮用油下滑。"},
    "CN:OI0": {"label": "通缩抗跌", "note": "菜油偏家庭消费，通缩期用量较稳。"},
    "CN:P0": {"label": "通缩抗跌", "note": "棕榈油食品用途广，弹性小于工业品。"},
    "US:ZL=F": {"label": "通缩抗跌", "note": "美豆油食用部分刚需，生柴政策另算。"},
    # 糖
    "CN:SR0": {"label": "通缩抗跌", "note": "食品饮料刚需，单价低，用量对收入不敏感。"},
    "US:SB=F": {"label": "通缩抗跌", "note": "原糖食品需求刚性，通缩期用量难降。"},
    # 黄金：配置需求
    "CN:AU0": {
        "label": "通缩配置升温",
        "note": "不是餐桌消费；物价弱、房价弱时居民金饰/金条配置需求常升。",
    },
    "US:GC=F": {
        "label": "通缩配置升温",
        "note": "避险与实际利率定价；中国通缩期居民购金往往升温。",
    },
}


def get_deflation(instrument_id: str) -> dict | None:
    return DEFLATION_TAGS.get(instrument_id)


def apply_deflation(item: dict) -> dict:
    tag = DEFLATION_TAGS.get(item.get("id", ""))
    if tag:
        item["deflation_tag"] = tag["label"]
        item["deflation_note"] = tag["note"]
    else:
        item["deflation_tag"] = None
        item["deflation_note"] = None
    return item
