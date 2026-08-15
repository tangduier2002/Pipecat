"""T2 kg_service 测试: 查询构造与 Cypher 模板正确性。"""

from __future__ import annotations

from app.knowledge_graph.kg_service import DomainKGService
from tests.conftest import FakeDriver


class FakeDomainKGService(DomainKGService):
    """用记录式 fake driver 替换真实驱动, 验证查询构造。"""

    def __init__(self):
        self._fake = FakeDriver(counts={"nodes": 0, "relations": 0})
        self._driver = self._fake

    @property
    def calls(self):
        return self._fake.calls


def _query_of(calls, needle: str) -> str:
    return next(c for c, _ in calls if needle in c)


async def test_query_returns_list_of_dicts():
    kg = FakeDomainKGService()
    rows = await kg.query("MATCH (n:Domain) RETURN n.name AS name")
    assert isinstance(rows, list)
    assert kg.calls[0][0] == "MATCH (n:Domain) RETURN n.name AS name"


async def test_get_disease_info_builds_expected_cypher():
    kg = FakeDomainKGService()
    await kg.get_disease_info("高血压")
    cypher, params = kg.calls[0]
    assert "典型症状" in cypher
    assert "推荐食物" in cypher
    assert "禁忌食物" in cypher
    assert "推荐运动" in cypher
    assert params == {"name": "高血压"}
    # 返回结构含疾病基础信息字段
    assert "RETURN d.name AS name" in cypher
    assert "d.icd10" in cypher


async def test_no_drug_in_any_query_template():
    kg = FakeDomainKGService()
    await kg.get_disease_info("高血压")
    cypher, _ = kg.calls[0]
    for kw in ("药", "Medication", "Drug", "西柚"):
        assert kw not in cypher