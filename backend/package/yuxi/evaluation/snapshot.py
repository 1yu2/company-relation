"""实验创建时的配置快照：记录复现一次实验所需的模型、Agent 配置、数据集、评分器和代码版本。

快照只记录创建时可确定的事实。运行时由 middleware 拼装的完整系统提示词与模型实际可见的工具列表
无法静态得到，这里记录 Agent 配置层面的 prompt/工具清单；实际调用的工具和模型名由每条 rollout 记录。
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

import yuxi
from yuxi.agents.buildin import agent_manager
from yuxi.evaluation.provenance import checksum_for
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.services.agent_run_service import _load_agent_context, resolve_agent_run_model_spec
from yuxi.utils.datetime_utils import utc_now_naive

PRICING_FIELDS = ("input_per_million", "output_per_million", "currency")


async def capture_agent_snapshot(db, *, agent_slug: str, requested_model_spec: str | None) -> dict[str, Any]:
    """解析被测 Agent 的实际模型和配置层 prompt/工具清单。"""
    agent_item = await AgentRepository(db).get_by_slug(agent_slug)
    if agent_item is None:
        raise LookupError(f"智能体不存在: {agent_slug}")
    backend = agent_manager.get_agent(agent_item.backend_id)
    if backend is None:
        raise LookupError(f"智能体后端不存在: {agent_item.backend_id}")
    context = _load_agent_context(agent_item, backend)
    config_context = (agent_item.config_json or {}).get("context") or {}
    return {
        "agent_slug": agent_slug,
        "backend_id": agent_item.backend_id,
        "model_spec": resolve_agent_run_model_spec(requested_model_spec, agent_item, backend, context),
        "agent_context_checksum": checksum_for(config_context),
        "system_prompt_checksum": checksum_for(str(getattr(context, "system_prompt", "") or "")),
        # None 表示使用默认集合，与显式空列表含义不同，因此原样记录。
        "configured_tools": getattr(context, "tools", None),
        "configured_mcps": getattr(context, "mcps", None),
        "configured_skills": getattr(context, "skills", None),
        "configured_knowledges": getattr(context, "knowledges", None),
    }


def capture_common_snapshot(*, version, tasks, evaluator_versions: dict[str, str]) -> dict[str, Any]:
    """记录数据集版本、评分器版本和当前运行代码的校验和。"""
    return {
        "dataset": {"version_id": version.version_id, "checksum": version.checksum, "task_count": len(tasks)},
        "evaluators": dict(sorted(evaluator_versions.items())),
        "code_checksum": backend_code_checksum(),
        "captured_at": utc_now_naive().isoformat(),
    }


def validate_pricing(pricing: Any) -> dict[str, Any] | None:
    """校验调用方提供的模型单价；不提供时不估算成本，而不是使用猜测值。"""
    if pricing is None:
        return None
    if not isinstance(pricing, dict) or set(pricing) != set(PRICING_FIELDS):
        raise ValueError(f"pricing 必须且只能包含 {', '.join(PRICING_FIELDS)}")
    for key in ("input_per_million", "output_per_million"):
        value = pricing[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"pricing.{key} 必须是非负数")
    return dict(pricing)


def backend_code_checksum() -> str:
    """计算 yuxi 包与 server 包全部 .py 源码的校验和，包含未提交的改动。

    容器内没有 .git，git commit 由调用方传入；该校验和由服务端独立计算，用于确认两次实验是否运行同一份代码。
    """
    roots = [Path(yuxi.__file__).parent]
    server_spec = importlib.util.find_spec("server")
    if server_spec and server_spec.origin:
        roots.append(Path(server_spec.origin).parent)
    digest = hashlib.sha256()
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            digest.update(f"{root.name}/{path.relative_to(root).as_posix()}".encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()
