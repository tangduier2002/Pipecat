# motivation — CDSMP 健康教练对话

**运行时**：LangGraph 节点 + Pipecat 语音子图（`motivation_agent.py`）

## 职责

1. 承担全部教练类对话：情绪支持、生活方式行为改变、日常寒暄
2. 对话必须遵循 CDSMP 框架（能力映射见 `docs/adr/0001-cdsmp-mapping.md`）
3. **Prompt 按 Triage 意图切换**：压力类 → 压力管理教练；生活方式/预防类 → 一级预防教练
4. 底层沟通框架：NVC（非暴力沟通）——先同理再改变、聚焦需要而非对错
5. **无用药依从内容**（合规裁剪，见 ADR-0001）

> 说明：代码层 `motivation` 即完整健康教练角色，不是「激励器」。产品层别名（Agent A 等）已废弃。

## Prompt 切换策略

| Triage intent | 使用 Prompt | 文件 |
|---------------|------------|------|
| `emotional_support` | 压力管理人生教练（V1.9） | `docs/prompts/stress-management-coach.md` |
| `lifestyle_coaching` | 一级预防健康教练（V4.0） | `docs/prompts/primary-prevention-coach.md` |
| `general_chat` | 一级预防（默认） | 同上 |

两份 Prompt 的共同骨架（提取自 CDSMP 教练提示词来源）：

- **角色边界**：健康指导者（Health Coach）——不诊断、不开药、不治疗
- **底层方法**：NVC（观察/感受/需要/请求四要素）+ MI（动机访谈）
- **人格设定**：大五人格模型（宜人性高、尽责性高、情绪稳定性高）
- **安全红线**：禁止诊断、禁止替代治疗方案、禁止过度承诺

## CDSMP 行为映射（motivation 侧落地）

| CDSMP 能力 | 对话行为 |
|-----------|---------|
| 行动计划 | SMART 目标分解，饮食/运动/睡眠微行动，每周小步前进 |
| 决策制定 | 症状变化识别、选项分析、紧急情况判断（紧急 → 转 emergency） |
| 资源利用 | 社区服务推荐、就医时机建议、家庭支持动员 |
| 情绪管理 | 负面情绪识别（NVC 共情）、认知重构引导、放松训练（压力管理 Prompt 主责） |
| 自我效能 | 进步可视化、能力肯定、成长故事记录（掌握经验/替代经验/社会说服） |
| 医患伙伴关系（弱化） | 仅「建议咨询医生」，不讨论治疗方案 |

## Pipecat 语音子图接口

Motivation 使用 Pipecat 做低延迟流式语音对话，作为 LangGraph 的一个语音子图：

```
LangGraph(motivation 节点)
  → Pipecat 子图：STT →（对话轮次）→ TTS
  → 回合结束后返回 LangGraph State
```

其余 Agent 不进入 Pipecat Pipeline，在 LangGraph 状态机内运行。

## 对话原则（承自大文档）

1. 永远不使用恐吓性语言（如「不吃药会中风」——注意：此类示例已随用药裁剪移除）
2. 使用「我们」而非「你应该」
3. 每次对话结束时，引导用户完成一个微小行动
4. 先共情，确认被理解后再给建议；建议时机遵循 MI 准备阶段 + 用户主动请求

## 切换细则 TODO

- `emotional_support` vs `lifestyle_coaching` 的细粒度边界（如「血压高 + 焦虑」的复合状态如何选择 Prompt）
- 是否引入状态条件路由：结合 Patient KG 近期数据（血压趋势 + 情绪快照）动态选择 Prompt
- 复合意图的优先级规则（如 emergency 中 motivation 部分固定用压力管理 Prompt，已定；其余待定）

## 验收标准

- 输入「我最近血压总是控制不好，很焦虑」→ emotional_support 路由，压力管理教练输出共情 + 安抚
- 输入「我想戒烟」→ lifestyle_coaching 路由，一级预防教练输出行为改变引导
- 输出必经 guard 审查（不绕过）