; NSIS 인스톨러 — oneClick 모드 보조.
; 라이센스 키 입력은 앱 첫 실행 시 LicenseGate KeyInputScreen 이 받음.
; 인스톨러는 별도 UI 없이 빠르게 설치 + 자동 실행 (electron-builder oneClick=true).
;
; 시작 직후 짧은 splash bitmap 으로 브랜드 인상 제공.

; ==================================================================
; preInit — installer 시작 직후 splash 표시 (1.6s)
; ==================================================================
!macro customInit
  ${If} ${Silent}
    ; 자동 업데이트 silent 모드는 splash 표시 안 함 (UX 방해 차단)
  ${Else}
    InitPluginsDir
    File /oname=$PLUGINSDIR\installer-splash.bmp "${BUILD_RESOURCES_DIR}\installer-splash.bmp"
    ; NSIS 표준 splash plugin (NSIS 3.x 내장).
    ; "splash::show DELAY_MS BMP_PATH" — DELAY_MS 만큼 BMP 표시 후 자동 종료.
    splash::show 1600 $PLUGINSDIR\installer-splash
    Pop $0
  ${EndIf}
!macroend

; ==================================================================
; customInstall — 파일 복사 끝나고 호출. VC++ Redistributable 확인/설치.
;
; 검사 로직:
;   1) HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64\Installed 가 1 이면 skip
;   2) 누락이면 번들된 vc_redist.x64.exe 실행 (/install /quiet /norestart)
;      → vcredist 자체가 admin 필요해서 UAC 한 번 뜸 (첫 설치 사용자만)
;      → 이미 깔린 사용자는 검사 통과 후 즉시 패스 (UAC 안 뜸)
; ==================================================================
!macro customInstall
  ${If} ${Silent}
    ; 자동 업데이트 silent 흐름에서는 이미 설치된 상태일 것이므로 skip
  ${Else}
    ; 64-bit 레지스트리 뷰로 강제 (NSIS 기본은 WOW64 redirect)
    SetRegView 64
    ReadRegDWORD $0 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Installed"
    SetRegView default

    ${If} $0 == 1
      DetailPrint "Visual C++ Redistributable 이미 설치됨 — skip"
    ${Else}
      DetailPrint "Visual C++ Redistributable 설치 중... (1회만 수행)"
      InitPluginsDir
      File /oname=$PLUGINSDIR\vc_redist.x64.exe "${BUILD_RESOURCES_DIR}\vc_redist.x64.exe"
      ; /quiet: UI 없음 (UAC 만 뜸), /norestart: 재시작 안 묻기
      ExecWait '"$PLUGINSDIR\vc_redist.x64.exe" /install /quiet /norestart' $1
      ; 0 = 성공, 1638 = 더 최신 버전 이미 설치됨 (성공으로 간주), 3010 = 성공 (재시작 필요)
      DetailPrint "Visual C++ Redistributable 설치 종료 코드: $1"
      Delete "$PLUGINSDIR\vc_redist.x64.exe"
    ${EndIf}
  ${EndIf}
!macroend

; ==================================================================
; 언인스톨 시 라이센스 캐시 처리.
;
; ${Silent} 분기:
;   - Silent uninstaller 호출 = 업데이트/재설치 흐름 (NSIS 가 새 Setup.exe 실행 시
;     구 uninstaller 를 silent 로 먼저 돌림). cache 보존 필수 — 안 그러면 매 업데이트마다
;     라이센스 재입력 강제됨.
;   - Non-silent uninstaller 호출 = 사용자가 Programs and Features 에서 명시적으로 제거.
;     "완전히 지우고 싶다" 는 의도 → cache 삭제하여 다음 설치 시 새 라이센스 입력 가능.
!macro customUnInstall
  ${IfNot} ${Silent}
    Delete "$APPDATA\Inserty AI\.pending_license"
    Delete "$APPDATA\Inserty AI\.license_cache"
  ${EndIf}
!macroend
