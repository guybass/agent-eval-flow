"""Optional subprocess integration with the inspected harness-eval 7.15.0 dialect.

Source contract: redhat-community-ai-tools/harness-eval, commit
51070d5f3374aaf740b234fa068a491133ba10de (CLI lint/review and output/report.py).
No upstream code or dependency is imported. Lint and semantic review are
separate explicitly configured invocations. Source files are always retained
snapshot bytes, never the mutable live candidate directory.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import anyio

from .. import objects as o
from ..objects.identity import semantic_fingerprint
from ..objects.values import observed, unknown
from .common import contained_relative
from .configuration_files_assessment import _retain_json, _unknown_resources


class HarnessEvalConfigurationEvaluator:
    """Run a pinned CLI over an isolated snapshot.

    Check params: ``mode='lint'|'review'``, ``recursive=False``; lint also
    accepts ``preset`` and ``exclude``; review requires explicit ``provider``
    and ``model``. Arbitrary CLI flags, fixes, watch mode and target rules are
    deliberately not accepted. ``command`` may include an explicit launcher
    prefix, e.g. ``(python, '-m', 'harness_eval')``.

    The portable runner bounds its wait and terminates its direct child on
    cancellation. It records descendant stop as unknown and does not claim a
    process-tree hard limit. It preserves streamed partial stdout/stderr.
    """

    def __init__(self, *, command, artifacts, workspace_root, environment=None,
                 ref=None, upstream_revision="7.15.0", timeout_s=120):
        self.ref = ref or o.VersionRef(name="harness-eval", revision="adapter-1")
        if not self.ref.revision or upstream_revision != "7.15.0":
            raise o.ConfigurationError("This adapter supports the explicitly pinned harness-eval 7.15.0 dialect")
        if (not command or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in command)
                or not Path(command[0]).is_absolute()):
            raise o.ConfigurationError("Scanner command needs an absolute executable and separate nonempty argv")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s < float("inf"):
            raise o.ConfigurationError("Scanner timeout must be finite and positive")
        self.command = tuple(command)
        self.artifacts, self.workspace_root = artifacts, Path(workspace_root).resolve()
        self.upstream_revision, self.timeout_s = upstream_revision, timeout_s
        self.environment = dict(environment or {})
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in self.environment.items()):
            raise o.ConfigurationError("Scanner environment must contain string values")

    def _options(self, request):
        if request.check.evaluator != self.ref:
            raise o.ConfigurationError("Selected scanner adapter revision does not match its binding")
        params = request.check.params
        mode = params.get("mode", "lint")
        if mode not in {"lint", "review"}:
            raise o.ConfigurationError("Scanner mode must be explicitly lint or review")
        allowed = {"mode", "recursive", "preset", "exclude"} if mode == "lint" else {"mode", "recursive", "provider", "model"}
        if set(params) - allowed or type(params.get("recursive", False)) is not bool:
            raise o.ConfigurationError("Unsupported scanner option; arbitrary flags/fixes are not accepted")
        if request.references:
            raise o.ConfigurationError("This CLI dialect has no candidate-reference input channel")
        if request.check.rule_selection:
            raise o.ConfigurationError("Use explicit preset/exclude params; this CLI dialect has no arbitrary rule selector")
        if request.check.output_types:
            raise o.ConfigurationError("This scanner adapter emits attributed findings, not declared numeric output values")
        if mode == "review" and (params.get("provider") not in {"gemini", "anthropic"}
                or not isinstance(params.get("model"), str) or not params["model"]):
            raise o.ConfigurationError("Semantic review requires an explicit supported provider and model")
        if mode == "lint" and params.get("preset", "recommended") not in {"recommended", "strict", "security", "pre-workflow"}:
            raise o.ConfigurationError("Unsupported harness-eval preset")
        excludes = params.get("exclude", ())
        if not isinstance(excludes, (tuple, list)) or not all(isinstance(value, str) and value for value in excludes):
            raise o.ConfigurationError("Scanner exclusions must be nonempty strings")
        return mode

    def _materialize(self, snapshot, root):
        project = root / "project"
        home = root / "home"
        user = home / ".claude"
        project.mkdir()
        user.mkdir(parents=True)
        path_map = {}
        for entry in snapshot.entries:
            # The pinned --user-config channel only models Claude user scope.
            if entry.scope not in {"project", "repository", "user"}:
                raise o.ConfigurationError(f"Unsupported scanner source scope: {entry.scope}")
            if entry.scope == "user" and entry.source_tool not in {"claude", "claude_code", "claude-code"}:
                raise o.ConfigurationError("The selected scanner dialect supports only Claude user-scope materialization")
            target_root = user if entry.scope == "user" else project
            logical = entry.path
            if entry.scope == "user" and logical.startswith(".claude/"):
                logical = logical[len(".claude/"):]
            target = contained_relative(target_root, logical)
            if str(target) in path_map:
                raise o.ConfigurationError("Two snapshot entries map to the same scanner path")
            verification = self.artifacts.verify(entry.artifact)
            if any(issue.severity == "error" for issue in verification.issues):
                raise o.CaptureValidationError("Retained configuration artifact is missing or changed")
            source = Path(entry.artifact.uri)
            if not source.is_absolute():
                raise o.CaptureValidationError("Scanner materialization needs absolute local artifact references")
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry.artifact.sha256:
                raise o.CaptureValidationError("Configuration artifact changed after verification")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            path_map[os.path.normcase(str(target.resolve()))] = entry
        return project, home, user, path_map

    async def _process(self, argv, *, cwd, environment, label, recorder, retained):
        stdout = self.artifacts.open_writer(label + ".stdout", "application/octet-stream")
        stderr = self.artifacts.open_writer(label + ".stderr", "application/octet-stream")
        process = None
        exit_code = None
        failure = None
        started = datetime.now(timezone.utc)
        async def drain(stream, writer):
            while True:
                try:
                    data = await stream.receive(65536)
                except anyio.EndOfStream:
                    return
                if not data:
                    return
                writer.write(data)
        try:
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            with anyio.fail_after(self.timeout_s):
                process = await anyio.open_process(argv, cwd=cwd, env=environment, **options)
                await process.stdin.aclose()
                async with anyio.create_task_group() as group:
                    group.start_soon(drain, process.stdout, stdout)
                    group.start_soon(drain, process.stderr, stderr)
                    exit_code = await process.wait()
            return exit_code
        except BaseException as exc:
            failure = exc
            raise
        finally:
            with anyio.CancelScope(shield=True):
                if process is not None:
                    if process.returncode is None:
                        try:
                            process.kill()
                            with anyio.move_on_after(5):
                                await process.wait()
                        except (ProcessLookupError, OSError):
                            pass
                    exit_code = process.returncode
                    with anyio.move_on_after(5):
                        await process.aclose()
                for suffix, writer in (("stdout", stdout), ("stderr", stderr)):
                    ref = writer.commit()
                    retained[label + "." + suffix] = ref
                    recorder.record_artifact(label + "." + suffix, ref)
                receipt = _retain_json(self.artifacts, label + ".process", {
                    "argv": argv, "exit_code": exit_code, "started_at": started.isoformat(),
                    "ended_at": datetime.now(timezone.utc).isoformat() if exit_code is not None else None,
                    "direct_child_stopped": exit_code is not None,
                    "descendants_stopped": None,
                    "stop_reason": "Only direct-child lifecycle is observed; no process-tree containment claim",
                    "failure": type(failure).__name__ if failure else None})
                retained[label + ".process"] = receipt
                recorder.record_artifact(label + ".process", receipt)

    def _normalize(self, request, report, report_ref, path_map, project, mode):
        findings, rule_statuses = [], {}
        inventory_known = True
        def finding(row, locator, *, semantic=False):
            nonlocal inventory_known
            if not isinstance(row, Mapping):
                raise ValueError("Provider finding must be an object")
            raw_file = row.get("file")
            entry = None
            if isinstance(raw_file, str) and raw_file:
                source = Path(raw_file)
                candidate_path = source if source.is_absolute() else project / source
                # Normalize lexically; a provider path never authorizes opening
                # or resolving symlinks in an unrelated machine directory.
                entry = path_map.get(os.path.normcase(os.path.abspath(candidate_path)))
            evidence = [o.EvidenceRef(artifact=report_ref, locator=locator,
                                       description="Attributed native scanner finding")]
            if entry:
                subject = o.ComponentSubject(candidate=request.subject, locator=entry.path,
                    source_tool=entry.source_tool, scope=entry.scope, entry_id=entry.id)
                evidence.append(o.EvidenceRef(artifact=entry.artifact,
                    locator=str(row["line"]) if row.get("line") else None, description="Retained source bytes"))
            else:
                subject = o.ComponentSubject(candidate=request.subject, locator=raw_file or locator,
                    source_tool="harness-eval", scope="provider", mapping_issue="Provider location does not identify a retained snapshot entry")
            rule = row.get("category" if semantic else "rule")
            message = row.get("description" if semantic else "message")
            severity = "info" if semantic else row.get("severity")
            if not isinstance(rule, str) or not rule or not isinstance(message, str) or not message or severity not in {"error", "warning", "info"}:
                raise ValueError("Provider finding has an invalid rule, severity or message")
            findings.append(o.Finding(id=semantic_fingerprint("harness-finding", {"request": request.id, "locator": locator}),
                subject=subject, rule=o.VersionRef(name=rule, revision=self.upstream_revision), message=message,
                severity=severity, basis="inferred", evidence=tuple(evidence), suggestion=row.get("suggestion"),
                metadata={"provider": "harness-eval", "mode": mode, "original": dict(row)}))
        if mode == "lint":
            inspection = report.get("inspection")
            if not isinstance(inspection, Mapping) or not isinstance(inspection.get("summary"), Mapping):
                raise ValueError("Expected harness-lint inspection summary")
            total = 0
            for kind, components in inspection.items():
                if kind == "summary":
                    continue
                if not isinstance(components, list):
                    raise ValueError("Inspection components must be lists")
                for index, component in enumerate(components):
                    if not isinstance(component, Mapping) or not isinstance(component.get("findings"), list):
                        raise ValueError("Invalid component finding inventory")
                    total += 1
                    rules = component.get("rules")
                    if not isinstance(rules, list):
                        inventory_known = False
                    else:
                        for rule in rules:
                            if not isinstance(rule, Mapping) or rule.get("result") not in {"pass", "fail"} or not isinstance(rule.get("rule"), str):
                                raise ValueError("Invalid provider rule result")
                            rule_statuses[f"{kind}/{component.get('name', index)}/{rule['rule']}"] = rule["result"]
                    for item, row in enumerate(component["findings"]):
                        finding(row, f"/inspection/{kind}/{index}/findings/{item}")
            if type(inspection["summary"].get("total")) is not int or total != inspection["summary"]["total"]:
                raise ValueError("Native component inventory contradicts its summary")
            if total == 0:
                inventory_known = False
            # A component's executed rules do not disclose skipped rules or
            # suppressed findings. Do not promote this report into full coverage.
            inventory_known = False
        else:
            rubric = report.get("rubric")
            if not isinstance(rubric, list):
                raise ValueError("Expected harness-review rubric list")
            for index, component in enumerate(rubric):
                if not isinstance(component, Mapping) or not isinstance(component.get("issues"), list):
                    raise ValueError("Invalid semantic review output")
                for item, row in enumerate(component["issues"]):
                    finding(row, f"/rubric/{index}/issues/{item}", semantic=True)
            inventory_known = False
        return tuple(findings), rule_statuses, inventory_known

    async def evaluate(self, request, *, recorder):
        mode = self._options(request)
        started = datetime.now(timezone.utc)
        retained, findings, rules = {}, (), {}
        error = None
        reason = "Provider retains executed rule evidence but does not establish skipped/suppressed-rule coverage"
        status, activity_status, coverage_status = "ok", "completed", "unknown"
        try:
            if request.capture.snapshot is None:
                raise o.CaptureValidationError("Configuration capture contains no retained snapshot")
            self.workspace_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="harness-assessment-", dir=self.workspace_root) as directory:
                root = Path(directory)
                project, home, user, path_map = self._materialize(request.capture.snapshot, root)
                env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if name in os.environ}
                env.update(self.environment)
                env.update({"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config")})
                settings = {"adapter": self.ref, "upstream_revision": self.upstream_revision,
                    "check": request.check, "snapshot_fingerprint": request.capture.snapshot.fingerprint,
                    "source_map": {path: entry.id for path, entry in path_map.items()},
                    "environment_keys": sorted(self.environment), "timeout_s": self.timeout_s}
                retained["harness.settings"] = _retain_json(self.artifacts, "harness.settings", settings)
                recorder.record_artifact("harness.settings", retained["harness.settings"])
                code = await self._process((*self.command, "--version"), cwd=project, environment=env,
                                           label="harness.version", recorder=recorder, retained=retained)
                version_text = Path(retained["harness.version.stdout"].uri).read_text(encoding="utf-8")
                if code != 0 or not re.search(r"(?<![\d.])" + re.escape(self.upstream_revision) + r"(?![\d.])", version_text):
                    raise o.ConfigurationError("Installed scanner does not match the selected upstream revision")
                params = request.check.params
                argv = [*self.command, "harness-lint" if mode == "lint" else "harness-review", str(project),
                        "--format", "json", "--user-config", str(user)]
                if params.get("recursive", False):
                    argv.append("--recursive")
                if mode == "lint":
                    argv += ["--preset", params.get("preset", "recommended"), "--fail-on-warning"]
                    for pattern in params.get("exclude", ()):
                        argv += ["--exclude", pattern]
                else:
                    argv += ["--provider", params["provider"], "--model", params["model"]]
                code = await self._process(tuple(argv), cwd=project, environment=env,
                                           label="harness.report", recorder=recorder, retained=retained)
                report_ref = retained["harness.report.stdout"]
                report = json.loads(Path(report_ref.uri).read_text(encoding="utf-8"))
                if not isinstance(report, Mapping) or report.get("metadata", {}).get("version") != self.upstream_revision:
                    raise ValueError("Native report lacks the selected upstream version metadata")
                if mode == "review" and (report["metadata"].get("provider") != params["provider"]
                        or report["metadata"].get("model") != params["model"]):
                    raise ValueError("Semantic review did not report the selected provider and model")
                findings, rules, _ = self._normalize(request, report, report_ref, path_map, project, mode)
                if code not in ({0, 1} if mode == "lint" else {0}):
                    raise RuntimeError(f"Scanner failed with exit code {code}")
                if mode == "lint" and code == 1 and not findings:
                    raise ValueError("Findings exit code lacks corresponding retained diagnostics")
        except Exception as exc:
            status, activity_status, coverage_status = "error", "error", "error"
            reason = f"{type(exc).__name__}: {exc}"
            error = o.ErrorRecord(code=type(exc).__name__, message=str(exc))
        activity = o.AssessmentActivity(id=request.activity_id, request_id=request.id, implementation=self.ref,
            input_fingerprint=request.input_fingerprint, subjects=(request.subject,), phase="configuration_evaluation",
            status=activity_status, resources=_unknown_resources(), inventory_complete=unknown("Scanner usage is unavailable"),
            started_at=started, ended_at=datetime.now(timezone.utc), artifacts=retained, error=error)
        recorder.record_activity(activity, final=True)
        assessment = o.Assessment(id=request.id, subject=request.subject,
            origin=o.ConfigurationOrigin(request_id=request.id, check_id=request.check.id,
                check_fingerprint=request.check_fingerprint, evaluator=self.ref), status=status,
            conclusion="unknown", findings=findings, reason=reason,
            evidence=tuple(o.EvidenceRef(artifact=ref, description=name) for name, ref in retained.items()),
            activity_refs=(o.ActivityRef(namespace="assessment", id=request.activity_id),))
        coverage = o.CheckCoverage(candidate_id=request.subject.candidate_id, check_id=request.check.id,
            request_id=request.id, assessment_id=assessment.id, status=coverage_status, reason=reason,
            rule_statuses=rules, inventory_complete=unknown(reason), omitted_suppressed_findings=None)
        return o.AssessmentOutput(assessment=assessment, activity=activity, coverage=coverage, artifacts=retained)
