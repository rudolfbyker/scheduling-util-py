from __future__ import annotations

import os
import socket
import stat
import threading
from logging import getLogger
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ValidationError

from ._controller import (
    ControllerResponse,
    ScheduleController,
    error_response,
    parse_controller_request,
    parse_controller_response,
)

logger = getLogger(__name__)

MAX_MESSAGE_BYTES = 64 * 1024
SOCKET_CONNECT_TIMEOUT = 0.2


class EmptySocketRequest(Exception):
    """
    Raised when a socket client connects without sending a request line.
    """

    pass


class ScheduleControllerSocketServer:
    """
    Unix domain socket transport for `ScheduleController`.

    The server owns the socket lifecycle and newline-delimited JSON framing.
    It deliberately delegates command semantics to the generic controller so other
    transports can share the same protocol models and state machine.
    """

    def __init__(
        self,
        *,
        path: Path,
        controller: ScheduleController,
    ) -> None:
        """
        Store the socket path and controller used by the server thread.
        """

        self._path = path
        self._controller = controller
        self._stop_event = threading.Event()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    @property
    def path(self) -> Path:
        """
        Return the filesystem path where the Unix socket is bound.
        """

        return self._path

    def __enter__(self) -> Self:
        """
        Start the socket server when entering a context manager's block.
        """

        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        """
        Stop the socket server when leaving a context manager's block.
        """

        self.stop()

    def start(self) -> None:
        """
        Bind the socket path, set owner-only permissions, and start serving.
        """

        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError(
                "Unix domain sockets are not supported on this platform."
            )

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare_socket_path()

        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server_socket.bind(str(self._path))
            os.chmod(self._path, 0o600)
            server_socket.listen()
            server_socket.settimeout(0.1)
        except Exception:
            server_socket.close()
            raise

        self._socket = server_socket
        self._thread = threading.Thread(
            target=self._serve_forever,
            name=f"scheduling-util-control-socket:{self._path}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """
        Stop serving, close the socket, join the server thread, and unlink it.
        """

        self._stop_event.set()

        if self._socket is not None:
            self._socket.close()
            self._socket = None

        if self._thread is not None:
            if threading.current_thread() is not self._thread:
                self._thread.join(timeout=10)
                if self._thread.is_alive():
                    logger.error(
                        "Controller socket server thread did not stop in time."
                    )
            self._thread = None
        try:
            if self._path.exists() and stat.S_ISSOCK(self._path.stat().st_mode):
                self._path.unlink()
        except FileNotFoundError:
            pass

    def _prepare_socket_path(self) -> None:
        """
        Validate, reject, or remove an existing socket path before binding.
        """

        if not self._path.exists():
            return

        if not stat.S_ISSOCK(self._path.stat().st_mode):
            raise FileExistsError(f"{self._path} exists but is not a socket.")

        if socket_path_accepts_connections(self._path):
            raise RuntimeError(
                f"{self._path} is already accepting connections. "
                "Another scheduler may already be running."
            )

        self._path.unlink()

    def _serve_forever(self) -> None:
        """
        Accept socket connections until the server is stopped.
        """

        while not self._stop_event.is_set():
            server_socket = self._socket
            if server_socket is None:
                return

            try:
                connection, _address = server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop_event.is_set():
                    logger.exception("Controller socket failed.")
                return

            self._serve_connection(connection)

    def _serve_connection(self, connection: socket.socket) -> None:
        """
        Read one request from a socket connection and write one response.
        """

        with connection:
            try:
                request = parse_controller_request(read_request(connection))
                response = self._controller.handle_request(request)
            except EmptySocketRequest:
                return
            except ValidationError as e:
                response = error_response(f"Invalid controller request: {e}")
            except Exception as e:
                logger.exception("Failed to handle controller request.")
                response = error_response(f"{type(e).__name__}: {e}")

            try:
                connection.sendall(serialize_socket_message(response))
            except BrokenPipeError:
                logger.debug(
                    "Controller socket client disconnected before reading the response."
                )


def read_request(connection: socket.socket) -> bytes:
    """
    Read one newline-delimited request from a socket connection.
    """

    connection.settimeout(5)
    data = b""

    while b"\n" not in data:
        chunk = connection.recv(4096)
        if not chunk:
            break

        data += chunk
        if len(data) > MAX_MESSAGE_BYTES:
            raise ValueError("Controller request is too large.")

    line = data.split(b"\n", 1)[0].strip()
    if not line:
        raise EmptySocketRequest()

    return line


def socket_path_accepts_connections(path: Path) -> bool:
    """
    Return whether a Unix socket path currently accepts client connections.
    """

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client_socket:
        client_socket.settimeout(SOCKET_CONNECT_TIMEOUT)
        try:
            client_socket.connect(str(path))
        except OSError:
            return False

        return True


def send_controller_socket_request(
    *,
    socket_path: Path,
    request: BaseModel,
    timeout: float = 5,
) -> ControllerResponse:
    """
    Send one controller request over a Unix socket and return one response.
    """

    if not hasattr(socket, "AF_UNIX"):
        raise RuntimeError("Unix domain sockets are not supported on this platform.")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client_socket:
        client_socket.settimeout(timeout)
        client_socket.connect(str(socket_path))
        client_socket.sendall(serialize_socket_message(request))

        data = b""
        while b"\n" not in data:
            chunk = client_socket.recv(4096)
            if not chunk:
                break

            data += chunk
            if len(data) > MAX_MESSAGE_BYTES:
                raise ValueError("Controller response is too large.")

    line = data.split(b"\n", 1)[0].strip()
    if not line:
        raise ValueError("Controller response is empty.")

    return parse_controller_response(line)


def serialize_socket_message(model: BaseModel) -> bytes:
    """
    Serialize a controller model as one newline-delimited socket message.
    """

    return model.model_dump_json().encode("utf-8") + b"\n"
