import json
import threading
from types import SimpleNamespace

import pytest

from lab_suite.controller import finish_controller, start_controller


def test_absent_lifecycle_has_no_side_effect(tmp_path):
    calls = []
    controller = start_controller(
        SimpleNamespace(), "11", ["docker", "compose"], tmp_path, lambda *a, **k: calls.append(a)
    )
    assert controller is None
    assert finish_controller(controller) is None
    assert calls == []
    assert not (tmp_path / "controller.json").exists()


def test_lifecycle_uses_bounded_commands_and_saves_evidence(tmp_path):
    finished = threading.Event()
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))

    def control(case, compose, evidence, stop, command):
        assert case == "13"
        assert compose == ["docker", "compose", "-p", "owned"]
        assert evidence == tmp_path
        command([*compose, "ps"], timeout=900)
        finished.set()
        return {"passed": True, "replacement_observed": True}

    controller = start_controller(
        SimpleNamespace(control=control), "13", ["docker", "compose", "-p", "owned"], tmp_path, run
    )
    assert finished.wait(5)
    result = finish_controller(controller)
    assert result == {"passed": True, "replacement_observed": True}
    assert calls[0][1]["timeout"] == 45
    assert json.loads((tmp_path / "controller.json").read_text()) == result
    assert finish_controller(controller) == result
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["exception", "missing_verdict", "unserializable"])
def test_controller_failure_cannot_be_success(tmp_path, failure):
    def control(*args):
        if failure == "exception":
            raise ValueError("replacement failed")
        if failure == "missing_verdict":
            return None
        return {"passed": True, "invalid": object()}

    controller = start_controller(
        SimpleNamespace(control=control), "13", [], tmp_path, lambda *a, **k: None
    )
    result = finish_controller(controller)
    assert result["passed"] is False
    assert result["error"]
    assert json.loads((tmp_path / "controller.json").read_text())["passed"] is False
    assert not controller.thread.is_alive()


def test_cancellation_prevents_late_mutation(tmp_path):
    waiting = threading.Event()
    calls = []

    def control(case, compose, evidence, stop, command):
        waiting.set()
        assert stop.wait(5)
        command(["docker", "compose", "up"])
        return {"passed": True}

    controller = start_controller(
        SimpleNamespace(control=control), "13", [], tmp_path, lambda *a, **k: calls.append(a)
    )
    assert waiting.wait(5)
    result = finish_controller(controller)
    assert result["passed"] is False
    assert "cancelled" in result["error"]
    assert calls == []
    assert not controller.thread.is_alive()
