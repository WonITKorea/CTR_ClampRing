import ctypes
import json
import os
import re
import struct
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import winreg
except ImportError:
    winreg = None

MR_MC240N_WINDOWS_ONLY_MESSAGE = "MR-MC240N position board monitoring is supported only on Windows."
MR_MC240N_USB_VID = "06D3"
MR_MC240N_USB_PID = "01D1"
MR_MC240N_USB_INSTANCE_PREFIX = f"USB\\VID_{MR_MC240N_USB_VID}&PID_{MR_MC240N_USB_PID}"
MR_MC240N_USB_DEVICE_NAME = "MITSUBISHI SSCNET Unit USB Controller"
MR_CONNECTION_USB_MAINTENANCE = "USB controller (direct)"
MR_CONNECTION_PCIE_API = "PCIe control (API)"
MR_MC240N_PROJECT_CONNECTIONS = {
    0: MR_CONNECTION_PCIE_API,
    1: MR_CONNECTION_USB_MAINTENANCE,
}
MR_MC240N_REQUIRED_API_EXPORTS = (
    "sscOpen",
    "sscClose",
    "sscGetLastError",
    "sscSystemStart",
    "sscReboot",
    "sscResetAllParameter",
    "sscChangeParameterEx",
    "sscChange2ParameterEx",
    "sscGetSystemStatusCode",
    "sscGetCurrentFbPositionFast",
    "sscSetCommandBitSignalEx",
    "sscGetStatusBitSignalEx",
    "sscJogStart",
    "sscJogStop",
    "sscIncStart",
    "sscHomeReturnStart",
    "sscDriveStop",
    "sscDriveRapidStop",
)


def calculate_soft_limit_stop_margin_mm(
    speed_mm_min,
    deceleration_ms,
    poll_interval_ms,
    reserve_mm=0.1,
):
    """Return a conservative JOG stop margin for a host-side soft limit.

    The estimate assumes the axis can remain at full speed throughout the
    configured deceleration time and two position-poll intervals. The board's
    own software stroke limit remains the primary protection once home return
    has completed; this margin is an independent application-side guard.
    """
    speed_mm_min = float(speed_mm_min)
    deceleration_ms = float(deceleration_ms)
    poll_interval_ms = float(poll_interval_ms)
    reserve_mm = float(reserve_mm)
    if speed_mm_min < 0:
        raise ValueError("Speed must not be negative.")
    if deceleration_ms < 0:
        raise ValueError("Deceleration time must not be negative.")
    if poll_interval_ms <= 0:
        raise ValueError("Poll interval must be greater than zero.")
    if reserve_mm < 0:
        raise ValueError("Soft-limit reserve must not be negative.")
    speed_mm_ms = speed_mm_min / 60_000.0
    return reserve_mm + speed_mm_ms * (
        deceleration_ms + 2.0 * poll_interval_ms
    )

MR_MC240N_API_ERROR_MESSAGES = {
    0xFFFFFFFF: "unknown API failure (SSC_FUNC_ERR_UNKNOWN)",
    0xFFFFFFFE: "unsupported operating system (SSC_FUNC_ERR_UNSURPORT_OS)",
    0x00000100: "API argument combination mismatch (SSC_FUNC_ERR_ARGUMENT_MISMATCH)",
    0x00010000: "API timeout (SSC_FUNC_ERR_TIMEOUT_01)",
    0x00010100: "API timeout (SSC_FUNC_ERR_TIMEOUT_02)",
    0x00010200: "API timeout (SSC_FUNC_ERR_TIMEOUT_03)",
    0x00010300: "API timeout (SSC_FUNC_ERR_TIMEOUT_04)",
    0x00010400: "API timeout (SSC_FUNC_ERR_TIMEOUT_05)",
    0x00010500: "API timeout (SSC_FUNC_ERR_TIMEOUT_06)",
    0x00010600: "API timeout (SSC_FUNC_ERR_TIMEOUT_07)",
    0x00010700: "API timeout (SSC_FUNC_ERR_TIMEOUT_08)",
    0x00010800: "API timeout (SSC_FUNC_ERR_TIMEOUT_09)",
    0x00020000: "board is already open (SSC_FUNC_ERR_REOPEN)",
    0x00020010: "board is not open (SSC_FUNC_ERR_UNOPEN)",
    0x00021010: "position board not found (SSC_FUNC_ERR_NOT_FOUND_BOARD)",
    0x00021011: "could not read the channel count (SSC_FUNC_ERR_GET_CHANNEL_NUM)",
    0x00021012: "unsupported device driver (SSC_FUNC_ERR_UNSUPPORT_DEVICE_DRIVER)",
    0x00023000: "device-driver operation failed (SSC_FUNC_ERR_DEVICE_DRIVER)",
    0x00030000: "system is not preparation-complete; reboot may be required (SSC_FUNC_ERR_UNREADY_CHANNEL)",
    0x00030010: "channel is already configured (SSC_FUNC_ERR_ALREADY_CHANNEL)",
    0x00030020: "system is waiting for System Start (SSC_FUNC_ERR_RUNNING_CHANNEL)",
    0x00030030: "position-board system alarm is active (SSC_FUNC_ERR_NOW_ALARM_SYSTEM)",
    0x00060010: "axis is currently driving (SSC_FUNC_ERR_NOW_DRIVING)",
    0x00060011: "axis is not drive-ready (SSC_FUNC_ERR_NOW_DRIVING_READY)",
    0x00060020: "servo alarm is active (SSC_FUNC_ERR_NOW_ALARM_SERVO)",
    0x00060030: (
        "drive alarm is active (SSC_FUNC_ERR_NOW_ALARM_DRIVE); "
        "motion is blocked until the MR-J4 amplifier alarm is reset"
    ),
}
MR_MC240N_API_ERROR_MESSAGES.update(
    {
        argument_number: (
            f"API argument {argument_number} is invalid "
            f"(SSC_FUNC_ERR_ARGUMENT_{argument_number:02d})"
        )
        for argument_number in range(1, 10)
    }
)


def describe_mr_mc240n_api_error(error_code):
    error_code = int(error_code) & 0xFFFFFFFF
    description = MR_MC240N_API_ERROR_MESSAGES.get(error_code, "unknown API error")
    return f"0x{error_code:08X}: {description}"


def load_mr_mc240n_parameter_file(parameter_path):
    """Load a PB Test ``.prm2`` file grouped by system/axis/station No."""
    resolved_path = os.path.abspath(
        os.path.expandvars(os.path.expanduser(parameter_path))
    )
    if not os.path.isfile(resolved_path):
        raise FileNotFoundError(
            f"MR-MC240N parameter file was not found: {resolved_path}"
        )

    parameter_groups = {}
    with open(resolved_path, "r", encoding="ascii") as parameter_file:
        for line_number, raw_line in enumerate(parameter_file, 1):
            line = raw_line.strip()
            if not line or line.startswith((";", "#")):
                continue
            fields = line.split(",")
            if len(fields) != 3:
                raise ValueError(
                    f"Invalid .prm2 row at line {line_number}: {line}"
                )
            try:
                target = int(fields[0], 10)
                parameter_number = int(fields[1], 16)
                parameter_value = int(fields[2], 16)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid .prm2 value at line {line_number}: {line}"
                ) from exc
            if not -4 <= target <= 32:
                raise ValueError(
                    f"Unsupported MR-MC200 target {target} at line {line_number}"
                )
            if not 0 <= parameter_number <= 0xFFFF:
                raise ValueError(
                    f"Parameter number is out of range at line {line_number}"
                )
            if not 0 <= parameter_value <= 0xFFFFFFFF:
                raise ValueError(
                    f"Parameter value is out of range at line {line_number}"
                )
            parameter_groups.setdefault(target, []).append(
                (parameter_number, parameter_value)
            )
    if not parameter_groups:
        raise ValueError(f"MR-MC240N parameter file is empty: {resolved_path}")
    return parameter_groups


def load_mr_mc240n_project(project_path):
    """Read the connection metadata from a PB Test ``.pbp2`` project.

    PB Test project files also contain bare filenames under PROJECT_FILE, so
    ConfigParser cannot parse them reliably.  Only the key/value sections
    needed by this application are read here; the referenced .prm2 remains a
    PB Test parameter file and is not silently written to the controller.
    """
    resolved_path = os.path.abspath(os.path.expandvars(os.path.expanduser(project_path)))
    if not os.path.isfile(resolved_path):
        raise FileNotFoundError(f"MR-MC240N project was not found: {resolved_path}")

    sections = {}
    current_section = ""
    with open(resolved_path, "r", encoding="utf-8-sig") as project_file:
        for line_number, raw_line in enumerate(project_file, 1):
            line = raw_line.strip()
            if not line or line.startswith((";", "#")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].strip().upper()
                sections.setdefault(current_section, {})
                continue
            if "=" in line and current_section:
                key, value = line.split("=", 1)
                sections[current_section][key.strip().upper()] = value.strip()

    board = sections.get("BOARD", {})
    interface = sections.get("INTERFACE", {})
    options = sections.get("OPTION", {})
    positioning = sections.get("POSITIONING", {})
    board_type = board.get("BOARD_TYPE", "").upper()
    if board_type not in ("MR-MC240", "MR-MC240N"):
        raise ValueError(
            f"Unsupported position-board project type {board_type or '(missing)'}"
        )
    try:
        board_id = int(board.get("BOARD_ID", ""))
        channel = int(board.get("CHANNEL", ""))
        connection_code = int(interface.get("CONNECTION", ""))
        tool_mode = int(options.get("TOOL_MODE", "0"))
    except ValueError as exc:
        raise ValueError("Invalid BOARD_ID, CHANNEL, or CONNECTION in project") from exc
    if not 0 <= board_id <= 3:
        raise ValueError(f"Project BOARD_ID must be 0..3, got {board_id}")
    if channel != 1:
        raise ValueError(f"Only MR-MC240N channel 1 is supported, got {channel}")
    if connection_code not in MR_MC240N_PROJECT_CONNECTIONS:
        raise ValueError(f"Unsupported project CONNECTION={connection_code}")
    if tool_mode not in (0, 1):
        raise ValueError(f"Unsupported project TOOL_MODE={tool_mode}")

    project_directory = os.path.dirname(resolved_path)

    def resolve_companion(filename):
        if not filename:
            return ""
        candidates = (
            os.path.join(project_directory, filename),
            os.path.join(
                project_directory,
                "PbTest",
                "Template",
                "MR-MC2XX",
                "ENU",
                filename,
            ),
        )
        for candidate in candidates:
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        return os.path.abspath(candidates[0])

    parameter_name = positioning.get("PRM_FILE", "")
    point_name = positioning.get("PNT_FILE_AUTO", "")
    parameter_file = resolve_companion(parameter_name)
    axis_parameters = {}
    if parameter_file and os.path.isfile(parameter_file):
        with open(parameter_file, "r", encoding="ascii") as parameter_handle:
            for raw_line in parameter_handle:
                fields = raw_line.strip().split(",")
                if len(fields) != 3 or fields[1].upper() not in (
                    "0X0200",
                    "0X0203",
                ):
                    continue
                try:
                    axis = int(fields[0])
                    value = int(fields[2], 16)
                except ValueError:
                    continue
                if 1 <= axis <= 20:
                    axis_parameters.setdefault(axis, {})[
                        fields[1].upper()
                    ] = value
    control_axes = [
        axis
        for axis in range(1, 21)
        if axis_parameters.get(axis, {}).get("0X0200") == 1
        and axis_parameters.get(axis, {}).get("0X0203", 0) != 0
    ]
    return {
        "path": resolved_path,
        "board_type": board_type,
        "board_id": board_id,
        "channel": channel,
        "connection_code": connection_code,
        "connection": MR_MC240N_PROJECT_CONNECTIONS[connection_code],
        "tool_mode": tool_mode,
        "tool_mode_name": "Test Mode" if tool_mode == 1 else "Monitor Mode",
        "parameter_file": parameter_file,
        "point_file": resolve_companion(point_name),
        "control_axes": control_axes,
    }


def get_position_board_api_library_directory():
    """Return the API Library directory registered by Position Board Utility2."""
    if os.name != "nt" or winreg is None:
        return ""

    registry_queries = [
        (
            r"SOFTWARE\MITSUBISHI\PositionBoardUtility2",
            getattr(winreg, "KEY_WOW64_32KEY", 0),
        ),
        (
            r"SOFTWARE\WOW6432Node\MITSUBISHI\PositionBoardUtility2",
            0,
        ),
    ]
    for registry_path, view_flag in registry_queries:
        try:
            access = winreg.KEY_READ | view_flag
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                registry_path,
                0,
                access,
            ) as key:
                install_path = str(winreg.QueryValueEx(key, "InstallPath")[0]).strip()
        except OSError:
            continue
        if install_path:
            return os.path.join(
                os.path.abspath(os.path.expandvars(install_path)),
                "API Library",
                "Library",
            )
    return ""


def detect_mr_mc240n_usb_controller():
    """Return USB maintenance-interface information without opening the device."""
    result = {
        "connected": False,
        "name": MR_MC240N_USB_DEVICE_NAME,
        "instance_prefix": MR_MC240N_USB_INSTANCE_PREFIX,
        "driver": "",
        "status": "",
    }
    if os.name != "nt":
        return result

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                "pnputil",
                "/enum-devices",
                "/connected",
                "/class",
                "USB",
                "/drivers",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            creationflags=creation_flags,
            check=False,
        )
        output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        result["status"] = f"USB query failed: {exc}"
        return result

    normalized_output = output.upper()
    result["connected"] = (
        completed.returncode == 0
        and f"VID_{MR_MC240N_USB_VID}&PID_{MR_MC240N_USB_PID}" in normalized_output
    )
    driver_match = re.search(r"\boem\d+\.inf\b", output, flags=re.IGNORECASE)
    if driver_match:
        result["driver"] = driver_match.group(0)
    if result["connected"]:
        result["status"] = "USB maintenance interface detected"
    elif output.strip():
        result["status"] = "USB maintenance interface not detected"
    return result


def get_windows_pe_architecture(file_path):
    """Return x86/x64 for a PE file, or an empty string when it cannot be read."""
    try:
        with open(file_path, "rb") as handle:
            if handle.read(2) != b"MZ":
                return ""
            handle.seek(0x3C)
            pe_offset = struct.unpack("<I", handle.read(4))[0]
            handle.seek(pe_offset)
            if handle.read(4) != b"PE\x00\x00":
                return ""
            machine = struct.unpack("<H", handle.read(2))[0]
    except (OSError, struct.error):
        return ""

    return {
        0x014C: "x86",
        0x8664: "x64",
    }.get(machine, "")


class MrMc240nUsbController:
    """Persistent x86 C bridge for MR-MC240 USB monitoring and motion."""

    SSC_DIR_PLUS = 0
    SSC_DIR_MINUS = 1
    SYSTEM_STATUS_NAMES = {
        0x0000: "PREPARING",
        0x0001: "PREPARATION COMPLETE",
        0x0009: "WAITING FOR SSCNET RESPONSE",
        0x000A: "RUNNING",
        0x000F: "REBOOTING",
    }

    def __init__(self, board_id, axis_number, dll_path=""):
        self.board_id = int(board_id)
        self.channel = 1
        self.axis_number = int(axis_number)
        self.dll_path = dll_path.strip()
        self.process = None
        self._request_lock = threading.Lock()
        self._is_open = False
        self.identity = ""
        self.board_signature = None
        self.system_status_code = None
        self._jog_active = False
        self._motion_command_may_be_active = False
        self._motion_seen_operating = False
        self._motion_seen_incomplete = False
        self._motion_dispatch_confirmed = False
        self._motion_start_position = None
        self._motion_kind = ""

    @staticmethod
    def is_supported_platform():
        return os.name == "nt"

    def _bridge_path(self):
        if not self.is_supported_platform():
            raise RuntimeError(MR_MC240N_WINDOWS_ONLY_MESSAGE)
        module_dir = os.path.dirname(os.path.abspath(__file__))
        bridge = os.path.join(module_dir, "bin", "mr_mc240n_usb.exe")
        if not os.path.isfile(bridge):
            raise RuntimeError(
                "MR-MC240 USB C bridge not found. Build it with "
                "scripts/build_usb_bridge.ps1. "
                f"Expected path: {bridge}"
            )
        return bridge

    def open(self):
        if self._is_open:
            return
        command = [
            self._bridge_path(),
            "--board",
            str(self.board_id),
            "--channel",
            str(self.channel),
        ]
        if self.dll_path:
            command.extend(["--dll", os.path.abspath(os.path.expandvars(self.dll_path))])
        command.append("serve")
        startup_info = None
        if os.name == "nt":
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            startupinfo=startup_info,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            ready_line = self.process.stdout.readline()
            if not ready_line:
                detail = self.process.stderr.read().strip()
                raise RuntimeError(detail or "USB C bridge exited before ready.")
            ready = json.loads(ready_line)
            if not ready.get("ok"):
                raise RuntimeError(ready.get("error", "USB C bridge failed to start."))
            self._is_open = True
            self.check_connection()
        except Exception:
            self.close()
            raise

    def close(self):
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin:
                process.stdin.write("QUIT\n")
                process.stdin.flush()
                process.wait(timeout=2)
        except Exception:
            process.terminate()
        finally:
            self._is_open = False
            self._jog_active = False
            self.process = None

    def _request(self, command):
        self.open()
        with self._request_lock:
            if self.process.poll() is not None:
                detail = self.process.stderr.read().strip()
                raise RuntimeError(detail or "USB C bridge has stopped.")
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
            line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("USB C bridge returned no response.")
        response = json.loads(line)
        if response.get("system_status") is not None:
            self.system_status_code = int(response["system_status"])
        if not response.get("ok"):
            message = response.get("error") or str(response)
            if self.system_status_code == 0x0009:
                message += (
                    " (0x0009: MR-MC240N은 SSCNET 앰프 응답을 기다리는 중입니다. "
                    "컨트롤러→첫 앰프 CN1A, CN1B→다음 CN1A, 앰프 제어전원과 "
                    "축 번호 스위치를 확인하세요.)"
                )
            raise RuntimeError(message)
        return response

    @classmethod
    def system_status_text(cls, status_code):
        if status_code is None:
            return "UNKNOWN"
        code = int(status_code)
        return cls.SYSTEM_STATUS_NAMES.get(code, f"STATUS 0x{code:04X}")

    def check_connection(self):
        response = self._request("STATUS")
        self.identity = response.get("identity", "")
        self.board_signature = response.get("signature")
        self.system_status_code = response.get("system_status")
        return bool(response.get("connected"))

    def start_system(self):
        response = self._request("SYSTEM_START")
        self.system_status_code = response.get("system_status")
        return self.system_status_code

    def configure_six_axis_btk1404(self):
        response = self._request("CONFIGURE_6AXES_BTK1404")
        self.system_status_code = response.get("system_status")
        return response

    def read_feedback_position_counts(self, axis_number=None):
        axis = self.axis_number if axis_number is None else int(axis_number)
        return int(self._request(f"AXIS_STATE {axis}")["position"])

    def read_axis_status(self, axis_number=None):
        axis = self.axis_number if axis_number is None else int(axis_number)
        response = self._request(f"AXIS_STATE {axis}")
        status = {
            "axis": axis,
            "position": int(response["position"]),
            "status0": int(response["status0"]),
            "status1": int(response["status1"]),
            "servo_ready": bool(response["servo_ready"]),
            "in_position": bool(response["in_position"]),
            "servo_alarm": bool(response["servo_alarm"]),
            "operating": bool(response["operating"]),
            "home_complete": bool(response["home_complete"]),
            "operation_alarm": bool(response["operation_alarm"]),
            "operation_complete": not bool(response["operating"]),
        }
        self._update_motion_latch_from_status(status)
        return status

    def _begin_motion_dispatch(self, start_position, motion_kind):
        self._motion_seen_operating = False
        self._motion_seen_incomplete = False
        self._motion_dispatch_confirmed = False
        self._motion_start_position = int(start_position)
        self._motion_kind = str(motion_kind)
        self._motion_command_may_be_active = True

    def _confirm_motion_dispatch(self):
        self._motion_dispatch_confirmed = True

    def _clear_motion_latch(self):
        self._motion_command_may_be_active = False
        self._motion_seen_operating = False
        self._motion_seen_incomplete = False
        self._motion_dispatch_confirmed = False
        self._motion_start_position = None
        self._motion_kind = ""

    def _update_motion_latch_from_status(self, status):
        if (
            int(status["axis"]) != self.axis_number
            or not self._motion_command_may_be_active
        ):
            return
        if status["operating"]:
            self._motion_seen_operating = True
        if not status["operation_complete"]:
            self._motion_seen_incomplete = True
        position_changed = (
            self._motion_start_position is not None
            and int(status["position"]) != self._motion_start_position
        )
        position_completion_confirmed = (
            self._motion_dispatch_confirmed
            and (
                (
                    self._motion_kind == "relative"
                    and position_changed
                    and status["in_position"]
                )
                or (
                    self._motion_kind == "home"
                    and status["home_complete"]
                )
            )
        )
        completion_observed = (
            self._motion_seen_operating
            or self._motion_seen_incomplete
            or position_completion_confirmed
        )
        if (
            not status["operating"]
            and status["operation_complete"]
            and completion_observed
        ):
            self._jog_active = False
            self._clear_motion_latch()

    def set_servo_on(self, enabled):
        self._request(f"SERVO {self.axis_number} {1 if enabled else 0}")
        if not enabled:
            self._jog_active = False

    def start_jog(self, direction, speed, acceleration_ms, deceleration_ms):
        if direction not in (self.SSC_DIR_PLUS, self.SSC_DIR_MINUS):
            raise ValueError("JOG direction must be plus or minus.")
        start_position = self.read_feedback_position_counts()
        self._begin_motion_dispatch(start_position, "jog")
        self._request(
            f"JOG {self.axis_number} {int(direction)} {int(speed)} "
            f"{int(acceleration_ms)} {int(deceleration_ms)}"
        )
        self._confirm_motion_dispatch()
        self._jog_active = True

    def stop_jog(self, timeout_ms=3000):
        self._request(f"STOP {self.axis_number}")
        self._jog_active = False
        self._clear_motion_latch()

    def move_relative(self, distance_counts, speed, acceleration_ms, deceleration_ms):
        start_position = self.read_feedback_position_counts()
        self._begin_motion_dispatch(start_position, "relative")
        self._request(
            f"MOVE_RELATIVE {self.axis_number} {int(distance_counts)} {int(speed)} "
            f"{int(acceleration_ms)} {int(deceleration_ms)}"
        )
        self._confirm_motion_dispatch()

    def start_home_return(self):
        start_position = self.read_feedback_position_counts()
        self._begin_motion_dispatch(start_position, "home")
        self._request(f"HOME {self.axis_number}")
        self._confirm_motion_dispatch()

    def stop(self, rapid=False, timeout_ms=3000):
        command = "RAPID_STOP" if rapid else "STOP"
        self._request(f"{command} {self.axis_number}")
        self._jog_active = False
        self._clear_motion_latch()

    def stop_all_axes(
        self,
        axis_numbers=range(1, 7),
        rapid=True,
        timeout_ms=3000,
    ):
        """Stop every requested axis, aggregating per-axis dispatch failures."""
        command = "RAPID_STOP" if rapid else "STOP"
        failures = []
        try:
            for requested_axis in axis_numbers:
                try:
                    axis = int(requested_axis)
                    self._request(f"{command} {axis}")
                except Exception as exc:
                    failures.append((requested_axis, exc))
        finally:
            # Do not clear local state until every requested stop was attempted.
            self._jog_active = False
            self._clear_motion_latch()

        if failures:
            details = "; ".join(
                f"axis {axis}: {error}" for axis, error in failures
            )
            raise RuntimeError(
                f"MR-MC240N USB {command.lower().replace('_', ' ')} failed "
                f"for {len(failures)} axis/axes: {details}"
            )


class MrMc240nPositionController:
    """ctypes wrapper for MR-MC200 monitoring and standard-mode axis control."""

    SSC_BIT_OFF = 0
    SSC_BIT_ON = 1
    SSC_DIR_PLUS = 0
    SSC_DIR_MINUS = 1
    SSC_STS_CODE_READY_FIN = 0x0001
    SSC_STS_CODE_RUNNING = 0x000A
    SSC_STS_CODE_AXIS_UNMOUNTED = 0xE400

    # mc2xxstd.h system/axis command and status bit numbers.
    # sscGetStatusBitSignalEx uses axis 0 for system status bits.
    SSC_CMDBIT_SYS_SEMI = 17
    SSC_STSBIT_SYS_EMIO = 273
    SSC_STSBIT_SYS_TSTO = 275
    SSC_STSBIT_SYS_EMID = 279
    SSC_CMDBIT_AX_SON = 513
    SSC_STSBIT_AX_RDY = 769
    SSC_STSBIT_AX_INP = 770
    SSC_STSBIT_AX_SWRN = 775
    SSC_STSBIT_AX_ABSE = 776
    SSC_STSBIT_AX_SALM = 774
    SSC_STSBIT_AX_OP = 777
    SSC_STSBIT_AX_ZP = 780
    SSC_STSBIT_AX_OALM = 782
    SSC_STSBIT_AX_OPF = 783
    SSC_STSBIT_AX_SO = 788
    SSC_STSBIT_AX_DSTO = 791
    SSC_STSBIT_AX_ISTP = 801
    SSC_STSBIT_AX_STO = 804

    def __init__(self, board_id, axis_number, dll_path="", auto_start_system=False):
        self.board_id = int(board_id)
        self.channel = 1
        self.axis_number = int(axis_number)
        self.dll_path = dll_path.strip()
        self.auto_start_system = bool(auto_start_system)
        self.library = None
        self.loaded_library_path = ""
        self._library_validation_error = ""
        self._open_cleanup_pending = ""
        self._is_open = False
        self._system_start_attempted = False
        self.system_status_code = None
        self._servo_commanded_on = False
        self._jog_active = False
        self._motion_command_may_be_active = False
        self._motion_seen_operating = False
        self._motion_seen_incomplete = False
        self._motion_dispatch_confirmed = False
        self._motion_start_position = None
        self._motion_kind = ""

    @staticmethod
    def is_supported_platform():
        return os.name == "nt"

    def _bind_api(self, name, argtypes, restype=ctypes.c_int):
        try:
            function = getattr(self.library, name)
        except AttributeError:
            return
        function.argtypes = argtypes
        function.restype = restype

    def _library_candidates(self):
        python_is_64_bit = ctypes.sizeof(ctypes.c_void_p) == 8
        library_names = (
            ("mc2xxstd_x64.dll",)
            if python_is_64_bit
            else ("mc2xxstd_wow64.dll", "mc2xxstd.dll")
        )

        if self.dll_path:
            return [
                os.path.abspath(
                    os.path.expanduser(os.path.expandvars(self.dll_path))
                )
            ]

        module_dir = os.path.dirname(os.path.abspath(__file__))
        installed_library_dir = get_position_board_api_library_directory()
        if installed_library_dir:
            candidate_directories = [installed_library_dir]
        else:
            candidate_directories = [
                os.path.join(module_dir, "vendor", "mitsubishi"),
                module_dir,
            ]

        candidates = []
        for directory in candidate_directories:
            for library_name in library_names:
                candidates.append(os.path.join(directory, library_name))

        unique_candidates = []
        seen = set()
        for candidate in candidates:
            normalized = os.path.normcase(os.path.abspath(candidate))
            if normalized in seen:
                continue
            unique_candidates.append(candidate)
            seen.add(normalized)
        return unique_candidates

    def _load_library(self):
        if not self.is_supported_platform():
            raise RuntimeError(MR_MC240N_WINDOWS_ONLY_MESSAGE)

        if self._library_validation_error:
            raise RuntimeError(self._library_validation_error)
        if self.library is not None:
            return

        library_candidates = self._library_candidates()
        explicit_library = bool(self.dll_path)
        if explicit_library and not os.path.isfile(library_candidates[0]):
            raise RuntimeError(
                "Configured MR-MC240N API DLL was not found. "
                f"No fallback DLL was loaded: {library_candidates[0]}"
            )

        load_error = None
        architecture_errors = []
        missing_exports = []
        python_arch = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"
        for candidate in library_candidates:
            resolved_candidate = (
                os.path.abspath(candidate) if os.path.isfile(candidate) else candidate
            )
            if os.path.isfile(resolved_candidate):
                dll_arch = get_windows_pe_architecture(resolved_candidate)
                if not dll_arch:
                    architecture_errors.append(f"{candidate} is not a readable PE DLL")
                    if explicit_library:
                        break
                    continue
                if dll_arch != python_arch:
                    architecture_errors.append(
                        f"{candidate} is {dll_arch}, Python is {python_arch}"
                    )
                    if explicit_library:
                        break
                    continue
            try:
                loaded_library = ctypes.WinDLL(resolved_candidate)
                missing_exports = [
                    name
                    for name in MR_MC240N_REQUIRED_API_EXPORTS
                    if not hasattr(loaded_library, name)
                ]
                self.library = loaded_library
                self.loaded_library_path = resolved_candidate
                break
            except Exception as exc:
                load_error = exc
                if explicit_library:
                    break

        if self.library is not None and missing_exports:
            self._library_validation_error = (
                "MR-MC240N API DLL loaded but is missing required exports: "
                + ", ".join(missing_exports)
                + f". No fallback DLL was selected: {self.loaded_library_path}"
            )
            raise RuntimeError(self._library_validation_error)

        if self.library is None:
            architecture_hint = ""
            if architecture_errors:
                architecture_hint = " Architecture mismatch: " + "; ".join(architecture_errors) + "."
            raise RuntimeError(
                "MR-MC240N API library could not be loaded. "
                "Use mc2xxstd_x64.dll with 64-bit Python or "
                "mc2xxstd_wow64.dll with 32-bit Python. "
                "Install one complete Position Board Utility2 runtime; "
                "copying a DLL from a different release is not sufficient."
                + architecture_hint
            ) from load_error

        self._bind_api("sscOpen", [ctypes.c_int])
        self._bind_api("sscClose", [ctypes.c_int])
        self._bind_api("sscGetLastError", [], ctypes.c_uint32)
        self._bind_api("sscSystemStart", [ctypes.c_int, ctypes.c_int, ctypes.c_int])
        self._bind_api("sscReboot", [ctypes.c_int, ctypes.c_int, ctypes.c_int])
        self._bind_api(
            "sscResetAllParameter", [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        )
        self._bind_api(
            "sscChangeParameterEx",
            [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_ushort,
                ctypes.c_uint32,
            ],
        )
        self._bind_api(
            "sscChange2ParameterEx",
            [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_ushort),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_ubyte),
            ],
        )
        self._bind_api(
            "sscGetSystemStatusCode",
            [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_short),
            ],
        )
        self._bind_api(
            "sscGetCurrentFbPositionFast",
            [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_long)],
        )
        self._bind_api(
            "sscSetCommandBitSignalEx",
            [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )
        self._bind_api(
            "sscGetStatusBitSignalEx",
            [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ],
        )
        self._bind_api(
            "sscJogStart",
            [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_long,
                ctypes.c_short,
                ctypes.c_short,
                ctypes.c_char,
            ],
        )
        self._bind_api(
            "sscJogStop",
            [ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )
        self._bind_api(
            "sscIncStart",
            [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_long,
                ctypes.c_long,
                ctypes.c_short,
                ctypes.c_short,
            ],
        )
        self._bind_api(
            "sscHomeReturnStart",
            [ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )
        self._bind_api(
            "sscDriveStop",
            [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )
        self._bind_api(
            "sscDriveRapidStop",
            [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )

    def _raise_api_error(self, action, status_code):
        detailed_error = None
        if self.library is not None:
            try:
                detailed_error = self.library.sscGetLastError()
            except Exception:
                detailed_error = None

        if detailed_error is None:
            raise RuntimeError(f"{action} failed. API status={status_code}.")
        recovery_hint = ""
        if (int(detailed_error) & 0xFFFFFFFF) == 0x00060030:
            recovery_hint = (
                " Motion was not accepted. Check the MR-J4 amplifier display "
                "and emergency-stop/limit inputs, remove the alarm cause, then "
                "reset the amplifier alarm (or power-cycle it safely). Confirm "
                "Servo Ready before retrying; reconnecting the app or replacing "
                "the DLL will not clear a drive alarm."
            )
        raise RuntimeError(
            f"{action} failed. API status={status_code}, "
            f"detail={describe_mr_mc240n_api_error(detailed_error)}. "
            + recovery_hint
            + " Run `python mr_mc240n_pcie_check.py`; keep the API DLL and "
            "Mitsubishi drivers from the same Utility2 installation."
        )

    def _ensure_axis_alarm_free(self):
        """Reject motion before dispatch when the board exposes an axis alarm."""
        servo_alarm = self.get_axis_status_bit(self.SSC_STSBIT_AX_SALM)
        operation_alarm = self.get_axis_status_bit(self.SSC_STSBIT_AX_OALM)
        if servo_alarm or operation_alarm:
            alarms = []
            if servo_alarm:
                alarms.append("servo alarm")
            if operation_alarm:
                alarms.append("operation alarm")
            raise RuntimeError(
                f"Axis {self.axis_number} has an active {' and '.join(alarms)}. "
                "Motion was not sent. Check the MR-J4 amplifier alarm code, "
                "emergency-stop and limit inputs, clear the cause, reset the "
                "amplifier alarm, and confirm Servo Ready before retrying."
            )

    def _get_api(self, name):
        if self.library is None:
            raise RuntimeError("MR-MC240N API library is not loaded.")
        try:
            return getattr(self.library, name)
        except AttributeError as exc:
            raise RuntimeError(
                f"{name} is not available in this MR-MC200 API DLL. "
                "Install the API library supplied with the current Position Board Utility2."
            ) from exc

    def _call_api(self, name, *args, allow_cleanup_pending=False):
        if allow_cleanup_pending and self._open_cleanup_pending:
            if not self._is_open:
                raise RuntimeError(self._open_cleanup_pending)
        else:
            self.open()
        status = self._get_api(name)(*args)
        if status != 0:
            self._raise_api_error(name, status)

    def open(self):
        self._load_library()
        if self._open_cleanup_pending:
            raise RuntimeError(self._open_cleanup_pending)
        if self._is_open:
            return

        status = self.library.sscOpen(self.board_id)
        if status != 0:
            self._raise_api_error("sscOpen", status)

        self._is_open = True
        try:
            self.ensure_running_if_requested()
        except Exception as start_error:
            cleanup_error = ""
            try:
                close_status = self.library.sscClose(self.board_id)
                if close_status != 0:
                    cleanup_error = f"sscClose returned {close_status}"
            except Exception as close_error:
                cleanup_error = f"sscClose raised {close_error}"

            if not cleanup_error:
                self._is_open = False
                self._system_start_attempted = False
                raise
            self._open_cleanup_pending = (
                f"{start_error} Cleanup also failed ({cleanup_error}); "
                "the board may still be open, so call close() again."
            )
            raise RuntimeError(self._open_cleanup_pending) from start_error

    def close(self):
        if not self._is_open or self.library is None:
            return
        try:
            status = self.library.sscClose(self.board_id)
        except Exception as exc:
            self._open_cleanup_pending = (
                f"sscClose raised {exc}; the board may still be open. "
                "Retry Rapid Stop/close before other API calls."
            )
            raise
        if status != 0:
            self._open_cleanup_pending = (
                f"sscClose failed with API status {status}; the board may "
                "still be open. Retry Rapid Stop/close before other API calls."
            )
            self._raise_api_error("sscClose", status)
        self._is_open = False
        self._open_cleanup_pending = ""
        self._system_start_attempted = False
        self.system_status_code = None
        self._servo_commanded_on = False
        self._jog_active = False

    def ensure_running_if_requested(self):
        if not self.auto_start_system or self._system_start_attempted:
            return

        self.system_status_code = self.get_system_status_code()
        self._system_start_attempted = True
        if self.system_status_code == self.SSC_STS_CODE_RUNNING:
            return
        if self.system_status_code != self.SSC_STS_CODE_READY_FIN:
            if self.system_status_code == self.SSC_STS_CODE_AXIS_UNMOUNTED:
                raise RuntimeError(
                    "sscSystemStart was not sent: system status 0xE400 means "
                    "AXIS UNMOUNTED. The axes enabled in the board parameters do "
                    "not match the SSCNET amplifiers that responded. In PB Test, "
                    "open System Diagnosis and run 'Search the axes'; verify CN1A/"
                    "CN1B wiring, amplifier control power, and unique amplifier "
                    "axis-number switches. Apply the corrected project parameters "
                    "before System Start. This is not a servo alarm."
                )
            raise RuntimeError(
                "sscSystemStart was not sent because the system is neither "
                "preparation-complete nor already running "
                f"(status=0x{self.system_status_code:04X}). "
                "Mitsubishi requires a system reboot before starting again."
            )
        status = self._get_api("sscSystemStart")(self.board_id, self.channel, 0)
        if status != 0:
            self._raise_api_error("sscSystemStart", status)
        self.system_status_code = self.SSC_STS_CODE_RUNNING

    def apply_parameter_file_and_start(self, parameter_path):
        """Apply a PB Test parameter file using Mitsubishi's documented flow.

        This is intentionally an explicit operation because it reboots the
        running channel, resets its RAM parameters, and stops every axis.
        Parameters are not saved to flash; the selected project file remains
        the source of truth for each explicit application.
        """
        if self._motion_command_may_be_active or self._jog_active:
            raise RuntimeError(
                "Project parameters cannot be applied while a motion command "
                "may still be active. Stop the axis first."
            )
        parameter_groups = load_mr_mc240n_parameter_file(parameter_path)
        self.open()

        current_status = self.get_system_status_code()
        if current_status != self.SSC_STS_CODE_READY_FIN:
            reboot_status = self._get_api("sscReboot")(
                self.board_id, self.channel, 0
            )
            if reboot_status != 0:
                self._raise_api_error("sscReboot", reboot_status)
        self._system_start_attempted = False
        self.system_status_code = self.SSC_STS_CODE_READY_FIN

        reset_status = self._get_api("sscResetAllParameter")(
            self.board_id, self.channel, 0
        )
        if reset_status != 0:
            self._raise_api_error("sscResetAllParameter", reset_status)

        def write_group(target, parameters):
            if target == 0:
                for parameter_number, parameter_value in parameters:
                    api_status = self._get_api("sscChangeParameterEx")(
                        self.board_id,
                        self.channel,
                        target,
                        parameter_number,
                        parameter_value,
                    )
                    if api_status != 0:
                        self._raise_api_error(
                            f"sscChangeParameterEx(target={target}, "
                            f"parameter=0x{parameter_number:04X})",
                            api_status,
                        )
                return len(parameters)

            written = 0
            for index in range(0, len(parameters), 2):
                pair = parameters[index : index + 2]
                if len(pair) == 1:
                    # Vendor API documents parameter No. 0 as the unused slot.
                    pair.append((0, 0))
                parameter_numbers = (ctypes.c_ushort * 2)(
                    pair[0][0], pair[1][0]
                )
                parameter_values = (ctypes.c_uint32 * 2)(
                    pair[0][1], pair[1][1]
                )
                write_statuses = (ctypes.c_ubyte * 2)()
                api_status = self._get_api("sscChange2ParameterEx")(
                    self.board_id,
                    self.channel,
                    target,
                    parameter_numbers,
                    parameter_values,
                    write_statuses,
                )
                if api_status != 0:
                    self._raise_api_error(
                        f"sscChange2ParameterEx(target={target}, "
                        f"parameter=0x{pair[0][0]:04X})",
                        api_status,
                    )
                written += min(2, len(parameters) - index)
            return written

        total_written = 0
        worker_count = min(len(parameter_groups), 37)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(write_group, target, parameters): target
                for target, parameters in parameter_groups.items()
            }
            for future in as_completed(futures):
                total_written += future.result()

        start_status = self._get_api("sscSystemStart")(
            self.board_id, self.channel, 0
        )
        self._system_start_attempted = True
        if start_status != 0:
            self._raise_api_error("sscSystemStart", start_status)
        self.system_status_code = self.get_system_status_code()
        if self.system_status_code != self.SSC_STS_CODE_RUNNING:
            raise RuntimeError(
                "System Start returned success, but the MR-MC240N did not "
                f"enter RUNNING state (status=0x{self.system_status_code:04X})."
            )
        applied_system_status = self.read_system_status()
        requested_emi_bypass = any(
            parameter_number == 0x000E and parameter_value == 0x5AE1
            for parameter_number, parameter_value in parameter_groups.get(0, ())
        )
        if (
            requested_emi_bypass
            and not applied_system_status["external_forced_stop_disabled"]
        ):
            raise RuntimeError(
                "CTR parameters were written and System Start completed, but "
                "EMID is still OFF. The external-EMI bypass was not accepted."
            )
        return {
            "parameter_count": total_written,
            "target_count": len(parameter_groups),
            "system_status": self.system_status_code,
            "system_bits": applied_system_status,
        }

    def get_system_status_code(self):
        """Read the current channel state without issuing System Start."""
        self.open()
        system_status = ctypes.c_short()
        status = self._get_api("sscGetSystemStatusCode")(
            self.board_id,
            self.channel,
            ctypes.byref(system_status),
        )
        if status != 0:
            self._raise_api_error("sscGetSystemStatusCode", status)
        self.system_status_code = int(system_status.value) & 0xFFFF
        return self.system_status_code

    def read_feedback_position_counts(self, axis_number=None):
        self.open()
        axis = self.axis_number if axis_number is None else int(axis_number)

        position = ctypes.c_long()
        status = self.library.sscGetCurrentFbPositionFast(
            self.board_id,
            self.channel,
            axis,
            ctypes.byref(position),
        )
        if status == 0:
            return int(position.value)

        if self.auto_start_system and not self._system_start_attempted:
            self.ensure_running_if_requested()
            status = self.library.sscGetCurrentFbPositionFast(
                self.board_id,
                self.channel,
                axis,
                ctypes.byref(position),
            )
            if status == 0:
                return int(position.value)

        self._raise_api_error("sscGetCurrentFbPositionFast", status)

    @staticmethod
    def _validate_motion_values(speed, acceleration_ms, deceleration_ms):
        speed = int(speed)
        acceleration_ms = int(acceleration_ms)
        deceleration_ms = int(deceleration_ms)
        if not 1 <= speed <= 2_147_483_647:
            raise ValueError("Speed must be between 1 and 2147483647 board speed units.")
        if not 0 <= acceleration_ms <= 20_000:
            raise ValueError("Acceleration time must be between 0 and 20000 ms.")
        if not 0 <= deceleration_ms <= 20_000:
            raise ValueError("Deceleration time must be between 0 and 20000 ms.")
        return speed, acceleration_ms, deceleration_ms

    def set_servo_on(self, enabled):
        if enabled:
            system_status = self.get_system_status_code()
            if system_status != self.SSC_STS_CODE_RUNNING:
                if system_status == self.SSC_STS_CODE_READY_FIN:
                    raise RuntimeError(
                        "Servo ON was not sent: the MR-MC240N system is prepared "
                        "but not running. Enable 'PCIe: start system if preparation "
                        "is complete', reconnect the board, then retry."
                    )
                if system_status == self.SSC_STS_CODE_AXIS_UNMOUNTED:
                    raise RuntimeError(
                        "Servo ON was not sent: system status 0xE400 is AXIS "
                        "UNMOUNTED, not an amplifier alarm. Match the configured "
                        "axes to the connected amplifiers in PB Test System "
                        "Diagnosis ('Search the axes'), then perform System Start."
                    )
                raise RuntimeError(
                    "Servo ON was not sent because the MR-MC240N system is not "
                    f"running (status=0x{system_status:04X}). Check SSCNET and "
                    "the amplifier control power before retrying."
                )
            self.read_feedback_position_counts()
            # PB Test releases its software forced-stop command before SON.
            # The API application must explicitly do the same; merely reading
            # TOOL_MODE=1 from a .pbp2 file does not change this command bit.
            self.set_software_forced_stop(False)
            stop_status = self.read_system_status()
            if stop_status["forced_stop_active"]:
                external_hint = (
                    " The CTR project external-EMI bypass is not active "
                    "(EMID=OFF); use 'Apply CTR Project + Start' first."
                    if not stop_status["external_forced_stop_disabled"]
                    else " Check the board system status and forced-stop factor."
                )
                raise RuntimeError(
                    "Servo ON was not sent. Software forced stop SEMI was "
                    "released, but the board still reports EMIO=ON."
                    + external_hint
                )
        bit_value = self.SSC_BIT_ON if enabled else self.SSC_BIT_OFF
        self._call_api(
            "sscSetCommandBitSignalEx",
            self.board_id,
            self.channel,
            self.axis_number,
            self.SSC_CMDBIT_AX_SON,
            bit_value,
        )
        self._servo_commanded_on = bool(enabled)
        if not enabled:
            self._jog_active = False

    def set_software_forced_stop(self, enabled):
        """Set the system SEMI command bit (axis No. 0)."""
        bit_value = self.SSC_BIT_ON if enabled else self.SSC_BIT_OFF
        self._call_api(
            "sscSetCommandBitSignalEx",
            self.board_id,
            self.channel,
            0,
            self.SSC_CMDBIT_SYS_SEMI,
            bit_value,
        )

    def engage_software_forced_stop(self):
        """Engage SEMI and confirm that the board entered forced stop."""
        self.set_software_forced_stop(True)
        system_status = self.read_system_status()
        if not system_status["forced_stop_active"]:
            raise RuntimeError(
                "Software forced stop SEMI=ON was sent, but EMIO did not turn ON."
            )
        self._jog_active = False
        self._clear_motion_latch()
        return system_status

    def get_axis_status_bit(self, bit_number, axis_number=None):
        self.open()
        axis = self.axis_number if axis_number is None else int(axis_number)
        return self._get_status_bit(bit_number, axis)

    def get_system_status_bit(self, bit_number):
        self.open()
        return self._get_status_bit(bit_number, 0)

    def _get_status_bit(self, bit_number, axis_number):
        bit_status = ctypes.c_int()
        status = self._get_api("sscGetStatusBitSignalEx")(
            self.board_id,
            self.channel,
            int(axis_number),
            int(bit_number),
            ctypes.byref(bit_status),
        )
        if status != 0:
            self._raise_api_error("sscGetStatusBitSignalEx", status)
        return bool(bit_status.value)

    def read_system_status(self):
        """Read system stop/test-mode signals using axis number 0."""
        return {
            "forced_stop_active": self.get_system_status_bit(
                self.SSC_STSBIT_SYS_EMIO
            ),
            "test_mode_active": self.get_system_status_bit(
                self.SSC_STSBIT_SYS_TSTO
            ),
            "external_forced_stop_disabled": self.get_system_status_bit(
                self.SSC_STSBIT_SYS_EMID
            ),
        }

    def read_axis_status(self, axis_number=None):
        axis = self.axis_number if axis_number is None else int(axis_number)
        status_bits = {
            "servo_ready": self.SSC_STSBIT_AX_RDY,
            "in_position": self.SSC_STSBIT_AX_INP,
            "servo_alarm": self.SSC_STSBIT_AX_SALM,
            "operating": self.SSC_STSBIT_AX_OP,
            "home_complete": self.SSC_STSBIT_AX_ZP,
            "operation_alarm": self.SSC_STSBIT_AX_OALM,
            "operation_complete": self.SSC_STSBIT_AX_OPF,
            "servo_warning": self.SSC_STSBIT_AX_SWRN,
            "absolute_encoder_error": self.SSC_STSBIT_AX_ABSE,
            # The vendor abbreviations below are easy to misread: SO is
            # incremental-feed mode, DSTO is home-position-reset mode, and
            # STO is start-up acceptance completed (not Safe Torque Off).
            "incremental_feed_mode": self.SSC_STSBIT_AX_SO,
            "home_reset_mode": self.SSC_STSBIT_AX_DSTO,
            "interlock_stop": self.SSC_STSBIT_AX_ISTP,
            "startup_accepted": self.SSC_STSBIT_AX_STO,
        }
        status = {
            name: self.get_axis_status_bit(bit_number, axis)
            for name, bit_number in status_bits.items()
        }
        status["axis"] = axis
        status["position"] = self.read_feedback_position_counts(axis)
        self._update_motion_latch_from_status(status)
        return status

    @staticmethod
    def axis_not_ready_reasons(status, system_status=None):
        """Return actionable reasons exposed by axis status bits."""
        reasons = []
        system_status = system_status or {}
        if system_status.get("forced_stop_active"):
            reasons.append("forced stop is active (EMIO=ON)")
        for key, text in (
            ("servo_alarm", "servo alarm"),
            ("operation_alarm", "operation alarm"),
            ("absolute_encoder_error", "absolute encoder error"),
            ("servo_warning", "servo warning"),
            ("interlock_stop", "interlock stop active (ISTP=ON)"),
        ):
            if status.get(key):
                reasons.append(text)
        if not status.get("servo_ready"):
            reasons.append(
                "servo ready (RDY) is off; after SON, check amplifier main-circuit "
                "power, CN8 STO1/STO2 (or the supplied short-circuit connector "
                "when STO is unused), and EM2"
            )
        return reasons

    def _begin_motion_dispatch(self, start_position, motion_kind):
        self._motion_seen_operating = False
        self._motion_seen_incomplete = False
        self._motion_dispatch_confirmed = False
        self._motion_start_position = int(start_position)
        self._motion_kind = str(motion_kind)
        self._motion_command_may_be_active = True

    def _confirm_motion_dispatch(self):
        self._motion_dispatch_confirmed = True

    def _clear_motion_latch(self):
        self._motion_command_may_be_active = False
        self._motion_seen_operating = False
        self._motion_seen_incomplete = False
        self._motion_dispatch_confirmed = False
        self._motion_start_position = None
        self._motion_kind = ""

    def _update_motion_latch_from_status(self, status):
        if (
            int(status["axis"]) != self.axis_number
            or not self._motion_command_may_be_active
        ):
            return
        if status["operating"]:
            self._motion_seen_operating = True
        if not status["operation_complete"]:
            self._motion_seen_incomplete = True
        position_changed = (
            self._motion_start_position is not None
            and int(status["position"]) != self._motion_start_position
        )
        position_completion_confirmed = (
            self._motion_dispatch_confirmed
            and (
                (
                    self._motion_kind == "relative"
                    and position_changed
                    and status["in_position"]
                )
                or (
                    self._motion_kind == "home"
                    and status["home_complete"]
                )
            )
        )
        completion_observed = (
            self._motion_seen_operating
            or self._motion_seen_incomplete
            or position_completion_confirmed
        )
        if (
            not status["operating"]
            and status["operation_complete"]
            and completion_observed
        ):
            self._jog_active = False
            self._clear_motion_latch()

    def start_jog(self, direction, speed, acceleration_ms, deceleration_ms):
        speed, acceleration_ms, deceleration_ms = self._validate_motion_values(
            speed, acceleration_ms, deceleration_ms
        )
        if direction not in (self.SSC_DIR_PLUS, self.SSC_DIR_MINUS):
            raise ValueError("Jog direction must be SSC_DIR_PLUS or SSC_DIR_MINUS.")

        self._ensure_axis_alarm_free()
        start_position = self.read_feedback_position_counts()
        self._begin_motion_dispatch(start_position, "jog")
        self._call_api(
            "sscJogStart",
            self.board_id,
            self.channel,
            self.axis_number,
            speed,
            acceleration_ms,
            deceleration_ms,
            bytes([direction]),
        )
        self._confirm_motion_dispatch()
        self._jog_active = True

    def stop_jog(self, timeout_ms=3000):
        # The public method retains timeout_ms for the shared USB/PCIe interface.
        # Mitsubishi's PCIe sscJogStop ABI has exactly three arguments.
        timeout_ms = int(timeout_ms)
        if not 0 <= timeout_ms <= 65_535:
            raise ValueError("Stop timeout must be between 0 and 65535 ms.")
        self._call_api(
            "sscJogStop",
            self.board_id,
            self.channel,
            self.axis_number,
            allow_cleanup_pending=True,
        )
        self._jog_active = False
        self._clear_motion_latch()

    def move_relative(self, distance_counts, speed, acceleration_ms, deceleration_ms):
        self._ensure_axis_alarm_free()
        start_position = self.read_feedback_position_counts()
        distance_counts = int(distance_counts)
        if not -2_147_483_647 <= distance_counts <= 2_147_483_647:
            raise ValueError("Relative distance exceeds the signed 32-bit command range.")
        if distance_counts == 0:
            raise ValueError("Relative distance must not be zero.")
        speed, acceleration_ms, deceleration_ms = self._validate_motion_values(
            speed, acceleration_ms, deceleration_ms
        )

        self._begin_motion_dispatch(start_position, "relative")
        self._call_api(
            "sscIncStart",
            self.board_id,
            self.channel,
            self.axis_number,
            distance_counts,
            speed,
            acceleration_ms,
            deceleration_ms,
        )
        self._confirm_motion_dispatch()

    def start_home_return(self):
        self._ensure_axis_alarm_free()
        start_position = self.read_feedback_position_counts()
        self._begin_motion_dispatch(start_position, "home")
        self._call_api(
            "sscHomeReturnStart",
            self.board_id,
            self.channel,
            self.axis_number,
        )
        self._confirm_motion_dispatch()

    def stop(self, rapid=False, timeout_ms=3000):
        timeout_ms = int(timeout_ms)
        if not 0 <= timeout_ms <= 65_535:
            raise ValueError("Stop timeout must be between 0 and 65535 ms.")

        if not rapid and (self._jog_active or self._motion_kind == "jog"):
            try:
                self.stop_jog(timeout_ms=timeout_ms)
                return {"mode": "jog stop", "escalated": False}
            except Exception as stop_error:
                self.engage_software_forced_stop()
                return {
                    "mode": "software forced stop",
                    "escalated": True,
                    "primary_error": str(stop_error),
                }

        api_name = "sscDriveRapidStop" if rapid else "sscDriveStop"
        try:
            self._call_api(
                api_name,
                self.board_id,
                self.channel,
                self.axis_number,
                timeout_ms,
                allow_cleanup_pending=True,
            )
            still_operating = self.get_axis_status_bit(self.SSC_STSBIT_AX_OP)
        except Exception as stop_error:
            self.engage_software_forced_stop()
            return {
                "mode": "software forced stop",
                "escalated": True,
                "primary_error": str(stop_error),
            }
        if still_operating:
            self.engage_software_forced_stop()
            return {
                "mode": "software forced stop",
                "escalated": True,
                "primary_error": f"{api_name} returned but OP remained ON",
            }
        self._jog_active = False
        self._clear_motion_latch()
        return {
            "mode": "rapid stop" if rapid else "drive stop",
            "escalated": False,
        }

    def stop_all_axes(
        self,
        axis_numbers=range(1, 7),
        rapid=True,
        timeout_ms=3000,
    ):
        """Stop every requested axis, aggregating per-axis dispatch failures."""
        timeout_ms = int(timeout_ms)
        if not 0 <= timeout_ms <= 65_535:
            raise ValueError("Stop timeout must be between 0 and 65535 ms.")

        software_stop_error = None
        if rapid:
            try:
                self.engage_software_forced_stop()
                return {
                    "mode": "software forced stop",
                    "escalated": False,
                }
            except Exception as exc:
                software_stop_error = exc

        api_name = "sscDriveRapidStop" if rapid else "sscDriveStop"
        failures = []
        try:
            for requested_axis in axis_numbers:
                try:
                    axis = int(requested_axis)
                    self._call_api(
                        api_name,
                        self.board_id,
                        self.channel,
                        axis,
                        timeout_ms,
                        allow_cleanup_pending=True,
                    )
                except Exception as exc:
                    failures.append((requested_axis, exc))
        finally:
            # Do not clear local state until every requested stop was attempted.
            self._jog_active = False
            self._clear_motion_latch()

        if failures:
            details = "; ".join(
                f"axis {axis}: {error}" for axis, error in failures
            )
            stop_kind = "rapid stop" if rapid else "stop"
            software_detail = (
                f" Software forced stop also failed ({software_stop_error})."
                if software_stop_error is not None
                else ""
            )
            raise RuntimeError(
                f"MR-MC240N PCIe {stop_kind} failed for "
                f"{len(failures)} axis/axes: {details}." + software_detail
            )
        return {
            "mode": "per-axis rapid stop fallback" if rapid else "drive stop",
            "escalated": software_stop_error is not None,
            "primary_error": (
                str(software_stop_error)
                if software_stop_error is not None
                else ""
            ),
        }


# Backward-compatible name for integrations that imported the original monitor.
MrMc240nPositionMonitor = MrMc240nPositionController
