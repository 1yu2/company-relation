<div align="center">
<h1>语析 Yuxi</h1>

<p><strong>多租户 Harness + 企业知识库</strong><br/>让企业知识可被智能体检索、推理与交付</p>

[![](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=ffffff)](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml)
[![](https://img.shields.io/github/issues/xerrors/Yuxi?color=F48D73)](https://github.com/xerrors/Yuxi/issues)
[![License](https://img.shields.io/github/license/bitcookies/winrar-keygen.svg?logo=github)](https://github.com/xerrors/Yuxi/blob/main/LICENSE)
[![DeepWiki](https://img.shields.io/badge/DeepWiki-blue.svg)](https://deepwiki.com/xerrors/Yuxi)
[![Bilibili](https://img.shields.io/badge/知识库演示-00A1D6?logo=bilibili&logoColor=fff)](https://www.bilibili.com/video/BV1erE26iEgv/?share_source=copy_web&vd_source=37b0bdbf95b72ea38b2dc959cfadc4d8)


<a href="https://trendshift.io/repositories/24335" target="_blank"><img src="https://trendshift.io/api/badge/repositories/24335" alt="xerrors%2FYuxi | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

[[项目文档]](https://xerrors.github.io/Yuxi) · [[版本特性]](http://xhslink.com/o/5Y6QWnmjF2d) · [[🇬🇧 English README]](README.en.md)

</div>

![arch](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/arch.png)

## 简介

语析（Yuxi）是一个基于大模型的智能知识库与知识图谱智能体开发平台。它把 **RAG 检索**、**Milvus 知识库内知识图谱** 与 **LangGraph 多智能体编排** 整合进统一的多租户工作台：管理员配置知识库、模型与权限，用户在类 ChatGPT 的界面中与可挂载 Skills、MCP、子智能体和沙盒工具的智能体对话，并获得带引用来源、知识图谱推理与可交付产物的回答。

导航：[项目介绍](https://xerrors.github.io/Yuxi/) ｜ [快速开始](https://xerrors.github.io/Yuxi/intro/quick-start) ｜ [开发路线图](https://xerrors.github.io/Yuxi/develop-guides/roadmap) | [0.7 版本特性](http://xhslink.com/o/5Y6QWnmjF2d)；最新开发动态，详见 [changelog](https://xerrors.github.io/Yuxi/develop-guides/changelog)。

> 📢 求职：作者为江南大学软件工程博士研究生，研究方向 AI Agent、知识图谱与大模型应用，预计 2027 年毕业，现寻求实习/全职机会，欢迎联系：wenjie.zhang@stu.jiangnan.edu.cn

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

## 企业尽调智能体（建设中）

本仓库正在基于 Yuxi 增加广东企业尽调场景：以现有广东企业 MySQL 作为**只读事实主库**，统一提供企业基本信息、股东出资、产品、融资、财务、风险、知识产权、新闻证据和多跳关系查询，并逐步补齐完整尽调报告、结果质量评测和系统性能评测。

核心边界：

- 不修改或补充原广东企业数据库表。
- MySQL 保存事实，Neo4j 和 Milvus 只保存可重建的关系与文本索引。
- AkShare 默认不参与主查询，仅保留为公开公告、最新披露核验和回归基线。
- 缺失、过期、冲突和低置信度关系必须显式返回，LLM 不生成无来源企业事实。

当前进度：

| 阶段 | 状态 | 交付内容 |
| --- | --- | --- |
| 数据审计与方案 | 已完成 | 924 家企业及股东、产品、融资、风险、知识产权数据只读审计 |
| 数据接入 | 待实现 | 只读连接池、企业身份解析、统一 DTO、来源与时效输出 |
| 查询与报告 | 待实现 | 企业查询 API、完整尽调报告、Neo4j 多跳关系和文本证据 |
| 结果质量评测 | 待实现 | 人工 Golden、字段/路径/证据/拒答/报告完整性评测 |
| 系统性能评测 | 待实现 | P50/P95/P99、并发、吞吐、错误率和连接池观测 |

相关文档：

- [企业尽调智能体总体方案](docs/agents/company-due-diligence-system.md)
- [企业尽调智能体实现逐字稿](docs/agents/company-due-diligence-implementation-guide.md)
- [企业尽调智能体任务清单](tasks/company-due-diligence/README.md)
- [现有公开企业关系评测基线](docs/agents/company-relation-evaluation.md)
- 本地开发审计与架构图：`docs/vibe/2026-08-10-guangdong-enterprise-data-audit.md`、`docs/vibe/2026-08-10-enterprise-due-diligence-architecture.drawio`（`docs/vibe` 默认不提交）

## 快速开始

**前置要求**：已安装 [Docker](https://docs.docker.com/get-docker/) 与 Docker Compose，并准备至少一个兼容 OpenAI 接口的大模型 API。

**1. 克隆代码并初始化**

```bash
git clone --branch v0.7.1 --depth 1 https://github.com/xerrors/Yuxi.git
cd Yuxi

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

> 💡 不需要知识库 / 知识图谱等重依赖时，可使用 `make up-lite` 以 LITE 轻量模式启动，加快冷启动速度。更多部署说明见 [项目文档](https://xerrors.github.io/Yuxi)。

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

<a href="https://github.com/xerrors/Yuxi/contributors">
  <img src="https://contrib.rocks/image?repo=xerrors/Yuxi&max=100&columns=10" />
</a>


## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=xerrors/Yuxi)](https://star-history.com/#xerrors/Yuxi)

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

---

<div align="center">

**如果这个项目对您有帮助，请不要忘记给我们一个 ⭐️**

</div>
