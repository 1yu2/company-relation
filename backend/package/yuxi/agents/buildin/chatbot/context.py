from dataclasses import dataclass, field

from yuxi.agents.context import BaseContext


@dataclass(kw_only=True)
class ChatBotContext(BaseContext):
    subagents: list[str] | None = field(
        default=None,
        metadata={
            "name": "子智能体",
            "options": [],
            "description": "可选子智能体列表，为空表示启用当前用户可见的全部子智能体。",
            "type": "list",
            "kind": "subagents",
        },
    )
    company_answer_gate: bool = field(
        default=False,
        metadata={
            "name": "企业尽调回答门禁",
            "configurable": False,
            "hide": True,
            "description": "最终回答先过企业事实引用校验再整段输出，仅企业尽调 Agent 开启。",
        },
    )
