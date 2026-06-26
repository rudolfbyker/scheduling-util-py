import json
import socket
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Literal

from click.testing import CliRunner
from pydantic import ValidationError

from scheduling_util import RateLimiter, schedule
from scheduling_util._schedule_cli import schedule_cli
from scheduling_util._controller import (
    PROTOCOL_VERSION,
    ControllerResponse,
    QuitRequest,
    RunNowRequest,
    ScheduleController,
    StatusRequest,
    WakeRequest,
    parse_controller_request,
    parse_controller_response,
)
from scheduling_util._controller_socket import (
    ScheduleControllerSocketServer,
    send_controller_socket_request,
)

has_unix_sockets = hasattr(socket, "AF_UNIX")


class TestControllerModels(unittest.TestCase):
    def test_parse_status_request(self) -> None:
        request = parse_controller_request(b'{"version":1,"command":"status"}')

        self.assertEqual(
            StatusRequest(version=PROTOCOL_VERSION, command="status"),
            request,
        )

    def test_request_requires_version(self) -> None:
        with self.assertRaises(ValidationError):
            parse_controller_request(b'{"command":"status"}')

    def test_request_rejects_unsupported_version(self) -> None:
        with self.assertRaises(ValidationError):
            parse_controller_request(b'{"version":2,"command":"status"}')

    def test_parse_response(self) -> None:
        response = parse_controller_response(
            b'{"version":1,"ok":true,"status":"ok","message":"OK.","scheduler":null}'
        )

        self.assertEqual(
            ControllerResponse(
                version=PROTOCOL_VERSION,
                ok=True,
                status="ok",
                message="OK.",
            ),
            response,
        )


@unittest.skipUnless(has_unix_sockets, "Unix sockets are not available.")
class TestControllerSocketServer(unittest.TestCase):
    def test_bind_fresh_socket_path(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            socket_path = Path(tmp_dir_str) / "scheduler.sock"
            controller = ScheduleController()

            with ScheduleControllerSocketServer(
                path=socket_path, controller=controller
            ):
                self.assertTrue(socket_path.exists())
                self.assertEqual(0o600, socket_path.stat().st_mode & 0o777)

                response = send_controller_socket_request(
                    socket_path=socket_path,
                    request=StatusRequest(version=PROTOCOL_VERSION, command="status"),
                )

            self.assertTrue(response.ok)
            self.assertEqual(PROTOCOL_VERSION, response.version)
            self.assertEqual("ok", response.status)
            self.assertFalse(socket_path.exists())

    def test_active_socket_path_fails(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            socket_path = Path(tmp_dir_str) / "scheduler.sock"
            controller = ScheduleController()

            with ScheduleControllerSocketServer(
                path=socket_path, controller=controller
            ):
                with self.assertRaises(RuntimeError):
                    ScheduleControllerSocketServer(
                        path=socket_path,
                        controller=ScheduleController(),
                    ).start()

    def test_stale_socket_path_is_unlinked(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            socket_path = Path(tmp_dir_str) / "scheduler.sock"

            stale_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale_socket.bind(str(socket_path))
            stale_socket.close()

            with ScheduleControllerSocketServer(
                path=socket_path,
                controller=ScheduleController(),
            ):
                response = send_controller_socket_request(
                    socket_path=socket_path,
                    request=StatusRequest(version=PROTOCOL_VERSION, command="status"),
                )

            self.assertTrue(response.ok)

    def test_non_socket_path_fails(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            socket_path = Path(tmp_dir_str) / "scheduler.sock"
            socket_path.write_text("not a socket")

            with self.assertRaises(FileExistsError):
                ScheduleControllerSocketServer(
                    path=socket_path,
                    controller=ScheduleController(),
                ).start()

    def test_invalid_request_gets_versioned_error_response(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            socket_path = Path(tmp_dir_str) / "scheduler.sock"

            with ScheduleControllerSocketServer(
                path=socket_path,
                controller=ScheduleController(),
            ):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(5)
                    client.connect(str(socket_path))
                    client.sendall(b'{"command":"status"}\n')
                    response_data = client.recv(4096)

            response = json.loads(response_data)
            self.assertEqual(PROTOCOL_VERSION, response["version"])
            self.assertFalse(response["ok"])
            self.assertEqual("error", response["status"])


@unittest.skipUnless(has_unix_sockets, "Unix sockets are not available.")
class TestScheduleControllerIntegration(unittest.TestCase):
    def _slack_rate_limiter(self, tmp_dir: Path) -> RateLimiter:
        return RateLimiter(
            minimum_period=timedelta(hours=1),
            path=tmp_dir / "rate_limiter" / "slack_errors",
        )

    def _start_scheduler(
        self,
        *,
        tmp_dir: Path,
        socket_path: Path,
        func: Callable[[], Literal["success"]],
        max_runs: int | None,
    ) -> threading.Thread:
        thread = threading.Thread(
            target=lambda: schedule(
                interval=timedelta(seconds=30),
                ipc_socket_path=socket_path,
                max_runs=max_runs,
                heartbeat_path=None,
                name="test",
                description=None,
                last_run_dir=tmp_dir / "last-run",
                last_run_reset=False,
                success_period=timedelta(days=1),
                neutral_period=timedelta(hours=2),
                failure_period=timedelta(hours=1),
                max_failures=3,
                on_max_failures="stall",
                func=func,
                slack_webhook=None,
                slack_rate_limiter=self._slack_rate_limiter(tmp_dir),
            ),
            daemon=True,
        )
        thread.start()
        return thread

    def _wait_for_status(
        self,
        *,
        socket_path: Path,
        predicate: Callable[[ControllerResponse], bool],
        thread: threading.Thread,
    ) -> ControllerResponse:
        deadline = time.monotonic() + 5
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            if not thread.is_alive():
                self.fail("Scheduler thread exited before expected status was seen.")

            try:
                response = send_controller_socket_request(
                    socket_path=socket_path,
                    request=StatusRequest(version=PROTOCOL_VERSION, command="status"),
                )
            except Exception as e:
                last_error = e
                time.sleep(0.01)
                continue

            if predicate(response):
                return response

            time.sleep(0.01)

        self.fail(f"Timed out waiting for scheduler status. Last error: {last_error}")

    def test_wake_respects_schedule_predicate(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            socket_path = tmp_dir / "scheduler.sock"
            n_runs = 0

            def func() -> Literal["success"]:
                nonlocal n_runs
                n_runs += 1
                return "success"

            thread = self._start_scheduler(
                tmp_dir=tmp_dir,
                socket_path=socket_path,
                func=func,
                max_runs=2,
            )
            self._wait_for_status(
                socket_path=socket_path,
                thread=thread,
                predicate=lambda r: r.scheduler is not None
                and r.scheduler.state == "sleeping"
                and r.scheduler.run_count == 1,
            )

            response = send_controller_socket_request(
                socket_path=socket_path,
                request=WakeRequest(version=PROTOCOL_VERSION, command="wake"),
            )
            thread.join(timeout=5)

            self.assertTrue(response.ok)
            self.assertFalse(thread.is_alive())
            self.assertEqual(1, n_runs)

    def test_run_now_bypasses_schedule_predicate(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            socket_path = tmp_dir / "scheduler.sock"
            n_runs = 0

            def func() -> Literal["success"]:
                nonlocal n_runs
                n_runs += 1
                return "success"

            thread = self._start_scheduler(
                tmp_dir=tmp_dir,
                socket_path=socket_path,
                func=func,
                max_runs=2,
            )
            self._wait_for_status(
                socket_path=socket_path,
                thread=thread,
                predicate=lambda r: r.scheduler is not None
                and r.scheduler.state == "sleeping"
                and r.scheduler.run_count == 1,
            )

            response = send_controller_socket_request(
                socket_path=socket_path,
                request=RunNowRequest(version=PROTOCOL_VERSION, command="run-now"),
            )
            thread.join(timeout=5)

            self.assertTrue(response.ok)
            self.assertFalse(thread.is_alive())
            self.assertEqual(2, n_runs)

    def test_run_now_returns_busy_while_job_is_running(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            socket_path = tmp_dir / "scheduler.sock"
            entered = threading.Event()
            can_finish = threading.Event()

            def func() -> Literal["success"]:
                entered.set()
                can_finish.wait(timeout=5)
                return "success"

            thread = self._start_scheduler(
                tmp_dir=tmp_dir,
                socket_path=socket_path,
                func=func,
                max_runs=None,
            )
            self.assertTrue(entered.wait(timeout=5))

            response = send_controller_socket_request(
                socket_path=socket_path,
                request=RunNowRequest(version=PROTOCOL_VERSION, command="run-now"),
            )
            quit_response = send_controller_socket_request(
                socket_path=socket_path,
                request=QuitRequest(version=PROTOCOL_VERSION, command="quit"),
            )
            can_finish.set()
            thread.join(timeout=5)

            self.assertFalse(response.ok)
            self.assertEqual("busy", response.status)
            self.assertTrue(quit_response.ok)
            self.assertFalse(thread.is_alive())

    def test_quit_exits_sleeping_scheduler(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            socket_path = tmp_dir / "scheduler.sock"
            n_runs = 0

            def func() -> Literal["success"]:
                nonlocal n_runs
                n_runs += 1
                return "success"

            thread = self._start_scheduler(
                tmp_dir=tmp_dir,
                socket_path=socket_path,
                func=func,
                max_runs=None,
            )
            self._wait_for_status(
                socket_path=socket_path,
                thread=thread,
                predicate=lambda r: r.scheduler is not None
                and r.scheduler.state == "sleeping",
            )

            response = send_controller_socket_request(
                socket_path=socket_path,
                request=QuitRequest(version=PROTOCOL_VERSION, command="quit"),
            )
            thread.join(timeout=5)

            self.assertTrue(response.ok)
            self.assertFalse(thread.is_alive())
            self.assertEqual(1, n_runs)

    def test_quit_waits_for_running_job_to_finish(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            socket_path = tmp_dir / "scheduler.sock"
            entered = threading.Event()
            can_finish = threading.Event()

            def func() -> Literal["success"]:
                entered.set()
                can_finish.wait(timeout=5)
                return "success"

            thread = self._start_scheduler(
                tmp_dir=tmp_dir,
                socket_path=socket_path,
                func=func,
                max_runs=None,
            )
            self.assertTrue(entered.wait(timeout=5))

            response = send_controller_socket_request(
                socket_path=socket_path,
                request=QuitRequest(version=PROTOCOL_VERSION, command="quit"),
            )

            self.assertTrue(response.ok)
            self.assertTrue(thread.is_alive())

            can_finish.set()
            thread.join(timeout=5)

            self.assertFalse(thread.is_alive())


@unittest.skipUnless(has_unix_sockets, "Unix sockets are not available.")
class TestScheduleControlCli(unittest.TestCase):
    def test_status_command_prints_versioned_response_json(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            socket_path = Path(tmp_dir_str) / "scheduler.sock"

            with ScheduleControllerSocketServer(
                path=socket_path,
                controller=ScheduleController(),
            ):
                result = CliRunner().invoke(
                    schedule_cli,
                    [
                        "control",
                        "--socket",
                        str(socket_path),
                        "status",
                    ],
                )

        self.assertEqual(0, result.exit_code, result.output)
        response = json.loads(result.output)
        self.assertEqual(PROTOCOL_VERSION, response["version"])
        self.assertTrue(response["ok"])
        self.assertEqual("ok", response["status"])
        self.assertEqual("starting", response["scheduler"]["state"])

    def test_connection_error_is_reported(self) -> None:
        with TemporaryDirectory() as tmp_dir_str:
            socket_path = Path(tmp_dir_str) / "missing.sock"

            result = CliRunner().invoke(
                schedule_cli,
                [
                    "control",
                    "--socket",
                    str(socket_path),
                    "status",
                ],
            )

        self.assertNotEqual(0, result.exit_code)
        self.assertIn("Error:", result.output)
