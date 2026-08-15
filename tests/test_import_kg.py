"""T1 导入脚本测试: 数据完整性、幂等语义、合规红线、导入流程。"""

from __future__ import annotations

import pytest

from scripts.import_kg import (
    entity_batch_cypher,
    import_domain,
    init_patient_schema,
    load_csv,
    relation_batch_cypher,
    validate_no_drug,
)
from scripts.import_kg import ROOT, ENTITY_COLS, RELATION_COLS
from tests.conftest import FakeDriver


@pytest.fixture
def entities():
    return load_csv(ROOT / "data" / "domain_entities.csv", ENTITY_COLS)


@pytest.fixture
def relations():
    return load_csv(ROOT / "data" / "domain_relations.csv", RELATION_COLS)


def test_entity_count_meets_acceptance(entities):
    assert len(entities) >= 50


def test_relation_count_meets_acceptance(relations):
    assert len(relations) >= 100


def test_six_entity_types_no_drug(entities):
    types = {e["type"] for e in entities}
    assert types == {"Disease", "Symptom", "Food", "Exercise", "Exam", "Department"}


def test_eight_relation_types_no_drug(relations):
    rels = {r["relation"] for r in relations}
    assert rels == {
        "典型症状", "推荐食物", "禁忌食物", "推荐运动",
        "禁忌运动", "关联疾病", "收治疾病", "诊断疾病",
    }


def test_all_relation_endpoints_resolve(entities, relations):
    by_type_name = {(e["type"], e["name"]) for e in entities}
    dangling = [
        (r["from_type"], r["from_name"], r["to_type"], r["to_name"])
        for r in relations
        if (r["from_type"], r["from_name"]) not in by_type_name
        or (r["to_type"], r["to_name"]) not in by_type_name
    ]
    assert not dangling, f"悬空关系端点: {dangling}"


def test_validate_no_drug_rejects_unknown_type():
    with pytest.raises(ValueError, match="实体类型不在允许范围内"):
        validate_no_drug([{"type": "Drug", "name": "硝苯地平"}], [])


def test_validate_no_drug_rejects_drug_relation():
    with pytest.raises(ValueError, match="关系类型不在允许范围内"):
        validate_no_drug([], [{"relation": "药品-适应症"}])


def test_entity_cypher_uses_merge(entities):
    cypher = entity_batch_cypher("Disease")
    assert "MERGE (n:Domain:`Disease` {name: row.name})" in cypher
    assert "UNWIND $rows AS row" in cypher


def test_relation_cypher_uses_merge():
    cypher = relation_batch_cypher("推荐食物", "Disease", "Food")
    assert "MERGE (a)-[r:`推荐食物`]->(b)" in cypher
    assert "MATCH (a:Domain:`Disease` {name: row.from_name})" in cypher
    assert "MATCH (b:Domain:`Food` {name: row.to_name})" in cypher


async def test_import_domain_flow():
    driver = FakeDriver()
    stats = await import_domain(driver)
    assert stats.entities == 54
    assert stats.relations == 112
    assert stats.domain_nodes == 54
    assert stats.domain_relations == 112

    cyphers = [c for c, _ in driver.calls]
    assert sum("MERGE (n:Domain" in c for c in cyphers) == 6  # 六类实体各一批
    assert sum("MERGE (a)-[r" in c for c in cyphers) > 0
    # 导入批量语句全部为 MERGE, 重复执行不增长
    assert all("MERGE" in c for c in cyphers if "UNWIND" in c)


async def test_init_patient_schema_is_idempotent_cypher():
    driver = FakeDriver()
    await init_patient_schema(driver)
    cyphers = [c for c, _ in driver.calls]
    assert any("IF NOT EXISTS" in c for c in cyphers)
    # 患者主节点唯一键用 name (schema: Patient:Patient {name, age})
    assert any("REQUIRE p.name IS UNIQUE" in c for c in cyphers)
    # 不允许出现重复标签 Patient:Patient 这种 Cypher 语法错误
    assert not any("Patient:Patient" in c for c in cyphers)


def test_entity_props_drop_empty_cells(entities):
    disease = next(e for e in entities if e["name"] == "高血压")
    assert disease["icd10"] == "I10"
    food = next(e for e in entities if e["name"] == "咸菜")
    assert food["sodium_per_100g"] == "2000"