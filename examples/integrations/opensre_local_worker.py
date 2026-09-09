"""Prepare private CLI authentication, then use the existing native worker."""
from pathlib import Path
import os
import shutil


def main():
    source = Path("/aef/auth/auth.json")
    private = Path("/root/.codex")
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = private / "auth.json"
    shutil.copyfile(source, target)
    target.chmod(0o600)
    os.environ["CODEX_HOME"] = str(private)
    # No credential bytes, environment dump or private home enters the capture.
    from agent_eval_flow.adapters.opensre import _worker_main
    _worker_main()


if __name__ == "__main__":
    main()
