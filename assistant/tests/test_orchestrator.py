"""Tests for orchestrator state and task manager (with mocks)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.agents.base import AgentResult, TaskContext
from assistant.core.agent_registry import AgentRegistry
from assistant.core.events import IncomingMessage, StreamToken
from assistant.core.orchestrator import Orchestrator
from assistant.core.task_manager import TaskManager


@pytest.mark.asyncio
async def test_task_manager_requires_redis():
    tm = TaskManager("redis://localhost:6379/14")
    try:
        await tm.connect()
    except Exception:
        pytest.skip("Redis not available")
    task_id = await tm.create(
        user_id="u1",
        chat_id="c1",
        text="hello",
    )
    assert task_id
    task = await tm.get(task_id)
    assert task["user_id"] == "u1"
    assert task["state"] == "received"
    await tm.update(task_id, state="assistant")
    task2 = await tm.get(task_id)
    assert task2["state"] == "assistant"


@pytest.mark.asyncio
async def test_agent_registry_unknown():
    reg = AgentRegistry()
    ctx = TaskContext(
        task_id="t1",
        user_id="u1",
        chat_id="c1",
        channel="telegram",
        message_id="m1",
        text="hi",
        reasoning_requested=False,
        state="assistant",
        iteration=0,
        tool_results=[],
        metadata={},
    )
    result = await reg.handle("unknown_agent", ctx)
    assert result.success is False
    assert "unknown" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_agent_registry_mock():
    reg = AgentRegistry()

    async def mock_handle(ctx):
        return AgentResult(success=True, output_text="ok")

    mock = MagicMock()
    mock.handle = AsyncMock(side_effect=mock_handle)
    reg.register("assistant", mock)
    ctx = TaskContext(
        task_id="t1",
        user_id="u1",
        chat_id="c1",
        channel="telegram",
        message_id="m1",
        text="hi",
        reasoning_requested=False,
        state="assistant",
        iteration=0,
        tool_results=[],
        metadata={},
    )
    result = await reg.handle("assistant", ctx)
    assert result.success is True
    assert result.output_text == "ok"


# --- Orchestrator stream_callback and StreamToken ---


def _make_orchestrator_with_mock_bus():
    config = MagicMock()
    config.orchestrator.max_iterations = 5
    config.orchestrator.autonomous_mode = False
    bus = MagicMock()
    bus.publish_stream_token = AsyncMock()
    orch = Orchestrator(config=config, bus=bus)
    return orch, bus


def _make_incoming_payload(chat_id: str = "chat_1", message_id: str = "msg_1"):
    return IncomingMessage(
        message_id=message_id,
        user_id="user_1",
        chat_id=chat_id,
        text="hello",
    )


@pytest.mark.asyncio
async def test_orchestrator_task_to_context_stream_callback_set_when_assistant_and_stream():
    """When state is assistant and stream True, stream_callback is set and publishes StreamToken."""
    orch, bus = _make_orchestrator_with_mock_bus()
    payload = _make_incoming_payload(chat_id="c1")
    task_data = {
        "state": "assistant",
        "stream": True,
        "chat_id": "c1",
        "task_id": "tid_1",
        "user_id": "u1",
        "message_id": "m1",
        "tool_results": [{"tool": "test", "result": "ok"}],
    }
    ctx = orch._task_to_context("tid_1", task_data, payload)
    stream_cb = ctx.metadata.get("stream_callback")
    assert stream_cb is not None

    await stream_cb("hi", False)
    bus.publish_stream_token.assert_called_once()
    call_arg = bus.publish_stream_token.call_args[0][0]
    assert isinstance(call_arg, StreamToken)
    assert call_arg.task_id == "tid_1"
    assert call_arg.chat_id == "c1"
    assert call_arg.token == "hi"
    assert call_arg.done is False

    bus.publish_stream_token.reset_mock()
    await stream_cb("", True)
    bus.publish_stream_token.assert_called_once()
    call_arg = bus.publish_stream_token.call_args[0][0]
    assert call_arg.token == ""
    assert call_arg.done is True


@pytest.mark.asyncio
async def test_orchestrator_task_to_context_stream_callback_none_when_not_assistant():
    """When state is not assistant, stream_callback is None."""
    orch, _ = _make_orchestrator_with_mock_bus()
    payload = _make_incoming_payload()
    task_data = {
        "state": "tool",
        "stream": True,
        "chat_id": "c1",
        "task_id": "tid_1",
        "user_id": "u1",
        "message_id": "m1",
    }
    ctx = orch._task_to_context("tid_1", task_data, payload)
    assert ctx.metadata.get("stream_callback") is None


@pytest.mark.asyncio
async def test_orchestrator_task_to_context_stream_callback_none_when_stream_disabled():
    """When stream is False, stream_callback is None."""
    orch, _ = _make_orchestrator_with_mock_bus()
    payload = _make_incoming_payload()
    task_data = {
        "state": "assistant",
        "stream": False,
        "chat_id": "c1",
        "task_id": "tid_1",
        "user_id": "u1",
        "message_id": "m1",
    }
    ctx = orch._task_to_context("tid_1", task_data, payload)
    assert ctx.metadata.get("stream_callback") is None


def test_orchestrator_is_only_file_content_question():
    assert Orchestrator._is_only_file_content_question("что написано тут") is True
    assert Orchestrator._is_only_file_content_question("Что в файле?") is True
    assert Orchestrator._is_only_file_content_question("опиши документ") is True
    assert Orchestrator._is_only_file_content_question("содержимое файла") is True
    assert Orchestrator._is_only_file_content_question("") is False  # пустой обрабатывается отдельно в caller
    assert Orchestrator._is_only_file_content_question("напомни завтра про встречу") is False
    assert Orchestrator._is_only_file_content_question("x" * 150) is False


def test_orchestrator_get_send_document_from_tool_results():
    assert Orchestrator._get_send_document_from_tool_results(None) is None
    assert Orchestrator._get_send_document_from_tool_results({}) is None
    assert Orchestrator._get_send_document_from_tool_results(
        {"tool_results": [{"send_document": {"file_id": "abc"}}]}
    ) == {"file_id": "abc"}
    assert Orchestrator._get_send_document_from_tool_results(
        {"tool_results": [{"x": 1}, {"send_document": {"file_id": "last"}}]}
    ) == {"file_id": "last"}


def test_orchestrator_get_send_checklist_from_tool_results():
    assert Orchestrator._get_send_checklist_from_tool_results(None) is None
    assert Orchestrator._get_send_checklist_from_tool_results(
        {"tool_results": [{"send_checklist": {"title": "T", "tasks": []}}]}
    ) == {"title": "T", "tasks": []}


@pytest.mark.asyncio
async def test_orchestrator_file_summary_for_user_no_gateway():
    config = MagicMock()
    bus = MagicMock()
    orch = Orchestrator(config=config, bus=bus, memory=None, gateway_factory=None)
    out = await orch._file_summary_for_user("some text", ["ref1"])
    assert "проиндексирован" in out or "Можешь спросить" in out


@pytest.mark.asyncio
async def test_orchestrator_file_summary_for_user_with_gateway_returns_summary():
    config = MagicMock()
    bus = MagicMock()
    gateway = MagicMock()
    gateway.generate = AsyncMock(return_value="Краткое содержание документа.")
    async def get_gw():
        return gateway
    orch = Orchestrator(config=config, bus=bus, memory=None, gateway_factory=get_gw)
    out = await orch._file_summary_for_user("Document text here.", ["ref1"])
    assert "Краткое содержание" in out
    gateway.generate.assert_called_once()


@pytest.mark.asyncio
async def test_orchestrator_file_summary_for_user_gateway_raises_fallback():
    config = MagicMock()
    bus = MagicMock()
    async def get_gw():
        raise RuntimeError("model unavailable")
    orch = Orchestrator(config=config, bus=bus, memory=None, gateway_factory=get_gw)
    out = await orch._file_summary_for_user("text", ["ref1"])
    assert "проиндексирован" in out or "Можешь спросить" in out


@pytest.mark.asyncio
async def test_orchestrator_start_stop():
    """start() connects bus and tasks, stop() disconnects."""
    config = MagicMock()
    config.redis.url = "redis://localhost:6379/0"
    config.orchestrator.max_iterations = 5
    config.orchestrator.autonomous_mode = False
    bus = MagicMock()
    bus.connect = AsyncMock()
    bus.disconnect = AsyncMock()
    bus.stop = MagicMock()
    bus.subscribe_incoming = MagicMock()
    with patch("assistant.core.orchestrator.TaskManager") as TM:
        tasks = MagicMock()
        tasks.connect = AsyncMock()
        TM.return_value = tasks
        orch = Orchestrator(config=config, bus=bus)
        await orch.start()
        bus.connect.assert_called_once()
        tasks.connect.assert_called_once()
        bus.subscribe_incoming.assert_called_once()
        assert orch._running is True
        await orch.stop()
        assert orch._running is False
        bus.stop.assert_called_once()
        bus.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_orchestrator_process_task_agent_success_publishes_outgoing():
    """_process_task: no attachments, agent returns text -> publish_outgoing and break."""
    config = MagicMock()
    config.orchestrator.max_iterations = 5
    config.orchestrator.autonomous_mode = False
    config.redis.url = "redis://localhost:6379/0"
    bus = MagicMock()
    bus.publish_outgoing = AsyncMock()
    bus.publish_stream_token = AsyncMock()
    tasks = MagicMock()
    tasks.get = AsyncMock(
        side_effect=[
            {"state": "assistant", "stream": False, "chat_id": "c1", "user_id": "u1", "message_id": "m1", "text": "hi", "tool_results": []},
            None,
        ]
    )
    tasks.update = AsyncMock()
    mock_registry = AgentRegistry()
    mock_agent = MagicMock()
    mock_agent.handle = AsyncMock(
        return_value=AgentResult(success=True, output_text="Answer", next_agent=None, tool_calls=None)
    )
    mock_registry.register("assistant", mock_agent)
    orch = Orchestrator(config=config, bus=bus, memory=None, gateway_factory=None)
    orch._tasks = tasks
    orch._agents = mock_registry
    payload = _make_incoming_payload(chat_id="c1", message_id="m1")
    await orch._process_task("task_1", payload)
    bus.publish_outgoing.assert_called_once()
    call_arg = bus.publish_outgoing.call_args[0][0]
    assert call_arg.text == "Answer"
    assert call_arg.done is True


@pytest.mark.asyncio
async def test_orchestrator_process_task_agent_error_publishes_error():
    """_process_task: agent returns success=False -> publish_outgoing with error and break."""
    config = MagicMock()
    config.orchestrator.max_iterations = 5
    config.orchestrator.autonomous_mode = False
    config.redis.url = "redis://localhost:6379/0"
    bus = MagicMock()
    bus.publish_outgoing = AsyncMock()
    tasks = MagicMock()
    tasks.get = AsyncMock(
        return_value={"state": "assistant", "stream": False, "chat_id": "c1", "user_id": "u1", "message_id": "m1", "text": "hi", "tool_results": []}
    )
    mock_registry = AgentRegistry()
    mock_agent = MagicMock()
    mock_agent.handle = AsyncMock(
        return_value=AgentResult(success=False, error="Model unavailable")
    )
    mock_registry.register("assistant", mock_agent)
    orch = Orchestrator(config=config, bus=bus, memory=None, gateway_factory=None)
    orch._tasks = tasks
    orch._agents = mock_registry
    payload = _make_incoming_payload()
    await orch._process_task("task_1", payload)
    bus.publish_outgoing.assert_called_once()
    assert bus.publish_outgoing.call_args[0][0].text == "Model unavailable"


@pytest.mark.asyncio
async def test_orchestrator_process_task_no_task_data_breaks():
    """_process_task: tasks.get returns None -> loop breaks without publishing."""
    config = MagicMock()
    config.orchestrator.max_iterations = 5
    config.orchestrator.autonomous_mode = False
    config.redis.url = "redis://localhost:6379/0"
    bus = MagicMock()
    bus.publish_outgoing = AsyncMock()
    tasks = MagicMock()
    tasks.get = AsyncMock(return_value=None)
    mock_registry = AgentRegistry()
    mock_agent = MagicMock()
    mock_agent.handle = AsyncMock(
        return_value=AgentResult(success=True, output_text="x", next_agent=None, tool_calls=None)
    )
    mock_registry.register("assistant", mock_agent)
    orch = Orchestrator(config=config, bus=bus, memory=None, gateway_factory=None)
    orch._tasks = tasks
    orch._agents = mock_registry
    payload = _make_incoming_payload()
    await orch._process_task("task_1", payload)
    bus.publish_outgoing.assert_not_called()


@pytest.mark.asyncio
async def test_orchestrator_file_summary_no_readable_uses_placeholder_prompt():
    """_file_summary_for_user: only image placeholder -> short placeholder prompt to gateway."""
    config = MagicMock()
    bus = MagicMock()
    gateway = MagicMock()
    gateway.generate = AsyncMock(return_value="Файл сохранён. По изображениям описать не могу.")
    async def get_gw():
        return gateway
    orch = Orchestrator(config=config, bus=bus, memory=None, gateway_factory=get_gw)
    out = await orch._file_summary_for_user(" [изображение] ", ["ref1"])
    assert "изображение" in gateway.generate.call_args[0][0].lower() or "файл" in gateway.generate.call_args[0][0].lower()
    assert "Файл сохранён" in out or "проиндексирован" in out


@pytest.mark.asyncio
async def test_orchestrator_task_to_context_no_tool_results_no_stream_callback():
    """_task_to_context: state assistant but no tool_results -> stream_callback is None."""
    orch, _ = _make_orchestrator_with_mock_bus()
    payload = _make_incoming_payload()
    task_data = {
        "state": "assistant",
        "stream": True,
        "chat_id": "c1",
        "task_id": "tid_1",
        "user_id": "u1",
        "message_id": "m1",
        "tool_results": [],
    }
    ctx = orch._task_to_context("tid_1", task_data, payload)
    assert ctx.metadata.get("stream_callback") is None


def test_orchestrator_task_to_context_includes_pending_tool_calls():
    """_task_to_context passes pending_tool_calls into context for tool agent."""
    orch, _ = _make_orchestrator_with_mock_bus()
    payload = _make_incoming_payload()
    task_data = {
        "state": "tool",
        "stream": True,
        "chat_id": "c1",
        "task_id": "tid_1",
        "user_id": "u1",
        "message_id": "m1",
        "tool_results": [],
        "pending_tool_calls": [{"name": "run_skill", "args": {"skill": "filesystem"}}],
    }
    ctx = orch._task_to_context("tid_1", task_data, payload)
    assert ctx.metadata.get("pending_tool_calls") == [{"name": "run_skill", "args": {"skill": "filesystem"}}]


@pytest.mark.asyncio
async def test_orchestrator_process_task_tool_calls_then_user_reply_returns():
    """_process_task: assistant returns tool_calls -> tool agent returns user_reply -> publish and return."""
    config = MagicMock()
    config.orchestrator.max_iterations = 5
    config.orchestrator.autonomous_mode = False
    config.redis.url = "redis://localhost:6379/0"
    bus = MagicMock()
    bus.publish_outgoing = AsyncMock()
    tasks = MagicMock()
    get_returns = [
        {"state": "assistant", "stream": False, "chat_id": "c1", "user_id": "u1", "message_id": "m1", "text": "hi", "tool_results": []},
        {"state": "tool", "stream": False, "chat_id": "c1", "user_id": "u1", "message_id": "m1", "text": "hi", "tool_results": [], "pending_tool_calls": [{"name": "x"}]},
    ]
    tasks.get = AsyncMock(side_effect=get_returns)
    tasks.update = AsyncMock()
    mock_registry = AgentRegistry()
    assistant_agent = MagicMock()
    assistant_agent.handle = AsyncMock(
        return_value=AgentResult(
            success=True,
            output_text="",
            next_agent="tool",
            tool_calls=[{"name": "run_skill", "args": {}}],
        )
    )
    tool_agent = MagicMock()
    tool_agent.handle = AsyncMock(
        return_value=AgentResult(
            success=True,
            output_text="",
            next_agent="assistant",
            metadata={"tool_results": [{"user_reply": "Готово, вот ответ."}]},
        )
    )
    mock_registry.register("assistant", assistant_agent)
    mock_registry.register("tool", tool_agent)
    orch = Orchestrator(config=config, bus=bus, memory=None, gateway_factory=None)
    orch._tasks = tasks
    orch._agents = mock_registry
    payload = _make_incoming_payload()
    await orch._process_task("task_1", payload)
    assert bus.publish_outgoing.call_count >= 1
    last_call = bus.publish_outgoing.call_args[0][0]
    assert last_call.text == "Готово, вот ответ."
    assert last_call.done is True


@pytest.mark.asyncio
async def test_orchestrator_process_task_max_iterations_publishes_limit_message():
    """_process_task: hit max_iterations without final answer -> publish limit message."""
    config = MagicMock()
    config.orchestrator.max_iterations = 2
    config.orchestrator.autonomous_mode = True
    config.redis.url = "redis://localhost:6379/0"
    bus = MagicMock()
    bus.publish_outgoing = AsyncMock()
    tasks = MagicMock()
    base = {"stream": False, "chat_id": "c1", "user_id": "u1", "message_id": "m1", "text": "hi", "tool_results": []}
    tasks.get = AsyncMock(
        side_effect=[
            {**base, "state": "assistant"},
            {**base, "state": "tool", "pending_tool_calls": [{"name": "x"}]},
            {**base, "state": "assistant"},
            {**base, "state": "assistant"},  # for iteration >= max_iterations block
        ]
    )
    tasks.update = AsyncMock()
    mock_registry = AgentRegistry()
    agent = MagicMock()
    agent.handle = AsyncMock(
        return_value=AgentResult(
            success=True,
            output_text="",
            next_agent="tool",
            tool_calls=[{"name": "run_skill"}],
        )
    )
    mock_registry.register("assistant", agent)
    mock_registry.register("tool", MagicMock(handle=AsyncMock(return_value=AgentResult(success=True, output_text="", next_agent="assistant", metadata={"tool_results": []}))))
    orch = Orchestrator(config=config, bus=bus, memory=None, gateway_factory=None)
    orch._tasks = tasks
    orch._agents = mock_registry
    payload = _make_incoming_payload()
    await orch._process_task("task_1", payload)
    calls = [c[0][0].text for c in bus.publish_outgoing.call_args_list]
    assert any("Превышено число шагов" in t for t in calls)
