"""Standalone MR-MC240N PCIe connection diagnostic.

This utility performs read-only host diagnostics and calls only sscOpen/sscClose.
It never starts the position-board system and never sends servo or motion commands.
"""

import argparse
import ctypes
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hardware import MR_MC240N_REQUIRED_API_EXPORTS

try:
    import winreg
except ImportError:
    winreg = None


BOARD_IDS = range(4)
MIN_SECURE_UTILITY2_VERSION = (3, 50)
KNOWN_API_ERRORS = {
    0xFFFFFFFF: "Unknown API failure (SSC_FUNC_ERR_UNKNOWN)",
    0xFFFFFFFE: "Unsupported operating system (SSC_FUNC_ERR_UNSURPORT_OS)",
    0x00000100: "API argument combination mismatch (SSC_FUNC_ERR_ARGUMENT_MISMATCH)",
    0x00020000: "Board is already open (SSC_FUNC_ERR_REOPEN)",
    0x00020010: "Board is not open (SSC_FUNC_ERR_UNOPEN)",
    0x00021010: "Position board not found (SSC_FUNC_ERR_NOT_FOUND_BOARD)",
    0x00021011: "Could not read the channel count (SSC_FUNC_ERR_GET_CHANNEL_NUM)",
    0x00021012: "Unsupported device driver (SSC_FUNC_ERR_UNSUPPORT_DEVICE_DRIVER)",
    0x00023000: "Device-driver operation failed (SSC_FUNC_ERR_DEVICE_DRIVER)",
    0x00030000: "System is not preparation-complete (SSC_FUNC_ERR_UNREADY_CHANNEL)",
    0x00030010: "Channel is already configured (SSC_FUNC_ERR_ALREADY_CHANNEL)",
    0x00030020: "System is waiting for System Start (SSC_FUNC_ERR_RUNNING_CHANNEL)",
    0x00030030: "Position-board system alarm is active (SSC_FUNC_ERR_NOW_ALARM_SYSTEM)",
}
KNOWN_API_ERRORS.update(
    {
        argument_number: (
            f"API argument {argument_number} is invalid "
            f"(SSC_FUNC_ERR_ARGUMENT_{argument_number:02d})"
        )
        for argument_number in range(1, 10)
    }
)


class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwStrucVersion", ctypes.c_uint32),
        ("dwFileVersionMS", ctypes.c_uint32),
        ("dwFileVersionLS", ctypes.c_uint32),
        ("dwProductVersionMS", ctypes.c_uint32),
        ("dwProductVersionLS", ctypes.c_uint32),
        ("dwFileFlagsMask", ctypes.c_uint32),
        ("dwFileFlags", ctypes.c_uint32),
        ("dwFileOS", ctypes.c_uint32),
        ("dwFileType", ctypes.c_uint32),
        ("dwFileSubtype", ctypes.c_uint32),
        ("dwFileDateMS", ctypes.c_uint32),
        ("dwFileDateLS", ctypes.c_uint32),
    ]


def pe_architecture(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            if handle.read(2) != b"MZ":
                return "unknown"
            handle.seek(0x3C)
            pe_offset = struct.unpack("<I", handle.read(4))[0]
            handle.seek(pe_offset)
            if handle.read(4) != b"PE\0\0":
                return "unknown"
            machine = struct.unpack("<H", handle.read(2))[0]
    except (OSError, struct.error):
        return "unknown"
    return {0x014C: "x86", 0x8664: "x64"}.get(machine, f"machine-0x{machine:04X}")


def file_version(path: Path) -> str:
    """Read a PE file version through the Windows version-information API."""
    if os.name != "nt" or not path.is_file():
        return ""
    try:
        version_api = ctypes.WinDLL("version", use_last_error=True)
        get_size = version_api.GetFileVersionInfoSizeW
        get_size.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        get_size.restype = ctypes.c_uint32
        get_info = version_api.GetFileVersionInfoW
        get_info.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        get_info.restype = ctypes.c_int
        query_value = version_api.VerQueryValueW
        query_value.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_uint),
        ]
        query_value.restype = ctypes.c_int

        ignored = ctypes.c_uint32()
        size = int(get_size(str(path), ctypes.byref(ignored)))
        if size <= 0:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not get_info(str(path), 0, size, buffer):
            return ""
        value_pointer = ctypes.c_void_p()
        value_length = ctypes.c_uint()
        if not query_value(
            buffer,
            "\\",
            ctypes.byref(value_pointer),
            ctypes.byref(value_length),
        ):
            return ""
        fixed_info = ctypes.cast(
            value_pointer,
            ctypes.POINTER(VS_FIXEDFILEINFO),
        ).contents
        if fixed_info.dwSignature != 0xFEEF04BD:
            return ""
        return ".".join(
            str(value)
            for value in (
                fixed_info.dwFileVersionMS >> 16,
                fixed_info.dwFileVersionMS & 0xFFFF,
                fixed_info.dwFileVersionLS >> 16,
                fixed_info.dwFileVersionLS & 0xFFFF,
            )
        )
    except (AttributeError, OSError, ValueError):
        return ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest().upper()


def run_command(command: List[str], timeout: float = 8.0) -> Dict[str, Any]:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=creation_flags,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "output": ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip(),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "returncode": None, "output": str(exc)}


def query_driver_service(name: str) -> Dict[str, Any]:
    command_result = run_command(["sc.exe", "query", name])
    output_upper = command_result["output"].upper()
    state = "unknown"
    if "RUNNING" in output_upper:
        state = "running"
    elif "STOPPED" in output_upper:
        state = "stopped"
    elif "1060" in output_upper or "DOES NOT EXIST" in output_upper:
        state = "not-installed"
    return {
        "name": name,
        "state": state,
        "query_ok": command_result["ok"],
        "raw": command_result["output"],
    }


def query_connected_devices() -> Dict[str, Any]:
    command_result = run_command(["pnputil", "/enum-devices", "/connected"])
    output = command_result["output"]
    output_upper = output.upper()
    interesting_lines = []
    markers = ("MR-MC", "MITSUBISHI", "POSITION BOARD", "SSCNET", "MC2XX")
    for line in output.splitlines():
        if any(marker in line.upper() for marker in markers):
            interesting_lines.append(line.strip())
    return {
        "query_ok": command_result["ok"],
        "usb_maintenance_detected": "VID_06D3&PID_01D1" in output_upper,
        "pcie_device_detected": "VEN_10BA&DEV_0624" in output_upper,
        "matching_lines": interesting_lines,
    }


def query_position_board_utility() -> Dict[str, Any]:
    result = {
        "installed": False,
        "utility_version": "",
        "api_version": "",
        "pcie_driver_version": "",
        "common_driver_version": "",
        "pb_test_version": "",
        "pb_test_path": "",
        "install_path": "",
    }
    if winreg is None:
        return result
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
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                registry_path,
                0,
                winreg.KEY_READ | view_flag,
            ) as key:
                mappings = {
                    "UtilityVersion": "utility_version",
                    "ApiVersion": "api_version",
                    "MC2XXVersion": "pcie_driver_version",
                    "MC2XXCmnVersion": "common_driver_version",
                    "InstallPath": "install_path",
                }
                for registry_name, result_name in mappings.items():
                    try:
                        result[result_name] = str(
                            winreg.QueryValueEx(key, registry_name)[0]
                        )
                    except OSError:
                        pass
                result["installed"] = True
                break
        except OSError:
            continue
    install_path = str(result.get("install_path", "")).strip()
    if install_path:
        pb_test_candidates = (
            Path(os.path.expandvars(install_path)) / "PbTest" / "PbTest.exe",
            Path(os.path.expandvars(install_path)) / "PB Test" / "PbTest.exe",
        )
        for candidate in pb_test_candidates:
            if candidate.is_file():
                result["pb_test_path"] = str(candidate.resolve())
                result["pb_test_version"] = file_version(candidate)
                break
    return result


def version_tuple(value: str) -> Tuple[int, ...]:
    parts = []
    for item in value.split("."):
        digits = "".join(character for character in item if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def dll_candidates(
    explicit_path: str,
    installed_utility: Optional[Dict[str, Any]] = None,
) -> List[Path]:
    candidates: List[Path] = []
    if explicit_path:
        candidates.append(Path(os.path.expandvars(explicit_path)).expanduser())
        return [candidates[0].resolve()]

    script_dir = Path(__file__).resolve().parent
    install_directories: List[Path] = []
    registered_install_path = (
        str((installed_utility or {}).get("install_path", "")).strip()
    )
    if registered_install_path:
        install_directories.append(
            Path(os.path.expandvars(registered_install_path))
            / "API Library"
            / "Library"
        )
    preferred_names = (
        ("mc2xxstd_x64.dll",)
        if ctypes.sizeof(ctypes.c_void_p) == 8
        else ("mc2xxstd_wow64.dll", "mc2xxstd.dll")
    )
    candidate_directories = install_directories + [
        script_dir / "vendor" / "mitsubishi",
        script_dir,
    ]
    seen_directories = set()
    unique_directories = []
    for directory in candidate_directories:
        normalized = os.path.normcase(str(directory.resolve()))
        if normalized in seen_directories:
            continue
        seen_directories.add(normalized)
        unique_directories.append(directory)

    for directory in unique_directories:
        for name in preferred_names:
            candidates.append(directory / name)

    unique_candidates: List[Path] = []
    seen = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        normalized = os.path.normcase(str(resolved))
        if normalized not in seen:
            unique_candidates.append(resolved)
            seen.add(normalized)
    return unique_candidates


def select_dll(
    explicit_path: str,
    installed_utility: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Path], List[Dict[str, Any]]]:
    python_arch = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"
    inspected = []
    selected = None
    installed_path = ""
    if installed_utility:
        installed_path = str(installed_utility.get("install_path", "")).strip()
    installed_root = (
        os.path.normcase(str(Path(installed_path).resolve()))
        if installed_path
        else ""
    )
    installed_api_version = str(
        (installed_utility or {}).get("api_version", "")
    )
    for candidate in dll_candidates(explicit_path, installed_utility):
        exists = candidate.is_file()
        architecture = pe_architecture(candidate) if exists else "missing"
        compatible = exists and architecture == python_arch
        candidate_version = file_version(candidate) if exists else ""
        candidate_hash = sha256_file(candidate) if exists else ""
        resolved_text = os.path.normcase(str(candidate.resolve()))
        source = "project"
        if explicit_path:
            source = "explicit"
        elif installed_root and resolved_text.startswith(installed_root + os.sep):
            source = "installed"
        elif "vendor" + os.sep + "mitsubishi" in resolved_text:
            source = "vendor"
        application_candidate = bool(
            explicit_path
            or not installed_root
            or source == "installed"
        )
        stack_match = bool(
            candidate_version
            and installed_api_version
            and version_tuple(candidate_version)[:2]
            == version_tuple(installed_api_version)[:2]
        )
        inspected.append(
            {
                "path": str(candidate),
                "exists": exists,
                "architecture": architecture,
                "compatible": compatible,
                "file_version": candidate_version,
                "sha256": candidate_hash,
                "source": source,
                "installed_stack_match": stack_match,
                "application_candidate": application_candidate,
            }
        )
        if selected is None and compatible:
            selected = candidate
    return selected, inspected


def describe_api_error(code: int) -> str:
    code = int(code) & 0xFFFFFFFF
    return KNOWN_API_ERRORS.get(code, "Unknown Mitsubishi API error")


def scan_boards(
    dll_path: Path, selected_board_id: Optional[int]
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "dll_path": str(dll_path),
        "library_loaded": False,
        "loaded": False,
        "probe_uncertain": False,
        "load_error": "",
        "boards": [],
    }
    try:
        library = ctypes.WinDLL(str(dll_path))
    except (OSError, AttributeError) as exc:
        result["load_error"] = str(exc)
        return result
    result["library_loaded"] = True

    missing_exports = [
        name
        for name in MR_MC240N_REQUIRED_API_EXPORTS
        if not hasattr(library, name)
    ]
    if missing_exports:
        result["load_error"] = (
            "Required API exports missing: " + ", ".join(missing_exports)
        )
        return result

    try:
        ssc_open = library.sscOpen
        ssc_close = library.sscClose
        get_last_error = library.sscGetLastError
    except AttributeError as exc:
        result["load_error"] = f"Required API export missing: {exc}"
        return result

    ssc_open.argtypes = [ctypes.c_int]
    ssc_open.restype = ctypes.c_int
    ssc_close.argtypes = [ctypes.c_int]
    ssc_close.restype = ctypes.c_int
    get_last_error.argtypes = []
    get_last_error.restype = ctypes.c_uint32
    result["loaded"] = True

    board_ids = [selected_board_id] if selected_board_id is not None else list(BOARD_IDS)
    for board_id in board_ids:
        status = int(ssc_open(board_id))
        board_result = {
            "board_id": board_id,
            "opened": status == 0,
            "found": status == 0,
            "open_status": status,
            "last_error": 0,
            "last_error_hex": "0x00000000",
            "description": "Board opened successfully" if status == 0 else "",
            "close_status": None,
        }
        if status == 0:
            try:
                board_result["close_status"] = int(ssc_close(board_id))
                if board_result["close_status"] != 0:
                    board_result["found"] = False
                    board_result["description"] += (
                        "; close failed, so the scan stopped for safety"
                    )
                    result["boards"].append(board_result)
                    result["close_failed"] = True
                    break
            except Exception as exc:
                board_result["found"] = False
                board_result["description"] += f"; close raised {exc}"
                result["boards"].append(board_result)
                result["close_failed"] = True
                break
        else:
            error_code = int(get_last_error()) & 0xFFFFFFFF
            board_result["last_error"] = error_code
            board_result["last_error_hex"] = f"0x{error_code:08X}"
            board_result["description"] = describe_api_error(error_code)
        result["boards"].append(board_result)
    return result


def scan_boards_isolated(
    dll_path: Path,
    selected_board_id: Optional[int],
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Run native DLL probing in a child so hangs/crashes do not kill the report."""
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_scan-only",
        "--dll",
        str(dll_path),
    ]
    if selected_board_id is not None:
        command.extend(["--board-id", str(selected_board_id)])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "dll_path": str(dll_path),
            "library_loaded": False,
            "loaded": False,
            "probe_uncertain": True,
            "load_error": (
                f"Native probe exceeded {timeout_seconds:.0f} seconds and "
                "the child process was terminated."
            ),
            "boards": [],
        }
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "dll_path": str(dll_path),
            "library_loaded": False,
            "loaded": False,
            "probe_uncertain": True,
            "load_error": (
                f"Native probe child exited with code {completed.returncode}"
                + (f": {detail}" if detail else ".")
            ),
            "boards": [],
        }
    result["child_returncode"] = completed.returncode
    if completed.returncode != 0:
        result["probe_uncertain"] = True
        result["loaded"] = False
        result["load_error"] = (
            result.get("load_error")
            or f"Native probe child exited with code {completed.returncode}."
        )
    return result


def build_report(args: argparse.Namespace) -> Dict[str, Any]:
    installed_utility = query_position_board_utility()
    report: Dict[str, Any] = {
        "utility": "MR-MC240N PCIe Connection Check",
        "safe_probe": "Only sscOpen/sscClose are called; no system-start or motion commands.",
        "platform": {
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "python_architecture": "x64"
            if ctypes.sizeof(ctypes.c_void_p) == 8
            else "x86",
            "executable": sys.executable,
        },
        "services": [
            query_driver_service("mc2xx"),
            query_driver_service("mc2xxcmn"),
        ],
        "devices": query_connected_devices(),
        "installed_utility": installed_utility,
    }
    _, inspected_dlls = select_dll(args.dll, installed_utility)
    report["dll_candidates"] = inspected_dlls

    pcie_scans = []
    for candidate in inspected_dlls:
        if not candidate["compatible"] or not candidate["application_candidate"]:
            continue
        scan = scan_boards_isolated(Path(candidate["path"]), args.board_id)
        scan["file_version"] = candidate["file_version"]
        scan["sha256"] = candidate["sha256"]
        scan["source"] = candidate["source"]
        scan["installed_stack_match"] = candidate["installed_stack_match"]
        scan["application_candidate"] = candidate["application_candidate"]
        pcie_scans.append(scan)
        # The application stops DLL selection as soon as one DLL loads. Mirror
        # that behavior and never call sscOpen through comparison-only DLLs.
        if (
            scan.get("library_loaded")
            or scan.get("close_failed")
            or scan.get("probe_uncertain")
        ):
            break
    report["pcie_scans"] = pcie_scans

    application_scans = [
        scan for scan in pcie_scans if scan["application_candidate"]
    ]
    loaded_application_scans = [
        scan for scan in application_scans if scan.get("loaded")
    ]
    if loaded_application_scans:
        report["pcie_scan"] = loaded_application_scans[0]
    elif application_scans:
        report["pcie_scan"] = application_scans[0]
    else:
        report["pcie_scan"] = {
            "loaded": False,
            "load_error": (
                "No architecture-compatible DLL is eligible for automatic "
                "application loading. Repair the registered Utility2 installation "
                "or configure one explicit matching DLL."
            ),
            "boards": [],
        }

    found = any(
        board["found"]
        for board in report["pcie_scan"].get("boards", [])
    )
    report["application_ready"] = found
    recommendations = []
    utility = report["installed_utility"]
    api_version = version_tuple(utility["api_version"])
    windows_10_or_newer = sys.getwindowsversion().major >= 10
    unsupported_installed_stack = (
        utility["installed"]
        and windows_10_or_newer
        and api_version
        and api_version < (2, 0)
    )
    if found:
        recommendations.append(
            "PCIe board access is ready. Use the successful Board ID in the main app."
        )
    elif unsupported_installed_stack:
        if utility.get("pb_test_version"):
            recommendations.append(
                f"PB Test {utility['pb_test_version']} is the test-tool file version, "
                f"not the Utility/API runtime version. This installation is still "
                f"Utility2/API {utility['utility_version']}/{utility['api_version']}."
            )
        recommendations.append(
            f"Installed Utility2/API {utility['utility_version']}/"
            f"{utility['api_version']} predates Windows 10 support. Install a complete "
            "Position Board Utility2 package so its API and drivers match. Version 2.00 "
            "added Windows 10 support; use a current Mitsubishi-supported release."
        )
        recommendations.append(
            "Do not fix this by copying only a newer mc2xxstd DLL; the kernel driver "
            "and API library must come from the same Utility2 installation."
        )
    elif report["devices"]["pcie_device_detected"]:
        recommendations.append(
            "Windows detects the MR-MC2xx PCIe device, but the Mitsubishi API cannot "
            "open it. Close all board applications, unplug the board USB cable, and "
            "perform a full power-off/cold boot before checking again."
        )
        recommendations.append(
            "If the cold-boot check still fails, verify PCIe access from PB Test. "
            "A PB Test USB success does not verify the PCIe API path."
        )
    else:
        recommendations.append(
            "No PCIe Board ID opened. Confirm that the MR-MC240N is seated in a "
            "PCIe slot and that its PCI Express link indicator is on."
        )
        if report["devices"]["usb_maintenance_detected"]:
            recommendations.append(
                "The USB maintenance interface is present, but its cable does not "
                "replace the PCIe application-control connection."
            )
        recommendations.append(
            "Close PB Test and other Position Board tools, then run this check again."
        )
    mismatched_dlls = [
        candidate
        for candidate in inspected_dlls
        if candidate["compatible"]
        and candidate["file_version"]
        and utility["api_version"]
        and not candidate["installed_stack_match"]
    ]
    if mismatched_dlls:
        recommendations.append(
            "One or more project/vendor DLLs do not match the installed API version. "
            "Automatic application loading uses the registered installed DLL; "
            "remove any explicit mismatched DLL path."
        )
    failed_board_results = [
        board
        for scan in pcie_scans
        for board in scan.get("boards", [])
        if not board.get("found")
    ]
    masked_not_found = (
        report["devices"]["pcie_device_detected"]
        and pcie_scans
        and failed_board_results
        and all(
            board.get("last_error") == 0x00021010
            for board in failed_board_results
        )
    )
    if masked_not_found:
        recommendations.append(
            "The public DLL reports only 0x21010 even when its lower-level driver "
            "initialization fails. Use tools/trace_pcie_open.py for a read-only "
            "developer trace; do not patch or bypass the driver license check."
        )
    utility_version = version_tuple(utility["utility_version"])
    if (
        utility["installed"]
        and utility_version
        and utility_version < MIN_SECURE_UTILITY2_VERSION
    ):
        recommendations.append(
            "Mitsubishi's WinDriver security advisory lists Position Board "
            "Utility2 3.40 and earlier as affected. Obtain a complete Utility2 "
            "3.50 or later package through the place of purchase; do not reinstall "
            "this legacy package."
        )
    report["recommendations"] = recommendations
    return report


def print_human_report(report: Dict[str, Any]) -> None:
    print("MR-MC240N PCIe Connection Check")
    print("=" * 38)
    print(report["safe_probe"])
    platform_info = report["platform"]
    print(
        f"Host: {platform_info['os']} / Python {platform_info['python']} "
        f"({platform_info['python_architecture']})"
    )
    print()
    print("Driver services")
    for service in report["services"]:
        print(f"  {service['name']}: {service['state']}")

    print()
    print("Related devices")
    usb_text = "detected" if report["devices"]["usb_maintenance_detected"] else "not detected"
    pcie_text = "detected" if report["devices"]["pcie_device_detected"] else "not detected"
    print(f"  USB maintenance interface 06D3:01D1: {usb_text}")
    print(f"  PCIe position board 10BA:0624: {pcie_text}")
    for line in report["devices"]["matching_lines"]:
        print(f"  {line}")

    print()
    print("Installed Position Board Utility2")
    utility = report["installed_utility"]
    if utility["installed"]:
        print(f"  Utility: {utility['utility_version']}")
        print(f"  API: {utility['api_version']}")
        if utility.get("pb_test_version"):
            print(
                f"  PB Test tool: {utility['pb_test_version']} "
                "(tool version; not the API runtime version)"
            )
        print(
            "  Drivers: "
            f"mc2xx {utility['pcie_driver_version']} / "
            f"mc2xxcmn {utility['common_driver_version']}"
        )
    else:
        print("  Not detected")

    print()
    print("DLL candidates")
    for candidate in report["dll_candidates"]:
        marker = "*" if candidate["compatible"] else "-"
        version_text = (
            f", version={candidate['file_version']}"
            if candidate["file_version"]
            else ""
        )
        match_text = (
            ", matches-installed-api"
            if candidate["installed_stack_match"]
            else ""
        )
        application_text = (
            ", application-candidate"
            if candidate["application_candidate"]
            else ", comparison-only"
        )
        print(
            f"  {marker} {candidate['path']} "
            f"[{candidate['architecture']}, exists={candidate['exists']}, "
            f"source={candidate['source']}{version_text}{match_text}"
            f"{application_text}]"
        )

    print()
    print("PCIe board scans")
    if not report["pcie_scans"]:
        print(
            "  No scan: "
            f"{report['pcie_scan'].get('load_error', 'no compatible DLL')}"
        )
    for scan in report["pcie_scans"]:
        version_text = (
            f" v{scan['file_version']}" if scan.get("file_version") else ""
        )
        role = (
            "application"
            if scan.get("application_candidate")
            else "comparison-only"
        )
        print(
            f"  DLL ({scan.get('source', 'unknown')}, {role}): "
            f"{scan['dll_path']}{version_text}"
        )
        if not scan.get("loaded"):
            print(f"    load failed: {scan.get('load_error', 'unknown error')}")
            continue
        for board in scan["boards"]:
            if board["found"]:
                print(
                    f"    Board ID {board['board_id']}: FOUND "
                    f"(sscOpen=0, sscClose={board['close_status']})"
                )
            elif board.get("opened"):
                print(
                    f"    Board ID {board['board_id']}: OPENED BUT NOT CLOSED "
                    f"(sscClose={board['close_status']}; {board['description']})"
                )
            else:
                print(
                    f"    Board ID {board['board_id']}: not found "
                    f"(sscOpen={board['open_status']}, "
                    f"detail={board['last_error_hex']} - {board['description']})"
                )
    print()
    print("Result")
    for recommendation in report["recommendations"]:
        print(f"  - {recommendation}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Safely check the MR-MC240N PCIe driver, API DLL, and Board IDs. "
            "No motion commands are sent."
        )
    )
    parser.add_argument(
        "--dll",
        default="",
        help="Explicit mc2xxstd DLL path. Auto-detected when omitted.",
    )
    parser.add_argument(
        "--board-id",
        type=int,
        choices=BOARD_IDS,
        help="Check only one Board ID (0-3). The default scans all IDs.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the human report.",
    )
    parser.add_argument(
        "--pause",
        action="store_true",
        help="Wait for Enter before closing (useful when launched by double-click).",
    )
    parser.add_argument(
        "--_scan-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.name != "nt":
        print("This utility supports Windows only.", file=sys.stderr)
        return 2

    if args._scan_only:
        if not args.dll:
            print("--_scan-only requires --dll.", file=sys.stderr)
            return 2
        print(
            json.dumps(
                scan_boards(Path(args.dll).resolve(), args.board_id),
                ensure_ascii=False,
            )
        )
        return 0

    report = build_report(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human_report(report)

    found = bool(report["application_ready"])
    if args.pause:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
