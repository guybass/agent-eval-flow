# Publishing to PyPI

The package is `agent-eval-flow`; imports use `agent_eval_flow`. Version 0.5.1
was published to [PyPI](https://pypi.org/project/agent-eval-flow/0.5.1/) on
2026-09-14 through [Trusted Publishing](https://github.com/guybass/agent-eval-flow/actions/runs/34837871575),
preserving the existing GitHub v0.5.0 tag.

## One-time account setup

A PyPI account owner must register this GitHub workflow as a trusted publisher.
For the first release, use the [pending-publisher form](https://pypi.org/manage/account/publishing/):

| Field | Value |
| --- | --- |
| PyPI project name | `agent-eval-flow` |
| GitHub owner | `guybass` |
| GitHub repository | `agent-eval-flow` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

The project is created by the first successful upload. A pending publisher does
not reserve the project name. Subsequent releases use the publisher attached to
the project. See [PyPI's instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

No long-lived PyPI token is stored in this repository. Publishing uses GitHub
OIDC scoped to the `pypi` environment and the named workflow. The GitHub
environment currently permits only the exact `v0.5.1` tag; publishing must not
run from a branch. Each later release needs its own tag added to the environment's
deployment allow-list before publication.

## Release sequence

1. Update the matching version in `pyproject.toml` and
   `src/agent_eval_flow/__init__.py`.
2. Build, run `python -m twine check --strict` on the exact new wheel and source
   archive, and push the changes to `main`.
3. Wait for all six **Tests and package** CI jobs to pass on that commit.
4. Publish a regular GitHub release tagged `vMAJOR.MINOR.PATCH` at that commit.
5. The [publish workflow](../../.github/workflows/publish.yml) verifies the tag,
   metadata, main-branch ancestry and CI result, then builds checked packages in
   a job without PyPI credentials. A separate job publishes the retained build
   artifact using Trusted Publishing and attestations.
6. Verify the [PyPI project](https://pypi.org/project/agent-eval-flow/) and install
   the exact version from PyPI before announcing availability.

After correcting publisher setup, retry an existing release with:

```bash
gh workflow run publish.yml --repo guybass/agent-eval-flow --ref v0.5.1
```

PyPI does not allow replacing a previously uploaded filename. New content needs
a new package version; do not move an existing release tag or overwrite an
existing distribution. The workflow intentionally fails on duplicate uploads.
