# ADR-0002: 技术栈精简（砍框架，保功能）

- **状态**：已接受
- **日期**：2026-08-15
- **决策者**：产品与开发（grilling 一轮确认）
- **关联**：ADR-0001（CDSMP 映射，本决策不改动其任何裁剪边界）

## 背景

MRP 功能集确定后重新审视支撑技术栈。主数据流本质是一条线性管道（triage → 单 agent → guard → TTS，唯一的并行点是 emergency 扇出），却挂了 4 个外部框架：LangGraph（编排）、NeMo Guardrails（审查）、Zep/Graphiti（记忆）、Redis（会话）。框架带来的是状态机、规则引擎、时序图数据库、分布式缓存四套学习与运维成本，而 MRP 的真实需求用确定性逻辑与少量函数即可满足。原则：**简单已足够复杂——复杂度留给业务本身（意图路由、CDSMP 对话、图谱查询），不留框架胶水**。

## 决策

### 砍 NeMo Guardrails → 纯函数审查

guard 的 5 条规则全部是确定性规则（关键词匹配 + 阈值判断），不需要规则引擎。替代：`guard_check(text) -> Action`（通过 / 重写 / 拒答 / 紧急）纯函数，规则与 NVC 话术模板原样保留。

### 砍 LangGraph → 普通 async 路由

triage 条件路由 + 三选一 agent + emergency 双路 `asyncio.gather`，状态为请求内 dict。**保留人工介入能力**（危机时联系子女与社区医生），以"写事件 + 发通知"实现，不依赖状态机：

- monitor 检测 → guard 确认 emergency → 写 `CrisisEvent` 节点到 Patient KG → 异步发邮件（FastAPI BackgroundTasks + `aiosmtplib`，收件人邮箱在 `.env` 配置）→ 对话侧照常安抚
- 通知在后台进行，不中断对话流；邮件含时间、血压读数、症状、对话摘要
- 未来若出现真正的多分支状态机或"等待人工确认后才继续"的需求，从函数演化到 LangGraph 的成本低（节点签名兼容）；反向（带着框架做简单事）是持续负债，故当前不引入

### 砍 Zep/Graphiti → 自建 memory 模块（保留动态记忆能力）

动态记忆是必须保留的功能（动态、实时更新、实时校验），但 Zep 与 Graphiti 功能重复，且其时间感知能力（实体演变、关系衰减）对 MRP 的 4 类实体收益很小。替代：memory 模块四步管道，直接写 Patient KG：

1. `extract`：LLM 单次调用 + 固定 JSON schema（VitalSign / Symptom / Emotion / LifeEvent 四类，不含 Medication）
2. `validate`：写入前规则校验——血压范围合理性、危机阈值预标记、时间戳合法性（即"实时校验"）
3. `merge`：动态更新——同实体合并、同日数据聚合、冲突检测（即"动态"）
4. `write`：写 Patient KG（Neo4j 单实例已存在，零新增服务）

### 砍 Redis → 内存会话 + Patient KG 恢复

单设备家庭场景（MRP 典型形态）无跨进程会话共享需求。会话状态存 FastAPI 进程内存，启动时从 Patient KG 恢复；持久化层就是 Patient KG 本身。

### education LLM 兜底分支 → NVC 拒答

图谱无匹配不再走 `fallback_llm`（双生成路径 + 免责声明一致性维护成本）。替代：直接走 NVC 拒答模板三（"我的知识够不到，问医生最稳当"），与合规红线方向一致。

## 精简后技术栈

| 层 | 精简前 | 精简后 |
|----|--------|--------|
| 外部服务 | Pipecat、Neo4j、Zep/Graphiti、Redis | **Pipecat、Neo4j** |
| 编排 | LangGraph | async 函数 + 路由 |
| 审查 | NeMo Guardrails | 纯函数 `guard_check` |
| 记忆 | Zep/Graphiti | 自建 memory 模块（extract/validate/merge/write） |
| 会话 | Redis | FastAPI 内存 + Patient KG 恢复 |

## 保留不变（不因精简而砍）

- **Pipecat**：语音是产品本体
- **Neo4j 双标签单实例**：Patient KG 图语义（时序、关联）是真实需求；Domain 子图边际成本近零
- **6 个职责单元**：triage / monitor / education / motivation / guard / profile（memory）——同一进程内的函数调用
- **全部功能与合规红线**：双知识图谱、guard 强制审查、NVC 拒答、emergency 并行、危机联系子女与社区医生，一个不少

## 影响

- 代码单元签名从"LangGraph 节点 `node(state: GraphState) -> GraphState`"简化为普通 async 函数，状态为请求内 dict
- `docs/agents/*.md` 的运行时描述同步更新（已在 ADR-0002 同日完成）
- 人工介入的邮件通知是 MRP 必须保留的功能，作为 guard 确认后的后台动作实现