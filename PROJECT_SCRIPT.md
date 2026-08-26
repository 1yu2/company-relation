# la-platform 项目介绍逐字稿

> 适用场景：面试/答辩/路演/技术分享
> 建议总时长：15–18 分钟
> 当前版本基于仓库 main 分支梳理，未提交到 Git

---

## 一、开场（30 秒）

“各位好，今天我介绍的项目是 **la-platform**，一个面向企业的 **RAG + 知识图谱 + 多智能体编排** 平台。

它解决的核心问题是：企业里有大量非结构化文档、结构化业务数据和领域知识分散在不同系统，传统搜索或单一大模型问答，要么找不到来源、要么无法做跨文档推理、更没法沉淀成可复用的智能体能力。

la-platform 把 RAG 检索、Milvus 知识库内的知识图谱、以及 LangGraph 多智能体编排，整合进一个多租户工作台，让管理员能配置知识库、模型和权限，业务用户则像用 ChatGPT 一样跟智能体对话，同时得到带引用来源、知识图谱推理和可交付产物的回答。”

---

## 二、项目定位与目标用户（1 分钟）

“用一句话概括：**让企业知识可被智能体检索、推理与交付**。

平台面向三类用户：

1. **企业管理员 / 运营**：负责配置知识库、接入模型供应商、管理部门权限、安装 Skills 和 MCP。
2. **普通业务用户**：通过聊天界面使用智能体，进行问答、上传文件、查询关系、生成报告。
3. **开发者 / 领域工程师**：基于平台扩展 Skill、MCP、子智能体、沙盒工具，甚至开发垂直领域的 Agent，比如我们做的广东企业尽调智能体。”

---

## 三、整体架构（2 分钟）

“整个平台采用 **Vue 3 + FastAPI + LangGraph** 的主干技术栈，全部用 Docker Compose 管理。

前端是 `web-dev` 容器，跑在 5173 端口；后端是 `api-dev` 容器，跑 FastAPI；还有一个独立的 `worker-dev` 容器，通过 ARQ 消费 Redis 队列来执行 AgentRun。

数据层用了五类存储：

- **PostgreSQL**：保存业务事实状态，包括用户、消息、对话线程、AgentRun、知识库元数据，以及 LangGraph checkpoint。
- **Redis**：负责 ARQ 任务投递、运行事件流、取消信号和跨进程缓存。
- **MinIO**：存附件、知识库原始文件和报告产物。
- **Milvus**：做向量检索和 BM25 稀疏向量检索。
- **Neo4j**：做知识图谱的关系推理。

一个典型请求的链路是：

1. 用户在前端输入消息，前端调用 `POST /api/agent/runs`。
2. 后端先把请求写入 PostgreSQL，创建用户消息和 AgentRunRequest。
3. 按用户、智能体、线程做 FIFO 排队控制，只有数据库提交成功后，才往 ARQ 投递任务。
4. Worker 加载 AgentRun、智能体配置和上下文，执行 LangGraph。
5. LangGraph 在执行过程中可以调用 LLM、工具、Skills、MCP、子智能体、RAG 检索和沙盒。
6. 运行事件写入 Redis Stream，前端通过 SSE 实时渲染；最终结果写回 PostgreSQL。

这里有一个很重要的架构不变量：**队列消息不能先于数据库状态可见**。也就是说，必须先落库，再投递任务。这样即使 Worker 重启或 Redis 丢数据，仍然可以从 PostgreSQL 恢复运行状态。”

---

## 四、智能体与编排能力（2 分钟）

“在 la-platform 里，智能体不是简单调用一次大模型，而是通过 **LangGraph + 中间件链** 组合出来的。

以内置的 ChatbotAgent 为例，它运行前会按顺序组装多个中间件：

- **SteerMiddleware**：注入系统提示，控制智能体行为。
- **沙盒文件系统中间件**：给智能体一个隔离的工作目录。
- **附件落盘中间件**：把用户上传的文件映射到沙盒路径。
- **SkillsMiddleware**：根据对话上下文动态激活 Skill，并展开它的依赖工具。
- **SubAgent 中间件**：支持调用子智能体完成独立任务。
- **摘要压缩中间件**：当上下文超过 token 阈值时，自动做双层摘要。
- **TodoList 中间件**：跟踪复杂任务的执行步骤。
- **工具审批中间件**：对敏感工具调用进行 approve/reject 控制。
- **Token 用量和重试中间件**：记录用量、处理模型异常。

这套设计的价值在于：**能力可以像插件一样叠加**，而不会把知识库、沙盒、扩展逻辑硬编码到单个路由或页面里。”

---

## 五、RAG 与知识库（2 分钟）

“RAG 是平台的核心能力之一。我们支持用户上传 pdf、docx、pptx、xlsx、图片、html、zip 等多种格式，系统会经过解析、分块、嵌入、入库，最后支持向量、关键词、混合三种检索模式。

文档解析用了工厂模式，可以根据配置选择 MinerU、PaddleX、RapidOCR、DeepSeek OCR、Docling 等引擎。

分块策略参考了 RAGflow 的设计，支持 general、book、qa、laws、semantic、separator 等多种 parser，按文档类型自动选择最合适的切分方式。

向量库基于 Milvus，每条 chunk 同时保存：

- `content`：原始文本，开启中文 analyzer；
- `embedding`：稠密向量，用 COSINE 相似度；
- `content_sparse`：BM25 稀疏向量。

检索时可以做向量 + BM25 混合检索，也可以走知识图谱路径：先做实体召回，再在三跳关系内扩散，最后把相关 chunk 召回并做 reranker 精排。返回结果带 source、file_id、score，前端会作为引用展示给用户。”

---

## 六、知识图谱抽取逻辑（2 分钟）

“接下来我详细讲一下知识库里的知识图谱是怎么抽出来的。

首先要明确一点：**图谱构建不是文件上传后自动发生的**。文档上传后先走解析、分块、向量入库，这时 chunk 已经生成，但 `graph_indexed` 是 false。用户需要在前端的知识图谱 Tab 里，先配置抽取器、锁定参数，再点‘开始索引’，才会触发一个后台 tasker 任务。

整个流程由 `MilvusGraphService.build_pending_chunks` 编排，可以分成七个阶段：

**第一阶段，配置校验。** 系统会检查 `graph_build_config` 是否已经 `locked`，只有锁定的配置才会被执行；同时目前只支持 Milvus 类型的知识库。

**第二阶段，加载待处理 Chunk。** 从 PostgreSQL 的 `knowledge_chunks` 表里，分批读取 `graph_indexed != True` 的 chunk。每批大小会根据并发 worker 数自动调整，范围在 100 到 1000 之间。

**第三阶段，并发 LLM 抽取。** 启动 `worker_count` 个抽取 worker，每个 worker 从队列取 chunk，调用 `LLMGraphExtractor` 进行抽取。当前唯一实现的抽取器就是 LLM。Prompt 是固定的，要求模型返回严格 JSON，格式包括：

```json
{
  "relations": [
    {
      "source": {"text": "...", "label": "...", "attributes": [...]},
      "target": {"text": "...", "label": "...", "attributes": [...]},
      "text": "关系显示文本",
      "label": "关系类型"
    }
  ]
}
```

如果用户在配置里填写了 schema，系统会把 schema 约束文本追加到 prompt 里。模型调用走 `select_model`，固定 60 秒超时，返回后先用 `json_repair` 容错解析，再做标准化。单个 chunk 抽取失败会自动重试 3 次，延迟分别是 2 秒和 10 秒。

**第四阶段，标准化。** 抽取结果会被转换成统一结构，包含 `entities`、`relations` 和 `metadata`。如果模型没给 `label`，默认会补成 `Entity` 或 `RELATED_TO`。

**第五阶段，写入 Neo4j。** 在单事务里完成三件事：MERGE Chunk 节点、MERGE Entity 节点并建立 `Chunk -[:MENTIONS]-> Entity` 关系、MERGE Entity 之间的关系边。这样就形成了图谱的拓扑结构。

**第六阶段，写入 PostgreSQL。** 把规范化后的实体写入 `knowledge_graph_entities` 表，三元组写入 `knowledge_graph_triples` 表，同时把 chunk 级引用写入 mentions 表。实体主键是 `hash(kb_id + normalized_name + label)`，同名同类型的实体用 `ON CONFLICT DO UPDATE` 合并。

**第七阶段，向量索引。** 独立的 `vector_worker` 消费 PG 里 `vector_status = pending` 的实体和三元组，生成 embedding 后写入 Milvus 的 `{kb_id}_entity` 和 `{kb_id}_triple` 集合，每个集合同样包含稠密向量和 BM25 稀疏向量。

这里有两个关键设计：

1. **先分块、后逐块抽取**。每个 chunk 独立调用 LLM，跨 chunk 的同名实体靠 `normalized_name + label` 去重合并。
2. **三层存储各司其职**：Neo4j 存图结构，PostgreSQL 存规范化记录和 mentions，Milvus 存向量索引。

另外，系统支持增量更新：新 chunk 入库后再点构建，只会处理未索引的 chunk。也支持失败重试、并发控制，以及 `reset` 全量重建和 `reconcile` 修复向量索引，保证图谱是可重建的。”

---

## 七、知识库评估与思维导图（2 分钟）

“知识库建好之后，还需要验证它到底能不能有效回答用户问题。平台提供了两套评测手段：**手工/自动 benchmark** 和 **思维导图**。

### 评估测试问题怎么来

**第一种是手工上传。** 用户可以上传 JSONL 文件，每行一条，包含 `query`、`gold_chunk_ids`、`gold_answer`。后端会把它们写入 PostgreSQL 的 `evaluation_datasets` 和 `evaluation_dataset_items` 表。

**第二种是 LLM 自动生成。** 前端在“生成评估集”里选择两种模式：

- **vector 模式**：随机选一个 anchor chunk，用它做向量检索，召回 top-K 邻居 chunk，把 anchor 和邻居拼成上下文，让 LLM 根据这段上下文生成问题和答案。
- **graph_enhanced 模式**：只从 `graph_indexed=True` 且含有 `ent_ids` 的 chunk 里选 anchor，然后用 Personalized PageRank 在知识图谱上做扩散，选出与 anchor 实体关联最紧密的 chunk 作为上下文。

生成时 Prompt 要求模型返回严格 JSON，包含 `query`、`gold_answer`、`gold_chunk_ids`，并且 gold chunk 必须是提供给它的上下文子集。生成结果会校验，不合格就丢弃，最多尝试 `max(count * 5, 50)` 次。

### 评测指标怎么算

运行评测时，系统会对每道题调用知识库的 `aquery` 做检索；如果配置了 `answer_llm`，还会用检索到的 top 5 chunk 生成答案。

指标分两类：

1. **检索指标**：Precision@K、Recall@K、F1@K，K 默认取 1、3、5、10。用召回的 chunk ID 和 `gold_chunk_ids` 比对。
2. **答案指标**：用 Judge LLM 判断生成答案与 `gold_answer` 是否在事实层面一致，返回 0 或 1，并附带 reasoning。

最后聚合出 `overall_score`：如果有答案指标，取答案正确率；否则取 recall@10。

### 思维导图怎么生成

思维导图不是从 chunk 内容或知识图谱抽的，而是**基于文件列表让 LLM 做分类**。

用户可以在知识库详情页的“知识导图”Tab 点击生成。后端读取当前知识库的文件元数据，最多取 200 个文件，然后让 LLM 生成一个 2–4 层的 JSON 树：根节点是知识库名，叶子节点必须是具体文件名。生成结果保存在 `KnowledgeBase.mindmap` 字段里。

增量更新时，系统会先对比现有文件列表，删除已不存在的文件分支，新增文件再调用 AI 整合进现有结构。Agent 也可以通过内置的 `get_mindmap` 工具读取这个导图，帮助用户快速了解知识库结构。

所以，评测验证的是‘检索和回答质量’，思维导图解决的是‘知识库结构可视化’，知识图谱解决的是‘实体关系推理’，三者是互补的。”

---

## 八、平台级语音交互（1.5 分钟）

“当前版本还新增了平台级语音能力，把它作为文字聊天的补充，而不是独立的语音 Agent。

用户在前端按住麦克风录音，音频上传到后端后调用 ASR 转写成文本，文本会回填到输入框，用户可以编辑确认后再发送。AI 回复出来后，如果用户开启了语音播报，会再调用 TTS 生成音频并播放。

后端提供了 `/api/voice/transcriptions` 和 `/api/voice/speech` 两个接口，封装了 OpenAI-compatible 的 ASR/TTS 服务商。未配置时不会影响文字聊天。

这个设计的好处是：**不改原有 Agent 链路**，语音只是输入输出层的增强，延迟和成本都可控。”

---

## 九、广东企业尽调智能体详细逻辑（4 分钟）

“接下来我详细讲一个在这个平台上落地的垂直领域 Agent：广东企业尽调智能体。它最能体现 la-platform 在复杂业务场景里的价值。

### 用户输入与输出

用户唯一需要做的，是在聊天框里输入自然语言问题，比如‘广东某某股份有限公司的股东有哪些’、‘000001.SZ 近三年的关系和风险’，或者直接点‘生成完整尽调报告’。

系统返回的是一个统一结构 `CompanyInvestigationResponse`，里面包含：系统理解后的主题、展示形式、时间范围、调查深度；已确认的企业身份；面向用户的简答；结构化事实、关系、路径、文本证据；所有来源；Claim Ledger 校验结果；数据质量声明；还有各阶段毫秒级耗时。

如果是完整报告，会提交一个异步任务，最终产物是 MinIO 上的 `report.json`、`report.md` 和 `record.json`。

### 企业身份解析

第一步是企业身份解析，这是一个**确定性流程**，不是让 LLM 猜。

`EntityResolutionService.resolve` 按固定优先级匹配：先 `eid`，再统一社会信用代码，再股票代码，再企业标准全称，再规范化名称。如果都不命中但提供了名称，就返回最多 20 个候选，状态标记为 `AMBIGUOUS`，要求用户确认。如果是 MySQL 连不上，就返回 `SOURCE_UNAVAILABLE`。

这里有两个关键设计：

1. **模糊匹配只返回候选，不允许 LLM 自动选择最终 `eid`**，防止把名字相近的企业搞混。
2. 企业提及提取用正则，能识别法律全称、18 位统一社会信用代码、6 位股票代码加 `.SZ/.SH/.BJ` 后缀。

### 意图与计划

解析出企业后，进入 `InvestigationPlanner.plan`。

它基于关键词规则识别主题：股东、关系、产品、融资、财务、风险、知识产权、新闻、企业概况，还有特殊的 `full_due_diligence`。

然后决定展示形式和交付方式：

- 完整尽调 → 异步生成 12 章节报告；
- 关系主题 → 同步返回关系路径图；
- 风险主题 → 同步返回风险摘要；
- 股东/产品/融资/知识产权 → 同步返回列表；
- 其他 → 直接回答。

时间范围支持‘近 N 年’、显式起止日期、as_of 某个日期、当前/最新，默认取最新可用数据。深度分为详细、标准、简要三档。如果主题本身需要证据，比如风险或新闻，会自动启用文本证据检索。

### QA 路径：17 类结构化查询 + 图路径 + 文本证据

计划生成后，进入 `CompanyQuestionService.query` 和 `QAWorkflow.execute`。

系统会并行执行三类查询：

**第一类是 17 类结构化查询**，映射到 MySQL 只读仓储。比如：

- 企业概况、注册资本、法定代表人、经营状态 → `EnterpriseRepository` / `t_enterprise`；
- 当前股东、历史股东、出资明细 → `ShareholderRepository` / `t_partners_alls`；
- 产品/业务线 → `ProductRepository`；
- 融资历史 → `FinanceRepository`；
- 行政处罚、环保处罚、经营异常、行政许可 → `RiskRepository`；
- 专利、商标、著作权 → `IntellectualPropertyRepository`；
- 财务指标序列 → `FinancialMetricRepository` / `company_indicator` + `wind_listed_company_metrics`。

每个 Repository 都通过只读连接池访问，双层 SQL 守卫会检查语句里不能出现 INSERT、UPDATE、DELETE 等写操作。

**第二类是图路径查询。** 如果意图是关系类，就调用 `GraphPathQueryService`，在 Neo4j 里做最多三跳路径搜索。关系类型包括持股、融资、投资、拥有专利/商标/著作权、处罚、许可等。查询时只取高置信度、有来源标识的边。

**第三类是文本证据查询。** 如果主题需要证据，就从 Milvus 证据索引里检索。系统会先按 `eid`、文档类型、发布时间范围做强制过滤，再做向量检索，最后校验 `eid`、投影版本和来源一致性。

三类结果合并后，进入 `_assemble_response`：校验来源完整性、检测事实冲突、计算数据质量状态，最后生成带 `source_id` 引用的简答。

### 报告路径：十二章节并行生成

当用户要求完整报告时，`DueDiligenceReportService.create` 会做四件事：

1. 解析并锁定企业身份；
2. 锁定派生数据版本，包括代码版本、配置 checksum、Neo4j 图谱版本、Milvus 证据版本；
3. 提交 Tasker 后台任务；
4. 后台执行 `DueDiligenceReportWorkflow.run`。

报告有 12 个章节：企业身份、工商与业务概览、股权结构与出资、高置信度关联关系、产品与业务线索、融资历程、财务指标、合规与处罚异常、知识产权、新闻与重大事件、关键风险与待核验事项、数据质量声明。

前 11 个章节**并发执行**，每个章节都复用上面的 QA 路径，调用 `CompanyQuestionService.query`。`DATA_QUALITY` 章节等所有查询章节完成后聚合生成，不依赖 LLM。

每个章节有独立状态：`complete / partial / no_data / stale / conflicted / failed`。单章节失败不会影响其他章节，整体报告状态据此判定为 complete、partial 或 failed。还支持单章节重试。

报告生成后，`_assemble_report` 会合并所有事实、关系、路径、证据和来源，再对所有 claim 做 Claim Ledger 校验；`_publish_report` 把 JSON、Markdown 和原子记录上传到 MinIO。

### Claim Ledger 与数据质量

这是企业尽调里非常关键的部分。`build_claim_ledger` 会为每个 `Fact`、`Relation`、`Path`、`Evidence` 创建 Claim，并校验它引用的 `source_id` 必须存在于 `sources` 集合中。如果校验失败，就拒绝输出未经验证的事实。

`DataQuality` 模型会显式返回：

- `source_status`：数据主库是否可用；
- `missing_fields`：缺失字段；
- `stale_fields`：过期字段；
- `conflicts`：来源冲突；
- `warnings`：质量警告；
- `minimum_confidence`：最低置信度。

所以系统不会假装什么都知道，而是把‘我不知道’‘这个数据过期了’‘两个来源说法不一致’这些情况明确告诉用户。

### 验证与回归

企业尽调当前通过单元测试、接口测试和关键 E2E 验证主链路，覆盖企业解析、全局问题、结构化查询、三跳关系、文本证据、Claim Ledger、主聊天工具调用和报告下载。

企业尽调专属质量评测、QA Benchmark、性能评测、评测页面和发布门禁已经删除。平台通用知识库 RAG 评测与企业关系评测仍由各自模块维护。”

---

## 十、技术亮点与可复用性（1.5 分钟）

“这个项目有几个值得强调的技术点：

1. **LangGraph 多智能体编排**：BaseAgent + 中间件链式组合，支持 Skills、MCP、SubAgent、工具审批、长上下文摘要。
2. **混合检索与图谱增强 RAG**：Milvus 向量 + BM25 + 知识图谱三跳扩散 + reranker。
3. **异步任务与队列**：ARQ + Redis 执行 AgentRun，支持运行事件、取消、恢复和 FIFO 请求队列。
4. **沙盒隔离执行**：基于 Docker/K8s 的 sandbox-provisioner，工具代码在隔离环境运行。
5. **多租户与权限**：用户、部门、管理员、超级管理员多级权限，后端做最终鉴权。
6. **领域 Agent 落地**：企业尽调场景从数据审计、SQL 守卫、图谱投影到报告生成的完整闭环。
7. **工程化**：Docker Compose 统一开发/生产，LITE 轻量模式可跳过重依赖，测试按 unit/integration/e2e 分层。”

---

## 十一、总结（30 秒）

“总结一下，la-platform 是一个工程完整度较高的企业级 Agent 平台。它不只是做一个聊天机器人，而是把 RAG、知识图谱和多智能体编排整合起来，让企业能够把分散的知识变成可检索、可推理、可交付的智能体能力。

当前版本已经完成了品牌改造、浅紫小鱼主题、平台级语音交互，以及广东企业尽调领域智能体的主链路。后续会继续完善更多垂直领域 Agent 和平台易用性。

这就是 la-platform 的介绍，谢谢。”

---

## 附：可自由组合的“一句话版本”

> “la-platform 是一个基于 LangGraph 的企业级 RAG + 知识图谱 + 多智能体编排平台，支持文档解析、混合检索、图谱推理、Skills/MCP/SubAgent 扩展、异步任务和沙盒工具执行，并已在广东企业尽调和平台级语音交互场景落地。”
