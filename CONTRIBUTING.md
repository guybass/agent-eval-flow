# Contributing

Agent Eval Flow is a developer preview. Issues and small pull requests are welcome.
For a bug, include the package/Python versions, a minimal reproducible example,
the expected outcome and the observed result. Remove credentials and private
task data before sharing evidence.

## Local checks

From a checkout, using Python 3.11 or later:

```bash
python -m pip install -e ".[test,cli]" build
python scripts/verify_acceptance_checkpoint.py
python -m pytest
python examples/archive_review.py --output demo-output
python -m build
python scripts/verify_distribution.py
```

Default tests do not start live models. Live profiles require explicit selection
and prepared runtimes; their setup is documented in [tests/e2e/SHOWCASE.md](tests/e2e/SHOWCASE.md).

Keep metric meaning, provenance and missingness explicit. Fixes should include
a regression check that demonstrates the affected behavior. Preserve the frozen
acceptance fixtures and their hashes; add new fixtures separately.

Local outputs belong in ignored `demo-output/` or `test-artifacts/` directories.
Include only deliberately selected, reviewed report artifacts in documentation.
Third-party fixtures retain their own licenses and provenance.
