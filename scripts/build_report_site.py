"""Build the public report gallery from an explicit file allowlist.

Run from any directory with ``python scripts/build_report_site.py``. The output
is always ``.report-site/`` at the repository root. No source trees are copied
recursively, and unexpected existing output is rejected rather than deleted.
"""
from pathlib import Path
import stat


REPORT_FILES = tuple(
    f"{name}.{extension}"
    for name in (
        "01-opensre",
        "01-opensre-walkthrough",
        "02-openkritt",
        "02-openkritt-walkthrough",
    )
    for extension in ("html", "png")
)
SOURCE_FILES = (
    "examples/index.html",
    "examples/assets/agent-eval-flow-banner.svg",
    "examples/assets/agent-eval-flow-banner.png",
    *(f"examples/reports/{name}" for name in REPORT_FILES),
)
OUTPUT_SOURCES = {
    **{name.removeprefix("examples/"): name for name in SOURCE_FILES},
    # Previously published package descriptions and posts use these URLs.
    **{f"examples/agent-evaluation/{name}": f"examples/reports/{name}"
       for name in REPORT_FILES},
}
OUTPUT_FILES = frozenset((*OUTPUT_SOURCES, ".nojekyll"))


def _reject_links(path: Path, root: Path) -> None:
    """Check each component before following it, including directory junctions."""
    for component in (path, *path.parents):
        try:
            attributes = component.lstat()
        except FileNotFoundError:
            continue
        reparse_point = getattr(attributes, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        )
        if component.is_symlink() or reparse_point:
            raise ValueError(f"Linked paths cannot be published: {component}")
        if component == root:
            break
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Path leaves the repository: {path}")


def build_report_site(root: Path) -> Path:
    """Copy approved files into ``root/.report-site``; ``root`` supports fixtures."""
    root = root.resolve(strict=True)
    output = root / ".report-site"
    _reject_links(output, root)

    # Read all inputs before making any output changes. Other examples, private
    # docs, report README files and experimental captures are never discovered.
    payloads = {}
    for relative in SOURCE_FILES:
        source = root / relative
        _reject_links(source, root)
        if not source.is_file():
            raise ValueError(f"Missing approved report source: {relative}")
        payloads[relative] = source.read_bytes()

    allowed_directories = {
        parent.as_posix()
        for name in OUTPUT_FILES
        for parent in Path(name).parents
        if parent != Path(".")
    }
    if output.exists():
        if not output.is_dir():
            raise ValueError(".report-site must be a directory")
        pending = [output]
        while pending:
            for path in pending.pop().iterdir():
                _reject_links(path, root)
                relative = path.relative_to(output).as_posix()
                if path.is_dir() and relative in allowed_directories:
                    pending.append(path)
                elif not (path.is_file() and relative in OUTPUT_FILES):
                    raise ValueError(f"Unexpected existing report-site entry: {relative}")

    for target_name, source_name in OUTPUT_SOURCES.items():
        target = output / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payloads[source_name])
    (output / ".nojekyll").write_bytes(b"")
    return output


def main() -> None:
    try:
        output = build_report_site(Path(__file__).resolve().parents[1])
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(f"Built {len(OUTPUT_FILES)} approved gallery files in {output}")


if __name__ == "__main__":
    main()
