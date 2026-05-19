; Inserty AI - Custom NSIS Installer Script
; 상업용 소프트웨어 - Proprietary License

; ============================================================
; 설치 UI 설정
; ============================================================

!macro customHeader
  ; NSIS 설치 마법사 UI 활성화 (electron-builder.protected.json에서 제어)
  ; oneClick: false → 설치 마법사 UI 표시

  ; 설치 완료 메시지
  !define MUI_FINISHPAGE_TITLE "Inserty AI 설치 완료"
  !define MUI_FINISHPAGE_TEXT "Inserty AI이 성공적으로 설치되었습니다.$\r$\n$\r$\n바탕화면 또는 시작 메뉴에서 실행하세요."
!macroend

!macro preInit
  ; 업그레이드 감지용 플래그 파일 생성
  FileOpen $0 "$TEMP\inserty-upgrading.flag" w
  FileWrite $0 "upgrading"
  FileClose $0
!macroend

!macro customInstall
  ; 설치 완료 후 업그레이드 플래그 정리
  Delete "$TEMP\inserty-upgrading.flag"
  DetailPrint "Inserty AI 설치 완료!"
!macroend

; ============================================================
; 삭제 로직
; ============================================================

!macro customUnInstall
  DetailPrint "Inserty AI 삭제 중..."

  ; 0. 실행 중 프로세스 정리 (파일 잠금 해제)
  DetailPrint "실행 중인 Inserty AI 종료 중..."
  nsExec::ExecToLog 'taskkill /F /T /IM "Inserty AI.exe"'
  nsExec::ExecToLog 'taskkill /F /T /IM "inserty_python.exe"'
  nsExec::ExecToLog 'taskkill /F /T /IM "inserty_agent.exe"'
  nsExec::ExecToLog 'taskkill /F /T /IM "inserty_file_reader.exe"'
  nsExec::ExecToLog 'taskkill /F /T /IM "hwp_window_monitor.exe"'
  Sleep 1500

  ; ── 업그레이드 vs 완전삭제 분기 ─────────────────────────
  ; 플래그 파일 체크
  IfFileExists "$TEMP\inserty-upgrading.flag" 0 check_silent
    ; 플래그 파일 발견 → 업그레이드
    Delete "$TEMP\inserty-upgrading.flag"
    DetailPrint "업그레이드 모드: 설치 파일 정리 후 재설치"
    Goto clean_install_dir

  check_silent:
  ${If} ${Silent}
    ; Silent 모드 → 업그레이드 (자동 업데이트)
    DetailPrint "업그레이드 모드(Silent): 설치 파일 정리 후 재설치"
    Goto clean_install_dir
  ${EndIf}

  ; ── 사용자가 직접 삭제 (제어판 등) ─────────────────────
  DetailPrint "완전 삭제 모드"

  ; 1. 설치 디렉토리 전체 삭제 ($INSTDIR = 사용자 지정 경로 포함)
  DetailPrint "설치 파일 삭제 중..."
  RMDir /r /REBOOTOK "$INSTDIR"

  ; 2. LocalAppData 삭제 (Electron 캐시, GPU 캐시 등)
  DetailPrint "로컬 캐시 삭제 중..."
  RMDir /r "$LOCALAPPDATA\Inserty AI"

  ; 3. Roaming AppData 삭제 (DB, 로그, 설정, 프로젝트 파일 등)
  DetailPrint "사용자 데이터 삭제 중..."
  RMDir /r "$APPDATA\Inserty AI"

  ; 4. 임시 파일 삭제
  DetailPrint "임시 파일 삭제 중..."
  RMDir /r "$TEMP\Inserty AI"
  RMDir /r "$TEMP\inserty-*"

  ; 5. 레지스트리 정리
  DeleteRegKey HKCU "Software\Inserty AI"
  DeleteRegKey HKLM "Software\Inserty AI"

  DetailPrint "Inserty AI 삭제 완료!"
  MessageBox MB_OK "Inserty AI가 성공적으로 삭제되었습니다.$\r$\n$\r$\nDB, 로그, 설정, 프로젝트 파일이 모두 제거되었습니다.$\r$\n$\r$\n참고: OpenAI Vector Store의 RAG 데이터는 서버에 남아있으며,$\r$\n필요 시 OpenAI 대시보드에서 삭제하세요."
  Goto uninstall_done

  ; ── 업그레이드: 설치 디렉토리만 정리 (DB/설정 보존) ──────
  clean_install_dir:
    ; 설치 디렉토리 내 프로그램 파일 전체 삭제 (잔여 파일 방지)
    ; $INSTDIR은 사용자가 지정한 경로 또는 기본 경로
    DetailPrint "기존 설치 파일 정리 중..."

    ; Python 바이너리 폴더 완전 삭제 (이전 버전 잔여 파일 방지 핵심)
    RMDir /r "$INSTDIR\resources\python"

    ; ASAR 및 Electron 리소스 삭제
    RMDir /r "$INSTDIR\resources\app.asar.unpacked"
    Delete "$INSTDIR\resources\app.asar"

    ; Electron 바이너리 삭제 (새 버전으로 교체)
    Delete "$INSTDIR\Inserty AI.exe"
    Delete "$INSTDIR\*.dll"
    Delete "$INSTDIR\*.pak"
    Delete "$INSTDIR\*.bin"
    Delete "$INSTDIR\*.dat"
    Delete "$INSTDIR\*.json"
    RMDir /r "$INSTDIR\locales"

    DetailPrint "기존 파일 정리 완료 — 새 버전 설치 준비"

  uninstall_done:
!macroend
