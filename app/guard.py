"""guard 危机预案 — CrisisEvent 写入 + 异步邮件通知子女/社区医生。

EMERGENCY 确认后由 guard 驱动 (T6)。邮件经 FastAPI BackgroundTasks 后台执行,
失败重试一次, 不影响对话主流程。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.message import EmailMessage

import aiosmtplib

from app.config import settings

logger = logging.getLogger(__name__)

PSYCH_HOTLINE = "12356"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def write_crisis_event(driver, patient_id: str, crisis_type: str, trigger_text: str, systolic: int | None = None, diastolic: int | None = None) -> None:
    """写 CrisisEvent 节点到 Patient KG + 经历 关系。"""
    props = {
        "crisis_type": crisis_type,
        "timestamp": _now_iso(),
        "trigger_text": trigger_text,
    }
    if systolic is not None:
        props["systolic"] = systolic
    if diastolic is not None:
        props["diastolic"] = diastolic
    async with driver.session() as session:
        await session.run(
            "MATCH (p:Patient {name: $patient_id}) "
            "CREATE (e:Patient:CrisisEvent) "
            "SET e = $props "
            "MERGE (p)-[:经历]->(e)",
            patient_id=patient_id,
            props=props,
        )


def _email_subject(crisis_type: str) -> str:
    kind = "血压异常" if crisis_type == "hypertensive_crisis" else "心理危机"
    return f"[紧急] 家中老人健康预警：{kind}"


def _email_body(crisis_type: str, trigger_text: str, systolic: int | None, diastolic: int | None, trend: str = "") -> str:
    lines = [
        "您好，",
        "",
        f"系统检测到老人的健康出现紧急情况（类型：{crisis_type}），详情如下：",
        f"- 触发时间：{_now_iso()}",
        f"- 触发内容：{trigger_text}",
    ]
    if systolic is not None or diastolic is not None:
        lines.append(f"- 血压读数：{systolic or '—'}/{diastolic or '—'} mmHg")
    lines.append("- 系统已建议就医/安抚，对话侧已处理。")
    if trend:
        lines.append("")
        lines.append("近 7 天血压趋势摘要：")
        lines.append(trend)
    lines.append("")
    lines.append("请尽快联系老人确认情况。")
    return "\n".join(lines)


async def _send_email_with_retry(subject: str, body: str, recipients: list[str]) -> bool:
    """SMTP 发送, 失败重试一次。成功返回 True。"""
    if not settings.smtp_host or not recipients:
        logger.warning("SMTP 未配置或收件人为空, 跳过邮件通知")
        return False
    for attempt in (1, 2):
        try:
            message = EmailMessage()
            message["From"] = settings.smtp_user or "pipecat-coach@localhost"
            message["To"] = ", ".join(recipients)
            message["Subject"] = subject
            message.set_content(body)
            await aiosmtplib.send(
                message,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user or None,
                password=settings.smtp_password or None,
                start_tls=bool(settings.smtp_port == 587),
            )
            logger.info("危机邮件已发送: %s", recipients)
            return True
        except Exception as exc:
            logger.warning("邮件发送失败 (第 %d 次): %s", attempt, exc)
    return False


async def notify_caregivers(
    crisis_type: str,
    trigger_text: str,
    systolic: int | None = None,
    diastolic: int | None = None,
    vital_trend: str = "",
) -> tuple[bool, bool]:
    """异步通知子女 + 社区医生。返回 (子女是否成功, 医生是否成功)。"""
    subject = _email_subject(crisis_type)
    body = _email_body(crisis_type, trigger_text, systolic, diastolic)
    caregiver_ok = await _send_email_with_retry(subject, body, [settings.caregiver_email])

    doctor_body = _email_body(crisis_type, trigger_text, systolic, diastolic, trend=vital_trend)
    doctor_ok = await _send_email_with_retry(subject, doctor_body, [settings.community_doctor_email])
    return caregiver_ok, doctor_ok


async def recent_vital_trend(driver, patient_id: str, days: int = 7) -> str:
    """近 N 天 VitalSign 趋势摘要 (社区医生邮件用)。"""
    try:
        async with driver.session() as session:
            rows = await session.run(
                """
                MATCH (p:Patient {name: $patient_id})-[:测量_AT]->(v:Patient:VitalSign)
                WHERE v.timestamp >= datetime() - duration({days: $days})
                RETURN v.name AS name, v.value AS value, v.diastolic AS diastolic, v.timestamp AS timestamp
                ORDER BY v.timestamp DESC LIMIT 14
                """,
                patient_id=patient_id,
                days=days,
            )
            records = [r.data() async for r in rows]
    except Exception as exc:
        logger.warning("趋势查询失败: %s", exc)
        return ""
    if not records:
        return ""
    lines = [f"{r['name']} {r['value']}/{r['diastolic'] or '—'} ({r['timestamp']})" for r in records]
    return "\n".join(lines)