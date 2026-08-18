# 调研：Pipecat 语音接入技术选型（Windows 10 / Python 3.13）

> 状态：调研完成，待 `/grill-with-docs` 敲定架构。日期：2026-08-16。
> 背景：后端 T1–T8 已验收（120 测试全绿），本调研支撑「语音接入」阶段的四个决策点。

## 0. 结论摘要

| 决策点 | 结论 | 一句话理由 |
|--------|------|-----------|
| 1. Pipecat 在 Windows 上的可行性 | **可行**，v1.7.0（2026-08） | 官方自 v0.0.52 起支持 Python 3.13；主库在 Windows 上全量测试通过；本项目采用「服务端 pipeline + 浏览器 WebSocket 采集/播放」，完全绕开 Windows 本地音频栈（pyaudio）风险 |
| 2. STT 提供商 | **SenseVoiceSmall（FunASR）本地优先**，Whisper 为降级备选 | 中文 CER 7.81%（Whisper-large-v3 为 20.02%）、内置粤语（yue）+ 中文方言组识别、CPU 可跑 17x realtime、有 OpenAI 兼容 API 与 Windows 预编译二进制，满足适老化「方言支持」 |
| 3. TTS 提供商 | **Edge TTS（微软神经语音）默认**；Azure TTS 为付费升级路径 | 免费、中文音色成熟（晓晓/云希等）、`rate`/`pitch` 参数直接满足「温暖缓慢」；Pipecat 官方 `EdgeTTSService` 即封装此接口 |
| 4. 对话形态 | **半双工按轮**（STT 一句话 → `handle_user_input` → TTS 一句话） | 与后端逐轮式接口天然匹配；`SystemContext` 跨轮保持是 `pending` 状态机的硬要求；不引入全双工流式的改动成本 |
| 5. 接入点 | `app/chat.py` 的 `handle_user_input` 为唯一语音入口；Pipecat 只做「音频 ↔ 文本」适配 | 语音层拿到 `handle_user_input` 返回值即可直接 TTS，**不重复 guard**（返回值已过 `guard_check`） |

## 1. Pipecat 平台现状

### 1.1 版本与 Python 支持

- 最新稳定版 **v1.7.0**（2026-08-01 发布）[release](https://github.com/pipecat-ai/pipecat/releases/tag/v1.7.0)。
- **Python 3.13 官方支持**：v0.0.52 起明确支持（audioop 依赖替换为 audioop-lts）[release note](https://github.com/pipecat-ai/pipecat/releases/tag/v0.0.52)。本项目 Python 3.13.12 满足要求。
- 仓库活跃：2026-07/08 期间持续合并 PR（#5227、#5205 等），中文/CJK 支持有官方维护（CJK TTS 时间戳修复 #4517、#2307）。

### 1.2 Windows 支持结论

- 主库可跨平台运行：贡献者在 Windows 上跑全量测试套件（2635+ passed，仅 21 个可选服务 extra 模块未收集）[#5205](https://github.com/pipecat-ai/pipecat/pull/5205)。
- 历史 Windows 问题（#957「FastAPI WebSocket 在 Windows 上音质差」）均发生在**本地音频输入输出栈**（pyaudio 采集/播放），2026-03 已关闭 [issue](https://github.com/pipecat-ai/pipecat/issues/957)。
- **规避策略**：服务端 pipeline 不碰音频设备——浏览器端 `getUserMedia` 采集麦克风、`AudioContext` 播放；服务端用 `FastAPIWebsocketTransport`（官方一等传输，序列化用 protobuf/JSON）收音频、回音频。本项目后端本就是 FastAPI，生命周期天然契合。
- 结论：Windows 10 不是阻断项；最大的风险点从「平台」转移到「本地推理模型能否在本机 CPU 上跑动」（见 §2）。

### 1.3 与本项目栈的匹配度

| 项目现有技术 | Pipecat 对应能力 |
|-------------|-----------------|
| LLM：本地 Ollama（qwen2.5:7b，OpenAI 兼容） | 原生 `OllamaLLMService`（v1.7 仍维护，`developer` 角色转换已修 #5027）——但本项目**不需要** pipecat 的 LLM 节点，LLM 由后端 `app/llm.py` 负责 |
| 编排：async 函数 + 路由（ADR-0002） | pipecat 只做音频适配器，编排仍留在后端，**不违背 ADR-0002**（pipecat 是 ADR-0002 明确保留的外部服务） |
| FastAPI（`app/main.py`） | `FastAPIWebsocketTransport` / `FastAPIWebRTCTransport` 可直接挂在现有 FastAPI 应用上 |

## 2. STT 候选评估（适老化：方言支持为硬性要求）

评估维度：中文准确率、**中文方言识别**、CPU 可行性、Pipecat 集成难度、成本/隐私。

### 2.1 SenseVoiceSmall（FunASR / 阿里达摩院）—— 推荐

- 语言：zh/en/ja/ko/**yue（粤语）**；`Fun-ASR-Nano` 额外覆盖中文方言组与地域口音（需 GPU）[model zoo](https://github.com/modelscope/FunASR)。
- 中文 CER **7.81%**（FunASR 官方 benchmark），CPU **17x realtime**，情绪/音频事件识别内建 [benchmark](https://github.com/modelscope/FunASR)。
- 集成路径（三选一）：
  1. `funasr-server` 起 OpenAI 兼容 API（`POST /v1/audio/transcriptions`），Pipecat 用自定义 STT 节点或 HTTP 适配调用 [server](https://github.com/modelscope/FunASR#deploy)；
  2. llama.cpp 预编译 **Windows x64** 二进制（SenseVoice GGUF，CPU/Vulkan/CUDA 包）[release](https://github.com/modelscope/FunASR/releases/tag/runtime-llamacpp-v0.2.0)——无 Python 运行时、最轻；
  3. 进程内 `funasr` Python 包（需 torch，Python 3.13 可用，但依赖较重）。
- 风险：非流式模型需按「一句一识别」使用（对半双工形态无影响）；首次需下载模型（~230MB 或 GGUF q8 ~120MB）；SenseVoice 中文输出默认带情绪标签，需 `rich_transcription_postprocess` 清理。

### 2.2 faster-whisper（Pipecat 原生 `BaseWhisperSTTService`）

- Pipecat 原生支持（whisper extra），本地、离线。
- 中文 CER 20.02%（large-v3）——约为 SenseVoice 的 2.5 倍；**无中文方言专项**，粤语/四川话等老人口音表现差。
- 定位：SenseVoice 不可用时的降级备选（Pipecat 集成成本最低）。

### 2.3 云端 API（AssemblyAI / Azure 等）

- AssemblyAI U3 Pro 支持 `zh` 语言声明与 keyterms/prompt 调优，但 `domain=medical-v1` **仅支持 en/es/de/fr**，中文无医疗专项；无中文方言识别 [docs](https://docs.pipecat.ai/api-reference/server/services/stt)。
- 腾讯云 STT 曾以 PR 提交给 pipecat（#476）但未合并。
- 定位：数据出本机 + 付费 + 方言不足 → 不符合本项目（隐私敏感的健康数据 + 方言硬要求）。

### 2.4 STT 推荐结论

**默认：SenseVoiceSmall（本地）。** 唯一同时满足「中文准确率」「方言（粤语/口音组）」「本地隐私」「CPU 可跑」的方案。接入形态建议路径 1 或 2（独立服务/二进制，与后端进程解耦），避免 torch 塞进后端 `.venv`。

## 3. TTS 候选评估（适老化：温暖缓慢的语音）

评估维度：中文音色成熟度、**语速/音调可调**、Pipecat 集成、成本。

### 3.1 Edge TTS（微软神经网络语音）—— 推荐默认

- Pipecat 原生 **`EdgeTTSService`**（免费，走微软 Edge 朗读接口，无需 API key）[docs](https://docs.pipecat.ai/api-reference/server/services/tts/edge-tts)。
- 中文音色成熟：晓晓（温暖女声）、云希（男声）等数十个，`rate`（-50%..+50%）与 `pitch` 参数直接实现「缓慢 + 温和」，满足架构约束 #4。
- 依赖：需联网访问微软语音服务；接口非正式商业 SLA（社区长期使用稳定）。
- 定位：零成本默认路径，MRP 阶段完全够用。

### 3.2 本地 TTS（Piper / Kokoro）

- `PiperTTSService`：Piper 社区有 `zh_CN-huayan` 中文音色，本地离线；但音色质量与「温暖」要求有差距，且中文音色选择少。
- `KokoroTTSService`：官方支持语言为英/法/德/意/葡/西，**不含中文**（v1.7 release notes 明确列出）[release](https://github.com/pipecat-ai/pipecat/releases/tag/v1.7.0) → 排除。
- 定位：断网降级备选（Piper zh）。

### 3.3 Azure TTS —— 付费升级路径

- `AzureTTSService`（Pipecat 原生），多语种神经语音、`rate`/`pitch`/情感 SSML 全面，CJK 时间戳支持已修复 [docs](https://docs.pipecat.ai/api-reference/server/services/tts/azure-tts)。
- 定位：产品化/量产时替换 Edge TTS 的平滑升级（同为微软语音体系，`edge-tts` 即 Azure TTS 的免费边缘端点）。

### 3.4 TTS 推荐结论

**默认 Edge TTS**（免费、中文成熟、慢速可调、Pipecat 原生）；**Piper（zh）为离线降级**；**Azure TTS 为量产升级**。三者都走 Pipecat 的 `TTSService` 抽象，配置化切换，不产生架构改动。

## 4. 对话形态：半双工按轮（推荐）vs 全双工流式

| 维度 | 半双工按轮（推荐） | 全双工流式 |
|------|-------------------|-----------|
| 与后端匹配 | `handle_user_input` 一次进一次出，**零后端改动** | 需后端改流式/多轮并行，违反逐轮式设计 |
| `ctx.pending` 状态机 | 天然保持（同一 SystemContext 跨轮） | 易并发错乱 |
| 打断/插话（barge-in） | 不支持（老人场景非必需） | 支持 |
| 实现复杂度 | 低：自定义 processor 桥接 STT 文本 → 后端 → TTS | 高：streaming 聚合、VAD 策略、interruption 处理 |
| Pipecat 用法 | 自定义 pipeline 节点（或复用现成 example 的按轮模式） | `LLMUserAggregator` + turn strategies（本项目不用 LLM 节点，需大量定制） |

结论：**半双工按轮**。与交接文档判断一致（决策点 3），后端不需要为流式改——它本来就是一次输入一次输出。Pipecat 侧用「STT 收一句 → 自定义 processor 调 `handle_user_input` → TTS 播一句」的线性 pipeline。

## 5. 集成切入点（对接现有代码）

```
浏览器（老人端）麦克风/扬声器
   ↕ WebSocket（FastAPIWebsocketTransport，protobuf 或 JSON 序列化）
FastAPI（app/main.py 挂 /ws 端点）
   ↕ 自定义 VoiceProcessor（pipecat 处理器）
   app/chat.py::handle_user_input(user_text, ctx)  ← 唯一文本入口，返回值已过 guard_check，直接 TTS
   ↕
EdgeTTSService → 浏览器播放
```

- `SystemContext` 跨轮保持：WebSocket 连接级持有（连接建立时组装，`pending`/memory_snapshot 随轮更新），pipecat processor 持引用即可。**pending 状态机的连续性依赖这一点**。
- 语音配置（STT 类型、TTS 类型/音色/语速、服务地址）加入 `app/config.py::Settings` + `.env.example`，与现有 frozen dataclass 风格一致。
- 生命周期：`app/main.py` lifespan 中可选预加载 TTS 服务；STT 若走独立 `funasr-server`/llama.cpp 进程则作为外部服务依赖（启动脚本/文档说明），不进后端进程。
- `app/device/blood_pressure.py` 不动：MRP 数据入口即语音（血压数值经 monitor 确认状态机录入）。

## 6. 测试策略

- 回归底线：现有 120 测试保持全绿（语音层不动 `handle_user_input` 逻辑）。
- 新增「语音管线装配」级测试：STT/TTS 用替身（fake STT 返回固定文本 → 断言 `handle_user_input` 被正确调用且 `SystemContext` 跨轮保持；fake TTS 断言收到 guard 后文本）。
- 不做真实音频端到端（CI 无麦克风/扬声器）；真实语音冒烟放本机手动验证。
- pipecat 本身不写测试（第三方框架），只测试我们自己的桥接层。

## 7. 风险清单

| 风险 | 等级 | 缓解 |
|------|------|------|
| SenseVoice 中文输出带情绪标签需清洗 | 低 | `rich_transcription_postprocess`；装配测试覆盖 |
| Edge TTS 依赖微软服务器、无 SLA | 低 | Piper(zh) 离线降级开关；配置化切换 |
| pipecat 大版本 API 变动（当前 1.x，2.0 计划中） | 中 | 锁定 `pipecat-ai==1.7.0`；桥接层薄封装，隔离上游 API |
| Windows 下首次跑 pipecat 的音频依赖链 | 低 | 纯 WebSocket 传输不碰本地音频设备；浏览器承担采集/播放 |
| 方言覆盖以粤语为标杆（SenseVoice 内置 yue），其他方言走 Fun-ASR-Nano（需 GPU） | 中 | MRP 以普通话 + 粤语验证；方言扩展留待 Fun-ASR-Nano/GPU 路径 |
| `.venv` 体积与依赖冲突（funasr 带 torch） | 低 | STT 走独立服务/二进制，不进后端 `.venv` |

## 8. 参考资料

- Pipecat v1.7.0 release（含 Python 3.13、Kokoro/Piper 本地 TTS、PocketTTS 新增）: https://github.com/pipecat-ai/pipecat/releases/tag/v1.7.0
- Pipecat Python 3.13 支持（v0.0.52）: https://github.com/pipecat-ai/pipecat/releases/tag/v0.0.52
- Windows 音质 issue（本地音频栈，已关闭）: https://github.com/pipecat-ai/pipecat/issues/957
- Windows 全量测试证据: https://github.com/pipecat-ai/pipecat/pull/5205
- FastAPI WebSocket 传输（官方文档）: https://docs.pipecat.ai/
- FunASR 主仓库（SenseVoiceSmall / Fun-ASR-Nano / benchmark / Windows llama.cpp 运行时）: https://github.com/modelscope/FunASR
- FunASR Windows 预编译二进制（llama.cpp runtime v0.2.0）: https://github.com/modelscope/FunASR/releases/tag/runtime-llamacpp-v0.2.0
- AssemblyAI STT（Pipecat 集成，`zh` 语言声明，medical domain 不含中文）: https://docs.pipecat.ai/api-reference/server/services/stt
- 中文/CJK TTS 时间戳修复（官方维护中文支持的证据）: https://github.com/pipecat-ai/pipecat/pull/4517
- 中文对话支持 PR（腾讯云 STT / ChatTTS，未合并）: https://github.com/pipecat-ai/pipecat/pull/476