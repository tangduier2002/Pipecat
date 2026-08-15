# Pipecat — 高血压自我管理多智能体语音系统（CDSMP 健康教练）

基于斯坦福 CDSMP 框架的适老化语音健康教练。多智能体协作 + 双知识图谱（Domain KG + Patient KG）+ 强制输出审查（guard）。

架构与约束见根目录 [`CONTEXT.md`](CONTEXT.md)，技术决策见 `docs/adr/`。

## 快速启动

### 1. 启动 Neo4j（Domain KG + Patient KG 共用单实例）

```bash
docker run -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:latest
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 按需修改 .env 中的 NEO4J / 邮件配置
```

### 3. 安装依赖

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt     # Windows
source .venv/bin/pip install -r requirements.txt  # macOS / Linux
```

### 4. 导入知识图谱

```bash
python scripts/import_kg.py            # Domain KG 导入（幂等，可重复执行）
python scripts/import_kg.py --init-patient  # 额外初始化 Patient KG 约束
```

### 5. 启动 API

```bash
uvicorn app.main:app
```

健康检查：`GET http://localhost:8000/health` → `{"status": "ok", "neo4j": "connected"}`

### 6. 运行测试

```bash
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

### 7. 端到端验收场景

对话入口为 `handle_user_input(text, ctx)`（`app/chat.py`），由 FastAPI 端点 / Pipecat 处理器调用。验收清单：

| 场景 | 预期 |
|------|------|
| 「我有点头晕，今天血压 155/95」 | monitor 数值确认 → 写入 Patient KG，无异常不打扰 |
| 「收缩压 185」 | emergency 并行（安抚 + 建议）→ CrisisEvent 写入 → 邮件通知子女/社区医生 |
| 「降压药能和西柚一起吃吗」 | NVC 拒答话术，全链路无药物内容 |
| 「我最近很焦虑」 | emotional_support → 压力管理教练共情 |
| 「我想戒烟」 | lifestyle_coaching → 一级预防教练行为改变引导 |
| 「不想活了」 | 危机话术 + 心理热线 12356 + CrisisEvent(psychological_crisis) |

系统级断言（`tests/test_e2e.py`）：所有输出必经 guard_check；Patient KG 无 Medication 标签；emergency 输出无用药指令/恐吓内容。

## 组件地图

| 组件 | 位置 | 职责 |
|------|------|------|
| triage | `app/triage.py` | 意图分类 + 路由入口 |
| monitor | `app/monitor.py` | 生命体征记录、异常检测、emergency 触发 |
| education | `app/education.py` + `app/knowledge_graph/kg_service.py` | Domain KG 问答（饮食/运动/症状，无药物） |
| motivation | `app/motivation.py` | CDSMP 健康教练对话（Prompt 切换 + 多轮会话） |
| guard | `app/guard_check.py` + `app/guard.py` | 输出合规审查纯函数 + NVC 拒答 + 危机通知 |
| memory | `app/memory.py` | 动态记忆四步管道（extract/validate/merge/write） |
| 编排 | `app/chat.py` | 对话入口：串行路由 + emergency 并行（`asyncio.gather`） |
| 数据 | `data/*.csv` + `scripts/import_kg.py` | Domain KG 图谱数据与导入 |

## 环境变量

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j 连接地址 |
| `NEO4J_USER` | `neo4j` | Neo4j 用户名 |
| `NEO4J_PASSWORD` | `password` | Neo4j 密码 |
| `CAREGIVER_EMAIL` | 空 | 危机通知收件人（子女） |
| `COMMUNITY_DOCTOR_EMAIL` | 空 | 危机通知收件人（社区医生） |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | 空 | 异步邮件通知（T6 使用） |

## 合规红线

- **无药物问答**：Domain KG 不含 Drug 实体；含药物/用药表达一律 NVC 拒答。
- **输出必经 guard**：任何对外输出先过 `guard_check` 再进 TTS。
- **紧急预案**：血压 ≥ 180/120 → 安抚 + 通知子女/社区医生（异步邮件）；心理危机 → 危机话术 + 心理热线。

## 已知限制

- 蓝牙设备接入为预留接口（`app/device/` 抽象），MRP 阶段默认关闭。
- Prompt 切换细则（emotional_support vs lifestyle_coaching 细粒度边界）留待后续迭代。
- 本机无 Docker/Neo4j 时，图谱相关验收由测试替身覆盖（`tests/conftest.py`）；真实图谱验收需按「快速启动」第 1 步启动 Neo4j 后执行 `python scripts/import_kg.py`。