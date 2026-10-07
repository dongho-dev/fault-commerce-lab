import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Controller:
    stop: threading.Event
    evidence: Path
    thread: threading.Thread | None = None
    result: dict[str, Any] | None = None
    settled: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


def start_controller(module, case, compose, evidence, run_command):
    control = getattr(module, "control", None)
    if control is None:
        return None
    if not callable(control):
        raise TypeError(f"Case {case} lifecycle control is not callable")
    controller = Controller(stop=threading.Event(), evidence=Path(evidence))

    def bounded_command(arguments, **kwargs):
        if controller.stop.is_set():
            raise RuntimeError("Lifecycle operation cancelled")
        kwargs["timeout"] = min(45, max(0.1, float(kwargs.get("timeout", 45))))
        return run_command(arguments, **kwargs)

    def execute():
        try:
            result = control(
                case, list(compose), controller.evidence, controller.stop, bounded_command
            )
            if not isinstance(result, dict) or not isinstance(result.get("passed"), bool):
                raise TypeError("Lifecycle control must return an explicit passed boolean")
        except Exception as exc:
            result = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
        with controller.lock:
            controller.result = result

    controller.thread = threading.Thread(
        target=execute, name=f"case-{case}-lifecycle", daemon=True
    )
    controller.thread.start()
    return controller


def finish_controller(controller):
    if controller is None:
        return None
    if controller.settled:
        return controller.result
    controller.stop.set()
    if controller.thread is not None:
        controller.thread.join(timeout=50)
    with controller.lock:
        result = controller.result
        if controller.thread is not None and controller.thread.is_alive():
            result = {"passed": False, "error": "Lifecycle controller did not stop"}
        elif result is None:
            result = {"passed": False, "error": "Lifecycle controller produced no evidence"}
        try:
            content = json.dumps(result, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            result = {"passed": False, "error": f"Invalid lifecycle evidence: {exc}"}
            content = json.dumps(result, ensure_ascii=False, indent=2)
        controller.result = result
        controller.settled = True
    (controller.evidence / "controller.json").write_text(content, encoding="utf-8")
    return result
