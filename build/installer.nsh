; NSIS 인스톨러 — 라이센스 키 입력 페이지 추가
; electron-builder.json의 nsis.include 옵션으로 통합

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"

Var Dialog
Var LicenseKeyLabel
Var LicenseKeyInput
Var LicenseKeyInfo
Var LicenseKeyValue

; ==================================================================
; 커스텀 매크로 — Welcome 다음에 키 입력 페이지 삽입
; ==================================================================
!macro customWelcomePage
  ; (electron-builder 기본 Welcome 페이지 호출)
!macroend

; 라이센스 키 입력 페이지를 ComponentSelection 직전에 추가
!macro customInit
  ; 빈 매크로 (initialize hook)
!macroend

; ==================================================================
; 키 입력 페이지
; ==================================================================
PageEx custom
  PageCallbacks LicenseKeyPageCreate LicenseKeyPageLeave
PageExEnd

Function LicenseKeyPageCreate
  !insertmacro MUI_HEADER_TEXT "라이센스 키 입력" "결제 후 발송된 라이센스 키를 입력하세요."

  nsDialogs::Create 1018
  Pop $Dialog
  ${If} $Dialog == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 12u "라이센스 키 (예: INSRT-XXXX-XXXX-XXXX-XXXX)"
  Pop $LicenseKeyLabel

  ${NSD_CreateText} 0 16u 100% 12u "$LicenseKeyValue"
  Pop $LicenseKeyInput
  ${NSD_SetTextLimit} $LicenseKeyInput 24

  ${NSD_CreateLabel} 0 36u 100% 36u "키가 없으신 경우 카카오톡 오픈채팅으로 문의해주세요:$\r$\nhttps://open.kakao.com/o/sSm9ZXei$\r$\n$\r$\n* 키 입력을 건너뛰면 앱 첫 실행 시 입력 화면이 표시됩니다."
  Pop $LicenseKeyInfo

  nsDialogs::Show
FunctionEnd

Function LicenseKeyPageLeave
  ${NSD_GetText} $LicenseKeyInput $LicenseKeyValue

  ; 비어 있으면 건너뛰기 허용 (앱 첫 실행 시 입력)
  ${If} $LicenseKeyValue == ""
    Return
  ${EndIf}

  ; 형식 검증 INSRT-XXXX-XXXX-XXXX-XXXX (24자)
  StrLen $0 $LicenseKeyValue
  ${If} $0 != 24
    MessageBox MB_ICONEXCLAMATION "라이센스 키는 24자입니다.$\r$\n형식: INSRT-XXXX-XXXX-XXXX-XXXX$\r$\n다시 확인해주세요. (비워두고 다음으로 가시면 앱 첫 실행 시 입력 가능합니다)"
    Abort
  ${EndIf}

  StrCpy $1 $LicenseKeyValue 5
  ${If} $1 != "INSRT"
    MessageBox MB_ICONEXCLAMATION "라이센스 키는 INSRT-로 시작해야 합니다.$\r$\n다시 확인해주세요."
    Abort
  ${EndIf}
FunctionEnd

; ==================================================================
; 설치 완료 시 키를 %APPDATA%\Inserty AI\.pending_license에 저장
; ==================================================================
!macro customInstall
  ${If} $LicenseKeyValue != ""
    ; APPDATA 디렉토리 생성
    CreateDirectory "$APPDATA\Inserty AI"
    ; 키 파일 저장 (앱 첫 실행 시 자동 활성화 후 삭제)
    FileOpen $0 "$APPDATA\Inserty AI\.pending_license" w
    FileWrite $0 "$LicenseKeyValue"
    FileClose $0
    DetailPrint "라이센스 키 저장 완료"
  ${EndIf}
!macroend

; ==================================================================
; 언인스톨 시 pending_license + license_cache 모두 제거
; ==================================================================
!macro customUnInstall
  Delete "$APPDATA\Inserty AI\.pending_license"
  Delete "$APPDATA\Inserty AI\.license_cache"
!macroend
