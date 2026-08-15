# ADR-0001: CDSMP 能力 → Agent 行为映射（含用药依从裁剪）

- **状态**：已接受
- **日期**：2026-08-15
- **决策者**：产品与开发（grilling 三轮确认）

## 背景

产品理论内核是斯坦福 CDSMP（慢性病自我管理项目）六大核心能力。合并三份项目文档时，需要明确：哪些 CDSMP 能力保留并映射到 Agent 行为，哪些因合规原因裁剪。同时确认 Motivation（代码层）即产品层的健康教练角色，不另设产品层别名（Agent A/B/C 命名已废弃）。

## 决策

### CDSMP 能力映射表

| CDSMP 能力 | MRP 状态 | 映射到 |
|-----------|---------|--------|
| 行动计划 (Action Planning) | 保留 | motivation：饮食/运动/睡眠微行动（SMART 目标） |
| 决策制定 (Decision Making) | 保留 | motivation：健康生活方式选择引导 |
| 资源利用 (Resource Utilization) | 保留 | motivation：引导社区/家庭资源 |
| 情绪管理 (Emotion Management) | 保留 | motivation：压力管理教练 Prompt 主责 |
| 自我效能 (Self-Efficacy) | 保留 | motivation 通用（掌握经验/替代经验/社会说服） |
| 患者-医疗提供者伙伴关系 | 弱化 | 仅「建议咨询医生」导向，不讨论治疗方案 |
| ~~用药依从~~ | 裁剪 | 不进入任何 Agent 行为 |

### 用药依从裁剪边界（合规红线）

| 层 | 处理 |
|----|------|
| motivation 对话 | 无吃药提醒、停药劝慰、依从性激励 |
| Patient KG | 无 `Medication` 实体、无用药抽取（Zep 本体不含药物） |
| education | 无 `DRUG_QUERY` / `INTERACTION_QUERY` 意图 |
| Domain KG | **不含 Drug 节点**及食物-药物相互作用关系 |
| guard 规则 | 药物表达兜底拦截 + NVC 拒答话术（见 `docs/agents/guard.md`） |

裁剪理由：药物相关对话涉及医疗合规风险（调整剂量、停药建议、食物-药物禁忌），MRP 阶段不具备医事责任承担能力；「去掉对话不代表去掉防护」——guard 仍兜底拦截含药物表达的内容。

## 影响

- triage 意图集不含任何药物类目；含药物关键词的输入直接走 NVC 拒答分支
- monitor 抽取的生命体征不含用药记录
- CDSMP 示例数据（大文档「我不想吃药了」对话）不迁入合并文档

## 其他被废弃的产品层命名

- 「Agent A / B / C」与「健康教练 / 记忆局长 / 参谋监察」别名全部废弃
- 唯一命名：`triage_agent` / `monitor_agent` / `education_agent` / `motivation_agent` / `guard_agent` / `profile_service`（见 `CONTEXT.md` 术语表）

## 关联文档

- `CONTEXT.md`（架构约束）
- `docs/agents/motivation.md`（CDSMP 行为落地）
- `docs/agents/guard.md`（拒答与兜底规则）