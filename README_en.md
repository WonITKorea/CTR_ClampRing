# CTR Seal Ring 6-Axis Test Application

[한국어](README.md)

Windows/PyQt application for a six-axis clamp test machine.

- Test simulation with CSV export and A4 PDF reports
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

Simulation mode works without the hardware drivers.

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

The diagnostic checks the device, driver, and DLL, and calls only
`sscOpen/sscClose`. It does not send System Start, Servo, or motion commands.

## Camera

Use the `Camera / Ring` panel to select a UVC camera, capture an unloaded
baseline, and record major/minor diameter, ovality, and baseline deformation.
The project uses `opencv-python-headless` to avoid Qt plugin conflicts.

## Pre-commit validation

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
git status --short
git diff --check
```

Before testing real motion, verify emergency stops, limits, SSCNET wiring,
amplifier power, and axis-number switches.
