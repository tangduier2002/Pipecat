"""motivation 路由函数 — CDSMP 健康教练对话。

Prompt 按 triage 意图切换 (emotional_support → 压力管理; lifestyle/general → 一级预防)。
每轮输出必经 guard_check (不可绕过)。单轮回复直接返回文本; 多轮对话经 coach_session 循环。
无用药依从内容 (合规红线: prompt 已裁剪 + guard 兜底)。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from app.guard_check import guard_check

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "prompts"

PROMPT_FILES: dict[str, Path] = {
    "emotional_support": PROMPTS_DIR / "stress-management-coach.md",
    "lifestyle_coaching": PROMPTS_DIR / "primary-prevention-coach.md",
    "general_chat": PROMPTS_DIR / "primary-prevention-coach.md",
}

# 对话原则 (docs/agents/motivation.md, 注入 system 前缀)
COACHING_PRINCIPLES = (
    "对话硬性原则: 1) 永远不使用恐吓性语言; "
    "2) 使用「我们」而非「你应该」; "
    "3) 每次对话结束时, 引导用户完成一个微小行动; "
    "4) 先共情, 确认被理解后再给建议。"
    "禁止输出任何药物、用药、剂量相关内容 (包括拒绝给出用药建议)。"
)


@lru_cache(maxsize=8)
def select_prompt(intent: str) -> str:
    """按意图加载 Prompt 全文 (缓存)。未知意图 → 默认一级预防。"""
    path = PROMPT_FILES.get(intent, PROMPT_FILES["general_chat"])
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Prompt 文件缺失: %s", path)
        return COACHING_PRINCIPLES


def _system_message(intent: str, memory_snapshot: dict | None) -> str:
    prompt = select_prompt(intent)
    memory_block = ""
    if memory_snapshot:
        memory_block = (
            "\n\n【用户近期情况, 仅作背景参考, 不主动提起】\n"
            f"近期血压记录: {memory_snapshot.get('vital_signs', [])}\n"
            f"近期情绪: {memory_snapshot.get('emotions', [])}"
        )
    return f"{prompt}\n{memory_block}\n{COACHING_PRINCIPLES}"


async def coach_chat(user_text: str, intent: str, llm, memory_snapshot: dict | None = None) -> str:
    """单轮教练回复: LLM 生成 (system = prompt + 记忆) → 返回原始文本。

    审查由调用方 (motivation_route / coach_session) 统一执行, 本函数不做。
    """
    system = _system_message(intent, memory_snapshot)
    data = await llm.chat_text(system, user_text)
    return data or "我在听，您慢慢说。"


async def motivation_route(
    user_text: str,
    intent: str,
    llm,
    memory_snapshot: dict | None = None,
    guard_fn=guard_check,
) -> str:
    """motivation 路由: 选 Prompt → 单轮生成 → guard 审查 → 返回最终输出。

    guard_fn 可注入测试替身; 默认 guard_check (输出必经, 不可绕过)。
    """
    raw = await coach_chat(user_text, intent, llm, memory_snapshot)
    _, reviewed = guard_fn(raw)
    return reviewed


class CoachSession:
    """多轮教练对话循环 (Pipecat 语音循环的文本等价物)。

    io 适配器提供 receive() -> str (STT) 与 send(text) (TTS);
    Pipecat 接入时替换 io 实现即可, 循环逻辑不变。
    """

    def __init__(self, intent: str, llm, memory_snapshot: dict | None = None, guard_fn=guard_check, max_turns: int = 10):
        self.intent = intent
        self.llm = llm
        self.memory_snapshot = memory_snapshot or {}
        self.guard_fn = guard_fn
        self.max_turns = max_turns

    async def run(self, io) -> list[str]:
        """执行多轮对话, 每轮输出经 guard 后 send。返回全部输出。"""
        outputs: list[str] = []
        for _ in range(self.max_turns):
            user_text = await io.receive()
            if not user_text or _is_end_marker(user_text):
                break
            raw = await coach_chat(user_text, self.intent, self.llm, self.memory_snapshot)
            _, reviewed = self.guard_fn(raw)
            await io.send(reviewed)
            outputs.append(reviewed)
        return outputs


def _is_end_marker(user_text: str) -> bool:
    return any(m in user_text for m in ("再见", "拜拜", "结束对话", "先这样"))