# CTR Seal Ring 6-Axis Test Application

[한국어](README.md)

Windows/PyQt application for a six-axis clamp test machine.

- Automated FC400/MR-MC240N hardware strokes with CSV and A4 PDF reports
- UNIPULSE FC400 voltage output through an NI USB-6002
- Mitsubishi MR-MC240N USB position feedback and guarded motion
- UVC camera/OpenCV ring-profile measurement

The app opens as a maximized window with the title bar and taskbar visible.
F11 toggles full-screen mode.

## Quick start

Python 3.10 or newer is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

Running a test requires NI-DAQmx, an NI USB-6002, an MR-MC240N, and the
matching Mitsubishi runtime.

## Repository layout

```text
main.py                       PyQt GUI and test logic
hardware.py                   MR-MC240N Python control layer
mr_mc240n_usb_cli.c           32-bit USB C bridge source
mr_mc240n_pcie_check.py       Read-only PCIe diagnostic
scripts/build_usb_bridge.ps1  USB bridge build
scripts/verify.ps1            Pre-commit validation
vendor/mitsubishi/            Local-only Mitsubishi runtime location
```

PB Test, Mitsubishi DLLs/libraries, reverse-engineering tools, and diagnostic
traces are excluded from Git for licensing and repository-size reasons. Existing
local files are not deleted.

## FC400 + NI USB-6002

Install NI-DAQmx and connect the FC400 voltage output to the USB-6002.

- Differential: `V OUT → AI0`, `COM → AI4 (AI0-)`
- RSE: `V OUT → AI0`, `COM → AI GND`

Select `FC400 + USB-6002` and configure the actual zero/full-scale voltages,
capacity, unit, and sample rate. Defaults are `0 V = 0 N`, `10 V = 1000 N`,
`Dev1/ai0`, and `1000 S/s`.

## MR-MC240N direct USB control

Copy the licensed 32-bit Mitsubishi runtime to:

```text
vendor/mitsubishi/mc2xxstd_wow64.dll
```

Build the headless 32-bit bridge:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_usb_bridge.ps1 `
  -Compiler C:\path\to\tcc.exe
```

An import `.lib` is not required. The generated
`bin\mr_mc240n_usb.exe` and vendor DLLs remain local.

The bridge rejects motion when an axis is unmounted, not ready, operating, or
reporting an alarm. System status `0x0009` means that the controller is waiting
for SSCNET amplifier responses.

The current six-axis preset assumes:

- HG-KR13 + MR-J4-10B-RJ on axes 1 through 6
- Amplifier rotary switches `0` through `5`
- THK BTK1404, 4 mm lead, direct 1:1 coupling
- `1000 command units/mm`

## PCIe diagnostic

```powershell
python mr_mc240n_pcie_check.py
```

The diagnostic checks the device and driver services, reads the registered
Utility2/API versions, and inspects every architecture-compatible DLL. It calls
`sscOpen/sscClose` only through the DLL that the application would select;
mismatched files are metadata-only unless supplied explicitly with `--dll`.
It does not send System Start, Servo, or motion commands.

The application automatically uses the DLL registered by the installed
Position Board Utility2. An explicitly configured DLL path is authoritative and
fails fast instead of silently falling back to a different file. Keep the API
DLL, common driver, and PCIe driver from one complete Mitsubishi installation.
API 2.00 added Windows 10 support; use a current Mitsubishi-supported package
for the installed Windows version and reboot after installing its drivers.
Mitsubishi's WinDriver advisory identifies Utility2 3.40 and earlier as
affected and 3.50 or later as fixed.
The PB Test executable version (for example, 3.8.0.0) is separate from the
Utility/API runtime version.

If Windows detects `10BA:0624` but every DLL returns `0x00021010`, see
[PCIe_RECOVERY.md](PCIe_RECOVERY.md). A developer can capture the masked,
lower-level driver status without sending motion commands:

```powershell
python tools\trace_pcie_open.py `
  --dll "C:\Program Files (x86)\Position Board\MR-MC2XX\API Library\Library\mc2xxstd_x64.dll" `
  --board-id 0 `
  --output artifacts\pcie_open_trace.jsonl
```

The trace helper requires Frida locally. It observes the open path only and must
not be used to patch or bypass vendor license checks. Native DLL probes run in
an isolated child process; a timeout or crash is reported as uncertain and no
further DLL is tried. Embedded license request data is redacted before a trace
is recorded. Until the official PCIe runtime is repaired, select
`USB controller (direct)` explicitly; the app never silently changes the
connection type.

The PCIe System Start option first reads `sscGetSystemStatusCode`: it leaves an
already-running system alone and calls `sscSystemStart` only from preparation
complete. Mitsubishi's call can wait at least 10 seconds while SSCNET is
initialized. Connection settings are locked while motion may be active, and a
failed Rapid Stop/Close retains the controller so cleanup can be retried.

## Camera

Use the `Camera / Ring` panel to select a UVC camera, capture an unloaded
baseline, and record major/minor diameter, ovality, and baseline deformation.
The project uses `opencv-python-headless` to avoid Qt plugin conflicts.


```

Before testing real motion, verify emergency stops, limits, SSCNET wiring,
amplifier power, and axis-number switches.
