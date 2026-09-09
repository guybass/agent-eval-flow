"""Source identity helpers shared by collection and configuration checks."""
from agent_eval_flow import objects as o
from agent_eval_flow.objects.identity import semantic_fingerprint


def configuration_params_fingerprint(spec):
    return semantic_fingerprint('configuration-params', spec.params)


def candidate_subject(candidate, snapshot=None):
    return o.CandidateSubject(
        candidate_id=candidate.id, candidate_fingerprint=candidate.fingerprint(),
        snapshot_fingerprint=None if snapshot is None else snapshot.fingerprint,
    )
