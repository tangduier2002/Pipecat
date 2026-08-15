# triage — 意图分类与路由

**运行时**：入口路由函数（`triage.py`），无编排框架（见 ADR-0002）

## 职责

1. 对用户输入做意图分类（LLM 分类 + 关键词规则兜底）
2. 将帧路由到对应 Agent；无匹配时给出通用回复
3. **药物边界**：任何含药物/用药的表达不进入 Education，直接走 NVC 拒答分支

## 意图路由表（v1 骨架）

| Triage intent | 路由目标 | 使用的教练 Prompt |
|---------------|---------|------------------|
| `emotional_support` | motivation → guard | `stress-management-coach`（压力管理） |
| `lifestyle_coaching` | motivation → guard | `primary-prevention-coach`（一级预防） |
| `knowledge_query` | education → guard | — |
| `data_record` | monitor → guard | — |
| `emergency` | monitor 并行（education + motivation）→ guard | 压力管理为主 |
| `general_chat` | motivation → guard | 默认一级预防 |
| ~~drug_query~~ | 已删除（合规） | — |

**药物表达**（`drug` / `medication` 关键词或 LLM 判定）→ 不走上述任何路由，直接调用 NVC 拒答话术（模板见 `docs/agents/guard.md`）。

## 路由逻辑（骨架）

```python
# triage.py（入口路由函数，非 Pipecat FrameProcessor）
async def triage_route(user_text: str) -> Route:
    intent_data = await classify_intent(user_text)
    if intent_data["intent"] == "drug_query":
        return Route(reject=build_nvc_reject(user_text))  # 拒答，不进任何 Agent
    return Route(target=intent_data["intent"], entities=intent_data.get("entities", {}))
```

## 切换细则 TODO

- `emotional_support` vs `lifestyle_coaching` 的细粒度边界规则（如「血压高 + 焦虑」走哪个 Prompt）尚未定稿
- 当前以 Triage 输出意图为准；后续可加状态条件路由（结合 Patient KG 近期数据）

## 验收标准

- 输入「我能吃香蕉吗？」→ `{"intent": "knowledge_query", "entities": {"food": "香蕉"}}`
- 输入「收缩压 185」→ `{"intent": "emergency", "entities": {"systolic": 185}}`
- 输入「降压药能和西柚一起吃吗」→ 拒答分支，不产生 Education 查询