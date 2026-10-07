import atexit
import hashlib
import json
import multiprocessing
import os
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass
from pathlib import Path


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def event_path(root, token, point):
    key = hashlib.sha256(token.encode()).hexdigest()[:24]
    return Path(root) / f"{key}-{point}.json"


class Coordination:
    def __init__(self, root):
        self.root = Path(root)

    def reach(self, point, token, **values):
        plan = read_json(self.root / "plan.json")
        if not plan or not token.startswith(plan["run_id"]):
            return
        path = event_path(self.root, token, point)
        write_json(
            path,
            {
                "token": token,
                "point": point,
                "pid": os.getpid(),
                "parent_pid": os.getppid(),
                "time": time.time(),
                **values,
            },
        )
        if f"{token}:{point}" not in plan.get("gates", []):
            return
        deadline = time.monotonic() + 20
        release = path.with_suffix(".release")
        while not release.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Operation coordination expired at {point}")
            threading.Event().wait(0.02)


def calculate_quote(root, token, attempt, settings, postal_code, quantity, merchandise_amount):
    from app.services.shipping import ShippingQuoteService

    ticks = None
    try:
        ticks = Path("/proc/self/stat").read_text().split(")", 1)[1].split()[19]
    except OSError:
        pass
    Coordination(root).reach(
        f"compute_{attempt}",
        token,
        worker_start_ticks=ticks,
    )
    return ShippingQuoteService(**settings).quote(
        postal_code=postal_code,
        quantity=quantity,
        merchandise_amount=merchandise_amount,
    )


@dataclass(eq=False)
class Generation:
    number: int
    executor: object
    borrowers: int = 0


class QuoteWorkers:
    def __init__(self, root, executor_factory=None, callback_timeout=3.0):
        self.coordination = Coordination(root)
        self.registry = threading.RLock()
        self.reservations = threading.Condition(self.registry)
        self.factory = executor_factory or self._executor
        self.callback_timeout = callback_timeout
        self.sequence = 0
        self.current = self._new_generation()
        self.generations = [self.current]
        self.pending = {}
        self.stop = threading.Event()
        self.controller = None

    @staticmethod
    def _executor():
        return ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn"))

    def _new_generation(self):
        self.sequence += 1
        return Generation(self.sequence, self.factory())

    def _borrow(self):
        with self.registry:
            generation = self.current
            generation.borrowers += 1
            return generation

    def _release(self, generation):
        with self.reservations:
            generation.borrowers -= 1
            self.reservations.notify_all()

    def _complete(self, internal, external, generation, token, attempt):
        self.coordination.reach(
            f"callback_{attempt}",
            token,
            generation=generation.number,
        )
        if not self.registry.acquire(timeout=self.callback_timeout):
            self.coordination.reach(
                f"callback_timeout_{attempt}",
                token,
                generation=generation.number,
            )
            external.set_exception(RuntimeError("Quote completion could not acquire registry"))
            return
        try:
            self.pending.pop(id(external), None)
            if internal.cancelled():
                external.cancel()
            else:
                try:
                    value = internal.result()
                except BaseException as exc:
                    external.set_exception(exc)
                else:
                    external.set_result(value)
        finally:
            self.registry.release()

    def _recover(self, failed):
        with self.registry:
            if self.current is not failed:
                return
            retired = self.current
            self.current = self._new_generation()
            self.generations.append(self.current)
        retired.executor.shutdown(wait=False, cancel_futures=True)

    def quote(self, settings, *, token, postal_code, quantity, merchandise_amount):
        for attempt in range(3):
            generation = self._borrow()
            try:
                self.coordination.reach(
                    f"submit_{attempt}",
                    token,
                    generation=generation.number,
                )
                external = Future()
                with self.registry:
                    internal = generation.executor.submit(
                        calculate_quote,
                        str(self.coordination.root),
                        token,
                        attempt,
                        settings,
                        postal_code,
                        quantity,
                        merchandise_amount,
                    )
                    self.pending[id(external)] = generation.number
            except BrokenProcessPool:
                self._release(generation)
                self.coordination.reach(
                    f"failed_{attempt}",
                    token,
                    generation=generation.number,
                )
                self._recover(generation)
                self.coordination.reach(f"recovered_{attempt}", token)
                continue
            except BaseException:
                self._release(generation)
                raise
            self._release(generation)
            internal.add_done_callback(
                lambda future, target=external, owner=generation, number=attempt: self._complete(
                    future, target, owner, token, number
                )
            )
            try:
                return external.result(timeout=30)
            except BrokenProcessPool:
                self.coordination.reach(
                    f"failed_{attempt}",
                    token,
                    generation=generation.number,
                )
                self._recover(generation)
                self.coordination.reach(f"recovered_{attempt}", token)
        raise RuntimeError("Quote worker recovery budget exhausted")

    def _drain_wait(self, old, token):
        with self.reservations:
            deadline = time.monotonic() + 20
            while old.borrowers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Generation reservations did not drain")
                self.reservations.wait(timeout=remaining)
        self.coordination.reach("drain_entered", token, generation=old.number)
        old.executor.shutdown(wait=True, cancel_futures=False)
        with self.registry:
            self.pending = {
                key: value for key, value in self.pending.items() if value != old.number
            }
        self.coordination.reach("drain_completed", token, generation=old.number)

    def _retire(self, old, token):
        self._drain_wait(old, token)

    def rotate(self, token):
        with self.registry:
            old = self.current
            self.current = self._new_generation()
            self.generations.append(self.current)
        self.coordination.reach("rotation_published", token, generation=self.current.number)
        self._retire(old, token)
        return {"retired": old.number, "current": self.current.number}

    def start_controller(self):
        def run():
            completed = set()
            request_path = self.coordination.root / "rotation-request.json"
            response_path = self.coordination.root / "rotation-response.json"
            while not self.stop.wait(0.025):
                request = read_json(request_path)
                if not request or request.get("token") in completed:
                    continue
                token = request["token"]
                plan = read_json(self.coordination.root / "plan.json")
                if not plan or not token.startswith(plan["run_id"]):
                    continue
                try:
                    result = {"token": token, "passed": True, **self.rotate(token)}
                except Exception as exc:
                    result = {"token": token, "passed": False, "error": str(exc)}
                completed.add(token)
                write_json(response_path, result)

        self.controller = threading.Thread(target=run, name="quote-worker-retirement", daemon=True)
        self.controller.start()

    def close(self):
        self.stop.set()
        if self.controller is not None:
            self.controller.join(timeout=25)
        for generation in self.generations:
            generation.executor.shutdown(wait=True, cancel_futures=True)


def install(root):
    from app.observability.context import get_request_id
    from app.services.shipping import ShippingQuoteService

    if getattr(ShippingQuoteService.quote, "_round2_workers", None) is not None:
        return
    workers = QuoteWorkers(root)

    def quote(service, *, postal_code, quantity, merchandise_amount):
        return workers.quote(
            asdict(service),
            token=get_request_id() or "ordinary-request",
            postal_code=postal_code,
            quantity=quantity,
            merchandise_amount=merchandise_amount,
        )

    quote._round2_workers = workers
    ShippingQuoteService.quote = quote
    workers.start_controller()
    atexit.register(workers.close)
