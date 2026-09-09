"""Optional runtime adapters. Import a concrete module to bind a runtime.

Importing agent_eval_flow never loads or starts an optional agent framework.
"""

__all__ = ["FileConfigurationCollector", "HarnessEvalConfigurationEvaluator", "CallbackSnapshotBinder"]


def __getattr__(name):
    # Keep construction and ordinary package import free of scanner/process work.
    modules = {
        "FileConfigurationCollector": ".configuration_files_assessment",
        "HarnessEvalConfigurationEvaluator": ".harness_eval_assessment",
        "CallbackSnapshotBinder": ".snapshot_binding_assessment",
    }
    if name not in modules:
        raise AttributeError(name)
    from importlib import import_module
    value = getattr(import_module(modules[name], __name__), name)
    globals()[name] = value
    return value
