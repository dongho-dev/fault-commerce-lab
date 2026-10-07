import copy
import json
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lab_suite import __main__ as teacher
from lab_suite import operator


class FakeSocket:
    def __init__(self, occupied):
        self.occupied = occupied

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def setsockopt(self, *args):
        pass

    def bind(self, address):
        if address[1] in self.occupied:
            raise OSError("Address already in use")


class DockerModel:
    def __init__(self, root, unexpected):
        self.root = root
        self.unexpected = unexpected
        self.calls = []
        self.mutations = []
        self.containers = []
        self.networks = []
        self.databases = {
            "fcl-exercise-01": {"order": "learner's existing order"},
            "fcl-exercise-02": {"order": "another exercise"},
            "shop1": {"order": "original checkout's order"},
            "fcl-cs-01-fault": {"order": "teacher's order"},
        }
        self.builds = []
        self.probe_override = {}
        self.probe_exit = 0
        self.write_probe = True
        self.probe_text = None
        self.oom_events = []

    def container(self, project, case="01", source=None, port=None):
        labels = {
            "com.docker.compose.project": project,
            operator.ROOT_LABEL: (source or self.root).as_posix(),
            operator.CASE_LABEL: case,
        }
        resource = {
            "Id": f"container-{len(self.containers)}",
            "Name": f"/{project}-app",
            "Config": {"Labels": labels},
            "NetworkSettings": {"Ports": {"8000/tcp": [{"HostPort": str(port)}]} if port else {}},
        }
        self.containers.append(resource)
        return resource

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["docker", "ps"]:
            query = args[args.index("--filter") + 1]
            if query.startswith("label=com.docker.compose.project="):
                project = query.split("=", 2)[2]
                found = [
                    item
                    for item in self.containers
                    if item["Config"]["Labels"]["com.docker.compose.project"] == project
                ]
            else:
                port = query.removeprefix("publish=")
                found = [
                    item
                    for item in self.containers
                    if any(
                        binding["HostPort"] == port
                        for values in item["NetworkSettings"]["Ports"].values()
                        for binding in values
                    )
                ]
            return "\n".join(item["Id"] for item in found).encode()
        if args[:3] == ["docker", "network", "ls"]:
            project = args[-1].split("=", 2)[2]
            return "\n".join(
                item["Id"]
                for item in self.networks
                if item["Labels"]["com.docker.compose.project"] == project
            ).encode()
        if args[:3] == ["docker", "network", "inspect"]:
            return json.dumps([item for item in self.networks if item["Id"] in args[3:]]).encode()
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([item for item in self.containers if item["Id"] in args[2:]]).encode()
        if args[:3] == ["docker", "image", "inspect"]:
            return b"sha256:current-checkout-image\n"
        if args[:2] == ["docker", "events"]:
            return "\n".join(json.dumps(event) for event in self.oom_events).encode()
        if args[:2] != ["docker", "compose"]:
            self.unexpected.append(args)
            raise AssertionError(f"Unmocked external command: {args}")
        project = args[args.index("-p") + 1]
        config_path = Path(args[args.index("-f") + 1])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        action = args[args.index("-f") + 2]
        if action == "build":
            self.mutations.append((project, action))
            source = Path(config["services"]["app"]["build"]["context"])
            self.builds.append((source, (source / "app" / "service.py").read_bytes()))
        elif action == "up":
            self.mutations.append((project, action))
            self.databases.setdefault(project, {})
            if not any(item["Name"] == f"/{project}-app" for item in self.containers):
                case = config["services"]["app"]["environment"]["LAB_CASE"]
                self.container(project, case, port=18100 + int(case))
                self.networks.append(
                    {
                        "Id": f"network-{project}",
                        "Labels": {
                            **config["networks"]["default"]["labels"],
                            "com.docker.compose.project": project,
                        },
                    }
                )
        elif action == "down":
            self.mutations.append((project, action))
            self.databases.pop(project, None)
            self.containers = [
                item
                for item in self.containers
                if item["Config"]["Labels"]["com.docker.compose.project"] != project
            ]
            self.networks = [
                item
                for item in self.networks
                if item["Labels"]["com.docker.compose.project"] != project
            ]
        elif action == "ps":
            return next(
                item["Id"].encode() for item in self.containers if item["Name"] == f"/{project}-app"
            )
        elif action == "run":
            self.mutations.append((project, "probe"))
            evidence = Path(
                next(
                    mount["source"]
                    for mount in config["services"]["probe"]["volumes"]
                    if mount["target"] == "/evidence"
                )
            )
            report = {
                "case": args[args.index("--case") + 1],
                "expected": args[args.index("--expect") + 1],
                "passed": True,
                **self.probe_override,
            }
            if self.write_probe:
                payload = self.probe_text if self.probe_text is not None else json.dumps(report)
                (evidence / "probe.json").write_text(payload, encoding="utf-8")
            return subprocess.CompletedProcess(args, self.probe_exit)
        elif action != "logs":
            self.unexpected.append(args)
            raise AssertionError(f"Unmocked compose action: {action}")
        return subprocess.CompletedProcess(args, 0)


@pytest.fixture
def exercise(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "app").mkdir(parents=True)
    (root / "lab_suite").mkdir()
    (root / "oracle").mkdir()
    (root / "app" / "service.py").write_text("learner_change = 'keep me'\n", encoding="utf-8")
    (root / "oracle" / "check.py").write_text("protected = True\n", encoding="utf-8")
    (root / ".dockerignore").write_text(".git\nartifacts\n", encoding="utf-8")
    active_case = root / "lab_suite" / "active_case.json"
    active_case.write_text('{"case": "01"}', encoding="utf-8")
    unexpected = []
    occupied = set()
    docker = DockerModel(root, unexpected)

    def forbidden(*args, **kwargs):
        unexpected.append(args)
        raise AssertionError(
            "Real Docker, Git, subprocess, network or source injection is forbidden"
        )

    def runtime_sample(container, evidence, stop):
        operator.write_json(evidence / "runtime.json", {"running": True, "restart_count": 0})

    monkeypatch.setattr(operator, "ROOT", root)
    monkeypatch.setattr(operator, "ARTIFACTS", root / "artifacts" / "cs-labs")
    monkeypatch.setattr(operator, "command", docker.command)
    monkeypatch.setattr(operator, "sampler", runtime_sample)
    monkeypatch.setattr(operator, "export_source", forbidden, raising=False)
    monkeypatch.setattr(teacher, "command", forbidden)
    monkeypatch.setattr(teacher, "export_source", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: FakeSocket(occupied))
    monkeypatch.setattr(socket, "create_connection", forbidden)

    def result(case="01"):
        active = json.loads(
            (root / "artifacts" / "cs-labs" / f"learner-{case}" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        return json.loads((Path(active["evidence"]) / "result.json").read_text(encoding="utf-8"))

    yield SimpleNamespace(
        root=root, active_case=active_case, docker=docker, occupied=occupied, result=result
    )
    assert unexpected == [], f"An unmocked external effect was attempted: {unexpected}"


def test_recheck_builds_learners_current_changes_without_reinjecting(exercise):
    source = exercise.root / "app" / "service.py"
    oracle = exercise.root / "oracle" / "check.py"
    protected = oracle.read_bytes()
    initial = source.read_bytes()
    assert operator.main(["up"]) == 0
    edited = b"learner_change = 'fixed after first startup'\n"
    source.write_bytes(edited)
    assert operator.main(["check", "--expect", "healthy"]) == 0
    assert exercise.docker.builds == [(exercise.root, initial), (exercise.root, edited)]
    assert source.read_bytes() == edited
    assert oracle.read_bytes() == protected
    assert exercise.result()["passed"] is True
    config = json.loads((operator.workspace("01") / "compose.json").read_text(encoding="utf-8"))
    source_mount = next(
        mount for mount in config["services"]["probe"]["volumes"] if mount["target"] == "/workspace"
    )
    assert Path(source_mount["source"]) == exercise.root
    assert source_mount["read_only"] is True


@pytest.mark.parametrize(
    "arguments,fresh",
    [
        (["up"], False),
        (["check", "--expect", "healthy"], False),
        (["check", "--expect", "healthy", "--fresh"], True),
    ],
)
def test_database_is_preserved_unless_fresh_is_explicit(exercise, arguments, fresh):
    before = copy.deepcopy(exercise.docker.databases)
    assert operator.main(arguments) == 0
    databases = exercise.docker.databases
    assert databases["fcl-exercise-01"] == ({} if fresh else before["fcl-exercise-01"])
    for project in before.keys() - {"fcl-exercise-01"}:
        assert databases[project] == before[project]
    removals = [project for project, action in exercise.docker.mutations if action == "down"]
    assert removals == (["fcl-exercise-01"] if fresh else [])


@pytest.mark.parametrize("kind", ["container", "network"])
@pytest.mark.parametrize("wrong_label", ["root", "case"])
@pytest.mark.parametrize("arguments", [["down"], ["check", "--expect", "healthy", "--fresh"]])
def test_refuses_to_stop_resources_owned_by_another_checkout(
    exercise, capsys, kind, wrong_label, arguments
):
    resource = exercise.docker.container("fcl-exercise-01")
    labels = resource["Config"]["Labels"]
    if wrong_label == "root":
        labels[operator.ROOT_LABEL] = (exercise.root.parent / "other-checkout").as_posix()
    else:
        labels[operator.CASE_LABEL] = "02"
    if kind == "network":
        exercise.docker.containers.clear()
        exercise.docker.networks.append({"Id": "foreign-network", "Labels": labels})
    before = copy.deepcopy(exercise.docker.databases)
    assert operator.main(arguments) == 1
    assert "another checkout" in capsys.readouterr().err
    assert exercise.docker.mutations == []
    assert exercise.docker.databases == before


@pytest.mark.parametrize("field", ["case", "project", "source", "config", "evidence"])
def test_tampered_active_record_cannot_target_other_environment(exercise, field):
    operator.prepare("01", "up")
    path = operator.workspace("01") / "active.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    replacements = {
        "case": "02",
        "project": "shop1",
        "source": str(exercise.root.parent / "original-checkout"),
        "config": str(exercise.root.parent / "original-compose.json"),
        "evidence": str(exercise.root.parent / "outside-evidence"),
    }
    record[field] = replacements[field]
    path.write_text(json.dumps(record), encoding="utf-8")
    before = copy.deepcopy(exercise.docker.databases)
    assert operator.main(["down"]) == 1
    assert exercise.docker.calls == []
    assert exercise.docker.databases == before


@pytest.mark.parametrize(
    "case,port",
    [
        ("01", 18101),
        ("05", 18105),
        ("05", 19105),
        ("06", 18106),
        ("06", 19106),
        ("06", 20106),
        ("12", 18112),
        ("12", 20112),
        ("13", 18113),
        ("13", 19113),
    ],
)
@pytest.mark.parametrize("owner", ["shop1", "fcl-cs-01-fault"])
def test_port_collision_does_not_stop_the_existing_environment(exercise, capsys, case, port, owner):
    exercise.active_case.write_text(json.dumps({"case": case}), encoding="utf-8")
    exercise.docker.container(owner, port=port)
    before = copy.deepcopy(exercise.docker.databases)
    assert operator.main(["check", "--expect", "healthy", "--fresh"]) == 1
    message = capsys.readouterr().err
    assert str(port) in message and owner in message
    assert exercise.docker.mutations == []
    assert exercise.docker.databases == before


def test_non_docker_port_conflict_stops_before_build_or_cleanup(exercise, capsys):
    exercise.occupied.add(18101)
    assert operator.main(["check", "--expect", "healthy", "--fresh"]) == 1
    assert "Port 18101 is unavailable" in capsys.readouterr().err
    assert exercise.docker.mutations == []


@pytest.mark.parametrize(
    "exit_code,override,write_probe,raw,expected",
    [
        (0, {}, True, None, True),
        (2, {}, True, None, False),
        (0, {"passed": False}, True, None, False),
        (0, {"passed": 1}, True, None, False),
        (0, {"case": "02"}, True, None, False),
        (0, {"expected": "fault"}, True, None, False),
        (0, {}, False, None, False),
        (0, {}, True, "[]", False),
        (0, {}, True, "invalid JSON", False),
    ],
)
def test_exit_status_and_saved_result_require_matching_success_evidence(
    exercise, exit_code, override, write_probe, raw, expected
):
    exercise.docker.probe_exit = exit_code
    exercise.docker.probe_override = override
    exercise.docker.write_probe = write_probe
    exercise.docker.probe_text = raw
    code = operator.main(["check", "--expect", "healthy"])
    result = exercise.result()
    assert code == (0 if expected else 1)
    assert result["passed"] is expected
    assert result["expected"] == "healthy"
    assert result["case"] == "01"


@pytest.mark.parametrize("has_oom", [False, True])
def test_case04_fault_requires_docker_oom_event_even_when_probe_passes(exercise, has_oom):
    exercise.active_case.write_text('{"case": "04"}', encoding="utf-8")
    exercise.docker.oom_events = [{"Action": "oom"}] if has_oom else []
    code = operator.main(["check", "--expect", "fault"])
    result = exercise.result("04")
    assert result["probe"]["passed"] is True
    assert code == (0 if has_oom else 1)
    assert result["passed"] is has_oom
    assert bool(result["oom_events"]) is has_oom
    evidence = Path(result["evidence"])
    assert (evidence / "runtime.json").exists()
    assert json.loads((evidence / "oom-events.json").read_text()) == exercise.docker.oom_events


@pytest.mark.parametrize(
    "arguments",
    [
        ["up", "--case", "02"],
        ["check", "--case", "02", "--expect", "healthy", "--fresh"],
    ],
)
def test_case_mismatch_fails_before_any_environment_change(exercise, capsys, arguments):
    assert operator.main(arguments) == 1
    assert "This checkout is case 01" in capsys.readouterr().err
    assert exercise.docker.calls == []


def test_explicit_down_can_stop_previous_case_after_branch_switch(exercise):
    exercise.active_case.write_text('{"case": "02"}', encoding="utf-8")
    exercise.docker.container("fcl-exercise-01", case="01")
    before = copy.deepcopy(exercise.docker.databases)
    assert operator.main(["down", "--case", "01"]) == 0
    assert "fcl-exercise-01" not in exercise.docker.databases
    for project in before.keys() - {"fcl-exercise-01"}:
        assert exercise.docker.databases[project] == before[project]
    assert exercise.docker.mutations == [("fcl-exercise-01", "down")]


def test_teacher_checkout_requires_explicit_case(exercise, capsys):
    exercise.active_case.unlink()
    assert operator.main(["up"]) == 1
    assert "supply --case NN" in capsys.readouterr().err
    assert exercise.docker.calls == []
    assert operator.main(["up", "--case", "1"]) == 0


@pytest.mark.parametrize("lifecycle_passed", [True, False])
def test_lifecycle_verdict_is_required_even_when_probe_passes(
    exercise, monkeypatch, lifecycle_passed
):
    token = object()
    monkeypatch.setattr(operator, "start_controller", lambda *args: token)
    monkeypatch.setattr(
        operator,
        "finish_controller",
        lambda value: {"passed": lifecycle_passed} if value is token else None,
    )
    code = operator.main(["check", "--expect", "healthy"])
    result = exercise.result()
    assert result["probe"]["passed"] is True
    assert result["passed"] is lifecycle_passed
    assert code == (0 if lifecycle_passed else 1)
