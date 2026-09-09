"""Bind the pinned OpenSRE harness to local incident tools and native Codex.

This module runs inside one disposable worker process. The upstream session,
ReAct loop, Codex transport, parsing, retries, and goal reviewer remain native.
The version-specific construction tap adds typed observation and call identity;
it does not choose tools or manufacture responses.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading

from agent_eval_flow import objects as o
from agent_eval_flow.adapters.opensre import SessionHandle, native_json
from agent_eval_flow.objects.values import observed, unknown


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def load_incident_module(root):
    """Load the verified fixture by path, avoiding the upstream tests namespace."""
    root = Path(root).resolve()
    spec = importlib.util.spec_from_file_location("aef_local_incident_store", root / "incident_store.py")
    if spec is None or spec.loader is None:
        raise o.ConfigurationError("Cannot load the local incident tool definitions")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_inputs(root)
    return module


class NativeCallIdentity:
    """Carry a native hook's call identity into the same-thread tool executor."""
    def __init__(self):
        self.local = threading.local()

    def before(self, loop_id, request):
        self.local.call = (loop_id, request.tool_call.id, request.tool_call.name, request.arguments)

    def consume(self, name, arguments):
        call = getattr(self.local, "call", None)
        self.local.call = None
        if call is None or call[2] != name or call[3] != arguments:
            raise o.CaptureValidationError("Incident tool execution lacks its native call identity")
        return call[0], call[1]


class IncidentToolProvider:
    """Expose the six fixture tools through OpenSRE's actual AgentTool contract."""
    def __init__(self, module, store, identity):
        self.module, self.store, self.identity = module, store, identity

    def action_tools(self, *, confirm_fn, is_tty, resolved_integrations=None):
        from core.tool.contracts import AgentTool

        def executor(name):
            def execute(arguments, context):
                loop_id, tool_call_id = self.identity.consume(name, arguments)
                return self.store.call(name, arguments, loop_id=loop_id, tool_call_id=tool_call_id)
            return execute

        return [AgentTool(**schema, execute=executor(schema["name"]), source="aef_incident_fixture")
                for schema in self.module.tool_schemas()]

    def tool_resources(self):
        return {}

    def observer(self, *, message):
        # Native tuple observers omit typed events; the constructor tap below
        # records the actual RuntimeEvent before any lossy reconstruction.
        def observe(kind, data):
            return None
        return observe


class NativeProcessCapture:
    """Observe the native CLI runner's subprocess.run without changing its IO."""
    def __init__(self, original, root):
        self.original, self.root = original, Path(root)
        self.root.mkdir(parents=True, exist_ok=False)
        self._lock, self.count = threading.Lock(), 0

    def __getattr__(self, name):
        return getattr(self.original, name)

    @staticmethod
    def _stream_bytes(value):
        return value if isinstance(value, bytes) else (value or "").encode("utf-8")

    def run(self, argv, *args, **kwargs):
        # This module-level proxy sees only the native CLI runner. Probes in
        # other modules continue to use their original subprocess binding.
        if not isinstance(argv, (tuple, list)) or "exec" not in argv:
            return self.original.run(argv, *args, **kwargs)
        with self._lock:
            self.count += 1
            sequence = self.count - 1
            directory = self.root / f"{sequence + 1:03d}"
            directory.mkdir()
        (directory / "stdin.txt").write_bytes(self._stream_bytes(kwargs.get("input")))
        receipt = {"schema_version": "aef-opensre-cli-process/1", "sequence": sequence,
                   "argv": list(argv), "cwd": kwargs.get("cwd"),
                   "started_at": datetime.now(timezone.utc).isoformat(),
                   "status": "running", "returncode": None,
                   "stream_format": "native subprocess decoded UTF-8 text; before native ANSI stripping"}
        _write_json(directory / "process.json", receipt)
        try:
            result = self.original.run(argv, *args, **kwargs)
        except BaseException as exc:
            receipt.update(status="interrupted", error_type=type(exc).__name__,
                           ended_at=datetime.now(timezone.utc).isoformat())
            (directory / "stdout.txt").write_bytes(self._stream_bytes(getattr(exc, "stdout", None)))
            (directory / "stderr.txt").write_bytes(self._stream_bytes(getattr(exc, "stderr", None)))
            _write_json(directory / "process.json", receipt)
            raise
        (directory / "stdout.txt").write_bytes(self._stream_bytes(result.stdout))
        (directory / "stderr.txt").write_bytes(self._stream_bytes(result.stderr))
        receipt.update(status="completed", returncode=result.returncode,
                       ended_at=datetime.now(timezone.utc).isoformat())
        _write_json(directory / "process.json", receipt)
        return result


def install_agent_observer(driver, observer, identity, construction_file, max_iterations):
    """Attach typed capture at the pinned native AgentConfig construction seam."""
    from core.tool.execution import ToolExecutionHooks, compose_tool_execution_hooks

    original_build, original_limit = driver.build_agent, driver._MAX_TOOL_CALLING_ITERATIONS
    constructions = []

    def build(config):
        loop_id = f"action-{len(constructions) + 1}"
        capture = observer.for_loop(loop_id)
        original_callback = config.on_runtime_event

        def callback(event):
            if original_callback is not None:
                original_callback(event)
            capture(event)

        def before(request):
            identity.before(loop_id, request)
            return None

        constructions.append({"loop_id": loop_id, "max_iterations": config.max_iterations,
            "tools": [tool.name for tool in config.tools],
            "system_sha256": hashlib.sha256(config.system.encode()).hexdigest(),
            "native_callback_preserved": original_callback is not None,
            "native_goal_reviewer_preserved": config.goal is not None})
        _write_json(construction_file, constructions)
        return original_build(replace(config, on_runtime_event=callback,
            tool_hooks=compose_tool_execution_hooks(config.tool_hooks,
                ToolExecutionHooks(before_tool_call=before))))

    driver.build_agent = build
    # Upstream does not expose this surface budget as a SessionConfig field.
    # Set its declared construction ceiling consistently in both AgentConfig
    # and ActionTurnPlan, and retain the explicit binding in effective_config.
    driver._MAX_TOOL_CALLING_ITERATIONS = max_iterations

    def restore():
        driver.build_agent, driver._MAX_TOOL_CALLING_ITERATIONS = original_build, original_limit
    return constructions, restore


def make_session(*, request, workspace, observer):
    """Return the prepared native AgentSession consumed by EmbeddedOpenSRERuntime."""
    model = request.candidate.settings.get("model", "gpt-5.6-luna")
    if not isinstance(model, str) or not model.strip():
        raise o.ConfigurationError("Local OpenSRE requires an explicit model")
    # Set native isolation policy before importing modules that cache settings.
    environment = {"LLM_PROVIDER": "codex", "CODEX_MODEL": model,
                   "OPENSRE_MEMORY_DISABLED": "1", "OPENSRE_MEMORY_AUTOEXTRACT_DISABLED": "1"}
    prior_env = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)

    def restore_environment():
        for key, value in prior_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    try:
        handle = _make_native_session(request=request, workspace=workspace, observer=observer)
    except BaseException:
        restore_environment()
        raise

    def close():
        try:
            handle.close()
        finally:
            restore_environment()
    return replace(handle, close=close)


def _make_native_session(*, request, workspace, observer):
    from core.agent_harness.harness import AgentSession, SessionConfig
    from core.agent_harness.session import SessionManager
    from core.agent_harness.turns import action_driver
    from core.agent_harness.turns.headless_adapters import BufferOutputSink, EmptyPromptContextProvider
    from integrations.harness_adapters import _register_cli_llm_adapters
    from integrations.llm_cli import runner

    settings, workspace = request.candidate.settings, Path(workspace).resolve()
    model = settings.get("model", "gpt-5.6-luna")
    iterations = settings.get("native_max_iterations", 18)
    if not isinstance(model, str) or not model.strip() or type(iterations) is not int or not 3 <= iterations <= 64:
        raise o.ConfigurationError("Local OpenSRE requires a model and native_max_iterations between 3 and 64")
    root = Path(settings.get("native_fixture_dir", settings.get("fixture_dir", ""))).resolve()
    module = load_incident_module(root)
    store = module.IncidentStore(root, workspace / "incident", invocation_id=observer.invocation_id)
    identity, sink = NativeCallIdentity(), BufferOutputSink()
    provider = IncidentToolProvider(module, store, identity)
    _register_cli_llm_adapters()
    capture = NativeProcessCapture(runner.subprocess, workspace / "codex")
    original_subprocess = runner.subprocess
    runner.subprocess = capture
    construction_file = workspace / "native-construction.json"
    constructions, restore_build = install_agent_observer(
        action_driver, observer, identity, construction_file, iterations)
    manager = SessionManager()
    session = None

    def prepare(native):
        native.resolved_integrations_cache = {}
        native.configured_integrations_known = True

    def close():
        try:
            if session is not None and session.bound_session is not None:
                manager.close(session.bound_session, extract_memory=False)
        finally:
            restore_build()
            runner.subprocess = original_subprocess

    try:
        session = AgentSession.start(SessionConfig(load_env=False, hydrate_integrations=False,
            warm_integrations=False, persistent_tasks=False, open_store=False,
            session_manager=manager), tools=provider, output=sink,
            prompts=EmptyPromptContextProvider(), prepare_session=prepare, is_tty=False)
    except BaseException:
        close()
        raise

    def final_turn(result):
        _write_json(workspace / "native-goal.json", native_json(result))
        return result.last_result

    def artifacts():
        _write_json(workspace / "native-output.json", {"lines": sink.lines, "streamed": sink.streamed})
        files = {"incident.source_logs": root / "upstream/HDFS_2k.log",
            "incident.context": root / "context.json", "incident.provenance": root / "SOURCES.json",
            "incident.license": root / "upstream/LOGHUB_LICENSE", "incident.mission": root / "incidents.json",
            "incident.tools_source": root / "incident_store.py", "native.output": workspace / "native-output.json",
            "native.session_binding": Path(__file__).resolve()}
        for name, path in {"incident.tool_audit": store.work / "tool-audit.jsonl",
                "incident.report": store.work / "investigation.json", "native.goal": workspace / "native-goal.json",
                "native.construction": construction_file,
                "native.dependencies": Path("/opt/opensre-dependencies/installed.txt")}.items():
            if path.is_file():
                files[name] = path
        for path in sorted(capture.root.rglob("*")):
            if path.is_file():
                files["codex." + ".".join(path.relative_to(capture.root).parts)] = path
        return files

    def resources():
        reason = "Pinned native CLIBackedAgentClient does not expose token counts or billing receipts"
        return o.Resources(cost_scope=request.policy.cost_scope, cost_usd=unknown(reason),
            input_tokens=unknown(reason), output_tokens=unknown(reason),
            human_minutes=unknown("Human review time was not measured"))

    return SessionHandle(session=session, session_id=session.bound_session.session_id,
        effective_config=observed({"model": model, "provider": "codex", "native_max_iterations": iterations,
            "native_iteration_binding": "core.agent_harness.turns.action_driver._MAX_TOOL_CALLING_ITERATIONS",
            "tools": list(module.TOOL_NAMES), "fixture_dir": str(root), "isolated_session": True,
            "memory_enabled": False, "memory_autoextract_enabled": False},
            "Observed prepared native SessionConfig, tool registration, and construction budget"),
        model=observed({"provider": "codex", "resolved_models": [model]}, "Explicit native CODEX_MODEL selection"),
        deployment=observed({"provider": "local-docker", "execution_id": observer.invocation_id},
            "One contained native session worker"), artifacts=artifacts, close=close,
        final_turn=final_turn, resources=resources)
