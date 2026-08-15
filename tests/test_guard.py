"""T6 guard 测试: 五条规则 + 拒答模板 + 危机通知 (mock SMTP)。"""

from __future__ import annotations

import pytest

from app.guard import notify_caregivers, recent_vital_trend, write_crisis_event
from app.guard_check import (
    CRISIS_REPLY,
    TEMPLATE_1_DRUG_INTERACTION,
    TEMPLATE_2_DRUG_ADJUST,
    TEMPLATE_3_GENERAL,
    Threshold,
    check_emergency_threshold,
    guard_check,
)
from tests.conftest import FakeDriver


def test_pass_normal_output():
    action, text = guard_check("建议您每天快走 30 分钟，配合清淡饮食。")
    assert action == "PASS"
    assert text == "建议您每天快走 30 分钟，配合清淡饮食。"


def test_reject_drug_adjust_uses_template2():
    action, text = guard_check("你该停药了")
    assert action == "REJECT"
    assert text == TEMPLATE_2_DRUG_ADJUST


def test_reject_drug_interaction_uses_template1():
    action, text = guard_check("降压药和西柚一起吃没问题")
    assert action == "REJECT"
    assert text == TEMPLATE_1_DRUG_INTERACTION


def test_reject_generic_drug_mention_uses_template3():
    action, text = guard_check("这个药效果不错")
    assert action == "REJECT"
    assert text == TEMPLATE_3_GENERAL


def test_rewrite_scare_language():
    action, text = guard_check("不吃药会中风，还会死")
    # ticket 允许: REJECT (规则 2 优先) 或 REWRITE (规则 1)
    if action == "REJECT":
        assert text == TEMPLATE_2_DRUG_ADJUST
    else:
        assert action == "REWRITE"
        assert "中风" not in text
        assert "会死" not in text


def test_rewrite_diagnosis():
    action, text = guard_check("你得了高血压，需要治疗")
    assert action == "REWRITE"
    assert "你得了" not in text


def test_emergency_crisis_phrase():
    action, text = guard_check("我不想活了")
    assert action == "EMERGENCY"
    assert "12356" in text  # 心理热线


def test_emergency_symptom_phrase():
    action, text = guard_check("我胸口好痛")
    assert action == "EMERGENCY"


def test_emergency_wins_over_drug():
    """规则 5 > 规则 2: 心理危机优先。"""
    action, _ = guard_check("不想活了，药也不想吃了")
    assert action == "EMERGENCY"


def test_threshold_check():
    assert check_emergency_threshold(185, 95) is True
    assert check_emergency_threshold(150, 120) is True
    assert check_emergency_threshold(179, 119) is False
    assert check_emergency_threshold(None, None) is False


async def test_write_crisis_event(monkeypatch):
    driver = FakeDriver()
    await write_crisis_event(driver, "张大爷", "hypertensive_crisis", "收缩压 185", systolic=185, diastolic=100)
    cypher, params = driver.calls[0]
    assert "CrisisEvent" in cypher
    assert "经历" in cypher
    assert params["props"]["crisis_type"] == "hypertensive_crisis"
    assert params["props"]["systolic"] == 185


async def test_notify_caregivers_mock_smtp(monkeypatch):
    from dataclasses import replace

    from app import guard

    sent = []

    async def fake_send(message, **kwargs):
        sent.append((message["To"], message["Subject"], message.get_content()))

    monkeypatch.setattr(
        guard,
        "settings",
        replace(
            guard.settings,
            smtp_host="smtp.test",
            smtp_port=587,
            caregiver_email="son@example.com",
            community_doctor_email="doc@example.com",
        ),
    )
    monkeypatch.setattr("aiosmtplib.send", fake_send)

    caregiver_ok, doctor_ok = await notify_caregivers("hypertensive_crisis", "收缩压 185", systolic=185)
    assert caregiver_ok and doctor_ok
    assert len(sent) == 2
    assert sent[0][0] == "son@example.com"
    assert "[紧急]" in sent[0][1]
    assert "185" in sent[0][2]


async def test_notify_skipped_when_smtp_unconfigured(monkeypatch):
    from dataclasses import replace

    from app import guard

    monkeypatch.setattr(
        guard,
        "settings",
        replace(
            guard.settings,
            smtp_host="",
            caregiver_email="son@example.com",
            community_doctor_email="doc@example.com",
        ),
    )
    caregiver_ok, doctor_ok = await notify_caregivers("hypertensive_crisis", "触发文本")
    assert caregiver_ok is False and doctor_ok is False


async def test_notify_retries_on_failure(monkeypatch):
    from dataclasses import replace

    from app import guard

    attempts = {"n": 0}

    async def failing_send(message, **kwargs):
        attempts["n"] += 1
        raise OSError("smtp down")

    monkeypatch.setattr(
        guard,
        "settings",
        replace(
            guard.settings,
            smtp_host="smtp.test",
            smtp_port=587,
            caregiver_email="son@example.com",
            community_doctor_email="doc@example.com",
        ),
    )
    monkeypatch.setattr("aiosmtplib.send", failing_send)

    caregiver_ok, _ = await notify_caregivers("hypertensive_crisis", "触发文本")
    assert caregiver_ok is False
    # 两个收件人各尝试 2 次 (首试 + 重试一次)
    assert attempts["n"] == 4


async def test_recent_vital_trend_queries_driver():
    driver = FakeDriver()
    trend = await recent_vital_trend(driver, "张大爷")
    assert isinstance(trend, str)
    cyphers = " ".join(c for c, _ in driver.calls)
    assert "VitalSign" in cyphers


def test_reply_never_contains_drug_after_guard():
    """合规红线: guard 处理后的输出不含用药指令内容。

    注: NVC 拒答模板本身含"药师"/"用药"字样 (合规话术设计),
    断言目标是拦截用药指令词, 而非裸"药"字。
    """
    for text in ("不吃药会中风", "加一片降压药", "你该停药了"):
        action, out = guard_check(text)
        assert action in ("REJECT", "REWRITE")
        for kw in ("加一片", "停药", "会死"):
            assert kw not in out