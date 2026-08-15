"""guard_check — 输出合规审查纯函数。

确定性规则 (关键词 + 阈值), 无 LLM, 无规则引擎 (ADR-0002)。
所有 Agent 输出进 TTS 前必经本函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GuardAction = Literal["PASS", "REWRITE", "REJECT", "EMERGENCY"]


@dataclass(frozen=True)
class Threshold:
    """紧急阈值 (输入侧识别)。"""

    systolic: int = 180
    diastolic: int = 120


# --- 规则关键词 ---

# 规则 1: 恐吓性语言
FRIGHTEN_PATTERNS = ("会中风", "会死", "会没命", "会瘫", "会瞎", "活不长", "会心梗")

# 规则 2: 药物相关输出 (药物-食物相互作用 → 模板一; 调整/停药 → 模板二; 一般 → 模板三)
DRUG_INTERACTION_PATTERNS = ("西柚", "柚子")
DRUG_ADJUST_PATTERNS = ("加一片", "减半", "停药", "吃药", "用药", "剂量", "加量", "减量", "一片", "半片", "两片")
# 裸"药"字也拦截: Agent 输出任何药物提及一律拒答 (合规红线)
DRUG_PATTERNS = DRUG_INTERACTION_PATTERNS + DRUG_ADJUST_PATTERNS + ("药",)

# 规则 3: 诊断与治疗建议
DIAGNOSIS_PATTERNS = ("你得了", "你需要治疗", "你的病", "这病是", "确诊")

# 规则 4/5: 紧急情况 (输入侧)
EMERGENCY_SYMPTOM_PATTERNS = ("胸痛", "胸口", "视力模糊", "看不清", "喘不上气", "气短")
CRISIS_PATTERNS = ("不想活", "活着没意思", "不想活了", "活不下去", "想自杀")

# --- NVC 拒答话术三模板 (文案来自 docs/agents/guard.md, 逐字引用) ---

TEMPLATE_1_DRUG_INTERACTION = (
    "您这么问，是想确认这样做安不安全——这份谨慎特别重要。"
    "药物搭配需要专业判断，我给您的答案可能不准，反而让您担风险。"
    "最稳当的是问医生或药师，这关系到您的身体，值得一个准话。"
    "咱们先聊别的——您今天量血压了吗？最近数字怎么样？"
)

TEMPLATE_2_DRUG_ADJUST = (
    "您愿意跟人商量用药的事，这个做法本身就特别稳妥。"
    "怎么调整药，是医生掌握分寸的事——他们了解您全部的情况。"
    "下次复诊时把您的想法说出来，医生会帮您一起判断。"
    "这会儿咱们聊聊别的：您这两天睡得怎么样？"
)

TEMPLATE_3_GENERAL = (
    "这个问题我很想帮您，但我的知识够不到专业的边界，说错了反而耽误您。"
    "问医生或药师最稳当。我先把您的问题记下来，下次见面时提醒您带去问。"
    "咱们先做点别的——您最近有什么想聊的？"
)

# 心理危机话术 + 心理热线 (规则 5)
CRISIS_REPLY = (
    "您现在的心情我听到了，谢谢您愿意告诉我。"
    "请您先别一个人扛着，现在就可以拨打心理援助热线 12356，"
    "或者让家人陪在您身边。我也会马上联系您的家人，好吗？"
)


def _rewrite_scare(text: str) -> str:
    """模板化改写: 去恐吓表述 → 建议性表述 (不调用 LLM)。"""
    rewritten = text
    for pattern in FRIGHTEN_PATTERNS:
        rewritten = rewritten.replace(pattern, "可能会有些风险")
    for pattern in ("中风", "心梗"):
        rewritten = rewritten.replace(pattern, "血压波动")
    return rewritten


def _rewrite_diagnosis(text: str) -> str:
    rewritten = text
    for pattern in DIAGNOSIS_PATTERNS:
        rewritten = rewritten.replace(pattern, "建议您关注")
    rewritten = rewritten.replace("需要治疗", "建议及时就医")
    return rewritten


def _pick_reject_template(text: str) -> str:
    """按违规类型选择拒答模板。"""
    if any(p in text for p in DRUG_INTERACTION_PATTERNS) and any(p in text for p in DRUG_PATTERNS):
        return TEMPLATE_1_DRUG_INTERACTION
    if any(p in text for p in DRUG_ADJUST_PATTERNS):
        return TEMPLATE_2_DRUG_ADJUST
    return TEMPLATE_3_GENERAL


def guard_check(text: str, threshold: Threshold | None = None) -> tuple[GuardAction, str]:
    """输出审查。返回 (动作, 处理后的文本)。

    规则优先级: 心理危机(5) > 紧急情况(4) > 药物(2) > 恐吓(1) > 诊断(3) > PASS
    """
    threshold = threshold or Threshold()

    if any(p in text for p in CRISIS_PATTERNS):
        return "EMERGENCY", CRISIS_REPLY

    # 输入侧紧急识别: 症状关键词 (输入文本由调用方传入, 见 rules 4)
    if any(p in text for p in EMERGENCY_SYMPTOM_PATTERNS):
        return "EMERGENCY", text

    if any(p in text for p in DRUG_PATTERNS):
        return "REJECT", _pick_reject_template(text)

    if any(p in text for p in FRIGHTEN_PATTERNS):
        return "REWRITE", _rewrite_scare(text)

    if any(p in text for p in DIAGNOSIS_PATTERNS):
        return "REWRITE", _rewrite_diagnosis(text)

    return "PASS", text


def check_emergency_threshold(systolic: int | None, diastolic: int | None, threshold: Threshold | None = None) -> bool:
    """血压阈值紧急判定 (规则 4 数值部分)。"""
    threshold = threshold or Threshold()
    if systolic is not None and systolic >= threshold.systolic:
        return True
    if diastolic is not None and diastolic >= threshold.diastolic:
        return True
    return False