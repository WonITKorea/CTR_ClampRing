# CTR Seal Ring 6축 시험기

[English](README_en.md)

6축 클램프 시험을 위한 Windows/PyQt 애플리케이션입니다.

- 시험 시뮬레이션, CSV 저장 및 A4 PDF 리포트
- UNIPULSE FC400 전압 출력 + NI USB-6002 하중 입력
- Mitsubishi MR-MC240N USB 위치 피드백 및 6축 구동
- UVC 카메라/OpenCV 기반 링 형상 측정

앱은 제목 표시줄과 작업 표시줄이 보이는 최대화 창으로 열리며 스크롤 없는
고정 레이아웃을 사용합니다. F11은 전체화면 전환입니다.

## 빠른 시작

Python 3.10 이상을 권장합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

실장비 드라이버가 없어도 `Simulation` 모드는 사용할 수 있습니다.

## 저장소 구성

```text
main.py                       PyQt GUI와 시험 로직
hardware.py                   MR-MC240N Python 제어 계층
mr_mc240n_usb_cli.c           32-bit USB C 브리지 소스
mr_mc240n_pcie_check.py       읽기 전용 PCIe 진단 유틸리티
scripts/build_usb_bridge.ps1  USB 브리지 빌드
scripts/verify.ps1            커밋 전 검사
vendor/mitsubishi/            로컬 전용 Mitsubishi 런타임 위치
```

PB Test, Mitsubishi DLL/라이브러리, 역분석 도구와 진단 트레이스는 라이선스 및
용량 문제로 Git에 포함하지 않습니다. 기존 로컬 파일은 삭제되지 않으며
`.gitignore`에서만 제외됩니다.

## FC400 + NI USB-6002

FC400의 D/A 전압 출력을 USB-6002 아날로그 입력으로 읽어 하중으로 환산합니다.
FC400 전류 출력은 USB-6002 AI에 직접 연결하지 않습니다.

1. NI-DAQmx 드라이버를 설치하고 USB-6002를 연결합니다.
2. FC400 D/A 출력을 전압 모드로 설정합니다.
3. 차동 입력은 `V OUT → AI0`, `COM → AI4 (AI0-)`로 연결합니다.
   RSE 입력은 `COM → AI GND`로 연결합니다.
4. 앱에서 `FC400 + USB-6002`를 선택합니다.
5. 실제 FC400 설정에 맞춰 Zero/Full-scale 전압, 용량, 단위와 샘플 속도를
   입력합니다.

기본값은 `0 V = 0 N`, `10 V = 1000 N`, `Dev1/ai0`, `1000 S/s`입니다.
한 개의 하중값은 6개 축에 동일하게 기록됩니다.

## MR-MC240N USB 직접 제어

### 로컬 런타임 준비

Mitsubishi Position Board 정식 설치본에서 32-bit USB 런타임을 다음 위치에
복사합니다.

```text
vendor/mitsubishi/mc2xxstd_wow64.dll
```

DLL은 Git에 포함되지 않습니다. 별도 import `.lib`는 필요하지 않습니다.

### C 브리지 빌드

USB API는 32-bit C 브리지로 실행됩니다. Tiny C Compiler 경로를 지정하거나
`TCC_PATH` 환경 변수를 사용할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_usb_bridge.ps1 `
  -Compiler C:\path\to\tcc.exe
```

로컬 개발 도구가 `tools\tcc-win32\tcc\tcc.exe`에 있으면 경로를 생략할 수
있습니다. 결과 파일은 `bin\mr_mc240n_usb.exe`이며 Git에는 포함되지 않습니다.

### 연결

1. MR-MC240N USB 케이블과 보드 전원을 연결합니다.
2. `Enable MR-MC240N position board`를 켭니다.
3. `USB controller (direct)`와 Board ID/Axis No를 선택합니다.
4. `Connect USB Controller`를 누릅니다.
5. 축 상태가 `READY`인지 확인한 다음에만 동작 명령을 Arm 합니다.

브리지는 축 미장착, 알람, 운전 중 또는 미준비 상태에서 Servo/JOG/Home/상대
이동을 거부합니다.

`0x0009 (WAITING FOR SSCNET RESPONSE)`는 USB 실패가 아니라 앰프 응답 대기
상태입니다. 컨트롤러 출력은 첫 앰프 `CN1A`, 각 앰프 `CN1B`는 다음 앰프
`CN1A`로 연결하고 마지막 `CN1B`에는 캡을 장착합니다.

### 현재 6축 프리셋

다음 기구 구성을 전제로 합니다.

- Axis 1~6: HG-KR13 + MR-J4-10B-RJ
- 앰프 축 선택 로터리: `0, 1, 2, 3, 4, 5`
- 볼스크루: THK BTK1404, 리드 4 mm
- 모터와 볼스크루 직결 1:1
- `1 command unit = 1 µm` (`1000 command units/mm`)

전자기어 값은 C 브리지의 6축 프리셋에 포함됩니다.

- CMX: `0x00400000` = 4,194,304
- CDV: `0x00000FA0` = 4,000
- Speed unit factor: `0x000003E8` = 1,000

프리셋 적용은 소프트웨어 재부팅 후 RAM 파라미터 기록과 System Start를
수행합니다. 플래시 저장을 하지 않은 상태에서 보드 전원을 끄면 다시 적용해야
할 수 있습니다.

## PCIe 진단

PCIe 경로는 별도의 읽기 전용 유틸리티로 먼저 확인할 수 있습니다.

```powershell
python mr_mc240n_pcie_check.py
```

이 유틸리티는 장치/드라이버/DLL을 점검하고 `sscOpen/sscClose`만 호출합니다.
System Start, Servo 또는 모션 명령은 보내지 않습니다.

## 카메라

일반 UVC 카메라를 연결하고 `Camera / Ring` 영역에서 카메라 인덱스, 해상도,
기준 링 외경과 색상 프로필을 설정합니다. 무부하 상태에서 기준 형상을 캡처한
후 Major/Minor diameter, Ovality 및 기준 대비 변형량을 기록할 수 있습니다.

PyQt와 OpenCV Qt 플러그인 충돌을 피하기 위해
`opencv-python-headless`를 사용합니다.

## 커밋 전 검사

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
git status --short
git diff --check
```

실제 하드웨어 시험 전에는 비상정지, 리미트, SSCNET 배선, 앰프 전원과 축
번호를 반드시 확인하십시오.
