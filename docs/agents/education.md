# education — Domain KG 问答

**运行时**：LangGraph 节点（`education_agent.py`）
**数据源**：Domain KG（Neo4j 静态医学子图，`Domain:*` 标签命名空间）

> 本文档吸收原 `语音多智能体.md` 的知识图谱设计，按 MRP 决策裁剪全部药物相关内容（见 `docs/adr/0001-cdsmp-mapping.md`）。

## 职责

1. 回答饮食、运动、症状类知识问题（`knowledge_query` 意图）
2. 基于 Domain KG 结构化查询，避免 RAG 语义检索偏差
3. **无药物问答**：药物类问题由 triage 拦截，Education 不处理、也不暴露任何药物查询接口

## Domain KG 设计

### 实体类型（6 类，不含 Drug）

| 实体类型 | 示例 |
|---------|------|
| 疾病 Disease | 高血压、原发性高血压 |
| 症状 Symptom | 头晕、头痛、心悸 |
| 食物 Food | 香蕉、咸菜、芹菜 |
| 运动 Exercise | 快走、游泳 |
| 检查 Exam | 血压测量、心电图 |
| 科室 Department | 心内科 |

### 关系类型（去掉药品相关 4 种，保留 8 种）

| 关系 | 示例三元组 |
|------|-----------|
| 疾病-典型症状 | (高血压)-[典型症状]->(头晕) |
| 疾病-推荐食物 | (高血压)-[推荐食物]->(芹菜) |
| 疾病-禁忌食物 | (高血压)-[禁忌食物]->(咸菜) |
| 疾病-推荐运动 | (高血压)-[推荐运动]->(快走) |
| 疾病-禁忌运动 | (高血压)-[禁忌运动]->(剧烈跑跳) |
| 症状-关联疾病 | (头痛)-[关联疾病]->(高血压) |
| 科室-收治疾病 | (心内科)-[收治疾病]->(高血压) |
| 检查-诊断疾病 | (血压测量)-[诊断疾病]->(高血压) |

> 相比原版 12 种关系，删除了：药品-适应症、药品-禁忌症、药品-食物相互作用、病因-导致疾病（病因关系涉及高血压成因讨论，MRP 不展开，可后续补回）。

## Neo4j 数据模型（Cypher）

```cypher
// Domain 子图创建示例（标签带 Domain: 前缀，与 Patient 子图隔离）
CREATE (d:Domain:Disease {
    name: '高血压',
    icd10: 'I10',
    description: '以体循环动脉血压增高为主要特征的临床综合征'
})

CREATE (f:Domain:Food {name: '咸菜', sodium_per_100g: 2000})

MATCH (d:Domain:Disease {name: '高血压'}), (f:Domain:Food {name: '咸菜'})
CREATE (d)-[:禁忌食物 {
    risk_level: 'high',
    evidence: '高钠导致水钠潴留，升高血压'
}]->(f)
```

## 核心代码模块

### kg_service.py（Neo4j 异步查询服务）

```python
# knowledge_graph/kg_service.py
from neo4j import AsyncGraphDatabase

class DomainKGService:
    def __init__(self, uri, user, password):
        self.driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def query(self, cypher_query: str, params: dict = None):
        async with self.driver.session() as session:
            result = await session.run(cypher_query, params)
            return [record.data() for record in await result.data()]

    async def get_disease_info(self, disease_name: str):
        """获取疾病的基本信息及关联实体"""
        query = """
        MATCH (d:Domain:Disease {name: $name})
        OPTIONAL MATCH (d)-[r:典型症状]->(s:Domain:Symptom)
        OPTIONAL MATCH (d)-[r2:推荐食物]->(f:Domain:Food)
        OPTIONAL MATCH (d)-[r3:禁忌食物]->(f2:Domain:Food)
        OPTIONAL MATCH (d)-[r4:推荐运动]->(e:Domain:Exercise)
        RETURN d, collect(DISTINCT s.name) as symptoms,
               collect(DISTINCT f.name) as recommended_foods,
               collect(DISTINCT f2.name) as forbidden_foods,
               collect(DISTINCT e.name) as recommended_exercises
        """
        return await self.query(query, {"name": disease_name})
```

### 意图识别（Knowledge 专用）

Education 只处理 `knowledge_query` 下的细粒度子意图（由 triage 粗分类后进入）：

| 子意图 | 示例 | 查询模板 |
|--------|------|---------|
| `FOOD_QUERY` | 高血压能吃香蕉吗？ | 推荐/禁忌食物 |
| `EXERCISE_QUERY` | 高血压适合什么运动？ | 推荐/禁忌运动 |
| `SYMPTOM_QUERY` | 高血压会头晕吗？ | 典型症状 |

### Education Agent 主逻辑

```python
# agents/education_agent.py（LangGraph 节点）
async def education_node(state: GraphState) -> GraphState:
    entities = state["entities"]           # 来自 triage
    sub_intent = entities.get("sub_intent")
    handler = INTENT_HANDLERS[sub_intent] # FOOD/EXERCISE/SYMPTOM → Cypher
    result = await kg.query(handler.cypher, handler.params(entities))
    if not result:
        state["reply"] = await fallback_llm(entities)  # 图谱无结果时 LLM 兜底（带免责声明）
        return state
    context = build_context(result, entities)
    state["reply"] = await generate_answer(context, entities)
    return state
```

答案生成要求（沿用 v1）：

1. 答案必须基于图谱检索结果，不编造
2. 语气友善，鼓励患者
3. 结尾医疗免责声明：「本回答仅供参考，具体请咨询医生」

## 数据导入

```bash
# 启动 Neo4j（Domain + Patient 共用单实例）
docker run -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:latest

# 批量导入（实体 CSV + 关系 CSV）
python scripts/import_kg.py
```

初始数据规模参考：50+ 实体、100+ 关系（v2 验收标准）。

## 验收标准

- 输入「高血压适合什么运动？」→ 返回基于图谱的自然语言回答 + 免责声明
- 图谱无匹配时走 LLM 兜底（同样带免责声明）
- 药物类输入永不进入本节点（triage 已拦截）