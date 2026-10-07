import ast
import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from pathlib import Path

import pytest

from app.services.shipping import ShippingQuoteService
from lab_suite import round2_b_runtime as runtime
from lab_suite.cases.advanced_round2_b import (
    _optional_event,
    _release,
    _wait,
    judge,
    replacements,
)

SETTINGS = {
    "base_fee": 3000,
    "remote_area_fee": 2500,
    "extra_packaging_fee": 700,
    "free_shipping_threshold": 200000,
}


def implementation(case_id, phase):
    if phase != "fault":
        return runtime.QuoteWorkers
    source = Path(runtime.__file__).read_text(encoding="utf-8-sig")
    for path, before, after in replacements(case_id):
        assert path == "lab_suite/round2_b_runtime.py"
        assert source.count(before) == 1
        source = source.replace(before, after, 1)
    tree = ast.parse(source)
    declaration = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "QuoteWorkers"
    )
    method_name = "_recover" if case_id == "18" else "_retire"
    method = next(
        node
        for node in declaration.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    namespace = dict(vars(runtime))
    exec(compile(ast.Module(body=[method], type_ignores=[]), "injected-runtime", "exec"), namespace)
    return type("InjectedWorkers", (runtime.QuoteWorkers,), {method_name: namespace[method_name]})


def quote(workers, token, quantity=1, postal_code="16841", amount=12000, settings=None):
    return workers.quote(
        SETTINGS if settings is None else settings,
        token=token,
        postal_code=postal_code,
        quantity=quantity,
        merchandise_amount=amount,
    )


def outcome(future):
    try:
        return {"value": future.result(timeout=12)}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}


@pytest.mark.parametrize("phase", ["healthy", "fault", "restored"])
def test_real_worker_failure_does_not_retire_a_successor(tmp_path, phase):
    workers = implementation("18", phase)(tmp_path)
    first, second = "test-recovery-first", "test-recovery-second"
    gates = [(first, "compute_0"), (second, "compute_0"), (second, "failed_0"), (first, "submit_1")]
    runtime.write_json(
        tmp_path / "plan.json",
        {
            "run_id": "test-recovery",
            "gates": [f"{token}:{point}" for token, point in gates],
        },
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            futures = [
                callers.submit(quote, workers, first),
                callers.submit(quote, workers, second, 3),
            ]
            first_worker = _wait(runtime.event_path(tmp_path, first, "compute_0"), timeout=12)
            _wait(runtime.event_path(tmp_path, second, "compute_0"), timeout=12)
            processes = dict(workers.current.executor._processes)
            owned = next(
                process for process in processes.values() if process.pid == first_worker["pid"]
            )
            owned.terminate()
            owned.join(timeout=3)
            assert not owned.is_alive() and owned.exitcode != 0
            _wait(runtime.event_path(tmp_path, first, "submit_1"), timeout=12)
            _wait(runtime.event_path(tmp_path, second, "failed_0"), timeout=12)
            _release(tmp_path, first, "compute_0")
            _release(tmp_path, second, "compute_0")
            _release(tmp_path, second, "failed_0")
            _wait(runtime.event_path(tmp_path, second, "recovered_0"), timeout=12)
            _release(tmp_path, first, "submit_1")
            results = [outcome(future) for future in futures]
        if phase == "fault":
            assert results[0]["error"] == "RuntimeError"
            assert "shutdown" in results[0]["message"]
            assert results[1] == {"value": 3700}
        else:
            assert results == [{"value": 3000}, {"value": 3700}]
        assert quote(workers, "after-recovery", 4, "63001", 300000) == 3900
    finally:
        for token, point in gates:
            _release(tmp_path, token, point)
        workers.close()


@pytest.mark.parametrize("phase", ["healthy", "fault", "restored"])
def test_real_executor_drain_and_completion_progress(tmp_path, phase):
    workers = implementation("19", phase)(tmp_path, callback_timeout=0.5)
    first, second, rotation = "test-drain-first", "test-drain-second", "test-drain-rotation"
    runtime.write_json(
        tmp_path / "plan.json",
        {
            "run_id": "test-drain",
            "gates": [f"{first}:compute_0"],
        },
    )
    try:
        with ThreadPoolExecutor(max_workers=3) as callers:
            first_future = callers.submit(quote, workers, first)
            _wait(runtime.event_path(tmp_path, first, "compute_0"), timeout=12)
            retirement = callers.submit(workers.rotate, rotation)
            _wait(runtime.event_path(tmp_path, rotation, "drain_entered"), timeout=12)
            second_future = callers.submit(quote, workers, second, 3)
            _release(tmp_path, first, "compute_0")
            results = [outcome(first_future), outcome(second_future)]
            assert retirement.result(timeout=12)["current"] == 2
        if phase == "fault":
            assert results[0]["error"] == "RuntimeError"
            assert "completion" in results[0]["message"]
            assert _optional_event(tmp_path, first, "callback_timeout_0")
        else:
            assert results[0] == {"value": 3000}
        assert results[1] == {"value": 3700}
        assert quote(workers, "ordinary-after", 3) == 3700
    finally:
        _release(tmp_path, first, "compute_0")
        workers.close()


def test_real_worker_exception_is_delivered_and_does_not_poison_later_work(tmp_path):
    workers = runtime.QuoteWorkers(tmp_path)
    try:
        with pytest.raises(TypeError, match="unexpected_keyword"):
            quote(workers, "invalid-provider-call", settings={"unexpected_keyword": 1})
        assert quote(workers, "following-customer", 5, "63001", 240000) == 4600
        assert not workers.pending
    finally:
        workers.close()


def test_real_pending_work_cancellation_is_published(tmp_path):
    workers = runtime.QuoteWorkers(tmp_path)
    tokens = ["cancel-first", "cancel-second"]
    runtime.write_json(
        tmp_path / "plan.json",
        {
            "run_id": "cancel",
            "gates": [f"{token}:compute_0" for token in tokens],
        },
    )
    executor = workers.current.executor
    futures = []
    try:
        for token in tokens:
            futures.append(
                executor.submit(
                    runtime.calculate_quote, str(tmp_path), token, 0, SETTINGS, "16841", 1, 12000
                )
            )
        for token in tokens:
            _wait(runtime.event_path(tmp_path, token, "compute_0"), timeout=12)
        cancelled = None
        for index in range(8):
            future = executor.submit(
                runtime.calculate_quote,
                str(tmp_path),
                f"cancel-queued-{index}",
                0,
                SETTINGS,
                "16841",
                1,
                12000,
            )
            futures.append(future)
            if future.cancel():
                cancelled = future
                break
        assert cancelled is not None
        external = Future()
        workers._complete(cancelled, external, workers.current, "cancel-completion", 0)
        assert external.cancelled()
        with pytest.raises(CancelledError):
            external.result()
        for token in tokens:
            _release(tmp_path, token, "compute_0")
        assert futures[0].result(timeout=12) == futures[1].result(timeout=12) == 3000
        assert quote(workers, "after-cancel") == 3000
    finally:
        for token in tokens:
            _release(tmp_path, token, "compute_0")
        workers.close()


def test_quote_contract_matches_reference_across_worker_generations(tmp_path):
    workers = runtime.QuoteWorkers(tmp_path)
    original = ShippingQuoteService()
    specs = [("16841", 1, 12000), ("63001", 3, 36000), ("A-12", 20, 240000), ("63001", 4, 400000)]
    try:
        for generation in range(2):
            for index, (postal, quantity, amount) in enumerate(specs):
                assert quote(
                    workers, f"pricing-{generation}-{index}", quantity, postal, amount
                ) == (
                    original.quote(postal_code=postal, quantity=quantity, merchandise_amount=amount)
                )
            workers.rotate(f"routine-{generation}")
        assert not workers.pending
    finally:
        workers.close()


def test_retirement_preserves_reserved_submission(tmp_path):
    workers = runtime.QuoteWorkers(tmp_path)
    token = "reservation-customer"
    runtime.write_json(
        tmp_path / "plan.json",
        {
            "run_id": "reservation",
            "gates": [f"{token}:submit_0"],
        },
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            customer = callers.submit(quote, workers, token)
            _wait(runtime.event_path(tmp_path, token, "submit_0"), timeout=12)
            retirement = callers.submit(workers.rotate, "reservation-rotation")
            _wait(runtime.event_path(tmp_path, "reservation-rotation", "rotation_published"))
            assert not retirement.done()
            _release(tmp_path, token, "submit_0")
            assert customer.result(timeout=12) == 3000
            retirement.result(timeout=12)
    finally:
        _release(tmp_path, token, "submit_0")
        workers.close()


@pytest.mark.parametrize("case_id", ["18", "19"])
def test_outcome_judge_accepts_alternative_recovery_without_internal_layout(case_id):
    groups = {
        name: [{"status": 201}, {"status": 201}]
        for name in ("before", "after", "operation-1", "operation-2")
    }
    groups["soldout"] = [{"status": 409}]
    result = judge(case_id, {"database_truth": True, "public_contract": True}, groups, [{}, {}])
    assert result == {"healthy": True, "symptom": False}
    groups["operation-1"][0]["status"] = 500
    assert not judge(case_id, {"database_truth": True}, groups, [{}, {}])["healthy"]
    assert not judge(case_id, {"database_truth": False}, groups, [{}, {}])["symptom"]


def test_callback_timeout_is_actual_registry_contention(tmp_path):
    workers = runtime.QuoteWorkers(tmp_path, callback_timeout=0.05)
    entered = threading.Event()
    release = threading.Event()

    def hold():
        with workers.registry:
            entered.set()
            assert release.wait(timeout=3)

    holder = threading.Thread(target=hold)
    holder.start()
    assert entered.wait(3)
    internal, external = Future(), Future()
    internal.set_result(3000)
    try:
        workers._complete(internal, external, workers.current, "bounded-callback", 0)
        with pytest.raises(RuntimeError, match="completion"):
            external.result()
    finally:
        release.set()
        holder.join(timeout=3)
        workers.close()
