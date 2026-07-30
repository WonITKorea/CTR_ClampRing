import sys
import os
import copy
import threading
import time
import numpy as np
import pandas as pd
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QGridLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
                             QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
                             QFileDialog, QMessageBox, QPlainTextEdit, QDialog,
                             QDialogButtonBox, QTabWidget, QSlider,
                             QAbstractItemView)
from PyQt5.QtCore import QLocale, QSize, QStandardPaths, Qt, QTimer
from PyQt5.QtGui import QDoubleValidator, QIcon, QImage, QIntValidator, QPixmap
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_pdf import PdfPages
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

APP_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
CTR_LOGO_PATH = os.path.join(APP_DIRECTORY, "Logo_CTR.png")
CTR_LOGO_MAX_WIDTH = 150
CTR_LOGO_HEADER_HEIGHT = 44

AXIS_TRAVEL_MIN_MM = 0.0
AXIS_TRAVEL_MAX_MM = 196.0
USB_MOTION_SPEED_MIN_MM_MIN = 1
USB_MOTION_SPEED_MAX_MM_MIN = 12_000
MOTION_RAMP_MIN_MS = 0
MOTION_RAMP_MAX_MS = 20_000
TEST_HOLD_MAX_SECONDS = 3_600.0
TEST_STROKES_MAX = 10_000
LOAD_INPUT_MAX = 1_000_000.0

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


class AspectRatioLogoLabel(QLabel):
    """A logo label whose height follows its available width."""

    def __init__(self, image_path, parent=None):
        super().__init__(parent)
        self._source_pixmap = QPixmap(image_path)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet("background: transparent; border: none;")

    def hasHeightForWidth(self):
        return not self._source_pixmap.isNull()

    def heightForWidth(self, width):
        if self._source_pixmap.isNull() or width <= 0:
            return 0
        return max(
            1,
            round(
                width
                * self._source_pixmap.height()
                / self._source_pixmap.width()
            ),
        )

    def sizeHint(self):
        width = 200
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self):
        width = 100
        return QSize(width, self.heightForWidth(width))

    def resizeEvent(self, event):
        if not self._source_pixmap.isNull():
            self.setPixmap(
                self._source_pixmap.scaled(
                    event.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        super().resizeEvent(event)


class ClampTestMachineApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("6-Axis Clamp Test Machine")
        if os.path.isfile(CTR_LOGO_PATH):
            self.setWindowIcon(QIcon(CTR_LOGO_PATH))
        self.configure_window_geometry()
        self.unit = "N"

        self.is_test_running = False
        self.test_state = "IDLE"
        self.current_stroke = 0
        self.target_strokes = 3
        self.target_load = 10.0
        self.hold_time = 5.0
        self.timer_interval = 100
        self.data_unit = "N"

        self.sensor_zeros = [0.0] * 6
        self.raw_data = [0.0] * 6
        self.latest_live_snapshot = [0.0] * 6
        self.ni_daq_task = None
        self.position_monitor = None
        self.position_jog_command_active = False
        self.position_jog_direction = None
        self.position_motion_may_be_active = False
        self.position_controller_close_failed = False
        self.latest_live_position_mm = None
        self.latest_live_position_counts = None
        self.position_zero_offset_mm = 0.0
        self.fc400_device_ready = False
        self.fc400_readiness_detail = "NI device not checked"
        self.position_axis_status_checked = False
        self.position_axis_ready = False
        self.position_readiness_detail = "MR-MC240N not checked"
        self.live_motion_cycle_active = False
        self.live_motion_config = None
        self.live_motion_target_mm = None
        self.live_motion_deadline = 0.0
        self.live_hold_deadline = 0.0
        self.live_stroke_peak_values = None
        self.camera_capture = None
        self.camera_capture_thread = None
        self.camera_capture_stop_event = threading.Event()
        self.camera_frame_lock = threading.Lock()
        self.camera_latest_frame = None
        self.camera_latest_frame_id = 0
        self.camera_processed_frame_id = 0
        self.camera_latest_frame_timestamp = 0.0
        self.camera_video_writer = None
        self.camera_recording_path = None
        self.camera_recording_started_at = None
        self.camera_recording_frame_count = 0
        self.camera_recording_started_by_test = False
        self.camera_timer = QTimer(self)
        self.camera_timer.timeout.connect(self.camera_timer_step)
        self.camera_timer.setTimerType(Qt.PreciseTimer)
        self.position_motion_status_timer = QTimer(self)
        self.position_motion_status_timer.setInterval(250)
        self.position_motion_status_timer.timeout.connect(
            self.poll_position_motion_status
        )
        self.position_motion_status_deadline = 0.0
        self.position_motion_status_failures = 0
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
        self._system_log_last_seen = {}
        self.load_limit_tripped = False
        self.sample_results = []
        self.current_sample_result_key = None
        self.review_indices = []
        self.review_selected_data_index = None

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

    @staticmethod
    def configure_double_input(
        widget,
        minimum,
        maximum,
        decimals=3,
        description="",
    ):
        validator = QDoubleValidator(minimum, maximum, decimals, widget)
        validator.setNotation(QDoubleValidator.StandardNotation)
        validator.setLocale(QLocale.c())
        widget.setValidator(validator)
        range_text = f"입력 범위: {minimum:g} ~ {maximum:g}"
        widget.setToolTip(
            f"{description}\n{range_text}".strip()
        )

    @staticmethod
    def configure_int_input(widget, minimum, maximum, description=""):
        validator = QIntValidator(minimum, maximum, widget)
        validator.setLocale(QLocale.c())
        widget.setValidator(validator)
        range_text = f"입력 범위: {minimum} ~ {maximum}"
        widget.setToolTip(
            f"{description}\n{range_text}".strip()
        )

    def initUI(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(14)

        left_container = QWidget()
        left_container.setMinimumWidth(820)
        left_layout = QGridLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setHorizontalSpacing(12)
        left_layout.setVerticalSpacing(10)
        left_layout.setColumnMinimumWidth(0, 275)
        left_layout.setColumnMinimumWidth(1, 275)
        left_layout.setColumnStretch(0, 3)
        left_layout.setColumnStretch(1, 3)
        left_layout.setColumnStretch(2, 6)

        report_panel = QWidget()
        report_panel_layout = QVBoxLayout(report_panel)
        report_panel_layout.setContentsMargins(0, 0, 0, 0)
        report_panel_layout.setSpacing(6)

        report_header = QWidget()
        report_header.setFixedHeight(CTR_LOGO_HEADER_HEIGHT)
        report_header_layout = QHBoxLayout(report_header)
        report_header_layout.setContentsMargins(0, 0, 0, 0)
        report_header_layout.setSpacing(8)

        self.lbl_ctr_logo = AspectRatioLogoLabel(CTR_LOGO_PATH)
        self.lbl_ctr_logo.setMaximumWidth(CTR_LOGO_MAX_WIDTH)
        if self.lbl_ctr_logo.hasHeightForWidth():
            report_header_layout.addWidget(self.lbl_ctr_logo)
        else:
            self.lbl_ctr_logo.hide()

        self.lbl_program_title = QLabel("6축 클램프링\n테스트 장비")
        self.lbl_program_title.setAlignment(Qt.AlignCenter)
        self.lbl_program_title.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self.lbl_program_title.setStyleSheet(
            "background: transparent; border: none; "
            "font-size: 13px; font-weight: 600;"
        )
        report_header_layout.addWidget(self.lbl_program_title, 1)
        report_panel_layout.addWidget(report_header)

        group_report = QGroupBox("Report")
        group_report.setStyleSheet(
            "QGroupBox { font-size: 11px; font-weight: 600; } "
            "QLabel { font-size: 11px; } "
            "QLineEdit { font-size: 11px; min-height: 26px; "
            "padding: 2px 4px; }"
        )
        layout_report = QGridLayout()
        layout_report.setContentsMargins(12, 18, 12, 14)
        layout_report.setHorizontalSpacing(10)
        layout_report.setVerticalSpacing(10)
        report_row = 0
        layout_report.addWidget(
            QLabel("관리번호\n(Report No.)"), report_row, 0
        )
        self.in_report_no = QLineEdit(f"Q-26-{datetime.now().strftime('%m%d')}-001")
        layout_report.addWidget(self.in_report_no, report_row, 1)
        report_row += 1
        layout_report.addWidget(QLabel("고객사\n(Customer)"), report_row, 0)
        self.in_customer = QLineEdit("TESLA")
        layout_report.addWidget(self.in_customer, report_row, 1)
        report_row += 1
        layout_report.addWidget(QLabel("차종\n(Model)"), report_row, 0)
        self.in_model = QLineEdit("PMY")
        layout_report.addWidget(self.in_model, report_row, 1)
        report_row += 1
        layout_report.addWidget(QLabel("품명\n(Part)"), report_row, 0)
        self.in_part_name = QLineEdit("CABJ O-Ring")
        layout_report.addWidget(self.in_part_name, report_row, 1)
        report_row += 1
        layout_report.addWidget(QLabel("품번\n(Part No.)"), report_row, 0)
        self.in_part_no = QLineEdit("GCR0127")
        layout_report.addWidget(self.in_part_no, report_row, 1)
        report_row += 1
        layout_report.addWidget(QLabel("시료\n(Sample No.)"), report_row, 0)
        self.in_sample_no = QLineEdit("1")
        layout_report.addWidget(self.in_sample_no, report_row, 1)
        layout_report.setColumnStretch(0, 0)
        layout_report.setColumnStretch(1, 1)
        layout_report.setColumnMinimumWidth(0, 105)
        layout_report.setColumnMinimumWidth(1, 135)
        group_report.setLayout(layout_report)
        group_report.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Expanding
        )
        report_panel_layout.addWidget(group_report)
        left_layout.addWidget(report_panel, 0, 0)

        group_params = QGroupBox("테스트 설정")
        group_params.setStyleSheet(
            "QGroupBox { font-size: 11px; font-weight: 600; } "
            "QLabel { font-size: 11px; } "
            "QLineEdit { font-size: 11px; min-height: 26px; "
            "padding: 2px 4px; }"
        )
        layout_params = QGridLayout()
        layout_params.setContentsMargins(12, 18, 12, 14)
        layout_params.setHorizontalSpacing(10)
        layout_params.setVerticalSpacing(10)
        layout_params.addWidget(QLabel("최소 길이\n[mm]:"), 0, 0)
        self.in_min_len = QLineEdit("0.0")
        self.configure_double_input(
            self.in_min_len,
            AXIS_TRAVEL_MIN_MM,
            AXIS_TRAVEL_MAX_MM,
            description="시험 시작 위치",
        )
        layout_params.addWidget(self.in_min_len, 0, 1)
        layout_params.addWidget(QLabel("최대 길이\n[mm]:"), 1, 0)
        self.in_max_len = QLineEdit("50.0")
        self.configure_double_input(
            self.in_max_len,
            AXIS_TRAVEL_MIN_MM,
            AXIS_TRAVEL_MAX_MM,
            description="시험 종료 위치",
        )
        layout_params.addWidget(self.in_max_len, 1, 1)
        layout_params.addWidget(QLabel("속도\n[mm/min]:"), 2, 0)
        self.in_speed = QLineEdit("10")
        self.configure_int_input(
            self.in_speed,
            USB_MOTION_SPEED_MIN_MM_MIN,
            USB_MOTION_SPEED_MAX_MM_MIN,
            "MR-MC240N 자동 시험 이동 속도",
        )
        layout_params.addWidget(self.in_speed, 2, 1)
        layout_params.addWidget(QLabel("목표 하중 시간\n(초):"), 3, 0)
        self.in_hold = QLineEdit("5.0")
        self.configure_double_input(
            self.in_hold,
            0.0,
            TEST_HOLD_MAX_SECONDS,
            description="최대 위치 유지 시간",
        )
        layout_params.addWidget(self.in_hold, 3, 1)
        self.lbl_target_load_input = QLabel(
            f"목표 하중\n[{self.unit}]:"
        )
        layout_params.addWidget(self.lbl_target_load_input, 4, 0)
        self.in_load = QLineEdit("10.0")
        self.configure_double_input(
            self.in_load,
            0.0,
            LOAD_INPUT_MAX,
            description="시험 목표 하중(현재 표시 단위)",
        )
        layout_params.addWidget(self.in_load, 4, 1)
        self.lbl_load_limit_input = QLabel(
            f"안전 하중 리미트\n[{self.unit}]:"
        )
        layout_params.addWidget(self.lbl_load_limit_input, 5, 0)
        self.in_load_limit = QLineEdit("100.0")
        self.configure_double_input(
            self.in_load_limit,
            0.001,
            LOAD_INPUT_MAX,
            description="이 값 이상이 감지되면 6축 전체에 긴급정지를 보냅니다.",
        )
        self.in_load_limit.setStyleSheet(
            "QLineEdit { border: 1px solid #C62828; }"
        )
        layout_params.addWidget(self.in_load_limit, 5, 1)
        layout_params.addWidget(QLabel("스트로크 횟수:"), 6, 0)
        self.in_strokes = QLineEdit("3")
        self.configure_int_input(
            self.in_strokes,
            1,
            TEST_STROKES_MAX,
            "1 Stroke는 Min → Max → Hold → Min 왕복 1회입니다.",
        )
        layout_params.addWidget(self.in_strokes, 6, 1)
        layout_params.setColumnStretch(0, 0)
        layout_params.setColumnStretch(1, 1)
        layout_params.setColumnMinimumWidth(0, 115)
        layout_params.setColumnMinimumWidth(1, 125)
        self.lbl_test_ranges = QLabel(
            "범위: 길이 0~196 mm · 속도 1~12,000 mm/min · "
            "유지 0~3,600 s · Stroke 1~10,000"
        )
        self.lbl_test_ranges.setWordWrap(True)
        self.lbl_test_ranges.setStyleSheet("color: #555555; font-size: 10px;")
        layout_params.addWidget(self.lbl_test_ranges, 7, 0, 1, 2)
        self.lbl_status = QLabel("Status: NOT READY (Hardware not checked)")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: #C62828; font-weight: bold;")
        layout_params.addWidget(self.lbl_status, 8, 0, 1, 2)
        group_params.setLayout(layout_params)
        group_params.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Expanding
        )
        left_layout.addWidget(group_params, 0, 1)

        group_settings = QGroupBox("그래프 세팅")
        layout_settings = QVBoxLayout()
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["kgf", "N"])
        self.unit_combo.setCurrentText("N")
        self.unit = self.unit_combo.currentText()
        self.unit_combo.currentTextChanged.connect(self.change_unit)
        layout_settings.addWidget(QLabel("Unit Selection:"))
        layout_settings.addWidget(self.unit_combo)

        self.interp_combo = QComboBox()
        self.interp_combo.addItems(["Linear (직선)", "Smooth (Spline 곡선)"])
        self.interp_combo.currentTextChanged.connect(self.update_chart)
        layout_settings.addWidget(QLabel("Graph Interpolation:"))
        layout_settings.addWidget(self.interp_combo)

        self.btn_zero = QPushButton("영점조절 & Data 리셋")
        self.btn_zero.setStyleSheet(
            "font-size: 11px; padding: 5px 8px;"
        )
        self.btn_zero.clicked.connect(self.zero_sensors)
        group_settings.setLayout(layout_settings)
        group_settings.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        group_fc400 = QGroupBox("FC400 / USB-6002")
        layout_fc400 = QGridLayout()
        layout_fc400.setContentsMargins(12, 16, 12, 12)
        layout_fc400.setHorizontalSpacing(16)
        layout_fc400.setVerticalSpacing(10)
        layout_fc400.setColumnMinimumWidth(0, 250)
        layout_fc400.setColumnStretch(1, 1)

        layout_fc400.addWidget(QLabel("기기 채널:"), 0, 0)
        self.in_fc400_daq_channel = QLineEdit("Dev1/ai0")
        self.in_fc400_daq_channel.editingFinished.connect(self.refresh_ni_devices)
        layout_fc400.addWidget(self.in_fc400_daq_channel, 0, 1)

        layout_fc400.addWidget(QLabel("모드:"), 1, 0)
        self.fc400_terminal_combo = QComboBox()
        self.fc400_terminal_combo.addItems(["Differential", "RSE"])
        layout_fc400.addWidget(self.fc400_terminal_combo, 1, 1)

        layout_fc400.addWidget(QLabel("무부하 전압 [V]:"), 2, 0)
        self.in_fc400_zero_voltage = QLineEdit("0.0")
        self.configure_double_input(
            self.in_fc400_zero_voltage, -10.0, 10.0, decimals=5
        )
        layout_fc400.addWidget(self.in_fc400_zero_voltage, 2, 1)

        layout_fc400.addWidget(QLabel("최대 출력 전압 [V]:"), 3, 0)
        self.in_fc400_full_scale_voltage = QLineEdit("10.0")
        self.configure_double_input(
            self.in_fc400_full_scale_voltage, -10.0, 10.0, decimals=5
        )
        layout_fc400.addWidget(self.in_fc400_full_scale_voltage, 3, 1)

        layout_fc400.addWidget(QLabel("FC400 최대값:"), 4, 0)
        self.in_fc400_full_scale_load = QLineEdit("1000.0")
        self.configure_double_input(
            self.in_fc400_full_scale_load,
            0.001,
            LOAD_INPUT_MAX,
            description="FC400 설정 용량",
        )
        layout_fc400.addWidget(self.in_fc400_full_scale_load, 4, 1)

        layout_fc400.addWidget(QLabel("FC400 단위:"), 5, 0)
        self.fc400_device_unit_combo = QComboBox()
        self.fc400_device_unit_combo.addItems(["N", "kgf"])
        self.fc400_device_unit_combo.setCurrentText("N")
        self.fc400_device_unit_combo.currentTextChanged.connect(self.on_source_configuration_changed)
        layout_fc400.addWidget(self.fc400_device_unit_combo, 5, 1)

        layout_fc400.addWidget(QLabel("샘플 속도 [S/s]:"), 6, 0)
        self.in_fc400_sample_rate = QLineEdit("1000")
        self.configure_int_input(
            self.in_fc400_sample_rate,
            1,
            50_000,
            "USB-6002 연속 취득 속도",
        )
        layout_fc400.addWidget(self.in_fc400_sample_rate, 6, 1)

        self.btn_refresh_fc400_daq = QPushButton("NI 기기 새로고침")
        self.btn_refresh_fc400_daq.clicked.connect(self.refresh_ni_devices)
        layout_fc400.addWidget(self.btn_refresh_fc400_daq, 7, 0, 1, 2)

        init_fc400_status = (
            "FC400 voltage output: Differential wiring V OUT -> AI0, COM -> AI4 (AI0-)"
        )
        if not NIDAQMX_AVAILABLE:
            init_fc400_status = f"USB-6002: nidaqmx import failed - {NIDAQMX_IMPORT_ERROR}"
        self._fc400_status_text = init_fc400_status

        group_fc400.setLayout(layout_fc400)
        group_fc400.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        group_position = QGroupBox("MR-MC240N 6축")
        layout_position = QVBoxLayout()
        layout_position.setContentsMargins(12, 14, 12, 12)
        layout_position.setSpacing(10)

        self.chk_position_monitor = QCheckBox("Enable MR-MC240N position board")
        self.chk_position_monitor.toggled.connect(self.on_position_monitor_toggled)
        if os.name != "nt":
            self.chk_position_monitor.setEnabled(False)

        connection_group = QGroupBox("포지션보드 연결 설정")
        connection_group.setStyleSheet(
            "QLabel { font-size: 11px; font-weight: 500; } "
            "QPushButton { font-size: 11px; padding: 4px 6px; }"
        )
        connection_grid = QGridLayout(connection_group)
        connection_grid.setContentsMargins(10, 12, 10, 10)
        connection_grid.setHorizontalSpacing(16)
        connection_grid.setVerticalSpacing(9)
        connection_grid.setColumnMinimumWidth(0, 250)
        connection_grid.setColumnStretch(1, 1)

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

        connection_grid.addWidget(QLabel("DLL 경로 (optional):"), 1, 0)
        self.in_mr_dll_path = QLineEdit("")
        self.in_mr_dll_path.editingFinished.connect(
            self.on_position_connection_settings_changed
        )
        connection_grid.addWidget(self.in_mr_dll_path, 1, 1)

        connection_grid.addWidget(QLabel("보드 ID:"), 2, 0)
        self.in_mr_board_id = QLineEdit("0")
        self.configure_int_input(self.in_mr_board_id, 0, 3)
        self.in_mr_board_id.editingFinished.connect(
            self.on_position_connection_settings_changed
        )
        connection_grid.addWidget(self.in_mr_board_id, 2, 1)

        connection_grid.addWidget(QLabel("축 No:"), 3, 0)
        self.in_mr_axis_no = QComboBox()
        self.in_mr_axis_no.addItems([str(axis) for axis in range(1, 7)])
        self.in_mr_axis_no.setToolTip(
            "HG-KR13 + MR-J4-10B-RJ six-axis setup. "
            "Amplifier rotary switch 0..5 maps to control axis 1..6."
        )
        self.in_mr_axis_no.currentTextChanged.connect(
            self.on_position_axis_changed
        )
        connection_grid.addWidget(self.in_mr_axis_no, 3, 1)

        connection_grid.addWidget(QLabel("Command Units / mm:"), 4, 0)
        self.in_mr_counts_per_mm = QLineEdit("1000.0")
        self.configure_double_input(
            self.in_mr_counts_per_mm,
            0.001,
            LOAD_INPUT_MAX,
            description=(
                "HG-KR13 + BTK1404 직결 프리셋은 "
                "1 command unit = 1 µm입니다."
            ),
        )
        connection_grid.addWidget(self.in_mr_counts_per_mm, 4, 1)

        self.chk_mr_auto_start = QCheckBox(
            "PCIe: start system if preparation is complete"
        )
        self.chk_mr_auto_start.setToolTip(
            "Already-running systems are left unchanged. Mitsubishi's "
            "sscSystemStart may wait at least 10 seconds while initializing SSCNET."
        )
        self.chk_mr_auto_start.setEnabled(False)
        self.chk_mr_auto_start.toggled.connect(
            self.on_position_connection_settings_changed
        )
        connection_grid.addWidget(self.chk_mr_auto_start, 5, 0, 1, 2)

        self.chk_mr_motion_arm = QCheckBox("모션 명령 Arm")
        self.chk_mr_motion_arm.toggled.connect(self.on_position_motion_arm_toggled)

        self.btn_mr_connect = QPushButton("Connect USB Controller")
        self.btn_mr_connect.setMinimumHeight(38)
        self.btn_mr_connect.clicked.connect(self.test_position_board_connection)
        connection_grid.addWidget(self.btn_mr_connect, 6, 0)
        self.btn_mr_system_start = QPushButton("USB System Start")
        self.btn_mr_system_start.setMinimumHeight(38)
        self.btn_mr_system_start.clicked.connect(self.start_position_usb_system)
        connection_grid.addWidget(self.btn_mr_system_start, 6, 1)
        self.btn_mr_apply_six_axis = QPushButton(
            "Apply HG-KR13 ×6 / BTK1404 Preset"
        )
        self.btn_mr_apply_six_axis.setMinimumHeight(38)
        self.btn_mr_apply_six_axis.clicked.connect(
            self.apply_position_six_axis_preset
        )
        connection_grid.addWidget(self.btn_mr_apply_six_axis, 7, 0, 1, 2)
        position_settings_page = QWidget()
        position_settings_layout = QVBoxLayout(position_settings_page)
        position_settings_layout.setContentsMargins(0, 0, 0, 0)
        position_settings_layout.setSpacing(12)
        position_settings_layout.addWidget(self.chk_position_monitor)
        position_settings_layout.addWidget(connection_group)
        position_settings_layout.addStretch(1)

        motion_group = QGroupBox("축 이동 / 모션")
        motion_group.setStyleSheet(
            "QLabel { font-size: 11px; } "
            "QLineEdit, QCheckBox { font-size: 10px; } "
            "QPushButton { font-size: 11px; padding: 4px 1px; }"
        )
        motion_grid = QGridLayout(motion_group)
        motion_grid.setContentsMargins(10, 12, 10, 10)
        motion_grid.setHorizontalSpacing(10)
        motion_grid.setVerticalSpacing(5)
        motion_grid.setColumnStretch(0, 1)
        motion_grid.setColumnStretch(1, 1)

        motion_grid.addWidget(QLabel("속도\n[mm/min]:"), 0, 0)
        self.in_mr_motion_speed = QLineEdit("100")
        self.configure_int_input(
            self.in_mr_motion_speed,
            USB_MOTION_SPEED_MIN_MM_MIN,
            USB_MOTION_SPEED_MAX_MM_MIN,
            "수동 이동/JOG 속도",
        )
        motion_grid.addWidget(self.in_mr_motion_speed, 1, 0)

        motion_grid.addWidget(QLabel("가속/감속\n[ms]:"), 0, 1)
        motion_time_layout = QHBoxLayout()
        self.in_mr_acceleration_ms = QLineEdit("500")
        self.in_mr_deceleration_ms = QLineEdit("500")
        self.configure_int_input(
            self.in_mr_acceleration_ms,
            MOTION_RAMP_MIN_MS,
            MOTION_RAMP_MAX_MS,
            "가속 시간",
        )
        self.configure_int_input(
            self.in_mr_deceleration_ms,
            MOTION_RAMP_MIN_MS,
            MOTION_RAMP_MAX_MS,
            "감속 시간",
        )
        motion_time_layout.addWidget(self.in_mr_acceleration_ms)
        motion_time_layout.addWidget(self.in_mr_deceleration_ms)
        motion_grid.addLayout(motion_time_layout, 1, 1)

        motion_grid.addWidget(QLabel("상대이동\n[mm]:"), 2, 0)
        self.in_mr_relative_move_mm = QLineEdit("1.0")
        self.configure_double_input(
            self.in_mr_relative_move_mm,
            -AXIS_TRAVEL_MAX_MM,
            AXIS_TRAVEL_MAX_MM,
            description="현재 위치 기준 이동량(목표 위치 0~196 mm 이내)",
        )
        motion_grid.addWidget(self.in_mr_relative_move_mm, 3, 0)

        servo_layout = QHBoxLayout()
        servo_layout.setSpacing(4)
        self.btn_mr_servo_on = QPushButton("SON")
        self.btn_mr_servo_on.setToolTip("Servo ON")
        self.btn_mr_servo_on.clicked.connect(lambda: self.set_position_servo(True))
        self.btn_mr_servo_off = QPushButton("SOFF")
        self.btn_mr_servo_off.setToolTip("Servo OFF")
        self.btn_mr_servo_off.clicked.connect(lambda: self.set_position_servo(False))
        servo_layout.addWidget(self.btn_mr_servo_on)
        servo_layout.addWidget(self.btn_mr_servo_off)
        motion_grid.addLayout(servo_layout, 3, 1)

        motion_layout = QHBoxLayout()
        motion_layout.setSpacing(4)
        self.btn_mr_home = QPushButton("홈")
        self.btn_mr_home.clicked.connect(self.start_position_home)
        self.btn_mr_move_relative = QPushButton("이동")
        self.btn_mr_move_relative.clicked.connect(self.start_position_relative_move)
        motion_layout.addWidget(self.btn_mr_home)
        motion_layout.addWidget(self.btn_mr_move_relative)
        motion_grid.addLayout(motion_layout, 4, 0)

        jog_layout = QHBoxLayout()
        jog_layout.setSpacing(4)
        self.btn_mr_jog_minus = QPushButton("JOG-")
        self.btn_mr_jog_minus.pressed.connect(
            lambda: self.start_position_jog(MrMc240nPositionController.SSC_DIR_MINUS)
        )
        self.btn_mr_jog_minus.released.connect(self.stop_position_jog)
        self.btn_mr_jog_plus = QPushButton("JOG+")
        self.btn_mr_jog_plus.pressed.connect(
            lambda: self.start_position_jog(MrMc240nPositionController.SSC_DIR_PLUS)
        )
        self.btn_mr_jog_plus.released.connect(self.stop_position_jog)
        jog_layout.addWidget(self.btn_mr_jog_minus)
        jog_layout.addWidget(self.btn_mr_jog_plus)
        motion_grid.addLayout(jog_layout, 4, 1)

        stop_layout = QHBoxLayout()
        stop_layout.setSpacing(4)
        self.btn_mr_stop = QPushButton("정지")
        self.btn_mr_stop.clicked.connect(lambda: self.stop_position_motion(False))
        self.btn_mr_rapid_stop = QPushButton("긴급정지")
        self.btn_mr_rapid_stop.clicked.connect(self.stop_all_position_axes)
        self.btn_mr_rapid_stop.setStyleSheet(
            "background-color: #C62828; color: white; font-weight: bold;"
        )
        stop_layout.addWidget(self.btn_mr_stop)
        stop_layout.addWidget(self.btn_mr_rapid_stop)
        motion_grid.addLayout(stop_layout, 5, 0)

        self.btn_mr_refresh_status = QPushButton("축 상태 갱신")
        self.btn_mr_refresh_status.clicked.connect(self.refresh_position_axis_status)
        motion_grid.addWidget(self.btn_mr_refresh_status, 5, 1)
        for position_control_button in (
            self.btn_mr_servo_on,
            self.btn_mr_servo_off,
            self.btn_mr_home,
            self.btn_mr_move_relative,
            self.btn_mr_jog_minus,
            self.btn_mr_jog_plus,
            self.btn_mr_stop,
            self.btn_mr_rapid_stop,
            self.btn_mr_refresh_status,
        ):
            position_control_button.setMinimumHeight(36)
        self.lbl_motion_ranges = QLabel(
            "허용 범위: 위치 0~196 mm · 속도 1~12,000 mm/min · "
            "가속/감속 0~20,000 ms"
        )
        self.lbl_motion_ranges.setWordWrap(True)
        self.lbl_motion_ranges.setStyleSheet(
            "color: #555555; font-size: 11px;"
        )
        motion_grid.addWidget(self.lbl_motion_ranges, 6, 0, 1, 2)
        motion_grid.addWidget(self.chk_mr_motion_arm, 7, 0, 1, 2)
        layout_position.addWidget(motion_group)

        overview_group = QGroupBox("6축 상태")
        overview_grid = QGridLayout(overview_group)
        overview_grid.setContentsMargins(8, 10, 8, 8)
        overview_grid.setHorizontalSpacing(8)
        overview_grid.setVerticalSpacing(8)
        self.mr_axis_status_labels = []
        for axis in range(1, 7):
            label = QLabel(f"A{axis}\n미확인")
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(58)
            label.setStyleSheet(
                "QLabel { background: #F5F7FA; border: 1px solid #D8DEE9; "
                "border-radius: 4px; padding: 6px; font-size: 11px; }"
            )
            overview_grid.addWidget(label, (axis - 1) // 3, (axis - 1) % 3)
            overview_grid.setColumnStretch((axis - 1) % 3, 1)
            self.mr_axis_status_labels.append(label)
        layout_position.addWidget(overview_group)

        mr_status = "MR-MC240N: Windows + matching Mitsubishi API DLL required"
        if os.name != "nt":
            mr_status = f"MR-MC240N: {MR_MC240N_WINDOWS_ONLY_MESSAGE}"

        log_group = QGroupBox("System Log")
        log_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(8, 10, 8, 8)
        log_layout.setSpacing(6)

        self.system_log = QPlainTextEdit()
        self.system_log.setReadOnly(True)
        self.system_log.setMinimumHeight(140)
        self.system_log.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self.system_log.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.system_log.document().setMaximumBlockCount(500)
        self.system_log.setStyleSheet(
            "QPlainTextEdit { background: #F5F7FA; border: 1px solid #D8DEE9; "
            "border-radius: 4px; padding: 6px; font-family: Consolas, monospace; }"
        )
        log_layout.addWidget(self.system_log)

        self.btn_clear_system_log = QPushButton("로그 지우기")
        self.btn_clear_system_log.setStyleSheet("font-size: 10px;")
        self.btn_clear_system_log.clicked.connect(self.clear_system_log)
        log_layout.addWidget(self.btn_clear_system_log, 0, Qt.AlignRight)
        layout_position.addWidget(log_group, 1)

        self.append_system_log("Application initialized", "SYSTEM")
        self.append_system_log(init_fc400_status, "FC400")
        self.append_system_log(mr_status, "MR-MC240N")

        group_position.setLayout(layout_position)
        group_position.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        left_layout.addWidget(group_position, 0, 2, 6, 1)

        preparation_group = QGroupBox("시험 준비")
        preparation_group.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 5px 8px; }"
        )
        preparation_layout = QHBoxLayout(preparation_group)
        self.btn_open_settings = QPushButton("장비 / 그래프 설정")
        self.btn_open_settings.clicked.connect(self.open_settings_dialog)
        preparation_layout.addWidget(self.btn_open_settings)
        preparation_layout.addWidget(self.btn_zero)
        for preparation_button in (
            self.btn_open_settings,
            self.btn_zero,
        ):
            preparation_button.setMinimumHeight(38)
        left_layout.addWidget(preparation_group, 1, 0, 1, 2)

        sample_group = QGroupBox("시료 결과 모음")
        sample_group.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 4px 6px; }"
        )
        sample_layout = QVBoxLayout(sample_group)
        sample_layout.setContentsMargins(8, 10, 8, 8)
        sample_layout.setSpacing(6)
        self.sample_table = QTableWidget(0, 4)
        self.sample_table.setHorizontalHeaderLabels(
            ["시료", "관리번호", "완료 시각", "Stroke"]
        )
        self.sample_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.sample_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sample_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.sample_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sample_table.setMaximumHeight(150)
        sample_layout.addWidget(self.sample_table)

        sample_actions = QHBoxLayout()
        self.btn_add_sample = QPushButton("현재 결과 추가")
        self.btn_add_sample.clicked.connect(self.add_current_sample_result)
        self.btn_remove_sample = QPushButton("선택 삭제")
        self.btn_remove_sample.clicked.connect(self.remove_selected_sample_results)
        self.btn_clear_samples = QPushButton("전체 초기화")
        self.btn_clear_samples.clicked.connect(self.clear_sample_results)
        sample_actions.addWidget(self.btn_add_sample)
        sample_actions.addWidget(self.btn_remove_sample)
        sample_actions.addWidget(self.btn_clear_samples)
        for sample_action_button in (
            self.btn_add_sample,
            self.btn_remove_sample,
            self.btn_clear_samples,
        ):
            sample_action_button.setMinimumHeight(36)
        sample_layout.addLayout(sample_actions)
        left_layout.addWidget(sample_group, 2, 0, 1, 2)

        self.btn_start = QPushButton("FC400 + MR-MC240N 시험 시작")
        self.btn_start.clicked.connect(self.toggle_test)
        self.btn_start.setMinimumHeight(46)
        self.btn_start.setStyleSheet(
            "background-color: #4CAF50; color: white; font-size: 12px; "
            "font-weight: bold; padding: 10px;"
        )
        left_layout.addWidget(self.btn_start, 3, 0, 1, 2)
        left_layout.setRowStretch(5, 1)
        for widget_type in (QLineEdit, QPushButton, QComboBox):
            for widget in left_container.findChildren(widget_type):
                policy = widget.sizePolicy()
                policy.setHorizontalPolicy(QSizePolicy.Expanding)
                widget.setSizePolicy(policy)
        main_layout.addWidget(left_container, 14)

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

        review_panel = QWidget()
        review_layout = QHBoxLayout(review_panel)
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.setSpacing(8)
        review_layout.addWidget(QLabel("탐색:"))
        self.review_mode_combo = QComboBox()
        self.review_mode_combo.addItem("시간 순", "time")
        self.review_mode_combo.addItem("위치 순", "position")
        self.review_mode_combo.currentIndexChanged.connect(
            self.on_review_mode_changed
        )
        review_layout.addWidget(self.review_mode_combo)
        self.review_slider = QSlider(Qt.Horizontal)
        self.review_slider.setRange(0, 0)
        self.review_slider.setEnabled(False)
        self.review_slider.valueChanged.connect(
            self.on_review_slider_changed
        )
        review_layout.addWidget(self.review_slider, 1)
        self.lbl_review_position = QLabel("LIVE")
        self.lbl_review_position.setMinimumWidth(120)
        self.lbl_review_position.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        review_layout.addWidget(self.lbl_review_position)
        top_visual_layout.addWidget(review_panel)

        group_camera_settings = QGroupBox("Jig / Camera / Ring")
        layout_camera = QGridLayout()
        layout_camera.setContentsMargins(12, 16, 12, 12)
        layout_camera.setHorizontalSpacing(16)
        layout_camera.setVerticalSpacing(10)
        layout_camera.setColumnMinimumWidth(0, 250)
        layout_camera.setColumnStretch(1, 1)

        layout_camera.addWidget(QLabel("Jig Size:"), 0, 0)
        self.jig_combo = QComboBox()
        self.jig_combo.addItems(["10.6 mm", "22.6 mm", "32.6 mm"])
        self.jig_combo.currentTextChanged.connect(self.update_camera_focus)
        layout_camera.addWidget(self.jig_combo, 0, 1)

        layout_camera.addWidget(QLabel("Camera Index:"), 1, 0)
        self.in_camera_index = QLineEdit("0")
        self.configure_int_input(self.in_camera_index, 0, 32)
        layout_camera.addWidget(self.in_camera_index, 1, 1)

        layout_camera.addWidget(QLabel("Known Ring OD [mm]:"), 2, 0)
        self.in_camera_reference_diameter = QLineEdit(
            self.jig_combo.currentText().split()[0]
        )
        self.in_camera_reference_diameter.setModified(False)
        self.configure_double_input(
            self.in_camera_reference_diameter,
            0.1,
            AXIS_TRAVEL_MAX_MM,
            description="실제 링 외경을 기준으로 pixel 값을 mm로 환산합니다.",
        )
        layout_camera.addWidget(self.in_camera_reference_diameter, 2, 1)

        layout_camera.addWidget(QLabel("Resolution:"), 3, 0)
        self.camera_resolution_combo = QComboBox()
        self.camera_resolution_combo.addItems(["640x480", "1280x720", "1920x1080"])
        self.camera_resolution_combo.setCurrentText("1280x720")
        layout_camera.addWidget(self.camera_resolution_combo, 3, 1)

        layout_camera.addWidget(QLabel("Clamp Ring Color:"), 4, 0)
        self.camera_ring_color_combo = QComboBox()
        for profile_key, profile in CAMERA_RING_COLOR_PROFILES.items():
            self.camera_ring_color_combo.addItem(profile["label"], profile_key)
        self.camera_ring_color_combo.setCurrentIndex(self.camera_ring_color_combo.findData("silver"))
        self.camera_ring_color_combo.currentTextChanged.connect(self.on_camera_ring_color_changed)
        layout_camera.addWidget(self.camera_ring_color_combo, 4, 1)

        self.chk_camera_auto_record = QCheckBox(
            "시험 시작 시 카메라 자동 녹화"
        )
        self.chk_camera_auto_record.setToolTip(
            "카메라가 열려 있으면 시험 시작과 함께 사용자 Videos 폴더에 "
            "오버레이 포함 AVI 영상을 저장합니다."
        )
        layout_camera.addWidget(
            self.chk_camera_auto_record,
            5,
            0,
            1,
            2,
        )

        self.btn_camera_toggle = QPushButton("카메라 열기")
        self.btn_camera_toggle.clicked.connect(self.toggle_camera)
        self.btn_camera_toggle.setMinimumHeight(34)
        self.btn_camera_toggle.setToolTip(
            "UVC 카메라 프리뷰를 열거나 닫습니다."
        )

        self.btn_camera_baseline = QPushButton("기준 캡처")
        self.btn_camera_baseline.clicked.connect(self.capture_ring_baseline)
        self.btn_camera_baseline.setEnabled(False)

        self.btn_camera_clear_baseline = QPushButton("기준 해제")
        self.btn_camera_clear_baseline.clicked.connect(self.clear_ring_baseline)
        self.btn_camera_clear_baseline.setEnabled(False)

        self.lbl_camera = QLabel("Camera Focus Action: Adjusted to 10.6 mm")
        self.lbl_camera.setWordWrap(True)

        init_camera_status = "Camera: connect a UVC camera and click Open Camera"
        if not CV2_AVAILABLE:
            init_camera_status = f"Camera: OpenCV import failed - {CV2_IMPORT_ERROR}"
            self.btn_camera_toggle.setEnabled(False)
        self._camera_status_text = init_camera_status
        self.append_system_log(init_camera_status, "CAMERA")

        group_camera_settings.setLayout(layout_camera)
        group_camera_settings.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Maximum
        )
        for widget_type in (QLineEdit, QPushButton, QComboBox):
            for widget in group_camera_settings.findChildren(widget_type):
                policy = widget.sizePolicy()
                policy.setHorizontalPolicy(QSizePolicy.Expanding)
                widget.setSizePolicy(policy)

        self.settings_dialog = QDialog(self)
        self.settings_dialog.setWindowTitle("장비 / 그래프 설정")
        self.settings_dialog.setMinimumSize(920, 560)
        self.settings_dialog.resize(980, 600)
        settings_dialog_layout = QVBoxLayout(self.settings_dialog)
        settings_dialog_layout.setContentsMargins(16, 16, 16, 16)
        settings_dialog_layout.setSpacing(12)

        def create_padded_settings_page(content):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(20, 22, 20, 20)
            page_layout.setSpacing(0)
            page_layout.addWidget(content, 0, Qt.AlignTop)
            page_layout.addStretch(1)
            return page

        self.settings_tabs = QTabWidget()
        settings_tab_bar = self.settings_tabs.tabBar()
        settings_tab_bar.setExpanding(True)
        settings_tab_bar.setElideMode(Qt.ElideNone)
        self.settings_tabs.setUsesScrollButtons(False)
        self.settings_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #C8CDD4; top: -1px; } "
            "QTabBar::tab { min-height: 26px; }"
        )
        self.settings_tabs.addTab(
            create_padded_settings_page(group_settings), "그래프"
        )
        self.settings_tabs.addTab(
            create_padded_settings_page(group_fc400), "FC400 / USB-6002"
        )
        self.settings_tabs.addTab(
            create_padded_settings_page(group_camera_settings), "Camera / Ring"
        )
        self.settings_tabs.addTab(
            create_padded_settings_page(position_settings_page),
            "MR-MC240N 연결",
        )
        settings_dialog_layout.addWidget(self.settings_tabs, 1)
        settings_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        settings_buttons.rejected.connect(self.settings_dialog.accept)
        settings_dialog_layout.addWidget(settings_buttons)

        group_camera_viewfinder = QGroupBox("Camera Viewfinder")
        group_camera_viewfinder.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 4px 6px; }"
        )
        layout_camera_viewfinder = QVBoxLayout(group_camera_viewfinder)

        self.lbl_camera_preview = QLabel("Camera preview is not running.")
        self.lbl_camera_preview.setAlignment(Qt.AlignCenter)
        self.lbl_camera_preview.setWordWrap(True)
        self.lbl_camera_preview.setMinimumSize(260, 160)
        self.lbl_camera_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.lbl_camera_preview.setStyleSheet(
            "background-color: #111111; color: #DDDDDD; border: 1px solid #444444; padding: 8px;"
        )
        layout_camera_viewfinder.addWidget(self.lbl_camera_preview, 1)

        camera_actions = QHBoxLayout()
        camera_actions.addWidget(self.btn_camera_toggle)
        camera_actions.addWidget(self.btn_camera_baseline)
        camera_actions.addWidget(self.btn_camera_clear_baseline)
        self.btn_camera_record = QPushButton("녹화 시작")
        self.btn_camera_record.clicked.connect(self.toggle_camera_recording)
        self.btn_camera_record.setEnabled(False)
        camera_actions.addWidget(self.btn_camera_record)
        for camera_action_button in (
            self.btn_camera_toggle,
            self.btn_camera_baseline,
            self.btn_camera_clear_baseline,
            self.btn_camera_record,
        ):
            camera_action_button.setMinimumHeight(38)
        layout_camera_viewfinder.addLayout(camera_actions)
        layout_camera_viewfinder.addWidget(self.lbl_camera)

        self.lbl_ring_metrics = QLabel("")
        self.lbl_ring_metrics.setWordWrap(True)
        self.lbl_ring_metrics.setVisible(False)
        self.lbl_ring_metrics.setStyleSheet(
            "background-color: #F8F8F8; border: 1px solid #D0D0D0; padding: 8px; font-family: monospace;"
        )
        layout_camera_viewfinder.addWidget(self.lbl_ring_metrics)

        top_visual_layout.insertWidget(0, group_camera_viewfinder, 3)
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
        self.btn_csv = QPushButton("CSV 데이터 저장")
        self.btn_csv.setStyleSheet("font-size: 10px;")
        self.btn_csv.clicked.connect(self.export_csv)
        self.btn_pdf = QPushButton("성적서 선택/전체 일괄 출력")
        self.btn_pdf.setStyleSheet("font-size: 10px;")
        self.btn_pdf.clicked.connect(self.export_pdf)
        btn_layout.addWidget(self.btn_csv)
        btn_layout.addWidget(self.btn_pdf)
        right_panel.addLayout(btn_layout)

        main_layout.addWidget(right_container, 6)
        self.timer = QTimer()
        self.timer.timeout.connect(self.timer_step)

        self.on_position_monitor_toggled(False)
        self.update_table_headers()
        self.update_chart()
        self.refresh_ni_devices()
        self.update_camera_button_state()
        self.update_ring_metrics_label()
        self.refresh_sample_results_table()
        self.refresh_review_controls()

    def open_settings_dialog(self):
        if self.is_test_running or self.position_motion_may_be_active:
            QMessageBox.warning(
                self,
                "설정 잠금",
                "시험 또는 축 동작을 완전히 정지한 뒤 설정을 변경해주세요.",
            )
            return
        self.settings_dialog.exec_()

    def set_test_form_locked(self, locked):
        form_widgets = [
            self.in_report_no,
            self.in_customer,
            self.in_model,
            self.in_part_name,
            self.in_part_no,
            self.in_sample_no,
            self.in_min_len,
            self.in_max_len,
            self.in_speed,
            self.in_hold,
            self.in_load,
            self.in_load_limit,
            self.in_strokes,
            self.btn_zero,
            self.btn_add_sample,
            self.btn_remove_sample,
            self.btn_clear_samples,
        ]
        for widget in form_widgets:
            widget.setEnabled(not locked)
        if not locked:
            self.refresh_sample_results_table()

    def append_system_log(self, message, source="SYSTEM", dedupe_seconds=2.0):
        if not hasattr(self, "system_log"):
            return

        message_text = " | ".join(
            part.strip() for part in str(message).splitlines() if part.strip()
        )
        if not message_text:
            return

        source_prefix = f"{source}:"
        if message_text.lower().startswith(source_prefix.lower()):
            message_text = message_text[len(source_prefix):].strip()

        now_monotonic = time.monotonic()
        signature = (source, message_text)
        last_seen = self._system_log_last_seen.get(signature)
        if (
            dedupe_seconds > 0
            and last_seen is not None
            and now_monotonic - last_seen < dedupe_seconds
        ):
            return
        self._system_log_last_seen[signature] = now_monotonic

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.system_log.appendPlainText(f"[{timestamp}] [{source}] {message_text}")
        self.system_log.verticalScrollBar().setValue(
            self.system_log.verticalScrollBar().maximum()
        )

    def clear_system_log(self):
        self.system_log.clear()
        self._system_log_last_seen.clear()
        self.append_system_log("Log cleared", "SYSTEM", dedupe_seconds=0)

    def set_mr_status_text(self, message):
        self.append_system_log(message, "MR-MC240N")

    def set_fc400_status_text(self, message):
        if self._fc400_status_text != message:
            self._fc400_status_text = message
            self.append_system_log(message, "FC400")

    def update_camera_focus(self, size):
        self.lbl_camera.setText(f"Camera Focus Action: Adjusted to {size}")
        if hasattr(self, "in_camera_reference_diameter") and not self.in_camera_reference_diameter.isModified():
            self.in_camera_reference_diameter.setText(size.split()[0])

    def set_camera_preview_message(self, message):
        self.lbl_camera_preview.clear()
        self.lbl_camera_preview.setText(message)

    def set_camera_status_text(self, message, log_event=True):
        if self._camera_status_text != message:
            self._camera_status_text = message
            if log_event:
                self.append_system_log(message, "CAMERA")

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
            self.btn_camera_toggle.setText(
                "카메라 닫기"
                if camera_open
                else "카메라 열기"
            )
            if camera_open:
                self.btn_camera_toggle.setStyleSheet(
                    "background-color: #D32F2F; color: white; font-weight: bold;"
                )
            else:
                self.btn_camera_toggle.setStyleSheet(
                    "background-color: #388E3C; color: white; font-weight: bold;"
                )
        else:
            self.btn_camera_toggle.setEnabled(False)
            self.btn_camera_toggle.setText("카메라 열기")
            self.btn_camera_toggle.setStyleSheet("")

        self.btn_camera_baseline.setEnabled(camera_open and self.latest_ring_measurement is not None)
        self.btn_camera_clear_baseline.setEnabled(self.camera_baseline is not None)
        recording = self.camera_recording_path is not None
        self.btn_camera_record.setEnabled(camera_open)
        self.btn_camera_record.setText("녹화 정지" if recording else "녹화 시작")
        self.btn_camera_record.setStyleSheet(
            "background-color: #C62828; color: white; font-weight: bold;"
            if recording
            else ""
        )

    def toggle_camera_recording(self):
        if self.camera_recording_path is not None:
            self.stop_camera_recording()
            return
        self.start_camera_recording()

    def start_camera_recording(self, file_path=None, started_by_test=False):
        if self.camera_capture is None:
            QMessageBox.warning(
                self,
                "Camera Not Open",
                "카메라를 먼저 연 뒤 녹화를 시작해주세요.",
            )
            return False

        if file_path is None:
            default_name = (
                f"ClampCamera_{datetime.now().strftime('%Y%m%d_%H%M%S')}.avi"
            )
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "카메라 녹화 파일 저장",
                default_name,
                "AVI Video (*.avi)",
            )
        if not file_path:
            return False
        if not file_path.lower().endswith(".avi"):
            file_path += ".avi"

        self.stop_camera_recording(notify=False)
        self.camera_recording_path = os.path.abspath(file_path)
        self.camera_recording_started_at = datetime.now()
        self.camera_recording_frame_count = 0
        self.camera_recording_started_by_test = bool(started_by_test)
        self.camera_video_writer = None
        self.append_system_log(
            f"카메라 녹화 시작: {self.camera_recording_path}",
            "CAMERA",
            dedupe_seconds=0,
        )
        self.update_camera_button_state()
        return True

    def start_test_camera_recording(self):
        if not self.chk_camera_auto_record.isChecked():
            return False
        if self.camera_capture is None:
            self.append_system_log(
                "자동 녹화가 설정됐지만 카메라가 열려 있지 않아 녹화를 "
                "시작하지 않았습니다.",
                "CAMERA",
                dedupe_seconds=0,
            )
            return False

        movies_directory = QStandardPaths.writableLocation(
            QStandardPaths.MoviesLocation
        )
        if not movies_directory:
            movies_directory = APP_DIRECTORY
        recording_directory = os.path.join(
            movies_directory,
            "CTR_ClampRing_Recordings",
        )
        os.makedirs(recording_directory, exist_ok=True)
        safe_sample_no = "".join(
            character
            for character in self.in_sample_no.text().strip()
            if character.isalnum() or character in {"-", "_"}
        ) or "sample"
        recording_name = (
            f"ClampCamera_{self.test_start_ts or datetime.now().strftime('%Y%m%d_%H%M%S')}"
            f"_{safe_sample_no}.avi"
        )
        return self.start_camera_recording(
            os.path.join(recording_directory, recording_name),
            started_by_test=True,
        )

    def write_camera_recording_frame(self, frame):
        if self.camera_recording_path is None:
            return

        if self.camera_video_writer is None:
            height, width = frame.shape[:2]
            fps = 30.0
            capture = self.camera_capture
            if capture is not None and hasattr(cv2, "CAP_PROP_FPS"):
                reported_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
                if 1.0 <= reported_fps <= 120.0:
                    fps = reported_fps
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            writer = cv2.VideoWriter(
                self.camera_recording_path,
                fourcc,
                fps,
                (width, height),
            )
            if not writer.isOpened():
                writer.release()
                raise RuntimeError(
                    f"녹화 파일을 열 수 없습니다: {self.camera_recording_path}"
                )
            self.camera_video_writer = writer

        self.camera_video_writer.write(frame)
        self.camera_recording_frame_count += 1

    def stop_camera_recording(self, notify=True):
        recording_path = self.camera_recording_path
        writer = self.camera_video_writer
        frame_count = self.camera_recording_frame_count
        self.camera_video_writer = None
        self.camera_recording_path = None
        self.camera_recording_started_at = None
        self.camera_recording_frame_count = 0
        self.camera_recording_started_by_test = False

        if writer is not None:
            try:
                writer.release()
            except Exception as exc:
                self.append_system_log(
                    f"카메라 녹화 파일 닫기 실패: {exc}",
                    "CAMERA",
                    dedupe_seconds=0,
                )

        if recording_path:
            self.append_system_log(
                f"카메라 녹화 종료: {recording_path} ({frame_count} frames)",
                "CAMERA",
                dedupe_seconds=0,
            )
            if notify:
                QMessageBox.information(
                    self,
                    "Recording Saved",
                    f"카메라 녹화를 저장했습니다.\n{recording_path}",
                )
        if hasattr(self, "btn_camera_record"):
            self.update_camera_button_state()

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
                self.append_system_log(
                    f"UVC 카메라를 열지 못했습니다: {exc}", "CAMERA"
                )
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
        self.stop_camera_recording(notify=False)
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
            self.append_system_log(
                "기준 형상 캡처 실패: 링이 검출되지 않았습니다", "CAMERA"
            )
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
        try:
            self.write_camera_recording_frame(display_frame)
        except Exception as exc:
            failed_path = self.camera_recording_path
            self.stop_camera_recording(notify=False)
            self.append_system_log(
                f"카메라 녹화 실패: {exc}",
                "CAMERA",
                dedupe_seconds=0,
            )
            QMessageBox.critical(
                self,
                "Camera Recording Error",
                f"카메라 녹화를 계속할 수 없습니다.\n{exc}\n{failed_path or ''}",
            )
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
                f"Camera: running, but the {target_description} was not detected",
                log_event=False,
            )
        elif self.camera_baseline is not None:
            self.set_camera_status_text(
                f"Camera: running, {target_description} detected, baseline active",
                log_event=False,
            )
        else:
            self.set_camera_status_text(
                f"Camera: running, {target_description} detected",
                log_event=False,
            )

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

    @staticmethod
    def draw_camera_overlay_text(
        frame,
        lines,
        color,
        font_scale=0.65,
        thickness=2,
    ):
        if not lines:
            return

        frame_height, frame_width = frame.shape[:2]
        margin_x = max(12, int(round(frame_width * 0.025)))
        safe_top = max(20, int(round(frame_height * 0.06)))
        padding_x = max(7, int(round(frame_width * 0.012)))
        padding_y = max(6, int(round(frame_height * 0.012)))
        line_gap = max(6, int(round(frame_height * 0.012)))
        available_text_width = max(
            1,
            frame_width - (2 * margin_x) - (2 * padding_x),
        )

        def measure_text(scale):
            return [
                cv2.getTextSize(
                    line,
                    cv2.FONT_HERSHEY_SIMPLEX,
                    scale,
                    thickness,
                )
                for line in lines
            ]

        text_metrics = measure_text(font_scale)
        widest_text = max(size[0][0] for size in text_metrics)
        if widest_text > available_text_width:
            font_scale *= available_text_width / widest_text
            font_scale = max(0.35, font_scale)
            text_metrics = measure_text(font_scale)

        text_width = max(size[0][0] for size in text_metrics)
        line_heights = [size[0][1] + size[1] for size in text_metrics]
        text_block_height = sum(line_heights) + line_gap * (len(lines) - 1)
        box_left = margin_x
        box_top = safe_top
        box_right = min(
            frame_width - margin_x,
            box_left + text_width + (2 * padding_x),
        )
        box_bottom = min(
            frame_height - 1,
            box_top + text_block_height + (2 * padding_y),
        )

        shade = frame.copy()
        cv2.rectangle(
            shade,
            (box_left, box_top),
            (box_right, box_bottom),
            (0, 0, 0),
            -1,
        )
        cv2.addWeighted(shade, 0.58, frame, 0.42, 0, frame)

        baseline_y = box_top + padding_y + text_metrics[0][0][1]
        for index, line in enumerate(lines):
            cv2.putText(
                frame,
                line,
                (box_left + padding_x, baseline_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                color,
                thickness,
                cv2.LINE_AA,
            )
            if index + 1 < len(lines):
                baseline_y += line_heights[index] + line_gap

    def draw_ring_measurement_overlay(self, frame, measurement):
        if measurement is None:
            target_description = self.get_camera_ring_target_description().capitalize()
            self.draw_camera_overlay_text(
                frame,
                [f"{target_description} not detected"],
                (0, 0, 255),
                font_scale=0.9,
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

        self.draw_camera_overlay_text(
            frame,
            overlay_lines,
            (36, 255, 12),
            font_scale=0.65,
        )

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
        self.lbl_ring_metrics.clear()
        self.lbl_ring_metrics.setVisible(False)

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

    def is_position_monitor_enabled(self):
        return self.chk_position_monitor.isChecked()

    def is_position_pcie_enabled(self):
        # Historical name kept for callers; both PCIe and direct USB now
        # support position feedback and motion commands.
        return self.is_position_monitor_enabled()

    def is_position_control_armed(self):
        return (
            self.is_position_pcie_enabled()
            and self.chk_mr_motion_arm.isChecked()
            and not self.position_controller_close_failed
        )

    def get_source_data_unit(self):
        return self.fc400_device_unit_combo.currentText()

    def update_hardware_readiness_status(self):
        if not hasattr(self, "lbl_status") or self.is_test_running:
            return

        ready = False
        if not NIDAQMX_AVAILABLE:
            status_text = "Status: NOT READY (NI-DAQmx unavailable)"
        elif not self.fc400_device_ready:
            status_text = (
                f"Status: NOT READY (FC400/USB-6002: "
                f"{self.fc400_readiness_detail})"
            )
        elif self.is_position_monitor_enabled():
            if self.position_monitor is None:
                status_text = "Status: NOT READY (MR-MC240N not connected)"
            elif not self.position_axis_status_checked:
                status_text = "Status: NOT READY (MR axis status not checked)"
            elif not self.position_axis_ready:
                status_text = (
                    f"Status: NOT READY (MR-MC240N: "
                    f"{self.position_readiness_detail})"
                )
            elif not self.is_position_control_armed():
                status_text = "Status: NOT READY (Motion commands not armed)"
            else:
                ready = True
                status_text = "Status: READY (FC400 + MR-MC240N)"
        else:
            status_text = "Status: NOT READY (MR-MC240N disabled)"

        self.lbl_status.setText(status_text)
        self.lbl_status.setStyleSheet(
            f"color: {'#2E7D32' if ready else '#C62828'}; font-weight: bold;"
        )
        if hasattr(self, "btn_start"):
            self.btn_start.setEnabled(ready)

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

    def on_position_monitor_toggled(self, enabled):
        self.position_axis_status_checked = False
        self.position_axis_ready = False
        self.position_readiness_detail = (
            "not connected" if enabled else "position board disabled"
        )
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
        self.chk_mr_auto_start.setEnabled(
            enabled and not self.is_position_usb_mode()
        )

        if enabled:
            if os.name != "nt":
                self.set_mr_status_text(f"MR-MC240N: {MR_MC240N_WINDOWS_ONLY_MESSAGE}")
            elif self.is_position_usb_mode():
                usb_info = detect_mr_mc240n_usb_controller()
                if usb_info["connected"]:
                    driver_text = (
                        f" ({usb_info['driver']})" if usb_info["driver"] else ""
                    )
                    self.set_mr_status_text(
                        "MR-MC240N: direct USB controller detected"
                        f"{driver_text}; press Connect USB Controller"
                    )
                else:
                    self.set_mr_status_text(
                        "MR-MC240N: USB controller not detected"
                    )
            else:
                self.set_mr_status_text(
                    "MR-MC240N: PCIe API enabled; connect the installed board"
                )
        else:
            if self.chk_mr_motion_arm.isChecked():
                self.chk_mr_motion_arm.setChecked(False)
            if not self.close_position_monitor():
                self.chk_position_monitor.blockSignals(True)
                self.chk_position_monitor.setChecked(True)
                self.chk_position_monitor.blockSignals(False)
                for widget in configuration_widgets:
                    widget.setEnabled(True)
                self.chk_mr_auto_start.setEnabled(
                    not self.is_position_usb_mode()
                )
                self.update_position_control_state()
                self.update_hardware_readiness_status()
                return
            if os.name != "nt":
                self.set_mr_status_text(f"MR-MC240N: {MR_MC240N_WINDOWS_ONLY_MESSAGE}")
            else:
                self.set_mr_status_text("MR-MC240N: disabled")
        self.update_position_control_state()
        if hasattr(self, "btn_start") and not self.is_test_running:
            self.update_start_button_idle_state()
            self.update_hardware_readiness_status()

    def is_position_usb_mode(self):
        return self.mr_connection_combo.currentText() == MR_CONNECTION_USB_MAINTENANCE

    def on_position_connection_changed(self, connection):
        previous_connection = None
        if isinstance(self.position_monitor, MrMc240nUsbController):
            previous_connection = MR_CONNECTION_USB_MAINTENANCE
        elif isinstance(self.position_monitor, MrMc240nPositionController):
            previous_connection = MR_CONNECTION_PCIE_API
        if not self.close_position_monitor():
            if previous_connection:
                self.mr_connection_combo.blockSignals(True)
                self.mr_connection_combo.setCurrentText(previous_connection)
                self.mr_connection_combo.blockSignals(False)
            self.update_position_control_state()
            return
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
            self.update_hardware_readiness_status()

    def on_position_axis_changed(self, _axis_text):
        if not hasattr(self, "chk_mr_motion_arm"):
            return
        if self.chk_mr_motion_arm.isChecked():
            self.chk_mr_motion_arm.setChecked(False)
        previous_axis = (
            self.position_monitor.axis_number
            if self.position_monitor is not None
            else None
        )
        if not self.close_position_monitor():
            if previous_axis is not None:
                self.in_mr_axis_no.blockSignals(True)
                self.in_mr_axis_no.setCurrentText(str(previous_axis))
                self.in_mr_axis_no.blockSignals(False)
            self.update_position_control_state()
            return
        if self.is_position_monitor_enabled():
            self.set_mr_status_text(
                f"MR-MC240N: axis {self.in_mr_axis_no.currentText()} selected; reconnect"
            )
        self.update_position_control_state()
        self.update_hardware_readiness_status()

    def on_position_connection_settings_changed(self, *_args):
        """Invalidate a live controller when its open-time settings change."""
        if not hasattr(self, "position_monitor") or self.position_monitor is None:
            return
        if self.chk_mr_motion_arm.isChecked():
            self.chk_mr_motion_arm.setChecked(False)
        if not self.close_position_monitor():
            monitor = self.position_monitor
            if monitor is not None:
                self.in_mr_dll_path.blockSignals(True)
                self.in_mr_board_id.blockSignals(True)
                self.chk_mr_auto_start.blockSignals(True)
                self.in_mr_dll_path.setText(monitor.dll_path)
                self.in_mr_board_id.setText(str(monitor.board_id))
                self.chk_mr_auto_start.setChecked(
                    bool(
                        isinstance(monitor, MrMc240nPositionController)
                        and monitor.auto_start_system
                    )
                )
                self.in_mr_dll_path.blockSignals(False)
                self.in_mr_board_id.blockSignals(False)
                self.chk_mr_auto_start.blockSignals(False)
            self.update_position_control_state()
            return
        if self.is_position_monitor_enabled():
            self.set_mr_status_text(
                "MR-MC240N: connection settings changed; reconnect"
            )
        self.update_position_control_state()
        self.update_hardware_readiness_status()

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
            self.set_mr_status_text(
                "MR-MC240N: USB direct control is available on Windows only"
            )
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
            self.set_mr_status_text("MR-MC240N: motion commands armed")
        else:
            stop_confirmed = True
            if self.live_motion_cycle_active:
                stop_confirmed = self.stop_position_motion(True)
            elif self.position_jog_command_active:
                stop_confirmed = self.stop_position_motion(True)
            if stop_confirmed:
                self.position_jog_command_active = False
                self.position_jog_direction = None
            if self.is_position_monitor_enabled() and stop_confirmed:
                self.set_mr_status_text("MR-MC240N: motion commands disarmed")
        self.update_position_control_state()
        self.update_hardware_readiness_status()

    def update_position_control_state(self):
        enabled = self.is_position_monitor_enabled() and os.name == "nt"
        control_enabled = enabled
        controller_motion_may_be_active = bool(
            self.position_monitor is not None
            and self.position_monitor._motion_command_may_be_active
        )
        motion_may_be_active = (
            self.position_motion_may_be_active
            or controller_motion_may_be_active
        )
        configuration_locked = (
            self.is_test_running
            or self.live_motion_cycle_active
            or motion_may_be_active
            or self.position_controller_close_failed
        )
        if hasattr(self, "btn_open_settings"):
            self.btn_open_settings.setEnabled(not configuration_locked)
        armed = (
            control_enabled
            and self.chk_mr_motion_arm.isChecked()
            and not self.position_controller_close_failed
        )
        self.chk_position_monitor.setEnabled(
            os.name == "nt" and not configuration_locked
        )
        configuration_widgets = [
            self.mr_connection_combo,
            self.in_mr_dll_path,
            self.in_mr_board_id,
            self.in_mr_axis_no,
            self.in_mr_counts_per_mm,
            self.in_mr_motion_speed,
            self.in_mr_acceleration_ms,
            self.in_mr_deceleration_ms,
            self.in_mr_relative_move_mm,
        ]
        for widget in configuration_widgets:
            widget.setEnabled(enabled and not configuration_locked)
        motion_locked = (
            self.is_test_running
            or self.live_motion_cycle_active
            or motion_may_be_active
        )
        self.btn_mr_connect.setEnabled(
            enabled and not motion_locked
        )
        self.btn_mr_system_start.setEnabled(
            enabled
            and self.is_position_usb_mode()
            and not configuration_locked
        )
        self.btn_mr_apply_six_axis.setEnabled(
            enabled
            and self.is_position_usb_mode()
            and not armed
            and not configuration_locked
        )
        self.chk_mr_auto_start.setEnabled(
            enabled
            and not self.is_position_usb_mode()
            and not configuration_locked
        )
        self.chk_mr_motion_arm.setEnabled(
            control_enabled and not self.position_controller_close_failed
        )
        motion_buttons = [
            self.btn_mr_servo_on,
            self.btn_mr_servo_off,
            self.btn_mr_home,
            self.btn_mr_move_relative,
            self.btn_mr_jog_minus,
            self.btn_mr_jog_plus,
        ]
        for button in motion_buttons:
            button.setEnabled(
                armed
                and not self.is_test_running
                and not self.live_motion_cycle_active
                and not motion_may_be_active
                and not self.position_controller_close_failed
            )
        self.btn_mr_stop.setEnabled(control_enabled)
        self.btn_mr_rapid_stop.setEnabled(control_enabled)
        self.btn_mr_refresh_status.setEnabled(control_enabled)

    def test_position_board_connection(self):
        """Open the configured board now so detection errors are immediately visible."""
        if not self.close_position_monitor():
            QMessageBox.critical(
                self,
                "MR-MC240N Close Error",
                "기존 컨트롤러의 Rapid Stop/Close를 확인하지 못해 새 연결을 "
                "시작하지 않았습니다. 외부 비상정지 상태를 확인한 뒤 다시 "
                "Stop/Close를 시도하세요.",
            )
            return
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
                self.set_mr_status_text(
                    f"MR-MC240N: USB connected, identity {controller.identity}, "
                    f"system 0x{controller.system_status_code:04X} "
                    f"({controller.system_status_text(controller.system_status_code)}), "
                    f"{axis_text}"
                )
            else:
                dll_detail = controller.loaded_library_path or "unknown DLL"
                self.set_mr_status_text(
                    f"MR-MC240N: PCIe board {controller.board_id} opened with "
                    f"{dll_detail}; checking axis {controller.axis_number}"
                )
            self.refresh_position_axis_status()
        except Exception as exc:
            message = str(exc)
            self.position_axis_status_checked = False
            self.position_axis_ready = False
            self.position_readiness_detail = f"connection failed: {message}"
            self.update_hardware_readiness_status()
            self.set_mr_status_text(f"MR-MC240N: connection failed - {message}")
            if self.is_position_usb_mode():
                recovery_text = (
                    "PB Test나 MR Configurator2가 USB를 사용 중이면 종료한 뒤 "
                    "보드 전원·USB 드라이버를 확인해주세요."
                )
            else:
                recovery_text = (
                    "터미널에서 `python mr_mc240n_pcie_check.py`를 실행하세요. "
                    "DLL 하나만 교체하지 말고 Mitsubishi Position Board Utility2의 "
                    "API와 드라이버를 같은 배포판으로 설치해야 합니다. "
                    "복구 전에는 USB controller (direct) 모드를 사용할 수 있습니다."
                )
            QMessageBox.critical(
                self,
                "MR-MC240N Connection Error",
                "보드를 열지 못했습니다.\n\n"
                f"{message}\n\n"
                f"{recovery_text}",
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
            self.set_mr_status_text(
                f"MR-MC240N: USB System Start sent, status 0x{status_code:04X}"
            )
        except Exception as exc:
            self.handle_position_command_error("USB System Start", exc)

    def apply_position_six_axis_preset(self):
        if not self.is_position_usb_mode():
            return
        if self.chk_mr_motion_arm.isChecked():
            self.set_mr_status_text(
                "MR-MC240N: six-axis preset blocked; disarm motion commands first"
            )
            QMessageBox.warning(
                self,
                "Disarm Motion First",
                "6축 파라미터 적용 전에 모션 명령 Arm을 해제해주세요.",
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
            self.set_mr_status_text(
                "MR-MC240N: HG-KR13 ×6 / BTK1404 preset applied, "
                f"system 0x{int(response['system_status']):04X}"
            )
            self.refresh_position_axis_status()
        except Exception as exc:
            self.handle_position_command_error("Six-axis preset", exc)

    def on_source_configuration_changed(self, *_args):
        if self.is_test_running:
            self.stop_test(completed=False)

        self.data_unit = self.get_source_data_unit()
        self.raw_data = [0.0] * 6
        self.sensor_zeros = [0.0] * 6
        self.update_table_headers()
        self.update_table()
        self.update_chart(reset_scale=True)
        self.update_hardware_readiness_status()

    def update_start_button_idle_state(self):
        self.btn_start.setText("FC400 + MR-MC240N 시험 시작")
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")
        self.update_hardware_readiness_status()

    def refresh_ni_devices(self):
        if not NIDAQMX_AVAILABLE:
            self.fc400_device_ready = False
            self.fc400_readiness_detail = "NI-DAQmx unavailable"
            self.set_fc400_status_text(
                f"USB-6002: nidaqmx import failed - {NIDAQMX_IMPORT_ERROR}"
            )
            self.update_hardware_readiness_status()
            return

        try:
            device_summaries = []
            all_ai_channels = []
            first_ai_channel = None
            for device in System.local().devices:
                try:
                    ai_channels = device.ai_physical_chans.channel_names
                except Exception:
                    ai_channels = []

                if not ai_channels:
                    continue

                all_ai_channels.extend(ai_channels)
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

            configured_channel = channel_widget.text().strip()
            self.fc400_device_ready = configured_channel in all_ai_channels
            if self.fc400_device_ready:
                self.fc400_readiness_detail = f"{configured_channel} detected"
                status_text = ", ".join(device_summaries)
                self.set_fc400_status_text("USB-6002: " + status_text)
            elif device_summaries:
                self.fc400_readiness_detail = (
                    f"configured channel {configured_channel or '(empty)'} not found"
                )
                self.set_fc400_status_text(
                    "USB-6002: "
                    + ", ".join(device_summaries)
                    + f"; channel {configured_channel or '(empty)'} not found"
                )
            else:
                self.fc400_readiness_detail = "no NI analog-input device found"
                self.set_fc400_status_text(
                    "USB-6002: no NI analog-input device found"
                )
        except Exception as exc:
            self.fc400_device_ready = False
            self.fc400_readiness_detail = "device scan failed"
            self.set_fc400_status_text(
                f"USB-6002: device scan failed - {exc}"
            )
        self.update_hardware_readiness_status()

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

    def read_ni_daq_samples(self):
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
            samples = np.asarray(value, dtype=float).reshape(-1)
            if samples.size == 0:
                raise RuntimeError("USB-6002 버퍼에 읽을 샘플이 없습니다.")
            return samples.tolist()
        finally:
            if opened_here and not self.is_test_running:
                self.close_ni_daq_task()

    def read_ni_daq_value(self):
        return float(self.read_ni_daq_samples()[-1])

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
        except Exception as exc:
            task.close()
            self.fc400_device_ready = False
            self.fc400_readiness_detail = "DAQ task open failed"
            self.set_fc400_status_text(
                f"FC400 / USB-6002 task open failed - {exc}"
            )
            self.update_hardware_readiness_status()
            raise

        self.ni_daq_task = task
        self.fc400_device_ready = True
        self.fc400_readiness_detail = (
            f"{config['physical_channel']} task ready"
        )
        self.data_unit = config["device_unit"]
        self.update_table_headers()
        self.set_fc400_status_text(
            f"FC400 / USB-6002: {config['physical_channel']} "
            f"({config['terminal_mode']}) @ {task.timing.samp_clk_rate:.0f} S/s"
        )
        self.update_hardware_readiness_status()

    def read_fc400_measurement(self):
        opened_here = False
        if self.ni_daq_task is None:
            self.open_fc400_usb_task()
            opened_here = True

        try:
            voltages = self.read_ni_daq_samples()
            config = self.get_fc400_config()
            voltage_span = config["full_scale_voltage"] - config["zero_voltage"]
            load_samples = [
                (
                    (voltage - config["zero_voltage"])
                    / voltage_span
                    * config["full_scale_load"]
                )
                for voltage in voltages
            ]
            load_value = load_samples[-1]
            peak_load_value = max(load_samples)
            peak_voltage = voltages[load_samples.index(peak_load_value)]
            return {
                "value": load_value,
                "samples": load_samples,
                "peak_value": peak_load_value,
                "stable": None,
                "voltage": voltages[-1],
                "peak_voltage": peak_voltage,
            }
        finally:
            if opened_here and not self.is_test_running:
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
        if not -AXIS_TRAVEL_MAX_MM <= distance_mm <= AXIS_TRAVEL_MAX_MM:
            raise ValueError(
                f"Relative Move는 {-AXIS_TRAVEL_MAX_MM:g}~"
                f"{AXIS_TRAVEL_MAX_MM:g} mm 범위여야 합니다."
            )
        if abs(distance_mm) < 1e-12:
            raise ValueError("Relative Move는 0이 아니어야 합니다.")

        return {
            "speed": speed,
            "acceleration_ms": acceleration_ms,
            "deceleration_ms": deceleration_ms,
            "distance_mm": distance_mm,
        }

    def open_position_monitor(self):
        if not self.is_position_monitor_enabled():
            return

        config = self.get_position_monitor_config()
        usb_mode = self.is_position_usb_mode()
        if self.position_monitor is not None:
            expected_type = (
                MrMc240nUsbController
                if usb_mode
                else MrMc240nPositionController
            )
            configured_dll = (
                os.path.normcase(
                    os.path.abspath(os.path.expandvars(config["dll_path"]))
                )
                if config["dll_path"]
                else ""
            )
            active_dll = (
                os.path.normcase(
                    os.path.abspath(
                        os.path.expandvars(self.position_monitor.dll_path)
                    )
                )
                if self.position_monitor.dll_path
                else ""
            )
            settings_match = (
                isinstance(self.position_monitor, expected_type)
                and self.position_monitor.board_id == config["board_id"]
                and self.position_monitor.axis_number == config["axis_number"]
                and active_dll == configured_dll
                and (
                    usb_mode
                    or self.position_monitor.auto_start_system
                    == config["auto_start_system"]
                )
            )
            if settings_match:
                return
            if not self.close_position_monitor():
                raise RuntimeError(
                    "The existing MR-MC240N controller could not be stopped/"
                    "closed, so the new connection settings were not applied."
                )

        if usb_mode:
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
        except Exception as open_error:
            cleanup_error = None
            try:
                monitor.close()
            except Exception as close_error:
                cleanup_error = close_error
            if getattr(monitor, "_is_open", False):
                self.position_monitor = monitor
                self.position_controller_close_failed = True
                self.position_axis_status_checked = False
                self.position_axis_ready = False
                self.position_readiness_detail = (
                    "open failed and board-close could not be confirmed"
                )
                detail = (
                    f"; retry close failed: {cleanup_error}"
                    if cleanup_error is not None
                    else ""
                )
                self.update_position_control_state()
                self.update_hardware_readiness_status()
                raise RuntimeError(
                    f"{open_error}{detail}. The controller reference was retained "
                    "so Rapid Stop/Close can be retried."
                ) from open_error
            raise

        self.position_monitor = monitor
        self.position_axis_status_checked = False
        self.position_axis_ready = False
        self.position_readiness_detail = "axis status not checked"
        connection_detail = "USB bridge"
        if isinstance(monitor, MrMc240nPositionController):
            connection_detail = monitor.loaded_library_path or "PCIe API"
        self.set_mr_status_text(
            f"MR-MC240N: board {config['board_id']} axis "
            f"{config['axis_number']} opened via {connection_detail}"
        )
        self.update_hardware_readiness_status()

    def close_position_monitor(self):
        if self.position_monitor is None:
            self.stop_position_motion_status_monitor()
            return True
        monitor = self.position_monitor
        motion_may_be_active = (
            self.position_jog_command_active
            or monitor._jog_active
            or monitor._motion_command_may_be_active
            or self.live_motion_cycle_active
            or self.position_motion_may_be_active
        )
        if motion_may_be_active:
            try:
                if self.load_limit_tripped:
                    monitor.stop_all_axes(
                        axis_numbers=range(1, 7),
                        rapid=True,
                        timeout_ms=3000,
                    )
                else:
                    monitor.stop(rapid=True, timeout_ms=3000)
                self.position_jog_command_active = False
                self.position_jog_direction = None
                self.position_motion_may_be_active = False
            except Exception as exc:
                self.position_axis_status_checked = False
                self.position_axis_ready = False
                self.position_readiness_detail = (
                    "safety stop failed; controller retained"
                )
                self.set_mr_status_text(
                    "MR-MC240N: safety stop failed; controller kept open - "
                    f"{exc}"
                )
                self.append_system_log(
                    f"Safety stop failed; controller retained: {exc}",
                    "MR-MC240N",
                )
                self.position_controller_close_failed = True
                self.update_position_control_state()
                self.update_hardware_readiness_status()
                return False
        try:
            monitor.close()
        except Exception as exc:
            self.position_axis_status_checked = False
            self.position_axis_ready = False
            self.position_readiness_detail = (
                "board close failed; controller retained"
            )
            self.set_mr_status_text(
                f"MR-MC240N: Close failed; controller retained - {exc}"
            )
            self.append_system_log(
                f"Board close failed; controller retained: {exc}",
                "MR-MC240N",
            )
            self.position_controller_close_failed = True
            self.update_position_control_state()
            self.update_hardware_readiness_status()
            return False

        self.position_jog_command_active = False
        self.position_jog_direction = None
        self.position_motion_may_be_active = False
        self.position_controller_close_failed = False
        self.position_monitor = None
        self.stop_position_motion_status_monitor()
        self.position_axis_status_checked = False
        self.position_axis_ready = False
        self.position_readiness_detail = (
            "not connected"
            if self.is_position_monitor_enabled()
            else "position board disabled"
        )
        self.update_position_control_state()
        self.update_hardware_readiness_status()
        return True

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
            if opened_here and not self.is_test_running:
                self.close_position_monitor()

    def get_position_controller(self, require_armed=True):
        if not self.is_position_monitor_enabled():
            raise RuntimeError("MR-MC240N position board를 먼저 활성화해주세요.")
        if require_armed and self.position_controller_close_failed:
            raise RuntimeError(
                "이전 MR-MC240N Stop/Close가 확인되지 않아 새 모션 명령을 "
                "차단했습니다. 외부 비상정지를 확인하고 Rapid Stop/Connect로 "
                "정리 상태를 다시 확인하세요."
            )
        if require_armed and not self.is_position_control_armed():
            raise RuntimeError("모션 명령 Arm을 먼저 활성화해주세요.")
        # Always pass through the configuration fingerprint check. This
        # prevents a retained old controller from receiving commands after a
        # Board ID/DLL/axis setting changed.
        self.open_position_monitor()
        if self.position_monitor is None:
            raise RuntimeError("MR-MC240N position board를 열지 못했습니다.")
        return self.position_monitor

    def handle_position_command_error(self, action, exc):
        message = f"{action} 실패: {exc}"
        self.set_mr_status_text(f"MR-MC240N: {message}")
        QMessageBox.critical(self, "MR-MC240N Control Error", message)

    def begin_position_motion_status_monitor(self):
        if (
            self.position_monitor is None
            or not self.position_monitor._motion_command_may_be_active
        ):
            self.stop_position_motion_status_monitor()
            return
        self.position_motion_status_deadline = time.monotonic() + 300.0
        self.position_motion_status_failures = 0
        self.position_motion_status_timer.start()

    def stop_position_motion_status_monitor(self):
        self.position_motion_status_timer.stop()
        self.position_motion_status_deadline = 0.0
        self.position_motion_status_failures = 0

    def poll_position_motion_status(self):
        controller = self.position_monitor
        if controller is None:
            self.stop_position_motion_status_monitor()
            return
        if not controller._motion_command_may_be_active:
            self.position_motion_may_be_active = False
            self.stop_position_motion_status_monitor()
            self.update_position_control_state()
            return
        if time.monotonic() >= self.position_motion_status_deadline:
            self.stop_position_motion_status_monitor()
            self.set_mr_status_text(
                "MR-MC240N: motion status polling timed out; motion remains "
                "locked until Stop/Rapid Stop is confirmed"
            )
            self.update_position_control_state()
            return
        try:
            axis_status = controller.read_axis_status()
        except Exception as exc:
            self.position_motion_status_failures += 1
            if self.position_motion_status_failures >= 3:
                self.stop_position_motion_status_monitor()
                self.set_mr_status_text(
                    "MR-MC240N: motion status read failed three times; motion "
                    f"remains locked - {exc}"
                )
                self.append_system_log(
                    f"Motion status monitor stopped after read failures: {exc}",
                    "MR-MC240N",
                )
            return

        self.position_motion_status_failures = 0
        if self.position_jog_command_active:
            try:
                counts_per_mm = self.get_position_monitor_config()[
                    "counts_per_mm"
                ]
                position_mm = (
                    int(axis_status["position"]) / counts_per_mm
                ) - self.position_zero_offset_mm
            except Exception as exc:
                self.set_mr_status_text(
                    f"MR-MC240N: JOG soft-limit position check failed - {exc}"
                )
                position_mm = None
            reached_minimum = (
                position_mm is not None
                and self.position_jog_direction
                == MrMc240nPositionController.SSC_DIR_MINUS
                and position_mm <= AXIS_TRAVEL_MIN_MM
            )
            reached_maximum = (
                position_mm is not None
                and self.position_jog_direction
                == MrMc240nPositionController.SSC_DIR_PLUS
                and position_mm >= AXIS_TRAVEL_MAX_MM
            )
            if reached_minimum or reached_maximum:
                limit_text = (
                    f"{AXIS_TRAVEL_MIN_MM:g} mm"
                    if reached_minimum
                    else f"{AXIS_TRAVEL_MAX_MM:g} mm"
                )
                self.stop_all_position_axes(
                    reason=f"JOG soft limit {limit_text}"
                )
                QMessageBox.critical(
                    self,
                    "JOG Soft Limit",
                    f"축 위치가 소프트 리미트 {limit_text}에 도달해 "
                    "6축 전체를 긴급정지했습니다.",
                )
                return
        if axis_status["servo_alarm"] or axis_status["operation_alarm"]:
            self.stop_position_motion_status_monitor()
            self.set_mr_status_text(
                "MR-MC240N: axis alarm while monitoring motion; use Rapid Stop "
                "and inspect axis status"
            )
            self.update_position_control_state()
            return
        if not controller._motion_command_may_be_active:
            self.position_motion_may_be_active = False
            self.stop_position_motion_status_monitor()
            self.update_position_control_state()
            self.set_mr_status_text(
                f"MR-MC240N: axis {controller.axis_number} motion completion confirmed"
            )

    def set_position_servo(self, enabled):
        action = "Servo ON" if enabled else "Servo OFF"
        try:
            controller = self.get_position_controller(require_armed=True)
            controller.set_servo_on(enabled)
            self.set_mr_status_text(
                f"MR-MC240N: {action} command sent to axis {controller.axis_number}"
            )
            self.refresh_position_axis_status()
            if enabled:
                QTimer.singleShot(250, self.refresh_position_axis_status)
        except Exception as exc:
            self.handle_position_command_error(action, exc)

    def start_position_home(self):
        controller = None
        try:
            controller = self.get_position_controller(require_armed=True)
            controller.start_home_return()
            self.position_motion_may_be_active = (
                controller._motion_command_may_be_active
            )
            self.begin_position_motion_status_monitor()
            self.update_position_control_state()
            self.set_mr_status_text(
                f"MR-MC240N: home return started on axis {controller.axis_number}"
            )
        except Exception as exc:
            if controller is not None:
                self.position_motion_may_be_active = (
                    controller._motion_command_may_be_active
                )
                self.begin_position_motion_status_monitor()
                self.update_position_control_state()
            self.handle_position_command_error("Home return", exc)

    def start_position_relative_move(self):
        controller = None
        try:
            controller = self.get_position_controller(require_armed=True)
            board_config = self.get_position_monitor_config()
            motion_config = self.get_position_motion_config()
            current_position_mm, _ = self.read_position_feedback()
            if current_position_mm is None:
                raise RuntimeError("현재 축 위치를 읽지 못했습니다.")
            target_position_mm = (
                current_position_mm + motion_config["distance_mm"]
            )
            if not (
                AXIS_TRAVEL_MIN_MM
                <= target_position_mm
                <= AXIS_TRAVEL_MAX_MM
            ):
                raise ValueError(
                    f"요청 목표 위치 {target_position_mm:.3f} mm가 "
                    f"소프트 리미트 {AXIS_TRAVEL_MIN_MM:g}~"
                    f"{AXIS_TRAVEL_MAX_MM:g} mm를 벗어납니다."
                )
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
            self.position_motion_may_be_active = (
                controller._motion_command_may_be_active
            )
            self.begin_position_motion_status_monitor()
            self.update_position_control_state()
            self.set_mr_status_text(
                "MR-MC240N: relative move started "
                f"({motion_config['distance_mm']:.4f} mm / {distance_counts} command units)"
            )
        except Exception as exc:
            if controller is not None:
                self.position_motion_may_be_active = (
                    controller._motion_command_may_be_active
                )
                self.begin_position_motion_status_monitor()
                self.update_position_control_state()
            self.handle_position_command_error("Relative move", exc)

    def start_position_jog(self, direction):
        direction_text = "+" if direction == MrMc240nPositionController.SSC_DIR_PLUS else "-"
        controller = None
        try:
            controller = self.get_position_controller(require_armed=True)
            motion_config = self.get_position_motion_config()
            if hasattr(controller, "read_feedback_position_counts"):
                current_position_mm, _ = self.read_position_feedback()
                if current_position_mm is None:
                    raise RuntimeError("현재 축 위치를 읽지 못했습니다.")
                if (
                    direction == MrMc240nPositionController.SSC_DIR_MINUS
                    and current_position_mm <= AXIS_TRAVEL_MIN_MM
                ):
                    raise ValueError(
                        f"현재 위치가 최소 소프트 리미트 "
                        f"{AXIS_TRAVEL_MIN_MM:g} mm입니다."
                    )
                if (
                    direction == MrMc240nPositionController.SSC_DIR_PLUS
                    and current_position_mm >= AXIS_TRAVEL_MAX_MM
                ):
                    raise ValueError(
                        f"현재 위치가 최대 소프트 리미트 "
                        f"{AXIS_TRAVEL_MAX_MM:g} mm입니다."
                    )
            controller.start_jog(
                direction,
                motion_config["speed"],
                motion_config["acceleration_ms"],
                motion_config["deceleration_ms"],
            )
            self.position_jog_command_active = True
            self.position_jog_direction = direction
            self.position_motion_may_be_active = (
                controller._motion_command_may_be_active
            )
            self.begin_position_motion_status_monitor()
            self.update_position_control_state()
            self.set_mr_status_text(f"MR-MC240N: JOG {direction_text} running")
        except Exception as exc:
            if controller is not None:
                automatic_stop_error = None
                if controller._motion_command_may_be_active:
                    try:
                        controller.stop_jog()
                    except Exception as stop_exc:
                        automatic_stop_error = stop_exc
                        try:
                            controller.stop(rapid=True)
                            automatic_stop_error = None
                        except Exception as rapid_stop_exc:
                            automatic_stop_error = RuntimeError(
                                f"JOG stop failed ({stop_exc}); Rapid Stop also "
                                f"failed ({rapid_stop_exc})"
                            )
                self.position_motion_may_be_active = (
                    controller._motion_command_may_be_active
                )
                self.position_jog_command_active = bool(
                    controller._jog_active
                    or controller._motion_command_may_be_active
                )
                self.update_position_control_state()
                if automatic_stop_error is not None:
                    exc = RuntimeError(f"{exc}; {automatic_stop_error}")
            else:
                self.position_jog_command_active = False
                self.position_jog_direction = None
            self.handle_position_command_error(f"JOG {direction_text}", exc)

    def stop_position_jog(self):
        controller_motion_may_be_active = bool(
            self.position_monitor is not None
            and self.position_monitor._motion_command_may_be_active
        )
        if not (
            self.position_jog_command_active
            or self.position_motion_may_be_active
            or controller_motion_may_be_active
        ):
            return
        try:
            controller = self.get_position_controller(require_armed=False)
            controller.stop_jog()
            self.position_jog_command_active = False
            self.position_jog_direction = None
            self.position_motion_may_be_active = False
            self.stop_position_motion_status_monitor()
            self.update_position_control_state()
            self.set_mr_status_text("MR-MC240N: JOG stopped")
        except Exception as exc:
            self.handle_position_command_error("JOG stop", exc)

    def stop_position_motion(self, rapid):
        action = "Rapid stop" if rapid else "Stop"
        automatic_test_active = self.live_motion_cycle_active
        motion_was_uncertain = bool(
            automatic_test_active
            or self.position_jog_command_active
            or self.position_motion_may_be_active
            or (
                self.position_monitor is not None
                and self.position_monitor._motion_command_may_be_active
            )
        )
        try:
            controller = self.get_position_controller(require_armed=False)
            controller.stop(rapid=rapid)
            self.position_jog_command_active = False
            self.position_jog_direction = None
            self.position_motion_may_be_active = False
            self.stop_position_motion_status_monitor()
            self.update_position_control_state()
            self.set_mr_status_text(f"MR-MC240N: {action} completed")
            if automatic_test_active:
                self.live_motion_cycle_active = False
                self.stop_test(completed=False)
            return True
        except Exception as exc:
            controller_motion_may_be_active = bool(
                self.position_monitor is not None
                and self.position_monitor._motion_command_may_be_active
            )
            self.position_motion_may_be_active = (
                motion_was_uncertain or controller_motion_may_be_active
            )
            self.update_position_control_state()
            self.handle_position_command_error(action, exc)
            return False

    def stop_all_position_axes(
        self,
        _checked=False,
        reason=None,
        report_error=True,
    ):
        automatic_test_active = self.live_motion_cycle_active
        motion_was_uncertain = bool(
            automatic_test_active
            or self.position_jog_command_active
            or self.position_motion_may_be_active
            or (
                self.position_monitor is not None
                and self.position_monitor._motion_command_may_be_active
            )
        )
        try:
            controller = self.get_position_controller(require_armed=False)
            controller.stop_all_axes(
                axis_numbers=range(1, 7),
                rapid=True,
                timeout_ms=3000,
            )
            self.position_jog_command_active = False
            self.position_jog_direction = None
            self.position_motion_may_be_active = False
            self.stop_position_motion_status_monitor()
            if automatic_test_active:
                self.live_motion_cycle_active = False
                self.stop_test(completed=False)
            self.update_position_control_state()
            detail = f" ({reason})" if reason else ""
            self.set_mr_status_text(
                f"MR-MC240N: all-axis Rapid Stop completed{detail}"
            )
            return True
        except Exception as exc:
            controller_motion_may_be_active = bool(
                self.position_monitor is not None
                and self.position_monitor._motion_command_may_be_active
            )
            self.position_motion_may_be_active = (
                motion_was_uncertain or controller_motion_may_be_active
            )
            self.update_position_control_state()
            if report_error:
                self.handle_position_command_error("All-axis Rapid Stop", exc)
            else:
                self.append_system_log(
                    f"All-axis Rapid Stop failed: {exc}",
                    "MR-MC240N",
                    dedupe_seconds=0,
                )
            return False

    def refresh_position_axis_status(self):
        self.position_axis_status_checked = False
        self.position_axis_ready = False
        self.position_readiness_detail = "axis status not checked"
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
                        "border-radius: 4px; padding: 4px; "
                        "font-size: 9px; }"
                    )
                    display_state = {
                        "WAIT SSCNET": "WAIT",
                        "UNCONFIGURED": "미설정",
                        "RUNNING": "RUN",
                        "NOT READY": "NOT READY",
                    }.get(short_state, short_state)
                    label.setText(
                        f"A{axis}\n{display_state}\n{position_mm:.2f} mm"
                    )
                    label.setToolTip(
                        f"Axis {axis}: {short_state}\n"
                        f"{position_mm:.4f} mm ({raw_counts} cmd)"
                    )

                    if axis == selected_axis:
                        if (
                            not axis_status["operating"]
                            and axis_status["operation_complete"]
                            and not controller._motion_command_may_be_active
                        ):
                            self.position_motion_may_be_active = False
                            self.update_position_control_state()
                        self.position_axis_status_checked = True
                        self.position_axis_ready = short_state == "READY"
                        self.position_readiness_detail = {
                            "READY": "axis ready",
                            "RUNNING": "axis is operating",
                            "ALARM": "servo/operation alarm",
                            "WAIT SSCNET": "waiting for SSCNET amplifier response",
                            "UNCONFIGURED": "axis not mounted/configured",
                            "NOT READY": "servo not ready",
                        }.get(short_state, short_state.lower())
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
                    label.setText(f"A{axis}\nREAD ERROR")
                    label.setToolTip(
                        f"Axis {axis} status read failed:\n{axis_exc}"
                    )
                    label.setStyleSheet(
                        "QLabel { background: #FFF3E0; border: 2px solid #EF6C00; "
                        "border-radius: 4px; padding: 4px; font-size: 9px; }"
                    )
                    if axis == selected_axis:
                        self.position_axis_status_checked = False
                        self.position_axis_ready = False
                        self.position_readiness_detail = (
                            f"axis status read failed: {axis_exc}"
                        )
                        selected_summary = (
                            f"MR-MC240N axis {axis}: status read failed - {axis_exc}"
                        )

            if selected_summary:
                self.set_mr_status_text(selected_summary)
            self.update_hardware_readiness_status()
        except Exception as exc:
            self.position_axis_status_checked = False
            self.position_axis_ready = False
            self.position_readiness_detail = f"axis status refresh failed: {exc}"
            self.update_hardware_readiness_status()
            self.handle_position_command_error("Axis status refresh", exc)

    def get_default_stroke_mm(self):
        try:
            return float(self.in_max_len.text())
        except ValueError:
            return 0.0

    def get_live_motion_test_config(self):
        if not self.is_position_monitor_enabled():
            raise RuntimeError(
                "MR-MC240N position board를 활성화해주세요."
            )
        if not self.is_position_control_armed():
            raise RuntimeError(
                "MR-MC240N 자동 왕복 시험을 시작하려면 모션 명령 Arm을 활성화해주세요."
            )

        minimum_mm = float(self.in_min_len.text())
        maximum_mm = float(self.in_max_len.text())
        speed_mm_min = float(self.in_speed.text())
        hold_seconds = float(self.in_hold.text())
        target_load = float(self.in_load.text())
        load_limit = float(self.in_load_limit.text())
        target_strokes = int(self.in_strokes.text())
        acceleration_ms = int(self.in_mr_acceleration_ms.text())
        deceleration_ms = int(self.in_mr_deceleration_ms.text())
        position_config = self.get_position_monitor_config()

        if not AXIS_TRAVEL_MIN_MM <= minimum_mm <= AXIS_TRAVEL_MAX_MM:
            raise ValueError(
                f"Min Length는 {AXIS_TRAVEL_MIN_MM:g}~"
                f"{AXIS_TRAVEL_MAX_MM:g} mm 범위여야 합니다."
            )
        if not AXIS_TRAVEL_MIN_MM <= maximum_mm <= AXIS_TRAVEL_MAX_MM:
            raise ValueError(
                f"Max Length는 {AXIS_TRAVEL_MIN_MM:g}~"
                f"{AXIS_TRAVEL_MAX_MM:g} mm 범위여야 합니다."
            )
        if maximum_mm <= minimum_mm:
            raise ValueError("Max Length는 Min Length보다 커야 합니다.")
        if speed_mm_min < 1:
            raise ValueError("Speed는 1 mm/min 이상이어야 합니다.")
        board_speed = int(round(speed_mm_min))
        if abs(speed_mm_min - board_speed) > 1e-9:
            raise ValueError(
                "MR-MC240N 속도 명령은 정수 단위입니다. Speed를 정수 mm/min으로 입력해주세요."
            )
        max_speed = 12_000 if self.is_position_usb_mode() else 2_147_483_647
        if board_speed > max_speed:
            raise ValueError(f"Speed는 1~{max_speed} mm/min 범위여야 합니다.")
        if not 0 <= acceleration_ms <= 20_000:
            raise ValueError("Acceleration은 0~20000 ms 범위로 입력해주세요.")
        if not 0 <= deceleration_ms <= 20_000:
            raise ValueError("Deceleration은 0~20000 ms 범위로 입력해주세요.")
        if not 0 <= hold_seconds <= TEST_HOLD_MAX_SECONDS:
            raise ValueError(
                f"Hold Time은 0~{TEST_HOLD_MAX_SECONDS:g}초 범위여야 합니다."
            )
        if not 0 <= target_load <= LOAD_INPUT_MAX:
            raise ValueError(
                f"Target Load는 0~{LOAD_INPUT_MAX:g} 범위여야 합니다."
            )
        if not 0 < load_limit <= LOAD_INPUT_MAX:
            raise ValueError(
                f"안전 하중 리미트는 0 초과 {LOAD_INPUT_MAX:g} 이하여야 합니다."
            )
        if load_limit <= target_load:
            raise ValueError(
                "안전 하중 리미트는 목표 하중보다 크게 설정해야 합니다."
            )
        if not 1 <= target_strokes <= TEST_STROKES_MAX:
            raise ValueError(
                f"Operation Strokes는 1~{TEST_STROKES_MAX} 범위여야 합니다."
            )

        fc400_config = self.get_fc400_config()
        fc400_capacity = abs(
            self.convert_value_units(
                fc400_config["full_scale_load"],
                fc400_config["device_unit"],
                self.unit,
            )
        )
        if target_load > fc400_capacity:
            raise ValueError(
                f"Target Load는 FC400 설정 용량 {fc400_capacity:.3f} "
                f"{self.unit} 이하여야 합니다."
            )
        if load_limit > fc400_capacity:
            raise ValueError(
                f"안전 하중 리미트는 FC400 설정 용량 {fc400_capacity:.3f} "
                f"{self.unit} 이하여야 합니다."
            )

        counts_per_mm = position_config["counts_per_mm"]
        tolerance_mm = max(0.01, 2.0 / counts_per_mm)
        return {
            "minimum_mm": minimum_mm,
            "maximum_mm": maximum_mm,
            "stroke_span_mm": maximum_mm - minimum_mm,
            "speed_mm_min": board_speed,
            "hold_seconds": hold_seconds,
            "target_load": target_load,
            "load_limit": load_limit,
            "target_strokes": target_strokes,
            "acceleration_ms": acceleration_ms,
            "deceleration_ms": deceleration_ms,
            "counts_per_mm": counts_per_mm,
            "tolerance_mm": tolerance_mm,
        }

    def validate_live_motion_axis(self, controller, motion_config):
        axis_status = controller.read_axis_status()
        if axis_status.get("servo_alarm") or axis_status.get("operation_alarm"):
            raise RuntimeError("선택 축에 Servo/Operation alarm이 있습니다.")
        if axis_status.get("operating"):
            raise RuntimeError("선택 축이 이미 운전 중입니다.")
        if not axis_status.get("servo_ready"):
            raise RuntimeError(
                "선택 축이 Servo Ready 상태가 아닙니다. Servo ON 후 다시 시작해주세요."
            )

        raw_counts = int(axis_status["position"])
        current_position_mm = (
            raw_counts / motion_config["counts_per_mm"]
        ) - self.position_zero_offset_mm
        allowed_margin_mm = max(1.0, motion_config["stroke_span_mm"] * 0.05)
        if not (
            motion_config["minimum_mm"] - allowed_margin_mm
            <= current_position_mm
            <= motion_config["maximum_mm"] + allowed_margin_mm
        ):
            raise RuntimeError(
                f"현재 위치 {current_position_mm:.3f} mm가 시험 범위 "
                f"{motion_config['minimum_mm']:.3f}~{motion_config['maximum_mm']:.3f} mm에서 "
                "너무 멉니다. 위치 영점 또는 시험 범위를 확인해주세요."
            )
        return current_position_mm, raw_counts

    def start_live_motion_move(self, target_mm, state, current_position_mm=None):
        controller = self.get_position_controller(require_armed=True)
        config = self.live_motion_config
        if config is None:
            raise RuntimeError("자동 왕복 시험 설정이 없습니다.")

        if current_position_mm is None:
            current_position_mm, _ = self.read_position_feedback()
        if current_position_mm is None:
            raise RuntimeError("MR-MC240N 현재 위치를 읽지 못했습니다.")

        distance_mm = target_mm - current_position_mm
        if abs(distance_mm) > (
            config["stroke_span_mm"] + max(1.0, config["tolerance_mm"] * 5)
        ):
            raise RuntimeError(
                f"요청 이동량 {distance_mm:.3f} mm가 설정 Stroke 범위를 초과합니다."
            )

        self.test_state = state
        self.live_motion_target_mm = target_mm
        expected_seconds = abs(distance_mm) / config["speed_mm_min"] * 60.0
        self.live_motion_deadline = time.monotonic() + max(
            10.0,
            expected_seconds * 2.0
            + (config["acceleration_ms"] + config["deceleration_ms"]) / 1000.0
            + 5.0,
        )

        if abs(distance_mm) <= config["tolerance_mm"]:
            return

        distance_counts = round(distance_mm * config["counts_per_mm"])
        if distance_counts == 0:
            raise RuntimeError("이동량이 1 command unit 미만입니다.")
        try:
            controller.move_relative(
                distance_counts,
                config["speed_mm_min"],
                config["acceleration_ms"],
                config["deceleration_ms"],
            )
        finally:
            self.position_motion_may_be_active = (
                controller._motion_command_may_be_active
            )
            self.update_position_control_state()
        self.append_system_log(
            f"{state}: axis {controller.axis_number}, "
            f"{current_position_mm:.3f} → {target_mm:.3f} mm "
            f"({distance_counts} cmd, {config['speed_mm_min']} mm/min)",
            "MR-MC240N",
        )

    def begin_live_motion_cycle(self, current_position_mm):
        config = self.live_motion_config
        self.live_motion_cycle_active = True
        self.current_stroke = 0
        self.live_stroke_peak_values = None

        if (
            abs(current_position_mm - config["minimum_mm"])
            <= config["tolerance_mm"]
        ):
            self.start_live_motion_move(
                config["maximum_mm"],
                "MOVING_TO_MAX",
                current_position_mm,
            )
        else:
            self.start_live_motion_move(
                config["minimum_mm"],
                "POSITIONING_MIN",
                current_position_mm,
            )

    def update_live_stroke_peak(self, calibrated_values):
        if self.live_stroke_peak_values is None:
            self.live_stroke_peak_values = list(calibrated_values)
            return
        self.live_stroke_peak_values = [
            max(previous, current)
            for previous, current in zip(
                self.live_stroke_peak_values,
                calibrated_values,
            )
        ]

    def record_live_motion_stroke(self):
        if self.live_stroke_peak_values is None:
            calibrated_values = [
                max(0, self.raw_data[index] - self.sensor_zeros[index])
                for index in range(6)
            ]
        else:
            calibrated_values = self.live_stroke_peak_values
        self.stroke_data_history.append(list(calibrated_values))
        self.stroke_position_history.append(self.live_motion_config["maximum_mm"])

    def update_live_motion_cycle(self, position_mm, calibrated_values):
        if not self.live_motion_cycle_active:
            return False

        config = self.live_motion_config
        now = time.monotonic()
        if self.test_state in {"MOVING_TO_MAX", "HOLDING_MAX"}:
            self.update_live_stroke_peak(calibrated_values)

        if self.test_state == "HOLDING_MAX":
            if now < self.live_hold_deadline:
                return False
            self.record_live_motion_stroke()
            self.start_live_motion_move(
                config["minimum_mm"],
                "MOVING_TO_MIN",
                position_mm,
            )
            return False

        controller = self.get_position_controller(require_armed=True)
        axis_status = controller.read_axis_status()
        if axis_status.get("servo_alarm") or axis_status.get("operation_alarm"):
            raise RuntimeError("자동 왕복 중 Servo/Operation alarm이 발생했습니다.")
        if now > self.live_motion_deadline:
            raise TimeoutError(
                f"{self.test_state} 제한시간을 초과했습니다 "
                f"(현재 {position_mm:.3f} mm / 목표 {self.live_motion_target_mm:.3f} mm)."
            )

        target_reached = (
            abs(position_mm - self.live_motion_target_mm)
            <= config["tolerance_mm"]
        )
        operation_finished = (
            not axis_status.get("operating", False)
            and (
                axis_status.get("in_position", False)
                or axis_status.get("operation_complete", False)
            )
        )
        if not (target_reached and operation_finished):
            return False

        if self.test_state == "POSITIONING_MIN":
            self.live_stroke_peak_values = None
            self.start_live_motion_move(
                config["maximum_mm"],
                "MOVING_TO_MAX",
                position_mm,
            )
            return False

        if self.test_state == "MOVING_TO_MAX":
            self.test_state = "HOLDING_MAX"
            self.live_hold_deadline = now + config["hold_seconds"]
            self.append_system_log(
                f"Stroke {self.current_stroke + 1}/{config['target_strokes']}: "
                f"Max Length 도달, {config['hold_seconds']:.2f} s 유지",
                "TEST",
            )
            return False

        if self.test_state == "MOVING_TO_MIN":
            self.current_stroke += 1
            self.append_system_log(
                f"Stroke {self.current_stroke}/{config['target_strokes']} completed",
                "TEST",
            )
            if self.current_stroke >= config["target_strokes"]:
                self.live_motion_cycle_active = False
                self.stop_test(completed=True)
                QMessageBox.information(
                    self,
                    "Test Complete",
                    f"{config['target_strokes']} Strokes 실장비 시험이 완료되었습니다.",
                )
                return True

            self.live_stroke_peak_values = None
            self.start_live_motion_move(
                config["maximum_mm"],
                "MOVING_TO_MAX",
                position_mm,
            )
        return False

    def abort_live_motion_test(self, message):
        self.append_system_log(message, "MR-MC240N")
        controller = self.position_monitor
        if controller is not None:
            try:
                controller.stop_all_axes(
                    axis_numbers=range(1, 7),
                    rapid=True,
                    timeout_ms=3000,
                )
                self.append_system_log(
                    "자동 왕복 중단: all-axis Rapid Stop command sent",
                    "MR-MC240N",
                )
            except Exception as stop_exc:
                self.append_system_log(
                    f"All-axis Rapid Stop 실패: {stop_exc}",
                    "MR-MC240N",
                )
        self.live_motion_cycle_active = False
        self.stop_test(completed=False)
        QMessageBox.critical(self, "MR-MC240N Automatic Test Error", message)

    def trigger_load_limit_emergency_stop(self, peak_load, limit):
        if self.load_limit_tripped:
            return
        self.load_limit_tripped = True
        message = (
            f"안전 하중 리미트 초과: 버퍼 피크 {peak_load:.3f} {self.unit} "
            f"≥ 설정값 {limit:.3f} {self.unit}"
        )
        self.append_system_log(message, "SAFETY", dedupe_seconds=0)
        stop_confirmed = self.stop_all_position_axes(
            reason=message,
            report_error=False,
        )
        if self.is_test_running:
            self.live_motion_cycle_active = False
            self.stop_test(completed=False)
        self.lbl_status.setText(
            "Status: EMERGENCY STOP - LOAD LIMIT "
            f"({peak_load:.3f} / {limit:.3f} {self.unit})"
        )
        self.lbl_status.setStyleSheet(
            "color: #B71C1C; font-weight: bold;"
        )
        stop_result = (
            "6축 전체 긴급정지 명령이 확인되었습니다."
            if stop_confirmed
            else (
                "일부 축의 긴급정지 확인에 실패했습니다. "
                "하드와이어 비상정지를 즉시 작동하고 축 상태를 확인하세요."
            )
        )
        QMessageBox.critical(
            self,
            "Load Limit Emergency Stop",
            f"{message}\n\n{stop_result}",
        )

    def start_hardware_test(self):
        motion_config = None
        initial_position_mm = None
        initial_position_counts = None
        try:
            motion_config = self.get_live_motion_test_config()
            self.open_fc400_usb_task()
            self.data_unit = self.get_fc400_config()["device_unit"]
            self.open_position_monitor()
            controller = self.get_position_controller(require_armed=True)
            (
                initial_position_mm,
                initial_position_counts,
            ) = self.validate_live_motion_axis(controller, motion_config)
        except Exception as exc:
            self.close_ni_daq_task()
            self.close_position_monitor()
            self.append_system_log(f"실장비 연결 실패: {exc}", "HARDWARE")
            QMessageBox.warning(self, "Hardware Connection Error", f"실장비 연결에 실패했습니다.\n{exc}")
            return

        self.is_test_running = True
        self.set_test_form_locked(True)
        self.update_position_control_state()
        self.current_stroke = 0
        self.current_sample_result_key = None
        self.stroke_data_history = []
        self.stroke_position_history = []
        self.time_series_data = []
        self.time_elapsed = 0.0
        self.latest_live_snapshot = [0.0] * 6
        self.latest_live_position_mm = initial_position_mm
        self.latest_live_position_counts = initial_position_counts
        self.live_motion_config = motion_config
        self.live_motion_target_mm = None
        self.live_motion_deadline = 0.0
        self.live_hold_deadline = 0.0
        self.live_stroke_peak_values = None
        self.load_limit_tripped = False
        self.refresh_review_controls()
        self.update_table_headers()
        self.chart.reset_scale()

        now = datetime.now()
        self.test_start_ts = now.strftime("%Y%m%d_%H%M%S")
        self.test_start_display_time = now.strftime('%Y-%m-%d %H:%M:%S')
        self.start_test_camera_recording()

        self.test_state = "LIVE"
        self.target_strokes = motion_config["target_strokes"]
        self.target_load = motion_config["target_load"]
        self.hold_time = motion_config["hold_seconds"]
        try:
            self.begin_live_motion_cycle(initial_position_mm)
        except Exception as exc:
            self.is_test_running = False
            self.set_test_form_locked(False)
            self.live_motion_cycle_active = False
            self.live_motion_config = None
            self.live_motion_target_mm = None
            self.close_ni_daq_task()
            self.close_position_monitor()
            self.update_start_button_idle_state()
            self.append_system_log(
                f"자동 왕복 시작 실패: {exc}",
                "MR-MC240N",
            )
            QMessageBox.critical(
                self,
                "MR-MC240N Automatic Test Error",
                f"자동 왕복 시험을 시작하지 못했습니다.\n{exc}",
            )
            return
        self.btn_start.setText("FC400 + MR-MC240N 시험 중지")
        self.btn_start.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 10px;")
        self.btn_start.setEnabled(True)
        self.lbl_status.setStyleSheet("color: #1565C0; font-weight: bold;")
        self.lbl_status.setText(
            f"Status: {self.test_state} (0 / {self.target_strokes} Strokes)"
        )
        self.append_system_log(
            "FC400 + MR-MC240N automatic test started: "
            f"{motion_config['minimum_mm']:.3f}↔{motion_config['maximum_mm']:.3f} mm, "
            f"{motion_config['speed_mm_min']} mm/min, "
            f"{motion_config['target_strokes']} strokes",
            "TEST",
        )
        self.timer.start(self.timer_interval)
        self.hardware_test_step()

    def hardware_test_step(self):
        try:
            measurement = self.read_fc400_measurement()
            load_value = measurement["value"]
            live_stable = measurement["stable"]
            live_voltage = measurement["voltage"]
        except Exception as exc:
            self.stop_test(completed=False)
            self.append_system_log(f"하중 읽기 실패: {exc}", "FC400")
            QMessageBox.critical(
                self, "FC400 Read Error", f"하중 값을 읽지 못했습니다.\n{exc}"
            )
            return

        load_samples = measurement.get("samples") or [load_value]
        tare_value = self.sensor_zeros[0]
        peak_sample = max(
            load_samples,
            key=lambda sample: abs(float(sample) - tare_value),
        )
        peak_calibrated_base = abs(float(peak_sample) - tare_value)
        peak_calibrated_display = abs(
            self.convert_value_units(
                peak_calibrated_base,
                self.data_unit,
                self.unit,
            )
        )
        load_limit = (
            self.live_motion_config["load_limit"]
            if self.live_motion_config is not None
            else float(self.in_load_limit.text())
        )
        if peak_calibrated_display >= load_limit:
            self.raw_data = [float(peak_sample)] * 6
            self.latest_live_snapshot = [peak_calibrated_base] * 6
            self.time_elapsed += self.timer_interval / 1000.0
            trip_row = {
                "Time [sec]": round(self.time_elapsed, 3),
                "Stroke": min(
                    self.current_stroke + 1,
                    max(1, self.target_strokes),
                ),
                "State": "LOAD_LIMIT_TRIP",
                f"Peak Load [{self.unit}]": round(
                    peak_calibrated_display, 6
                ),
                f"Load Limit [{self.unit}]": round(load_limit, 6),
                "Buffered Sample Count": len(load_samples),
            }
            for axis in range(1, 7):
                trip_row[
                    f"Axis {axis} Raw [{self.data_unit}]"
                ] = round(float(peak_sample), 6)
                trip_row[
                    f"Axis {axis} Calibrated [{self.unit}]"
                ] = round(peak_calibrated_display, 6)
            self.time_series_data.append(trip_row)
            self.update_table()
            self.update_chart()
            self.trigger_load_limit_emergency_stop(
                peak_calibrated_display,
                load_limit,
            )
            return

        try:
            position_mm, position_counts = self.read_position_feedback()
        except Exception as exc:
            self.stop_test(completed=False)
            self.append_system_log(f"위치 읽기 실패: {exc}", "MR-MC240N")
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
            'Stroke': (
                min(self.current_stroke + 1, self.target_strokes)
                if self.live_motion_cycle_active
                else 1
            ),
            'State': self.test_state,
        }
        if position_mm is not None:
            log_row['Position [mm]'] = round(position_mm, 3)
        if position_counts is not None:
            log_row['Position Raw [cmd]'] = int(position_counts)
        if live_stable is not None:
            log_row['Stable'] = int(bool(live_stable))
        if live_voltage is not None:
            log_row['FC400 Analog Output [V]'] = round(live_voltage, 5)
        log_row[f'Buffered Peak Load [{self.unit}]'] = round(
            peak_calibrated_display,
            5,
        )
        log_row['Buffered Sample Count'] = len(load_samples)

        for i in range(6):
            log_row[f'Axis {i+1} Raw [{self.data_unit}]'] = round(self.raw_data[i], 3)
        for i in range(6):
            log_row[f'Axis {i+1} Calibrated [{self.unit}]'] = round(current_calibrated_display[i], 3)
        self.append_camera_metrics_to_log_row(log_row)
        self.time_series_data.append(log_row)

        if self.live_motion_cycle_active:
            try:
                test_stopped = self.update_live_motion_cycle(
                    position_mm,
                    current_calibrated_base,
                )
            except Exception as exc:
                self.abort_live_motion_test(f"자동 왕복 시험 실패: {exc}")
                return
            if test_stopped:
                return

        source_name = "FC400"
        if self.live_motion_cycle_active and position_mm is not None:
            self.lbl_status.setText(
                f"Status: {self.test_state} "
                f"({self.current_stroke} / {self.target_strokes} Strokes, "
                f"{source_name} {current_calibrated_display[0]:.2f} {self.unit}, "
                f"{position_mm:.3f} mm)"
            )
        elif position_mm is not None:
            self.lbl_status.setText(
                f"Status: LIVE ({source_name} {current_calibrated_display[0]:.2f} {self.unit}, Stroke {position_mm:.3f} mm)"
            )
        else:
            self.lbl_status.setText(
                f"Status: LIVE ({source_name} {current_calibrated_display[0]:.2f} {self.unit}, 6채널 동일)"
            )

    def timer_step(self):
        self.hardware_test_step()

    def ensure_export_snapshot(self):
        if self.stroke_data_history:
            return
        if len(self.time_series_data) > 0:
            self.stroke_data_history = [self.latest_live_snapshot.copy()]
            snapshot_mm = (
                self.latest_live_position_mm
                if self.latest_live_position_mm is not None
                else self.get_default_stroke_mm()
            )
            self.stroke_position_history = [snapshot_mm]

    def change_unit(self, unit):
        previous_unit = self.unit
        if previous_unit != unit:
            for attribute_name in ("in_load", "in_load_limit"):
                widget = getattr(self, attribute_name, None)
                if widget is None:
                    continue
                try:
                    converted_value = self.convert_value_units(
                        float(widget.text()),
                        previous_unit,
                        unit,
                    )
                except (TypeError, ValueError):
                    continue
                widget.setText(f"{converted_value:.3f}")
        self.unit = unit
        self.lbl_target_load_input.setText(f"목표 하중\n[{unit}]:")
        self.lbl_load_limit_input.setText(
            f"안전 하중 리미트\n[{unit}]:"
        )
        self.update_table_headers()
        self.update_table()
        self.update_chart(reset_scale=True)
        if self.review_slider.isEnabled():
            self.on_review_slider_changed(self.review_slider.value())

    def zero_sensors(self):
        if self.is_test_running:
            QMessageBox.warning(
                self,
                "Test Running",
                "시험을 중지한 뒤 영점을 설정해주세요.",
            )
            return

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
        self.current_sample_result_key = None

        try:
            current_value = self.read_fc400_measurement()["value"]
        except Exception as exc:
            self.fc400_device_ready = False
            self.fc400_readiness_detail = "zero read failed"
            self.update_hardware_readiness_status()
            self.append_system_log(f"로드셀 영점 설정 실패: {exc}", "FC400")
            QMessageBox.warning(
                self,
                "FC400 Zero Error",
                f"로드셀 영점 설정에 실패했습니다.\n{exc}",
            )
            return

        self.raw_data = [current_value] * 6
        self.sensor_zeros = self.raw_data.copy()
        message_text = "로드셀 영점(Tare) 및 이전 데이터 초기화가 완료되었습니다."

        if self.is_position_pcie_enabled():
            try:
                current_position_mm, current_position_counts = self.read_position_feedback()
                self.position_zero_offset_mm += current_position_mm if current_position_mm is not None else 0.0
                self.latest_live_position_mm = 0.0
                self.latest_live_position_counts = current_position_counts
                if self.position_monitor is not None:
                    self.refresh_position_axis_status()
            except Exception as exc:
                self.set_mr_status_text(f"MR-MC240N: 위치 영점은 유지됨 - {exc}")

        self.update_table()
        self.update_chart(reset_scale=True)
        self.update_hardware_readiness_status()
        self.append_system_log(message_text, "TEST")
        self.refresh_review_controls()
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

    def refresh_review_controls(self):
        has_review_data = bool(self.time_series_data) and not self.is_test_running
        self.review_mode_combo.setEnabled(has_review_data)
        self.review_slider.setEnabled(has_review_data)
        if not has_review_data:
            self.review_indices = []
            self.review_selected_data_index = None
            self.review_slider.blockSignals(True)
            self.review_slider.setRange(0, 0)
            self.review_slider.setValue(0)
            self.review_slider.blockSignals(False)
            self.lbl_review_position.setText(
                "LIVE" if self.is_test_running else "기록 없음"
            )
            return
        self.on_review_mode_changed()

    def on_review_mode_changed(self, _index=None):
        if self.is_test_running or not self.time_series_data:
            self.refresh_review_controls()
            return

        mode = self.review_mode_combo.currentData()
        indices = list(range(len(self.time_series_data)))
        if mode == "position":
            indices.sort(
                key=lambda index: (
                    self.time_series_data[index].get(
                        "Position [mm]",
                        float("inf"),
                    ),
                    self.time_series_data[index].get(
                        "Time [sec]",
                        float("inf"),
                    ),
                )
            )
        self.review_indices = indices
        self.review_slider.blockSignals(True)
        self.review_slider.setRange(0, max(0, len(indices) - 1))
        self.review_slider.setValue(max(0, len(indices) - 1))
        self.review_slider.blockSignals(False)
        self.on_review_slider_changed(self.review_slider.value())

    def get_review_row_loads(self, row):
        loads = []
        for axis in range(1, 7):
            prefix = f"Axis {axis} Calibrated ["
            matching_key = next(
                (
                    key
                    for key in row
                    if key.startswith(prefix) and key.endswith("]")
                ),
                None,
            )
            if matching_key is None:
                return None
            source_unit = matching_key[len(prefix):-1]
            loads.append(
                max(
                    0.0,
                    self.convert_value_units(
                        float(row[matching_key]),
                        source_unit,
                        self.unit,
                    ),
                )
            )
        return loads

    def on_review_slider_changed(self, slider_index):
        if (
            self.is_test_running
            or not self.review_indices
            or not 0 <= slider_index < len(self.review_indices)
        ):
            return
        data_index = self.review_indices[slider_index]
        row = self.time_series_data[data_index]
        loads = self.get_review_row_loads(row)
        if loads is not None:
            self.chart.plot_data(
                loads,
                interpolate_type=self.interp_combo.currentText(),
                unit=self.unit,
                reset_scale=False,
            )
        self.review_selected_data_index = data_index
        time_text = row.get("Time [sec]", "-")
        position = row.get("Position [mm]")
        position_text = (
            f"{float(position):.3f} mm"
            if position is not None
            else "위치 없음"
        )
        self.lbl_review_position.setText(
            f"t={time_text}s · {position_text} · {data_index + 1}/"
            f"{len(self.time_series_data)}"
        )

    def stop_test(self, completed=False):
        stop_test_recording = self.camera_recording_started_by_test
        was_live_motion_test = self.live_motion_config is not None
        was_live_motion_active = self.live_motion_cycle_active
        live_motion_strokes = self.current_stroke
        controller_motion_may_be_active = bool(
            self.position_monitor is not None
            and self.position_monitor._motion_command_may_be_active
        )
        motion_was_uncertain = bool(
            was_live_motion_active
            or self.position_jog_command_active
            or self.position_motion_may_be_active
            or controller_motion_may_be_active
        )
        motion_stop_confirmed = not motion_was_uncertain

        if (
            motion_was_uncertain
            and self.position_monitor is not None
        ):
            if self.load_limit_tripped:
                try:
                    self.position_monitor.stop_all_axes(
                        axis_numbers=range(1, 7),
                        rapid=True,
                        timeout_ms=3000,
                    )
                    motion_stop_confirmed = True
                    self.append_system_log(
                        "Load-limit cleanup: all-axis Rapid Stop confirmed",
                        "SAFETY",
                        dedupe_seconds=0,
                    )
                except Exception as exc:
                    motion_stop_confirmed = False
                    self.append_system_log(
                        f"Load-limit cleanup Rapid Stop failed: {exc}",
                        "SAFETY",
                        dedupe_seconds=0,
                    )
            else:
                try:
                    self.position_monitor.stop(
                        rapid=False,
                        timeout_ms=3000,
                    )
                    motion_stop_confirmed = True
                    self.append_system_log(
                        "Automatic test stop command sent",
                        "MR-MC240N",
                    )
                except Exception as exc:
                    self.append_system_log(
                        f"Automatic test normal stop failed: {exc}",
                        "MR-MC240N",
                    )
                    try:
                        self.position_monitor.stop(
                            rapid=True,
                            timeout_ms=3000,
                        )
                        motion_stop_confirmed = True
                        self.append_system_log(
                            "Automatic test rapid stop command sent",
                            "MR-MC240N",
                        )
                    except Exception as rapid_exc:
                        motion_stop_confirmed = False
                        self.append_system_log(
                            f"Automatic test rapid stop failed: {rapid_exc}",
                            "MR-MC240N",
                        )
        if motion_stop_confirmed:
            self.position_jog_command_active = False
            self.position_jog_direction = None
        self.position_motion_may_be_active = not motion_stop_confirmed

        self.is_test_running = False
        self.set_test_form_locked(False)
        self.test_state = "IDLE"
        self.live_motion_cycle_active = False
        self.timer.stop()
        if stop_test_recording:
            self.stop_camera_recording(notify=False)
        self.update_position_control_state()
        self.update_start_button_idle_state()

        self.close_ni_daq_task()
        self.close_position_monitor()
        self.ensure_export_snapshot()
        if was_live_motion_test and completed:
            self.lbl_status.setText(
                f"Status: Test Completed ({live_motion_strokes} Strokes)"
            )
            self.lbl_status.setStyleSheet(
                "color: #2E7D32; font-weight: bold;"
            )
            self.append_system_log(
                f"FC400 + MR-MC240N test completed "
                f"({live_motion_strokes} strokes)",
                "TEST",
            )
        elif was_live_motion_test:
            self.lbl_status.setText(
                f"Status: Hardware Test Stopped ({live_motion_strokes} Strokes)"
            )
            self.lbl_status.setStyleSheet(
                "color: #1565C0; font-weight: bold;"
            )
            self.append_system_log(
                f"FC400 + MR-MC240N test stopped "
                f"({live_motion_strokes} strokes)",
                "TEST",
            )
        else:
            self.lbl_status.setText("Status: FC400 / USB-6002 Monitoring Stopped")
            self.lbl_status.setStyleSheet(
                "color: #1565C0; font-weight: bold;"
            )
            self.append_system_log(
                "FC400 / USB-6002 live monitoring stopped",
                "TEST",
            )
        if completed:
            self.add_current_sample_result(auto=True)
        self.live_motion_config = None
        self.live_motion_target_mm = None
        self.live_stroke_peak_values = None
        self.update_table()
        self.update_chart()
        self.refresh_review_controls()

    def toggle_test(self):
        if self.is_test_running:
            self.stop_test(completed=False)
            return

        self.update_hardware_readiness_status()
        if not self.btn_start.isEnabled():
            QMessageBox.warning(
                self,
                "Hardware Not Ready",
                self.lbl_status.text().replace("Status: ", ""),
            )
            return
        self.start_hardware_test()

    def get_stroke_position_value(self, index):
        if index < len(self.stroke_position_history):
            return self.stroke_position_history[index]
        return self.get_default_stroke_mm()

    def build_current_sample_result(self):
        self.ensure_export_snapshot()
        if not self.stroke_data_history:
            return None

        sample_no = self.in_sample_no.text().strip() or str(
            len(self.sample_results) + 1
        )
        result_key = (
            self.test_start_ts
            or f"manual_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        )
        return {
            "key": f"{result_key}:{sample_no}",
            "sample_no": sample_no,
            "report_no": self.in_report_no.text(),
            "customer": self.in_customer.text(),
            "model": self.in_model.text(),
            "part_name": self.in_part_name.text(),
            "part_no": self.in_part_no.text(),
            "test_start_ts": self.test_start_ts,
            "test_start_display_time": (
                self.test_start_display_time
                or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ),
            "completed_display_time": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "unit": self.unit,
            "data_unit": self.data_unit,
            "target_load": self.in_load.text(),
            "load_limit": self.in_load_limit.text(),
            "interpolation": self.interp_combo.currentText(),
            "stroke_data_history": copy.deepcopy(
                self.stroke_data_history
            ),
            "stroke_position_history": list(
                self.stroke_position_history
            ),
            "time_series_data": copy.deepcopy(self.time_series_data),
            "final_calibrated_base": list(
                self.stroke_data_history[-1]
                if self.stroke_data_history
                else self.latest_live_snapshot
            ),
        }

    def add_current_sample_result(self, _checked=False, auto=False):
        if self.is_test_running:
            if not auto:
                QMessageBox.warning(
                    self,
                    "Test Running",
                    "시험이 끝난 뒤 현재 결과를 추가해주세요.",
                )
            return False

        result = self.build_current_sample_result()
        if result is None:
            if not auto:
                QMessageBox.warning(
                    self,
                    "No Data",
                    "추가할 측정 결과가 없습니다.",
                )
            return False

        if (
            self.current_sample_result_key is not None
            and any(
                sample["key"] == self.current_sample_result_key
                for sample in self.sample_results
            )
        ):
            if not auto:
                QMessageBox.information(
                    self,
                    "Already Added",
                    "현재 측정 결과는 이미 시료 목록에 추가되어 있습니다.",
                )
            return False

        self.sample_results.append(result)
        self.current_sample_result_key = result["key"]
        self.refresh_sample_results_table()
        self.append_system_log(
            f"시료 {result['sample_no']} 결과 저장 "
            f"({len(result['stroke_data_history'])} strokes)",
            "REPORT",
            dedupe_seconds=0,
        )
        try:
            next_sample_number = int(result["sample_no"]) + 1
        except ValueError:
            next_sample_number = len(self.sample_results) + 1
        self.in_sample_no.setText(str(next_sample_number))
        return True

    def refresh_sample_results_table(self):
        self.sample_table.setRowCount(len(self.sample_results))
        for row, sample in enumerate(self.sample_results):
            values = [
                sample["sample_no"],
                sample["report_no"],
                sample["completed_display_time"],
                str(len(sample["stroke_data_history"])),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                self.sample_table.setItem(row, column, item)
        self.btn_remove_sample.setEnabled(bool(self.sample_results))
        self.btn_clear_samples.setEnabled(bool(self.sample_results))

    def remove_selected_sample_results(self):
        selected_rows = sorted(
            {
                index.row()
                for index in self.sample_table.selectionModel().selectedRows()
            },
            reverse=True,
        )
        if not selected_rows:
            QMessageBox.information(
                self,
                "No Selection",
                "삭제할 시료 행을 선택해주세요.",
            )
            return
        removed_keys = set()
        for row in selected_rows:
            if 0 <= row < len(self.sample_results):
                removed_keys.add(self.sample_results[row]["key"])
                del self.sample_results[row]
        if self.current_sample_result_key in removed_keys:
            self.current_sample_result_key = None
        self.refresh_sample_results_table()

    def clear_sample_results(self):
        if not self.sample_results:
            return
        answer = QMessageBox.question(
            self,
            "Clear Sample Results",
            "누적된 모든 시료 결과를 목록에서 삭제할까요?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.sample_results.clear()
        self.current_sample_result_key = None
        self.refresh_sample_results_table()

    def get_report_samples(self):
        if self.sample_results:
            selected_rows = sorted(
                {
                    index.row()
                    for index in self.sample_table.selectionModel().selectedRows()
                }
            )
            if selected_rows:
                return [
                    self.sample_results[row]
                    for row in selected_rows
                    if 0 <= row < len(self.sample_results)
                ]
            return list(self.sample_results)
        current_result = self.build_current_sample_result()
        return [current_result] if current_result is not None else []

    def export_csv(self):
        self.ensure_export_snapshot()
        if len(self.stroke_data_history) == 0:
            self.append_system_log("CSV 저장 실패: 저장할 테스트 데이터가 없습니다", "EXPORT")
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

            self.append_system_log(f"CSV saved: {file_name}", "EXPORT")
            QMessageBox.information(self, "Saved", f"CSV 파일이 성공적으로 저장되었습니다.\n{file_name}")

    def build_report_figure(self, sample):
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.patch.set_facecolor('white')
        report_unit = sample["unit"]
        source_unit = sample["data_unit"]
        stroke_positions = sample["stroke_position_history"]
        all_data = np.array(sample["stroke_data_history"], dtype=float)
        all_data = self.convert_array_units(
            all_data,
            source_unit,
            report_unit,
        )
        final_data = self.convert_array_units(
            np.array(sample["final_calibrated_base"], dtype=float),
            source_unit,
            report_unit,
        ).tolist()

        if os.path.isfile(CTR_LOGO_PATH):
            try:
                ax_logo = fig.add_axes([0.06, 0.91, 0.22, 0.06])
                ax_logo.imshow(plt.imread(CTR_LOGO_PATH))
                ax_logo.axis('off')
            except (OSError, ValueError):
                pass

        # 메인 타이틀
        fig.text(0.43, 0.94, 'Test Report', ha='center', fontsize=20, fontproperties=font_prop, weight='bold')

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
        date_str = sample["test_start_display_time"]
        target_spec = (
            f"{sample['target_load']} {report_unit} / "
            f"Limit: {sample['load_limit']} {report_unit}"
        )

        header_data = [
            ['관리번호 (Report No)', sample["report_no"], '시험항목 (Test Item)', '힘 (Load)'],
            ['시료번호 (Sample No)', sample["sample_no"], '판정기준 (Test Spec)', '합격'],
            ['고객사 (Customer)', sample["customer"], '시험목적 (Test purpose)', target_spec],
            ['차종 (Model)', sample["model"], '의뢰자 (Client)', ''],
            ['품명 (Part Name)', sample["part_name"], '품번 (Part No)', sample["part_no"]],
            ['Test Start', date_str, 'Completed', sample["completed_display_time"]],
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

        table_data = [['No', 'Stroke\n[mm]', 'Axis 1', 'Axis 2', 'Axis 3',
                       'Axis 4', 'Axis 5', 'Axis 6', f'Average']]

        visible_data = all_data[:10]
        for idx, stroke_data in enumerate(visible_data):
            stroke_mm_value = (
                stroke_positions[idx]
                if idx < len(stroke_positions)
                else 0.0
            )
            stroke_mm = f"{stroke_mm_value:.3f}"
            avg = np.mean(stroke_data)
            row = [str(idx+1), stroke_mm] + [f'{v:.2f}' for v in stroke_data] + [f'{avg:.2f}']
            table_data.append(row)

        for _ in range(10 - len(visible_data)):
            table_data.append(['-', '-', '-', '-', '-', '-', '-', '-', '-'])

        min_vals = np.min(all_data, axis=0)
        max_vals = np.max(all_data, axis=0)
        range_vals = max_vals - min_vals
        avg_vals = np.mean(all_data, axis=0)
        min_stroke = round(np.min(stroke_positions), 3) if stroke_positions else 0.0
        max_stroke = round(np.max(stroke_positions), 3) if stroke_positions else 0.0
        avg_stroke = round(np.mean(stroke_positions), 3) if stroke_positions else 0.0

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
            elif row > len(visible_data) and row <= 10:
                cell.set_text_props(color='#999999')
            elif row > 10:
                cell.set_facecolor('#FFF5E6')
                if col == 0:
                    cell.set_text_props(weight='bold', fontproperties=font_prop)

        # Remark 섹션
        ax_remark = fig.add_axes([0.08, 0.40, 0.84, 0.03])
        ax_remark.axis('off')
        omitted_count = max(0, len(all_data) - len(visible_data))
        remark = (
            f"First 10 strokes shown; {omitted_count} additional strokes "
            "are included in statistics."
            if omitted_count
            else " "
        )
        ax_remark.text(0.01, 0.5, remark, fontsize=8, fontproperties=font_prop, weight='bold', va='center')
        rect = Rectangle((0, 0), 1, 1, linewidth=1, edgecolor='black', facecolor='none', transform=ax_remark.transAxes)
        ax_remark.add_patch(rect)

        # 방사형 그래프
        ax_graph = fig.add_axes([0.25, 0.06, 0.5, 0.32], polar=True)
        data = final_data
        angles = np.linspace(0, 2 * np.pi, 6, endpoint=False)
        plot_data = data + [data[0]]
        angles_plot = np.append(angles, angles[0])

        ax_graph.set_theta_offset(np.pi / 2)
        ax_graph.set_theta_direction(-1)
        ax_graph.set_xticks(angles)
        ax_graph.set_xticklabels(['Axis 1', 'Axis 2', 'Axis 3', 'Axis 4', 'Axis 5', 'Axis 6'], fontproperties=font_prop, fontsize=9)

        interpolate_type = sample["interpolation"]
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
        ax_graph.set_ylabel(f'Load [{report_unit}]', labelpad=25, fontproperties=font_prop, fontsize=9)
        ax_graph.set_title('Final Load Distribution (6-Axis)', pad=15, fontproperties=font_prop, fontsize=11, weight='bold')
        ax_graph.grid(True, linestyle='--', alpha=0.7)
        return fig

    def export_pdf(self):
        samples = self.get_report_samples()
        if not samples:
            self.append_system_log(
                "PDF 저장 실패: 저장할 테스트 데이터가 없습니다",
                "EXPORT",
            )
            QMessageBox.warning(
                self,
                "No Data",
                "저장할 테스트 데이터가 없습니다.",
            )
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = (
            f"ClampReports_{ts}_{len(samples)}samples.pdf"
            if len(samples) > 1
            else f"ClampReport_{ts}.pdf"
        )
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save Report",
            default_name,
            "PDF Files (*.pdf)",
        )
        if not file_name:
            return
        if not file_name.lower().endswith(".pdf"):
            file_name += ".pdf"

        try:
            with PdfPages(file_name) as pdf:
                for sample in samples:
                    fig = self.build_report_figure(sample)
                    try:
                        pdf.savefig(fig, dpi=300)
                    finally:
                        plt.close(fig)
        except Exception as exc:
            self.append_system_log(
                f"PDF 저장 실패: {exc}",
                "EXPORT",
                dedupe_seconds=0,
            )
            QMessageBox.critical(
                self,
                "PDF Export Error",
                f"성적서를 저장하지 못했습니다.\n{exc}",
            )
            return

        self.append_system_log(
            f"PDF saved: {file_name} ({len(samples)} samples/pages)",
            "EXPORT",
            dedupe_seconds=0,
        )
        QMessageBox.information(
            self,
            "Saved",
            f"{len(samples)}개 시료의 A4 성적서를 한 PDF로 저장했습니다.\n"
            f"{file_name}",
        )

    def closeEvent(self, event):
        self.close_camera(reset_status=False)
        if self.is_test_running:
            self.stop_test(completed=False)
        else:
            self.close_ni_daq_task()
            self.close_position_monitor()
        if self.position_monitor is not None:
            QMessageBox.critical(
                self,
                "MR-MC240N Stop/Close Not Confirmed",
                "MR-MC240N의 Rapid Stop 또는 Close가 확인되지 않아 프로그램 "
                "종료를 차단했습니다.\n\n외부 비상정지를 작동하고 실제 축 정지를 "
                "확인한 뒤 Rapid Stop/Connect를 다시 시도하세요.",
            )
            event.ignore()
            return
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
    ex = ClampTestMachineApp()
    ex.showMaximized()
    sys.exit(app.exec_())
