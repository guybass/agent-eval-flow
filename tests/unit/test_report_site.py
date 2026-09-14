"""The Pages artifact must never include nearby private or unapproved files."""
from pathlib import Path

import pytest

from scripts.build_report_site import (
    OUTPUT_FILES,
    REPORT_FILES,
    SOURCE_FILES,
    build_report_site,
)


def approved_sources(root: Path) -> None:
    for name in SOURCE_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture content: {name}".encode())


def test_gallery_copies_only_allowlisted_bytes_and_preserves_legacy_urls(tmp_path):
    approved_sources(tmp_path)
    for name in (
        "docs/internal-plan.md",
        "examples/private-profile.json",
        "examples/reports/README.md",
        "examples/reports/raw-trace.json",
        "examples/assets/unapproved.png",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must remain unpublished", encoding="utf-8")

    output = build_report_site(tmp_path)
    assert {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()} == OUTPUT_FILES
    for name in REPORT_FILES:
        expected = (tmp_path / "examples/reports" / name).read_bytes()
        assert (output / "reports" / name).read_bytes() == expected
        assert (output / "examples/agent-evaluation" / name).read_bytes() == expected
    assert (output / ".nojekyll").read_bytes() == b""
    assert build_report_site(tmp_path) == output


def test_gallery_rejects_missing_input_before_writing_output(tmp_path):
    approved_sources(tmp_path)
    (tmp_path / SOURCE_FILES[-1]).unlink()
    with pytest.raises(ValueError, match="Missing approved report source"):
        build_report_site(tmp_path)
    assert not (tmp_path / ".report-site").exists()


def test_gallery_rejects_unexpected_old_output_without_deleting_it(tmp_path):
    approved_sources(tmp_path)
    output = build_report_site(tmp_path)
    unexpected = output / "private.json"
    unexpected.write_bytes(b"private fixture")
    with pytest.raises(ValueError, match="Unexpected existing report-site entry: private.json"):
        build_report_site(tmp_path)
    assert unexpected.read_bytes() == b"private fixture"


def test_gallery_rejects_linked_source_file(tmp_path):
    approved_sources(tmp_path)
    source = tmp_path / SOURCE_FILES[-1]
    real_file = tmp_path / "private-capture.txt"
    real_file.write_bytes(b"private fixture")
    source.unlink()
    try:
        source.symlink_to(real_file)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this host")
    with pytest.raises(ValueError, match="Linked paths cannot be published"):
        build_report_site(tmp_path)
    assert not (tmp_path / ".report-site").exists()
