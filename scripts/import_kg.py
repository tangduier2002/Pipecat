"""Domain KG 批量导入 + Patient KG 约束初始化。

用法:
    python scripts/import_kg.py            # 仅导入 Domain KG
    python scripts/import_kg.py --init-patient  # 额外初始化 Patient KG 约束

幂等: 全部使用 MERGE, 重复执行不会产生重复节点。
连接信息从 .env 读取 (NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD)。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from neo4j import AsyncGraphDatabase

ROOT = Path(__file__).resolve().parent.parent

# 合规红线: 允许的实体类型与关系; 不含任何 Drug / 药物关系
ALLOWED_TYPES = frozenset(
    {"Disease", "Symptom", "Food", "Exercise", "Exam", "Department"}
)
ALLOWED_RELATIONS = frozenset(
    {"典型症状", "推荐食物", "禁忌食物", "推荐运动", "禁忌运动", "关联疾病", "收治疾病", "诊断疾病"}
)

ENTITY_COLS = ("type", "name", "icd10", "description", "sodium_per_100g")
RELATION_COLS = ("relation", "from_type", "from_name", "to_type", "to_name", "risk_level", "evidence")


@dataclass(frozen=True)
class ImportStats:
    entities: int
    relations: int
    domain_nodes: int
    domain_relations: int


def load_csv(path: Path, cols: tuple[str, ...]) -> list[dict]:
    """读取 CSV (兼容 UTF-8 BOM), 空字段置为 None。"""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    cleaned = []
    for row in rows:
        cleaned.append({k: (row.get(k) or "").strip() or None for k in cols})
    return cleaned


def validate_no_drug(entities: list[dict], relations: list[dict]) -> None:
    """合规红线断言: 实体类型与关系类型均不允许药物相关内容。"""
    for e in entities:
        if e["type"] not in ALLOWED_TYPES:
            raise ValueError(f"实体类型不在允许范围内: {e['type']} (name={e['name']})")
    for r in relations:
        if r["relation"] not in ALLOWED_RELATIONS:
            raise ValueError(f"关系类型不在允许范围内: {r['relation']}")
        if r["from_type"] not in ALLOWED_TYPES or r["to_type"] not in ALLOWED_TYPES:
            raise ValueError(
                f"关系端点类型非法: {r['from_type']}-[{r['relation']}]->{r['to_type']}"
            )


def props(row: dict, excluded: set[str]) -> dict:
    """非空属性, 去除 CSV 空单元格与排除键。"""
    return {k: v for k, v in row.items() if k not in excluded and v is not None}


ENTITY_EXCLUDED = {"type"}
RELATION_EXCLUDED = {"relation", "from_type", "from_name", "to_type", "to_name"}


def entity_batch_cypher(label: str) -> str:
    return f"""
    UNWIND $rows AS row
    MERGE (n:Domain:`{label}` {{name: row.name}})
    SET n += row.props
    """


def relation_batch_cypher(rel_name: str, from_label: str, to_label: str) -> str:
    """关系批量 MERGE。标签来自固定的 ENTITY_LABELS 映射, 无注入面。"""
    return f"""
    UNWIND $rows AS row
    MATCH (a:Domain:`{from_label}` {{name: row.from_name}})
    MATCH (b:Domain:`{to_label}` {{name: row.to_name}})
    MERGE (a)-[r:`{rel_name}`]->(b)
    SET r += row.props
    """


def group_by(rows: list[dict], key) -> dict:
    grouped = {}
    for row in rows:
        grouped.setdefault(key(row), []).append(row)
    return grouped


async def import_domain(driver) -> ImportStats:
    """导入 Domain KG, 返回导入实体/关系数。全部 MERGE, 幂等。"""
    entities = load_csv(ROOT / "data" / "domain_entities.csv", ENTITY_COLS)
    relations = load_csv(ROOT / "data" / "domain_relations.csv", RELATION_COLS)
    validate_no_drug(entities, relations)

    stats = ImportStats(entities=len(entities), relations=len(relations), domain_nodes=0, domain_relations=0)

    async with driver.session() as session:
        for label, group in group_by(entities, lambda e: e["type"]).items():
            rows = [{"name": e["name"], "props": props(e, ENTITY_EXCLUDED)} for e in group]
            await session.run(entity_batch_cypher(label), rows=rows)
        for (rel_name, from_type, to_type), group in group_by(
            relations, lambda r: (r["relation"], r["from_type"], r["to_type"])
        ).items():
            rows = [
                {
                    "from_name": r["from_name"],
                    "to_name": r["to_name"],
                    "props": props(r, RELATION_EXCLUDED),
                }
                for r in group
            ]
            await session.run(
                relation_batch_cypher(rel_name, from_type, to_type), rows=rows
            )

        node_count = await session.run(
            "MATCH (n:Domain) RETURN count(n) AS c"
        )
        rel_count = await session.run(
            "MATCH (a:Domain)-[r]->(b:Domain) RETURN count(r) AS c"
        )
        stats = ImportStats(
            entities=stats.entities,
            relations=stats.relations,
            domain_nodes=(await node_count.single())["c"],
            domain_relations=(await rel_count.single())["c"],
        )
    return stats


async def init_patient_schema(driver) -> None:
    """Patient KG 约束/索引初始化 (幂等)。写入实现见 T4 memory 模块。

    患者主节点唯一键为 name (schema: Patient:Patient {name, age})。
    """
    async with driver.session() as session:
        await session.run(
            "CREATE CONSTRAINT patient_name_unique IF NOT EXISTS "
            "FOR (p:Patient) REQUIRE p.name IS UNIQUE"
        )
        await session.run(
            "CREATE INDEX patient_timestamp_idx IF NOT EXISTS "
            "FOR (n:Patient) ON (n.timestamp)"
        )


async def main() -> int:
    load_dotenv(ROOT / ".env")
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init-patient", action="store_true", help="同时初始化 Patient KG 约束")
    args = parser.parse_args()

    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    try:
        stats = await import_domain(driver)
        print(f"导入完成: 实体 {stats.entities} 条, 关系 {stats.relations} 条")
        print(f"图谱现状: Domain 节点 {stats.domain_nodes} 个, Domain 关系 {stats.domain_relations} 条")
        if args.init_patient:
            await init_patient_schema(driver)
            print("Patient KG 约束初始化完成")
    finally:
        await driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))