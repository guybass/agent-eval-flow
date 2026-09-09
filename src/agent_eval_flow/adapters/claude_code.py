"""Claude Code stream-json mapping with source-linked tool exchanges."""
from dataclasses import dataclass
from decimal import Decimal
import json

from .. import objects as o
from ..objects.identity import plain
from ..objects.values import unknown
from .cli import CliCapture, NativeCliBackend, native_token, parse_jsonl


@dataclass(frozen=True)
class ClaudeDialect:
    revision: str = "stream-json/1"
    profile: str = "claude_local"

    @property
    def accepted_settings(self):
        return frozenset({"tools", "allowed_tools", "max_turns"})

    @property
    def capture_files(self):
        return {}

    def validate_native(self, config):
        if config.schema_ref != o.VersionRef(name="claude-code.print", revision=self.revision) or config.values:
            raise o.ConfigurationError("Claude dialect accepts claude-code.print/stream-json/1; use candidate.settings")

    def validate_connection(self, model, environment):
        if model["provider"] != "anthropic":
            raise o.ConfigurationError("This Claude dialect supports the direct Anthropic provider only")
        for flag in ("CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_FOUNDRY"):
            if environment.get(flag, "").lower() not in ("", "0", "false"):
                raise o.ConfigurationError(f"{flag} conflicts with the declared direct Anthropic provider")
        endpoint = environment.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        if endpoint != "https://api.anthropic.com":
            raise o.ConfigurationError("An alternate Anthropic endpoint requires its own verified provider dialect")

    def argv(self, executable, work, model, settings, schema):
        if model["provider"] != "anthropic":
            raise o.ConfigurationError("This Claude dialect supports the direct Anthropic provider only")
        args = [str(executable), "--bare", "-p", "--output-format", "stream-json", "--verbose", "--model", model["id"]]
        if schema is not None:
            args += ["--json-schema", json.dumps(plain(schema), allow_nan=False)]
        turns = settings.get("max_turns")
        if turns is not None:
            if type(turns) is not int or turns <= 0:
                raise o.ConfigurationError("max_turns must be a positive integer")
            args += ["--max-turns", str(turns)]
        for key, flag in (("tools", "--tools"), ("allowed_tools", "--allowedTools")):
            values = settings.get(key)
            if values is not None:
                if not isinstance(values, (list, tuple)) or not all(isinstance(value, str) for value in values):
                    raise o.ConfigurationError(f"{key} must be an array of tool names")
                args += [flag, ",".join(values)]
        return tuple(args)

    def parse(self, capture):
        rows, issues = parse_jsonl(capture.stdout)
        terminals = tuple(row for row in rows if row.value.get("type") == "result")
        terminal = terminals[-1] if terminals else None
        refs = {}
        for row in rows:
            if isinstance(row.value.get("session_id"), str):
                refs["invocation_id"] = row.value["session_id"]
        output, state = None, "unavailable"
        if terminal:
            value = terminal.value
            if "structured_output" in value:
                output, state = value["structured_output"], "available"
            elif "result" in value:
                # Unstructured text is still an available native output.
                output, state = value["result"], "available"
        return CliCapture(rows, terminal, output, state, refs, issues)

    def status(self, native, capture):
        if native.terminal:
            value = native.terminal.value
            return "completed" if value.get("subtype") == "success" and not value.get("is_error") else "agent_error"
        return "infrastructure_error"

    def resolved_model(self, native):
        return next((row.value.get("model") for row in native.events
                     if row.value.get("type") == "system" and row.value.get("subtype") == "init"), None)

    def resources(self, native):
        terminal = native.terminal
        value = terminal.value if terminal else {}
        usage, source = value.get("usage", {}), terminal.source if terminal else None
        if not isinstance(usage, dict):
            usage = {}
        cost = unknown("Claude export did not expose a cost estimate")
        native_cost = value.get("total_cost_usd")
        if type(native_cost) in (int, float) and Decimal(str(native_cost)).is_finite() and native_cost >= 0:
            # Documented client-side estimate, not an observed provider invoice.
            cost = o.Observation(value=Decimal(str(native_cost)), status="estimated",
                                 reason="Claude Code client-side total_cost_usd estimate", evidence=(source,))
        return o.Resources(cost_usd=cost, cost_scope=("model",),
                           input_tokens=native_token(usage.get("input_tokens"), source),
                           output_tokens=native_token(usage.get("output_tokens"), source),
                           human_minutes=unknown("Human effort is not measured by the CLI export"))

    def events(self, native, execution_id):
        events, choices = [], {}
        for index, row in enumerate(native.events):
            value = row.value
            message = value.get("message", {})
            content = message.get("content", ()) if isinstance(message, dict) else ()
            event_id = f"{execution_id}/event/{index}"
            if value.get("type") == "assistant" and isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "tool_use":
                        choices[part.get("id")] = (part, event_id, row.source)
                events.append(o.Event(id=event_id, execution_id=execution_id, kind="model_response", at=None,
                                      fields=value, source=row.source))
            elif value.get("type") == "user" and isinstance(content, list):
                for number, part in enumerate(content):
                    if not isinstance(part, dict) or part.get("type") != "tool_result":
                        continue
                    prior = choices.get(part.get("tool_use_id"))
                    events.append(o.Event(id=f"{event_id}/tool/{number}", execution_id=execution_id, kind="tool_call", at=None,
                        fields={"name": prior[0].get("name", "unknown") if prior else "unknown", "native_id": part.get("tool_use_id"),
                                "arguments": prior[0].get("input") if prior else None, "result": part.get("content"),
                                "is_error": part.get("is_error", False), "decision_event_id": prior[1] if prior else None},
                        inputs=(prior[2],) if prior else (), outputs=(row.source,), source=row.source))
            else:
                events.append(o.Event(id=event_id, execution_id=execution_id, kind="native." + str(value.get("type", "unknown")),
                                      at=None, fields=value, source=row.source))
        return tuple(events)


class ClaudeCodeBackend(NativeCliBackend):
    def __init__(self, *, binding, connection, dialect=None, model=None, scenario=None):
        super().__init__(binding=binding, connection=connection, dialect=dialect or ClaudeDialect(), model=model, scenario=scenario)
