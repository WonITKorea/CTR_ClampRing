"""Trace the read-only MR-MC2xx PCIe open path.

The target process runs ``mr_mc240n_pcie_check.py`` for one Board ID, so the
only vendor API calls it makes are sscOpen and (after success) sscClose.  Frida
observes the Win32 device handles and IOCTL traffic without changing buffers
or return values.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOL_ROOT = _REPO_ROOT / ".reverse_tools"
sys.path.insert(0, str(_TOOL_ROOT))

try:
    import frida
except ImportError:
    frida = None


HOOK_SCRIPT = r"""
const INVALID_HANDLE_VALUE = ptr("-1");
const hookedAddresses = new Set();

function emit(item) {
  send(item);
}

function safeString(pointer, wide) {
  if (pointer.isNull()) return "";
  try {
    return wide ? pointer.readUtf16String() : pointer.readCString();
  } catch (_) {
    return "<unreadable>";
  }
}

function safeHex(pointer, length) {
  if (pointer.isNull() || length <= 0) return "";
  try {
    const data = pointer.readByteArray(Math.min(length, 512));
    return Array.from(new Uint8Array(data), value =>
      value.toString(16).padStart(2, "0")
    ).join("");
  } catch (_) {
    return "<unreadable>";
  }
}

function findExport(name) {
  for (const module of Process.enumerateModules()) {
    for (const item of module.enumerateExports()) {
      if (item.type === "function" && item.name === name) return item.address;
    }
  }
  return null;
}

const getLastErrorAddress = findExport("GetLastError");
const getLastError = getLastErrorAddress === null
  ? null
  : new NativeFunction(getLastErrorAddress, "uint32", []);

function installHook(address, callbacks) {
  if (address === null) return;
  const key = address.toString();
  if (hookedAddresses.has(key)) return;
  hookedAddresses.add(key);
  Interceptor.attach(address, callbacks);
}

for (const name of ["CreateFileA", "CreateFileW"]) {
  const address = findExport(name);
  installHook(address, {
    onEnter(args) {
      this.path = safeString(args[0], name.endsWith("W"));
      this.access = args[1].toUInt32();
      this.share = args[2].toUInt32();
    },
    onLeave(retval) {
      if (!this.path.toLowerCase().includes("mc2xx")) return;
      emit({
        event: "CreateFile",
        api: name,
        path: this.path,
        access: "0x" + this.access.toString(16),
        share: "0x" + this.share.toString(16),
        handle: retval.toString(),
        success: !retval.equals(INVALID_HANDLE_VALUE),
        win32_error: getLastError === null ? null : getLastError()
      });
    }
  });
}

const deviceIoControlAddress = findExport("DeviceIoControl");
installHook(deviceIoControlAddress, {
  onEnter(args) {
    this.handle = args[0];
    this.code = args[1].toUInt32();
    this.input = args[2];
    this.inputSize = args[3].toUInt32();
    this.output = args[4];
    this.outputSize = args[5].toUInt32();
    this.bytesReturned = args[6];
    this.inputHex = this.code === 0x9538354b
      ? "<redacted vendor license payload>"
      : safeHex(this.input, this.inputSize);
  },
  onLeave(retval) {
    let returned = null;
    if (!this.bytesReturned.isNull()) {
      try {
        returned = this.bytesReturned.readU32();
      } catch (_) {
        returned = null;
      }
    }
    let outputStatus = null;
    if (!this.output.isNull() && this.outputSize >= 4) {
      try {
        outputStatus = this.output.readU32();
      } catch (_) {
        outputStatus = null;
      }
    }
    emit({
      event: "DeviceIoControl",
      handle: this.handle.toString(),
      code: "0x" + this.code.toString(16).padStart(8, "0"),
      input_size: this.inputSize,
      input: this.inputHex,
      output_size: this.outputSize,
      output: safeHex(
        this.output,
        returned === null || returned === 0
          ? this.outputSize
          : Math.min(returned, this.outputSize)
      ),
      output_status: outputStatus === null
        ? null
        : "0x" + outputStatus.toString(16).padStart(8, "0"),
      output_status_description: outputStatus === 0x20000009
        ? "No valid license"
        : null,
      bytes_returned: returned,
      success: retval.toInt32() !== 0,
      win32_error: getLastError === null ? null : getLastError()
    });
  }
});

function installVendorHooks() {
  for (const module of Process.enumerateModules()) {
    const moduleName = module.name.toLowerCase();
    if (![
      "mc2xxstd.dll",
      "mc2xxstd_x64.dll",
      "mc2xxstd_wow64.dll"
    ].includes(moduleName)) continue;
    for (const item of module.enumerateExports()) {
      if (item.type !== "function") continue;
      if (!["sscOpen", "sscClose", "sscGetLastError"].includes(item.name)) {
        continue;
      }
      const functionName = item.name;
      installHook(item.address, {
        onEnter(args) {
          this.board = functionName === "sscGetLastError"
            ? null
            : args[0].toInt32();
          emit({
            event: "vendor_enter",
            name: functionName,
            board: this.board
          });
        },
        onLeave(retval) {
          emit({
            event: "vendor_leave",
            name: functionName,
            board: this.board,
            result: retval.toInt32(),
            result_hex: "0x" + retval.toUInt32().toString(16).padStart(8, "0")
          });
        }
      });
      emit({
        event: "hook",
        module: module.name,
        name: functionName,
        address: item.address.toString()
      });
    }
    return true;
  }
  return false;
}

for (const loaderName of [
  "LoadLibraryA",
  "LoadLibraryW",
  "LoadLibraryExA",
  "LoadLibraryExW"
]) {
  installHook(findExport(loaderName), {
    onLeave(_) {
      installVendorHooks();
    }
  });
}

const timer = setInterval(function () {
  if (installVendorHooks()) clearInterval(timer);
}, 10);
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trace one safe sscOpen/sscClose PCIe probe."
    )
    parser.add_argument("--dll", type=Path, required=True)
    parser.add_argument("--board-id", type=int, choices=range(4), default=0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if frida is None:
        raise SystemExit(
            "Frida is not installed. Install it in the active Python environment "
            "or place the local package under .reverse_tools before tracing."
        )
    dll_path = args.dll.resolve()
    if not dll_path.is_file():
        raise SystemExit(f"DLL not found: {dll_path}")

    command = [
        sys.executable,
        str(_REPO_ROOT / "mr_mc240n_pcie_check.py"),
        "--_scan-only",
        "--dll",
        str(dll_path),
        "--board-id",
        str(args.board_id),
    ]
    device = frida.get_local_device()
    pid = device.spawn(command, cwd=str(_REPO_ROOT))
    session = device.attach(pid)
    script = session.create_script(HOOK_SCRIPT)
    detached = threading.Event()
    output_handle = None
    if args.output:
        output_path = args.output.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_handle = output_path.open("w", encoding="utf-8")

    def emit(record: dict) -> None:
        record["time"] = time.time()
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)
        if output_handle is not None:
            output_handle.write(line + "\n")
            output_handle.flush()

    def on_message(message, data) -> None:
        if message.get("type") == "send":
            emit(message["payload"])
        else:
            emit({"event": "frida", "message": message})

    def on_detached(reason, crash) -> None:
        emit({"event": "detached", "reason": reason, "crash": str(crash or "")})
        detached.set()

    script.on("message", on_message)
    session.on("detached", on_detached)
    try:
        script.load()
        device.resume(pid)
        if not detached.wait(args.timeout):
            emit({"event": "timeout", "seconds": args.timeout})
            try:
                device.kill(pid)
            except frida.ProcessNotFoundError:
                pass
            return 2
        return 0
    finally:
        if output_handle is not None:
            output_handle.close()
        try:
            session.detach()
        except frida.InvalidOperationError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
