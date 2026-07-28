import ctypes
import json
import os
import platform
import re
import struct
import subprocess
import threading

MR_MC240N_WINDOWS_ONLY_MESSAGE = "MR-MC240N position board monitoring is supported only on Windows."
MR_MC240N_USB_VID = "06D3"
MR_MC240N_USB_PID = "01D1"
MR_MC240N_USB_INSTANCE_PREFIX = f"USB\\VID_{MR_MC240N_USB_VID}&PID_{MR_MC240N_USB_PID}"
MR_MC240N_USB_DEVICE_NAME = "MITSUBISHI SSCNET Unit USB Controller"
MR_CONNECTION_USB_MAINTENANCE = "USB controller (direct)"
MR_CONNECTION_PCIE_API = "PCIe control (API)"

MR_MC240N_API_ERROR_MESSAGES = {
    0x00021010: "position board not found (SSC_FUNC_ERR_NOT_FOUND_BOARD)",
}


def describe_mr_mc240n_api_error(error_code):
    error_code = int(error_code)
    description = MR_MC240N_API_ERROR_MESSAGES.get(error_code, "unknown API error")
    return f"0x{error_code:08X}: {description}"


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
        return {
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

    def set_servo_on(self, enabled):
        self._request(f"SERVO {self.axis_number} {1 if enabled else 0}")
        if not enabled:
            self._jog_active = False

    def start_jog(self, direction, speed, acceleration_ms, deceleration_ms):
        if direction not in (self.SSC_DIR_PLUS, self.SSC_DIR_MINUS):
            raise ValueError("JOG direction must be plus or minus.")
        self._request(
            f"JOG {self.axis_number} {int(direction)} {int(speed)} "
            f"{int(acceleration_ms)} {int(deceleration_ms)}"
        )
        self._jog_active = True

    def stop_jog(self, timeout_ms=3000):
        self._request(f"STOP {self.axis_number}")
        self._jog_active = False

    def move_relative(self, distance_counts, speed, acceleration_ms, deceleration_ms):
        self._request(
            f"MOVE_RELATIVE {self.axis_number} {int(distance_counts)} {int(speed)} "
            f"{int(acceleration_ms)} {int(deceleration_ms)}"
        )

    def start_home_return(self):
        self._request(f"HOME {self.axis_number}")

    def stop(self, rapid=False, timeout_ms=3000):
        command = "RAPID_STOP" if rapid else "STOP"
        self._request(f"{command} {self.axis_number}")
        self._jog_active = False


class MrMc240nPositionController:
    """ctypes wrapper for MR-MC200 monitoring and standard-mode axis control."""

    DEFAULT_LIBRARY_CANDIDATES = (
        "mc2xxstd_x64.dll",
        "mc2xxstd.dll",
    )

    SSC_BIT_OFF = 0
    SSC_BIT_ON = 1
    SSC_DIR_PLUS = 0
    SSC_DIR_MINUS = 1

    # mc2xxstd.h axis command/status bit numbers.
    SSC_CMDBIT_AX_SON = 1
    SSC_STSBIT_AX_RDY = 1
    SSC_STSBIT_AX_INP = 2
    SSC_STSBIT_AX_SALM = 6
    SSC_STSBIT_AX_OP = 9
    SSC_STSBIT_AX_ZP = 12
    SSC_STSBIT_AX_OALM = 14
    SSC_STSBIT_AX_OPF = 15

    def __init__(self, board_id, axis_number, dll_path="", auto_start_system=False):
        self.board_id = int(board_id)
        self.channel = 1
        self.axis_number = int(axis_number)
        self.dll_path = dll_path.strip()
        self.auto_start_system = bool(auto_start_system)
        self.library = None
        self._is_open = False
        self._system_start_attempted = False
        self._servo_commanded_on = False
        self._jog_active = False

    @staticmethod
    def is_supported_platform():
        return os.name == "nt"

    def _bind_api(self, name, argtypes):
        try:
            function = getattr(self.library, name)
        except AttributeError:
            return
        function.argtypes = argtypes
        function.restype = ctypes.c_int

    def _load_library(self):
        if not self.is_supported_platform():
            raise RuntimeError(MR_MC240N_WINDOWS_ONLY_MESSAGE)

        if self.library is not None:
            return

        library_candidates = []
        if self.dll_path:
            library_candidates.append(os.path.abspath(os.path.expandvars(self.dll_path)))
        module_dir = os.path.dirname(os.path.abspath(__file__))
        if platform.architecture()[0] == "64bit":
            library_names = ["mc2xxstd_x64.dll", "mc2xxstd.dll"]
        else:
            library_names = ["mc2xxstd.dll", "mc2xxstd_x64.dll"]
        for library_name in library_names:
            library_candidates.extend(
                [
                    os.path.join(
                        module_dir,
                        "vendor",
                        "mitsubishi",
                        library_name,
                    ),
                    os.path.join(module_dir, library_name),
                    library_name,
                ]
            )

        load_error = None
        architecture_errors = []
        python_arch = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"
        for candidate in library_candidates:
            resolved_candidate = os.path.abspath(candidate) if os.path.isfile(candidate) else candidate
            if os.path.isfile(resolved_candidate):
                dll_arch = get_windows_pe_architecture(resolved_candidate)
                if dll_arch and dll_arch != python_arch:
                    architecture_errors.append(f"{candidate} is {dll_arch}, Python is {python_arch}")
                    continue
            try:
                self.library = ctypes.WinDLL(resolved_candidate)
                break
            except Exception as exc:
                load_error = exc

        if self.library is None:
            architecture_hint = ""
            if architecture_errors:
                architecture_hint = " Architecture mismatch: " + "; ".join(architecture_errors) + "."
            raise RuntimeError(
                "MR-MC240N API library could not be loaded. "
                "Use mc2xxstd_x64.dll with 64-bit Python or mc2xxstd.dll with 32-bit Python."
                + architecture_hint
            ) from load_error

        self._bind_api("sscOpen", [ctypes.c_int])
        self._bind_api("sscClose", [ctypes.c_int])
        self._bind_api("sscGetLastError", [])
        self._bind_api("sscSystemStart", [ctypes.c_int, ctypes.c_int, ctypes.c_int])
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
            [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
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
        raise RuntimeError(
            f"{action} failed. API status={status_code}, "
            f"detail={describe_mr_mc240n_api_error(detailed_error)}."
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

    def _call_api(self, name, *args):
        self.open()
        status = self._get_api(name)(*args)
        if status != 0:
            self._raise_api_error(name, status)

    def open(self):
        self._load_library()
        if self._is_open:
            return

        status = self.library.sscOpen(self.board_id)
        if status != 0:
            self._raise_api_error("sscOpen", status)

        self._is_open = True

    def close(self):
        if not self._is_open or self.library is None:
            return
        try:
            status = self.library.sscClose(self.board_id)
            if status != 0:
                self._raise_api_error("sscClose", status)
        finally:
            self._is_open = False
            self._system_start_attempted = False
            self._servo_commanded_on = False
            self._jog_active = False

    def ensure_running_if_requested(self):
        if not self.auto_start_system or self._system_start_attempted:
            return

        status = self.library.sscSystemStart(self.board_id, self.channel, 0)
        self._system_start_attempted = True
        if status != 0:
            self._raise_api_error("sscSystemStart", status)

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
            self.read_feedback_position_counts()
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

    def get_axis_status_bit(self, bit_number, axis_number=None):
        self.open()
        axis = self.axis_number if axis_number is None else int(axis_number)
        bit_status = ctypes.c_int()
        status = self._get_api("sscGetStatusBitSignalEx")(
            self.board_id,
            self.channel,
            axis,
            int(bit_number),
            ctypes.byref(bit_status),
        )
        if status != 0:
            self._raise_api_error("sscGetStatusBitSignalEx", status)
        return bool(bit_status.value)

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
        }
        status = {
            name: self.get_axis_status_bit(bit_number, axis)
            for name, bit_number in status_bits.items()
        }
        status["axis"] = axis
        status["position"] = self.read_feedback_position_counts(axis)
        return status

    def start_jog(self, direction, speed, acceleration_ms, deceleration_ms):
        self.read_feedback_position_counts()
        speed, acceleration_ms, deceleration_ms = self._validate_motion_values(
            speed, acceleration_ms, deceleration_ms
        )
        if direction not in (self.SSC_DIR_PLUS, self.SSC_DIR_MINUS):
            raise ValueError("Jog direction must be SSC_DIR_PLUS or SSC_DIR_MINUS.")

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
        self._jog_active = True

    def stop_jog(self, timeout_ms=3000):
        timeout_ms = int(timeout_ms)
        if not 0 <= timeout_ms <= 65_535:
            raise ValueError("Stop timeout must be between 0 and 65535 ms.")
        self._call_api(
            "sscJogStop",
            self.board_id,
            self.channel,
            self.axis_number,
            timeout_ms,
        )
        self._jog_active = False

    def move_relative(self, distance_counts, speed, acceleration_ms, deceleration_ms):
        self.read_feedback_position_counts()
        distance_counts = int(distance_counts)
        if not -2_147_483_647 <= distance_counts <= 2_147_483_647:
            raise ValueError("Relative distance exceeds the signed 32-bit command range.")
        if distance_counts == 0:
            raise ValueError("Relative distance must not be zero.")
        speed, acceleration_ms, deceleration_ms = self._validate_motion_values(
            speed, acceleration_ms, deceleration_ms
        )

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

    def start_home_return(self):
        self.read_feedback_position_counts()
        self._call_api(
            "sscHomeReturnStart",
            self.board_id,
            self.channel,
            self.axis_number,
        )

    def stop(self, rapid=False, timeout_ms=3000):
        timeout_ms = int(timeout_ms)
        if not 0 <= timeout_ms <= 65_535:
            raise ValueError("Stop timeout must be between 0 and 65535 ms.")
        api_name = "sscDriveRapidStop" if rapid else "sscDriveStop"
        self._call_api(
            api_name,
            self.board_id,
            self.channel,
            self.axis_number,
            timeout_ms,
        )
        self._jog_active = False


# Backward-compatible name for integrations that imported the original monitor.
MrMc240nPositionMonitor = MrMc240nPositionController
