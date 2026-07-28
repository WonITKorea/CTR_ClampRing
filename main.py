import sys
import os
import threading
import time
import numpy as np
import pandas as pd
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QGridLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
                             QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
                             QFileDialog, QMessageBox)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from scipy.interpolate import interp1d
from matplotlib.patches import Rectangle
import platform

from hardware import (
    MR_MC240N_WINDOWS_ONLY_MESSAGE,
    MR_CONNECTION_PCIE_API,
    MR_CONNECTION_USB_MAINTENANCE,
    MrMc240nPositionController,
    MrMc240nUsbController,
    detect_mr_mc240n_usb_controller,
)

try:
    import nidaqmx
    from nidaqmx.system import System
    from nidaqmx.constants import (
        AcquisitionType,
        READ_ALL_AVAILABLE,
        TerminalConfiguration,
    )
    NIDAQMX_AVAILABLE = True
    NIDAQMX_IMPORT_ERROR = ""
except Exception as exc:
    nidaqmx = None
    System = None
    AcquisitionType = None
    READ_ALL_AVAILABLE = None
    TerminalConfiguration = None
    NIDAQMX_AVAILABLE = False
    NIDAQMX_IMPORT_ERROR = str(exc)

try:
    import cv2
    CV2_AVAILABLE = True
    CV2_IMPORT_ERROR = ""
except Exception as exc:
    cv2 = None
    CV2_AVAILABLE = False
    CV2_IMPORT_ERROR = str(exc)


def _sanitize_qt_plugin_env_after_cv2_import():
    if not CV2_AVAILABLE:
        return

    cv2_file = getattr(cv2, "__file__", "")
    if not cv2_file:
        return

    cv2_package_dir = os.path.dirname(cv2_file)
    cv2_qt_dir = os.path.normpath(os.path.join(cv2_package_dir, "qt"))

    for env_key in ("QT_QPA_PLATFORM_PLUGIN_PATH", "QT_PLUGIN_PATH"):
        env_value = os.environ.get(env_key)
        if not env_value:
            continue

        normalized_value = os.path.normpath(env_value)
        if normalized_value.startswith(cv2_qt_dir):
            os.environ.pop(env_key, None)


_sanitize_qt_plugin_env_after_cv2_import()

SIMULATION_SOURCE = "Simulation"
FC400_SOURCE = "FC400 analog via NI USB-6002"
CAMERA_RING_COLOR_PROFILES = {
    "silver": {
        "label": "Silver / 은색",
        "status_name": "silver",
        "description": "silver clamp ring",
        "mode": "neutral",
        "s_max": 75,
        "v_min": 55,
        "v_floor_percentile": 25,
        "v_floor_ceiling": 160,
        "neutral_tol": 18,
    },
    "white": {
        "label": "White / 흰색",
        "status_name": "white",
        "description": "white clamp ring",
        "mode": "neutral",
        "s_max": 55,
        "v_min": 150,
        "v_floor_percentile": 35,
        "v_floor_ceiling": 235,
        "neutral_tol": 14,
    },
    "black": {
        "label": "Black / 검정",
        "status_name": "black",
        "description": "black clamp ring",
        "mode": "dark-neutral",
        "s_max": 85,
        "v_max": 95,
        "neutral_tol": 18,
    },
    "red": {
        "label": "Red / 빨강",
        "status_name": "red",
        "description": "red clamp ring",
        "mode": "hsv",
        "ranges": [
            ((0, 80, 35), (10, 255, 255)),
            ((170, 80, 35), (179, 255, 255)),
        ],
    },
    "blue": {
        "label": "Blue / 파랑",
        "status_name": "blue",
        "description": "blue clamp ring",
        "mode": "hsv",
        "ranges": [
            ((95, 70, 35), (135, 255, 255)),
        ],
    },
    "green": {
        "label": "Green / 초록",
        "status_name": "green",
        "description": "green clamp ring",
        "mode": "hsv",
        "ranges": [
            ((35, 55, 35), (95, 255, 255)),
        ],
    },
    "yellow": {
        "label": "Yellow / 노랑",
        "status_name": "yellow",
        "description": "yellow clamp ring",
        "mode": "hsv",
        "ranges": [
            ((15, 80, 60), (40, 255, 255)),
        ],
    },
}

# 한글 폰트 강제 로드 (OS 자동 인식)
font_path = None
if platform.system() == 'Windows':
    font_path = 'C:/Windows/Fonts/malgun.ttf'
elif platform.system() == 'Darwin':
    font_path = '/System/Library/Fonts/Supplemental/AppleGothic.ttf'
else:
    font_path = '/usr/share/fonts/truetype/nanum/NanumGothic.ttf'

if os.path.exists(font_path):
    font_entry = fm.FontEntry(fname=font_path, name='NanumGothic_Force')
    fm.fontManager.ttflist.insert(0, font_entry)
    plt.rcParams.update({'font.family': 'NanumGothic_Force'})
    font_prop = fm.FontProperties(fname=font_path)
else:
    print(f"폰트를 찾을 수 없습니다: {font_path}")
    font_prop = fm.FontProperties()

plt.rcParams['axes.unicode_minus'] = False

class SpiderChartCanvas(FigureCanvas):
    def __init__(self, parent=None, width=6, height=5, dpi=100):
        self.fig = plt.Figure(figsize=(width, height), dpi=dpi)
        self.ax = self.fig.add_subplot(111, polar=True)
        super(SpiderChartCanvas, self).__init__(self.fig)
        self.angles = np.linspace(0, 2 * np.pi, 6, endpoint=False).tolist()
        self.angles_closed = self.angles + [self.angles[0]]
        self.default_radius_limit = 10.0
        self.min_radius_limit = 2.5
        self.scale_decay = 0.70
        self.radius_limit = self.default_radius_limit

    def reset_scale(self):
        self.radius_limit = self.default_radius_limit

    @staticmethod
    def _nice_radius_limit(peak_value):
        if peak_value <= 0:
            return 10.0

        padded_value = peak_value * 1.2
        magnitude = 10 ** np.floor(np.log10(padded_value))
        normalized = padded_value / magnitude

        if normalized <= 1.0:
            nice_normalized = 1.0
        elif normalized <= 2.0:
            nice_normalized = 2.0
        elif normalized <= 2.5:
            nice_normalized = 2.5
        elif normalized <= 5.0:
            nice_normalized = 5.0
        else:
            nice_normalized = 10.0

        return float(nice_normalized * magnitude)

    def plot_data(self, data, interpolate_type="Linear (직선)", unit="kgf", reset_scale=False):
        if reset_scale:
            self.reset_scale()

        self.ax.clear()
        self.ax.set_theta_offset(np.pi / 2)
        self.ax.set_theta_direction(-1)
        self.ax.set_xticks(self.angles)
        self.ax.set_xticklabels(['Axis 1', 'Axis 2', 'Axis 3', 'Axis 4', 'Axis 5', 'Axis 6'], fontproperties=font_prop)

        peak_value = max(data) if data else 0.0
        if peak_value <= 0:
            suggested_radius_limit = self.min_radius_limit
        else:
            suggested_radius_limit = self._nice_radius_limit(peak_value)

        if reset_scale:
            self.radius_limit = suggested_radius_limit
        elif suggested_radius_limit >= self.radius_limit:
            self.radius_limit = suggested_radius_limit
        else:
            self.radius_limit = suggested_radius_limit + (
                self.radius_limit - suggested_radius_limit
            ) * self.scale_decay
            if abs(self.radius_limit - suggested_radius_limit) <= max(0.1, suggested_radius_limit * 0.05):
                self.radius_limit = suggested_radius_limit

        self.radius_limit = max(self.radius_limit, self.min_radius_limit)

        self.ax.set_ylim(0, self.radius_limit)
        self.ax.set_ylabel(f"Load ({unit})", labelpad=20, fontproperties=font_prop)

        plot_data = data + [data[0]]

        if interpolate_type == "Smooth (Spline 곡선)":
            try:
                extended_angles = np.concatenate([
                    np.array(self.angles) - 2*np.pi,
                    self.angles,
                    np.array(self.angles) + 2*np.pi
                ])
                extended_data = data * 3
                f = interp1d(extended_angles, extended_data, kind='cubic')
                t_smooth = np.linspace(0, 2 * np.pi, 100)
                smooth_data = f(t_smooth)
                smooth_data = np.clip(smooth_data, 0, None)

                self.ax.plot(t_smooth, smooth_data, color='blue', linewidth=2)
                self.ax.fill(t_smooth, smooth_data, color='blue', alpha=0.1)
            except Exception as e:
                self.ax.plot(self.angles_closed, plot_data, color='blue', linewidth=2)
                self.ax.fill(self.angles_closed, plot_data, color='blue', alpha=0.1)
        else:
            self.ax.plot(self.angles_closed, plot_data, color='blue', linewidth=2)
            self.ax.fill(self.angles_closed, plot_data, color='blue', alpha=0.1)

        self.ax.scatter(self.angles, data, color='red', s=40, zorder=5)
        self.draw()

class ClampSimulatorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("6-Axis Clamp Test Machine Simulator")
        self.configure_window_geometry()
        self.unit = "kgf"

        self.is_simulating = False
        self.sim_state = "IDLE"
        self.current_stroke = 0
        self.target_strokes = 3
        self.target_load = 10.0
        self.hold_time = 5.0
        self.hold_ticks = 0
        self.sim_current_load = 0.0
        self.timer_interval = 100
        self.input_source = SIMULATION_SOURCE
        self.data_unit = "kgf"

        self.sensor_zeros = [0.0] * 6
        self.raw_data = [0.0] * 6
        self.latest_live_snapshot = [0.0] * 6
        self.ni_daq_task = None
        self.position_monitor = None
        self.position_jog_command_active = False
        self.latest_live_position_mm = None
        self.latest_live_position_counts = None
        self.position_zero_offset_mm = 0.0
        self.camera_capture = None
        self.camera_capture_thread = None
        self.camera_capture_stop_event = threading.Event()
        self.camera_frame_lock = threading.Lock()
        self.camera_latest_frame = None
        self.camera_latest_frame_id = 0
        self.camera_processed_frame_id = 0
        self.camera_latest_frame_timestamp = 0.0
        self.camera_timer = QTimer(self)
        self.camera_timer.timeout.connect(self.camera_timer_step)
        self.camera_timer.setTimerType(Qt.PreciseTimer)
        self.camera_timer_interval_ms = 16
        self.camera_analysis_interval_ms = 50
        self.camera_metrics_update_interval_ms = 120
        self.camera_analysis_max_width = 960
        self.camera_morph_kernel = np.ones((3, 3), np.uint8)
        self.camera_tracking_roi = None
        self.camera_ring_min_axis_ratio = 0.55
        self.camera_ring_min_circularity = 0.42
        self.camera_ring_max_ellipse_error = 0.18
        self.camera_ring_min_band_silver_ratio = 0.44
        self.camera_ring_min_band_hole_gap = 0.10
        self.camera_ring_band_inner_scale = 0.70
        self.camera_ring_hole_probe_scale = 0.42
        self.camera_profile_sample_count = 32
        self.camera_profile_min_valid_points = 20
        self.camera_profile_sampling_step_px = 1.5
        self.camera_profile_min_inner_ratio = 0.12
        self.camera_profile_min_inner_presence_ratio = 0.60
        self.camera_reference_diameter_mm = None
        self.camera_last_analysis_ts = 0.0
        self.camera_last_metrics_update_ts = 0.0
        self.latest_ring_measurement = None
        self.camera_baseline = None
        self.camera_read_failures = 0

        # 데이터 저장소
        self.stroke_data_history = []  # 각 스트로크 최종 결과 저장
        self.stroke_position_history = []  # 각 스트로크의 대표 위치(mm) 저장
        self.time_series_data = []     # 실시간 시계열 로깅 데이터 저장
        self.time_elapsed = 0.0        # 시계열용 누적 시간

        self.test_start_ts = None
        self.test_start_display_time = None

        self.initUI()

    def configure_window_geometry(self):
        base_width = 1820
        base_height = 1180
        fallback_min_width = 1200
        fallback_min_height = 760

        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(base_width, base_height)
            self.setMinimumSize(fallback_min_width, fallback_min_height)
            return

        available = screen.availableGeometry()
        start_width = min(base_width, available.width())
        start_height = min(base_height, available.height())
        min_width = min(fallback_min_width, available.width())
        min_height = min(fallback_min_height, available.height())

        self.setGeometry(available.x(), available.y(), start_width, start_height)
        self.setMinimumSize(min_width, min_height)

    def initUI(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(14)

        left_container = QWidget()
        left_container.setMinimumWidth(650)
        left_layout = QGridLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setHorizontalSpacing(12)
        left_layout.setVerticalSpacing(10)
        left_layout.setColumnStretch(0, 1)
        left_layout.setColumnStretch(1, 1)
        left_layout.setColumnStretch(2, 3)

        group_report = QGroupBox("Report")
        layout_report = QGridLayout()
        layout_report.addWidget(QLabel("관리번호 (Report No):"), 0, 0)
        self.in_report_no = QLineEdit(f"Q-26-{datetime.now().strftime('%m%d')}-001")
        layout_report.addWidget(self.in_report_no, 0, 1)
        layout_report.addWidget(QLabel("고객사 (Customer):"), 1, 0)
        self.in_customer = QLineEdit("TESLA")
        layout_report.addWidget(self.in_customer, 1, 1)
        layout_report.addWidget(QLabel("차종 (Model):"), 2, 0)
        self.in_model = QLineEdit("PMY")
        layout_report.addWidget(self.in_model, 2, 1)
        layout_report.addWidget(QLabel("품명 (Part Name):"), 3, 0)
        self.in_part_name = QLineEdit("CABJ O-Ring")
        layout_report.addWidget(self.in_part_name, 3, 1)
        layout_report.addWidget(QLabel("품번 (Part No):"), 4, 0)
        self.in_part_no = QLineEdit("GCR0127")
        layout_report.addWidget(self.in_part_no, 4, 1)
        group_report.setLayout(layout_report)
        left_layout.addWidget(group_report, 0, 0)

        group_jig = QGroupBox("Jig / Camera")
        layout_jig = QVBoxLayout()
        self.jig_combo = QComboBox()
        self.jig_combo.addItems(["10.6 mm", "22.6 mm", "32.6 mm"])
        self.jig_combo.currentTextChanged.connect(self.update_camera_focus)
        self.lbl_camera = QLabel("Camera Focus Action: Adjusted to 10.6 mm")
        layout_jig.addWidget(QLabel("Select Jig Size:"))
        layout_jig.addWidget(self.jig_combo)
        layout_jig.addWidget(self.lbl_camera)
        group_jig.setLayout(layout_jig)
        left_layout.addWidget(group_jig, 0, 1)

        group_params = QGroupBox("Test")
        layout_params = QGridLayout()
        layout_params.addWidget(QLabel("Min Length (mm):"), 0, 0)
        self.in_min_len = QLineEdit("0.0")
        layout_params.addWidget(self.in_min_len, 0, 1)
        layout_params.addWidget(QLabel("Max Length (mm):"), 1, 0)
        self.in_max_len = QLineEdit("50.0")
        layout_params.addWidget(self.in_max_len, 1, 1)
        layout_params.addWidget(QLabel("Speed (mm/min):"), 2, 0)
        self.in_speed = QLineEdit("10")
        layout_params.addWidget(self.in_speed, 2, 1)
        layout_params.addWidget(QLabel("Hold Time (sec):"), 3, 0)
        self.in_hold = QLineEdit("5.0")
        layout_params.addWidget(self.in_hold, 3, 1)
        layout_params.addWidget(QLabel("Target Load:"), 4, 0)
        self.in_load = QLineEdit("10.0")
        layout_params.addWidget(self.in_load, 4, 1)
        layout_params.addWidget(QLabel("Operation Strokes:"), 5, 0)
        self.in_strokes = QLineEdit("3")
        layout_params.addWidget(self.in_strokes, 5, 1)
        self.lbl_status = QLabel("Status: Ready")
        self.lbl_status.setStyleSheet("color: blue; font-weight: bold;")
        layout_params.addWidget(self.lbl_status, 6, 0, 1, 2)
        group_params.setLayout(layout_params)
        left_layout.addWidget(group_params, 1, 0)

        group_settings = QGroupBox("Settings")
        layout_settings = QVBoxLayout()
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["kgf", "N"])
        self.unit_combo.currentTextChanged.connect(self.change_unit)
        layout_settings.addWidget(QLabel("Unit Selection:"))
        layout_settings.addWidget(self.unit_combo)

        self.interp_combo = QComboBox()
        self.interp_combo.addItems(["Linear (직선)", "Smooth (Spline 곡선)"])
        self.interp_combo.currentTextChanged.connect(self.update_chart)
        layout_settings.addWidget(QLabel("Graph Interpolation:"))
        layout_settings.addWidget(self.interp_combo)

        self.btn_zero = QPushButton("Zero Sensors & Reset Data")
        self.btn_zero.clicked.connect(self.zero_sensors)
        layout_settings.addWidget(self.btn_zero)
        group_settings.setLayout(layout_settings)
        left_layout.addWidget(group_settings, 1, 1)

        group_source = QGroupBox("Input")
        group_source.setMaximumHeight(100)
        layout_source = QVBoxLayout(group_source)
        source_button_layout = QHBoxLayout()
        source_button_layout.setSpacing(8)
        self.source_buttons = {}
        source_button_specs = [
            ("Simulation", SIMULATION_SOURCE),
            ("FC400 + USB-6002", FC400_SOURCE),
        ]
        for button_label, source_name in source_button_specs:
            button = QPushButton(button_label)
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setMinimumHeight(34)
            button.setStyleSheet(
                "QPushButton {padding: 8px 10px; background-color: #F5F7FA; border: 1px solid #B8C1CC;}"
                "QPushButton:checked {background-color: #1976D2; color: white; border: 1px solid #0D47A1;}"
            )
            button.clicked.connect(
                lambda _checked, selected_source=source_name: self.on_input_source_button_clicked(selected_source)
            )
            self.source_buttons[source_name] = button
            source_button_layout.addWidget(button)
        layout_source.addLayout(source_button_layout)
        left_layout.addWidget(group_source, 2, 0, 1, 2)

        group_fc400 = QGroupBox("FC400 / USB-6002")
        group_fc400.setMaximumHeight(220)
        layout_fc400 = QGridLayout()
        layout_fc400.setColumnStretch(1, 1)

        layout_fc400.setColumnStretch(3, 1)

        layout_fc400.addWidget(QLabel("Physical Channel:"), 0, 0)
        self.in_fc400_daq_channel = QLineEdit("Dev1/ai0")
        layout_fc400.addWidget(self.in_fc400_daq_channel, 0, 1)

        layout_fc400.addWidget(QLabel("Terminal Mode:"), 0, 2)
        self.fc400_terminal_combo = QComboBox()
        self.fc400_terminal_combo.addItems(["Differential", "RSE"])
        layout_fc400.addWidget(self.fc400_terminal_combo, 0, 3)

        layout_fc400.addWidget(QLabel("Zero Output [V]:"), 1, 0)
        self.in_fc400_zero_voltage = QLineEdit("0.0")
        layout_fc400.addWidget(self.in_fc400_zero_voltage, 1, 1)

        layout_fc400.addWidget(QLabel("Full-scale Output [V]:"), 1, 2)
        self.in_fc400_full_scale_voltage = QLineEdit("10.0")
        layout_fc400.addWidget(self.in_fc400_full_scale_voltage, 1, 3)

        layout_fc400.addWidget(QLabel("FC400 Full Scale:"), 2, 0)
        self.in_fc400_full_scale_load = QLineEdit("1000.0")
        layout_fc400.addWidget(self.in_fc400_full_scale_load, 2, 1)

        layout_fc400.addWidget(QLabel("FC400 Unit:"), 2, 2)
        self.fc400_device_unit_combo = QComboBox()
        self.fc400_device_unit_combo.addItems(["N", "kgf"])
        self.fc400_device_unit_combo.setCurrentText("N")
        self.fc400_device_unit_combo.currentTextChanged.connect(self.on_source_configuration_changed)
        layout_fc400.addWidget(self.fc400_device_unit_combo, 2, 3)

        layout_fc400.addWidget(QLabel("Sample Rate [S/s]:"), 3, 0)
        self.in_fc400_sample_rate = QLineEdit("1000")
        layout_fc400.addWidget(self.in_fc400_sample_rate, 3, 1)

        self.btn_refresh_fc400_daq = QPushButton("Refresh NI Devices")
        self.btn_refresh_fc400_daq.clicked.connect(self.refresh_ni_devices)
        layout_fc400.addWidget(self.btn_refresh_fc400_daq, 3, 2, 1, 2)

        init_fc400_status = (
            "FC400 voltage output: Differential wiring V OUT -> AI0, COM -> AI4 (AI0-)"
        )
        if not NIDAQMX_AVAILABLE:
            init_fc400_status = f"USB-6002: nidaqmx import failed - {NIDAQMX_IMPORT_ERROR}"
        self.lbl_fc400_status = QLabel(init_fc400_status)
        self.lbl_fc400_status.setWordWrap(True)
        layout_fc400.addWidget(self.lbl_fc400_status, 4, 0, 1, 4)

        group_fc400.setLayout(layout_fc400)
        left_layout.addWidget(group_fc400, 3, 0, 1, 2)

        group_position = QGroupBox("MR-MC240N 6-Axis")
        layout_position = QVBoxLayout()
        layout_position.setContentsMargins(12, 14, 12, 12)
        layout_position.setSpacing(10)

        self.chk_position_monitor = QCheckBox("Enable MR-MC240N position board")
        self.chk_position_monitor.toggled.connect(self.on_position_monitor_toggled)
        if os.name != "nt":
            self.chk_position_monitor.setEnabled(False)
        layout_position.addWidget(self.chk_position_monitor)

        connection_group = QGroupBox("Controller connection")
        connection_grid = QGridLayout(connection_group)
        connection_grid.setContentsMargins(10, 12, 10, 10)
        connection_grid.setHorizontalSpacing(10)
        connection_grid.setVerticalSpacing(9)
        connection_grid.setColumnStretch(1, 1)
        connection_grid.setColumnStretch(3, 1)

        connection_grid.addWidget(QLabel("Connection:"), 0, 0)
        self.mr_connection_combo = QComboBox()
        self.mr_connection_combo.addItems(
            [MR_CONNECTION_USB_MAINTENANCE, MR_CONNECTION_PCIE_API]
        )
        self.mr_connection_combo.setCurrentText(MR_CONNECTION_USB_MAINTENANCE)
        self.mr_connection_combo.currentTextChanged.connect(
            self.on_position_connection_changed
        )
        connection_grid.addWidget(self.mr_connection_combo, 0, 1)

        connection_grid.addWidget(QLabel("DLL Path (optional):"), 0, 2)
        self.in_mr_dll_path = QLineEdit("")
        connection_grid.addWidget(self.in_mr_dll_path, 0, 3)

        connection_grid.addWidget(QLabel("Board ID:"), 1, 0)
        self.in_mr_board_id = QLineEdit("0")
        connection_grid.addWidget(self.in_mr_board_id, 1, 1)

        connection_grid.addWidget(QLabel("Axis No:"), 1, 2)
        self.in_mr_axis_no = QComboBox()
        self.in_mr_axis_no.addItems([str(axis) for axis in range(1, 7)])
        self.in_mr_axis_no.setToolTip(
            "HG-KR13 + MR-J4-10B-RJ six-axis setup. "
            "Amplifier rotary switch 0..5 maps to control axis 1..6."
        )
        self.in_mr_axis_no.currentTextChanged.connect(
            self.on_position_axis_changed
        )
        connection_grid.addWidget(self.in_mr_axis_no, 1, 3)

        connection_grid.addWidget(QLabel("Command Units / mm:"), 2, 0)
        self.in_mr_counts_per_mm = QLineEdit("1000.0")
        self.in_mr_counts_per_mm.setToolTip(
            "HG-KR13 + BTK1404 direct-drive preset: "
            "electronic gear uses 1 command unit = 1 µm."
        )
        connection_grid.addWidget(self.in_mr_counts_per_mm, 2, 1)

        self.chk_mr_auto_start = QCheckBox("PCIe only: try sscSystemStart()")
        self.chk_mr_auto_start.setEnabled(False)
        connection_grid.addWidget(self.chk_mr_auto_start, 2, 2)

        self.chk_mr_motion_arm = QCheckBox("Arm motion commands")
        self.chk_mr_motion_arm.toggled.connect(self.on_position_motion_arm_toggled)
        connection_grid.addWidget(self.chk_mr_motion_arm, 2, 3)

        self.btn_mr_connect = QPushButton("Connect USB Controller")
        self.btn_mr_connect.setMinimumHeight(32)
        self.btn_mr_connect.clicked.connect(self.test_position_board_connection)
        connection_grid.addWidget(self.btn_mr_connect, 3, 0, 1, 2)
        self.btn_mr_system_start = QPushButton("USB System Start")
        self.btn_mr_system_start.setMinimumHeight(32)
        self.btn_mr_system_start.clicked.connect(self.start_position_usb_system)
        connection_grid.addWidget(self.btn_mr_system_start, 3, 2, 1, 2)
        self.btn_mr_apply_six_axis = QPushButton(
            "Apply HG-KR13 ×6 / BTK1404 Preset"
        )
        self.btn_mr_apply_six_axis.setMinimumHeight(32)
        self.btn_mr_apply_six_axis.clicked.connect(
            self.apply_position_six_axis_preset
        )
        connection_grid.addWidget(self.btn_mr_apply_six_axis, 4, 0, 1, 4)
        layout_position.addWidget(connection_group)

        motion_group = QGroupBox("Axis setup and operation")
        motion_grid = QGridLayout(motion_group)
        motion_grid.setContentsMargins(10, 12, 10, 10)
        motion_grid.setHorizontalSpacing(10)
        motion_grid.setVerticalSpacing(9)
        motion_grid.setColumnStretch(1, 1)
        motion_grid.setColumnStretch(3, 1)

        motion_grid.addWidget(QLabel("Speed [mm/min]:"), 0, 0)
        self.in_mr_motion_speed = QLineEdit("100")
        motion_grid.addWidget(self.in_mr_motion_speed, 0, 1)

        motion_grid.addWidget(QLabel("Accel / Decel [ms]:"), 0, 2)
        motion_time_layout = QHBoxLayout()
        self.in_mr_acceleration_ms = QLineEdit("500")
        self.in_mr_deceleration_ms = QLineEdit("500")
        motion_time_layout.addWidget(self.in_mr_acceleration_ms)
        motion_time_layout.addWidget(self.in_mr_deceleration_ms)
        motion_grid.addLayout(motion_time_layout, 0, 3)

        motion_grid.addWidget(QLabel("Relative Move [mm]:"), 1, 0)
        self.in_mr_relative_move_mm = QLineEdit("1.0")
        motion_grid.addWidget(self.in_mr_relative_move_mm, 1, 1)

        servo_layout = QHBoxLayout()
        self.btn_mr_servo_on = QPushButton("Servo ON")
        self.btn_mr_servo_on.clicked.connect(lambda: self.set_position_servo(True))
        self.btn_mr_servo_off = QPushButton("Servo OFF")
        self.btn_mr_servo_off.clicked.connect(lambda: self.set_position_servo(False))
        servo_layout.addWidget(self.btn_mr_servo_on)
        servo_layout.addWidget(self.btn_mr_servo_off)
        motion_grid.addLayout(servo_layout, 1, 2, 1, 2)

        motion_layout = QHBoxLayout()
        self.btn_mr_home = QPushButton("Home")
        self.btn_mr_home.clicked.connect(self.start_position_home)
        self.btn_mr_move_relative = QPushButton("Move Relative")
        self.btn_mr_move_relative.clicked.connect(self.start_position_relative_move)
        motion_layout.addWidget(self.btn_mr_home)
        motion_layout.addWidget(self.btn_mr_move_relative)
        motion_grid.addLayout(motion_layout, 2, 0, 1, 2)

        jog_layout = QHBoxLayout()
        self.btn_mr_jog_minus = QPushButton("JOG -")
        self.btn_mr_jog_minus.pressed.connect(
            lambda: self.start_position_jog(MrMc240nPositionController.SSC_DIR_MINUS)
        )
        self.btn_mr_jog_minus.released.connect(self.stop_position_jog)
        self.btn_mr_jog_plus = QPushButton("JOG +")
        self.btn_mr_jog_plus.pressed.connect(
            lambda: self.start_position_jog(MrMc240nPositionController.SSC_DIR_PLUS)
        )
        self.btn_mr_jog_plus.released.connect(self.stop_position_jog)
        jog_layout.addWidget(self.btn_mr_jog_minus)
        jog_layout.addWidget(self.btn_mr_jog_plus)
        motion_grid.addLayout(jog_layout, 2, 2, 1, 2)

        stop_layout = QHBoxLayout()
        self.btn_mr_stop = QPushButton("Stop")
        self.btn_mr_stop.clicked.connect(lambda: self.stop_position_motion(False))
        self.btn_mr_rapid_stop = QPushButton("RAPID STOP")
        self.btn_mr_rapid_stop.clicked.connect(lambda: self.stop_position_motion(True))
        self.btn_mr_rapid_stop.setStyleSheet(
            "background-color: #C62828; color: white; font-weight: bold;"
        )
        stop_layout.addWidget(self.btn_mr_stop)
        stop_layout.addWidget(self.btn_mr_rapid_stop)
        motion_grid.addLayout(stop_layout, 3, 0, 1, 2)

        self.btn_mr_refresh_status = QPushButton("Refresh Axis Status")
        self.btn_mr_refresh_status.clicked.connect(self.refresh_position_axis_status)
        motion_grid.addWidget(self.btn_mr_refresh_status, 3, 2, 1, 2)
        layout_position.addWidget(motion_group)

        overview_group = QGroupBox("Six-axis overview")
        overview_grid = QGridLayout(overview_group)
        overview_grid.setContentsMargins(8, 10, 8, 8)
        overview_grid.setHorizontalSpacing(8)
        overview_grid.setVerticalSpacing(8)
        self.mr_axis_status_labels = []
        for axis in range(1, 7):
            label = QLabel(f"Axis {axis}\nNOT CHECKED")
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(54)
            label.setStyleSheet(
                "QLabel { background: #F5F7FA; border: 1px solid #D8DEE9; "
                "border-radius: 4px; padding: 5px; }"
            )
            overview_grid.addWidget(label, (axis - 1) // 3, (axis - 1) % 3)
            overview_grid.setColumnStretch((axis - 1) % 3, 1)
            self.mr_axis_status_labels.append(label)
        layout_position.addWidget(overview_group)

        mr_status = "MR-MC240N: Windows + matching Mitsubishi API DLL required"
        if os.name != "nt":
            mr_status = f"MR-MC240N: {MR_MC240N_WINDOWS_ONLY_MESSAGE}"
        self.lbl_mr_status = QLabel(mr_status)
        self.lbl_mr_status.setWordWrap(True)
        self.lbl_mr_status.setMinimumHeight(42)
        self.lbl_mr_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_mr_status.setStyleSheet(
            "QLabel { background: #F5F7FA; border: 1px solid #D8DEE9; "
            "border-radius: 4px; padding: 7px; }"
        )
        layout_position.addWidget(self.lbl_mr_status)

        group_position.setLayout(layout_position)
        left_layout.addWidget(group_position, 0, 2, 5, 1)

        self.btn_start = QPushButton("Start Test Simulation")
        self.btn_start.clicked.connect(self.toggle_simulation)
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")
        left_layout.addWidget(self.btn_start, 4, 0, 1, 2)
        for widget_type in (QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox):
            for widget in left_container.findChildren(widget_type):
                policy = widget.sizePolicy()
                policy.setHorizontalPolicy(QSizePolicy.Ignored)
                widget.setSizePolicy(policy)
        main_layout.addWidget(left_container, 13)

        right_container = QWidget()
        right_container.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        right_panel = QVBoxLayout(right_container)
        right_panel.setContentsMargins(0, 0, 0, 0)
        top_visual_layout = QVBoxLayout()
        top_visual_layout.setSpacing(12)

        self.chart = SpiderChartCanvas(self, width=6, height=5)
        self.chart.setMinimumHeight(180)
        self.chart.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        top_visual_layout.addWidget(self.chart, 2)

        group_camera = QGroupBox("Camera / Ring")
        layout_camera = QGridLayout()
        layout_camera.setColumnStretch(1, 1)
        layout_camera.setColumnStretch(3, 1)

        layout_camera.addWidget(QLabel("Camera Index:"), 0, 0)
        self.in_camera_index = QLineEdit("0")
        layout_camera.addWidget(self.in_camera_index, 0, 1)

        layout_camera.addWidget(QLabel("Resolution:"), 0, 2)
        self.camera_resolution_combo = QComboBox()
        self.camera_resolution_combo.addItems(["640x480", "1280x720", "1920x1080"])
        self.camera_resolution_combo.setCurrentText("1280x720")
        layout_camera.addWidget(self.camera_resolution_combo, 0, 3)

        layout_camera.addWidget(QLabel("Known Ring OD [mm]:"), 1, 0)
        self.in_camera_reference_diameter = QLineEdit(self.jig_combo.currentText().split()[0])
        self.in_camera_reference_diameter.setModified(False)
        self.in_camera_reference_diameter.setToolTip("실제 링 외경(mm)을 입력하면 pixel 값을 mm로 환산합니다.")
        layout_camera.addWidget(self.in_camera_reference_diameter, 1, 1)

        layout_camera.addWidget(QLabel("Clamp Ring Color:"), 2, 0)
        self.camera_ring_color_combo = QComboBox()
        for profile_key, profile in CAMERA_RING_COLOR_PROFILES.items():
            self.camera_ring_color_combo.addItem(profile["label"], profile_key)
        self.camera_ring_color_combo.setCurrentIndex(self.camera_ring_color_combo.findData("silver"))
        self.camera_ring_color_combo.currentTextChanged.connect(self.on_camera_ring_color_changed)
        layout_camera.addWidget(self.camera_ring_color_combo, 2, 1)

        self.btn_camera_toggle = QPushButton("Open Camera")
        self.btn_camera_toggle.clicked.connect(self.toggle_camera)
        layout_camera.addWidget(self.btn_camera_toggle, 1, 2)

        self.btn_camera_baseline = QPushButton("Capture Baseline")
        self.btn_camera_baseline.clicked.connect(self.capture_ring_baseline)
        self.btn_camera_baseline.setEnabled(False)
        layout_camera.addWidget(self.btn_camera_baseline, 1, 3)

        self.btn_camera_clear_baseline = QPushButton("Clear Baseline")
        self.btn_camera_clear_baseline.clicked.connect(self.clear_ring_baseline)
        self.btn_camera_clear_baseline.setEnabled(False)
        layout_camera.addWidget(self.btn_camera_clear_baseline, 2, 2, 1, 2)

        init_camera_status = "Camera: connect a UVC camera and click Open Camera"
        if not CV2_AVAILABLE:
            init_camera_status = f"Camera: OpenCV import failed - {CV2_IMPORT_ERROR}"
            self.btn_camera_toggle.setEnabled(False)
        self.lbl_camera_status = QLabel(init_camera_status)
        self.lbl_camera_status.setWordWrap(True)
        layout_camera.addWidget(self.lbl_camera_status, 3, 0, 1, 4)

        self.lbl_camera_preview = QLabel("Camera preview is not running.")
        self.lbl_camera_preview.setAlignment(Qt.AlignCenter)
        self.lbl_camera_preview.setMinimumSize(260, 160)
        self.lbl_camera_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.lbl_camera_preview.setStyleSheet(
            "background-color: #111111; color: #DDDDDD; border: 1px solid #444444; padding: 8px;"
        )
        layout_camera.addWidget(self.lbl_camera_preview, 4, 0, 1, 4)

        self.lbl_ring_metrics = QLabel(
            "Ring measurement:\n"
            "- 32-point profile; select the clamp ring color.\n"
            "- Open and center the ring, then capture baseline."
        )
        self.lbl_ring_metrics.setWordWrap(True)
        self.lbl_ring_metrics.setStyleSheet(
            "background-color: #F8F8F8; border: 1px solid #D0D0D0; padding: 8px; font-family: monospace;"
        )
        layout_camera.addWidget(self.lbl_ring_metrics, 5, 0, 1, 4)

        group_camera.setLayout(layout_camera)
        for widget_type in (QLabel, QLineEdit, QPushButton, QComboBox):
            for widget in group_camera.findChildren(widget_type):
                policy = widget.sizePolicy()
                policy.setHorizontalPolicy(QSizePolicy.Ignored)
                widget.setSizePolicy(policy)
        top_visual_layout.addWidget(group_camera, 3)
        right_panel.addLayout(top_visual_layout, 3)

        self.table = QTableWidget(6, 4)
        self.table.setMinimumHeight(130)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for i in range(6):
            self.table.setItem(i, 0, QTableWidgetItem(f"Axis {i+1}"))
            for j in range(1, 4):
                self.table.setItem(i, j, QTableWidgetItem("0.00"))
        right_panel.addWidget(self.table, 1)

        btn_layout = QHBoxLayout()
        self.btn_csv = QPushButton("Export CSV (데이터 저장)")
        self.btn_csv.clicked.connect(self.export_csv)
        self.btn_pdf = QPushButton("Print Report (A4 성적서 출력)")
        self.btn_pdf.clicked.connect(self.export_pdf)
        btn_layout.addWidget(self.btn_csv)
        btn_layout.addWidget(self.btn_pdf)
        right_panel.addLayout(btn_layout)

        main_layout.addWidget(right_container, 7)
        self.timer = QTimer()
        self.timer.timeout.connect(self.timer_step)

        self.on_position_monitor_toggled(False)
        self.update_table_headers()
        self.update_chart()
        self.on_input_source_changed(self.input_source)
        self.update_camera_button_state()
        self.update_ring_metrics_label()

    def update_camera_focus(self, size):
        self.lbl_camera.setText(f"Camera Focus Action: Adjusted to {size}")
        if hasattr(self, "in_camera_reference_diameter") and not self.in_camera_reference_diameter.isModified():
            self.in_camera_reference_diameter.setText(size.split()[0])

    def set_camera_preview_message(self, message):
        self.lbl_camera_preview.clear()
        self.lbl_camera_preview.setText(message)

    def set_camera_status_text(self, message):
        if self.lbl_camera_status.text() != message:
            self.lbl_camera_status.setText(message)

    def get_camera_reference_diameter_value(self):
        reference_text = self.in_camera_reference_diameter.text().strip()
        if not reference_text:
            return None

        reference_diameter_mm = float(reference_text)
        if reference_diameter_mm <= 0:
            raise ValueError("Known Ring OD [mm]는 0보다 커야 합니다.")
        return reference_diameter_mm

    def get_camera_ring_color_key(self):
        if not hasattr(self, "camera_ring_color_combo"):
            return "silver"
        color_key = self.camera_ring_color_combo.currentData()
        if color_key not in CAMERA_RING_COLOR_PROFILES:
            return "silver"
        return color_key

    def get_camera_ring_color_profile(self):
        return CAMERA_RING_COLOR_PROFILES[self.get_camera_ring_color_key()]

    def get_camera_ring_target_description(self):
        return self.get_camera_ring_color_profile()["description"]

    def get_camera_ring_color_tip(self):
        color_key = self.get_camera_ring_color_key()
        if color_key in {"silver", "white"}:
            return "use a plain dark background so the selected ring color stands out"
        if color_key == "black":
            return "use a bright plain background so the selected ring color stands out"
        return "use a background that contrasts clearly with the selected ring color"

    def on_camera_ring_color_changed(self, _text=None):
        self.camera_tracking_roi = None
        self.latest_ring_measurement = None
        self.camera_last_analysis_ts = 0.0
        self.camera_last_metrics_update_ts = 0.0
        self.update_camera_button_state()
        self.update_ring_metrics_label()
        if self.camera_capture is not None:
            color_name = self.get_camera_ring_color_profile()["status_name"]
            self.set_camera_status_text(
                f"Camera: ring color changed to {color_name}; waiting for detection"
            )

    def update_camera_button_state(self):
        camera_open = self.camera_capture is not None
        if CV2_AVAILABLE:
            self.btn_camera_toggle.setEnabled(True)
            self.btn_camera_toggle.setText("Close Camera" if camera_open else "Open Camera")
        else:
            self.btn_camera_toggle.setEnabled(False)
            self.btn_camera_toggle.setText("Open Camera")

        self.btn_camera_baseline.setEnabled(camera_open and self.latest_ring_measurement is not None)
        self.btn_camera_clear_baseline.setEnabled(self.camera_baseline is not None)

    def get_camera_config(self):
        if not CV2_AVAILABLE:
            raise RuntimeError(f"OpenCV를 불러오지 못했습니다: {CV2_IMPORT_ERROR}")

        camera_index = int(self.in_camera_index.text())
        if camera_index < 0:
            raise ValueError("Camera Index는 0 이상의 정수여야 합니다.")

        resolution_text = self.camera_resolution_combo.currentText()
        frame_width, frame_height = [int(token) for token in resolution_text.split("x")]

        reference_diameter_mm = self.get_camera_reference_diameter_value()

        return {
            "camera_index": camera_index,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "reference_diameter_mm": reference_diameter_mm,
        }

    def camera_capture_loop(self):
        local_failures = 0
        while not self.camera_capture_stop_event.is_set():
            capture = self.camera_capture
            if capture is None:
                break

            ok, frame = capture.read()
            if not ok or frame is None:
                local_failures += 1
                self.camera_read_failures = local_failures
                time.sleep(0.005)
                continue

            local_failures = 0
            self.camera_read_failures = 0
            with self.camera_frame_lock:
                self.camera_latest_frame = frame
                self.camera_latest_frame_id += 1
                self.camera_latest_frame_timestamp = time.monotonic()

        self.camera_read_failures = local_failures

    def toggle_camera(self):
        if self.camera_capture is None:
            try:
                self.open_camera()
            except Exception as exc:
                self.close_camera(reset_status=False)
                QMessageBox.warning(self, "Camera Error", f"UVC 카메라를 열지 못했습니다.\n{exc}")
        else:
            self.close_camera()

    def open_camera(self):
        if self.camera_capture is not None:
            return

        config = self.get_camera_config()
        api_preference = cv2.CAP_DSHOW if os.name == "nt" and hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
        capture = cv2.VideoCapture(config["camera_index"], api_preference)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"Camera index {config['camera_index']}를 열 수 없습니다.")

        if hasattr(cv2, "CAP_PROP_FOURCC") and hasattr(cv2, "VideoWriter_fourcc"):
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, config["frame_width"])
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config["frame_height"])
        if hasattr(cv2, "CAP_PROP_FPS"):
            capture.set(cv2.CAP_PROP_FPS, 30)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self.camera_capture = capture
        self.camera_capture_stop_event.clear()
        self.camera_read_failures = 0
        self.camera_reference_diameter_mm = config["reference_diameter_mm"]
        self.camera_tracking_roi = None
        self.camera_last_analysis_ts = 0.0
        self.camera_last_metrics_update_ts = 0.0
        self.latest_ring_measurement = None
        with self.camera_frame_lock:
            self.camera_latest_frame = None
            self.camera_latest_frame_id = 0
            self.camera_processed_frame_id = 0
            self.camera_latest_frame_timestamp = 0.0
        self.camera_capture_thread = threading.Thread(
            target=self.camera_capture_loop,
            name="camera-capture",
            daemon=True,
        )
        self.camera_capture_thread.start()
        self.camera_timer.start(self.camera_timer_interval_ms)
        self.set_camera_status_text(
            f"Camera: opened UVC index {config['camera_index']} @ {config['frame_width']}x{config['frame_height']}"
        )
        self.set_camera_preview_message("Camera is starting...")
        self.update_ring_metrics_label()
        self.update_camera_button_state()

    def close_camera(self, reset_status=True):
        self.camera_timer.stop()
        self.camera_capture_stop_event.set()
        capture = self.camera_capture
        self.camera_capture = None
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass
        if self.camera_capture_thread is not None:
            self.camera_capture_thread.join(timeout=1.0)
        self.camera_capture_thread = None

        with self.camera_frame_lock:
            self.camera_latest_frame = None
            self.camera_latest_frame_id = 0
            self.camera_processed_frame_id = 0
            self.camera_latest_frame_timestamp = 0.0
        self.camera_read_failures = 0
        self.camera_reference_diameter_mm = None
        self.camera_tracking_roi = None
        self.camera_last_analysis_ts = 0.0
        self.camera_last_metrics_update_ts = 0.0
        self.latest_ring_measurement = None
        if reset_status:
            if CV2_AVAILABLE:
                self.set_camera_status_text("Camera: closed")
            else:
                self.set_camera_status_text(f"Camera: OpenCV import failed - {CV2_IMPORT_ERROR}")
        self.set_camera_preview_message("Camera preview is not running.")
        self.update_ring_metrics_label()
        self.update_camera_button_state()

    def clear_ring_baseline(self):
        self.camera_baseline = None
        self.update_ring_metrics_label()
        self.update_camera_button_state()
        if self.camera_capture is not None:
            self.set_camera_status_text("Camera: running, baseline cleared")
        else:
            self.set_camera_status_text("Camera: baseline cleared")

    def capture_ring_baseline(self):
        if self.latest_ring_measurement is None:
            QMessageBox.warning(self, "Baseline Error", "기준 형상을 캡처하려면 먼저 링이 검출되어야 합니다.")
            return

        measurement = self.latest_ring_measurement
        self.camera_baseline = {
            "major_px": measurement["major_px"],
            "minor_px": measurement["minor_px"],
            "mean_px": measurement["mean_px"],
            "ovality_px": measurement["ovality_px"],
            "profile_point_count": measurement["profile_point_count"],
            "profile_valid_count": measurement["profile_valid_count"],
            "profile_outer_radii_px": list(measurement["profile_outer_radii_px"]),
            "profile_inner_radii_px": list(measurement["profile_inner_radii_px"]),
            "profile_valid_mask": list(measurement["profile_valid_mask"]),
            "reference_diameter_mm": measurement.get("reference_diameter_mm"),
            "mm_per_px": measurement.get("mm_per_px"),
        }
        self.set_camera_status_text("Camera: baseline captured from current ring shape")
        self.update_ring_metrics_label()
        self.update_camera_button_state()

    def camera_timer_step(self):
        if self.camera_capture is None:
            return

        frame = None
        frame_id = self.camera_processed_frame_id
        with self.camera_frame_lock:
            latest_frame = self.camera_latest_frame
            latest_frame_id = self.camera_latest_frame_id
            latest_frame_timestamp = self.camera_latest_frame_timestamp
            if latest_frame is not None and latest_frame_id != self.camera_processed_frame_id:
                frame = latest_frame
                frame_id = latest_frame_id
                self.camera_processed_frame_id = latest_frame_id

        if frame is None:
            if self.camera_read_failures >= 5:
                self.set_camera_status_text("Camera: frame read failed. Check the UVC device connection.")
            return

        now = time.monotonic()
        measurement = self.latest_ring_measurement
        should_analyze = (
            measurement is None
            or self.camera_last_analysis_ts == 0.0
            or (now - self.camera_last_analysis_ts) >= (self.camera_analysis_interval_ms / 1000.0)
        )
        if should_analyze:
            try:
                self.camera_reference_diameter_mm = self.get_camera_reference_diameter_value()
                measurement = self.measure_ring_from_frame(frame)
            except Exception as exc:
                self.set_camera_status_text(f"Camera: measurement failed - {exc}")
                measurement = None
            self.latest_ring_measurement = measurement
            self.camera_last_analysis_ts = now

        display_frame = self.draw_ring_measurement_overlay(frame, measurement)
        self.render_camera_frame(display_frame)
        if should_analyze or (now - self.camera_last_metrics_update_ts) >= (self.camera_metrics_update_interval_ms / 1000.0):
            self.update_ring_metrics_label()
            self.camera_last_metrics_update_ts = now

        baseline_ready = self.camera_capture is not None and self.latest_ring_measurement is not None
        if self.btn_camera_baseline.isEnabled() != baseline_ready:
            self.btn_camera_baseline.setEnabled(baseline_ready)

        target_description = self.get_camera_ring_target_description()
        if self.latest_ring_measurement is None:
            self.set_camera_status_text(
                f"Camera: running, but the {target_description} was not detected"
            )
        elif self.camera_baseline is not None:
            self.set_camera_status_text(
                f"Camera: running, {target_description} detected, baseline active"
            )
        else:
            self.set_camera_status_text(f"Camera: running, {target_description} detected")

    def build_camera_tracking_roi(self, measurement, frame_shape):
        frame_height, frame_width = frame_shape[:2]
        center_x, center_y = measurement["center"]
        half_span = 0.5 * max(measurement["major_px"], measurement["minor_px"])
        padding = max(40, int(half_span * 0.9))

        x0 = max(0, int(center_x - half_span - padding))
        y0 = max(0, int(center_y - half_span - padding))
        x1 = min(frame_width, int(center_x + half_span + padding))
        y1 = min(frame_height, int(center_y + half_span + padding))

        if (x1 - x0) < 20 or (y1 - y0) < 20:
            return None
        return (x0, y0, x1, y1)

    def get_camera_analysis_view(self, frame, use_tracking_roi=True):
        offset_x = 0
        offset_y = 0
        roi_used = False
        analysis_frame = frame

        if use_tracking_roi and self.camera_tracking_roi is not None:
            x0, y0, x1, y1 = self.camera_tracking_roi
            if (x1 - x0) >= 20 and (y1 - y0) >= 20:
                analysis_frame = frame[y0:y1, x0:x1]
                offset_x = x0
                offset_y = y0
                roi_used = True

        scale = 1.0
        analysis_height, analysis_width = analysis_frame.shape[:2]
        if analysis_width > self.camera_analysis_max_width:
            scale = self.camera_analysis_max_width / float(analysis_width)
            resized_width = max(1, int(round(analysis_width * scale)))
            resized_height = max(1, int(round(analysis_height * scale)))
            analysis_frame = cv2.resize(
                analysis_frame,
                (resized_width, resized_height),
                interpolation=cv2.INTER_AREA,
            )

        return analysis_frame, offset_x, offset_y, scale, roi_used

    @staticmethod
    def map_camera_ellipse_to_frame(ellipse, offset_x, offset_y, scale_factor):
        (center_x, center_y), (axis_a, axis_b), angle = ellipse
        return (
            (float(center_x * scale_factor + offset_x), float(center_y * scale_factor + offset_y)),
            (float(axis_a * scale_factor), float(axis_b * scale_factor)),
            float(angle),
        )

    def build_ring_color_mask(self, analysis_frame):
        profile = self.get_camera_ring_color_profile()
        mode = profile["mode"]

        hsv_frame = cv2.cvtColor(analysis_frame, cv2.COLOR_BGR2HSV)
        color_mask = None

        if mode in {"neutral", "dark-neutral"}:
            lab_frame = cv2.cvtColor(analysis_frame, cv2.COLOR_BGR2LAB)
            saturation = hsv_frame[:, :, 1]
            value = hsv_frame[:, :, 2]
            a_channel = lab_frame[:, :, 1].astype(np.int16)
            b_channel = lab_frame[:, :, 2].astype(np.int16)

            color_mask = (
                (saturation <= profile["s_max"])
                & (np.abs(a_channel - 128) <= profile["neutral_tol"])
                & (np.abs(b_channel - 128) <= profile["neutral_tol"])
            )
            if mode == "neutral":
                dynamic_value_floor = min(
                    profile["v_floor_ceiling"],
                    max(
                        profile["v_min"],
                        int(np.percentile(value, profile["v_floor_percentile"])),
                    ),
                )
                color_mask &= value >= dynamic_value_floor
            else:
                color_mask &= value <= profile["v_max"]
            color_mask = color_mask.astype(np.uint8) * 255
        else:
            color_mask = np.zeros(hsv_frame.shape[:2], dtype=np.uint8)
            for lower_bound, upper_bound in profile["ranges"]:
                lower = np.array(lower_bound, dtype=np.uint8)
                upper = np.array(upper_bound, dtype=np.uint8)
                color_mask = cv2.bitwise_or(color_mask, cv2.inRange(hsv_frame, lower, upper))

        color_mask = cv2.morphologyEx(
            color_mask,
            cv2.MORPH_OPEN,
            self.camera_morph_kernel,
            iterations=1,
        )
        color_mask = cv2.morphologyEx(
            color_mask,
            cv2.MORPH_CLOSE,
            self.camera_morph_kernel,
            iterations=2,
        )
        return color_mask

    @staticmethod
    def calculate_ellipse_fit_error(contour, ellipse):
        (center_x, center_y), (axis_a, axis_b), angle = ellipse
        semi_major = max(float(axis_a) * 0.5, 1e-6)
        semi_minor = max(float(axis_b) * 0.5, 1e-6)

        points = contour.reshape(-1, 2).astype(np.float32)
        translated_x = points[:, 0] - float(center_x)
        translated_y = points[:, 1] - float(center_y)

        angle_rad = np.deg2rad(float(angle))
        cos_theta = np.cos(angle_rad)
        sin_theta = np.sin(angle_rad)
        rotated_x = translated_x * cos_theta + translated_y * sin_theta
        rotated_y = -translated_x * sin_theta + translated_y * cos_theta

        normalized_radius = np.sqrt((rotated_x / semi_major) ** 2 + (rotated_y / semi_minor) ** 2)
        return float(np.mean(np.abs(normalized_radius - 1.0)))

    def get_ring_candidate_silver_metrics(self, silver_mask, ellipse):
        mask_shape = silver_mask.shape
        (center_x, center_y), (axis_a, axis_b), angle = ellipse
        center = (int(round(center_x)), int(round(center_y)))
        outer_size = (
            max(1, int(round(axis_a))),
            max(1, int(round(axis_b))),
        )
        band_inner_size = (
            max(1, int(round(outer_size[0] * self.camera_ring_band_inner_scale))),
            max(1, int(round(outer_size[1] * self.camera_ring_band_inner_scale))),
        )
        hole_size = (
            max(1, int(round(outer_size[0] * self.camera_ring_hole_probe_scale))),
            max(1, int(round(outer_size[1] * self.camera_ring_hole_probe_scale))),
        )

        outer_mask = np.zeros(mask_shape, dtype=np.uint8)
        band_inner_mask = np.zeros(mask_shape, dtype=np.uint8)
        hole_mask = np.zeros(mask_shape, dtype=np.uint8)

        cv2.ellipse(outer_mask, (center, outer_size, float(angle)), 255, -1)
        cv2.ellipse(band_inner_mask, (center, band_inner_size, float(angle)), 255, -1)
        cv2.ellipse(hole_mask, (center, hole_size, float(angle)), 255, -1)

        band_mask = cv2.subtract(outer_mask, band_inner_mask)
        band_pixels = cv2.countNonZero(band_mask)
        band_silver_ratio = 0.0
        if band_pixels > 0:
            band_silver_pixels = cv2.countNonZero(cv2.bitwise_and(silver_mask, band_mask))
            band_silver_ratio = band_silver_pixels / band_pixels

        hole_pixels = cv2.countNonZero(hole_mask)
        hole_silver_ratio = 0.0
        if hole_pixels > 0:
            hole_silver_pixels = cv2.countNonZero(cv2.bitwise_and(silver_mask, hole_mask))
            hole_silver_ratio = hole_silver_pixels / hole_pixels

        return float(band_silver_ratio), float(hole_silver_ratio)

    def sample_ring_profile(self, component_mask, center):
        height, width = component_mask.shape
        center_x, center_y = center
        max_radius = float(np.hypot(max(center_x, width - center_x), max(center_y, height - center_y)))
        angles = np.linspace(0.0, 2.0 * np.pi, self.camera_profile_sample_count, endpoint=False)

        outer_radii = []
        inner_radii = []
        outer_points = []
        inner_points = []
        valid_mask = []

        radii = np.arange(0.0, max_radius + self.camera_profile_sampling_step_px, self.camera_profile_sampling_step_px)
        for angle in angles:
            cos_theta = np.cos(angle)
            sin_theta = np.sin(angle)
            sample_x = np.clip(np.rint(center_x + cos_theta * radii).astype(np.int32), 0, width - 1)
            sample_y = np.clip(np.rint(center_y + sin_theta * radii).astype(np.int32), 0, height - 1)
            mask_hits = component_mask[sample_y, sample_x] > 0
            hit_indices = np.flatnonzero(mask_hits)
            if hit_indices.size == 0:
                outer_radii.append(None)
                inner_radii.append(None)
                outer_points.append(None)
                inner_points.append(None)
                valid_mask.append(False)
                continue

            first_idx = int(hit_indices[0])
            last_idx = int(hit_indices[-1])
            inner_radius = float(radii[first_idx])
            outer_radius = float(radii[last_idx])
            inner_point = (
                float(center_x + cos_theta * inner_radius),
                float(center_y + sin_theta * inner_radius),
            )
            outer_point = (
                float(center_x + cos_theta * outer_radius),
                float(center_y + sin_theta * outer_radius),
            )
            inner_radii.append(inner_radius)
            outer_radii.append(outer_radius)
            inner_points.append(inner_point)
            outer_points.append(outer_point)
            valid_mask.append(True)

        valid_outer_radii = np.array([radius for radius in outer_radii if radius is not None], dtype=np.float32)
        valid_inner_radii = np.array([radius for radius in inner_radii if radius is not None], dtype=np.float32)
        valid_count = int(sum(valid_mask))
        coverage = valid_count / float(self.camera_profile_sample_count)

        inner_ratio = 0.0
        inner_presence_ratio = 0.0
        thickness_mean_px = None
        thickness_range_px = None
        radius_spread_px = None
        if valid_count > 0:
            median_outer_radius = float(np.median(valid_outer_radii))
            inner_threshold = max(3.0, median_outer_radius * self.camera_profile_min_inner_ratio)
            inner_present = valid_inner_radii >= inner_threshold
            inner_presence_ratio = float(np.mean(inner_present))
            inner_ratio = float(np.median(valid_inner_radii / np.maximum(valid_outer_radii, 1e-6)))
            thickness_values = valid_outer_radii - valid_inner_radii
            thickness_mean_px = float(np.mean(thickness_values))
            thickness_range_px = float(np.max(thickness_values) - np.min(thickness_values))
            radius_spread_px = float(np.max(valid_outer_radii) - np.min(valid_outer_radii))

        return {
            "angles_rad": angles.tolist(),
            "outer_radii_px": outer_radii,
            "inner_radii_px": inner_radii,
            "outer_points": outer_points,
            "inner_points": inner_points,
            "valid_mask": valid_mask,
            "valid_count": valid_count,
            "coverage": float(coverage),
            "inner_ratio": float(inner_ratio),
            "inner_presence_ratio": float(inner_presence_ratio),
            "thickness_mean_px": thickness_mean_px,
            "thickness_range_px": thickness_range_px,
            "radius_spread_px": radius_spread_px,
        }

    @staticmethod
    def map_camera_points_to_frame(points, offset_x, offset_y, scale_factor):
        mapped_points = []
        for point in points:
            if point is None:
                mapped_points.append(None)
                continue
            point_x, point_y = point
            mapped_points.append(
                (
                    float(point_x * scale_factor + offset_x),
                    float(point_y * scale_factor + offset_y),
                )
            )
        return mapped_points

    def measure_ring_from_frame(self, frame, use_tracking_roi=True):
        analysis_frame, offset_x, offset_y, scale, roi_used = self.get_camera_analysis_view(
            frame,
            use_tracking_roi=use_tracking_roi,
        )
        color_mask = self.build_ring_color_mask(analysis_frame)
        label_count, labels, stats, _ = cv2.connectedComponentsWithStats(color_mask, connectivity=8)
        if label_count <= 1:
            if roi_used:
                self.camera_tracking_roi = None
                return self.measure_ring_from_frame(frame, use_tracking_roi=False)
            self.camera_tracking_roi = None
            return None

        frame_height, frame_width = frame.shape[:2]
        frame_area = frame_height * frame_width
        frame_center = np.array([frame_width / 2.0, frame_height / 2.0], dtype=np.float32)
        scale_factor = 1.0 / scale

        best_candidate = None
        best_score = None
        for label_index in range(1, label_count):
            component_area_px = int(stats[label_index, cv2.CC_STAT_AREA])
            area = component_area_px * (scale_factor ** 2)
            if area < frame_area * 0.003:
                continue

            component_x = int(stats[label_index, cv2.CC_STAT_LEFT])
            component_y = int(stats[label_index, cv2.CC_STAT_TOP])
            component_w = int(stats[label_index, cv2.CC_STAT_WIDTH])
            component_h = int(stats[label_index, cv2.CC_STAT_HEIGHT])
            mapped_x = int(round(component_x * scale_factor + offset_x))
            mapped_y = int(round(component_y * scale_factor + offset_y))
            mapped_w = int(round(component_w * scale_factor))
            mapped_h = int(round(component_h * scale_factor))
            if (
                mapped_x <= 1
                or mapped_y <= 1
                or (mapped_x + mapped_w) >= (frame_width - 1)
                or (mapped_y + mapped_h) >= (frame_height - 1)
            ):
                continue

            component_mask = np.where(labels == label_index, 255, 0).astype(np.uint8)
            contours_info = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            contours = contours_info[0] if len(contours_info) == 2 else contours_info[1]
            if not contours:
                continue
            contour = max(contours, key=cv2.contourArea)
            if len(contour) < 10:
                continue

            perimeter = cv2.arcLength(contour, True) * scale_factor
            if perimeter <= 0:
                continue
            circularity = float((4.0 * np.pi * area) / max(perimeter * perimeter, 1e-6))
            if circularity < 0.08:
                continue

            (sample_center_x, sample_center_y), enclosing_radius = cv2.minEnclosingCircle(contour)
            if enclosing_radius <= 10:
                continue

            profile = self.sample_ring_profile(component_mask, (sample_center_x, sample_center_y))
            if profile["valid_count"] < self.camera_profile_min_valid_points:
                continue
            if profile["inner_presence_ratio"] < self.camera_profile_min_inner_presence_ratio:
                continue
            if profile["inner_ratio"] < self.camera_profile_min_inner_ratio:
                continue

            fitted_ellipse = None
            ellipse_fit_error = 0.0
            try:
                fitted_ellipse = cv2.fitEllipse(contour)
                ellipse_fit_error = self.calculate_ellipse_fit_error(contour, fitted_ellipse)
            except cv2.error:
                fitted_ellipse = None

            if fitted_ellipse is not None and ellipse_fit_error > 0.35:
                continue

            outer_radii = np.array(
                [radius for radius in profile["outer_radii_px"] if radius is not None],
                dtype=np.float32,
            )
            major_px = float(np.max(outer_radii) * 2.0)
            minor_px = float(np.min(outer_radii) * 2.0)
            mean_px = float(np.mean(outer_radii) * 2.0)
            ovality_px = float(major_px - minor_px)
            axis_ratio = minor_px / max(major_px, 1e-6)
            if axis_ratio < 0.35:
                continue

            if fitted_ellipse is not None:
                ellipse = self.map_camera_ellipse_to_frame(fitted_ellipse, offset_x, offset_y, scale_factor)
                fill_ratio = area / max(np.pi * (ellipse[1][0] * 0.5) * (ellipse[1][1] * 0.5), 1.0)
            else:
                mapped_radius = float(enclosing_radius * scale_factor)
                ellipse = (
                    (
                        float(sample_center_x * scale_factor + offset_x),
                        float(sample_center_y * scale_factor + offset_y),
                    ),
                    (mapped_radius * 2.0, mapped_radius * 2.0),
                    0.0,
                )
                fill_ratio = 1.0

            center_x = float(sample_center_x * scale_factor + offset_x)
            center_y = float(sample_center_y * scale_factor + offset_y)
            center_distance = float(np.linalg.norm(np.array([center_x, center_y]) - frame_center))
            score = (
                area * (1.0 + profile["coverage"] + profile["inner_presence_ratio"])
                - (center_distance * 2.0)
                - (ellipse_fit_error * area * 0.3)
            )
            if best_score is None or score > best_score:
                best_score = score
                best_candidate = {
                    "ellipse": ellipse,
                    "center": (float(center_x), float(center_y)),
                    "angle": float(ellipse[2]),
                    "major_px": major_px,
                    "minor_px": minor_px,
                    "mean_px": mean_px,
                    "ovality_px": ovality_px,
                    "area": float(area),
                    "fill_ratio": float(fill_ratio),
                    "axis_ratio": float(axis_ratio),
                    "circularity": float(circularity),
                    "ellipse_fit_error": float(ellipse_fit_error),
                    "profile_point_count": int(self.camera_profile_sample_count),
                    "profile_valid_count": int(profile["valid_count"]),
                    "profile_coverage": float(profile["coverage"]),
                    "profile_inner_ratio": float(profile["inner_ratio"]),
                    "profile_inner_presence_ratio": float(profile["inner_presence_ratio"]),
                    "profile_outer_radii_px": list(profile["outer_radii_px"]),
                    "profile_inner_radii_px": list(profile["inner_radii_px"]),
                    "profile_valid_mask": list(profile["valid_mask"]),
                    "profile_angles_deg": [
                        float(np.degrees(angle_value)) for angle_value in profile["angles_rad"]
                    ],
                    "profile_outer_points": self.map_camera_points_to_frame(
                        profile["outer_points"], offset_x, offset_y, scale_factor
                    ),
                    "profile_inner_points": self.map_camera_points_to_frame(
                        profile["inner_points"], offset_x, offset_y, scale_factor
                    ),
                    "radius_spread_px": profile["radius_spread_px"],
                    "thickness_mean_px": profile["thickness_mean_px"],
                    "thickness_range_px": profile["thickness_range_px"],
                }

        if best_candidate is None:
            if roi_used:
                self.camera_tracking_roi = None
                return self.measure_ring_from_frame(frame, use_tracking_roi=False)
            self.camera_tracking_roi = None
            return None

        measurement = {
            **best_candidate,
            "reference_diameter_mm": self.camera_reference_diameter_mm,
        }

        mm_per_px = None
        if self.camera_baseline is not None and self.camera_baseline.get("mm_per_px"):
            mm_per_px = self.camera_baseline["mm_per_px"]
        elif measurement["reference_diameter_mm"] is not None and measurement["mean_px"] > 0:
            mm_per_px = measurement["reference_diameter_mm"] / measurement["mean_px"]
        measurement["mm_per_px"] = mm_per_px

        if mm_per_px is not None:
            measurement["major_mm"] = measurement["major_px"] * mm_per_px
            measurement["minor_mm"] = measurement["minor_px"] * mm_per_px
            measurement["mean_mm"] = measurement["mean_px"] * mm_per_px
            measurement["ovality_mm"] = measurement["ovality_px"] * mm_per_px
            if measurement.get("radius_spread_px") is not None:
                measurement["radius_spread_mm"] = measurement["radius_spread_px"] * mm_per_px
            if measurement.get("thickness_mean_px") is not None:
                measurement["thickness_mean_mm"] = measurement["thickness_mean_px"] * mm_per_px
            if measurement.get("thickness_range_px") is not None:
                measurement["thickness_range_mm"] = measurement["thickness_range_px"] * mm_per_px

        if self.camera_baseline is not None:
            measurement["delta_major_px"] = measurement["major_px"] - self.camera_baseline["major_px"]
            measurement["delta_minor_px"] = measurement["minor_px"] - self.camera_baseline["minor_px"]
            measurement["delta_mean_px"] = measurement["mean_px"] - self.camera_baseline["mean_px"]
            measurement["delta_ovality_px"] = measurement["ovality_px"] - self.camera_baseline["ovality_px"]

            current_outer = np.array(
                [np.nan if radius is None else float(radius) for radius in measurement["profile_outer_radii_px"]],
                dtype=np.float32,
            )
            baseline_outer = np.array(
                [
                    np.nan if radius is None else float(radius)
                    for radius in self.camera_baseline["profile_outer_radii_px"]
                ],
                dtype=np.float32,
            )
            current_valid = np.array(measurement["profile_valid_mask"], dtype=bool)
            baseline_valid = np.array(self.camera_baseline["profile_valid_mask"], dtype=bool)
            common_valid = current_valid & baseline_valid
            measurement["profile_common_valid_count"] = int(np.count_nonzero(common_valid))
            if measurement["profile_common_valid_count"] > 0:
                delta_profile_px = current_outer[common_valid] - baseline_outer[common_valid]
                measurement["profile_delta_mean_abs_px"] = float(np.mean(np.abs(delta_profile_px)))
                measurement["profile_delta_max_abs_px"] = float(np.max(np.abs(delta_profile_px)))
                measurement["profile_delta_mean_signed_px"] = float(np.mean(delta_profile_px))

            if mm_per_px is not None:
                measurement["delta_major_mm"] = measurement["delta_major_px"] * mm_per_px
                measurement["delta_minor_mm"] = measurement["delta_minor_px"] * mm_per_px
                measurement["delta_mean_mm"] = measurement["delta_mean_px"] * mm_per_px
                measurement["delta_ovality_mm"] = measurement["delta_ovality_px"] * mm_per_px
                if measurement.get("profile_delta_mean_abs_px") is not None:
                    measurement["profile_delta_mean_abs_mm"] = measurement["profile_delta_mean_abs_px"] * mm_per_px
                    measurement["profile_delta_max_abs_mm"] = measurement["profile_delta_max_abs_px"] * mm_per_px
                    measurement["profile_delta_mean_signed_mm"] = (
                        measurement["profile_delta_mean_signed_px"] * mm_per_px
                    )
                    measurement["deformation_mm"] = measurement["profile_delta_max_abs_mm"]

        self.camera_tracking_roi = self.build_camera_tracking_roi(measurement, frame.shape)
        return measurement

    def draw_ring_measurement_overlay(self, frame, measurement):
        if measurement is None:
            target_description = self.get_camera_ring_target_description().capitalize()
            cv2.putText(
                frame,
                f"{target_description} not detected",
                (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 0, 255),
                2,
                cv2.LINE_AA,
            )
            return frame

        center_x, center_y = measurement["center"]
        cv2.circle(frame, (int(center_x), int(center_y)), 4, (255, 0, 0), -1)

        previous_point = None
        for point in measurement.get("profile_outer_points", []):
            if point is None:
                previous_point = None
                continue
            point_xy = (int(round(point[0])), int(round(point[1])))
            cv2.circle(frame, point_xy, 3, (0, 140, 255), -1)
            if previous_point is not None:
                cv2.line(frame, previous_point, point_xy, (0, 220, 255), 1, cv2.LINE_AA)
            previous_point = point_xy

        for point in measurement.get("profile_inner_points", []):
            if point is None:
                continue
            point_xy = (int(round(point[0])), int(round(point[1])))
            cv2.circle(frame, point_xy, 2, (120, 255, 120), -1)

        overlay_lines = [
            f"Samples: {measurement['profile_valid_count']}/{measurement['profile_point_count']}",
            f"Mean Dia: {measurement['mean_px']:.1f}px",
            f"Spread: {measurement['radius_spread_px']:.2f}px" if measurement.get("radius_spread_px") is not None else "Spread: n/a",
        ]
        if measurement.get("major_mm") is not None:
            overlay_lines = [
                f"Samples: {measurement['profile_valid_count']}/{measurement['profile_point_count']}",
                f"Mean Dia: {measurement['mean_mm']:.3f}mm",
                (
                    f"Spread: {measurement['radius_spread_mm']:.3f}mm"
                    if measurement.get("radius_spread_mm") is not None
                    else "Spread: n/a"
                ),
            ]
        if measurement.get("profile_delta_max_abs_mm") is not None:
            overlay_lines.append(f"Max Def.: {measurement['profile_delta_max_abs_mm']:.3f}mm")
        elif measurement.get("profile_delta_max_abs_px") is not None:
            overlay_lines.append(f"Max Def.: {measurement['profile_delta_max_abs_px']:.2f}px")

        origin_y = 30
        for line in overlay_lines:
            cv2.putText(
                frame,
                line,
                (20, origin_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (36, 255, 12),
                2,
                cv2.LINE_AA,
            )
            origin_y += 26

        return frame

    def render_camera_frame(self, frame):
        preview_width = max(1, self.lbl_camera_preview.width())
        preview_height = max(1, self.lbl_camera_preview.height())
        height, width = frame.shape[:2]
        preview_scale = min(preview_width / max(width, 1), preview_height / max(height, 1), 1.0)
        if preview_scale < 0.995:
            resized_width = max(1, int(round(width * preview_scale)))
            resized_height = max(1, int(round(height * preview_scale)))
            frame = cv2.resize(
                frame,
                (resized_width, resized_height),
                interpolation=cv2.INTER_AREA,
            )
            height, width = frame.shape[:2]

        bytes_per_line = frame.shape[2] * width
        bgr_format = getattr(QImage, "Format_BGR888", None)
        if bgr_format is not None:
            image = QImage(frame.data, width, height, bytes_per_line, bgr_format).copy()
        else:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = QImage(
                rgb_frame.data,
                width,
                height,
                rgb_frame.shape[2] * width,
                QImage.Format_RGB888,
            ).copy()
        pixmap = QPixmap.fromImage(image)
        self.lbl_camera_preview.setPixmap(pixmap)

    def update_ring_metrics_label(self):
        if self.latest_ring_measurement is None:
            baseline_text = "captured" if self.camera_baseline is not None else "not captured"
            target_description = self.get_camera_ring_target_description()
            metrics_text = (
                "Ring measurement:\n"
                f"- Detection: waiting for a centered {target_description}\n"
                f"- Profile Points: up to {self.camera_profile_sample_count}\n"
                f"- Baseline: {baseline_text}\n"
                f"- Tip: {self.get_camera_ring_color_tip()}"
            )
            if self.lbl_ring_metrics.text() != metrics_text:
                self.lbl_ring_metrics.setText(metrics_text)
            return

        measurement = self.latest_ring_measurement
        lines = [
            "Ring measurement:",
            f"- Profile Points: {measurement['profile_valid_count']} / {measurement['profile_point_count']}",
            f"- Profile Coverage: {measurement['profile_coverage'] * 100.0:.1f} %",
            f"- Mean Diameter: {measurement['mean_px']:.2f} px",
            f"- Diameter Spread: {measurement['ovality_px']:.2f} px",
        ]
        if measurement.get("major_mm") is not None:
            lines.extend(
                [
                    f"- Mean Diameter: {measurement['mean_mm']:.3f} mm",
                    f"- Diameter Spread: {measurement['ovality_mm']:.3f} mm",
                ]
            )
        if measurement.get("thickness_mean_px") is not None:
            lines.append(f"- Band Thickness Mean: {measurement['thickness_mean_px']:.2f} px")
        if measurement.get("thickness_mean_mm") is not None:
            lines.append(f"- Band Thickness Mean: {measurement['thickness_mean_mm']:.3f} mm")

        if self.camera_baseline is None:
            lines.append("- Baseline: not captured")
        else:
            lines.append("- Baseline: captured")
            if measurement.get("profile_common_valid_count"):
                lines.append(f"- Common Sample Points: {measurement['profile_common_valid_count']}")
                lines.append(f"- Mean Radial Delta: {measurement['profile_delta_mean_abs_px']:.2f} px")
                lines.append(f"- Max Radial Delta: {measurement['profile_delta_max_abs_px']:.2f} px")
                if measurement.get("profile_delta_mean_abs_mm") is not None:
                    lines.append(f"- Mean Radial Delta: {measurement['profile_delta_mean_abs_mm']:.3f} mm")
                    lines.append(f"- Max Radial Delta: {measurement['profile_delta_max_abs_mm']:.3f} mm")
                    lines.append(f"- Deformation Amount: {measurement['deformation_mm']:.3f} mm")
            else:
                lines.append("- Delta: waiting for enough common sample points after baseline capture")

        metrics_text = "\n".join(lines)
        if self.lbl_ring_metrics.text() != metrics_text:
            self.lbl_ring_metrics.setText(metrics_text)

    def append_camera_metrics_to_log_row(self, log_row):
        measurement = self.latest_ring_measurement
        if measurement is None:
            return

        log_row["Ring Major [px]"] = round(measurement["major_px"], 2)
        log_row["Ring Minor [px]"] = round(measurement["minor_px"], 2)
        log_row["Ring Mean Diameter [px]"] = round(measurement["mean_px"], 2)
        log_row["Ring Ovality [px]"] = round(measurement["ovality_px"], 2)
        log_row["Ring Profile Points"] = int(measurement["profile_point_count"])
        log_row["Ring Valid Sample Points"] = int(measurement["profile_valid_count"])
        log_row["Ring Profile Coverage [%]"] = round(measurement["profile_coverage"] * 100.0, 1)
        if measurement.get("radius_spread_px") is not None:
            log_row["Ring Radius Spread [px]"] = round(measurement["radius_spread_px"], 2)

        if measurement.get("major_mm") is not None:
            log_row["Ring Major [mm]"] = round(measurement["major_mm"], 3)
            log_row["Ring Minor [mm]"] = round(measurement["minor_mm"], 3)
            log_row["Ring Mean Diameter [mm]"] = round(measurement["mean_mm"], 3)
            log_row["Ring Ovality [mm]"] = round(measurement["ovality_mm"], 3)
            if measurement.get("radius_spread_mm") is not None:
                log_row["Ring Radius Spread [mm]"] = round(measurement["radius_spread_mm"], 3)

        if self.camera_baseline is not None:
            if measurement.get("profile_common_valid_count"):
                log_row["Ring Common Sample Points"] = int(measurement["profile_common_valid_count"])
                log_row["Ring Mean Radial Delta [px]"] = round(measurement["profile_delta_mean_abs_px"], 2)
                log_row["Ring Max Radial Delta [px]"] = round(measurement["profile_delta_max_abs_px"], 2)
            if measurement.get("profile_delta_mean_abs_mm") is not None:
                log_row["Ring Mean Radial Delta [mm]"] = round(measurement["profile_delta_mean_abs_mm"], 3)
                log_row["Ring Max Radial Delta [mm]"] = round(measurement["profile_delta_max_abs_mm"], 3)
                log_row["Ring Deformation [mm]"] = round(measurement["deformation_mm"], 3)

    def is_fc400_mode(self):
        return self.input_source == FC400_SOURCE

    def is_live_monitor_mode(self):
        return self.is_fc400_mode()

    def is_position_monitor_enabled(self):
        return self.chk_position_monitor.isChecked()

    def is_position_pcie_enabled(self):
        # Historical name kept for callers; both PCIe and direct USB now
        # support position feedback and motion commands.
        return self.is_position_monitor_enabled()

    def is_position_control_armed(self):
        return self.is_position_pcie_enabled() and self.chk_mr_motion_arm.isChecked()

    def get_source_data_unit(self):
        if self.is_fc400_mode():
            return self.fc400_device_unit_combo.currentText()
        return "kgf"

    def convert_value_units(self, value, from_unit, to_unit):
        if from_unit == to_unit:
            return value
        if from_unit == "kgf" and to_unit == "N":
            return value * 9.80665
        if from_unit == "N" and to_unit == "kgf":
            return value / 9.80665
        raise ValueError(f"지원하지 않는 단위 변환입니다: {from_unit} -> {to_unit}")

    def convert_array_units(self, values, from_unit, to_unit):
        if from_unit == to_unit:
            return values
        factor = self.convert_value_units(1.0, from_unit, to_unit)
        return values * factor

    def update_table_headers(self):
        self.table.setHorizontalHeaderLabels(
            [
                "Axis",
                f"Raw Data [{self.data_unit}]",
                f"Zero Offset [{self.data_unit}]",
                f"Calibrated Value [{self.unit}]",
            ]
        )

    def update_input_source_buttons(self):
        for source_name, button in self.source_buttons.items():
            previous_state = button.blockSignals(True)
            button.setChecked(source_name == self.input_source)
            button.blockSignals(previous_state)

    def on_input_source_button_clicked(self, source):
        if source == self.input_source:
            self.update_input_source_buttons()
            return
        self.on_input_source_changed(source)

    def on_position_monitor_toggled(self, enabled):
        configuration_widgets = [
            self.mr_connection_combo,
            self.in_mr_dll_path,
            self.in_mr_board_id,
            self.in_mr_axis_no,
            self.in_mr_counts_per_mm,
            self.chk_mr_auto_start,
            self.chk_mr_motion_arm,
            self.in_mr_motion_speed,
            self.in_mr_acceleration_ms,
            self.in_mr_deceleration_ms,
            self.in_mr_relative_move_mm,
        ]
        for widget in configuration_widgets:
            widget.setEnabled(enabled)

        if enabled:
            if os.name != "nt":
                self.lbl_mr_status.setText(f"MR-MC240N: {MR_MC240N_WINDOWS_ONLY_MESSAGE}")
            elif self.is_position_usb_mode():
                usb_info = detect_mr_mc240n_usb_controller()
                if usb_info["connected"]:
                    driver_text = (
                        f" ({usb_info['driver']})" if usb_info["driver"] else ""
                    )
                    self.lbl_mr_status.setText(
                        "MR-MC240N: direct USB controller detected"
                        f"{driver_text}; press Connect USB Controller"
                    )
                else:
                    self.lbl_mr_status.setText(
                        "MR-MC240N: USB controller not detected"
                    )
            else:
                self.lbl_mr_status.setText(
                    "MR-MC240N: PCIe API enabled; connect the installed board"
                )
        else:
            if self.chk_mr_motion_arm.isChecked():
                self.chk_mr_motion_arm.setChecked(False)
            self.close_position_monitor()
            if os.name != "nt":
                self.lbl_mr_status.setText(f"MR-MC240N: {MR_MC240N_WINDOWS_ONLY_MESSAGE}")
            else:
                self.lbl_mr_status.setText("MR-MC240N: disabled")
        self.update_position_control_state()

    def is_position_usb_mode(self):
        return self.mr_connection_combo.currentText() == MR_CONNECTION_USB_MAINTENANCE

    def on_position_connection_changed(self, connection):
        self.close_position_monitor()
        if connection == MR_CONNECTION_USB_MAINTENANCE:
            if self.chk_mr_motion_arm.isChecked():
                self.chk_mr_motion_arm.setChecked(False)
            self.btn_mr_connect.setText("Connect USB Controller")
            self.chk_mr_auto_start.setEnabled(False)
        else:
            self.btn_mr_connect.setText("Connect PCIe Board")
            self.chk_mr_auto_start.setEnabled(True)
        if self.is_position_monitor_enabled():
            self.on_position_monitor_toggled(True)
        else:
            self.update_position_control_state()

    def on_position_axis_changed(self, _axis_text):
        if not hasattr(self, "chk_mr_motion_arm"):
            return
        if self.chk_mr_motion_arm.isChecked():
            self.chk_mr_motion_arm.setChecked(False)
        self.close_position_monitor()
        if self.is_position_monitor_enabled():
            self.lbl_mr_status.setText(
                f"MR-MC240N: axis {self.in_mr_axis_no.currentText()} selected; reconnect"
            )
        self.update_position_control_state()

    def on_position_motion_arm_toggled(self, armed):
        if armed and not self.is_position_monitor_enabled():
            self.chk_mr_motion_arm.setChecked(False)
            return
        if (
            armed
            and self.is_position_usb_mode()
            and not MrMc240nUsbController.is_supported_platform()
        ):
            self.chk_mr_motion_arm.setChecked(False)
            QMessageBox.information(
                self,
                "USB Direct Control Unavailable",
                "USB 직접 제어 브리지는 Windows에서만 사용할 수 있습니다.",
            )
            return

        if armed:
            answer = QMessageBox.question(
                self,
                "Arm MR-J4 Motion",
                "MR-MC240N을 통해 MR-J4-10B-RJ 실제 축 명령을 활성화합니다.\n"
                "외부 비상정지, 리미트 스위치, 작업 영역 안전을 확인했습니까?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.chk_mr_motion_arm.setChecked(False)
                return
            self.lbl_mr_status.setText("MR-MC240N: motion commands armed")
        else:
            if self.position_jog_command_active:
                self.stop_position_motion(True)
            self.position_jog_command_active = False
            if self.is_position_monitor_enabled():
                self.lbl_mr_status.setText("MR-MC240N: motion commands disarmed")
        self.update_position_control_state()

    def update_position_control_state(self):
        enabled = self.is_position_monitor_enabled() and os.name == "nt"
        control_enabled = enabled
        armed = control_enabled and self.chk_mr_motion_arm.isChecked()
        self.btn_mr_connect.setEnabled(enabled)
        self.btn_mr_system_start.setEnabled(enabled and self.is_position_usb_mode())
        self.btn_mr_apply_six_axis.setEnabled(
            enabled and self.is_position_usb_mode() and not armed
        )
        self.chk_mr_motion_arm.setEnabled(control_enabled)
        motion_buttons = [
            self.btn_mr_servo_on,
            self.btn_mr_servo_off,
            self.btn_mr_home,
            self.btn_mr_move_relative,
            self.btn_mr_jog_minus,
            self.btn_mr_jog_plus,
        ]
        for button in motion_buttons:
            button.setEnabled(armed)
        self.btn_mr_stop.setEnabled(control_enabled)
        self.btn_mr_rapid_stop.setEnabled(control_enabled)
        self.btn_mr_refresh_status.setEnabled(control_enabled)

    def test_position_board_connection(self):
        """Open the configured board now so detection errors are immediately visible."""
        self.close_position_monitor()
        try:
            self.open_position_monitor()
            controller = self.position_monitor
            if isinstance(controller, MrMc240nUsbController):
                axis_status = controller.read_axis_status()
                if controller.system_status_code == 0x0009:
                    axis_text = (
                        "WAITING SSCNET - no amplifier response; check first CN1A"
                    )
                elif axis_status["status0"] == 0 and axis_status["status1"] == 0:
                    axis_text = (
                        f"axis {controller.axis_number} not mounted/configured"
                    )
                else:
                    axis_text = (
                        f"axis {controller.axis_number} "
                        f"{'READY' if axis_status['servo_ready'] else 'NOT READY'}"
                    )
                self.lbl_mr_status.setText(
                    f"MR-MC240N: USB connected, identity {controller.identity}, "
                    f"system 0x{controller.system_status_code:04X} "
                    f"({controller.system_status_text(controller.system_status_code)}), "
                    f"{axis_text}"
                )
            else:
                self.lbl_mr_status.setText(
                    f"MR-MC240N: PCIe connected to board {controller.board_id}; "
                    f"axis {controller.axis_number}"
                )
            self.refresh_position_axis_status()
        except Exception as exc:
            message = str(exc)
            self.lbl_mr_status.setText(f"MR-MC240N: connection failed - {message}")
            QMessageBox.critical(
                self,
                "MR-MC240N Connection Error",
                "보드를 열지 못했습니다.\n\n"
                f"{message}\n\n"
                "PB Test나 MR Configurator2가 USB를 사용 중이면 종료한 뒤 다시 시도하고, "
                "보드 전원·USB 드라이버를 확인해주세요.",
            )

    def start_position_usb_system(self):
        if not self.is_position_usb_mode():
            return
        answer = QMessageBox.question(
            self,
            "USB System Start",
            "MR-MC240N의 현재 보드 파라미터로 System Start 명령을 보냅니다.\n"
            "Servo/JOG 명령은 보내지 않습니다. 축 구성과 비상정지를 확인했습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            controller = self.get_position_controller(require_armed=False)
            if not isinstance(controller, MrMc240nUsbController):
                raise RuntimeError("USB direct controller가 선택되지 않았습니다.")
            status_code = controller.start_system()
            self.lbl_mr_status.setText(
                f"MR-MC240N: USB System Start sent, status 0x{status_code:04X}"
            )
        except Exception as exc:
            self.handle_position_command_error("USB System Start", exc)

    def apply_position_six_axis_preset(self):
        if not self.is_position_usb_mode():
            return
        if self.chk_mr_motion_arm.isChecked():
            QMessageBox.warning(
                self,
                "Disarm Motion First",
                "6축 파라미터 적용 전에 Arm motion commands를 해제해주세요.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Apply Six-Axis Hardware Preset",
            "실제 MR-MC240N을 소프트웨어 재기동하고 다음 구성을 적용합니다.\n\n"
            "• Axis 1~6: HG-KR13 / MR-J4-10B-RJ\n"
            "• Amplifier rotary switches: 0~5\n"
            "• Ball screw: BTK1404, lead 4 mm, direct 1:1\n"
            "• Position unit: 1 µm (1000 command units/mm)\n\n"
            "Servo/JOG 명령은 보내지 않습니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            controller = self.get_position_controller(require_armed=False)
            if not isinstance(controller, MrMc240nUsbController):
                raise RuntimeError("USB direct controller가 선택되지 않았습니다.")
            response = controller.configure_six_axis_btk1404()
            self.in_mr_counts_per_mm.setText("1000.0")
            self.lbl_mr_status.setText(
                "MR-MC240N: HG-KR13 ×6 / BTK1404 preset applied, "
                f"system 0x{int(response['system_status']):04X}"
            )
            self.refresh_position_axis_status()
        except Exception as exc:
            self.handle_position_command_error("Six-axis preset", exc)

    def on_source_configuration_changed(self, *_args):
        previous_source = getattr(self, "input_source", SIMULATION_SOURCE)
        if self.is_simulating and self.is_live_monitor_mode():
            self.stop_simulation(completed=False, source_override=previous_source)

        self.data_unit = self.get_source_data_unit()
        self.raw_data = [0.0] * 6
        self.sensor_zeros = [0.0] * 6
        self.update_table_headers()
        self.update_table()
        self.update_chart(reset_scale=True)

    def update_start_button_idle_state(self):
        if self.is_fc400_mode():
            self.btn_start.setText("Start FC400 / USB-6002 Monitoring")
        else:
            self.btn_start.setText("Start Test Simulation")
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")

    def on_input_source_changed(self, source):
        previous_source = getattr(self, "input_source", SIMULATION_SOURCE)
        if self.is_simulating:
            self.stop_simulation(completed=False, source_override=previous_source)

        self.input_source = source
        self.update_input_source_buttons()
        use_fc400 = self.is_fc400_mode()
        if (
            use_fc400
            and previous_source != FC400_SOURCE
            and self.unit_combo.currentText() == "kgf"
            and self.fc400_device_unit_combo.currentText() == "N"
        ):
            self.unit_combo.setCurrentText("N")

        fc400_widgets = [
            self.in_fc400_daq_channel,
            self.fc400_terminal_combo,
            self.in_fc400_zero_voltage,
            self.in_fc400_full_scale_voltage,
            self.in_fc400_full_scale_load,
            self.fc400_device_unit_combo,
            self.in_fc400_sample_rate,
            self.btn_refresh_fc400_daq,
        ]
        for widget in fc400_widgets:
            widget.setEnabled(use_fc400)

        if use_fc400:
            self.close_ni_daq_task()
            self.lbl_status.setText("Status: Ready (FC400 via NI USB-6002)")
            if not NIDAQMX_AVAILABLE:
                self.lbl_fc400_status.setText(
                    f"USB-6002: nidaqmx import failed - {NIDAQMX_IMPORT_ERROR}"
                )
            else:
                self.refresh_ni_devices()
        else:
            self.close_ni_daq_task()
            self.lbl_status.setText("Status: Ready")
            self.lbl_fc400_status.setText("FC400 / USB-6002 mode disabled")

        self.data_unit = self.get_source_data_unit()
        self.raw_data = [0.0] * 6
        self.sensor_zeros = [0.0] * 6
        self.latest_live_snapshot = [0.0] * 6
        self.latest_live_position_mm = None
        self.latest_live_position_counts = None
        self.position_zero_offset_mm = 0.0
        self.update_table_headers()
        self.update_table()
        self.update_chart(reset_scale=True)

        self.update_start_button_idle_state()

    def refresh_ni_devices(self):
        if not NIDAQMX_AVAILABLE:
            self.lbl_fc400_status.setText(
                f"USB-6002: nidaqmx import failed - {NIDAQMX_IMPORT_ERROR}"
            )
            return

        try:
            device_summaries = []
            first_ai_channel = None
            for device in System.local().devices:
                try:
                    ai_channels = device.ai_physical_chans.channel_names
                except Exception:
                    ai_channels = []

                if not ai_channels:
                    continue

                product_type = ""
                try:
                    product_type = device.product_type
                except Exception:
                    product_type = "NI Device"

                device_summaries.append(f"{device.name} ({product_type})")
                if first_ai_channel is None:
                    first_ai_channel = ai_channels[0]

            channel_widget = self.in_fc400_daq_channel
            current_channel = channel_widget.text().strip()
            default_channels = {"", "Dev1/ai0"}
            if first_ai_channel and current_channel in default_channels:
                channel_widget.setText(first_ai_channel)

            if device_summaries:
                status_text = ", ".join(device_summaries)
                self.lbl_fc400_status.setText("USB-6002: " + status_text)
            else:
                self.lbl_fc400_status.setText(
                    "USB-6002: no NI analog-input device found"
                )
        except Exception as exc:
            self.lbl_fc400_status.setText(
                f"USB-6002: device scan failed - {exc}"
            )

    def close_ni_daq_task(self):
        if self.ni_daq_task is None:
            return

        try:
            self.ni_daq_task.stop()
        except Exception:
            pass

        try:
            self.ni_daq_task.close()
        except Exception:
            pass

        self.ni_daq_task = None

    def read_ni_daq_value(self):
        opened_here = False
        if self.ni_daq_task is None:
            self.open_fc400_usb_task()
            opened_here = True

        try:
            available_samples = self.ni_daq_task.in_stream.avail_samp_per_chan
            if available_samples < 1:
                value = self.ni_daq_task.read(
                    number_of_samples_per_channel=1, timeout=1.0
                )
            else:
                value = self.ni_daq_task.read(
                    number_of_samples_per_channel=READ_ALL_AVAILABLE,
                    timeout=1.0,
                )
            if isinstance(value, np.ndarray):
                if value.size == 0:
                    raise RuntimeError("USB-6002 버퍼에 읽을 샘플이 없습니다.")
                return float(value[-1])
            if isinstance(value, (list, tuple)):
                if len(value) == 0:
                    raise RuntimeError("USB-6002 버퍼에 읽을 샘플이 없습니다.")
                last_value = value[-1]
                if isinstance(last_value, (list, tuple, np.ndarray)):
                    if len(last_value) == 0:
                        raise RuntimeError("USB-6002 버퍼에 읽을 샘플이 없습니다.")
                    last_value = last_value[-1]
                return float(last_value)
            return float(value)
        finally:
            if opened_here and not self.is_simulating:
                self.close_ni_daq_task()

    def get_fc400_config(self):
        physical_channel = self.in_fc400_daq_channel.text().strip()
        if not physical_channel:
            raise ValueError("USB-6002 Physical Channel을 입력해주세요. 예: Dev1/ai0")

        zero_voltage = float(self.in_fc400_zero_voltage.text())
        full_scale_voltage = float(self.in_fc400_full_scale_voltage.text())
        full_scale_load = float(self.in_fc400_full_scale_load.text())
        sample_rate_hz = float(self.in_fc400_sample_rate.text())

        if not -10.0 <= zero_voltage <= 10.0:
            raise ValueError("FC400 Zero Output은 USB-6002 범위인 -10~10 V 안이어야 합니다.")
        if not -10.0 <= full_scale_voltage <= 10.0:
            raise ValueError("FC400 Full-scale Output은 USB-6002 범위인 -10~10 V 안이어야 합니다.")
        if full_scale_voltage == zero_voltage:
            raise ValueError("FC400 Zero/Full-scale Output 전압은 서로 달라야 합니다.")
        if full_scale_load <= 0:
            raise ValueError("FC400 Full Scale은 0보다 커야 합니다.")
        if not 1.0 <= sample_rate_hz <= 50_000.0:
            raise ValueError("USB-6002 Sample Rate는 1~50000 S/s 범위로 입력해주세요.")

        return {
            "physical_channel": physical_channel,
            "terminal_mode": self.fc400_terminal_combo.currentText(),
            "zero_voltage": zero_voltage,
            "full_scale_voltage": full_scale_voltage,
            "full_scale_load": full_scale_load,
            "sample_rate_hz": sample_rate_hz,
            "device_unit": self.fc400_device_unit_combo.currentText(),
        }

    def open_fc400_usb_task(self):
        if self.ni_daq_task is not None:
            return

        if not NIDAQMX_AVAILABLE:
            raise RuntimeError(f"nidaqmx를 불러오지 못했습니다: {NIDAQMX_IMPORT_ERROR}")

        config = self.get_fc400_config()
        terminal_config = (
            TerminalConfiguration.DIFF
            if config["terminal_mode"] == "Differential"
            else TerminalConfiguration.RSE
        )
        task = nidaqmx.Task()
        try:
            task.ai_channels.add_ai_voltage_chan(
                config["physical_channel"],
                terminal_config=terminal_config,
                min_val=-10.0,
                max_val=10.0,
            )
            task.timing.cfg_samp_clk_timing(
                config["sample_rate_hz"],
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=max(int(config["sample_rate_hz"]), 100),
            )
            task.in_stream.read_all_avail_samp = True
            task.start()
        except Exception:
            task.close()
            raise

        self.ni_daq_task = task
        self.data_unit = config["device_unit"]
        self.update_table_headers()
        self.lbl_fc400_status.setText(
            f"FC400 / USB-6002: {config['physical_channel']} "
            f"({config['terminal_mode']}) @ {task.timing.samp_clk_rate:.0f} S/s"
        )

    def read_fc400_measurement(self):
        opened_here = False
        if self.ni_daq_task is None:
            self.open_fc400_usb_task()
            opened_here = True

        try:
            voltage = self.read_ni_daq_value()
            config = self.get_fc400_config()
            voltage_span = config["full_scale_voltage"] - config["zero_voltage"]
            load_value = (
                (voltage - config["zero_voltage"])
                / voltage_span
                * config["full_scale_load"]
            )
            return {
                "value": load_value,
                "stable": None,
                "voltage": voltage,
            }
        finally:
            if opened_here and not self.is_simulating:
                self.close_ni_daq_task()

    def get_position_monitor_config(self):
        board_id = int(self.in_mr_board_id.text())
        axis_number = int(self.in_mr_axis_no.currentText())
        counts_per_mm = float(self.in_mr_counts_per_mm.text())

        if not 0 <= board_id <= 3:
            raise ValueError("MR-MC240N Board ID는 0~3 범위로 입력해주세요.")
        if not 1 <= axis_number <= 20:
            raise ValueError("MR-MC240N + MR-J4 Axis No는 1~20 범위로 입력해주세요.")
        if counts_per_mm <= 0:
            raise ValueError("Command Units / mm는 0보다 커야 합니다.")

        return {
            "dll_path": self.in_mr_dll_path.text().strip(),
            "board_id": board_id,
            "axis_number": axis_number,
            "counts_per_mm": counts_per_mm,
            "auto_start_system": self.chk_mr_auto_start.isChecked(),
        }

    def get_position_motion_config(self):
        speed = int(self.in_mr_motion_speed.text())
        acceleration_ms = int(self.in_mr_acceleration_ms.text())
        deceleration_ms = int(self.in_mr_deceleration_ms.text())
        distance_mm = float(self.in_mr_relative_move_mm.text())

        max_speed = 12_000 if self.is_position_usb_mode() else 2_147_483_647
        if not 1 <= speed <= max_speed:
            raise ValueError(f"Speed는 1~{max_speed} 범위로 입력해주세요.")
        if not 0 <= acceleration_ms <= 20_000:
            raise ValueError("Acceleration은 0~20000 ms 범위로 입력해주세요.")
        if not 0 <= deceleration_ms <= 20_000:
            raise ValueError("Deceleration은 0~20000 ms 범위로 입력해주세요.")

        return {
            "speed": speed,
            "acceleration_ms": acceleration_ms,
            "deceleration_ms": deceleration_ms,
            "distance_mm": distance_mm,
        }

    def open_position_monitor(self):
        if not self.is_position_monitor_enabled():
            return
        if self.position_monitor is not None:
            return

        config = self.get_position_monitor_config()
        if self.is_position_usb_mode():
            monitor = MrMc240nUsbController(
                board_id=config["board_id"],
                axis_number=config["axis_number"],
                dll_path=config["dll_path"],
            )
        else:
            monitor = MrMc240nPositionController(
                board_id=config["board_id"],
                axis_number=config["axis_number"],
                dll_path=config["dll_path"],
                auto_start_system=config["auto_start_system"],
            )
        try:
            monitor.open()
        except Exception:
            monitor.close()
            raise

        self.position_monitor = monitor
        self.lbl_mr_status.setText(
            f"MR-MC240N: board {config['board_id']} axis {config['axis_number']} opened"
        )

    def close_position_monitor(self):
        if self.position_monitor is None:
            return
        try:
            if self.position_jog_command_active or self.position_monitor._jog_active:
                try:
                    self.position_monitor.stop(rapid=True, timeout_ms=3000)
                except Exception:
                    pass
            self.position_monitor.close()
        except Exception:
            pass
        finally:
            self.position_jog_command_active = False
            self.position_monitor = None

    def read_position_feedback(self):
        if not self.is_position_pcie_enabled():
            return None, None

        opened_here = False
        if self.position_monitor is None:
            self.open_position_monitor()
            opened_here = True

        try:
            config = self.get_position_monitor_config()
            raw_counts = self.position_monitor.read_feedback_position_counts()
            absolute_position_mm = raw_counts / config["counts_per_mm"]
            relative_position_mm = absolute_position_mm - self.position_zero_offset_mm
            return relative_position_mm, raw_counts
        finally:
            if opened_here and not self.is_simulating:
                self.close_position_monitor()

    def get_position_controller(self, require_armed=True):
        if not self.is_position_monitor_enabled():
            raise RuntimeError("MR-MC240N position board를 먼저 활성화해주세요.")
        if require_armed and not self.is_position_control_armed():
            raise RuntimeError("Arm motion commands를 먼저 활성화해주세요.")
        if self.position_monitor is None:
            self.open_position_monitor()
        if self.position_monitor is None:
            raise RuntimeError("MR-MC240N position board를 열지 못했습니다.")
        return self.position_monitor

    def handle_position_command_error(self, action, exc):
        message = f"{action} 실패: {exc}"
        self.lbl_mr_status.setText(f"MR-MC240N: {message}")
        QMessageBox.critical(self, "MR-MC240N Control Error", message)

    def set_position_servo(self, enabled):
        action = "Servo ON" if enabled else "Servo OFF"
        try:
            controller = self.get_position_controller(require_armed=True)
            controller.set_servo_on(enabled)
            self.lbl_mr_status.setText(
                f"MR-MC240N: {action} command sent to axis {controller.axis_number}"
            )
        except Exception as exc:
            self.handle_position_command_error(action, exc)

    def start_position_home(self):
        try:
            controller = self.get_position_controller(require_armed=True)
            controller.start_home_return()
            self.lbl_mr_status.setText(
                f"MR-MC240N: home return started on axis {controller.axis_number}"
            )
        except Exception as exc:
            self.handle_position_command_error("Home return", exc)

    def start_position_relative_move(self):
        try:
            controller = self.get_position_controller(require_armed=True)
            board_config = self.get_position_monitor_config()
            motion_config = self.get_position_motion_config()
            distance_counts = round(
                motion_config["distance_mm"] * board_config["counts_per_mm"]
            )
            if distance_counts == 0:
                raise ValueError(
                    "Relative Move와 Command Units / mm 조합이 1 command unit 미만입니다."
                )
            controller.move_relative(
                distance_counts,
                motion_config["speed"],
                motion_config["acceleration_ms"],
                motion_config["deceleration_ms"],
            )
            self.lbl_mr_status.setText(
                "MR-MC240N: relative move started "
                f"({motion_config['distance_mm']:.4f} mm / {distance_counts} command units)"
            )
        except Exception as exc:
            self.handle_position_command_error("Relative move", exc)

    def start_position_jog(self, direction):
        direction_text = "+" if direction == MrMc240nPositionController.SSC_DIR_PLUS else "-"
        try:
            controller = self.get_position_controller(require_armed=True)
            motion_config = self.get_position_motion_config()
            controller.start_jog(
                direction,
                motion_config["speed"],
                motion_config["acceleration_ms"],
                motion_config["deceleration_ms"],
            )
            self.position_jog_command_active = True
            self.lbl_mr_status.setText(f"MR-MC240N: JOG {direction_text} running")
        except Exception as exc:
            self.position_jog_command_active = False
            self.handle_position_command_error(f"JOG {direction_text}", exc)

    def stop_position_jog(self):
        if not self.position_jog_command_active:
            return
        try:
            controller = self.get_position_controller(require_armed=False)
            controller.stop_jog()
            self.lbl_mr_status.setText("MR-MC240N: JOG stopped")
        except Exception as exc:
            self.handle_position_command_error("JOG stop", exc)
        finally:
            self.position_jog_command_active = False

    def stop_position_motion(self, rapid):
        action = "Rapid stop" if rapid else "Stop"
        try:
            controller = self.get_position_controller(require_armed=False)
            controller.stop(rapid=rapid)
            self.position_jog_command_active = False
            self.lbl_mr_status.setText(f"MR-MC240N: {action} completed")
        except Exception as exc:
            self.handle_position_command_error(action, exc)

    def refresh_position_axis_status(self):
        try:
            controller = self.get_position_controller(require_armed=False)
            config = self.get_position_monitor_config()
            selected_axis = config["axis_number"]
            selected_summary = None
            waiting_for_sscnet = False
            if isinstance(controller, MrMc240nUsbController):
                controller.check_connection()
                waiting_for_sscnet = controller.system_status_code == 0x0009

            for axis, label in enumerate(self.mr_axis_status_labels, start=1):
                try:
                    axis_status = controller.read_axis_status(axis)
                    raw_counts = int(axis_status["position"])
                    position_mm = (
                        raw_counts / config["counts_per_mm"]
                        - self.position_zero_offset_mm
                    )
                    if waiting_for_sscnet:
                        short_state = "WAIT SSCNET"
                    elif (
                        axis_status.get("status0") == 0
                        and axis_status.get("status1") == 0
                    ):
                        short_state = "UNCONFIGURED"
                    elif axis_status["servo_alarm"] or axis_status["operation_alarm"]:
                        short_state = "ALARM"
                    elif axis_status["operating"]:
                        short_state = "RUNNING"
                    elif axis_status["servo_ready"]:
                        short_state = "READY"
                    else:
                        short_state = "NOT READY"

                    border_color = "#2979FF" if axis == selected_axis else "#D8DEE9"
                    background = "#EAF2FF" if axis == selected_axis else "#F5F7FA"
                    if short_state == "ALARM":
                        background = "#FFEBEE"
                        border_color = "#C62828"
                    elif short_state == "WAIT SSCNET":
                        background = "#FFF8E1"
                        border_color = "#F9A825"
                    label.setStyleSheet(
                        f"QLabel {{ background: {background}; "
                        f"border: 2px solid {border_color}; "
                        "border-radius: 4px; padding: 5px; }"
                    )
                    label.setText(
                        f"Axis {axis}  {short_state}\n"
                        f"{position_mm:.4f} mm  ({raw_counts} cmd)"
                    )

                    if axis == selected_axis:
                        state_names = [
                            text
                            for key, text in [
                                ("servo_ready", "READY"),
                                ("operating", "RUNNING"),
                                ("in_position", "IN-POS"),
                                ("home_complete", "HOME"),
                                ("servo_alarm", "SERVO-ALARM"),
                                ("operation_alarm", "OP-ALARM"),
                            ]
                            if axis_status[key]
                        ]
                        state_text = (
                            ", ".join(state_names) if state_names else "NOT READY"
                        )
                        if short_state == "WAIT SSCNET":
                            state_text = (
                                "WAITING SSCNET RESPONSE - check controller→CN1A, "
                                "CN1B→next CN1A, amplifier control power"
                            )
                        elif short_state == "UNCONFIGURED":
                            state_text = "AXIS NOT MOUNTED / CONFIGURED"
                        selected_summary = (
                            f"MR-MC240N axis {axis}: {state_text}, "
                            f"{position_mm:.4f} mm ({raw_counts} cmd)"
                        )
                except Exception as axis_exc:
                    label.setText(f"Axis {axis}  READ ERROR\n{axis_exc}")
                    label.setStyleSheet(
                        "QLabel { background: #FFF3E0; border: 2px solid #EF6C00; "
                        "border-radius: 4px; padding: 5px; }"
                    )
                    if axis == selected_axis:
                        selected_summary = (
                            f"MR-MC240N axis {axis}: status read failed - {axis_exc}"
                        )

            if selected_summary:
                self.lbl_mr_status.setText(selected_summary)
        except Exception as exc:
            self.handle_position_command_error("Axis status refresh", exc)

    def get_default_stroke_mm(self):
        try:
            return float(self.in_max_len.text())
        except ValueError:
            return 0.0

    def start_live_monitoring(self):
        try:
            if self.is_fc400_mode():
                self.open_fc400_usb_task()
                self.data_unit = self.get_fc400_config()["device_unit"]
            if self.is_position_pcie_enabled():
                self.open_position_monitor()
        except Exception as exc:
            self.close_ni_daq_task()
            self.close_position_monitor()
            QMessageBox.warning(self, "Hardware Connection Error", f"실장비 연결에 실패했습니다.\n{exc}")
            return

        self.is_simulating = True
        self.current_stroke = 0
        self.stroke_data_history = []
        self.stroke_position_history = []
        self.time_series_data = []
        self.time_elapsed = 0.0
        self.latest_live_snapshot = [0.0] * 6
        self.latest_live_position_mm = None
        self.latest_live_position_counts = None
        self.update_table_headers()
        self.chart.reset_scale()

        now = datetime.now()
        self.test_start_ts = now.strftime("%Y%m%d_%H%M%S")
        self.test_start_display_time = now.strftime('%Y-%m-%d %H:%M:%S')

        self.sim_state = "LIVE"
        self.btn_start.setText("Stop FC400 / USB-6002 Monitoring")
        self.btn_start.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 10px;")
        self.lbl_status.setText("Status: LIVE (실장비 모니터링)")
        self.timer.start(self.timer_interval)
        self.live_step()

    def live_step(self):
        try:
            measurement = self.read_fc400_measurement()
            load_value = measurement["value"]
            live_stable = measurement["stable"]
            live_voltage = measurement["voltage"]
        except Exception as exc:
            self.stop_simulation(completed=False, source_override=FC400_SOURCE)
            QMessageBox.critical(
                self, "FC400 Read Error", f"하중 값을 읽지 못했습니다.\n{exc}"
            )
            return

        try:
            position_mm, position_counts = self.read_position_feedback()
        except Exception as exc:
            self.stop_simulation(completed=False, source_override=FC400_SOURCE)
            QMessageBox.critical(self, "MR-MC240N Read Error", f"위치 값을 읽지 못했습니다.\n{exc}")
            return

        self.raw_data = [load_value] * 6
        self.update_table()
        self.update_chart()

        self.time_elapsed += self.timer_interval / 1000.0
        current_calibrated_base = [max(0, self.raw_data[i] - self.sensor_zeros[i]) for i in range(6)]
        self.latest_live_snapshot = current_calibrated_base.copy()
        self.latest_live_position_mm = position_mm
        self.latest_live_position_counts = position_counts

        current_calibrated_display = [
            self.convert_value_units(value, self.data_unit, self.unit)
            for value in current_calibrated_base
        ]

        log_row = {
            'Time [sec]': round(self.time_elapsed, 1),
            'Stroke': 1,
            'State': 'LIVE'
        }
        if position_mm is not None:
            log_row['Position [mm]'] = round(position_mm, 3)
        if position_counts is not None:
            log_row['Position Raw [cmd]'] = int(position_counts)
        if live_stable is not None:
            log_row['Stable'] = int(bool(live_stable))
        if live_voltage is not None:
            log_row['FC400 Analog Output [V]'] = round(live_voltage, 5)

        for i in range(6):
            log_row[f'Axis {i+1} Raw [{self.data_unit}]'] = round(self.raw_data[i], 3)
        for i in range(6):
            log_row[f'Axis {i+1} Calibrated [{self.unit}]'] = round(current_calibrated_display[i], 3)
        self.append_camera_metrics_to_log_row(log_row)
        self.time_series_data.append(log_row)

        source_name = "FC400"
        if position_mm is not None:
            self.lbl_status.setText(
                f"Status: LIVE ({source_name} {current_calibrated_display[0]:.2f} {self.unit}, Stroke {position_mm:.3f} mm)"
            )
        else:
            self.lbl_status.setText(
                f"Status: LIVE ({source_name} {current_calibrated_display[0]:.2f} {self.unit}, 6채널 동일)"
            )

    def timer_step(self):
        if self.is_live_monitor_mode():
            self.live_step()
        else:
            self.simulation_step()

    def ensure_export_snapshot(self):
        if self.stroke_data_history:
            return
        if self.is_live_monitor_mode() and len(self.time_series_data) > 0:
            self.stroke_data_history = [self.latest_live_snapshot.copy()]
            snapshot_mm = (
                self.latest_live_position_mm
                if self.latest_live_position_mm is not None
                else self.get_default_stroke_mm()
            )
            self.stroke_position_history = [snapshot_mm]

    def change_unit(self, unit):
        self.unit = unit
        self.update_table_headers()
        self.update_table()
        self.update_chart(reset_scale=True)

    def zero_sensors(self):
        # 모든 물리적 데이터, 영점 기준, 그리고 이전 테스트 기록과 시계열 데이터 완전히 리셋
        self.stroke_data_history = []
        self.stroke_position_history = []
        self.time_series_data = []
        self.time_elapsed = 0.0
        self.latest_live_snapshot = [0.0] * 6
        self.latest_live_position_mm = None
        self.latest_live_position_counts = None

        self.test_start_ts = None
        self.test_start_display_time = None

        if self.is_live_monitor_mode():
            try:
                current_value = self.read_fc400_measurement()["value"]
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "FC400 Zero Error",
                    f"로드셀 영점 설정에 실패했습니다.\n{exc}",
                )
                return

            self.raw_data = [current_value] * 6
            self.sensor_zeros = self.raw_data.copy()
            self.sim_current_load = 0.0
            status_text = "Status: Ready (Load Cell Tared)"
            message_text = "로드셀 영점(Tare) 및 이전 데이터 초기화가 완료되었습니다."

            if self.is_position_pcie_enabled():
                try:
                    current_position_mm, current_position_counts = self.read_position_feedback()
                    self.position_zero_offset_mm += current_position_mm if current_position_mm is not None else 0.0
                    self.latest_live_position_mm = 0.0
                    self.latest_live_position_counts = current_position_counts
                except Exception as exc:
                    self.lbl_mr_status.setText(f"MR-MC240N: 위치 영점은 유지됨 - {exc}")
        else:
            self.raw_data = [0.0] * 6
            self.sensor_zeros = [0.0] * 6
            self.sim_current_load = 0.0
            self.position_zero_offset_mm = 0.0
            status_text = "Status: Ready (Data Reset)"
            message_text = "센서 영점 맞춤 및 이전 데이터 초기화가 완료되었습니다."

        self.lbl_status.setText(status_text)
        self.update_table()
        self.update_chart(reset_scale=True)
        QMessageBox.information(self, "Zeroed", message_text)

    def update_table(self):
        for i in range(6):
            raw_display = self.raw_data[i]
            zero_display = self.sensor_zeros[i]
            calibrated_base = self.raw_data[i] - self.sensor_zeros[i]
            calibrated_display = self.convert_value_units(calibrated_base, self.data_unit, self.unit)
            self.table.setItem(i, 1, QTableWidgetItem(f"{raw_display:.2f}"))
            self.table.setItem(i, 2, QTableWidgetItem(f"{zero_display:.2f}"))
            self.table.setItem(i, 3, QTableWidgetItem(f"{calibrated_display:.2f}"))

    def get_calibrated_data(self):
        data = []
        for i in range(6):
            calibrated_base = self.raw_data[i] - self.sensor_zeros[i]
            display_val = self.convert_value_units(calibrated_base, self.data_unit, self.unit)
            data.append(max(0, display_val))
        return data

    def update_chart(self, reset_scale=False):
        data = self.get_calibrated_data()
        interp = self.interp_combo.currentText()
        self.chart.plot_data(data, interpolate_type=interp, unit=self.unit, reset_scale=reset_scale)

    def stop_simulation(self, completed=False, source_override=None):
        active_source = source_override if source_override is not None else self.input_source
        using_live_hardware = active_source == FC400_SOURCE
        self.is_simulating = False
        self.sim_state = "IDLE"
        self.timer.stop()
        self.update_start_button_idle_state()

        if using_live_hardware:
            self.close_ni_daq_task()
            self.close_position_monitor()
            self.ensure_export_snapshot()
            self.lbl_status.setText("Status: FC400 / USB-6002 Monitoring Stopped")
            self.update_table()
            self.update_chart()
            return

        if completed:
            if len(self.stroke_data_history) > 0:
                self.raw_data = [val + self.sensor_zeros[i] for i, val in enumerate(self.stroke_data_history[-1])]
            self.lbl_status.setText(f"Status: Test Completed ({len(self.stroke_data_history)} Strokes)")
        else:
            self.lbl_status.setText(f"Status: Stopped ({self.current_stroke} / {self.target_strokes} Strokes)")
        self.update_table()
        self.update_chart()

    def toggle_simulation(self):
        if not self.is_simulating:
            if self.is_live_monitor_mode():
                self.start_live_monitoring()
                return

            try:
                self.target_strokes = int(self.in_strokes.text())
                self.target_load = float(self.in_load.text())
                self.hold_time = float(self.in_hold.text())
            except ValueError:
                QMessageBox.warning(self, "Input Error", "숫자를 정확히 입력해주세요.")
                return

            self.is_simulating = True
            self.current_stroke = 0
            self.data_unit = "kgf"

            # 새 테스트 시작 시 이력 리셋
            self.stroke_data_history = []
            self.stroke_position_history = []
            self.time_series_data = []
            self.time_elapsed = 0.0
            self.latest_live_position_mm = None
            self.latest_live_position_counts = None
            self.position_zero_offset_mm = 0.0
            self.update_table_headers()
            self.chart.reset_scale()

            now = datetime.now()
            self.test_start_ts = now.strftime("%Y%m%d_%H%M%S")
            self.test_start_display_time = now.strftime('%Y-%m-%d %H:%M:%S')

            self.sim_state = "PULLING"
            self.sim_current_load = 0.0

            self.btn_start.setText("Stop Simulation")
            self.btn_start.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 10px;")
            self.lbl_status.setText(f"Status: PULLING ({self.current_stroke} / {self.target_strokes} Strokes)")
            self.timer.start(self.timer_interval)
        else:
            self.stop_simulation(completed=False)

    def simulation_step(self):
        load_step = self.target_load / (1500 / self.timer_interval)

        # 1. 상태에 따른 하중 논리 갱신
        if self.sim_state == "PULLING":
            self.sim_current_load += load_step
            if self.sim_current_load >= self.target_load:
                self.sim_current_load = self.target_load
                self.sim_state = "HOLDING"
                self.hold_ticks = int((self.hold_time * 1000) / self.timer_interval)
        elif self.sim_state == "HOLDING":
            if self.hold_ticks == int((self.hold_time * 1000) / self.timer_interval):
                calibrated = [(self.raw_data[i] - self.sensor_zeros[i]) for i in range(6)]
                self.stroke_data_history.append(calibrated)
                self.stroke_position_history.append(self.get_default_stroke_mm())
            self.hold_ticks -= 1
            if self.hold_ticks <= 0:
                self.sim_state = "RELEASING"
        elif self.sim_state == "RELEASING":
            self.sim_current_load -= load_step
            if self.sim_current_load <= 0:
                self.sim_current_load = 0.0

        # 2. 물리적 Raw Data 시뮬레이션 적용 및 UI 반영
        noise = np.random.normal(0, max(0.05, self.target_load * 0.01), 6)
        dist_factors = [1.0, 0.96, 1.04, 0.98, 1.02, 1.0]
        for i in range(6):
            if self.sim_current_load <= 0:
                val = 0
            else:
                val = self.sim_current_load * dist_factors[i] + noise[i]
            self.raw_data[i] = max(0, val) + self.sensor_zeros[i]

        self.update_table()
        self.update_chart()

        # 3. 실시간 시계열 데이터 로깅 (Raw 데이터 및 Calibrated 데이터 모두 저장)
        self.time_elapsed += self.timer_interval / 1000.0
        current_calibrated = [max(0, self.raw_data[i] - self.sensor_zeros[i]) for i in range(6)]
        if self.unit == "N":
            current_calibrated = [v * 9.80665 for v in current_calibrated]

        log_row = {
            'Time [sec]': round(self.time_elapsed, 1),
            'Stroke': self.current_stroke + 1 if self.current_stroke < self.target_strokes else self.target_strokes,
            'State': self.sim_state
        }

        # 1~6축 Raw Data 추가
        for i in range(6):
            log_row[f'Axis {i+1} Raw'] = round(self.raw_data[i], 2)

        # 1~6축 Calibrated Data 추가
        for i in range(6):
            log_row[f'Axis {i+1} Calibrated [{self.unit}]'] = round(current_calibrated[i], 2)
        self.append_camera_metrics_to_log_row(log_row)

        self.time_series_data.append(log_row)

        # 4. 스트로크 완료 여부 체크 및 상태 전환
        if self.sim_state == "RELEASING" and self.sim_current_load <= 0:
            self.current_stroke += 1
            if self.current_stroke >= self.target_strokes:
                self.stop_simulation(completed=True)
                QMessageBox.information(self, "Test Complete", f"{self.target_strokes} Strokes 테스트가 완료되었습니다.")
                return
            else:
                self.sim_state = "PULLING"

        if self.is_simulating:
            self.lbl_status.setText(f"Status: {self.sim_state} ({self.current_stroke} / {self.target_strokes} Strokes)")

    def get_stroke_position_value(self, index):
        if index < len(self.stroke_position_history):
            return self.stroke_position_history[index]
        return self.get_default_stroke_mm()

    def export_csv(self):
        self.ensure_export_snapshot()
        if len(self.stroke_data_history) == 0:
            QMessageBox.warning(self, "No Data", "저장할 테스트 데이터가 없습니다.")
            return

        ts = self.test_start_ts if self.test_start_ts else datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"ClampData_{ts}.csv"
        file_name, _ = QFileDialog.getSaveFileName(self, "Save CSV", default_name, "CSV Files (*.csv)")

        if file_name:
            all_data = np.array(self.stroke_data_history)
            all_data = self.convert_array_units(all_data, self.data_unit, self.unit)

            # ============ 1. 상단 요약본 (스트로크 통계) ============
            records = []
            for idx, stroke_data in enumerate(all_data):
                row_dict = {'No': f'Stroke {idx + 1}', 'Stroke [mm]': round(self.get_stroke_position_value(idx), 3)}
                for axis_idx in range(6):
                    row_dict[f'Axis {axis_idx + 1} [{self.unit}]'] = round(stroke_data[axis_idx], 2)
                row_dict[f'Average [{self.unit}]'] = round(np.mean(stroke_data), 2)
                records.append(row_dict)

            df_main = pd.DataFrame(records)

            min_vals = np.min(all_data, axis=0)
            max_vals = np.max(all_data, axis=0)
            range_vals = max_vals - min_vals
            avg_vals = np.mean(all_data, axis=0)

            stat_rows = [
                {'No': 'Min', 'Stroke [mm]': round(np.min(self.stroke_position_history), 3) if self.stroke_position_history else self.get_default_stroke_mm()},
                {'No': 'Max', 'Stroke [mm]': round(np.max(self.stroke_position_history), 3) if self.stroke_position_history else self.get_default_stroke_mm()},
                {'No': 'R (Range)', 'Stroke [mm]': '0.00'},
                {'No': 'Ave (Total)', 'Stroke [mm]': round(np.mean(self.stroke_position_history), 3) if self.stroke_position_history else self.get_default_stroke_mm()}
            ]

            for i in range(6):
                col_name = f'Axis {i + 1} [{self.unit}]'
                stat_rows[0][col_name] = round(min_vals[i], 2)
                stat_rows[1][col_name] = round(max_vals[i], 2)
                stat_rows[2][col_name] = round(range_vals[i], 2)
                stat_rows[3][col_name] = round(avg_vals[i], 2)

            avg_col_name = f'Average [{self.unit}]'
            stat_rows[0][avg_col_name] = round(np.min(all_data), 2)
            stat_rows[1][avg_col_name] = round(np.max(all_data), 2)
            stat_rows[2][avg_col_name] = round(np.max(range_vals), 2)
            stat_rows[3][avg_col_name] = round(np.mean(all_data), 2)

            df_stats = pd.DataFrame(stat_rows)
            df_final = pd.concat([df_main, df_stats], ignore_index=True)

            # 요약본 파일에 우선 저장
            df_final.to_csv(file_name, index=False, encoding='utf-8-sig')

            # ============ 2. 하단 시계열 (Time Series Raw Data) ============
            with open(file_name, 'a', encoding='utf-8-sig') as f:
                f.write('\n\n--- Time Series Raw & Calibrated Data ---\n')

            df_ts = pd.DataFrame(self.time_series_data)
            # 모드를 'a'(Append)로 설정하여 기존 CSV 파일 아래에 이어서 작성
            df_ts.to_csv(file_name, mode='a', index=False, encoding='utf-8-sig')

            QMessageBox.information(self, "Saved", f"CSV 파일이 성공적으로 저장되었습니다.\n{file_name}")

    def export_pdf(self):
        self.ensure_export_snapshot()
        if len(self.stroke_data_history) == 0:
            QMessageBox.warning(self, "No Data", "저장할 테스트 데이터가 없습니다.")
            return

        ts = self.test_start_ts if self.test_start_ts else datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"ClampReport_{ts}.pdf"
        file_name, _ = QFileDialog.getSaveFileName(self, "Save Report", default_name, "PDF Files (*.pdf)")
        if not file_name:
            return

        fig = plt.figure(figsize=(8.27, 11.69))
        fig.patch.set_facecolor('white')

        # 메인 타이틀
        fig.text(0.35, 0.94, 'Test Report', ha='center', fontsize=20, fontproperties=font_prop, weight='bold')

        # ==================== 완벽하게 고정된 결재란 ====================
        ax_sign = fig.add_axes([0.65, 0.89, 0.27, 0.07])
        ax_sign.axis('off')

        rect = Rectangle((0, 0), 0.15, 1, transform=ax_sign.transAxes,
                         facecolor='#F0F0F0', edgecolor='black', linewidth=0.8)
        ax_sign.add_patch(rect)

        ax_sign.text(0.075, 0.5, 'SIGN', fontproperties=font_prop, fontsize=8,
                     ha='center', va='center', rotation='vertical')

        sign_data = [
            ['EDIT', 'CHECK', 'APPROVE'],
            ['', '', '']
        ]

        table_sign = ax_sign.table(cellText=sign_data, cellLoc='center',
                                   bbox=[0.15, 0, 0.85, 1])
        table_sign.auto_set_font_size(False)
        table_sign.set_fontsize(8)

        for key, cell in table_sign.get_celld().items():
            row, col = key
            cell.set_edgecolor('black')
            cell.set_linewidth(0.8)
            cell.set_text_props(fontproperties=font_prop)
            if row == 0:
                cell.set_height(0.3)
                cell.set_facecolor('#F0F0F0')
            else:
                cell.set_height(0.7)
                cell.set_facecolor('white')
        # =============================================================

        # 헤더 테이블
        ax_header = fig.add_axes([0.08, 0.82, 0.84, 0.08])
        ax_header.axis('off')
        date_str = self.test_start_display_time if self.test_start_display_time else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        target_spec = f"기준하중: {self.in_load.text()}{self.unit}"

        header_data = [
            ['관리번호 (Report No)', self.in_report_no.text(), '시험항목 (Test Item)', '힘 (Load)'],
            ['고객사 (Customer)', self.in_customer.text(), '판정기준 (Test Spec)', '합격'],
            ['차종 (Model)', self.in_model.text(), '시험목적 (Test purpose)', target_spec],
            ['품명 (Part Name)', self.in_part_name.text(), '의뢰자 (Client)', ''],
            ['품번 (Part No)', self.in_part_no.text(), 'Test Start', date_str]
        ]

        table_header = ax_header.table(cellText=header_data, cellLoc='center', loc='center',
                                       colWidths=[0.2, 0.3, 0.2, 0.3])
        table_header.auto_set_font_size(False)
        table_header.set_fontsize(8)
        table_header.scale(1, 1.6)
        for key, cell in table_header.get_celld().items():
            cell.set_edgecolor('black')
            cell.set_linewidth(0.8)
            cell.set_text_props(fontproperties=font_prop)
            if key[1] % 2 == 0: cell.set_facecolor('#F0F0F0')

        # 데이터 테이블
        ax_table = fig.add_axes([0.08, 0.44, 0.84, 0.35])
        ax_table.axis('off')

        all_data = np.array(self.stroke_data_history)
        all_data = self.convert_array_units(all_data, self.data_unit, self.unit)

        table_data = [['No', 'Stroke\n[mm]', 'Axis 1', 'Axis 2', 'Axis 3',
                       'Axis 4', 'Axis 5', 'Axis 6', f'Average']]

        for idx, stroke_data in enumerate(all_data):
            stroke_mm = f"{self.get_stroke_position_value(idx):.3f}"
            avg = np.mean(stroke_data)
            row = [str(idx+1), stroke_mm] + [f'{v:.2f}' for v in stroke_data] + [f'{avg:.2f}']
            table_data.append(row)

        for i in range(10 - len(all_data)):
            table_data.append(['-', '-', '-', '-', '-', '-', '-', '-', '-'])

        min_vals = np.min(all_data, axis=0)
        max_vals = np.max(all_data, axis=0)
        range_vals = max_vals - min_vals
        avg_vals = np.mean(all_data, axis=0)
        min_stroke = round(np.min(self.stroke_position_history), 3) if self.stroke_position_history else self.get_default_stroke_mm()
        max_stroke = round(np.max(self.stroke_position_history), 3) if self.stroke_position_history else self.get_default_stroke_mm()
        avg_stroke = round(np.mean(self.stroke_position_history), 3) if self.stroke_position_history else self.get_default_stroke_mm()

        table_data.append(['Min', f'{min_stroke:.3f}'] + [f'{v:.2f}' for v in min_vals] + [f'{np.min(all_data):.2f}'])
        table_data.append(['Max', f'{max_stroke:.3f}'] + [f'{v:.2f}' for v in max_vals] + [f'{np.max(all_data):.2f}'])
        table_data.append(['R', '0.00'] + [f'{v:.2f}' for v in range_vals] + [f'{np.max(range_vals):.2f}'])
        table_data.append(['Ave', f'{avg_stroke:.3f}'] + [f'{v:.2f}' for v in avg_vals] + [f'{np.mean(all_data):.2f}'])

        data_table = ax_table.table(cellText=table_data, cellLoc='center', loc='center',
                                    colWidths=[0.06, 0.1, 0.12, 0.12, 0.12, 0.12, 0.12, 0.12, 0.12])
        data_table.auto_set_font_size(False)
        data_table.set_fontsize(7.5)
        data_table.scale(1, 1.45)

        for key, cell in data_table.get_celld().items():
            cell.set_edgecolor('black')
            cell.set_linewidth(0.5)
            cell.set_text_props(fontproperties=font_prop)
            row, col = key
            if row == 0:
                cell.set_facecolor('#E0E0E0')
                cell.set_text_props(weight='bold', fontproperties=font_prop)
            elif row > len(all_data) and row <= 10:
                cell.set_text_props(color='#999999')
            elif row > 10:
                cell.set_facecolor('#FFF5E6')
                if col == 0:
                    cell.set_text_props(weight='bold', fontproperties=font_prop)

        # Remark 섹션
        ax_remark = fig.add_axes([0.08, 0.40, 0.84, 0.03])
        ax_remark.axis('off')
        ax_remark.text(0.01, 0.5, ' ', fontsize=8, fontproperties=font_prop, weight='bold', va='center')
        rect = Rectangle((0, 0), 1, 1, linewidth=1, edgecolor='black', facecolor='none', transform=ax_remark.transAxes)
        ax_remark.add_patch(rect)

        # 방사형 그래프
        ax_graph = fig.add_axes([0.25, 0.06, 0.5, 0.32], polar=True)
        data = self.get_calibrated_data()
        angles = np.linspace(0, 2 * np.pi, 6, endpoint=False)
        plot_data = data + [data[0]]
        angles_plot = np.append(angles, angles[0])

        ax_graph.set_theta_offset(np.pi / 2)
        ax_graph.set_theta_direction(-1)
        ax_graph.set_xticks(angles)
        ax_graph.set_xticklabels(['Axis 1', 'Axis 2', 'Axis 3', 'Axis 4', 'Axis 5', 'Axis 6'], fontproperties=font_prop, fontsize=9)

        interpolate_type = self.interp_combo.currentText()
        if interpolate_type == "Smooth (Spline 곡선)":
            try:
                extended_angles = np.concatenate([angles - 2*np.pi, angles, angles + 2*np.pi])
                extended_data = data * 3
                f = interp1d(extended_angles, extended_data, kind='cubic')
                t_smooth = np.linspace(0, 2 * np.pi, 100)
                smooth_data = np.clip(f(t_smooth), 0, None)
                ax_graph.plot(t_smooth, smooth_data, 'b-', linewidth=2)
                ax_graph.fill(t_smooth, smooth_data, 'b', alpha=0.15)
            except Exception:
                ax_graph.plot(angles_plot, plot_data, 'b-', linewidth=2)
                ax_graph.fill(angles_plot, plot_data, 'b', alpha=0.15)
        else:
            ax_graph.plot(angles_plot, plot_data, 'b-', linewidth=2)
            ax_graph.fill(angles_plot, plot_data, 'b', alpha=0.15)

        ax_graph.scatter(angles, data, color='red', s=40, zorder=5)
        ax_graph.set_ylabel(f'Load [{self.unit}]', labelpad=25, fontproperties=font_prop, fontsize=9)
        ax_graph.set_title('Final Load Distribution (6-Axis)', pad=15, fontproperties=font_prop, fontsize=11, weight='bold')
        ax_graph.grid(True, linestyle='--', alpha=0.7)

        fig.savefig(file_name, format='pdf', dpi=300)
        plt.close(fig)

        QMessageBox.information(self, "Saved", f"A4 성적서가 성공적으로 저장되었습니다.\n{file_name}")

    def closeEvent(self, event):
        self.close_camera(reset_status=False)
        self.close_ni_daq_task()
        self.close_position_monitor()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            if self.isFullScreen():
                self.showMaximized()
            else:
                self.showFullScreen()
            event.accept()
            return

        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.showMaximized()
            event.accept()
            return

        super().keyPressEvent(event)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = ClampSimulatorApp()
    ex.showMaximized()
    sys.exit(app.exec_())
