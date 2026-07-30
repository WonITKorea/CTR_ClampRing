"""Unit tests for the read-only MR-MC240N PCIe diagnostic."""

import io
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mr_mc240n_pcie_check as diagnostic


def fake_diagnostic_library(**overrides):
    functions = {
        name: Mock(name=name, return_value=0)
        for name in diagnostic.MR_MC240N_REQUIRED_API_EXPORTS
    }
    functions.update(overrides)
    return SimpleNamespace(**functions)


class DllCandidateTests(unittest.TestCase):
    def test_explicit_dll_is_the_only_candidate(self):
        candidate = diagnostic.dll_candidates(r".\chosen.dll", {})

        self.assertEqual(candidate, [Path("chosen.dll").resolve()])

    def test_unknown_pe_architecture_is_not_treated_as_compatible(self):
        candidate = Path("unknown.dll").resolve()
        with (
            patch.object(diagnostic, "dll_candidates", return_value=[candidate]),
            patch.object(Path, "is_file", return_value=True),
            patch.object(diagnostic, "pe_architecture", return_value="unknown"),
            patch.object(diagnostic, "file_version", return_value=""),
            patch.object(diagnostic, "sha256_file", return_value=""),
        ):
            selected, inspected = diagnostic.select_dll(str(candidate), {})

        self.assertIsNone(selected)
        self.assertFalse(inspected[0]["compatible"])


class ScanTests(unittest.TestCase):
    def test_missing_motion_export_rejects_dll_before_open(self):
        library = fake_diagnostic_library()
        delattr(library, "sscJogStop")
        with patch.object(diagnostic.ctypes, "WinDLL", return_value=library):
            result = diagnostic.scan_boards(Path("fake.dll"), 0)

        self.assertTrue(result["library_loaded"])
        self.assertFalse(result["loaded"])
        self.assertIn("sscJogStop", result["load_error"])
        library.sscOpen.assert_not_called()

    def test_last_error_is_normalized_to_unsigned_32_bits(self):
        library = fake_diagnostic_library(
            sscOpen=Mock(return_value=-1),
            sscClose=Mock(return_value=0),
            sscGetLastError=Mock(return_value=-1),
        )
        with patch.object(diagnostic.ctypes, "WinDLL", return_value=library):
            result = diagnostic.scan_boards(Path("fake.dll"), 0)

        self.assertEqual(result["boards"][0]["last_error"], 0xFFFFFFFF)
        self.assertEqual(result["boards"][0]["last_error_hex"], "0xFFFFFFFF")
        library.sscClose.assert_not_called()

    def test_scan_stops_if_a_successfully_opened_board_cannot_close(self):
        library = fake_diagnostic_library(
            sscOpen=Mock(return_value=0),
            sscClose=Mock(return_value=-1),
            sscGetLastError=Mock(return_value=0),
        )
        with patch.object(diagnostic.ctypes, "WinDLL", return_value=library):
            result = diagnostic.scan_boards(Path("fake.dll"), None)

        self.assertTrue(result["close_failed"])
        self.assertFalse(result["boards"][0]["found"])
        self.assertTrue(result["boards"][0]["opened"])
        self.assertEqual(len(result["boards"]), 1)
        library.sscOpen.assert_called_once_with(0)

    def test_isolated_scan_turns_native_timeout_into_report_data(self):
        with patch.object(
            diagnostic.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["python"], 20),
        ):
            result = diagnostic.scan_boards_isolated(Path("hung.dll"), 0)

        self.assertFalse(result["loaded"])
        self.assertTrue(result["probe_uncertain"])
        self.assertIn("terminated", result["load_error"])


class ReportRenderingTests(unittest.TestCase):
    def test_recommendations_are_printed_when_no_dll_can_load(self):
        report = {
            "safe_probe": "safe",
            "platform": {
                "os": "Windows",
                "python": "3.x",
                "python_architecture": "x64",
            },
            "services": [],
            "devices": {
                "usb_maintenance_detected": False,
                "pcie_device_detected": False,
                "matching_lines": [],
            },
            "installed_utility": {"installed": False},
            "dll_candidates": [],
            "pcie_scans": [],
            "pcie_scan": {
                "loaded": False,
                "load_error": "simulated load failure",
                "boards": [],
            },
            "recommendations": ["install the matching runtime"],
        }
        output = io.StringIO()

        with redirect_stdout(output):
            diagnostic.print_human_report(report)

        self.assertIn("simulated load failure", output.getvalue())
        self.assertIn("install the matching runtime", output.getvalue())


if __name__ == "__main__":
    unittest.main()
