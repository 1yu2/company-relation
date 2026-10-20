"""以真实 DeepAgents execute 工具约束退出码标记与状态转换。"""

from types import SimpleNamespace

import pytest
from deepagents.backends.protocol import ExecuteResponse
from langchain_core.messages import ToolMessage

from yuxi.agents.backends.composite import YuxiFilesystemMiddleware
from yuxi.agents.backends.sandbox.backend import ProvisionerSandboxBackend


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("truncated", [False, True])
@pytest.mark.parametrize("exit_code", [0, 1, -9])
async def test_real_execute_status(exit_code, truncated, asynchronous):
    """真实工具生成标记；升级改变格式时非零退出码用例必须失败。"""

    class Backend(ProvisionerSandboxBackend):
        """仅替换命令执行，保留真实工具的能力检查。"""

        def __init__(self):
            """假后端不连接沙盒。"""

        def execute(self, command, *, timeout=None):
            """模拟命令输出，包括不应触发判错的正文。"""
            return ExecuteResponse(
                output="Error: ordinary output\n[Command failed with exit code 1]",
                exit_code=exit_code,
                truncated=truncated,
            )

        async def aexecute(self, command, *, timeout=None):
            """异步执行返回相同的结构化结果。"""
            return self.execute(command, timeout=timeout)

    middleware = YuxiFilesystemMiddleware(backend=Backend(), tool_token_limit_before_evict=None)
    tool = next(tool for tool in middleware.tools if tool.name == "execute")
    runtime = SimpleNamespace(tool_call_id="call-execute")
    request = SimpleNamespace(tool_call={"name": "execute"}, runtime=runtime)
    if asynchronous:

        async def handler(request):
            """调用三方工具的真实异步实现。"""
            return await tool.coroutine(command="probe", runtime=request.runtime)

        result = await middleware.awrap_tool_call(request, handler)
    else:
        result = middleware.wrap_tool_call(request, lambda request: tool.func(command="probe", runtime=request.runtime))
    assert result.status == ("success" if exit_code == 0 else "error")
    assert result.tool_call_id == "call-execute"
    assert result.content.startswith("Error: ordinary output")


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("execute", "Error: text only"),
        ("execute", "\n[Command failed with exit code 1]\nmore output"),
        ("read_file", "\n[Command failed with exit code 1]"),
    ],
)
def test_execute_status_does_not_match_body_or_other_tools(name, content):
    """只识别 execute 末尾的协议标记，不按正文猜测失败。"""
    middleware = YuxiFilesystemMiddleware(tool_token_limit_before_evict=None)
    result = middleware.wrap_tool_call(
        SimpleNamespace(tool_call={"name": name}),
        lambda _: ToolMessage(content=content, tool_call_id="call-1"),
    )
    assert result.status == "success"
