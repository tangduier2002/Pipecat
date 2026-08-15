"""monitor 路由函数 — 生命体征记录 + 异常检测 + emergency 触发。

数据流: triage 实体 → 语音数值确认 → memory 写入 → 异常检测 → emergency 并行扇出。
无编排框架 (ADR-0002): 并行分支用 asyncio.gather, 路径上无任何编排框架。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.memory import PatientRecord, RecordItem, items_to_nodes, validate, write
from app.triage import CRISIS_KEYWORDS, EMERGENCY_SYMPTOM_KEYWORDS

# 异常阈值 (docs/agents/monitor.md)
EMERGENCY_SYSTOLIC = 180
EMERGENCY_DIASTOLIC = 120

# 确认话术 (适老化: 明确、缓慢、单句)
CONFIRM_TEMPLATE = "您说的是收缩压 {systolic}、舒张压 {diastolic}，对吗？"


@dataclass
class MonitorResult:
    reply: str
    emergency: bool = False
    crisis_kind: str | None = None      # hypertensive_crisis | psychological_crisis
    written: bool = False               # 是否已写入 Patient KG
    confirmation: bool = False          # 是否处于数值确认环节


@dataclass
class EmergencyBranch:
    """emergency 并行分支上下文。"""

    text: str
    entities: dict
    education_route: object = None
    motivation_route: object = None


async def _run_emergency_branch(branch: EmergencyBranch) -> str:
    """并行扇出 (asyncio.gather 双路): education 建议 + motivation 安抚。"""
    education_fn = branch.education_route or _placeholder_education
    motivation_fn = branch.motivation_route or _placeholder_motivation
    edu_reply, mot_reply = await asyncio.gather(
        education_fn(branch.text, branch.entities),
        motivation_fn(branch.text, branch.entities),
    )
    return merge_emergency_replies(edu_reply, mot_reply)


async def _placeholder_education(text: str, entities: dict) -> str:
    return "建议您尽快联系医生，或让家人陪同前往医院检查血压。"


async def _placeholder_motivation(text: str, entities: dict) -> str:
    return "别担心，我陪着你。先深呼吸，坐下来休息一会儿，我们慢慢来。"


def merge_emergency_replies(education_reply: str, motivation_reply: str) -> str:
    return f"{motivation_reply} {education_reply}"


def detect_crisis(text: str, entities: dict) -> tuple[bool, str | None]:
    """异常检测: 血压阈值 + 症状关键词 + 心理危机。返回 (是否触发, 危机类型)。"""
    if any(kw in text for kw in CRISIS_KEYWORDS):
        return True, "psychological_crisis"
    if any(kw in text for kw in EMERGENCY_SYMPTOM_KEYWORDS):
        return True, "hypertensive_crisis"
    if entities.get("systolic", 0) >= EMERGENCY_SYSTOLIC:
        return True, "hypertensive_crisis"
    if entities.get("diastolic", 0) >= EMERGENCY_DIASTOLIC:
        return True, "hypertensive_crisis"
    return False, None


def build_confirmation(entities: dict) -> str:
    """语音数值确认 (适老化)。"""
    sys_val = entities.get("systolic")
    dia_val = entities.get("diastolic")
    if sys_val is not None and dia_val is not None:
        return CONFIRM_TEMPLATE.format(systolic=sys_val, diastolic=dia_val)
    if sys_val is not None:
        return f"您说的是收缩压 {sys_val}，对吗？"
    if dia_val is not None:
        return f"您说的是舒张压 {dia_val}，对吗？"
    return "您说的是多少呢？再说一遍好吗？"


def is_confirmation_reply(user_text: str) -> bool:
    """数值确认答复判定 (适老化: 对/是/对呀/没错)。"""
    text = user_text.strip()
    return any(text.startswith(kw) for kw in ("对", "是", "嗯", "没错", "对呀", "是的"))


def is_denial_reply(user_text: str) -> bool:
    text = user_text.strip()
    return any(text.startswith(kw) for kw in ("不", "没有", "错了", "不是"))


async def monitor_route(
    user_text: str,
    entities: dict,
    driver,
    patient_id: str = "default",
    pending: dict | None = None,
    education_route=None,
    motivation_route=None,
) -> MonitorResult:
    """monitor 主流程。

    pending: 未确认的待写实体 (确认环节状态, 由调用方传入/接收)。
    education_route / motivation_route: T2/T7 路由函数, 缺省占位实现。
    确认写入为确定性构造 (结构化实体 → 记录), 不依赖 LLM。
    """
    pending = pending or {}

    # 数值确认环节: 上一次询问后用户答复
    if pending:
        if is_confirmation_reply(user_text):
            return await _write_and_check(pending, driver, patient_id, education_route, motivation_route)
        if is_denial_reply(user_text):
            return MonitorResult(
                reply="没关系，那我们重新说一遍数值好吗？",
                confirmation=True,
            )
        # 非确认答复 → 重新询问 (不写入)
        return MonitorResult(reply=build_confirmation(pending), confirmation=True)

    # 新记录: 抽取实体 → 语音确认 (不立即写入)
    if not entities:
        return MonitorResult(reply="您能告诉我血压数值吗？", confirmation=True)

    reply = build_confirmation(entities)
    return MonitorResult(reply=reply, confirmation=True, written=False)


async def _write_and_check(
    pending: dict,
    driver,
    patient_id: str,
    education_route,
    motivation_route,
) -> MonitorResult:
    """确认后: memory 写入 + 异常检测 → emergency 或简短确认。

    结构化实体 (血压/心率/体重) 确定性构造记录走管道, 不依赖 LLM 抽取
    (真实验收发现: 无 LLM 时 extract 返回空, 写入静默丢失)。
    """
    record = _entities_to_record(pending)
    validated = validate(record)
    nodes, crisis_items = items_to_nodes(validated)
    await write(nodes, patient_id, driver)

    text = _entities_to_text(pending)
    crisis = detect_crisis(text, pending)

    if crisis[0] or crisis_items:
        branch = EmergencyBranch(text=text, entities=pending, education_route=education_route, motivation_route=motivation_route)
        reply = await _run_emergency_branch(branch)
        return MonitorResult(reply=reply, emergency=True, crisis_kind=crisis[1], written=True)
    return MonitorResult(reply="好的，已经帮您记下了。", written=True)


def _entities_to_record(entities: dict) -> PatientRecord:
    """结构化实体 → PatientRecord (确定性, 无 LLM)。"""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    if "systolic" in entities or "diastolic" in entities:
        records.append(
            RecordItem(
                type="vitalsign", name="血压",
                value=float(entities.get("systolic") or 0),
                unit="mmHg", diastolic=entities.get("diastolic"),
                timestamp=ts,
            )
        )
    if "pulse" in entities:
        records.append(
            RecordItem(type="vitalsign", name="心率", value=float(entities["pulse"]), unit="次/分", timestamp=ts)
        )
    if "weight" in entities:
        records.append(
            RecordItem(type="vitalsign", name="体重", value=float(entities["weight"]), unit="kg", timestamp=ts)
        )
    return PatientRecord(records=records)


def _entities_to_text(entities: dict) -> str:
    """实体 → 抽取文本 (memory 管道输入)。"""
    parts = []
    if "systolic" in entities and "diastolic" in entities:
        parts.append(f"血压 {entities['systolic']}/{entities['diastolic']}")
    elif "systolic" in entities:
        parts.append(f"收缩压 {entities['systolic']}")
    elif "diastolic" in entities:
        parts.append(f"舒张压 {entities['diastolic']}")
    if "pulse" in entities:
        parts.append(f"心率 {entities['pulse']}")
    if "weight" in entities:
        parts.append(f"体重 {entities['weight']}")
    return "，".join(parts)