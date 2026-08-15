"""memory 模块 — 动态记忆四步管道 (extract → validate → merge → write)。

替代 Zep/Graphiti (ADR-0002): 纯 LLM 调用 + Neo4j 驱动 + 规则逻辑, 无外部框架。
schema 固定四类 (VitalSign / Symptom / Emotion / LifeEvent), 无 Medication 写入路径。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

logger = logging.getLogger(__name__)

RecordType = Literal["vitalsign", "symptom", "emotion", "lifeevent"]
# type -> Patient KG 标签
TYPE_LABELS: dict[str, str] = {
    "vitalsign": "VitalSign",
    "symptom": "Symptom",
    "emotion": "Emotion",
    "lifeevent": "LifeEvent",
}
# type -> 与患者主节点的关系
TYPE_RELATIONS: dict[str, str] = {
    "vitalsign": "测量_AT",
    "symptom": "经历",
    "emotion": "经历",
    "lifeevent": "经历",
}
# 追加式类型 (不合并, 时序保留); VitalSign 按 天 聚合
APPEND_ONLY = {"symptom", "emotion", "lifeevent"}

# 血压合理性范围 (范围外 → 标记可疑, 不写入)
BP_RANGES = {"systolic": (60, 260), "diastolic": (40, 160)}
# 危机阈值预标记 (供 T6 guard 确认)
CRISIS_SYSTOLIC = 180
CRISIS_DIASTOLIC = 120

# LLM 抽取固定 schema (不含 Medication; prompt 明确禁止)
EXTRACT_SYSTEM_PROMPT = (
    "你是健康助手的记忆抽取器。从用户对话中抽取健康相关信息, 只输出 JSON: "
    '{"records": [...]}。records 每项形如: '
    '{"type": "vitalsign", "name": "血压", "value": 155, "unit": "mmHg", "timestamp": "ISO时间"}, '
    '{"type": "emotion", "name": "焦虑", "intensity": 0.8, "timestamp": "ISO时间"}, '
    '{"type": "symptom", "name": "头晕", "severity": 0.6, "timestamp": "ISO时间"}, '
    '{"type": "lifeevent", "name": "家庭聚会", "description": "...", "timestamp": "ISO时间"}。'
    "type 只能是 vitalsign / symptom / emotion / lifeevent。"
    "血压记录须含 value (收缩压) 与可选 diastolic 字段。"
    "绝对禁止抽取任何药物、用药、剂量相关内容。无相关信息时输出 {\"records\": []}。"
)


@dataclass
class RecordItem:
    """单条抽取记录 (写入前形态)。"""

    type: RecordType
    name: str
    timestamp: str
    value: float | None = None
    unit: str | None = None
    diastolic: float | None = None
    severity: float | None = None
    intensity: float | None = None
    description: str | None = None


@dataclass
class PatientRecord:
    records: list[RecordItem] = field(default_factory=list)


@dataclass
class ValidatedItem:
    """校验后形态: 决定是否写入 + 危机预标记。"""

    item: RecordItem
    ok: bool = True           # False → 可疑值, 不写入
    reason: str | None = None
    crisis_pending: bool = False


@dataclass
class Node:
    """待写节点 (Patient KG)。"""

    label: str
    props: dict
    relation: str
    merge_key: str | None = None  # 非 None → 按 (merge_key, date) MERGE; None → CREATE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: str | None) -> str:
    """时间戳合法性: 缺失/非法 → 当前时间; 未来时间 → 当前时间 (钳制)。"""
    if not value:
        return _now_iso()
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return _now_iso()
    if ts > datetime.now(ts.tzinfo or timezone.utc):
        return _now_iso()
    return value


def _bp_ok(value: float, kind: str) -> bool:
    lo, hi = BP_RANGES[kind]
    return lo <= value <= hi


async def extract(text: str, llm) -> PatientRecord:
    """LLM 单次调用 + 固定 JSON schema 抽取。LLM 失败 → 空记录 (不阻塞对话)。"""
    data = await llm.chat_json(EXTRACT_SYSTEM_PROMPT, text)
    records: list[RecordItem] = []
    if data and isinstance(data.get("records"), list):
        for raw in data["records"]:
            if not isinstance(raw, dict) or raw.get("type") not in TYPE_LABELS:
                continue
            records.append(
                RecordItem(
                    type=raw["type"],
                    name=str(raw.get("name", "")).strip(),
                    timestamp=str(raw.get("timestamp", "")),
                    value=raw.get("value"),
                    unit=raw.get("unit"),
                    diastolic=raw.get("diastolic"),
                    severity=raw.get("severity"),
                    intensity=raw.get("intensity"),
                    description=raw.get("description"),
                )
            )
    # 防线: 丢弃药物内容 (LLM 违例时兜底)
    records = [r for r in records if not _is_drug_record(r)]
    return PatientRecord(records=records)


_DRUG_HINTS = ("药", "Medication", "drug")


def _is_drug_record(item: RecordItem) -> bool:
    name = (item.name or "") + (item.description or "")
    return any(h in name for h in _DRUG_HINTS)


def validate(record: PatientRecord) -> list[ValidatedItem]:
    """写前实时校验。

    - 血压范围合理性: 收缩压 60-260 / 舒张压 40-160, 范围外 → 可疑不写入
    - 危机阈值预标记: 收缩压 ≥ 180 或舒张压 ≥ 120 → crisis_pending (T6 guard 确认)
    - 时间戳: 缺失/未来 → 补当前时间
    """
    results: list[ValidatedItem] = []
    for item in record.records:
        item.timestamp = _parse_ts(item.timestamp)
        ok, reason = True, None
        crisis = False
        if item.type == "vitalsign":
            kind = _bp_kind(item.name)
            if kind and item.value is not None:
                if not _bp_ok(float(item.value), kind):
                    ok, reason = False, f"{item.name} 值超出合理范围 ({item.value})"
                elif float(item.value) >= CRISIS_SYSTOLIC:
                    crisis = True
            if kind == "systolic" and item.diastolic is not None:
                if not _bp_ok(float(item.diastolic), "diastolic"):
                    ok, reason = False, f"舒张压 值超出合理范围 ({item.diastolic})"
                elif float(item.diastolic) >= CRISIS_DIASTOLIC:
                    crisis = True
        results.append(ValidatedItem(item=item, ok=ok, reason=reason, crisis_pending=crisis))
    return results


def _bp_kind(name: str) -> str | None:
    if "收缩" in name or name in ("血压", "高压", "systolic"):
        return "systolic"
    if "舒张" in name or name in ("低压", "diastolic"):
        return "diastolic"
    return None


def _date_key(timestamp: str) -> str:
    return timestamp[:10]


def merge(existing: list[Node], record: PatientRecord) -> list[Node]:
    """增量合并。VitalSign 同 (name, date) 聚合保留最新; 其余追加。

    聚合规则: 收缩压/舒张压 同日 → 合并为单条血压记录 (值取最新, 历史保留在 history)。
    """
    merged: dict[str, Node] = {n.merge_key: n for n in existing if n.merge_key}
    appended: list[Node] = [n for n in existing if not n.merge_key]

    for item in record.records:
        label = TYPE_LABELS[item.type]
        relation = TYPE_RELATIONS[item.type]
        props = _node_props(item)
        if item.type == "vitalsign":
            date = _date_key(item.timestamp)
            key = f"VitalSign|{item.name}|{date}"
            if key in merged:
                old = merged[key]
                history = list(old.props.get("history", [])) + [old.props["value"]]
                merged[key] = Node(
                    label=label, props={**old.props, **props, "history": history},
                    relation=relation, merge_key=key,
                )
            else:
                merged[key] = Node(label=label, props=props, relation=relation, merge_key=key)
        else:
            appended.append(Node(label=label, props=props, relation=relation))
    return list(merged.values()) + appended


def _node_props(item: RecordItem) -> dict:
    props: dict = {"name": item.name, "timestamp": item.timestamp}
    for key in ("value", "unit", "diastolic", "severity", "intensity", "description"):
        if getattr(item, key) is not None:
            props[key] = getattr(item, key)
    if item.type == "vitalsign" and "unit" not in props:
        props["unit"] = "mmHg"
    return props


async def write(nodes: list[Node], patient_id: str, driver) -> None:
    """写 Patient KG。节点带 Patient: 标签前缀 + timestamp; 关系 测量_AT | 经历。"""
    if not nodes:
        return
    async with driver.session() as session:
        for node in nodes:
            await session.run(
                _write_cypher(node),
                patient_id=patient_id,
                props=node.props,
                relation=node.relation,
            )


def _write_cypher(node: Node) -> str:
    """按节点类型选择 MERGE (可聚合) 或 CREATE (追加) 写入语句。

    患者主节点用 MERGE 自举 (首次写入自动创建, 幂等)。
    相邻字符串字面量拼接后统一 .format(), 故 Cypher 字面花括号须双写转义,
    仅 {label} / {relation} 为格式化占位。
    """
    match_stmt = "MERGE (p:Patient {{name: $patient_id}}) "
    if node.merge_key:
        return (
            match_stmt
            + "MERGE (n:Patient:`{label}` {{name: $props.name, date: $props.date}}) "
            + "SET n += $props "
            + "MERGE (p)-[:`{relation}`]->(n)"
        ).format(label=node.label, relation=node.relation)
    return (
        match_stmt
        + "CREATE (n:Patient:`{label}`) "
        + "SET n = $props "
        + "MERGE (p)-[:`{relation}`]->(n)"
    ).format(label=node.label, relation=node.relation)


def items_to_nodes(validated: list[ValidatedItem]) -> tuple[list[Node], list[ValidatedItem]]:
    """通过校验的记录 → 待写节点; 返回 (nodes, crisis_items)。

    crisis_pending 项仍写入 (数据保留), 但单独返回供 T6 guard 确认。
    """
    nodes: list[Node] = []
    crisis: list[ValidatedItem] = []
    for v in validated:
        if not v.ok:
            logger.info("memory 校验拦截: %s", v.reason)
            continue
        item = v.item
        label = TYPE_LABELS[item.type]
        relation = TYPE_RELATIONS[item.type]
        props = _node_props(item)
        if item.type == "vitalsign":
            props["date"] = _date_key(item.timestamp)
            nodes.append(
                Node(label=label, props=props, relation=relation,
                     merge_key=f"VitalSign|{item.name}|{props['date']}")
            )
        else:
            nodes.append(Node(label=label, props=props, relation=relation))
        if v.crisis_pending:
            crisis.append(v)
    return nodes, crisis


async def memory_pipeline(text: str, patient_id: str, llm, driver, existing: list[Node] | None = None) -> tuple[list[Node], list[ValidatedItem]]:
    """四步管道入口: extract → validate → merge → write。

    返回 (写出的节点, crisis_pending 项)。existing 为进程缓存中的近期节点
    (None 时跳过 merge 聚合, 直接写入)。
    """
    record = await extract(text, llm)
    validated = validate(record)
    nodes, crisis = items_to_nodes(validated)
    if existing is not None:
        nodes = merge(existing, record)
    await write(nodes, patient_id, driver)
    return nodes, crisis


async def patient_summary(patient_id: str, driver) -> dict:
    """患者概要 (会话状态恢复): 姓名 + 近期 VitalSign 趋势 + 近期 Emotion。

    结果缓存到进程内存 (FastAPI 启动时加载), 供 motivation prompt 构建。
    """
    async with driver.session() as session:
        vital_rows = await session.run(
            """
            MATCH (p:Patient {name: $patient_id})-[:测量_AT]->(v:Patient:VitalSign)
            RETURN v.name AS name, v.value AS value, v.unit AS unit,
                   v.timestamp AS timestamp, v.diastolic AS diastolic
            ORDER BY v.timestamp DESC LIMIT 14
            """,
            patient_id=patient_id,
        )
        vitals = [r.data() async for r in vital_rows]

        emotion_rows = await session.run(
            """
            MATCH (p:Patient {name: $patient_id})-[:经历]->(e:Patient:Emotion)
            RETURN e.name AS name, e.intensity AS intensity, e.timestamp AS timestamp
            ORDER BY e.timestamp DESC LIMIT 10
            """,
            patient_id=patient_id,
        )
        emotions = [r.data() async for r in emotion_rows]

        event_rows = await session.run(
            """
            MATCH (p:Patient {name: $patient_id})-[:经历]->(e:Patient:LifeEvent)
            RETURN e.name AS name, e.timestamp AS timestamp, e.description AS description
            ORDER BY e.timestamp DESC LIMIT 10
            """,
            patient_id=patient_id,
        )
        events = [r.data() async for r in event_rows]

    return {
        "patient_id": patient_id,
        "vital_signs": vitals,
        "emotions": emotions,
        "life_events": events,
    }