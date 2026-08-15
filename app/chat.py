"""系统对话入口 — 编排 triage / monitor / education / motivation / guard。

无编排框架 (ADR-0002): 串行 async 路由 + emergency 并行 asyncio.gather。
所有输出必经 guard_check 才返回 (进 TTS 前的最后一道)。
"""

from __future__ import annotations

import asyncio
import logging

from app.education import education_route
from app.guard import notify_caregivers, recent_vital_trend, write_crisis_event
from app.guard_check import CRISIS_REPLY, guard_check
from app.monitor import MonitorResult, monitor_route
from app.motivation import motivation_route
from app.triage import CRISIS_KEYWORDS, triage_route

logger = logging.getLogger(__name__)

# motivation 意图 → Prompt 映射 (triage intent 直接透传)
_MOTIVATION_INTENTS = {"emotional_support", "lifestyle_coaching", "general_chat"}


class SystemContext:
    """运行时依赖容器 (driver / llm / kg / 患者), 由 FastAPI lifespan 组装。"""

    def __init__(self, driver, llm, kg, patient_id: str = "default"):
        self.driver = driver
        self.llm = llm
        self.kg = kg
        self.patient_id = patient_id
        self.pending: dict | None = None  # monitor 数值确认状态
        self.memory_snapshot: dict = {}


def _education_for_emergency(ctx: SystemContext):
    """emergency 分支 education 闭包 (绑定 ctx, 无全局状态)。"""

    async def route(text: str, entities: dict) -> str:
        raw = await education_route(text, entities, kg=ctx.kg)
        _, reviewed = guard_check(raw)
        return reviewed

    return route


def _motivation_for_emergency(ctx: SystemContext):
    """emergency 分支 motivation 闭包 (安抚, 压力管理 Prompt)。"""

    async def route(text: str, entities: dict) -> str:
        raw = await motivation_route(text, "emotional_support", ctx.llm, ctx.memory_snapshot)
        _, reviewed = guard_check(raw)
        return reviewed

    return route


async def _run_crisis_protocol(ctx: SystemContext, result: MonitorResult) -> None:
    """guard 确认后: CrisisEvent 写入 + 异步邮件通知 (后台, 不阻塞对话)。"""
    crisis_type = result.crisis_kind or "hypertensive_crisis"
    sys_val = ctx.pending.get("systolic") if ctx.pending else None
    dia_val = ctx.pending.get("diastolic") if ctx.pending else None
    try:
        await write_crisis_event(
            ctx.driver, ctx.patient_id, crisis_type,
            trigger_text=result.reply[:200],
            systolic=sys_val,
            diastolic=dia_val,
        )
    except Exception as exc:
        logger.error("CrisisEvent 写入失败: %s", exc)
    # 邮件通知独立于对话主流程 (Best-effort, 失败已内部重试)
    asyncio.create_task(_notify_in_background(ctx, crisis_type, result))


async def _notify_in_background(ctx: SystemContext, crisis_type: str, result: MonitorResult) -> None:
    try:
        trend = await recent_vital_trend(ctx.driver, ctx.patient_id)
        await notify_caregivers(crisis_type, result.reply[:200], vital_trend=trend)
    except Exception as exc:
        logger.error("危机通知失败: %s", exc)


async def handle_user_input(user_text: str, ctx: SystemContext) -> str:
    """单轮对话入口: 输入 → 路由 → 组件 → guard → 输出文本 (进 TTS)。

    pending 非空时 (monitor 数值确认环节) 直接进入确认流程, 不重新分类意图。
    """
    edu_for_emergency = _education_for_emergency(ctx)
    mot_for_emergency = _motivation_for_emergency(ctx)

    # 数值确认环节优先: 确认/否认答复不经过 triage 分类
    if ctx.pending:
        result = await monitor_route(
            user_text, {}, ctx.llm, ctx.driver,
            patient_id=ctx.patient_id, pending=ctx.pending,
            education_route=edu_for_emergency,
            motivation_route=mot_for_emergency,
        )
        if result.emergency:
            ctx.pending = None
            await _run_crisis_protocol(ctx, result)
        elif result.confirmation:
            ctx.pending = ctx.pending if result.written is False else None
        else:
            ctx.pending = None
        return result.reply

    route = await triage_route(user_text, ctx.llm)
    if route.reject is not None:
        return route.reject

    target = route.target

    if target == "data_record":
        result = await monitor_route(
            user_text, route.entities, ctx.llm, ctx.driver,
            patient_id=ctx.patient_id, pending=None,
            education_route=edu_for_emergency,
            motivation_route=mot_for_emergency,
        )
        ctx.pending = route.entities if result.confirmation and not result.written else None
        if result.emergency:
            await _run_crisis_protocol(ctx, result)
        return result.reply

    if target == "emergency":
        return await _handle_emergency(user_text, ctx, edu_for_emergency, mot_for_emergency)

    if target == "knowledge_query":
        raw = await education_route(user_text, route.entities, kg=ctx.kg)
        _, reviewed = guard_check(raw)
        return reviewed

    if target in _MOTIVATION_INTENTS:
        raw = await motivation_route(user_text, target, ctx.llm, ctx.memory_snapshot)
        _, reviewed = guard_check(raw)
        return reviewed

    # 未知意图兜底
    return "我没太听清，您能再说一遍吗？"


async def _handle_emergency(user_text: str, ctx: SystemContext, edu_route, mot_route) -> str:
    """emergency 分支: 心理危机 → 危机话术 + 热线; 否则并行双路 (各自已过 guard)。"""
    if any(kw in user_text for kw in CRISIS_KEYWORDS):
        result = MonitorResult(reply=CRISIS_REPLY, emergency=True, crisis_kind="psychological_crisis")
        await _run_crisis_protocol(ctx, result)
        return CRISIS_REPLY

    edu_reply, mot_reply = await asyncio.gather(
        edu_route(user_text, {}),
        mot_route(user_text, {}),
    )
    merged = f"{mot_reply} {edu_reply}"
    result = MonitorResult(reply=merged, emergency=True, crisis_kind="hypertensive_crisis")
    await _run_crisis_protocol(ctx, result)
    return merged