"""education 路由函数 — Domain KG 问答 (饮食/运动/症状)。

无药物接口 (合规红线)。图谱无匹配 → NVC 拒答模板三, 无 LLM 兜底 (ADR-0002)。
答案由图谱结果 + 固定模板生成, 全程确定性, 不调用 LLM。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# 免责声明: 每条答案必带
DISCLAIMER = "本回答仅供参考，具体请咨询医生。"

# NVC 拒答模板三 (一般超范围) — 文案来自 docs/agents/guard.md
NVC_REJECT_TEMPLATE_3 = (
    "这个问题我很想帮您，但我的知识够不到专业的边界，说错了反而耽误您。"
    "问医生或药师最稳当。我先把您的问题记下来，下次见面时提醒您带去问。"
    "咱们先做点别的——您最近有什么想聊的？"
)


def build_nvc_reject(user_text: str) -> str:
    """NVC 拒答模板三 (一般超范围)。user_text 保留备用, 当前模板为固定文案。"""
    return NVC_REJECT_TEMPLATE_3


def _join_names(names: list[str]) -> str:
    return "、".join(names) if names else ""


@dataclass(frozen=True)
class QueryHandler:
    cypher: str
    params: Callable[[dict], dict]
    answer: Callable[[dict, dict], str]


def _food_dual_params(entities: dict) -> dict:
    return {"disease": entities["disease"], "food": entities["food"]}


def _disease_params(entities: dict) -> dict:
    return {"disease": entities["disease"]}


def _food_answer(row: dict, entities: dict) -> str:
    side = row.get("side")
    food = row["food"]
    disease = row["disease"]
    if side == "recommended":
        body = f"{disease}朋友，{food}在推荐食物里，可以适量食用，对身体有好处。"
    elif side == "forbidden":
        body = f"{disease}朋友，{food}属于需要留意避开的食物，建议少吃或不吃。"
    else:
        body = f"关于{disease}和{food}，目前没有查到明确的推荐或禁忌记录，适量即可。"
    return f"{body} {DISCLAIMER}"


def _exercise_answer(row: dict, entities: dict) -> str:
    rec = _join_names(row.get("recommended", []))
    forb = _join_names(row.get("forbidden", []))
    disease = row["disease"]
    parts = []
    if rec:
        parts.append(f"{disease}适合的运动有：{rec}。")
    else:
        parts.append(f"关于{disease}适合的运动，目前没有查询到记录。")
    if forb:
        parts.append(f"需要注意避免的运动：{forb}。")
    if parts:
        parts.append(f"坚持规律运动，对{row['disease']}的日常管理很有帮助。")
    parts.append(DISCLAIMER)
    return " ".join(parts)


def _symptom_answer(row: dict, entities: dict) -> str:
    symptoms = _join_names(row.get("symptoms", []))
    disease = row["disease"]
    if symptoms:
        body = f"{disease}可能出现这些情况：{symptoms}。每个人的感受不同，别对号入座，有不舒服及时告诉家人或医生。"
    else:
        body = f"关于{disease}的症状，目前没有查询到记录。"
    return f"{body} {DISCLAIMER}"


# 子意图 → 查询模板 + 答案生成器 (Cypher 均为图谱内查询, 无注入面)
INTENT_HANDLERS: dict[str, QueryHandler] = {
    "FOOD_QUERY": QueryHandler(
        cypher="""
        MATCH (d:Domain:Disease {name: $disease}), (f:Domain:Food {name: $food})
        OPTIONAL MATCH (d)-[r:推荐食物]->(f)
        OPTIONAL MATCH (d)-[r2:禁忌食物]->(f)
        RETURN d.name AS disease, f.name AS food,
               CASE WHEN r IS NOT NULL THEN 'recommended'
                    WHEN r2 IS NOT NULL THEN 'forbidden'
                    ELSE 'unknown' END AS side
        """,
        params=_food_dual_params,
        answer=_food_answer,
    ),
    "EXERCISE_QUERY": QueryHandler(
        cypher="""
        MATCH (d:Domain:Disease {name: $disease})
        OPTIONAL MATCH (d)-[:推荐运动]->(e:Domain:Exercise)
        OPTIONAL MATCH (d)-[:禁忌运动]->(f:Domain:Exercise)
        WITH d, collect(DISTINCT e) AS rec, collect(DISTINCT f) AS forb
        RETURN d.name AS disease,
               [x IN rec | x.name] AS recommended,
               [x IN forb | x.name] AS forbidden
        """,
        params=_disease_params,
        answer=_exercise_answer,
    ),
    "SYMPTOM_QUERY": QueryHandler(
        cypher="""
        MATCH (d:Domain:Disease {name: $disease})
        OPTIONAL MATCH (d)-[:典型症状]->(s:Domain:Symptom)
        RETURN d.name AS disease, collect(DISTINCT s.name) AS symptoms
        """,
        params=_disease_params,
        answer=_symptom_answer,
    ),
}


# 模块级默认 kg (由 FastAPI lifespan 初始化), 便于路由层无参调用
_default_kg = None


def set_default_kg(kg) -> None:
    global _default_kg
    _default_kg = kg


async def education_route(user_text: str, entities: dict, kg=None) -> str:
    """Education 路由: 子意图 → Cypher → 图谱结果 → 模板化答案。

    kg: 提供 async query(cypher, params) -> list[dict] 的对象
    (DomainKGService 或测试替身); 缺省用 set_default_kg 注册的实例。
    """
    kg = kg if kg is not None else _default_kg
    if kg is None:
        return build_nvc_reject(user_text)
    sub_intent = entities.get("sub_intent")
    handler = INTENT_HANDLERS.get(sub_intent)
    if handler is None:
        return build_nvc_reject(user_text)
    try:
        result = await kg.query(handler.cypher, handler.params(entities))
    except KeyError:
        # 实体缺失 (如没有 disease / food) → 拒答, 不编造
        return build_nvc_reject(user_text)
    if not result:
        return build_nvc_reject(user_text)
    return handler.answer(result[0], entities)