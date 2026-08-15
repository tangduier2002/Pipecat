# CONTEXT — 高血压自我管理多智能体语音系统（CDSMP 健康教练）

## 愿景

基于斯坦福 CDSMP（慢性病自我管理项目）框架，以适老化语音交互为载体，融合多智能体协作、双知识图谱（Domain KG + Patient KG）与大语言模型，为高血压老年人提供「有温度、有记忆」的全天候自我管理伙伴。

项目定位为 **MRP（Minimum Release Product）**：一开始就采用完整架构（LangGraph 编排、双 Neo4j 子图、Zep/Graphiti 时序记忆、guard 强制审查），不做临时内存态 MVP。

## 架构总览

| 层 | 技术选型 | 职责 |
|----|---------|------|
| 编排 | **LangGraph**（主 orchestrator） | 全部 Agent 状态机流转、emergency 并行分支 |
| 语音 | **Pipecat**（语音子图） | STT/TTS 流式语音 IO，供 Triage 入口与 Motivation 使用 |
| 记忆 | **Zep / Graphiti** | 实体抽取 + 时序图谱写入（Patient KG） |
| 知识 | **neo4j 官方驱动** | Domain KG 查询（Education） |
| 安全 | **NeMo Guardrails** | guard 节点的规则引擎 |
| 图谱 | **Neo4j 单实例双标签** | `Domain:*`（静态医学）+ `Patient:*`（个人时序） |
| 后端 | FastAPI + Redis | API 服务、会话状态持久化 |

## Agent 拓扑

```
用户语音
   │
   ▼
Pipecat STT
   │
   ▼
┌──────────────┐   ┌─────────────────────────────────────┐
│ triage_agent │──▶│ 识别意图并路由（LangGraph 入口节点）    │
└──────┬───────┘   └─────────────────────────────────────┘
       │
       ├──▶ monitor_agent      data_record / emergency
       ├──▶ education_agent    knowledge_query（Domain KG）
       ├──▶ motivation_agent   emotional_support / lifestyle_coaching / general_chat
       │                          （Pipecat 语音子图，Prompt 按意图切换）
       └──▶（拒答）             药物/超范围表达 → NVC 拒答话术
       │
       ▼
┌──────────────┐
│ guard 节点    │── 所有输出必经审查（NeMo Guardrails）──▶ Pipecat TTS
└──────────────┘

后台服务：profile_service（Zep/Graphiti → Patient KG 时序子图）
```

**数据流（主路径）**：

```
Pipecat STT → LangGraph(triage) → [monitor | education | motivation+Pipecat] → guard → Pipecat TTS

emergency 分支（并行扇出）：
monitor 触发 → 并行(education + motivation) → guard → TTS
```

**关键设计原则**：

- **串行路由**：triage 决定下一步交给谁，完成后回到 triage 或结束
- **并行扇出**：异常情况时 monitor 同时触发 education（给建议）+ motivation（给安抚）
- **输出必经审查**：任何 Agent 的对外输出必须先过 guard 节点才能进 TTS
- **Agent = 4 个**：triage / monitor / education / motivation；`profile_service` 是数据服务，guard 是审查节点

## 架构约束（必须遵守）

1. **无药物问答（合规红线）**：Education 不暴露药物查询接口；Domain KG **不含任何 Drug 实体**；含药物/用药的表达由 triage 识别后走 NVC 拒答话术，引导咨询医生。
2. **输出必经 guard**：Motivation / Education 的输出必须先经 guard 审查再 TTS，不可绕过。
3. **CDSMP 约束**：Motivation 对话必须遵循 CDSMP 能力映射（见 `docs/adr/0001-cdsmp-mapping.md`）；用药依从能力已裁剪。
4. **适老化**：语音为主交互、最小字号 24pt、黄黑高对比、方言支持、温暖缓慢的 TTS 语音、主动关怀式对话。
5. **紧急预案**：血压 ≥ 180/120 → 安抚 + 通知家属；心理危机言论 → 危机话术 + 心理热线；以上由 monitor 检测、guard 确认后触发。

## 术语表

| 术语 | 定义 |
|------|------|
| Domain KG | 静态医学知识图谱（疾病/症状/食物/运动/检查/科室），Education 查询 |
| Patient KG | 个人动态时序图谱（患者/生命体征/症状/情绪/生活事件），profile_service 读写 |
| profile_service | 记忆服务，Zep/Graphiti 实体抽取 + Patient KG 读写（替代早期内存 dict 方案） |
| triage | 意图分类 + 路由（LangGraph 入口节点） |
| monitor | 血压/心率记录、异常检测、emergency 触发 |
| education | Domain KG 问答（饮食/运动/症状，无药物） |
| motivation | CDSMP 健康教练对话；按意图切换两份教练 Prompt |
| guard | 输出合规审查节点（NeMo Guardrails 实现），所有输出必经 |
| NVC | 非暴力沟通，Motivation 与拒答话术的底层沟通框架 |
| CDSMP | 斯坦福慢性病自我管理项目，Motivation 行为的理论内核 |

**命名规则**：代码层命名即唯一命名（`triage_agent` / `monitor_agent` / `education_agent` / `motivation_agent` / `guard_agent` / `profile_service`）。不使用产品层别名（如 Agent A/B/C、健康教练/记忆局长/参谋监察），避免文档与代码脱节。

## 文档地图

| 文档 | 内容 |
|------|------|
| `docs/agents/triage.md` | 意图分类、路由表、药物拒答边界 |
| `docs/agents/monitor.md` | 数据记录、异常阈值、emergency 流程 |
| `docs/agents/education.md` | Domain KG 实现（图谱 schema、Cypher 模板、kg_service） |
| `docs/agents/motivation.md` | CDSMP 教练对话、Prompt 切换策略 |
| `docs/agents/guard.md` | 输出审查规则、NVC 拒答话术、SOS 升级 |
| `docs/adr/0001-cdsmp-mapping.md` | CDSMP 能力 → Agent 行为映射决策 |
| `docs/prompts/primary-prevention-coach.md` | 一级预防健康教练系统提示词 |
| `docs/prompts/stress-management-coach.md` | 压力管理人生教练系统提示词 |
| `docs/appendix/hardware.md` | 硬件终端形态（不进开发路径） |
| `docs/archive/` | 合并前原始文档（只读参考） |