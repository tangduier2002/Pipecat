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

## 组件地图

| 组件 | 位置 | 职责 |
|------|------|------|
| triage | `triage.py`（T3） | 意图分类 + 路由入口 |
| monitor | `monitor.py`（T5） | 生命体征记录、异常检测、emergency 触发 |
| education | `education.py` + `knowledge_graph/kg_service.py`（T2） | Domain KG 问答（饮食/运动/症状，无药物） |
| motivation | `motivation.py`（T7） | CDSMP 健康教练对话（Pipecat 语音） |
| guard | `guard_check.py`（T6） | 输出合规审查纯函数 + NVC 拒答 + 危机通知 |
| memory | `memory.py`（T4） | 动态记忆四步管道（extract/validate/merge/write） |
| 数据 | `data/*.csv` + `scripts/import_kg.py`（T1） | Domain KG 图谱数据与导入 |

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

- 蓝牙设备接入为预留接口（`device/` 抽象），MRP 阶段默认关闭。
- Prompt 切换细则（emotional_support vs lifestyle_coaching 细粒度边界）留待后续迭代。