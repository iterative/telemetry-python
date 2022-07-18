"""Iterative Telemetry."""

import json
import logging
import os
import platform
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path
from threading import Thread
from typing import Any, Callable, Dict, Union

import distro
import requests
from appdirs import user_config_dir  # type: ignore
from filelock import FileLock, Timeout

logger = logging.getLogger(__name__)
TOKEN = "s2s.jtyjusrpsww4k9b76rrjri.bl62fbzrb7nd9n6vn5bpqt"
URL = (
    "https://iterative-telemetry.herokuapp.com"
    "/api/v1/s2s/event?ip_policy=strict"
)

DO_NOT_TRACK_ENV = "ITERATIVE_DO_NOT_TRACK"
DO_NOT_TRACK_VALUE = "do-not-track"


class IterativeTelemetryLogger:
    def __init__(
        self,
        tool_name,
        tool_version,
        enabled: Union[bool, Callable] = True,
        url=URL,
        token=TOKEN,
        debug: bool = False,
    ):
        self.tool_name = tool_name
        self.tool_version = tool_version
        self.enabled = enabled
        self.url = url
        self.token = token
        self.debug = debug
        if self.debug:
            logger.setLevel(logging.DEBUG)
            logger.debug("IterativeTelemetryLogger is in debug mode")

    def send_cli_call(self, cmd_name: str, error: str = None, **kwargs):
        self.send_event("cli", cmd_name, error=error, **kwargs)

    def send_event(
        self,
        interface: str,
        action: str,
        error: str = None,
        use_thread: bool = False,
        use_daemon: bool = True,
        **kwargs,
    ):
        self.send(
            {
                "interface": interface,
                "action": action,
                "error": error,
                "extra": kwargs,
            },
            use_thread=use_thread,
            use_daemon=use_daemon,
        )

    def is_enabled(self):
        return (
            os.environ.get(DO_NOT_TRACK_ENV, None) is None and self.enabled()
            if callable(self.enabled)
            else self.enabled and _find_or_create_user_id() is not None
        )

    def send(
        self,
        payload: Dict[str, Any],
        use_thread: bool = False,
        use_daemon: bool = True,
    ):
        if not self.is_enabled():
            return
        payload.update(self._runtime_info())
        if use_thread and use_daemon:
            raise ValueError(
                "use_thread and use_daemon cannot be true at the same time"
            )
        logger.debug("Sending payload %s", payload)
        impl = self._send
        if use_daemon:
            impl = self._send_daemon
        if use_thread:
            impl = self._send_thread
        impl(payload)

    def _send_daemon(self, payload):
        cmd = (
            f"import requests;requests.post('{self.url}',"
            f"params={{'token':'{self.token}'}},json={payload})"
        )

        if os.name == "nt":

            from subprocess import (
                CREATE_NEW_PROCESS_GROUP,
                CREATE_NO_WINDOW,
                STARTF_USESHOWWINDOW,
                STARTUPINFO,
            )

            detached_flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            startupinfo = STARTUPINFO()
            startupinfo.dwFlags |= STARTF_USESHOWWINDOW
            subprocess.Popen(  # pylint: disable=consider-using-with
                [sys.executable, "-c", cmd],
                creationflags=detached_flags,
                close_fds=True,
                startupinfo=startupinfo,
            )
        elif os.name == "posix":
            subprocess.Popen(  # pylint: disable=consider-using-with
                [sys.executable, "-c", cmd],
                close_fds=True,
            )
        else:
            raise NotImplementedError

    def _send_thread(self, payload):
        Thread(target=self._send, args=[payload]).start()

    def _send(self, payload):
        try:
            requests.post(
                self.url, params={"token": self.token}, json=payload, timeout=2
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("failed to send analytics report", exc_info=True)

    def _runtime_info(self):
        """
        Gather information from the environment where DVC runs to fill a report
        """

        return {
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            # "tool_source": self.tool_source, # TODO
            # "scm_class": _scm_in_use(),
            **_system_info(),
            "user_id": _find_or_create_user_id(),
            "group_id": "",  # TODO
        }


def _system_info():
    system = platform.system()

    if system == "Windows":
        # pylint: disable=no-member
        version = sys.getwindowsversion()
        return {
            "os_name": "windows",
            "os_version": f"{version.build}.{version.major}."
            f"{version.minor}-{version.service_pack}",
        }

    if system == "Darwin":
        return {
            "os_name": "mac",
            "os_version": platform.mac_ver()[0],
        }  # TODO do we include arch here?

    if system == "Linux":
        # TODO distro.id() and distro.like()?
        return {
            "os_name": "linux",
            "os_version": distro.version(),
        }

    # We don't collect data for any other system.
    raise NotImplementedError


def _generate_id():
    """A randomly generated ID string"""
    return str(uuid.uuid4())  # TODO: CI env-based ID


def _read_user_id(config_file: Path):
    try:
        with config_file.open(encoding="utf8") as fobj:
            return json.load(fobj)["user_id"]
    except (FileNotFoundError, ValueError, KeyError):
        pass
    return None


def _read_user_id_locked(config_file: Path):
    lockfile = str(config_file.with_suffix(".lock"))
    if config_file.parent.is_dir():
        with FileLock(lockfile, timeout=5):
            return _read_user_id(config_file)
    return None


@lru_cache(None)
def _find_or_create_user_id():
    """
    The user's ID is stored on a file under the global config directory.
    The file should contain a JSON with a "user_id" key:
        {"user_id": "16fd2706-8baf-433b-82eb-8c7fada847da"}
    IDs are generated randomly with UUID4.
    """
    config_file = Path(
        user_config_dir(os.path.join("iterative", "telemetry"), False)
    )
    config_file.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    lockfile = str(config_file.with_suffix(".lock"))
    # DVC backwards-compatibility
    config_file_old = Path(
        user_config_dir(os.path.join("dvc", "user_id"), "iterative")
    )

    try:
        with FileLock(  # pylint: disable=abstract-class-instantiated
            lockfile, timeout=5
        ):
            user_id = _read_user_id(config_file)
            if user_id is None:
                try:
                    user_id = _read_user_id_locked(config_file_old)
                except Timeout:
                    logger.debug(
                        "Failed to acquire %s",
                        config_file_old.with_suffix(".lock"),
                    )
                    return None
                if user_id is None:
                    user_id = _generate_id()
                with config_file.open(mode="w", encoding="utf8") as fobj:
                    json.dump({"user_id": user_id}, fobj)
    except Timeout:
        logger.debug("Failed to acquire %s", lockfile)
    return user_id if user_id.lower() != DO_NOT_TRACK_VALUE.lower() else None
