# Mitsubishi runtime files

Vendor binaries are intentionally not included in this repository.

For direct MR-MC240N USB control, copy the architecture-compatible runtime from
your licensed Mitsubishi Position Board installation:

```text
vendor/mitsubishi/mc2xxstd_wow64.dll
```

For the optional PCIe API path, also provide the DLL matching the Python
process architecture:

```text
vendor/mitsubishi/mc2xxstd_x64.dll  # 64-bit Python
vendor/mitsubishi/mc2xxstd.dll      # 32-bit Python
```

Do not commit these files. The repository `.gitignore` keeps them local.
