"""T5 monitor 测试: 确认流程、写入、emergency 并行扇出、阈值检测。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.memory import Node
from app.monitor import (
    build_confirmation,
    detect_crisis,
    is_confirmation_reply,
    is_denial_reply,
    merge_emergency_replies,
    monitor_route,
)
from tests.conftest import FakeDriver


class FakeLLM:
    def __init__(self, records: list | None = None):
        self._records = records or []

    @property
    def available(self) -> bool:
        return True

    async def chat_json(self, system: str, user: str) -> dict | None:
        return {"records": self._records}


def _now_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _bp_llm(systolic: int, diastolic: int):
    return FakeLLM(
        [
            {"type": "vitalsign", "name": "血压", "value": systolic, "diastolic": diastolic, "unit": "mmHg", "timestamp": _now_iso()}
        ]
    )


async def test_monitor_new_record_asks_confirmation_not_written():
    driver = FakeDriver()
    result = await monitor_route(
        "今天早上量血压 155/95",
        {"systolic": 155, "diastolic": 95},
        FakeLLM(),
        driver,
    )
    assert result.confirmation is True
    assert "155" in result.reply and "95" in result.reply
    assert result.written is False
    assert driver.calls == []  # 确认前不写入


async def test_monitor_confirmation_writes_and_replies():
    driver = FakeDriver()
    result = await monitor_route(
        "对",
        {"systolic": 155, "diastolic": 95},
        _bp_llm(155, 95),
        driver,
        pending={"systolic": 155, "diastolic": 95},
    )
    assert result.written is True
    assert result.emergency is False
    assert driver.calls  # 已发出写入查询
    cyphers = " ".join(c for c, _ in driver.calls)
    assert "VitalSign" in cyphers
    assert "Medication" not in cyphers


async def test_monitor_denial_does_not_write():
    driver = FakeDriver()
    result = await monitor_route(
        "不对，我说错了",
        {},
        FakeLLM(),
        driver,
        pending={"systolic": 155, "diastolic": 95},
    )
    assert result.confirmation is True
    assert result.written is False
    assert driver.calls == []


async def test_monitor_systolic_185_triggers_emergency():
    driver = FakeDriver()
    result = await monitor_route(
        "对",
        {"systolic": 185, "diastolic": 95},
        _bp_llm(185, 95),
        driver,
        pending={"systolic": 185, "diastolic": 95},
    )
    assert result.emergency is True
    assert result.crisis_kind == "hypertensive_crisis"
    # 占位双路输出已合并
    assert "别担心" in result.reply and "医院" in result.reply


async def test_monitor_crisis_keyword_triggers_psychological():
    driver = FakeDriver()
    result = await monitor_route(
        "对",
        {"systolic": 140, "diastolic": 90},
        FakeLLM(),
        driver,
        pending={"systolic": 140, "diastolic": 90},
    )
    # 数值正常, 无危机
    assert result.emergency is False
    crisis = detect_crisis("我最近不想活了", {})
    assert crisis == (True, "psychological_crisis")


def test_detect_crisis_thresholds():
    assert detect_crisis("正常血压", {"systolic": 179, "diastolic": 119}) == (False, None)
    assert detect_crisis("血压很高", {"systolic": 180, "diastolic": 100}) == (True, "hypertensive_crisis")
    assert detect_crisis("血压很高", {"systolic": 150, "diastolic": 120}) == (True, "hypertensive_crisis")
    assert detect_crisis("我胸口好痛", {}) == (True, "hypertensive_crisis")
    assert detect_crisis("我有点头晕", {}) == (False, None)  # 头晕不触发
    assert detect_crisis("活着没意思", {}) == (True, "psychological_crisis")


def test_build_confirmation_variants():
    assert "收缩压 155、舒张压 95" in build_confirmation({"systolic": 155, "diastolic": 95})
    assert "收缩压 155" in build_confirmation({"systolic": 155})
    assert "舒张压 95" in build_confirmation({"diastolic": 95})
    assert build_confirmation({})  # 兜底话术非空


def test_confirmation_and_denial_replies():
    assert is_confirmation_reply("对")
    assert is_confirmation_reply("是的，没错")
    assert is_denial_reply("不对")
    assert is_denial_reply("没有，错了")
    assert not is_confirmation_reply("不对")


def test_merge_emergency_replies_order():
    merged = merge_emergency_replies("建议去医院", "别担心")
    assert merged.index("别担心") < merged.index("建议去医院")


async def test_monitor_uses_gather_for_parallel_branches():
    """emergency 分支必须双路并行 (asyncio.gather)。"""
    import asyncio
    import inspect

    from app.monitor import _run_emergency_branch

    source = inspect.getsource(_run_emergency_branch)
    assert "asyncio.gather" in source

    calls = []

    async def edu(text, entities):
        await asyncio.sleep(0.05)
        calls.append("edu")
        return "建议就医"

    async def mot(text, entities):
        await asyncio.sleep(0.05)
        calls.append("mot")
        return "别担心"

    branch = __import__("app.monitor", fromlist=["EmergencyBranch"]).EmergencyBranch(
        text="血压 185", entities={"systolic": 185}, education_route=edu, motivation_route=mot
    )
    reply = await _run_emergency_branch(branch)
    assert "建议就医" in reply and "别担心" in reply
    assert set(calls) == {"edu", "mot"}