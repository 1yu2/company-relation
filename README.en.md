<div align="center">
<h1>Company Relation & Due Diligence Agent</h1>

<p><strong>A Guangdong enterprise due-diligence agent and an agent evaluation system, built on <a href="https://github.com/xerrors/Yuxi">Yuxi</a></strong></p>

[![](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=ffffff)](docker-compose.yml)
[![](https://img.shields.io/github/issues/1yu2/company-relation?color=F48D73)](https://github.com/1yu2/company-relation/issues)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[中文](README.md)

</div>

## What this repository is

This repository adds two things on top of the Yuxi agent platform:

1. **Company due-diligence agent** — an agent in the main chat that chooses its own due-diligence tools to answer questions about Guangdong companies (registration, shareholders, relations, risks, financing, IP) and, when asked, writes topical or full due-diligence reports, using an existing MySQL database as a **read-only source of truth**. Answers cite tool results sentence by sentence and pass a citation gate before they are shown; the LLM never invents company facts.
2. **Agent evaluation system** — a reproducible evaluation loop built into the platform. It runs through the real AgentRun pipeline and supports paired per-task comparison and a regression gate, so changes to an agent (including the due-diligence agent) can be shown to actually help.

Chat, multi-tenancy, model providers, Skills / MCP / sub-agents, the sandbox and knowledge bases come from Yuxi and are only summarized here; see the [documentation site](https://1yu2.github.io/company-relation/).

## Architecture

```mermaid
flowchart LR
    U[Browser Vue 3] -->|/api| API[FastAPI]
    API --> PG[(PostgreSQL<br/>conversations / AgentRuns / evaluation)]
    API -->|enqueue| R[(Redis<br/>ARQ queue / event stream)]
    R --> W[ARQ worker]
    W --> LG[LangGraph agent<br/>middleware + tools]
    LG --> SB[Sandbox<br/>sandbox-provisioner]
    LG --> CDD[Due-diligence services]
    CDD --> MY[(Guangdong company MySQL<br/>read-only)]
    CDD --> NEO[(Neo4j<br/>relation projection)]
    CDD --> MV[(Milvus + MinIO<br/>text evidence index)]
    W -->|SSE events| R
    R -->|SSE| U
```

Chat requests are persisted as AgentRuns and executed by the ARQ worker; run events stream back to the browser over Redis Streams and SSE. The Neo4j projection, the evidence index and evaluation experiments are background tasks persisted in PostgreSQL and recovered after restarts.

## Due-diligence architecture

```mermaid
flowchart TB
    Chat[Main chat: due-diligence agent<br/>deepseek-v4-flash] -->|chooses| Tools
    subgraph Tools[cdd_ atomic tools over the domain services]
        R[cdd_resolve_company<br/>name / credit code / stock code / context eid to one eid]
        F[cdd_query_facts<br/>17 whitelisted query types]
        P[cdd_query_relations<br/>up to 3-hop paths]
        E[cdd_search_evidence<br/>company-scoped text evidence]
        G[cdd_query_global_stats<br/>coverage / shared shareholders]
        X[query_external_company_data<br/>external data]
    end
    R & F & G --> MY[(MySQL read-only)]
    P --> NEO[(Neo4j)]
    E --> MV[(Milvus + MinIO)]
    Chat --> Ans[Final answer citing fact ids per sentence<br/>Q&A, or a report when the user asks for one]
    Ans --> Gate{Answer gate<br/>id exists / value matches / subject matches}
    Gate -->|pass| Out[Shown as a whole]
    Gate -->|fail| Rewrite[Rewrite with reasons, up to 2 times]
    Rewrite --> Gate
    Gate -->|still failing| Fallback[Fallback: list this turn's facts]
```

- MySQL is the only source of truth: read-only connections, a two-layer SQL guard and whitelisted business queries only. The Neo4j projection and the Milvus evidence index are rebuilt offline from MySQL.
- Amounts, ratios, dates and pagination are handled by deterministic code (for example, registered and paid-in capital are converted from "万元人民币" to base currency units).
- Missing, stale, conflicting or low-confidence results are returned explicitly; failed validation refuses to produce a definitive answer.
- Q&A and reports share one agent and one gate. Reports are produced only when the user asks for one, section by section; sections without data say so explicitly.
- The previous deterministic workflow (keyword intent planner, `investigate_company`, the 12-section report task pipeline) has been removed. On the same datasets it scored 29/36 on Q-02 v4 and 16/60 on the Q-03 paraphrase set, and answered with the context company's data when the named company did not exist.

## Agent evaluation

```mermaid
flowchart LR
    DS[Dataset version<br/>immutable once published] --> EXP[Experiment<br/>config snapshot]
    EXP -->|ARQ| JOB[Job<br/>executor lock + concurrency]
    JOB --> RO[Rollouts<br/>committed one by one, resumable]
    RO --> RUN{runner}
    RUN -->|agent_run| AR[Real AgentRun]
    RUN -->|fixture| FX[Fixed answer]
    AR --> EVAL[Evaluator by protocol]
    FX --> EVAL
    EVAL --> M[(Metrics / trace / failure attribution)]
    M --> SUM[Experiment summary]
    SUM --> CMP[Paired comparison<br/>McNemar]
    CMP --> GATE[make eval-gate]
    ONL[Online sampling] --> POOL[Failure pool] -->|human review| DS
```

- **Protocols**: code tasks, fault injection, knowledge-base QA (Hit@K / MRR / nDCG@K with failure attribution), open answers judged by a versioned LLM Judge (excluded from pass rates until human-calibrated), and company due diligence.
- **Reproducibility**: each experiment snapshots the resolved model, prompt/config checksums, tools, dataset version, evaluator versions and code checksum; runs pin the snapshot model and refuse to run if an evaluator changed after creation.
- **Comparison and gate**: paired per-task comparison, refused across datasets or scoring versions; `make eval-gate BASE=<id> CANDIDATE=<id>` applies noise-derived thresholds.
- Use the `/evaluation` page and import datasets as described in [scripts/eval/datasets/README.md](scripts/eval/datasets/README.md). Current local baselines with experiment IDs are listed in the [Chinese README](README.md#agent-评测体系); they come from single local runs and are not production accuracy or capacity figures.

## Quick start

Prerequisites: Docker with Docker Compose and at least one OpenAI-compatible LLM API; due diligence also needs a read-only Guangdong company MySQL configured as `MYSQL_URL` in `.env`.

```bash
git clone https://github.com/1yu2/company-relation.git
cd company-relation
./scripts/init.sh          # Windows: .\scripts\init.ps1
docker compose up --build
```

Open `http://localhost:5173`, sign in with the generated admin account, pick the due-diligence agent in the main chat, and open `/evaluation` for evaluations. Run `make verify` before opening a PR; CI runs the same checks. See [ARCHITECTURE.md](ARCHITECTURE.md) for the code map.

## Acknowledgements

- [Yuxi](https://github.com/xerrors/Yuxi) — the platform this project is built on.
- [LangGraph](https://github.com/langchain-ai/langgraph) and [DeepAgents](https://github.com/langchain-ai/deepagents) — agent orchestration.
- [AkShare](https://github.com/akfamily/akshare) — public data supplementation.

## License

MIT — see [LICENSE](LICENSE).
