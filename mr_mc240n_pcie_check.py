"""Standalone MR-MC240N PCIe connection diagnostic.

This utility performs read-only host diagnostics and calls only sscOpen/sscClose.
It never starts the position-board system and never sends servo or motion commands.
"""

import argparse
import ctypes
import json
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import winreg
except ImportError:
    winreg = None


BOARD_IDS = range(4)
KNOWN_API_ERRORS = {
    0x00021010: "Position board not found (SSC_FUNC_ERR_NOT_FOUND_BOARD)",
}


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
        "install_path": "",
    }
    if winreg is None:
        return result
    registry_path = r"SOFTWARE\WOW6432Node\MITSUBISHI\PositionBoardUtility2"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, registry_path) as key:
            mappings = {
                "UtilityVersion": "utility_version",
                "ApiVersion": "api_version",
                "MC2XXVersion": "pcie_driver_version",
                "MC2XXCmnVersion": "common_driver_version",
                "InstallPath": "install_path",
            }
            for registry_name, result_name in mappings.items():
                try:
                    result[result_name] = str(winreg.QueryValueEx(key, registry_name)[0])
                except OSError:
                    pass
            result["installed"] = True
    except OSError:
        pass
    return result


def version_tuple(value: str) -> Tuple[int, ...]:
    parts = []
    for item in value.split("."):
        digits = "".join(character for character in item if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def dll_candidates(explicit_path: str) -> List[Path]:
    candidates: List[Path] = []
    if explicit_path:
        candidates.append(Path(os.path.expandvars(explicit_path)).expanduser())

    script_dir = Path(__file__).resolve().parent
    program_files_x86 = Path(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    )
    install_dir = (
        program_files_x86
        / "Position Board"
        / "MR-MC2XX"
        / "API Library"
        / "Library"
    )
    preferred_names = (
        ("mc2xxstd_x64.dll", "mc2xxstd.dll")
        if ctypes.sizeof(ctypes.c_void_p) == 8
        else ("mc2xxstd.dll", "mc2xxstd_wow64.dll")
    )
    for name in preferred_names:
        candidates.extend(
            (
                script_dir / "vendor" / "mitsubishi" / name,
                script_dir / name,
                install_dir / name,
            )
        )

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
) -> Tuple[Optional[Path], List[Dict[str, Any]]]:
    python_arch = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"
    inspected = []
    selected = None
    for candidate in dll_candidates(explicit_path):
        exists = candidate.is_file()
        architecture = pe_architecture(candidate) if exists else "missing"
        compatible = exists and architecture in {python_arch, "unknown"}
        inspected.append(
            {
                "path": str(candidate),
                "exists": exists,
                "architecture": architecture,
                "compatible": compatible,
            }
        )
        if selected is None and compatible:
            selected = candidate
    return selected, inspected


def describe_api_error(code: int) -> str:
    return KNOWN_API_ERRORS.get(code, "Unknown Mitsubishi API error")


def scan_boards(
    dll_path: Path, selected_board_id: Optional[int]
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "dll_path": str(dll_path),
        "loaded": False,
        "load_error": "",
        "boards": [],
    }
    try:
        library = ctypes.WinDLL(str(dll_path))
    except (OSError, AttributeError) as exc:
        result["load_error"] = str(exc)
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
    get_last_error.restype = ctypes.c_int
    result["loaded"] = True

    board_ids = [selected_board_id] if selected_board_id is not None else list(BOARD_IDS)
    for board_id in board_ids:
        status = int(ssc_open(board_id))
        board_result = {
            "board_id": board_id,
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
            except Exception as exc:  # Keep scanning/reporting even if close reports an issue.
                board_result["description"] += f"; close raised {exc}"
        else:
            error_code = int(get_last_error())
            board_result["last_error"] = error_code
            board_result["last_error_hex"] = f"0x{error_code:08X}"
            board_result["description"] = describe_api_error(error_code)
        result["boards"].append(board_result)
    return result


def build_report(args: argparse.Namespace) -> Dict[str, Any]:
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
        "installed_utility": query_position_board_utility(),
    }
    selected_dll, inspected_dlls = select_dll(args.dll)
    report["dll_candidates"] = inspected_dlls
    if selected_dll is None:
        report["pcie_scan"] = {
            "loaded": False,
            "load_error": "No architecture-compatible mc2xxstd DLL was found.",
            "boards": [],
        }
    else:
        report["pcie_scan"] = scan_boards(selected_dll, args.board_id)
    boards = report["pcie_scan"].get("boards", [])
    found = any(board["found"] for board in boards)
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
        recommendations.append(
            f"Installed Utility2/API {utility['utility_version']}/"
            f"{utility['api_version']} predates Windows 10 support. Install a complete "
            "Position Board Utility2 package so its API and drivers match. Version 2.00 "
            "is the Windows 10 minimum; Mitsubishi currently recommends 3.50 or later."
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
        print(
            f"  {marker} {candidate['path']} "
            f"[{candidate['architecture']}, exists={candidate['exists']}]"
        )

    scan = report["pcie_scan"]
    print()
    print("PCIe board scan")
    if not scan.get("loaded"):
        print(f"  DLL load failed: {scan.get('load_error', 'unknown error')}")
        return
    print(f"  DLL: {scan['dll_path']}")
    for board in scan["boards"]:
        if board["found"]:
            print(
                f"  Board ID {board['board_id']}: FOUND "
                f"(sscOpen=0, sscClose={board['close_status']})"
            )
        else:
            print(
                f"  Board ID {board['board_id']}: not found "
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.name != "nt":
        print("This utility supports Windows only.", file=sys.stderr)
        return 2

    report = build_report(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human_report(report)

    found = any(board["found"] for board in report["pcie_scan"].get("boards", []))
    if args.pause:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
