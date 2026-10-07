import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from lab_suite.__main__ import ROOT, export_source
from lab_suite.catalog import BASELINE, CASES, normalize

BASELINE_TAG = "l1-baseline-v2"
PROTECTED = ("oracle", "tests", "app/api", "app/schemas", "app/models", "alembic")
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024


class PublicationError(RuntimeError):
    pass



def remove_authoring_keys(source):
    removed = []
    for path in (source / "lab_suite" / "cases").glob("advanced_*.py"):
        safe = inside(source, path)
        text = safe.read_text(encoding="utf-8-sig")
        module = ast.parse(text)
        spans = []
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "replacements":
                    start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                    spans.append((start - 1, node.end_lineno))
        if not spans:
            continue
        lines = text.splitlines(keepends=True)
        for start, end in reversed(spans):
            del lines[start:end]
        result = "".join(lines)
        ast.parse(result)
        safe.write_text(result, encoding="utf-8")
        removed.append(path.relative_to(source).as_posix())
    return removed


def inside(base, path):
    base = Path(base).resolve()
    path = Path(path)
    resolved = path.resolve()
    if resolved == base or not resolved.is_relative_to(base):
        raise PublicationError(f"Path must remain below {base}: {path}")
    for part in (path, *path.parents):
        if part == base:
            break
        if part.is_symlink() or part.is_junction():
            raise PublicationError(f"Linked publication path is not allowed: {part}")
    return resolved


def environment(index=None):
    transport = {"GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS", "GIT_TERMINAL_PROMPT"}
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k in transport}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_LITERAL_PATHSPECS"] = "1"
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return env


class Repository:
    def __init__(self, root, baseline):
        self.root = Path(root).resolve(strict=True)
        self.baseline = baseline
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={self.root.as_posix()}",
                "-C",
                str(self.root),
                "rev-parse",
                "--show-toplevel",
                "--absolute-git-dir",
            ],
            cwd=self.root,
            env=environment(),
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise PublicationError(result.stderr.decode("utf-8", "replace").strip())
        paths = result.stdout.decode("utf-8").splitlines()
        if len(paths) != 2 or Path(paths[0]).resolve(strict=True) != self.root:
            raise PublicationError("Root must be the exact Git worktree root")
        self.git_dir = Path(paths[1]).resolve(strict=True)
        self.tag_object = self.run("rev-parse", "--verify", f"refs/tags/{BASELINE_TAG}").strip()
        self.check_baseline()
        self.head = self.run("rev-parse", "--verify", "HEAD").strip()
        self.index = self.git_dir / "index"
        self.index_hash = self.index_digest()
        self.zero = "0" * len(self.baseline)

    def run(self, *args, source=None, index=None, input_text=None, timeout=120):
        work_tree = Path(source).resolve() if source else self.root
        config = ["-c", "remote.origin.mirror=false"] if args[0] == "push" else []
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={self.root.as_posix()}",
                "-c",
                f"safe.directory={work_tree.as_posix()}",
                "-c",
                "core.quotePath=false",
                *config,
                f"--git-dir={self.git_dir}",
                f"--work-tree={work_tree}",
                *args,
            ],
            cwd=work_tree,
            env=environment(index),
            input=input_text.encode("utf-8") if input_text is not None else None,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
            raise PublicationError(f"git {args[0]} failed ({result.returncode}): {detail}")
        return result.stdout.decode("utf-8")

    def index_digest(self):
        if not self.index.exists():
            return None
        return hashlib.sha256(self.index.read_bytes()).hexdigest()

    def check_baseline(self):
        tag_object = self.run("rev-parse", "--verify", f"refs/tags/{BASELINE_TAG}").strip()
        commit = self.run("rev-parse", "--verify", f"refs/tags/{BASELINE_TAG}^{{commit}}").strip()
        if tag_object != self.tag_object or commit != self.baseline:
            raise PublicationError(
                "Immutable baseline tag object or baseline commit does not match"
            )

    def check_original(self):
        if self.run("rev-parse", "--verify", "HEAD").strip() != self.head:
            raise PublicationError("Original worktree HEAD changed during publication")
        if self.index_digest() != self.index_hash:
            raise PublicationError("Original worktree index changed during publication")

    def local_refs(self, refs):
        lines = self.run("for-each-ref", "--format=%(refname)\t%(objectname)", *refs)
        found = dict(line.split("\t", 1) for line in lines.splitlines())
        return {ref: oid for ref, oid in found.items() if ref in refs}

    def remote_target(self, required):
        remotes = self.run("remote").splitlines()
        if "origin" not in remotes:
            if required:
                raise PublicationError("Cannot push: no configured origin remote")
            return None
        urls = self.run("remote", "get-url", "--push", "--all", "origin").splitlines()
        if len(urls) != 1 or not urls[0]:
            raise PublicationError("Origin must have exactly one usable push URL")
        return urls[0]

    def remote_refs(self, target, refs):
        lines = self.run("ls-remote", "--refs", target, *refs, timeout=60)
        found = dict(
            (ref, oid) for oid, ref in (line.split("\t", 1) for line in lines.splitlines())
        )
        return {ref: oid for ref, oid in found.items() if ref in refs}


def inspect_source(source):
    banned = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".aws",
        ".ssh",
        ".codex",
    }
    total = 0
    count = 0
    for path in sorted(source.rglob("*")):
        inside(source, path)
        relative = path.relative_to(source)
        parts = [part.lower() for part in relative.parts]
        if any(part in banned for part in parts):
            raise PublicationError(f"Non-source path in export: {relative.as_posix()}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise PublicationError(f"Non-regular source file: {relative.as_posix()}")
        name = path.name.lower()
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            raise PublicationError(f"Environment secret file in export: {relative.as_posix()}")
        if name in {"id_rsa", "id_ed25519", "credentials", "credentials.json"}:
            raise PublicationError(f"Credential file in export: {relative.as_posix()}")
        if parts[0] in {"artifacts", "logs"} and relative.as_posix() not in {
            "artifacts/.gitkeep",
            "logs/.gitkeep",
        }:
            raise PublicationError(f"Runtime evidence in export: {relative.as_posix()}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise PublicationError(f"Oversized source file: {relative.as_posix()} ({size} bytes)")
        total += size
        count += 1
        if total > MAX_SOURCE_BYTES:
            raise PublicationError("Export exceeds the 64 MiB source limit")
    return {"files": count, "bytes": total}


def incident_text(path, case):
    text = path.read_text(encoding="utf-8-sig")
    sections = list(re.finditer(r"(?m)^###\s+문제\s+(\d{1,2})\.[^\r\n]*(?:\r?\n|$)", text))
    matching = [i for i, section in enumerate(sections) if int(section[1]) == int(case)]
    if len(matching) != 1:
        raise PublicationError(f"Expected exactly one '### 문제 {case}.' section in {path}")
    index = matching[0]
    end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
    return text[sections[index].start() : end].strip() + "\n"


def save_manifest(path, manifest):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8") as output:
        output.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def publish(
    cases,
    *,
    push=False,
    run_id=None,
    statements=None,
    root=ROOT,
    baseline=BASELINE,
    exporter=export_source,
):
    root = Path(root).resolve(strict=True)
    cases = [normalize(case) for case in cases]
    if not cases or len(cases) != len(set(cases)):
        raise PublicationError("Select at least one case without duplicates")
    run_id = run_id or (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", run_id):
        raise PublicationError("Run ID must be 1-81 letters, numbers, dots, underscores or hyphens")
    artifacts = inside(root, root / "artifacts" / "cs-labs")
    artifacts.mkdir(parents=True, exist_ok=True)
    run_dir = inside(artifacts, artifacts / f"publication-{run_id}")
    manifest_path = inside(artifacts, artifacts / f"publication-{run_id}.json")
    if run_dir.exists() or manifest_path.exists():
        raise PublicationError(f"Publication run already exists; choose another --run: {run_id}")
    run_dir.mkdir(exist_ok=False)
    with manifest_path.open("x", encoding="utf-8"):
        pass
    manifest = {
        "run": run_id,
        "root": str(root),
        "baseline": baseline,
        "started_at": datetime.now(UTC).isoformat(),
        "push_requested": push,
        "success": False,
        "status": "preparing",
        "cases": [],
        "errors": [],
    }
    repo = None
    target = None
    refs = [f"refs/heads/incident/cs-{case}" for case in cases]
    try:
        repo = Repository(root, baseline)
        manifest["baseline_tag_object"] = repo.tag_object
        target = repo.remote_target(push)
        manifest["remote"] = "origin" if target else None
        local = repo.local_refs(refs)
        remote = repo.remote_refs(target, refs) if target else {}
        manifest["preexisting_refs"] = {"local": local, "remote": remote}
        if local or remote:
            raise PublicationError(
                "Ref collision; existing branches are never replaced: "
                + json.dumps(manifest["preexisting_refs"], ensure_ascii=False)
            )
        statement_path = inside(root, root / statements) if statements else None
        for case, ref in zip(cases, refs, strict=True):
            case_dir = inside(run_dir, run_dir / case)
            case_dir.mkdir(exist_ok=False)
            source = inside(case_dir, case_dir / "source")
            index = inside(case_dir, case_dir / "index")
            row = {
                "case": case,
                "branch": ref.removeprefix("refs/heads/"),
                "commit": None,
                "parent": baseline,
                "baseline_tag_object": repo.tag_object,
                "source": str(source),
                "protected_diff": None,
                "remote": "origin" if target else None,
                "pushed": False,
                "local_ref_created": False,
            }
            manifest["cases"].append(row)
            save_manifest(manifest_path, manifest)
            export_result = exporter(case, "fault", source)
            if statement_path:
                destination = source / "INCIDENT.md"
                with destination.open("x", encoding="utf-8", newline="\n") as output:
                    output.write(incident_text(statement_path, case))
            inspect_source(source)
            row["authoring_keys_removed"] = remove_authoring_keys(source)
            row["source_inventory"] = inspect_source(source)
            metadata = json.loads((source / "lab_suite" / "active_case.json").read_text("utf-8"))
            if metadata != {"case": case, "baseline": baseline, "initial_state": "fault"}:
                raise PublicationError(f"Case {case} has incorrect active-case metadata")
            repo.run("read-tree", baseline, source=source, index=index)
            repo.run("add", "--all", "--force", "--", ".", source=source, index=index)
            tree = repo.run("write-tree", source=source, index=index).strip()
            row["tree"] = tree
            protected_diff = repo.run(
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-only",
                "-z",
                baseline,
                tree,
                "--",
                *PROTECTED,
                source=source,
                index=index,
            ).split("\0")
            row["protected_diff"] = [name for name in protected_diff if name]
            if row["protected_diff"]:
                raise PublicationError(
                    f"Case {case} changes protected paths: {row['protected_diff']}"
                )
            changed = repo.run(
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-only",
                "-z",
                baseline,
                tree,
                "--",
                source=source,
                index=index,
            ).split("\0")
            declared = set(export_result["modified_files"])
            if statement_path:
                declared.add("INCIDENT.md")
            unexpected = [
                name
                for name in changed
                if name and name not in declared and not name.startswith("lab_suite/")
            ]
            if unexpected:
                raise PublicationError(f"Case {case} has undeclared source changes: {unexpected}")
            row["modified_files"] = sorted(name for name in changed if name)
            message = f"Add isolated commerce exercise CS-{case}\n\nBaseline: {baseline}\n"
            commit = repo.run(
                "commit-tree",
                tree,
                "-p",
                baseline,
                input_text=message,
                source=source,
                index=index,
            ).strip()
            row["commit"] = commit
            parents = repo.run("show", "-s", "--format=%P", commit).strip().split()
            if parents != [baseline]:
                raise PublicationError(f"Case {case} does not have the baseline as its only parent")
            save_manifest(manifest_path, manifest)
        repo.check_baseline()
        repo.check_original()
        transaction = (
            "start\n"
            + "".join(
                f"update {ref} {row['commit']} {repo.zero}\n"
                for ref, row in zip(refs, manifest["cases"], strict=True)
            )
            + "prepare\ncommit\n"
        )
        repo.run("update-ref", "--stdin", input_text=transaction)
        for row in manifest["cases"]:
            row["local_ref_created"] = True
        manifest["status"] = "created"
        save_manifest(manifest_path, manifest)
        if push:
            remote = repo.remote_refs(target, refs)
            if remote:
                raise PublicationError(
                    f"Remote refs appeared before push; refusing to replace: {remote}"
                )
            leases = [f"--force-with-lease={ref}:" for ref in refs]
            refspecs = [
                f"{row['commit']}:{ref}" for ref, row in zip(refs, manifest["cases"], strict=True)
            ]
            push_errors = []
            try:
                repo.run("push", "--atomic", "--no-follow-tags", *leases, target, *refspecs)
            except Exception as exc:
                push_errors.append(f"Push: {type(exc).__name__}: {exc}")
            try:
                observed = repo.remote_refs(target, refs)
                for ref, row in zip(refs, manifest["cases"], strict=True):
                    row["remote_commit"] = observed.get(ref)
                    row["pushed"] = observed.get(ref) == row["commit"]
            except Exception as exc:
                push_errors.append(f"Remote verification: {type(exc).__name__}: {exc}")
            save_manifest(manifest_path, manifest)
            if push_errors:
                raise PublicationError("; ".join(push_errors))
            if not all(row["pushed"] for row in manifest["cases"]):
                raise PublicationError("Remote verification did not confirm every prepared commit")
            manifest["status"] = "pushed"
        manifest["success"] = True
    except Exception as exc:
        manifest["errors"].append(f"{type(exc).__name__}: {exc}")
        manifest["status"] = "failed"
    finally:
        if repo is not None:
            for name, check in (
                ("baseline_tag_unchanged", repo.check_baseline),
                ("original_head_and_index_unchanged", repo.check_original),
            ):
                try:
                    check()
                    manifest[name] = True
                except Exception as exc:
                    manifest[name] = False
                    manifest["errors"].append(f"{type(exc).__name__}: {exc}")
                    manifest["success"] = False
                    manifest["status"] = "failed"
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        save_manifest(manifest_path, manifest)
    return manifest_path, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create incident branches directly from the baseline"
    )
    parser.add_argument("--cases", default="all")
    parser.add_argument("--push", action="store_true")
    parser.add_argument(
        "--run", help="Unique publication run ID; existing output is never overwritten"
    )
    parser.add_argument(
        "--statements", help="Repository Markdown with '### 문제 NN.' exercise sections"
    )
    args = parser.parse_args(argv)
    try:
        cases = list(CASES) if args.cases == "all" else args.cases.split(",")
        path, result = publish(cases, push=args.push, run_id=args.run, statements=args.statements)
    except Exception as exc:
        print(f"Publication failed: {type(exc).__name__}: {exc}", flush=True)
        return 1
    print(f"Publication {result['status']}: {path}", flush=True)
    for row in result["cases"]:
        print(f"{row['branch']} commit={row['commit']} pushed={row['pushed']}", flush=True)
    for error in result["errors"]:
        print(error, flush=True)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
