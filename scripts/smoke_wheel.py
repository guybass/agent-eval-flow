"""Import an installed wheel target and exercise its packaged schema/templates."""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--result", type=Path, default=Path("demo-output/result"))
    parser.add_argument("--report", type=Path, default=Path("test-artifacts/wheel-report.html"))
    args = parser.parse_args()
    target = args.target.resolve()
    sys.path.insert(0, str(target))
    import agent_eval_flow as a
    origin = Path(a.__file__).resolve()
    if not origin.is_relative_to(target):
        raise SystemExit(f"Loaded editable/source package instead of wheel target: {origin}")
    loaded = a.EvaluationResult.load(args.result)
    loaded.validate().raise_for_errors()
    report = loaded.report(args.report)
    assert report.is_file() and loaded.runs.runs
    print(f"Wheel {a.__version__}: typed result loaded, validated and reported from {origin}")


if __name__ == "__main__":
    main()
