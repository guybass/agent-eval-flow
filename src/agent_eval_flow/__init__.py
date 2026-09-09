"""Agent Eval Flow: configure systems, capture evidence, evaluate saved runs."""
from .objects import *
from .objects.records import __all__ as _record_exports
from .objects.errors import __all__ as _error_exports
from .objects.assessment import __all__ as _assessment_exports

__all__ = [*_record_exports, *_error_exports, *_assessment_exports,
           "EvaluationPipeline", "AssessmentPipeline", "evaluate", "wrap_behavior_result", "__version__"]

__version__ = "0.5.0"


def __getattr__(name):
    if name == "AssessmentPipeline":
        from .pipeline.assessment import AssessmentPipeline
        return AssessmentPipeline
    if name == "wrap_behavior_result":
        from .evaluation.assessment_projection import wrap_behavior_result
        return wrap_behavior_result
    if name in ("EvaluationPipeline", "evaluate"):
        from .pipeline import api
        return getattr(api, name)
    raise AttributeError(name)
