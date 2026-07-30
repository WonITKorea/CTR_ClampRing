# MR-MC240N PCIe recovery

## Current machine diagnosis

The PCIe board itself is visible to Windows as `PCI\VEN_10BA&DEV_0624`, and
both `mc2xx` and `mc2xxcmn` services are running. The USB maintenance interface
`06D3:01D1` is also present.

The installed stack is:

- Position Board Utility2 `1.80`
- PB Test tool `3.8.0.0` (tool version, not Utility/API version)
- API `1.8.0.0`
- `mc2xx` driver `1.2.0.0`
- `mc2xxcmn` driver `11.5.0.0`

The registered API DLL `1.8.0.0` and the project-root DLL `2.2.0.0` both fail
`sscOpen` for Board IDs 0 through 3 with public error `0x00021010`.

The supplied `MRZJW3-MC2-UTL_Ver180[1].zip` is the same Utility2 `1.80`
installation media already installed on this machine. Its PB Test executable
is version `3.8.0.0`, but that does not make the API/runtime version 3.8.
The installed and supplied PB Test executables are byte-identical.

A read-only instrumented run found the masked lower-level failure:

```text
sscOpen
  -> open mc2xxcmn successfully
  -> register the embedded WinDriver license request
  -> scan PCI vendor 0x10BA / device 0x0624
  -> 0x20000009: No valid license
  -> public API replaces it with 0x00021010: Board not found
```

The scan stops before PCI subsystem, BAR, or Board ID inspection. Changing the
Board ID cannot repair this failure.

## Required PCIe repair

1. Close this application, PB Test, MR Configurator2, and other position-board
   software.
2. Obtain a complete, current Position Board Utility2 package for MR-MC2xx from
   Mitsubishi. API 2.00 added Windows 10 support, and Mitsubishi's current
   [WinDriver security advisory](https://www.mitsubishielectric.com/psirt/vulnerability/pdf/2024-001_en.pdf)
   lists Utility2 3.40 and earlier as affected and 3.50 or later as fixed.
   Do not reuse the installed API 1.80 stack on Windows 10. See Mitsubishi's
   [API Library manual](https://dl.mitsubishielectric.com/dl/fa/document/manual/ssc/ib0300225/ib0300225engl.pdf).
3. Run the official installer as administrator and install its Utility, API
   library, common driver, and PCIe driver together. Follow the official
   [Position Board Utility2 installation instructions](https://dl.mitsubishielectric.com/dl/fa/document/manual/ssc/bcn-b62008-303/bcn-b62008-303h.pdf).
4. Reboot Windows. If the board still does not enumerate, shut down completely,
   remove power, and cold-start the PC.
5. Leave the application's optional DLL path empty so it uses the registered
   installed DLL.
6. Run the safe check:

   ```powershell
   python mr_mc240n_pcie_check.py
   ```

7. Continue only when one Board ID reports `FOUND`. Select that Board ID in the
   application.

The diagnostic makes native DLL calls in an isolated child process. A native
timeout or crash is reported as uncertain and stops the scan instead of
silently trying another runtime.

Copying only `mc2xxstd_x64.dll`, forcing `sscOpen` to return success, or
patching the WinDriver license result is not a valid repair. Without a
successful vendor scan there is no valid BAR mapping; pretending that the open
succeeded can cause invalid memory access or system instability.

## Read-only developer trace

The trace helper observes `CreateFile`, `DeviceIoControl`, `sscOpen`,
`sscGetLastError`, and `sscClose`. It does not change arguments or return
values and the child diagnostic sends no System Start, Servo, or motion
commands.

```powershell
python tools\trace_pcie_open.py `
  --dll "C:\Program Files (x86)\Position Board\MR-MC2XX\API Library\Library\mc2xxstd_x64.dll" `
  --board-id 0 `
  --output artifacts\pcie_open_trace.jsonl
```

Frida is a local developer dependency and is not included in the repository.
The embedded vendor-license request payload is redacted before output. Do not
use this tool to bypass vendor licensing.

## Available fallback

The direct USB bridge successfully identifies this controller. System status
`0x0009` means it is waiting for SSCNET amplifier responses, so check controller
output to the first amplifier `CN1A`, each `CN1B` to the next `CN1A`, amplifier
control power, axis switches, and the final connector cap. In the application,
select `USB controller (direct)` explicitly while PCIe is being repaired.
