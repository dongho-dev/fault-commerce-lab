import io
import json
import os
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from lab_suite.publish import PublicationError, Repository, publish

PROTECTED_FILES = (
    "oracle/check.py",
    "tests/test_contract.py",
    "app/api/routes.py",
    "app/schemas/order.py",
    "app/models/order.py",
    "alembic/migration.py",
)
FIRST_REF = "refs/heads/incident/cs-01"


def git(root, *args, raw=False):
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root.as_posix()}",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.file.allow=always",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "-C",
            str(root),
            *args,
        ],
        capture_output=True,
        check=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )
    return result.stdout if raw else result.stdout.decode("utf-8").strip()


def refs(root):
    output = git(root, "for-each-ref", "--format=%(refname)\t%(objectname)")
    return dict(line.split("\t", 1) for line in output.splitlines())


def snapshot(root):
    git_dir = Path(git(root, "rev-parse", "--absolute-git-dir"))
    return {
        "head": git(root, "rev-parse", "HEAD"),
        "head_file": (git_dir / "HEAD").read_bytes(),
        "index": (git_dir / "index").read_bytes(),
        "tracked": (root / "app/service.py").read_bytes(),
        "staged_and_dirty": (root / "work.txt").read_bytes(),
        "untracked": (root / "notes.txt").read_bytes(),
    }


def make_dirty(root):
    (root / "work.txt").write_text("staged user work\n", encoding="utf-8")
    git(root, "add", "work.txt")
    (root / "work.txt").write_text("unstaged user work\n", encoding="utf-8")
    (root / "app/service.py").write_text("unrelated dirty implementation\n", encoding="utf-8")
    (root / "notes.txt").write_text("untracked user notes\n", encoding="utf-8")


@dataclass
class LabFixture:
    sandbox: Path
    root: Path
    baseline: str

    def export(self, case, phase, destination):
        destination.mkdir()
        archive = git(self.root, "archive", "--format=tar", self.baseline, raw=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(destination, filter="data")
        (destination / "lab_suite").mkdir()
        (destination / "lab_suite/active_case.json").write_text(
            json.dumps({"case": case, "baseline": self.baseline, "initial_state": phase}),
            encoding="utf-8",
        )
        (destination / "app/service.py").write_text(f"fault {case}\n", encoding="utf-8")
        return {"modified_files": ["app/service.py"]}

    def publish(self, **kwargs):
        options = {
            "root": self.root,
            "baseline": self.baseline,
            "exporter": self.export,
            "run_id": "fixture",
        }
        options.update(kwargs)
        path, report = publish(["01", "02"], **options)
        assert json.loads(path.read_text(encoding="utf-8")) == report
        return path, report

    def remote(self):
        bare = self.sandbox / "remote.git"
        bare.mkdir()
        assert bare.resolve().is_relative_to(self.sandbox.resolve())
        template = self.sandbox / "empty-template"
        git(bare, "init", "--bare", f"--template={template}")
        git(bare, "config", "core.hooksPath", str(bare / "hooks"))
        git(self.root, "remote", "add", "origin", str(bare))
        git(self.root, "push", "origin", "HEAD:refs/heads/keeper")
        return bare


@pytest.fixture
def lab(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    template = tmp_path / "empty-template"
    template.mkdir()
    git(root, "init", f"--template={template}")
    for name, value in {
        "user.name": "Publication test",
        "user.email": "publication@example.invalid",
        "commit.gpgsign": "false",
        "tag.gpgsign": "false",
        "protocol.allow": "never",
        "protocol.file.allow": "always",
        "core.hooksPath": str(tmp_path / "empty-hooks"),
    }.items():
        git(root, "config", name, value)
    for name in PROTECTED_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("protected baseline\n", encoding="utf-8")
    (root / "app/service.py").write_text("healthy baseline\n", encoding="utf-8")
    (root / ".gitignore").write_text("artifacts/*\n*.json\n", encoding="utf-8")
    (root / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
    git(root, "add", "--all")
    git(root, "commit", "-m", "Fixture baseline")
    baseline = git(root, "rev-parse", "HEAD")
    git(root, "tag", "-a", "l1-baseline-v2", "-m", "Immutable fixture baseline")
    tag = git(root, "rev-parse", "refs/tags/l1-baseline-v2")
    make_dirty(root)
    before = snapshot(root)
    yield LabFixture(tmp_path, root, baseline)
    assert snapshot(root) == before
    assert git(root, "rev-parse", "refs/tags/l1-baseline-v2") == tag


def test_publication_has_only_baseline_parent_and_preserves_dirty_work(lab):
    statements = lab.root / "exercises.md"
    statements.write_text(
        "### 문제 01. First\nFirst symptom\n\n### 문제 02. Second\nSecond symptom\n",
        encoding="utf-8",
    )
    _, report = lab.publish(statements="exercises.md")
    assert report["success"], report["errors"]
    assert report["original_head_and_index_unchanged"]
    assert report["baseline_tag_unchanged"]
    for row in report["cases"]:
        commit = row["commit"]
        assert git(lab.root, "rev-parse", row["branch"]) == commit
        assert git(lab.root, "show", "-s", "--format=%P", commit) == lab.baseline
        assert row["protected_diff"] == []
        assert row["local_ref_created"] and not row["pushed"]
        changed = git(lab.root, "diff", "--name-only", lab.baseline, commit).splitlines()
        assert set(changed) == {"INCIDENT.md", "app/service.py", "lab_suite/active_case.json"}
        metadata = json.loads(git(lab.root, "show", f"{commit}:lab_suite/active_case.json"))
        assert metadata == {"case": row["case"], "baseline": lab.baseline, "initial_state": "fault"}
        assert git(lab.root, "show", f"{commit}:app/service.py") == f"fault {row['case']}"
        statement = git(lab.root, "show", f"{commit}:INCIDENT.md")
        assert statement.startswith(f"### 문제 {row['case']}.")
        assert statement.count("### 문제") == 1


def test_protected_paths_cannot_be_changed_even_when_declared(lab):
    def corrupt(case, phase, destination):
        report = lab.export(case, phase, destination)
        for name in PROTECTED_FILES:
            (destination / name).write_text("changed\n", encoding="utf-8")
        report["modified_files"].extend(PROTECTED_FILES)
        return report

    before = refs(lab.root)
    _, report = lab.publish(exporter=corrupt)
    assert not report["success"]
    assert "changes protected paths" in report["errors"][0]
    assert set(report["cases"][0]["protected_diff"]) == set(PROTECTED_FILES)
    assert report["cases"][0]["commit"] is None
    assert refs(lab.root) == before


@pytest.mark.parametrize("location", ["local", "remote"])
def test_existing_ref_is_never_replaced(lab, location):
    target = lab.remote() if location == "remote" else lab.root
    git(target, "update-ref", FIRST_REF, lab.baseline)
    before = refs(lab.root)
    target_before = refs(target)
    _, report = lab.publish(push=location == "remote")
    assert not report["success"]
    assert "Ref collision" in report["errors"][0]
    assert report["preexisting_refs"][location] == {FIRST_REF: lab.baseline}
    assert report["cases"] == []
    assert refs(lab.root) == before
    assert refs(target) == target_before


def test_local_creation_race_fails_without_partial_ref_creation(lab, monkeypatch):
    original = Repository.run
    raced = []

    def create_competing_ref(repository, *args, **kwargs):
        if args[0] == "update-ref" and not raced:
            raced.append(True)
            git(lab.root, "update-ref", FIRST_REF, lab.baseline)
        return original(repository, *args, **kwargs)

    monkeypatch.setattr(Repository, "run", create_competing_ref)
    before = refs(lab.root)
    _, report = lab.publish()
    assert raced and not report["success"]
    assert refs(lab.root) == {**before, FIRST_REF: lab.baseline}
    assert all(row["commit"] and not row["local_ref_created"] for row in report["cases"])


def test_atomic_push_creates_only_requested_refs(lab):
    bare = lab.remote()
    before = refs(bare)
    _, report = lab.publish(push=True)
    assert report["success"], report["errors"]
    expected = {f"refs/heads/{row['branch']}": row["commit"] for row in report["cases"]}
    assert refs(bare) == {**before, **expected}
    assert all(row["pushed"] and row["remote_commit"] == row["commit"] for row in report["cases"])


def test_remote_creation_race_refuses_even_a_fast_forward(lab, monkeypatch):
    bare = lab.remote()
    before = refs(bare)
    original = Repository.run
    raced = []

    def create_competing_ref(repository, *args, **kwargs):
        if args[0] == "push" and not raced:
            raced.append(True)
            git(bare, "update-ref", FIRST_REF, lab.baseline)
        return original(repository, *args, **kwargs)

    monkeypatch.setattr(Repository, "run", create_competing_ref)
    _, report = lab.publish(push=True)
    assert raced and not report["success"]
    assert refs(bare) == {**before, FIRST_REF: lab.baseline}
    for row in report["cases"]:
        assert row["local_ref_created"] and not row["pushed"]
        assert git(lab.root, "rev-parse", row["branch"]) == row["commit"]


def test_rejected_push_keeps_local_commits_and_reports_failure(lab):
    bare = lab.remote()
    before = refs(bare)
    hook = bare / "hooks/pre-receive"
    hook.parent.mkdir()
    hook.write_text(
        "#!/bin/sh\necho fixture-rejection >&2\nexit 1\n", encoding="utf-8", newline="\n"
    )
    hook.chmod(0o755)
    _, report = lab.publish(push=True)
    assert not report["success"]
    assert "fixture-rejection" in report["errors"][0]
    assert refs(bare) == before
    for row in report["cases"]:
        assert row["local_ref_created"] and not row["pushed"]
        assert git(lab.root, "rev-parse", row["branch"]) == row["commit"]
        assert git(lab.root, "show", "-s", "--format=%P", row["commit"]) == lab.baseline


def test_managed_worktree_git_file_uses_its_own_index(lab):
    managed = lab.sandbox / "managed"
    git(lab.root, "worktree", "add", "--detach", str(managed), lab.baseline)
    assert (managed / ".git").is_file()
    make_dirty(managed)
    before = snapshot(managed)
    _, report = lab.publish(root=managed)
    assert report["success"], report["errors"]
    assert snapshot(managed) == before
    for row in report["cases"]:
        assert git(lab.root, "show", "-s", "--format=%P", row["commit"]) == lab.baseline


def test_existing_publication_output_is_not_overwritten(lab):
    manifest, report = lab.publish()
    assert report["success"], report["errors"]
    before = manifest.read_bytes()
    before_refs = refs(lab.root)
    with pytest.raises(PublicationError, match="Publication run already exists"):
        lab.publish()
    assert manifest.read_bytes() == before
    assert refs(lab.root) == before_refs


@pytest.mark.parametrize("unsafe", ["environment", "oversized"])
def test_unsafe_source_export_is_rejected_before_commit(lab, unsafe):
    def unsafe_export(case, phase, destination):
        report = lab.export(case, phase, destination)
        if unsafe == "environment":
            (destination / ".env").write_text("TOKEN=fixture-only\n", encoding="utf-8")
        else:
            with (destination / "lab_suite/large.bin").open("wb") as output:
                output.truncate(8 * 1024 * 1024 + 1)
        return report

    before = refs(lab.root)
    _, report = lab.publish(exporter=unsafe_export)
    assert not report["success"]
    expected = "Environment secret file" if unsafe == "environment" else "Oversized source file"
    assert expected in report["errors"][0]
    assert report["cases"][0]["commit"] is None
    assert refs(lab.root) == before
