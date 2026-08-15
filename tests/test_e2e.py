"""T8 系统端到端验收: 五个场景 + 合规红线系统级断言。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.chat import SystemContext, handle_user_input
from tests.conftest import FakeDriver


class FakeLLM:
    """按用户输入返回预设回复; 未预设时回退。"""

    def __init__(self, replies: dict | None = None):
        self._replies = replies or {}
        self.systems: list[str] = []

    @property
    def available(self) -> bool:
        return True

    async def chat_json(self, system: str, user: str) -> dict | None:
        return None  # 分类走关键词兜底 (确定性)

    async def chat_text(self, system: str, user: str, temperature: float = 0.7, response_format: dict | None = None) -> str:
        self.systems.append(system)
        return self._replies.get(user, "好的，我们慢慢来。")


class FakeKG:
    """Domain KG 替身: 按 cypher 关键字返回预设图谱行。"""

    def __init__(self, rows_by_keyword: dict[str, list[dict]] | None = None):
        self._rows = rows_by_keyword or {}

    async def query(self, cypher: str, params: dict) -> list[dict]:
        for kw, rows in self._rows.items():
            if kw in cypher:
                return rows
        return []


def _ctx(driver=None, llm=None, kg=None) -> SystemContext:
    return SystemContext(
        driver=driver or FakeDriver(),
        llm=llm or FakeLLM(),
        kg=kg or FakeKG(),
    )


async def test_e2e_dizziness_with_bp_records_without_disturbance():
    """场景 1: 「我有点头晕，今天血压 155/95」→ monitor 记录确认, 无异常不打扰。"""
    ctx = _ctx()
    reply = await handle_user_input("我有点头晕，今天血压 155/95", ctx)
    assert "155" in reply and "95" in reply  # 数值确认
    assert ctx.pending == {"systolic": 155, "diastolic": 95}  # 等待确认
    # 用户确认 → 写入
    reply2 = await handle_user_input("对", ctx)
    assert "记下" in reply2
    assert ctx.pending is None


async def test_e2e_systolic_185_emergency_chain():
    """场景 2: 「收缩压 185」→ emergency 并行 → guard 确认 → CrisisEvent + 通知。"""
    driver = FakeDriver()
    llm = FakeLLM(replies={"收缩压 185": "别担心，先坐下休息，我会陪着你。"})
    ctx = _ctx(driver=driver, llm=llm)
    reply = await handle_user_input("收缩压 185", ctx)
    assert "别担心" in reply  # 安抚在合并输出中
    # CrisisEvent 已写入 (写入同步完成, 通知在后台)
    cyphers = " ".join(c for c, _ in driver.calls)
    assert "CrisisEvent" in cyphers
    # 输出无用药指令/恐吓内容
    for kw in ("停药", "加一片", "会死", "中风"):
        assert kw not in reply


async def test_e2e_drug_grapefruit_rejected_full_chain():
    """场景 3: 「降压药能和西柚一起吃吗」→ 拒答话术, 全链路无药物内容。"""
    driver = FakeDriver()
    ctx = _ctx(driver=driver)
    reply = await handle_user_input("降压药能和西柚一起吃吗", ctx)
    assert "医生或药师" in reply
    assert driver.calls == []  # 无任何图谱/写入调用


async def test_e2e_anxiety_emotional_support():
    """场景 4: 「我最近很焦虑」→ emotional_support → 压力管理教练共情。"""
    llm = FakeLLM(replies={"我最近很焦虑": "最近心里不太安定，是吗？我们聊聊。"})
    ctx = _ctx(llm=llm)
    reply = await handle_user_input("我最近很焦虑", ctx)
    assert "压力管理" in llm.systems[0]  # 压力管理 Prompt 已注入
    assert "我们" in reply


async def test_e2e_quit_smoking_lifestyle_coaching():
    """场景 5: 「我想戒烟」→ lifestyle_coaching → 行为改变引导。"""
    llm = FakeLLM(replies={"我想戒烟": "戒烟是重要的一步，我们从每天少抽一根开始。"})
    ctx = _ctx(llm=llm)
    reply = await handle_user_input("我想戒烟", ctx)
    assert "一级预防" in llm.systems[0]  # 一级预防 Prompt 已注入
    assert "戒烟" in reply


async def test_e2e_psychological_crisis_full_chain():
    """心理危机链路: 「不想活了」→ 危机话术 + 热线 + CrisisEvent。"""
    driver = FakeDriver()
    ctx = _ctx(driver=driver)
    reply = await handle_user_input("不想活了", ctx)
    assert "12356" in reply  # 心理热线
    cyphers = " ".join(c for c, _ in driver.calls)
    assert "CrisisEvent" in cyphers


async def test_compliance_no_medication_anywhere():
    """合规红线: 全部对话路径输出不含药物指令; 写入无 Medication 标签。"""
    llm = FakeLLM(replies={"我想运动": "我们一起快走吧"})
    wrote_any = False
    for text in ("我有点头晕，今天血压 155/95", "收缩压 185", "我最近很焦虑", "我想戒烟"):
        driver = FakeDriver()
        ctx = _ctx(driver=driver, llm=llm)
        reply = await handle_user_input(text, ctx)
        for kw in ("加一片", "停药", "吃药"):
            assert kw not in reply
        wrote_any = wrote_any or bool(driver.calls)
        assert not any("Medication" in c for c, _ in driver.calls)
    assert wrote_any  # 至少一条路径产生过写入