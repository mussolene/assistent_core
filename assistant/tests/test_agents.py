"""Tests for agents (Assistant, Tool) with mocks."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from assistant.agents.assistant import AssistantAgent, _format_model_error_for_user
from assistant.agents.base import TaskContext
from assistant.agents.tool_agent import ToolAgent
from assistant.skills.registry import SkillRegistry
from assistant.skills.runner import SandboxRunner


def test_format_model_error_403_html():
    """HTML 403 ответ подменяется на короткое сообщение."""
    exc = Exception(
        "<html>\n<head><title>403 Forbidden</title></head>\n"
        "<body><center><h1>403 Forbidden</h1></center></body>\n</html>"
    )
    msg = _format_model_error_for_user(exc)
    assert "403" in msg
    assert "доступ запрещён" in msg or "forbidden" in msg.lower()
    assert "<html" not in msg and "<body" not in msg


def test_format_model_error_403_plain():
    exc = Exception("403 Forbidden")
    msg = _format_model_error_for_user(exc)
    assert "403" in msg and "доступ" in msg


def test_format_model_error_404_and_5xx():
    assert "404" in _format_model_error_for_user(Exception("404 Not Found"))
    assert "5xx" in _format_model_error_for_user(Exception("502 Bad Gateway"))
    assert "5xx" in _format_model_error_for_user(Exception("503 Service Unavailable"))


def test_format_model_error_html_404_5xx():
    assert "404" in _format_model_error_for_user(Exception("<html>404 Not Found</html>"))
    assert "5xx" in _format_model_error_for_user(Exception("<!DOCTYPE html><body>502</body>"))


def test_format_model_error_connection_refused():
    msg = _format_model_error_for_user(Exception("Connection refused"))
    assert "недоступна" in msg or "LM Studio" in msg or "OPENAI_BASE_URL" in msg


def test_format_model_error_400_bad_request():
    msg = _format_model_error_for_user(Exception("400 Bad Request"))
    assert "400" in msg


def test_format_model_error_long_text_truncated():
    long_msg = "Error: " + "x" * 200
    msg = _format_model_error_for_user(Exception(long_msg))
    assert msg.startswith("Ошибка модели:")
    assert len(msg) <= 140


def test_assistant_agent_init_requires_exactly_one():
    """AssistantAgent requires exactly one of model_gateway or gateway_factory."""
    with pytest.raises(ValueError, match="exactly one"):
        AssistantAgent()
    with pytest.raises(ValueError, match="exactly one"):
        AssistantAgent(model_gateway=MagicMock(), gateway_factory=lambda: None)


def _ctx(**kwargs):
    return TaskContext(
        task_id=kwargs.get("task_id", "t1"),
        user_id=kwargs.get("user_id", "u1"),
        chat_id=kwargs.get("chat_id", "c1"),
        channel=kwargs.get("channel", "telegram"),
        message_id=kwargs.get("message_id", "m1"),
        text=kwargs.get("text", "hello"),
        reasoning_requested=kwargs.get("reasoning_requested", False),
        state=kwargs.get("state", "assistant"),
        iteration=kwargs.get("iteration", 0),
        tool_results=kwargs.get("tool_results", []),
        metadata=kwargs.get("metadata", {}),
    )


@pytest.mark.asyncio
async def test_assistant_agent_returns_text():
    model = MagicMock()
    model.generate = AsyncMock(return_value="Hi there!")
    memory = MagicMock()
    memory.get_context_for_user = AsyncMock(return_value=[])
    memory.append_message = AsyncMock()
    agent = AssistantAgent(model_gateway=model, memory=memory)
    result = await agent.handle(_ctx())
    assert result.success is True
    assert "Hi there" in result.output_text


@pytest.mark.asyncio
async def test_assistant_agent_uses_gateway_factory():
    model = MagicMock()
    model.generate = AsyncMock(return_value="From factory")
    memory = MagicMock()
    memory.get_context_for_user = AsyncMock(return_value=[])
    memory.append_message = AsyncMock()

    async def get_gw():
        return model

    agent = AssistantAgent(gateway_factory=get_gw, memory=memory)
    result = await agent.handle(_ctx())
    assert result.success is True
    assert "From factory" in result.output_text


@pytest.mark.asyncio
async def test_assistant_agent_handle_with_tool_results():
    model = MagicMock()
    model.generate = AsyncMock(return_value="Done with tools")
    memory = MagicMock()
    memory.get_context_for_user = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    memory.append_message = AsyncMock()
    agent = AssistantAgent(model_gateway=model, memory=memory)
    ctx = _ctx(tool_results=[{"tool": "fs", "result": "file content"}])
    result = await agent.handle(ctx)
    assert result.success is True
    assert "Tool results" in (model.generate.call_args[0][0] or "")


@pytest.mark.asyncio
async def test_assistant_agent_handle_skips_system_messages_in_prompt():
    model = MagicMock()
    model.generate = AsyncMock(return_value="Ok")
    memory = MagicMock()
    memory.get_context_for_user = AsyncMock(
        return_value=[
            {"role": "system", "content": "Secret system"},
            {"role": "user", "content": "hi"},
        ]
    )
    memory.append_message = AsyncMock()
    agent = AssistantAgent(model_gateway=model, memory=memory)
    await agent.handle(_ctx(text="hi"))
    prompt = model.generate.call_args[0][0]
    assert "Secret system" not in prompt
    assert "User:" in prompt and "hi" in prompt


@pytest.mark.asyncio
async def test_assistant_agent_handle_stream_sync_fallback():
    """При stream=True модель возвращает не-итератор → fallback на generate(stream=False). Без неожиданных корутин."""
    model = MagicMock()

    async def _sync_response(*args: object, **kwargs: object) -> str:
        return "Sync response"

    def _generate_side_effect(*args: object, **kwargs: object) -> object:
        if kwargs.get("stream"):
            return None  # нет __aiter__ → код пойдёт в else и вызовет generate(stream=False)
        return _sync_response()

    model.generate = MagicMock(side_effect=_generate_side_effect)
    memory = MagicMock()
    memory.get_context_for_user = AsyncMock(return_value=[])
    memory.append_message = AsyncMock()
    stream_cb = AsyncMock()
    agent = AssistantAgent(model_gateway=model, memory=memory)
    ctx = _ctx(metadata={"stream_callback": stream_cb})
    result = await agent.handle(ctx)
    assert result.success is True
    assert "Sync response" in result.output_text


@pytest.mark.asyncio
async def test_assistant_agent_handle_appends_assistant_message_when_no_tool_results():
    model = MagicMock()
    model.generate = AsyncMock(return_value="Reply")
    memory = MagicMock()
    memory.get_context_for_user = AsyncMock(return_value=[])
    memory.append_message = AsyncMock()
    agent = AssistantAgent(model_gateway=model, memory=memory)
    ctx = _ctx(text="hi", tool_results=[])
    await agent.handle(ctx)
    assert memory.append_message.call_count >= 1
    calls = [
        c for c in memory.append_message.call_args_list if len(c[0]) >= 3 and c[0][1] == "assistant"
    ]
    assert len(calls) >= 1
    assert calls[0][0][2] == "Reply"


@pytest.mark.asyncio
async def test_assistant_agent_parse_tool_calls_invalid_json():
    agent = AssistantAgent(model_gateway=MagicMock(), memory=MagicMock())
    out = agent._parse_tool_calls('{"tool_calls": [invalid]}')
    assert out == []


@pytest.mark.asyncio
async def test_assistant_agent_handle_stream_callback():
    async def stream_gen():
        yield "Hello"
        yield " world"

    model = MagicMock()
    model.generate = MagicMock(return_value=stream_gen())
    memory = MagicMock()
    memory.get_context_for_user = AsyncMock(return_value=[])
    memory.append_message = AsyncMock()
    agent = AssistantAgent(model_gateway=model, memory=memory)
    ctx = _ctx(metadata={"stream_callback": AsyncMock()})
    result = await agent.handle(ctx)
    assert result.success is True
    assert "Hello" in result.output_text and "world" in result.output_text


@pytest.mark.asyncio
async def test_assistant_agent_handle_model_error_with_stream_cb():
    """When model raises and stream_callback is set, callback gets done=True and user sees error message."""
    model = MagicMock()
    model.generate = AsyncMock(side_effect=ConnectionError("Connection refused"))
    memory = MagicMock()
    memory.get_context_for_user = AsyncMock(return_value=[])
    memory.append_message = AsyncMock()
    stream_cb = AsyncMock()
    agent = AssistantAgent(model_gateway=model, memory=memory)
    ctx = _ctx(metadata={"stream_callback": stream_cb})
    result = await agent.handle(ctx)
    assert result.success is True
    assert "недоступна" in result.output_text or "LM Studio" in result.output_text
    stream_cb.assert_any_call("", done=True)


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:coroutine .* was never awaited:RuntimeWarning")
async def test_assistant_agent_handle_model_error_connection():
    """When model.generate raises connection error, returns user-friendly message."""

    async def _raise_connection_error(*args: object, **kwargs: object) -> str:
        raise ConnectionError("Connection refused")

    model = MagicMock()
    model.generate = _raise_connection_error
    memory = MagicMock()
    memory.get_context_for_user = AsyncMock(return_value=[])
    memory.append_message = AsyncMock()
    agent = AssistantAgent(model_gateway=model, memory=memory)
    result = await agent.handle(_ctx())
    assert result.success is True
    assert (
        "refused" in result.output_text.lower()
        or "недоступна" in result.output_text
        or "model" in result.output_text.lower()
    )


@pytest.mark.asyncio
async def test_tool_agent_no_tool_calls():
    reg = SkillRegistry()
    runner = SandboxRunner()
    memory = MagicMock()
    agent = ToolAgent(reg, runner, memory)
    ctx = _ctx(metadata={})
    result = await agent.handle(ctx)
    assert result.success is False
    assert "no tool_calls" in result.error.lower()


@pytest.mark.asyncio
async def test_tool_agent_missing_skill_name():
    reg = SkillRegistry()
    runner = SandboxRunner()
    memory = MagicMock()
    memory.append_tool_result = AsyncMock()
    agent = ToolAgent(reg, runner, memory)
    ctx = _ctx(metadata={"pending_tool_calls": [{"params": {"x": 1}}]})
    result = await agent.handle(ctx)
    assert result.success is True
    assert result.metadata["tool_results"][0].get("error") == "missing skill name"
    assert result.metadata["tool_results"][0].get("ok") is False


@pytest.mark.asyncio
async def test_tool_agent_runs_skill():
    reg = SkillRegistry()
    runner = SandboxRunner()
    memory = MagicMock()
    memory.append_tool_result = AsyncMock()
    import tempfile

    from assistant.skills.filesystem import FilesystemSkill

    with tempfile.TemporaryDirectory() as d:
        reg.register(FilesystemSkill(workspace_dir=d))
        agent = ToolAgent(reg, runner, memory)
        ctx = _ctx(
            metadata={
                "pending_tool_calls": [
                    {"name": "filesystem", "params": {"action": "list", "path": "."}}
                ]
            }
        )
        result = await agent.handle(ctx)
    assert result.success is True
    assert result.next_agent == "assistant"
    assert result.metadata and "tool_results" in result.metadata


def test_assistant_parse_tool_calls_toolcalls_key():
    """Парсит JSON с ключом 'toolcalls' (без подчёркивания) и вложенными params."""
    agent = AssistantAgent(model_gateway=MagicMock(), memory=MagicMock())
    text = '{"toolcalls": [{"name": "tasks", "params": {"action": "createtask", "title": "X", "startdate": "2026-02-23"}}]}'
    calls = agent._parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].get("name") == "tasks"
    assert calls[0].get("params", {}).get("title") == "X"


@pytest.mark.asyncio
async def test_tool_agent_unknown_skill():
    reg = SkillRegistry()
    runner = SandboxRunner()
    memory = MagicMock()
    memory.append_tool_result = AsyncMock()
    agent = ToolAgent(reg, runner, memory)
    ctx = _ctx(metadata={"pending_tool_calls": [{"name": "nonexistent", "params": {}}]})
    result = await agent.handle(ctx)
    assert result.success is True
    assert result.metadata
    assert any(r.get("ok") is False for r in result.metadata.get("tool_results", []))


@pytest.mark.asyncio
async def test_tool_agent_tasks_normalizes_action_and_params():
    reg = SkillRegistry()
    run_params: list[dict] = []

    async def capture_run(name, params, runner):
        run_params.append((name, dict(params)))
        return {"ok": True}

    reg.run = AsyncMock(side_effect=capture_run)
    runner = SandboxRunner()
    memory = MagicMock()
    memory.append_tool_result = AsyncMock()
    agent = ToolAgent(reg, runner, memory)
    ctx = _ctx(
        metadata={
            "pending_tool_calls": [
                {"name": "tasks", "params": {"action": "createtask", "title": "T"}}
            ]
        }
    )
    await agent.handle(ctx)
    assert len(run_params) == 1
    assert run_params[0][0] == "tasks"
    assert run_params[0][1].get("action") == "create_task"
