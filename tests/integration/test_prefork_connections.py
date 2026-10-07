import multiprocessing
import os
import sys
from multiprocessing.connection import Connection
from multiprocessing.synchronize import Event

import pytest
from sqlalchemy import Engine, create_engine, text

from app.config import get_settings
from lab_suite.advanced_server import initialize_worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="The prefork server requires Linux"),
]


def _worker_connection(engine: Engine, result: Connection, release: Event) -> None:
    try:
        initialize_worker(engine)
        with engine.connect() as connection:
            driver = connection.connection.driver_connection
            result.send({"worker_pid": os.getpid(), "backend_pid": driver.info.backend_pid})
            if not release.wait(timeout=15):
                raise TimeoutError("The parent did not release the worker connection")
    except Exception as error:
        result.send({"error": f"{type(error).__name__}: {error}"})
        raise
    finally:
        result.close()


def test_forked_workers_use_distinct_connections_and_preserve_parent() -> None:
    engine = create_engine(
        get_settings().database_url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=False,
    )
    context = multiprocessing.get_context("fork")
    release = context.Event()
    processes = []
    receivers = []
    try:
        with engine.connect() as connection:
            parent_backend = connection.scalar(text("SELECT pg_backend_pid()"))
        for _ in range(2):
            receiver, sender = context.Pipe(duplex=False)
            worker = context.Process(target=_worker_connection, args=(engine, sender, release))
            worker.start()
            sender.close()
            processes.append(worker)
            receivers.append(receiver)
        observations = []
        for receiver in receivers:
            assert receiver.poll(10), "The worker did not report its connection"
            observation = receiver.recv()
            assert "error" not in observation, observation
            observations.append(observation)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT pg_backend_pid()")) == parent_backend
        assert len({row["worker_pid"] for row in observations}) == 2
        child_backends = {row["backend_pid"] for row in observations}
        assert parent_backend not in child_backends, observations
        assert len(child_backends) == 2, observations
    finally:
        release.set()
        for worker in processes:
            worker.join(timeout=10)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        for receiver in receivers:
            receiver.close()
        engine.dispose()
    assert all(worker.exitcode == 0 for worker in processes)
