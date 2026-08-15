"""T2 education 路由测试: 四个验收样例 + 图谱无匹配拒答。"""

from __future__ import annotations

import pytest

from app.education import (
    DISCLAIMER,
    INTENT_HANDLERS,
    NVC_REJECT_TEMPLATE_3,
    education_route,
)


class FakeKG:
    """按 (cypher, params) 返回预设行的测试替身。"""

    def __init__(self, rows_by_query: list[tuple[str, list[dict]]] | None = None, empty: bool = False):
        self._rows = rows_by_query or []
        self._empty = empty
        self.calls: list[tuple[str, dict]] = []

    async def query(self, cypher: str, params: dict) -> list[dict]:
        self.calls.append((cypher, params))
        if self._empty:
            return []
        for expected, rows in self._rows:
            if expected in cypher:
                return rows
        return []


@pytest.fixture
def exercise_rows():
    return [
        {
            "disease": "高血压",
            "recommended": ["快走", "游泳", "太极拳"],
            "forbidden": ["剧烈跑跳"],
        }
    ]


async def test_exercise_query_returns_kg_answer_with_disclaimer(exercise_rows):
    kg = FakeKG([("推荐运动", exercise_rows)])
    out = await education_route("高血压适合什么运动？", {"disease": "高血压", "sub_intent": "EXERCISE_QUERY"}, kg)
    assert "快走" in out and "游泳" in out
    assert "剧烈跑跳" in out
    assert DISCLAIMER in out


async def test_food_query_recommended_side():
    kg = FakeKG([("推荐食物", [{"disease": "高血压", "food": "香蕉", "side": "recommended"}])])
    out = await education_route("高血压能吃香蕉吗？", {"disease": "高血压", "food": "香蕉", "sub_intent": "FOOD_QUERY"}, kg)
    assert "香蕉" in out and "推荐" in out
    assert DISCLAIMER in out


async def test_food_query_forbidden_side():
    kg = FakeKG([("推荐食物", [{"disease": "高血压", "food": "咸菜", "side": "forbidden"}])])
    out = await education_route("高血压能吃咸菜吗？", {"disease": "高血压", "food": "咸菜", "sub_intent": "FOOD_QUERY"}, kg)
    assert "咸菜" in out and "避开" in out
    assert DISCLAIMER in out


async def test_symptom_query_returns_kg_answer():
    kg = FakeKG([("典型症状", [{"disease": "高血压", "symptoms": ["头晕", "头痛", "心悸"]}])])
    out = await education_route("高血压会头晕吗？", {"disease": "高血压", "sub_intent": "SYMPTOM_QUERY"}, kg)
    assert "头晕" in out
    assert DISCLAIMER in out


async def test_no_graph_match_returns_nvc_template3():
    # 疾病在图谱, 但查询实体 (如黄瓜) 不在图谱 → 空结果 → 拒答
    kg = FakeKG(empty=True)
    out = await education_route(
        "黄瓜能吃吗？",
        {"disease": "高血压", "food": "黄瓜", "sub_intent": "FOOD_QUERY"},
        kg,
    )
    assert out == NVC_REJECT_TEMPLATE_3
    assert len(kg.calls) == 1  # 查询已发出, 结果为空


async def test_missing_disease_entity_rejects_without_query():
    kg = FakeKG()
    out = await education_route("黄瓜能吃吗？", {"food": "黄瓜", "sub_intent": "FOOD_QUERY"}, kg)
    assert out == NVC_REJECT_TEMPLATE_3
    assert kg.calls == []  # 实体缺失时不产生任何图谱查询


async def test_unknown_sub_intent_rejects():
    kg = FakeKG()
    out = await education_route("帮我查查药", {"sub_intent": "DRUG_QUERY"}, kg)
    assert out == NVC_REJECT_TEMPLATE_3


async def test_education_never_generates_drug_content():
    """全组件无药物路径: 所有 handler 的 Cypher 均无药物相关字样。"""
    drug_keywords = ["药", "Medication", "Drug", "西柚"]
    for sub_intent, handler in INTENT_HANDLERS.items():
        assert sub_intent in {"FOOD_QUERY", "EXERCISE_QUERY", "SYMPTOM_QUERY"}
        assert not any(kw in handler.cypher for kw in drug_keywords)