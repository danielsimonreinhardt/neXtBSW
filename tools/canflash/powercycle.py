"""Optional power cycle of the ECU while canflash waits for the bootloader (TOOL-51).

`canflash --wait` catches the bootloader in its startup window after a reset. When the ECU cannot be reset over
the bus - it holds a safe state (for example after ROM_CHECK_FAILURE) and does not take part in the bus any more -
only switching the supply off and on again brings it there. This module does that on request, and only then:

- `--power-cycle <device>`: a LabControl power supply over its network interface (the same HTTP interface the
  LabControl MCP server uses; address and token from LABCONTROL_URL and LABCONTROL_TOKEN). Only the standard
  library is used, so canflash has no dependency on LabControl; without the option nothing is switched. Without
  LABCONTROL_TOKEN (or with a wrong one) LabControl refuses every switch with "unauthorized" (TOOL-153, board
  2026-09-28): the token comes from LabControl itself (Settings -> Network, copy button), never from canflash,
  and canflash sends it only to LabControl - nowhere else, never logged.
- `--power-off-cmd <cmd>` / `--power-on-cmd <cmd>`: any other switch (relay, other supply), as shell commands.

Without one of these options canflash switches nothing (default). Free of Qt and of the bus: the hook only
switches, canflash runs it in a thread of its own while it keeps sending 10 02, so the bootloader's startup window
is covered from the first millisecond of power."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

LABCONTROL_URL_ENV = "LABCONTROL_URL"
LABCONTROL_TOKEN_ENV = "LABCONTROL_TOKEN"
LABCONTROL_DEFAULT_URL = "http://127.0.0.1:8420"
LABCONTROL_TIMEOUT_S = 25.0        # above LabControl's own wait for the device (15 s), like its MCP server
COMMAND_TIMEOUT_S = 60.0
DEFAULT_OFF_S = 1.0                # long enough for the ECU's supply capacitors to discharge (assumption, board)


class PowerError(RuntimeError):
    """The power switch refused or could not be reached."""


class PowerSwitch:
    """Switches the supply of the ECU. `off()` and `on()` return when the switch confirmed, or raise PowerError."""

    name = "power switch"

    def off(self) -> None:
        raise NotImplementedError

    def on(self) -> None:
        raise NotImplementedError


class CommandSwitch(PowerSwitch):
    """Two shell commands; a non-zero exit code or a timeout is a PowerError."""

    def __init__(self, off_cmd: str, on_cmd: str, run: Callable = subprocess.run):
        self.off_cmd, self.on_cmd, self._run = off_cmd, on_cmd, run
        self.name = "power commands"

    def _call(self, cmd: str) -> None:
        try:
            result = self._run(cmd, shell=True, timeout=COMMAND_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as e:
            raise PowerError(f"'{cmd}' failed: {e}") from e
        if result.returncode != 0:
            raise PowerError(f"'{cmd}' ended with exit code {result.returncode}")

    def off(self) -> None:
        self._call(self.off_cmd)

    def on(self) -> None:
        self._call(self.on_cmd)


class LabControlSwitch(PowerSwitch):
    """A power supply in LabControl (actions PSU_OUT_OFF / PSU_OUT_ON of POST /api/v1/tiles/<device>/actions).

    LabControl decides everything that concerns safety (token, control permission per device, main switch, safety
    limits); a refusal ends as a PowerError with LabControl's error code. With `on_current` the supply is switched
    on with that current limit (PSU_CURR) instead of PSU_OUT_ON: an HCS-34xx has no real output switch, its
    PSU_OUT_ON raises the current limit to 0.1 A only, which may be too little for the ECU's start."""

    def __init__(self, device: str, url: str | None = None, token: str | None = None,
                 on_current: float | None = None, urlopen: Callable = urllib.request.urlopen):
        self.device = device
        self.url = (url or os.environ.get(LABCONTROL_URL_ENV) or LABCONTROL_DEFAULT_URL).rstrip("/")
        self.token = token if token is not None else os.environ.get(LABCONTROL_TOKEN_ENV, "")
        self.on_current = on_current
        self._urlopen = urlopen
        self.name = f"LabControl {device}"

    def _action(self, action: str, value: float | None = None) -> dict:
        payload: dict = {"action": action}
        if value is not None:
            payload["value"] = value
        path = "/api/v1/tiles/" + urllib.parse.quote(self.device, safe=":") + "/actions"
        request = urllib.request.Request(self.url + path, data=json.dumps(payload).encode("utf-8"), method="POST")
        request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with self._urlopen(request, timeout=LABCONTROL_TIMEOUT_S) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise PowerError(f"LabControl at {self.url} not reachable ({e})") from e
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        error_code = body.get("error")
        # TOOL-153: board 2026-09-28, `--power-cycle psu:COM6` was refused with "unauthorized" because no
        # LABCONTROL_TOKEN was set. LabControl answers a missing or wrong token with HTTP 401 and
        # error "unauthorized" (lab_gui/share_api.py); HTTP 403 is used for other, unrelated refusals
        # (remote_control_inactive, control_not_permitted) that already get their own message below, so
        # only 401/the "unauthorized" code are treated as a token problem.
        if status == 401 or error_code == "unauthorized":
            token_hint = (f"no {LABCONTROL_TOKEN_ENV} was set" if not self.token
                         else f"the token in {LABCONTROL_TOKEN_ENV} was refused")
            raise PowerError(
                f"LabControl refused {action} for {self.device}: unauthorized (HTTP {status}, {token_hint}). "
                f"Copy the current token from LabControl (Settings -> Network: enable network sharing, then "
                f"the copy button next to the token field) into the environment variable {LABCONTROL_TOKEN_ENV} "
                "and try again. canflash only ever sends the token to LabControl, never anywhere else.")
        if status not in (200, 202) or body.get("ok") is False:
            raise PowerError(f"LabControl refused {action} for {self.device}: "
                             f"{error_code or 'HTTP ' + str(status)}")
        return body

    def off(self) -> None:
        self._action("PSU_OUT_OFF")

    def on(self) -> None:
        if self.on_current is not None:
            self._action("PSU_CURR", self.on_current)
        else:
            self._action("PSU_OUT_ON")


def switch_from_args(args) -> PowerSwitch | None:
    """The power switch the command line asks for, or None (default: nothing is switched).

    Error messages name the option, like the other checks of canflash (ValueError ends as one line)."""
    device = getattr(args, "power_cycle", None)
    off_cmd = getattr(args, "power_off_cmd", None)
    on_cmd = getattr(args, "power_on_cmd", None)
    on_current = getattr(args, "power_on_current", None)
    if device is None and off_cmd is None and on_cmd is None:
        if on_current is not None:
            raise ValueError("--power-on-current belongs to --power-cycle")
        return None
    if device is not None and (off_cmd is not None or on_cmd is not None):
        raise ValueError("--power-cycle and --power-off-cmd/--power-on-cmd exclude each other")
    if device is None and (off_cmd is None or on_cmd is None):
        raise ValueError("--power-off-cmd and --power-on-cmd are only accepted together")
    if device is None and on_current is not None:
        raise ValueError("--power-on-current belongs to --power-cycle")
    if not getattr(args, "wait", 0.0) > 0:
        raise ValueError("switching the power needs --wait <s>: the bootloader is caught in its startup window")
    if getattr(args, "power_off_time", DEFAULT_OFF_S) < 0:
        raise ValueError("--power-off-time must not be negative")
    if on_current is not None and on_current <= 0:
        raise ValueError("--power-on-current must be greater than 0 A")
    if device is not None:
        return LabControlSwitch(device, on_current=on_current)
    return CommandSwitch(off_cmd, on_cmd)


class PowerCycle(threading.Thread):
    """Off, `off_s` seconds pause, on - in a thread of its own, so the caller keeps requesting meanwhile.

    Not a daemon thread on purpose: a Ctrl-C of the tester during the pause still switches the ECU on again
    before the process ends. `error` holds what went wrong (None after a complete cycle)."""

    def __init__(self, switch: PowerSwitch, off_s: float = DEFAULT_OFF_S, log=print, sleep=time.sleep):
        super().__init__(name="canflash-power-cycle")
        self.switch, self.off_s, self.log, self._sleep = switch, off_s, log, sleep
        self.error: Exception | None = None
        self.switched_off = False

    def run(self) -> None:
        try:
            self.log(f"Power cycle over {self.switch.name}: off for {self.off_s:.1f} s ...")
            self.switch.off()
            self.switched_off = True
            self._sleep(self.off_s)
            self.switch.on()
            self.log("Power on again.")
        except Exception as e:          # the waiting goes on; the tester can still switch by hand
            self.error = e
            state = "the ECU may still be without power" if self.switched_off else "nothing was switched"
            self.log(f"Power cycle failed ({e}); {state} - switch it by hand, still waiting.")
