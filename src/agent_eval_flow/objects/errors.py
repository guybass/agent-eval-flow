"""Public errors keep structured validation issues available to callers."""

__all__ = ["AgentEvalFlowError", "ValidationError", "ConfigurationError",
           "CaptureCompatibilityError", "CaptureValidationError", "StorageError"]


class AgentEvalFlowError(Exception):
    pass


class ValidationError(AgentEvalFlowError):
    def __init__(self, report):
        if isinstance(report, str):
            from .records import ValidationIssue, ValidationReport
            report = ValidationReport(issues=(ValidationIssue(path="/", message=report, severity="error"),))
        self.report = report
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in report.issues if i.severity == "error"))


class ConfigurationError(ValidationError):
    pass


class CaptureCompatibilityError(ValidationError):
    pass


class CaptureValidationError(ValidationError):
    pass


class StorageError(AgentEvalFlowError):
    pass
