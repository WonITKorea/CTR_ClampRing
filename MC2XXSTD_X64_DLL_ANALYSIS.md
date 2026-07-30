# mc2xxstd_x64.dll 분석 결과

분석일: 2026-07-30

대상:
`C:\Users\CTR\Desktop\CTR_SealRing_Simul-main\mc2xxstd_x64.dll`

## 결론

이 파일은 손상된 DLL이 아니라 Mitsubishi MR-MC2xx용 x64 API 버전 2.2입니다.
프로젝트의 `mc2xx.zip` 안에 있는 DLL과 바이트 단위로 동일합니다.

그러나 이것은 사용자 모드 API DLL일 뿐이며, 대응하는 SYS/INF 커널 드라이버가
포함된 전체 Utility2 패키지가 아닙니다. 현재 설치된 Utility2 1.80 드라이버
스택에서 이 DLL만 교체해도 PCIe 연결 문제는 해결되지 않습니다.

## 파일 식별

- 형식: PE32+ AMD64 DLL
- File/Product version: 2.2.0.0
- Company: MITSUBISHI ELECTRIC CORPORATION
- 크기: 463,360 bytes
- PE 빌드 시각: 2018-05-22 09:42:17 UTC
- SHA-256:
  `036DD7DED05946911D775051F7A699BEEBF4BC9BDB51DA4C0936AACAAD3CC8AD`
- `mc2xx.zip` 내부 DLL과 일치: 예
- Authenticode: 미서명
- Windows Defender 사용자 지정 검사: 위협 없음

미서명 파일이므로 Mitsubishi 서명 인증서로 발행자를 암호학적으로 검증할 수는
없습니다. 다만 버전 리소스, PE 구조, API, 헤더/LIB 묶음 및 기존 Mitsubishi
DLL과의 구조적 일관성은 확인됐습니다.

## 설치된 1.8 DLL과 비교

| 항목 | 설치된 DLL | 검사 대상 DLL |
|---|---:|---:|
| API 버전 | 1.8.0.0 | 2.2.0.0 |
| 크기 | 445,440 bytes | 463,360 bytes |
| 내보낸 함수 | 340개 | 343개 |
| PE 빌드 시각 | 2015-08-07 | 2018-05-22 |

2.2에서 추가된 export:

- `sscGet2portTopAddressEx`
- `sscGetMonitorEx`
- `sscSaveDumpFile`

1.8의 함수명 340개는 2.2에도 모두 존재합니다. 이름으로 함수를 찾는 현재
프로젝트에는 필요한 함수가 모두 있습니다. 다만 공통 함수 중 205개의 ordinal이
바뀌었으므로 ordinal로 함수를 가져오는 다른 프로그램은 호환을 보장할 수 없습니다.

2.2 헤더에서는 일부 구조체 멤버와 상태 비트 정의도 변경됐습니다. 따라서 2.2
DLL을 사용할 때는 ZIP에 포함된 2.2 헤더와 LIB를 한 세트로 사용해야 합니다.

## 내부 동작

DLL은 별도 `WinDriver1660` DLL을 import하지 않습니다. Jungo WinDriver 사용자
모드 코드가 정적으로 포함되어 있으며 다음 기존 Mitsubishi 장치에 직접 연결합니다.

`\\.\mc2xxcmn`

따라서 DriverWizard가 만든 `WinDriver1660` 장치와 자동 호환되지 않습니다.

## 안전 호출 시험

실행한 호출:

- DLL 로드 및 export 확인
- `sscOpen(0)`
- 성공한 경우에만 `sscClose`

시스템 시작, 축 제어, MMIO, 인터럽트, DMA 명령은 실행하지 않았습니다.

결과:

1. DLL 로드 성공
2. 현재 프로젝트에 필요한 이름 기반 API 해석 성공
3. `\\.\mc2xxcmn` 열기 성공
4. 초기 드라이버 통신 성공
5. PCI 장치 `VEN_10BA / DEV_0624` 검색 요청 도달
6. 검색 단계에서 커널 드라이버가 내부 오류
   `0x20000009 (No valid license)` 반환
7. 공개 API가 이를
   `0x00021010 (Position board not found)`으로 반환

설치된 1.8 DLL도 같은 단계와 같은 내부 오류로 실패합니다.

## 판정

- 파일 손상: 아님
- x64 아키텍처 오류: 아님
- API export 누락: 아님
- `mc2xx.zip`과 불일치: 아님
- 악성코드 탐지: 없음
- DLL 단독 교체로 PCIe 해결: 불가
- DriverWizard `WinDriver1660`과 직접 호환: 불가
- 필요한 조치: Utility2 3.50 이상 전체 정식 패키지의 일치하는
  API/PCIe/common 드라이버 설치

## 현재 설치된 드라이버 파일명

PCIe 보드에 실제로 바인딩된 서비스와 파일:

- 서비스: `Mc2xxCmn`
- 파일: `C:\Windows\System32\drivers\mc2xxcmn.sys`
- 설명: MR-MC2XX Common Device Driver (x64)
- 버전: 11.5.0.0
- 서명: Mitsubishi Electric 유효 서명

함께 실행 중인 MR-MC2XX 제어 드라이버:

- 서비스: `mc2xx`
- 파일: `C:\Windows\System32\drivers\mc2xx.sys`
- 설명: MR-MC2XX Device Driver (x64)
- 버전: 1.2.0.0
- 서명: Mitsubishi Electric 유효 서명

연관 설치 파일:

- `mc2xx.inf`
- `mc2xx.cat`
- `mc2xxcmn.inf`
- `mc2xxcmn.cat`

Windows가 붙인 게시 이름은 각각 `oem8.inf`와 `oem7.inf`이지만, 원래 패키지
파일명은 `mc2xx.inf`와 `mc2xxcmn.inf`입니다.

DriverWizard의 `windrvr1660.sys`는 별도 범용 Jungo 드라이버이며 현재 MR-MC2xx
PCIe 장치에는 바인딩되어 있지 않습니다.

최신 파일을 요청할 때는 `mc2xxcmn.sys`만 요청하지 말고 다음 제품명을 사용해야
합니다.

`MRZJW3-MC2-UTL Position Board Utility2 Ver.3.50 or later — complete x64
driver/API installation package for MR-MC240N`
