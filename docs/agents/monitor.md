# monitor — 数据记录与异常检测

**运行时**：路由函数（`monitor.py`）

## 职责

1. 从用户语音或蓝牙设备提取血压/心率数据（`data_record`）
2. 写入 Patient KG（经 memory 模块）
3. 异常检测：超阈值 → 触发 `emergency` 并行分支（education 给建议 + motivation 给安抚）
4. 无 Medication 实体（合规裁剪，见 ADR-0001）

## 数据记录流程

```
用户语音：「今天早上量血压 155/95」
  → monitor 抽取 {systolic: 155, diastolic: 95, timestamp}
  → 语音数值确认（适老化）：「您说的是收缩压 155、舒张压 95，对吗？」
  → memory.write 写入 Patient KG（VitalSign 节点 + 测量_AT 关系）
```

## 异常阈值

| 指标 | 阈值 | 动作 |
|------|------|------|
| 收缩压 | ≥ 180 | 触发 emergency |
| 舒张压 | ≥ 120 | 触发 emergency |
| 症状关键词 | 胸痛 / 视力模糊 | 触发 emergency |
| 心理危机 | 不想活 / 活着没意思 | 危机话术 + 心理热线（guard 确认） |

## emergency 分支（并行扇出）

```
monitor 触发 emergency
  → asyncio.gather 并行：education（基于 Domain KG 给一般性建议）+ motivation（安抚，压力管理 Prompt）
  → 两路输出合并 → guard_check 审查 → TTS
  → guard 确认 → 写 CrisisEvent + 邮件通知子女/社区医生（见 docs/agents/guard.md）
```

## Patient KG 写入内容（memory 模块抽取范围）

- VitalSign（血压 / 心率 / 体重）
- Symptom（头晕、头痛、心悸、失眠等）
- Emotion（焦虑、抑郁、愉快等）
- LifeEvent（家庭变故、饮食变化、运动）

**明确不写入**：Medication、药物相关 LifeEvent。

## 验收标准

- 输入「收缩压 185」→ 触发 emergency 并行广播
- 输入「155/95」→ 语音确认后写入 Patient KG，无异常不打扰
- Patient KG 中不存在 Medication 标签节点