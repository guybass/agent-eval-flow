"""Apply capture policy revision 4 to a completed local run, without an agent.

python -m examples.openkritt_local_regrade --source demo-output/openkritt-local-run2 --output demo-output/openkritt-local-final

The source study, result and native evidence remain intact. Revision 4 checks
native cumulative-repeat envelopes, lossless tool mapping, and complete portable
EvidenceRef provenance. Optional archive completion produces a derived capture.
There is deliberately no backend binding in this command.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

import agent_eval_flow as a
from examples.openkritt_local_review import CHECKS, CaptureMetrics, save_outputs


class CaptureMetricsV4(CaptureMetrics):
    ref = a.VersionRef(name="example.openkritt-local-capture", revision="4")


def regrade(source, output, *, complete_evidence=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or (output / "result").exists():
        raise ValueError("Choose a fresh output directory; the original evaluation is preserved")
    original_study = a.Study.load(source / "study")
    original_result = a.EvaluationResult.load(source / "result")
    metric = CaptureMetricsV4()
    suite = replace(original_study.suite, version="4-complete-evidence-lineage",
        metrics=tuple(replace(spec, source=a.EvaluatorSource(ref=metric.ref)) for spec in original_study.suite.metrics))
    study = replace(original_study, suite=suite)
    output.mkdir(parents=True, exist_ok=True)
    study.save(output / "study")
    captured = original_result.runs
    if complete_evidence:
        from agent_eval_flow.adapters.openkritt import complete_evidence_bundle
        from agent_eval_flow.storage.artifacts import ArtifactCache
        if len(captured.runs) != 1:
            raise ValueError("The local offline evidence repair expects one native run")
        native_run = captured.runs[0]
        bundle = ArtifactCache(output / "evidence").write_bytes("openkritt.bundle.complete",
            complete_evidence_bundle(native_run), "application/zip")
        updated_run = replace(native_run, artifacts={**native_run.artifacts, "openkritt.bundle": bundle})
        captured = replace(captured, id=captured.id + "/portable-" + bundle.sha256[:16], runs=(updated_run,))
        captured.validate().raise_for_errors()
    result = a.EvaluationPipeline(study=study, evaluators={metric.ref.name: metric}).eval(runs=captured)
    summary = save_outputs(study, result, metric, output)
    summary.update({"regraded_from": str(source), "original_result_id": original_result.id,
        "original_suite_fingerprint": original_result.suite_fingerprint, "final_suite_fingerprint": result.suite_fingerprint,
        "evaluation_policy_revision": "4", "native_agent_invocations_in_this_command": 0,
        "policy_change": "Verify exact native repeat/branch lineage plus complete portable EvidenceRef bytes, hashes, locators and descriptions",
        "native_capture_unchanged": result.runs.id == original_result.runs.id,
        "offline_repackaged_capture": complete_evidence, "same_native_execution": True,
        "original_runset_fingerprint": original_result.runs.fingerprint(),
        "derived_runset_fingerprint": result.runs.fingerprint()})
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--complete-evidence", action="store_true", help="Create a derived capture with a complete portable ZIP; retain original ZIP and native execution records")
    args = parser.parse_args()
    summary = regrade(args.source, args.output, complete_evidence=args.complete_evidence)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str), flush=True)
    return 0 if summary["capture_acceptance"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
