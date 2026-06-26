from __future__ import annotations

import threading
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION: Literal[1] = 1

ProtocolVersion = Literal[1]
SchedulerState = Literal["starting", "sleeping", "running", "stopping", "stopped"]
ResponseStatus = Literal["ok", "accepted", "busy", "error"]


class ControllerRequestBase(BaseModel):
    """
    Base model shared by all scheduler control requests.

    The version field is part of the wire protocol, so future transports can
    reject or route incompatible requests before executing a command.
    """

    model_config = ConfigDict(extra="forbid")

    version: ProtocolVersion


class StatusRequest(ControllerRequestBase):
    """
    Request the scheduler's current observable controller state.
    """

    command: Literal["status"]


class WakeRequest(ControllerRequestBase):
    """
    Wake the scheduler loop and evaluate the normal schedule immediately.
    """

    command: Literal["wake"]


class RunNowRequest(ControllerRequestBase):
    """
    Request a forced run that bypasses the schedule predicate once.
    """

    command: Literal["run-now"]


class QuitRequest(ControllerRequestBase):
    """
    Request a graceful scheduler shutdown.
    """

    command: Literal["quit"]


ControllerRequest = Annotated[
    StatusRequest | WakeRequest | RunNowRequest | QuitRequest,
    Field(discriminator="command"),
]


class SchedulerStatus(BaseModel):
    """
    Snapshot of scheduler state returned by controller responses.
    """

    model_config = ConfigDict(extra="forbid")

    state: SchedulerState
    pending_wake: bool
    pending_run_now: bool
    quit_requested: bool
    run_count: int


class ControllerResponse(BaseModel):
    """
    Versioned response shared by all scheduler control transports.
    """

    model_config = ConfigDict(extra="forbid")

    version: ProtocolVersion
    ok: bool
    status: ResponseStatus
    message: str
    scheduler: SchedulerStatus | None = None


_request_adapter: TypeAdapter[ControllerRequest] = TypeAdapter(ControllerRequest)
_response_adapter: TypeAdapter[ControllerResponse] = TypeAdapter(ControllerResponse)


def parse_controller_request(data: bytes | str) -> ControllerRequest:
    """
    Parse and validate a JSON scheduler control request.
    """

    return _request_adapter.validate_json(data)


def parse_controller_response(data: bytes | str) -> ControllerResponse:
    """
    Parse and validate a JSON scheduler control response.
    """

    return _response_adapter.validate_json(data)


def error_response(message: str) -> ControllerResponse:
    """
    Build a versioned error response for invalid or failed control requests.
    """

    return ControllerResponse(
        version=PROTOCOL_VERSION,
        ok=False,
        status="error",
        message=message,
    )


class ScheduleController:
    """
    Thread-safe scheduler control state machine.

    Transports such as Unix sockets or a future HTTP endpoint should validate
    JSON into `ControllerRequest` models, pass them to `handle_request`, and
    serialize the returned `ControllerResponse` without adding command logic.
    """

    def __init__(self) -> None:
        """
        Initialize the controller in the `starting` state with no pending work.
        """

        self._condition = threading.Condition()
        self._state: SchedulerState = "starting"
        self._pending_wake = False
        self._pending_run_now = False
        self._quit_requested = False
        self._run_count = 0

    def status(self) -> SchedulerStatus:
        """
        Return a consistent snapshot of the current scheduler control state.
        """

        with self._condition:
            return SchedulerStatus(
                state=self._state,
                pending_wake=self._pending_wake,
                pending_run_now=self._pending_run_now,
                quit_requested=self._quit_requested,
                run_count=self._run_count,
            )

    def handle_request(self, request: ControllerRequest) -> ControllerResponse:
        """
        Apply one validated control request and return its protocol response.
        """

        if isinstance(request, StatusRequest):
            return ControllerResponse(
                version=PROTOCOL_VERSION,
                ok=True,
                status="ok",
                message="Scheduler status.",
                scheduler=self.status(),
            )

        if isinstance(request, WakeRequest):
            return self._request_wake()

        if isinstance(request, RunNowRequest):
            return self._request_run_now()

        if isinstance(request, QuitRequest):
            return self._request_quit()

        return error_response("Unsupported command.")

    def _request_wake(self) -> ControllerResponse:
        """
        Record a pending wake request and notify the scheduler loop.
        """

        with self._condition:
            if self._quit_requested:
                return ControllerResponse(
                    version=PROTOCOL_VERSION,
                    ok=False,
                    status="error",
                    message="Scheduler is quitting.",
                    scheduler=self.status(),
                )

            self._pending_wake = True
            self._condition.notify_all()
            return ControllerResponse(
                version=PROTOCOL_VERSION,
                ok=True,
                status="accepted",
                message="Wake requested.",
                scheduler=self.status(),
            )

    def _request_run_now(self) -> ControllerResponse:
        """
        Record a pending forced run unless the scheduler is already running.
        """

        with self._condition:
            if self._state == "running":
                return ControllerResponse(
                    version=PROTOCOL_VERSION,
                    ok=False,
                    status="busy",
                    message="Scheduler is already running the job.",
                    scheduler=self.status(),
                )

            if self._quit_requested:
                return ControllerResponse(
                    version=PROTOCOL_VERSION,
                    ok=False,
                    status="error",
                    message="Scheduler is quitting.",
                    scheduler=self.status(),
                )

            self._pending_run_now = True
            self._condition.notify_all()
            return ControllerResponse(
                version=PROTOCOL_VERSION,
                ok=True,
                status="accepted",
                message="Run requested.",
                scheduler=self.status(),
            )

    def _request_quit(self) -> ControllerResponse:
        """
        Request a graceful scheduler shutdown and wake the scheduler loop.
        """

        with self._condition:
            self._quit_requested = True
            if self._state != "running":
                self._state = "stopping"
            self._condition.notify_all()
            return ControllerResponse(
                version=PROTOCOL_VERSION,
                ok=True,
                status="accepted",
                message="Quit requested.",
                scheduler=self.status(),
            )

    def mark_running(self) -> None:
        """
        Mark the scheduler as currently executing the scheduled job.
        """

        with self._condition:
            self._state = "running"
            self._condition.notify_all()

    def mark_stopped(self) -> None:
        """
        Mark the scheduler as fully stopped.
        """

        with self._condition:
            self._state = "stopped"
            self._condition.notify_all()

    def update_run_count(self, run_count: int) -> None:
        """
        Store the number of scheduler loop runs completed.
        """

        with self._condition:
            self._run_count = run_count
            self._condition.notify_all()

    def quit_requested(self) -> bool:
        """
        Return whether a graceful shutdown has been requested.
        """

        with self._condition:
            return self._quit_requested

    def take_next_run_mode(self) -> Literal["normal", "run-now"]:
        """
        Consume pending wake/run-now state and return the next run mode.
        """

        with self._condition:
            if self._pending_run_now:
                self._pending_run_now = False
                self._pending_wake = False
                return "run-now"

            self._pending_wake = False
            return "normal"

    def has_pending_work(self) -> bool:
        """
        Return whether the scheduler should skip sleeping and loop again now.
        """

        with self._condition:
            return self._pending_wake or self._pending_run_now or self._quit_requested

    def wait_for_work(self, timeout: float) -> None:
        """
        Wait until a control request arrives or the timeout elapses.
        """

        with self._condition:
            if self._quit_requested:
                self._state = "stopping"
                return

            self._state = "sleeping"
            self._condition.notify_all()
            if not self._pending_wake and not self._pending_run_now:
                self._condition.wait(timeout=timeout)
