# 平台级语音交互

平台语音能力是现有 Agent 聊天的输入输出渠道，不是新的 Voice Agent。当前版本支持非实时语音输入和按需语音输出，并统一复用“模型供应商”中的 Qwen/阿里云百炼配置；未配置语音模型时不影响普通文字聊天。

## 使用方式

### 语音输入

1. 在聊天输入区点击麦克风并允许浏览器访问麦克风。
2. 点击停止，等待平台完成转写。
3. 转写文字会回填到当前输入框，用户可以直接修改。
4. 只有点击普通发送按钮后，文字才进入原有 Request、AgentRun、Redis Stream 和 SSE 链路。

取消录音、权限拒绝、空转写和转写失败都不会创建消息或 AgentRun。切换会话、离开页面和组件销毁时会释放媒体流。浏览器产生的 WebM、OGG 或 MP4 录音会在服务端临时转换为 16kHz 单声道 WAV，再提交给 Qwen-ASR；请求结束后立即清理临时文件。

### 按需朗读

- 助手消息完成后显示“朗读”按钮，默认不会自动播放。
- 未选择文字时朗读助手可见正文；选中助手正文的一部分时只朗读选区。
- 支持播放、暂停、继续和停止；页面同时只维护一个音频播放器。
- Markdown 标记、工具调试状态和隐藏推理内容不会作为朗读事实来源。
- 超过平台文字限制时需要选择部分内容，系统不会静默截断或让 LLM 重新总结。

朗读接口只处理页面已有的助手文字或用户选区，不调用 Agent、工具、知识库或图谱，也不会新增消息。

## 统一模型供应商配置

语音 URL、API Key 和模型名不再通过独立的 `VOICE_ASR_*`、`VOICE_TTS_*` 凭据配置。管理员在“智能体管理 → 模型供应商”中维护一份阿里云百炼供应商记录，ASR 与 TTS 共同复用该记录的 `api_key` 或 `api_key_env`。

### 1. 复用现有供应商

优先编辑已经配置好 Key 的 `alibaba-cn`，不需要新建第二个供应商，也不需要再填写一份语音 Key：

- `API Key 环境变量`：推荐填写 `DASHSCOPE_API_KEY`；也可以在供应商记录中直接配置 Key。
- `Base URL`：保留该供应商现有聊天模型地址即可，语音模型使用自己的 Base URL 覆盖。
- `能力`：加入 `asr` 和 `tts`；如果同一供应商还承载聊天、向量或重排模型，保留原有能力。

同一份 Key 可以同时服务 Qwen 对话、ASR 和 TTS。北京、新加坡等地域的 API Key 与 Workspace 地址必须属于同一地域。

### 2. 获取并启用 Qwen-ASR 模型

在该供应商的“模型配置”中点击“获取远端模型”。平台会根据已知的 Qwen 模型 ID 自动把 `qwen3-asr-flash` 及其日期版本识别为 `asr`，协议填为 `qwen_asr`。点击“启用”即可，不需要重复创建模型供应商。

如果该模型已经以旧版本 `chat` 类型启用，按钮会显示“同步语音配置”；点击后会原地更新模型类型和协议，不会新增供应商或重复保存 Key。必要时再在模型配置中补充百炼控制台提供的 Workspace Base URL：

| 字段 | 配置 |
| --- | --- |
| 模型 ID | `qwen3-asr-flash` |
| 模型类型 | 自动识别为 `asr` |
| 协议覆盖 | `qwen_asr` |
| Base URL 覆盖 | 百炼控制台对应地域的 Workspace OpenAI 兼容地址，例如 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` |
| 语音模型扩展参数 | `{}` |

ASR 通过 `POST /chat/completions` 提交 Base64 Data URL。当前平台限制原始录音不超过 7 MiB，以保证 Base64 编码后仍低于百炼 10 MB 输入限制。

### 3. 获取并启用 Qwen-TTS 模型

同样在现有供应商中获取远端模型。平台会把 `qwen3-tts-flash` 及其日期版本自动识别为 `tts`，协议填为 `qwen_tts`，并预填北京地域的原生 TTS Base URL、`Cherry` 音色和中文配置。点击“启用”即可；旧的 `chat` 记录也可以通过“同步语音配置”原地修正。

只有远端列表没有该模型，或使用尚未纳入自动识别规则的模型时，才需要手动添加：

| 字段 | 配置 |
| --- | --- |
| 模型 ID | `qwen3-tts-flash` |
| 模型类型 | `tts` |
| 协议覆盖 | `qwen_tts` |
| Base URL 覆盖 | 北京地域使用 `https://dashscope.aliyuncs.com/api/v1`；其他地域按百炼官方文档填写 |
| 语音模型扩展参数 | 见下方 JSON |

```json
{
  "voices": ["Cherry"],
  "default_voice": "Cherry",
  "language_type": "Chinese",
  "max_characters": 600
}
```

`voices` 必须填写模型实际支持的音色。Qwen3-TTS-Flash 当前按 WAV 返回，供应商可能返回内联 Base64 音频或签名阿里云临时音频地址，平台会兼容两种响应；平台固定使用 `speed=1.0`。如需指令控制语速，需要后续单独接入 `qwen3-tts-instruct-flash`，当前版本不伪造不受支持的语速参数。

### 4. 唯一模型规则

平台分别要求模型缓存中只有一个启用的 `asr` 模型和一个启用的 `tts` 模型：

- 没有对应模型时，能力接口返回 `disabled`；
- 同类型配置多个模型时，能力接口返回 `missing_configuration`，避免随机选择；
- 供应商停用、Key 缺失、协议错误或 Base URL 缺失时，能力保持不可用。

同一供应商可以同时保留 `chat`、`embedding`、`rerank`、`asr` 和 `tts` 模型；模型类型和协议是模型级元数据，不是新的供应商配置。

修改供应商或模型后，管理接口会自动刷新模型缓存，无需重启容器。

## 环境变量

`.env` 只保留语音运行限制；URL、Key、模型与音色均从模型供应商配置读取：

```dotenv
# 供应商记录的 api_key_env 可引用此变量
DASHSCOPE_API_KEY=

VOICE_ASR_TIMEOUT_SECONDS=60
VOICE_TTS_TIMEOUT_SECONDS=60
VOICE_MAX_AUDIO_BYTES=7340032
VOICE_MAX_AUDIO_SECONDS=120
VOICE_RATE_LIMIT_PER_MINUTE=10
VOICE_CACHE_TTL_SECONDS=300
```

## API

| 接口 | 作用 | 结果 |
| --- | --- | --- |
| `GET /api/voice/capabilities` | 查询当前能力 | ASR/TTS 启用状态及限制 |
| `POST /api/voice/transcriptions` | 上传临时录音 | 转写草稿 JSON |
| `POST /api/voice/speech` | 合成已有文字 | 受控音频流 |
| `POST /api/voice/metrics` | 上报固定交互事件 | `204 No Content` |

所有接口都要求登录。原始录音只在本次 ASR 中临时落盘并在请求结束后清理；数据库不新增音频消息表。TTS 音频只使用 Redis 短期缓存，缓存键为脱敏哈希，不包含 API Key、Token 或明文全文。服务端只接受 Qwen 响应中的内联 Base64 音频或 `*.aliyuncs.com` 临时地址；供应商返回 HTTP 地址时只对白名单阿里云域名升级为 HTTPS，不下载任意第三方 URL。

## 错误处理

客户端应根据错误响应中的 `detail.code` 提示下一步操作。当前统一支持：

- `voice_not_configured`、`provider_timeout`、`provider_unavailable`、`rate_limited`；
- `unsupported_audio_type`、`audio_too_large`、`audio_too_long`、`empty_transcript`；
- `speech_text_too_long` 和语音参数校验错误。

供应商原始 URL、凭据和原始响应不会返回给用户或写入日志。

## 评测与指标

语音请求会在 Redis 中保存 7 天脱敏聚合指标：请求/成功/错误计数、错误码、缓存命中、延迟分桶、录音时长分桶和文字长度分桶。前端只上报固定事件名，用于统计录音取消率、转写完成率、朗读启动/停止/完成率和选区朗读比例，不上传音频、转写全文或朗读全文。

真实 Qwen ASR/TTS 成功率和 P50/P95/P99 必须在管理员配置真实 Key、Workspace 地址和音色后，用固定音频/固定文本基准运行。当前代码测试只验证请求协议、参数映射、浏览器音频转换、错误脱敏和主窗口接入，不用 Mock 结果冒充真实供应商性能数据。
