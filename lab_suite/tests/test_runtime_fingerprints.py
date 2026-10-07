from types import SimpleNamespace

import pytest

from lab_suite import __main__ as teacher


def test_added_runtime_source_participates_in_restoration_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(teacher, "provider", lambda case: SimpleNamespace(
        runtime_paths=lambda case: ["lab_suite/runtime.py"]
    ))
    runtime = tmp_path / "lab_suite" / "runtime.py"
    runtime.parent.mkdir()
    runtime.write_text("value = 1\n", encoding="utf-8")
    paths = teacher.application_paths("01")
    before = teacher.hashes(tmp_path, paths)
    runtime.write_text("value = 2\n", encoding="utf-8")
    assert teacher.hashes(tmp_path, paths) != before
    runtime.write_text("value = 1\n", encoding="utf-8")
    assert teacher.hashes(tmp_path, paths) == before


@pytest.mark.parametrize("path", ["../outside.py", "/outside.py", "C:outside.py", "."])
def test_runtime_fingerprints_reject_paths_outside_checkout(path, monkeypatch):
    monkeypatch.setattr(teacher, "provider", lambda case: SimpleNamespace(
        runtime_paths=lambda case: [path]
    ))
    with pytest.raises(ValueError, match="repository-relative"):
        teacher.application_paths("01")
