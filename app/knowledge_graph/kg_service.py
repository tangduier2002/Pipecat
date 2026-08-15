"""Neo4j 异步查询服务 (Domain KG)。

复用单连接驱动实例, 全异步。T2 交付; T1 已建 Domain KG schema。
"""

from __future__ import annotations

from neo4j import AsyncGraphDatabase


class DomainKGService:
    """Domain KG 只读查询服务。调用方负责生命周期 (app lifespan 中 close)。"""

    def __init__(self, uri: str, user: str, password: str):
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def close(self) -> None:
        await self._driver.close()

    async def query(self, cypher_query: str, params: dict | None = None) -> list[dict]:
        """执行 Cypher, 返回记录列表 (dict)。"""
        params = params or {}
        async with self._driver.session() as session:
            result = await session.run(cypher_query, params)
            return [record.data() async for record in result]

    async def get_disease_info(self, disease_name: str) -> dict | None:
        """疾病基础信息及关联实体 (症状/推荐与禁忌食物/推荐运动)。"""
        query = """
        MATCH (d:Domain:Disease {name: $name})
        OPTIONAL MATCH (d)-[:典型症状]->(s:Domain:Symptom)
        OPTIONAL MATCH (d)-[:推荐食物]->(rf:Domain:Food)
        OPTIONAL MATCH (d)-[:禁忌食物]->(ff:Domain:Food)
        OPTIONAL MATCH (d)-[:推荐运动]->(re:Domain:Exercise)
        WITH d, collect(DISTINCT s) AS symptoms, collect(DISTINCT rf) AS rec_foods,
             collect(DISTINCT ff) AS forb_foods, collect(DISTINCT re) AS rec_exercises
        RETURN d.name AS name, d.icd10 AS icd10, d.description AS description,
               [x IN symptoms | x.name] AS symptoms,
               [x IN rec_foods | x.name] AS recommended_foods,
               [x IN forb_foods | x.name] AS forbidden_foods,
               [x IN rec_exercises | x.name] AS recommended_exercises
        """
        rows = await self.query(query, {"name": disease_name})
        return rows[0] if rows else None