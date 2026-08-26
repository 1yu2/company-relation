<div align="center">
<h1>语析 Yuxi</h1>

<p><strong>多租户 Harness + 企业知识库</strong><br/>让企业知识可被智能体检索、推理与交付</p>

[![](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=ffffff)](https://github.com/1yu2/company-relation/blob/main/docker-compose.yml)
[![](https://img.shields.io/github/issues/1yu2/company-relation?color=F48D73)](https://github.com/1yu2/company-relation/issues)
[![License](https://img.shields.io/github/license/bitcookies/winrar-keygen.svg?logo=github)](https://github.com/1yu2/company-relation/blob/main/LICENSE)

<a href="https://github.com/1yu2/company-relation" target="_blank"><img src="https://github.com/1yu2/company-relation/raw/main/docs/public/yuxi-system-architecture.svg" alt="Yuxi 系统架构图" style="width: 720px; max-width: 100%;"/></a>

</div>

## 系统架构

Yuxi 基于 Docker Compose 管理全服务栈：Vue 3 前端通过 `/api` 调用 FastAPI，请求经 PostgreSQL 持久化后通过 ARQ 投递给 Worker 执行 LangGraph AgentRun，运行事件经 Redis Stream 通过 SSE 推送回前端。知识检索使用 Milvus 向量检索与 Neo4j 知识图谱，文件和附件由 MinIO 承载，工具执行在隔离沙盒中完成。

<a href="https://github.com/1yu2/company-relation" target="_blank"><img src="https://github.com/1yu2/company-relation/raw/main/docs/public/yuxi-agent-architecture.svg" alt="Yuxi Agent 架构图" style="width: 720px; max-width: 100%;"/></a>

## Agent 架构

Agent 运行时以垂直流水线组织：ARQ Worker 加载 AgentRun 后，经 11 层 LangGraph 中间件（Steer → 文件系统 → Skills → SubAgent → Summary → …）编排，工具调用经沙盒隔离执行，Skills/MCP 扩展能力按需加载。Skill 依赖的工具只有在对应 Skill 激活后才对模型开放；基础工具与受 Skill 门控的工具保持边界。

## 简介

语析（Yuxi）是一个基于大模型的智能知识库与知识图谱智能体开发平台。它把 **RAG 检索**、**Milvus 知识库内知识图谱** 与 **LangGraph 多智能体编排** 整合进统一的多租户工作台：管理员配置知识库、模型与权限，用户在类 ChatGPT 的界面中与可挂载 Skills、MCP、子智能体和沙盒工具的智能体对话，并获得带引用来源、知识图谱推理与可交付产物的回答。

导航：[项目介绍](https://1yu2.github.io/company-relation/) ｜ [快速开始](https://1yu2.github.io/company-relation/intro/quick-start) ｜ [开发路线图](https://1yu2.github.io/company-relation/develop-guides/roadmap) | [0.7 版本特性](http://xhslink.com/o/5Y6QWnmjF2d)；最新开发动态，详见 [changelog](https://1yu2.github.io/company-relation/develop-guides/changelog)。

---

![image-20260606190609377](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260606235615139.png)

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3 · Vite · Pinia |
| 后端 | FastAPI · LangGraph · ARQ (异步 worker) |
| 存储 | PostgreSQL · Redis · MinIO · Milvus · Neo4j |
| 文档解析 | MinerU · PaddleX · RapidOCR |
| 部署 | Docker Compose |

## 企业尽调智能体

本仓库基于 Yuxi 提供广东企业尽调领域智能体方案：以现有广东企业 MySQL 作为**只读事实主库**，由 `CompanyDueDiligenceAgent` 统一理解企业上下文和调查意图，内部组合结构化查询、最多三跳关系、文本证据与报告工具，并按用户要求动态输出摘要、列表、关系结果、专题报告或完整报告。

核心边界：

- 不修改或补充原广东企业数据库表。
- MySQL 保存事实，Neo4j 和 Milvus 只保存可重建的关系与文本索引。
- AkShare 默认不参与主查询，仅保留为公开公告、最新披露核验和回归基线。
- 缺失、过期、冲突和低置信度关系必须显式返回，LLM 不生成无来源企业事实。
- 普通用户不需要填写内部 `eid`，也不选择简单问答、多跳关系或证据检索等内部模式。
- 自然语言报告要求和报告按钮归一到同一个领域智能体链路。

当前进度：

| 阶段 | 状态 | 交付内容 |
| --- | --- | --- |
| 数据审计与方案 | 已完成 | 924 家企业及股东、产品、融资、风险、知识产权数据只读审计 |
| 数据接入 | 已完成 | 只读连接池、双层 SQL 守卫、企业身份解析、统一 DTO、来源与时效输出 |
| 查询与报告 | 已完成 | 17 类结构化查询、最多三跳、文本证据、十二章节报告和 JSON/Markdown 产物；管理员部署态 HTTP E2E 已通过 |
| 前端入口 | 已完成 | 主聊天统一承载企业问答、关系查询和报告，不提供独立企业尽调工作台 |
| 智能体输入输出升级 | 已完成 | 全局共享 Agent、自然语言唯一输入、Claim Ledger 和动态 `presentation` 已落地 |
| 核心链路回归 | 已完成 | 单元、接口与 E2E 覆盖主聊天工具调用、全局问题、企业解析、关系调查和报告导出 |

登录用户在主聊天窗口选择“企业尽调智能体”后直接提问。当前部署已将企业尽调 Agent 绑定到通过连通性检查且支持工具调用的模型；其他部署也必须配置可用对话模型。本地开发使用不提交的 `.env`。

企业尽调专属质量评测、QA Benchmark、性能评测、评测页面和发布门禁已经从当前代码中移除。PostgreSQL 中既有历史评测表和数据未执行 `DROP` 或 `DELETE`，但当前应用不再映射、创建或访问这些表。

相关文档：

- [企业尽调智能体总体方案](docs/agents/company-due-diligence-system.md)
- [企业尽调智能体实现逐字稿](docs/agents/company-due-diligence-implementation-guide.md)
- [企业尽调智能体全链路与效果论证逐字稿](docs/agents/company-due-diligence-end-to-end-script.md)
- [企业尽调智能体任务清单](tasks/company-due-diligence/README.md)
- [平台级语音交互任务清单](tasks/platform-voice/README.md)
- [平台级语音交互使用说明](docs/agents/platform-voice.md)
- 平台语音统一复用“模型供应商”中的 Qwen/阿里云百炼 URL、Key 与模型配置，不维护第二套语音凭据。
- [现有公开企业关系评测基线](docs/agents/company-relation-evaluation.md)
- 本地开发审计与架构图：`docs/vibe/2026-08-10-guangdong-enterprise-data-audit.md`、`docs/vibe/2026-08-11-company-due-diligence-end-to-end.drawio`（`docs/vibe` 默认不提交）

## 快速开始

**前置要求**：已安装 [Docker](https://docs.docker.com/get-docker/) 与 Docker Compose，并准备至少一个兼容 OpenAI 接口的大模型 API。

**1. 克隆代码并初始化**

```bash
git clone https://github.com/1yu2/company-relation.git
cd company-relation

# Linux/macOS
./scripts/init.sh

# Windows PowerShell
.\scripts\init.ps1
```

**2. 使用 Docker 启动**

```bash
docker compose up --build
```

**3. 访问平台**

等待启动完成后，浏览器打开 `http://localhost:5173`，使用初始化时生成的管理员账户登录即可。

> 💡 不需要知识库 / 知识图谱等重依赖时，可使用 `make up-lite` 以 LITE 轻量模式启动，加快冷启动速度。更多部署说明见 [项目文档](https://1yu2.github.io/company-relation/)。

## 致谢

本项目参考并引用了以下优秀开源项目，在此致以诚挚的感谢：

- [LightRAG](https://github.com/HKUDS/LightRAG) - 早期版本曾参考其图谱构建与检索思路；当前 Yuxi 已实现自研 Milvus 知识库/图谱链路以替换历史集成，降低兼容性问题
- [DeepAgents](https://github.com/langchain-ai/deepagents) - 直接引入作为深度智能体框架
- [DeerFlow](https://github.com/bytedance/deer-flow) - 参考了其 Sandbox 智能体架构的实现思路
- [RAGflow](https://github.com/infiniflow/ragflow) - 参考了其文档 Text Chunking 的分块策略
- [LangGraph](https://github.com/langchain-ai/langgraph) - 多智能体编排框架，本项目的核心架构基础
- [QwenPaw](https://github.com/agentscope-ai/QwenPaw) - 参考模型配置与个人文件区域设计

## 参与贡献

感谢所有贡献者的支持！

<a href="https://github.com/1yu2/company-relation/contributors">
  <img src="https://contrib.rocks/image?repo=1yu2/company-relation&max=100&columns=10" />
</a>


## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=1yu2/company-relation)](https://star-history.com/#1yu2/company-relation)

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

---

<div align="center">

**如果这个项目对您有帮助，请不要忘记给我们一个 ⭐️**

</div>
