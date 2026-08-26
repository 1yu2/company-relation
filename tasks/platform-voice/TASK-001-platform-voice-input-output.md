# TASK-001：平台级语音输入与按需语音输出

| 属性 | 内容 |
| --- | --- |
| 状态 | Qwen 首版已完成（真实供应商待配置验收） |
| 类型 | 平台横切能力 |
| 优先级 | P1 |
| 依赖 | 现有聊天输入、消息渲染、AgentRun、认证和系统配置链路 |
| 可并行任务 | 语音契约冻结后，后端供应商适配与前端录音/播放组件可并行 |

## 目标

为平台所有 Agent 提供统一的非实时语音输入和按需语音输出，不改变现有 Agent 的业务输入输出逻辑：

1. 用户可以在聊天输入区录音，将音频转写为可编辑文字，再按普通消息发送。
2. 用户可以对助手已经生成的消息或选中文字执行朗读、暂停、继续和停止。
3. 用户在问题中要求“简短回答”“只说重点”时，继续由当前 Agent 控制文字长度；语音层只朗读最终文字，不额外生成事实。
4. 默认不自动播放，不默认朗读长回答全文，不永久保存原始录音。

## 最小验收标准

### 语音输入

- 聊天输入区提供麦克风按钮，支持开始、停止、取消和重新录音。
- 停止录音后调用平台 ASR 接口，转写结果回填当前输入框。
- 用户可以检查和修改转写结果，只有主动发送才创建普通 Agent 请求。
- 转写失败、取消录音或空转写不会创建消息、Request 或 AgentRun。
- 录音期间不能重复开始录音；切换会话、退出页面和组件销毁时会停止录音并释放媒体资源。

### 语音输出

- 助手消息提供“朗读”入口，用户选中文字时可以只朗读选中部分。
- 支持播放、暂停、继续和停止；同一时间只允许一个平台音频播放器占用输出。
- 切换会话、删除消息或开始朗读另一条消息时，旧音频停止且不串到新会话。
- 默认不自动播放；长回答由用户选择全文或选中部分，首版不自动调用 LLM 总结。
- TTS 输入只接受已有助手消息正文或用户选择的子串，不重新调用 Agent，不重新查询工具。

### 安全与隐私

- 音频接口要求登录认证，并复用现有用户身份和访问控制。
- 服务端限制允许的 MIME、单文件大小、录音时长、文本长度、语速范围和请求频率。
- 原始录音默认仅用于本次 ASR，成功或失败后按配置清理；数据库不新增永久音频消息副本。
- TTS 音频使用短期缓存或流式返回；缓存键不包含用户凭据，过期后可清理。
- 日志不记录音频二进制、完整供应商请求、API Key、Token 或敏感连接信息。

### 兼容性

- 默认助手、知识库 Agent、深度研究 Agent、SubAgent 和现有领域 Agent 均可复用同一语音入口。
- 普通文字消息、附件、审批、Steer、请求排队、取消、恢复和 SSE 行为不因语音入口改变。
- `LITE_MODE` 仍可使用平台语音接口，前提是配置了可用供应商；未配置时返回明确的能力不可用状态。

## 输入输出链路

### 语音输入

```text
浏览器麦克风
  → MediaRecorder 生成受限音频
  → POST /api/voice/transcriptions
  → VoiceService 调用 ASR Provider
  → 返回 transcript、language、duration 和 provider metadata
  → 前端把 transcript 回填 AgentInputArea
  → 用户确认或修改
  → 复用现有普通文字发送链路
  → Request / AgentRun / Redis Stream / SSE
```

### 语音输出

```text
已有助手消息或用户选中文本
  → POST /api/voice/speech
  → VoiceService 校验长度、声音和语速
  → TTS Provider 返回音频
  → 全局单实例播放器播放
  → 暂停 / 继续 / 停止 / 切换消息清理
```

语音模块不能跳过现有消息链路直接调用 Agent，也不能从 Agent 的内部状态拼装另一份回答。

## 用户交互规则

### 输入区

- 默认显示麦克风按钮，不改变当前文字输入和附件按钮布局。
- 首次录音由浏览器请求麦克风权限；拒绝权限时展示可理解的恢复提示。
- 录音中展示状态和时长，并提供停止、取消操作。
- 转写中禁止重复提交同一音频，但不阻塞用户切换到手动文字输入。
- ASR 成功后只更新输入框草稿，不自动点击发送。

### 消息区

- 助手消息的操作区增加朗读按钮，不为用户消息默认提供 TTS。
- 没有选中文字时朗读当前助手消息的可见正文；有选区时只朗读合法选区。
- Markdown、代码块、表格、引用和工具状态需要先转换为可朗读纯文本，不能直接朗读 Markdown 标记或隐藏调试数据。
- 超过最大 TTS 字符数时提示用户选择部分内容，不静默截断导致语义不完整。
- 用户希望语音简短时，应在问题中要求 Agent 简短回答；平台语音层不自行改写答案。

## 后端设计

### 模块边界

建议新增：

```text
backend/package/yuxi/voice/
├── models.py
├── service.py
└── providers/
    ├── base.py
    └── <provider>.py

backend/server/routers/voice_router.py
```

- `models.py`：冻结 ASR/TTS 请求、响应、能力状态和错误契约。
- `providers/base.py`：定义 `transcribe`、`synthesize` 和健康检查接口。
- Provider 实现：只负责供应商协议转换，不处理聊天、消息或 AgentRun。
- `service.py`：负责供应商选择、输入限制、临时文件生命周期、超时、错误映射和短期缓存。
- `voice_router.py`：负责认证、上传适配、HTTP 状态和响应，不直接调用供应商 SDK。

### API 契约

#### `POST /api/voice/transcriptions`

使用 `multipart/form-data` 上传音频：

```text
file: 音频文件
language: 可选语言提示
```

响应：

```json
{
  "transcript": "转写后的文字",
  "language": "zh-CN",
  "duration_ms": 5200,
  "provider": "configured-provider",
  "request_id": "语音请求标识"
}
```

首版不承诺所有供应商都返回可靠的置信度；不能用伪造的固定置信度误导前端。

#### `POST /api/voice/speech`

```json
{
  "text": "需要朗读的已有助手文字或选中内容",
  "voice": "Cherry",
  "speed": 1.0,
  "format": "wav"
}
```

返回音频流或带短期有效期的受控音频地址。供应商失败时返回明确错误，不回退为浏览器本地声音而隐藏配置问题。

#### `GET /api/voice/capabilities`

返回平台当前是否启用 ASR/TTS、允许格式、大小、时长、文字上限、声音和语速范围。前端不得硬编码供应商能力。

### 配置

平台配置统一维护在“模型供应商”：

- 同一阿里云百炼供应商记录保存一份 `api_key` 或 `api_key_env`；
- `asr` 模型保存 `qwen_asr` 协议和 Workspace 级 OpenAI 兼容 Base URL；
- `tts` 模型保存 `qwen_tts` 协议、百炼原生 Base URL、声音和语言；
- 模型缓存中分别只能启用一个 `asr` 和一个 `tts` 模型；
- `.env.template` 只保留超时、音频大小、录音时长、限流和缓存 TTL。

## 前端设计

### 预计新增

```text
web/src/apis/voice_api.js
web/src/components/voice/VoiceRecorderButton.vue
web/src/components/voice/VoicePlaybackControl.vue
web/src/composables/useVoiceRecorder.js
web/src/composables/useVoicePlayback.js
```

### 预计修改

- `web/src/components/AgentInputArea.vue`：挂载录音入口、转写状态和输入框回填。
- `web/src/components/AgentMessageComponent.vue`：在助手消息操作区挂载按需朗读。
- 会话页面或全局布局：维护单实例播放器，切换会话时停止旧音频。
- 用户配置页面：首版只展示平台是否可用；用户声音偏好可后续拆分。

前端 API 必须集中在 `web/src/apis/voice_api.js`，样式复用 `base.css` 变量，图标优先使用 `lucide-vue-next`。

## 状态与错误契约

### 前端录音状态

```text
idle
requesting_permission
recording
stopping
transcribing
ready
error
```

### 前端播放状态

```text
idle
loading
playing
paused
stopped
error
```

### 后端错误分类

- `voice_not_configured`：平台未配置对应语音能力；
- `unsupported_audio_type`：音频格式不支持；
- `audio_too_large`：文件超过限制；
- `audio_too_long`：录音时长超过限制；
- `empty_transcript`：供应商未识别出有效文字；
- `speech_text_too_long`：朗读文字超过限制；
- `provider_timeout`：供应商超时；
- `provider_unavailable`：供应商不可用；
- `rate_limited`：用户或平台请求达到限制。

错误提示应告诉用户下一步操作，不展示供应商凭据、内部 URL 或完整原始响应。

## 实现步骤

1. 冻结 ASR、TTS、能力查询、错误和临时音频生命周期契约。
2. 定义供应商无关的 Provider 接口和平台配置，不在路由中写供应商分支。
3. 实现认证后的能力查询、音频转写和语音合成接口。
4. 实现输入区录音、权限、取消、上传、转写和草稿回填。
5. 实现助手消息朗读、选中文字朗读和全局单实例播放器。
6. 增加 MIME、大小、时长、文本、超时、限流、缓存和清理约束。
7. 补充单元、API 集成和关键聊天 E2E，确认不会绕过现有 AgentRun。
8. 更新 `.env.template`、正式用户文档、导航和 changelog。
9. 在 Docker 容器执行格式、测试、Lint 和生产构建。

## 测试范围

### 单元测试

- ASR/TTS 请求模型、长度限制、格式校验和错误映射。
- Provider 超时、空转写、能力未配置和敏感错误清理。
- 临时音频清理和短期缓存键不包含凭据。
- 录音状态机、重复点击、取消和组件销毁资源释放。
- 播放、暂停、继续、停止、切换消息和切换会话。
- Markdown/代码块/表格转换为可朗读纯文本。

### 集成测试

- 登录用户可以查询能力、上传合法测试音频并获取转写。
- 未登录、非法 MIME、超限文件和 Provider 失败返回正确状态。
- TTS 返回正确媒体类型，超长文本被明确拒绝。
- 转写接口不创建聊天消息、Request 或 AgentRun。
- TTS 接口不调用 Agent、不产生工具执行和新的助手消息。

### E2E 测试

- 在聊天输入区录音，转写回填后手动发送，正常进入现有 AgentRun 和 SSE 链路。
- 用户修改转写内容后发送，消息历史只保存修改后的文字。
- 点击助手消息朗读并执行暂停、继续、停止。
- 选中助手消息的一部分，只朗读选中内容。
- 切换会话后旧音频停止，不污染新会话。
- 语音能力未配置时，文字聊天仍完整可用。

## 质量与性能指标

首版至少记录脱敏聚合指标：

- ASR 请求成功率、空转写率、P50/P95/P99 和音频时长分布；
- TTS 请求成功率、P50/P95/P99、文本长度和音频时长分布；
- 用户转写后编辑率、取消率和最终发送率；
- 朗读启动率、停止率、完整播放率和选区朗读比例；
- Provider 超时、限流和不可用次数。

指标不保存原始音频、完整转写、完整朗读文本或用户敏感内容。

## 验收标准

- [x] 所有主 Agent 聊天入口共享同一语音输入和输出组件。
- [x] 语音输入转写后只回填草稿，不自动发送。
- [x] 转写成功后使用现有文字消息和 AgentRun 链路。
- [x] 转写失败或取消不会创建消息、Request 或 AgentRun。
- [x] 助手消息和选中文字可以按需朗读。
- [x] 默认不自动播放，用户可以暂停、继续和停止。
- [x] 同一时间只有一个音频播放器，切换会话会停止旧音频。
- [x] TTS 不重新调用 Agent、不重新查询工具、不生成新事实。
- [x] 音频、文本、时长、速率和超时限制由后端统一校验。
- [x] 原始录音按临时策略清理，日志和缓存不泄露敏感内容。
- [x] 未配置语音供应商时返回明确能力状态，文字聊天不受影响。
- [x] URL、Key、模型和音色统一从模型供应商配置读取，不维护第二套语音凭据。
- [x] Qwen ASR/TTS HTTP Mock、模型缓存、配置校验和浏览器录音转换均有专项测试。
- [x] 真实 Qwen TTS 短文本调用已验证成功；ASR E2E 待管理员确认现有供应商中的 Workspace 地址后执行。
- [x] `.env.template`、用户文档、导航和 changelog 已更新，真实凭据仅保存在不提交的 `.env`。

## 2026-08-12 验证结果

- 本次模型供应商统一配置回归：模型配置、缓存、语音服务与路由专项 `57 passed`；真实远端清单已验证 Qwen3 ASR/TTS 非实时 Flash 模型能自动分类。
- Qwen TTS 协议专项 `20 passed`，真实短文本调用返回 `audio/x-wav` 音频；已兼容百炼内联 Base64 与签名阿里云临时音频地址。
- Docker 后端语音、模型缓存与供应商配置专项：`51 passed`。
- Docker 语音路由与语音领域专项：`20 passed`。
- 后端 Ruff 检查与格式检查：通过。
- 前端单元测试：`18 passed`；语音及模型供应商相关 ESLint：通过。
- 前端 Vite 生产构建：通过，仅保留第三方依赖纯注解和既有大分块警告。
- 当前运行态：同一 `alibaba-cn` 供应商下 TTS 已启用并可用；ASR 尚未启用，因为现有配置没有 Workspace 级 Base URL。远端通用列表会自动识别 `qwen3-asr-flash*` 为 `asr`、`qwen3-tts-flash*` 为 `tts`；启用 ASR 时在模型配置中补充 Workspace 地址即可，仍只复用一份供应商 Key。

## 非目标

- 不建设企业尽调、医疗、法律或其他专业领域的语音词典与专用 Voice Agent。
- 不新增独立语音 Agent、语音消息事实库或第二套会话历史。
- 不实现 WebRTC、全双工实时对话、流式 ASR/TTS 或用户打断 Agent。
- 不自动生成长回答摘要，不默认朗读完整报告或超长消息。
- 不使用声纹识别、语音唤醒、情感识别或说话人身份认证。
- 不通过语音直接确认审批、删除、权限变更等敏感操作。
- 不永久保存所有原始录音，不把音频用于未明确说明的训练或分析。
- 当前首版只实现 Qwen-ASR 与 Qwen-TTS；其他供应商协议后续按独立任务接入。
