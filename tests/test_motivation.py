"""T7 motivation 测试: Prompt 切换、guard 必经、CDSMP 行为、无用药内容。"""

from __future__ import annotations

import pytest

from app.motivation import (
    COACHING_PRINCIPLES,
    PROMPT_FILES,
    CoachSession,
    coach_chat,
    motivation_route,
    select_prompt,
)


class FakeLLM:
    def __init__(self, reply: str):
        self._reply = reply
        self.systems: list[str] = []
        self.users: list[str] = []

    @property
    def available(self) -> bool:
        return True

    async def chat_text(self, system: str, user: str, temperature: float = 0.7, response_format: dict | None = None) -> str:
        self.systems.append(system)
        self.users.append(user)
        return self._reply


def test_select_prompt_emotional_uses_stress_manager():
    prompt = select_prompt("emotional_support")
    assert "压力管理" in prompt


def test_select_prompt_lifestyle_uses_primary_prevention():
    prompt = select_prompt("lifestyle_coaching")
    assert "一级预防" in prompt


def test_select_prompt_general_defaults_to_primary_prevention():
    assert select_prompt("general_chat") == select_prompt("lifestyle_coaching")


def test_prompt_has_no_medication_adherence_content():
    """合规红线: 两份 Prompt 无用药依从内容。"""
    for intent in ("emotional_support", "lifestyle_coaching", "general_chat"):
        prompt = select_prompt(intent)
        for kw in ("吃药提醒", "按时服药", "依从性", "别停药"):
            assert kw not in prompt


async def test_coach_chat_emotional_empathy():
    llm = FakeLLM("我能感受到您最近的压力很大，我们先一起慢慢梳理。")
    out = await coach_chat("我最近血压总是控制不好，很焦虑", "emotional_support", llm)
    assert "压力" in out
    assert "压力管理" in llm.systems[0]  # 压力管理 prompt 注入


async def test_coach_chat_lifestyle_behavior_change():
    llm = FakeLLM("戒烟是个重要的决定，我们从一个小目标开始：每天少抽一根。")
    out = await coach_chat("我想戒烟", "lifestyle_coaching", llm)
    assert "戒烟" in out


async def test_motivation_route_guard_rejects_drug_output():
    """输出必经 guard: 含药物表述的 LLM 输出被拦截替换。"""
    llm = FakeLLM("你该停药了，加一片就行")
    out = await motivation_route("我最近血压高", "lifestyle_coaching", llm)
    assert "停药" not in out
    assert "加一片" not in out
    assert "医生" in out  # NVC 拒答模板二


async def test_motivation_route_guard_rewrites_scare():
    llm = FakeLLM("不吃药会中风")
    out = await motivation_route("我最近血压高", "lifestyle_coaching", llm)
    assert "中风" not in out


async def test_motivation_route_pass_through():
    llm = FakeLLM("我们一起从每天快走 10 分钟开始，好吗？")
    out = await motivation_route("我想运动", "lifestyle_coaching", llm)
    assert out == "我们一起从每天快走 10 分钟开始，好吗？"


async def test_motivation_route_injects_memory_snapshot():
    llm = FakeLLM("好的")
    snapshot = {"vital_signs": [{"name": "血压", "value": 155}], "emotions": [{"name": "焦虑"}]}
    await motivation_route("我最近睡不好", "emotional_support", llm, snapshot)
    assert "155" in llm.systems[0]
    assert "焦虑" in llm.systems[0]


async def test_coach_session_multiple_turns_guarded():
    class FakeIO:
        def __init__(self, inputs: list[str]):
            self._inputs = list(inputs)
            self.sent: list[str] = []

        async def receive(self) -> str:
            return self._inputs.pop(0) if self._inputs else ""

        async def send(self, text: str) -> None:
            self.sent.append(text)

    llm = FakeLLM("好的，我们慢慢来。")
    session = CoachSession("general_chat", llm)
    io = FakeIO(["早上好", "我今天头有点晕", "再见"])
    outputs = await session.run(io)
    assert len(outputs) == 2  # 第三轮是结束语, 不生成
    assert all("我们慢慢来" in o for o in outputs)


async def test_coach_session_guard_applied_each_turn():
    class FakeIO:
        def __init__(self, inputs: list[str]):
            self._inputs = list(inputs)
            self.sent: list[str] = []

        async def receive(self) -> str:
            return self._inputs.pop(0) if self._inputs else ""

        async def send(self, text: str) -> None:
            self.sent.append(text)

    class DrugLLM(FakeLLM):
        async def chat_text(self, system, user, temperature=0.7, response_format=None):
            return "你该停药了"

    session = CoachSession("general_chat", DrugLLM("你该停药了"))
    io = FakeIO(["聊聊吧", "再见"])
    await session.run(io)
    assert "停药" not in io.sent[0]


def test_coaching_principles_embedded():
    assert "恐吓" in COACHING_PRINCIPLES
    assert "我们" in COACHING_PRINCIPLES


def test_prompt_files_map_all_intents():
    assert set(PROMPT_FILES) == {"emotional_support", "lifestyle_coaching", "general_chat"}