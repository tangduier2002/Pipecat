"""T3 triage 测试: 4 个验收样例 + 关键词兜底 + 药物 100% 拦截。"""

from __future__ import annotations

import pytest

from app.triage import (
    Route,
    classify_by_keywords,
    classify_intent,
    triage_route,
)


class FakeLLM:
    """预设响应的 LLM 替身。"""

    def __init__(self, response: dict | None = None, available: bool = True, fail: bool = False):
        self._response = response
        self._available = available
        self._fail = fail
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return self._available

    async def chat_json(self, system: str, user: str) -> dict | None:
        self.calls.append(user)
        if self._fail:
            return None
        return self._response


async def test_banana_query_routes_to_knowledge():
    route = await triage_route("我能吃香蕉吗？", llm=None)
    assert route.target == "knowledge_query"
    assert route.entities["food"] == "香蕉"
    assert route.entities["sub_intent"] == "FOOD_QUERY"
    assert route.reject is None


async def test_systolic_185_routes_to_emergency():
    route = await triage_route("收缩压 185", llm=None)
    assert route.target == "emergency"
    assert route.entities["systolic"] == 185
    assert route.reject is None


async def test_drug_grapefruit_question_rejected_no_agent_call():
    route = await triage_route("降压药能和西柚一起吃吗", llm=None)
    assert route.reject is not None
    assert route.target is None  # 不产生任何 Agent 调用


async def test_anxiety_routes_to_emotional_support():
    route = await triage_route("我最近很焦虑", llm=None)
    assert route.target == "emotional_support"
    assert route.reject is None


async def test_bp_pair_routes_to_data_record():
    route = await triage_route("155/95", llm=None)
    assert route.target == "data_record"
    assert route.entities["systolic"] == 155
    assert route.entities["diastolic"] == 95


async def test_dizziness_with_bp_routes_to_data_record():
    # T8 验收场景: 症状 + 血压读数 → 记录优先
    route = await triage_route("我有点头晕，今天血压 155/95", llm=None)
    assert route.target == "data_record"
    assert route.entities["systolic"] == 155


async def test_exercise_query_sub_intent():
    route = await triage_route("高血压适合什么运动？", llm=None)
    assert route.target == "knowledge_query"
    assert route.entities["sub_intent"] == "EXERCISE_QUERY"


async def test_symptom_query_sub_intent():
    route = await triage_route("高血压会头晕吗？", llm=None)
    assert route.target == "knowledge_query"
    assert route.entities["sub_intent"] == "SYMPTOM_QUERY"


async def test_quit_smoking_routes_to_lifestyle():
    route = await triage_route("我想戒烟", llm=None)
    assert route.target == "lifestyle_coaching"


async def test_greeting_routes_to_general_chat():
    route = await triage_route("早上好", llm=None)
    assert route.target == "general_chat"


@pytest.mark.parametrize(
    "text",
    [
        "这个药能吃吗",
        "我想停药",
        "能不能加量",
        "减量一半可以吗",
        "西柚和药一起吃行不行",
        "降压药说明书",
        "一片够吗",
    ],
)
async def test_drug_expressions_always_rejected(text):
    route = await triage_route(text, llm=None)
    assert route.reject is not None, f"应拒答: {text}"
    assert route.target is None


async def test_drug_rejection_wins_over_llm():
    """LLM 返回错误意图时, 关键词双保险仍拦截 (药物 100%)。"""
    llm = FakeLLM(response={"intent": "knowledge_query", "entities": {"food": "西柚"}})
    route = await triage_route("降压药能和西柚一起吃吗", llm=llm)
    assert route.reject is not None
    assert route.target is None
    assert llm.calls == []  # 药物输入甚至不调用 LLM


async def test_llm_classification_main_path():
    llm = FakeLLM(response={"intent": "emotional_support", "entities": {}})
    intent_data = await classify_intent("我最近很焦虑", llm)
    assert intent_data["intent"] == "emotional_support"
    assert llm.calls == ["我最近很焦虑"]


async def test_llm_failure_falls_back_to_keywords():
    llm = FakeLLM(fail=True)
    intent_data = await classify_intent("收缩压 185", llm)
    assert intent_data["intent"] == "emergency"
    assert intent_data["entities"]["systolic"] == 185


async def test_llm_unknown_intent_falls_back():
    llm = FakeLLM(response={"intent": "chitchat_about_weather", "entities": {}})
    intent_data = await classify_intent("早上好", llm)
    assert intent_data["intent"] == "general_chat"


def test_route_defaults():
    r = Route()
    assert r.target is None
    assert r.entities == {}
    assert r.reject is None


def test_classify_by_keywords_no_agent_path_for_reject():
    data = classify_by_keywords("停药")
    assert data["intent"] == "reject"