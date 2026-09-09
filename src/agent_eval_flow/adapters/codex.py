"""Codex exec JSONL adapter. No replacement model client or agent loop."""
from dataclasses import dataclass

from .. import objects as o
from ..objects.values import unknown
from .cli import CliCapture, NativeCliBackend, json_output, native_token, parse_jsonl


@dataclass(frozen=True)
class CodexDialect:
    revision: str = "exec-jsonl/1"
    profile: str = "codex_local"

    @property
    def accepted_settings(self):
        return frozenset({"sandbox"})

    @property
    def capture_files(self):
        return {"native.output": "final.json"}

    def validate_native(self, config):
        if config.schema_ref != o.VersionRef(name="codex.exec", revision=self.revision) or config.values:
            raise o.ConfigurationError("Codex dialect accepts codex.exec/exec-jsonl/1; declare CLI options in candidate.settings")

    def argv(self, executable, work, model, settings, schema):
        if model["provider"] != "openai":
            raise o.ConfigurationError("This isolated Codex dialect supports the default OpenAI provider only")
        sandbox = settings.get("sandbox", "read-only")
        if sandbox not in ("read-only", "workspace-write"):
            raise o.ConfigurationError("Codex sandbox must be read-only or workspace-write")
        args = [str(executable), "exec", "--cd", str(work), "--sandbox", sandbox,
                "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check", "--json",
                "--model", model["id"], "--output-last-message", str(work / "final.json")]
        if schema is not None:
            args += ["--output-schema", str(work / "response.schema.json")]
        args.append("-")
        return tuple(args)

    def parse(self, capture):
        rows, issues = parse_jsonl(capture.stdout)
        terminals = tuple(row for row in rows if row.value.get("type") in ("turn.completed", "turn.failed"))
        refs = {}
        for row in rows:
            if row.value.get("type") == "thread.started" and isinstance(row.value.get("thread_id"), str):
                refs["invocation_id"] = row.value["thread_id"]
        output, state, output_issues = json_output(capture.artifacts.get("native.output"))
        return CliCapture(rows, terminals[-1] if terminals else None, output, state, refs, issues + output_issues)

    def status(self, native, capture):
        if native.terminal:
            return "completed" if native.terminal.value["type"] == "turn.completed" else "agent_error"
        return "infrastructure_error"

    def resolved_model(self, native):
        return None  # The supported JSONL schema does not expose model resolution.

    def resources(self, native):
        usage = native.terminal.value.get("usage", {}) if native.terminal else {}
        if not isinstance(usage, dict):
            usage = {}
        source = native.terminal.source if native.terminal else None
        return o.Resources(cost_usd=unknown("Codex JSONL contains no actual dollar charge"), cost_scope=("model",),
                           input_tokens=native_token(usage.get("input_tokens"), source),
                           output_tokens=native_token(usage.get("output_tokens"), source),
                           human_minutes=unknown("Human effort is not measured by the CLI export"))

    def events(self, native, execution_id):
        events, started = [], {}
        for index, row in enumerate(native.events):
            value, item = row.value, row.value.get("item", {})
            if not isinstance(item, dict):
                item = {}
            kind = "native." + str(value.get("type", "unknown"))
            fields = dict(value)
            inputs, outputs = (), ()
            if item.get("type") == "command_execution":
                if value.get("type") == "item.started":
                    kind = "model_response"
                    started[item.get("id")] = (f"{execution_id}/event/{index}", row.source)
                elif value.get("type") == "item.completed":
                    kind = "tool_call"
                    prior = started.get(item.get("id"))
                    fields = {"name": "command_execution", "native_id": item.get("id"),
                              "arguments": {"command": item.get("command")},
                              "result": {"stdout": item.get("aggregated_output"), "exit_code": item.get("exit_code")},
                              "decision_event_id": prior[0] if prior else None, "native": value}
                    inputs = (prior[1],) if prior else ()
                    outputs = (row.source,)
            events.append(o.Event(id=f"{execution_id}/event/{index}", execution_id=execution_id, kind=kind,
                                  at=None, fields=fields, inputs=inputs, outputs=outputs, source=row.source))
        return tuple(events)


class CodexBackend(NativeCliBackend):
    def __init__(self, *, binding, connection, dialect=None, model=None, scenario=None):
        super().__init__(binding=binding, connection=connection, dialect=dialect or CodexDialect(), model=model, scenario=scenario)
