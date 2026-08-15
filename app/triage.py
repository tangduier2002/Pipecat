"""triage 入口路由 — 意图分类 + 路由分发。

无编排框架 (ADR-0002): triage_route 是普通 async 函数, 调用方根据 Route.target 分发。
药物/用药表达 100% 拦截: 关键词先行 + LLM 判定双保险, 绝不进入任何 Agent。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.education import build_nvc_reject

Intent = Literal[
    "emotional_support", "lifestyle_coaching", "knowledge_query",
    "data_record", "emergency", "general_chat",
]

SUPPORTED_INTENTS: frozenset[str] = frozenset(
    {"emotional_support", "lifestyle_coaching", "knowledge_query", "data_record", "emergency", "general_chat"}
)

# 药物表达关键词 — 命中即拒答 (双保险第一道)
DRUG_KEYWORDS = (
    "药", "西柚", "剂量", "一片", "半片", "两片", "加量", "减量", "停药", "服药", "用药",
)

# 症状类紧急关键词 (规则 4) 与心理危机关键词 (规则 5)
EMERGENCY_SYMPTOM_KEYWORDS = ("胸痛", "视力模糊", "看不清", "喘不上气")
CRISIS_KEYWORDS = ("不想活", "活着没意思", "不想活了", "活不下去", "想自杀")

EMOTION_KEYWORDS = ("焦虑", "难过", "害怕", "孤独", "压力大", "烦躁", "抑郁", "失眠", "担心", "紧张")
LIFESTYLE_KEYWORDS = ("戒烟", "戒酒", "减肥", "运动计划", "锻炼计划", "饮食计划", "少吃", "多动")

FOOD_VERBS = ("吃", "喝", "食用")
EXERCISE_HINTS = ("运动", "锻炼", "活动")
SYMPTOM_HINTS = ("头晕", "头痛", "心悸", "会", "症状", "难受")

_BP_PAIR = re.compile(r"(\d{2,3})\s*/\s*(\d{2,3})")
_SYSTOLIC = re.compile(r"收缩压[^0-9]{0,4}(\d{2,3})")
_DIASTOLIC = re.compile(r"舒张压[^0-9]{0,4}(\d{2,3})")
_SINGLE_BP = re.compile(r"血压[^0-9]{0,4}(\d{2,3})")
_PULSE = re.compile(r"(?:心率|心跳|脉搏)[^0-9]{0,4}(\d{2,3})")
_WEIGHT = re.compile(r"体重[^0-9]{0,4}(\d{2,3}(?:\.\d+)?)")

# 图谱食物实体表 (从 data/domain_entities.csv 动态加载), 用于 food 实体抽取
_FOOD_NAMES: frozenset[str] = frozenset()


def _load_food_names() -> frozenset[str]:
    path = Path(__file__).resolve().parent.parent / "data" / "domain_entities.csv"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return frozenset(
                row["name"]
                for row in csv.DictReader(f)
                if row.get("type") == "Food" and row.get("name")
            )
    except FileNotFoundError:
        return frozenset()


def _init_foods() -> None:
    global _FOOD_NAMES
    if not _FOOD_NAMES:
        _FOOD_NAMES = _load_food_names()


@dataclass
class Route:
    """路由结果。reject 非空时不进入任何 Agent, 直接走 TTS。"""

    target: str | None = None
    entities: dict = field(default_factory=dict)
    reject: str | None = None


def _is_drug_expression(text: str) -> bool:
    return any(kw in text for kw in DRUG_KEYWORDS)


def _extract_bp(text: str) -> dict:
    """提取血压实体: 优先血压对, 其次 收缩压/舒张压 单值, 再单数字血压。"""
    entities: dict = {}
    pair = _BP_PAIR.search(text)
    if pair:
        entities["systolic"] = int(pair.group(1))
        entities["diastolic"] = int(pair.group(2))
        return entities
    m = _SYSTOLIC.search(text)
    if m:
        entities["systolic"] = int(m.group(1))
        return entities
    m = _DIASTOLIC.search(text)
    if m:
        entities["diastolic"] = int(m.group(1))
        return entities
    m = _SINGLE_BP.search(text)
    if m:
        entities["systolic"] = int(m.group(1))
    return entities


def _extract_food(text: str) -> str | None:
    """在图谱食物表中查找文本中出现的食物名 (最长匹配优先)。"""
    for name in sorted(_FOOD_NAMES, key=len, reverse=True):
        if name in text:
            return name
    return None


def _sub_intent_of(text: str) -> str | None:
    """knowledge_query 细分: 依据用户问法。"""
    if any(h in text for h in EXERCISE_HINTS):
        return "EXERCISE_QUERY"
    if any(v in text for v in FOOD_VERBS):
        return "FOOD_QUERY"
    if any(h in text for h in SYMPTOM_HINTS):
        return "SYMPTOM_QUERY"
    return None


def _emergency_of(text: str, entities: dict) -> bool:
    """emergency 判定: 血压阈值 + 症状关键词 + 心理危机。"""
    if any(kw in text for kw in EMERGENCY_SYMPTOM_KEYWORDS + CRISIS_KEYWORDS):
        return True
    if entities.get("systolic", 0) >= 180:
        return True
    if entities.get("diastolic", 0) >= 120:
        return True
    return False


def classify_by_keywords(user_text: str) -> dict:
    """确定性关键词兜底分类。LLM 不可用 / 无明确意图时使用。

    优先级: 药物拒答 > emergency > data_record > knowledge_query
            > emotional_support > lifestyle_coaching > general_chat
    """
    _init_foods()

    if _is_drug_expression(user_text):
        return {"intent": "reject", "entities": {}}

    entities: dict = _extract_bp(user_text)
    if _emergency_of(user_text, entities):
        return {"intent": "emergency", "entities": entities}

    m = _PULSE.search(user_text)
    if m:
        entities["pulse"] = int(m.group(1))
    m = _WEIGHT.search(user_text)
    if m:
        entities["weight"] = float(m.group(1))

    # 数值记录: 血压/心率/体重 读数
    if "血压" in user_text or "心率" in user_text or "心跳" in user_text or "脉搏" in user_text or "体重" in user_text:
        if entities or "pulse" in entities or "weight" in entities:
            return {"intent": "data_record", "entities": entities}
    if _BP_PAIR.search(user_text) and not any(v in user_text for v in FOOD_VERBS):
        return {"intent": "data_record", "entities": entities}

    # 知识问答: 图谱食物 + 吃/喝 动词
    food = _extract_food(user_text)
    if food is not None:
        return {
            "intent": "knowledge_query",
            "entities": {"food": food, "sub_intent": _sub_intent_of(user_text) or "FOOD_QUERY"},
        }
    if any(h in user_text for h in EXERCISE_HINTS):
        return {"intent": "knowledge_query", "entities": {"sub_intent": "EXERCISE_QUERY"}}
    if any(h in user_text for h in SYMPTOM_HINTS):
        return {"intent": "knowledge_query", "entities": {"sub_intent": "SYMPTOM_QUERY"}}

    if any(kw in user_text for kw in EMOTION_KEYWORDS):
        return {"intent": "emotional_support", "entities": {}}
    if any(kw in user_text for kw in LIFESTYLE_KEYWORDS):
        return {"intent": "lifestyle_coaching", "entities": {}}

    return {"intent": "general_chat", "entities": {}}


async def classify_intent(user_text: str, llm=None) -> dict:
    """LLM 分类主路径 + 关键词兜底。返回 {"intent", "entities"}。"""
    # 药物拦截先行: 无论 LLM 结果如何, 关键词命中即拒答 (双保险)
    if _is_drug_expression(user_text):
        return {"intent": "reject", "entities": {}}

    if llm is not None and llm.available:
        system = (
            "你是老年健康助手的意图分类器。只输出 JSON: "
            '{"intent": "<意图>", "entities": {...}}。'
            "意图只能是以下之一: emotional_support, lifestyle_coaching, knowledge_query, "
            "data_record, emergency, general_chat。entities 可含 food / systolic / diastolic / sub_intent "
            "(knowledge_query 细分为 FOOD_QUERY / EXERCISE_QUERY / SYMPTOM_QUERY)。"
            "含药物、用药、剂量、停药、加量、减量、西柚等内容的输入, intent 一律输出 reject。"
        )
        data = await llm.chat_json(system, user_text)
        if data and data.get("intent") in SUPPORTED_INTENTS:
            return {"intent": data["intent"], "entities": data.get("entities", {})}
        if data and data.get("intent") == "reject":
            return {"intent": "reject", "entities": {}}

    return classify_by_keywords(user_text)


async def triage_route(user_text: str, llm=None) -> Route:
    """入口路由: 意图分类 → Route。reject 非空时调用方必须直接走 TTS。"""
    intent_data = await classify_intent(user_text, llm)
    if intent_data["intent"] == "reject":
        return Route(reject=build_nvc_reject(user_text))
    return Route(target=intent_data["intent"], entities=intent_data.get("entities", {}))