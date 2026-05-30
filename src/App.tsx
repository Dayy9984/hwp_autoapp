import { useCallback, useEffect, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { ChatCanvas } from './components/ChatCanvas'
import { ChatInput } from './components/ChatInput'
import { FolderView } from './components/FolderView'
import { ToolsImportView } from './components/ToolsImportView'
import { ChatSearchModal } from './components/modals/ChatSearchModal'
import { CreateFolderModal } from './components/modals/CreateFolderModal'
import { AddFolderFileModal } from './components/modals/AddFolderFileModal'
import { SettingsModal } from './components/modals/SettingsModal'
import { TallyEmbedModal } from './components/modals/TallyEmbedModal'
import { useBetaSurveyStore } from './stores/beta-survey-store'
import { IS_BETA } from './config/beta'
import { NotificationModal, Notification } from './components/modals/NotificationModal'
import { SignatureToolModal } from './components/modals/SignatureToolModal'
import { ToolEditModal } from './components/modals/ToolEditModal'
import { PromptCustomModal } from './components/modals/PromptCustomModal'
import { PromptFullOverrideModal } from './components/modals/PromptFullOverrideModal'
import { ChatOptionsPopover, FolderMovePopover, FolderOptionsPopover } from './components/popovers'
import { ProfilePopover } from './components/popovers/ProfilePopover'
import { SignatureOptionsPopover } from './components/popovers/SignatureOptionsPopover'
import { ToolOptionsPopover } from './components/popovers/ToolOptionsPopover'
import { FileUploadPopover } from './components/popovers/FileUploadPopover'
import { SplashScreen } from './components/SplashScreen'
import { ErrorBoundary } from './components/ErrorBoundary'
import { TrackChangeButtons } from './components/TrackChangeButtons'
import { useUIStore } from './stores/ui-store'
import { useDocumentStore } from './stores/document-store'
import { useChatStore } from './stores/chat-store'
import { useFolderStore } from './stores/folder-store'
import { useSettingsStore } from './stores/settings-store'
import { useAuthStore } from './stores/auth-store'
import { useToolStore } from './stores/tool-store'
import { getProjectChatOrder, loadChatOrderSettings } from './utils/chat-order-storage'
import './dev-utils/test-data' // Import dev utilities for browser console access
import { LicenseGate, LicenseState } from './components/LicenseGate'
import { UpdateProgressModal } from './components/modals/UpdateProgressModal'

// 진행률 전용 창인지 판별 (main process 가 hash=#update-progress 로 띄움).
function isUpdateProgressRoute(): boolean {
  if (typeof window === 'undefined') return false
  return window.location.hash === '#update-progress'
}

// 알림 우선순위 (높을수록 중요 - 낮은 우선순위 알림이 높은 우선순위를 덮어쓸 수 없음)
const NOTIFICATION_PRIORITY: Record<string, number> = {
  'critical-update': 100,
  'version-update': 80,
}

// bridge 응답 + 캐시 키를 LicenseGate state로 정규화 (license_key는 캐시에서 보충)
function mapLicenseStatus(raw: any, cachedKey: string | null): LicenseState {
  if (!raw) return { state: 'no_license' }
  if (['expired', 'leaked', 'revoked', 'device_limit_reached'].includes(raw.state)) {
    return { ...raw, license_key: cachedKey }
  }
  return raw
}

// 업데이트 진행률 전용 윈도우 렌더링 — main process 가 BrowserWindow 를 열 때
// hash=#update-progress 로 indexHtml 을 로드. 이 경우 다른 UI 는 다 빼고 진행률만 보여줌.
function UpdateProgressApp() {
  // 진행률 모달이 자체적으로 downloaded 이벤트 받으면 자동 install 트리거.
  return (
    <UpdateProgressModal
      open
      onClose={() => { /* 사용자가 닫을 수 없음 — 자동 진행 */ }}
    />
  )
}

// 베타 설문 모달 마운트 — store 가 currentModal 을 채우면 자동 노출.
function BetaSurveyMount() {
  const currentModal = useBetaSurveyStore((s) => s.currentModal)
  const closeModal = useBetaSurveyStore((s) => s.closeModal)
  if (!currentModal) return null
  return (
    <TallyEmbedModal
      formKey={currentModal.formKey}
      title={currentModal.title}
      onClose={closeModal}
    />
  )
}

function App() {
  // 업데이트 진행률 전용 창이면 메인 UI / 라이센스 / Python 전부 건너뜀.
  if (isUpdateProgressRoute()) return <UpdateProgressApp />

  const [licenseStatus, setLicenseStatus] = useState<LicenseState>({ state: 'loading' })
  const [showSplash, setShowSplash] = useState(true)
  const [pythonReady, setPythonReady] = useState(false)
  const [appStatus, setAppStatus] = useState<{ message: string; progress?: number } | null>(null)
  const [notification, setNotification] = useState<Notification | null>(null)
  const notificationPriorityRef = useRef(0)
  const updateCheckRef = useRef(false)
  const initialCheckRef = useRef(false)
  const { isAuthenticated } = useAuthStore()
  const {
    theme,
    setTheme,
    currentView,
    clearNavigationHistory,
    openModal,
    closeModal,
    selectedFolderId,
  } = useUIStore()
  const { clearDocumentState } = useDocumentStore()
  const { currentChatId } = useChatStore()
  const { restoreFolder, getChatFolder } = useFolderStore()

  // 현재 채팅이 속한 프로젝트 ID 가져오기
  const currentChatFolder = currentChatId ? getChatFolder(currentChatId) : null
  const currentChatFolderId = currentChatFolder?.id

  /**
   * 우선순위 기반 알림 표시 헬퍼
   * - 높은 우선순위 알림은 낮은 우선순위를 덮어씀
   * - 낮은 우선순위 알림은 높은 우선순위가 표시 중이면 무시됨
   * - 업데이트 알림이 OpenAI 키 알림 등에 의해 덮어쓰이는 문제 방지
   */
  const showNotification = useCallback((notif: Notification) => {
    const priority = NOTIFICATION_PRIORITY[notif.type] ?? 50
    if (priority >= notificationPriorityRef.current) {
      notificationPriorityRef.current = priority
      setNotification(notif)
      openModal('notification')
    }
  }, [openModal])

  const clearNotification = useCallback(() => {
    notificationPriorityRef.current = 0
    setNotification(null)
    closeModal()
  }, [closeModal])

  // 시작 시 Python 준비 상태 확인 (SplashScreen 바이패스)
  useEffect(() => {
    if (initialCheckRef.current) return
    initialCheckRef.current = true

    // Python이 이미 준비됐으면 SplashScreen 즉시 건너뜀
    window.electronAPI.python.isReady().then((result: { ready: boolean }) => {
      if (result.ready) {
        console.log('[App] Python already ready - skipping SplashScreen')
        setPythonReady(true)
        setShowSplash(false)
      }
    }).catch((err: Error) => {
      console.error('[App] Failed to check Python ready status:', err)
    })
  }, [])

  // Python 준비 완료 이벤트 리스너
  useEffect(() => {
    const cleanup = window.electronAPI.onPythonReady(() => {
      console.log('[App] Python ready')
      setPythonReady(true)
    })
    return cleanup
  }, [])

  useEffect(() => {
    const cleanup = window.electronAPI.onAppStatus((message, progress) => {
      setAppStatus({ message, progress })
    })
    return cleanup
  }, [])

  // Apply theme on mount and when theme changes
  useEffect(() => {
    // Apply theme class to body
    if (theme === 'dark') {
      document.body.classList.add('dark')
    } else {
      document.body.classList.remove('dark')
    }
  }, [theme])

  // Clear navigation history when entering main app after authentication
  useEffect(() => {
    if (isAuthenticated) {
      clearNavigationHistory()
    }
  }, [isAuthenticated, clearNavigationHistory])


  // 자동 업데이트 상태 리스너
  useEffect(() => {
    if (!isAuthenticated) return

    const api = typeof window !== 'undefined' ? window.electronAPI : undefined
    if (!api?.update?.onStatus) return

    const unsubscribe = api.update.onStatus((status) => {
      console.log('[App] Update status:', status.status, status.info?.version ?? '', status.isCritical ? '(CRITICAL)' : '')

      // 'available' 시점에 알림 표시. 클릭하면 startInstallFlow 가:
      //   1) 메인 창 닫음
      //   2) 별도 progress window 띄움
      //   3) 다운로드 시작 → 진행률 표시 → 자동 설치 → 새 앱 실행
      // 단일 흐름.
      if (status.status === 'available' && status.info) {
        const isCritical = status.isCritical ?? false
        const releaseNotes = status.releaseNotes ?? ''
        const title = isCritical ? '필수 업데이트' : '새 버전 사용 가능'
        const content = isCritical
          ? `중요한 업데이트가 있습니다.\n버전 ${status.info.version}(으)로 업데이트해야 합니다.${releaseNotes ? `\n\n${releaseNotes}` : ''}`
          : `새 버전 ${status.info.version}이(가) 출시되었습니다.\n지금 업데이트하시겠습니까?${releaseNotes ? `\n\n${releaseNotes}` : ''}`
        showNotification({
          id: `update-available-${status.info.version}`,
          type: isCritical ? 'critical-update' : 'version-update',
          title,
          content,
          actionLabel: '업데이트',
          isCritical,
        })
      }
    })

    // 리스너 등록 후, 놓친 이벤트가 있는지 확인 (앱 시작 시 renderer보다 먼저 발생한 이벤트 복구)
    if (api.update?.getLastStatus) {
      api.update.getLastStatus().then((result) => {
        if (result?.success && result.status) {
          console.log('[App] Recovering missed update status:', result.status.status)
          // 이미 리스너로 처리된 상태가 아닌 경우에만 처리
          if (result.status.status === 'available') {
            const recoveredStatus = result.status
            if (recoveredStatus.info) {
              const isCritical = recoveredStatus.isCritical ?? false
              const releaseNotes = recoveredStatus.releaseNotes ?? ''
              const title = isCritical ? '필수 업데이트' : '새 버전 사용 가능'
              const content = isCritical
                ? `중요한 업데이트가 있습니다.\n버전 ${recoveredStatus.info.version}(으)로 업데이트해야 합니다.${releaseNotes ? `\n\n${releaseNotes}` : ''}`
                : `새 버전 ${recoveredStatus.info.version}이(가) 출시되었습니다.\n지금 업데이트하시겠습니까?${releaseNotes ? `\n\n${releaseNotes}` : ''}`
              showNotification({
                id: `update-available-${recoveredStatus.info.version}`,
                type: isCritical ? 'critical-update' : 'version-update',
                title, content,
                actionLabel: '업데이트',
                isCritical,
              })
            }
          }
        }
      }).catch(err => {
        console.error('[App] Failed to get last update status:', err)
      })
    }

    return () => {
      unsubscribe()
    }
  }, [isAuthenticated, showNotification])

  // 프로젝트 로드 (앱 시작 시 DB에서 복원)
  useEffect(() => {
    if (!isAuthenticated) return

    // HWP 바인딩 활성화 (로그인 후에만)
    window.electronAPI.hwp.enableBinding().then(result => {
      if (result.success) {
        console.log('[App] HWP binding enabled')
      } else {
        console.error('[App] Failed to enable HWP binding:', result.error)
      }
    })

    // Agent 프로세스 사전 실행 (로그인 직후)
    if (window.electronAPI.agent?.start) {
      window.electronAPI.agent.start().then(result => {
        if (result.success) {
          console.log('[App] Agent bridge started')
        } else {
          console.error('[App] Failed to start agent bridge')
        }
      })
    }

    // Log app start
    window.electronAPI.log.usage({
      eventType: 'app_start',
      eventData: {
        timestamp: new Date().toISOString()
      }
    }).catch(err => {
      console.error('[App] Failed to log app start:', err)
    })

    void useSettingsStore.getState().hydrateFromDb()
    void useUIStore.getState().hydrateFromDb()
    void useChatStore.getState().hydrateFromDb()
    void useToolStore.getState().hydrateToolsFromDb()

    const loadProjects = async () => {
      try {
        await loadChatOrderSettings()
        const result = await window.electronAPI.invoke('project:list')
        if (result.success && result.data) {
          // 디버깅: 백엔드 데이터 구조 확인
          console.log('[App] Raw backend data:', JSON.stringify(result.data.projects, null, 2))

          // 모든 프로젝트를 스토어에 복원 (backend → frontend 형식 변환)
          result.data.projects.forEach((project: any) => {
            // name 타입 방어적 체크
            const projectName = typeof project.name === 'string'
              ? project.name
              : (typeof project.name === 'object' && project.name?.name)
                ? project.name.name
                : '이름 없음'

            // Backend Project → Frontend Folder 변환
            const orderChatBindings = (
              bindings: string[],
              preferredOrder: string[]
            ) => {
              if (!preferredOrder.length) return bindings
              const seen = new Set(preferredOrder)
              const ordered = preferredOrder.filter((id) => bindings.includes(id))
              const remaining = bindings.filter((id) => !seen.has(id))
              return [...ordered, ...remaining]
            }

            const chatBindings = orderChatBindings(
              project.chatBindings || [],
              getProjectChatOrder(project.id)
            )

            const folderData = {
              id: project.id,
              name: projectName,
              files: chatBindings.map((chatId: string) => ({
                id: chatId,
                chatId,
                addedAt: project.createdAt || Date.now()
              })),
              templatePairs: project.templatePairs || [],
              projectFiles: (project.files || []).map((file: any) => ({
                id: file.id,
                path: file.path,
                name: file.name,
                type: file.type,
                size: file.size,
                indexStatus: file.indexStatus,
                createdAt: file.addedAt || Date.now()  // Backend addedAt → Frontend createdAt
              })),
              createdAt: project.createdAt || Date.now(),
              updatedAt: project.updatedAt || Date.now()
            }

            console.log('[App] Loaded project:', folderData.id, folderData.name)
            restoreFolder(folderData)
          })
          console.log(`[App] Loaded ${result.data.projects.length} projects from DB`)
        }
      } catch (error) {
        console.error('[App] Failed to load projects:', error)
      }
    }

    loadProjects()
  }, [isAuthenticated, restoreFolder])


  // Update check (once after login / auto-login)
  useEffect(() => {
    if (!isAuthenticated) return
    if (updateCheckRef.current) return
    updateCheckRef.current = true

    const checkUpdates = async () => {
      const api = typeof window !== 'undefined' ? window.electronAPI : undefined
      if (!api?.update?.check) return

      try {
        const settingsResult = await api.invoke('settings:getAll')
        const settings = settingsResult?.data?.settings ?? {}
        const now = Date.now()
        const lastCheckAt = typeof settings.last_update_check_at === 'number'
          ? settings.last_update_check_at
          : 0
        const minIntervalMs = 6 * 60 * 60 * 1000

        if (now - lastCheckAt < minIntervalMs) {
          return
        }

        void api.invoke('settings:set', {
          key: 'last_update_check_at',
          value: now,
          type: 'number'
        })

        // 업데이트 체크 트리거 — 알림은 auto-update:status IPC 리스너가 단일 처리
        // (별도 version-update-* 알림을 만들면 update-available-* 와 중복 팝업 발생)
        await api.update.check()
      } catch (error) {
        console.error('[App] Update check failed:', error)
      }
    }

    void checkUpdates()
  }, [isAuthenticated])

  // OpenAI API 키 알림은 제거 — Codex 로그인 시에도 잘못 표시되는 문제,
  // 그리고 사용자 경험상 불필요한 시작 모달을 없앤다.
  // (채팅 전송 시점의 ensureOpenAiKey는 유지 — codex 모드는 자동 통과)

  // HWP 윈도우 이벤트 리스너 (문서 연결/해제 감지)
  useEffect(() => {
    if (!isAuthenticated) return

    const unsubscribeLost = window.electronAPI.on('hwp:windowLost', () => {
      console.log('[App] HWP window lost - clearing document state')
      clearDocumentState()
    })

    const unsubscribeBound = window.electronAPI.on('hwp:windowBound', (data: any) => {
      console.log('[App] HWP window bound - refreshing document info', data)
      // 문서 정보 및 선택 영역 갱신
      const { refreshPageInfo, refreshSelectionInfo } = useDocumentStore.getState()
      refreshPageInfo()
      refreshSelectionInfo()
    })

    return () => {
      unsubscribeLost()
      unsubscribeBound()
    }
  }, [isAuthenticated, clearDocumentState])

  // 라이센스 게이트 — 앱 시작 시 초기 상태 가져오고 24h heartbeat 등록
  useEffect(() => {
    const api = (window as any).electronAPI?.license
    if (!api) return
    let cancelled = false
    const sync = async () => {
      const raw = await api.getInitialStatus()
      const cachedKey = await api.getCachedKey()
      if (!cancelled) setLicenseStatus(mapLicenseStatus(raw, cachedKey))
    }
    sync()
    const interval = setInterval(async () => {
      const raw = await api.verify()
      const cachedKey = await api.getCachedKey()
      if (!cancelled) setLicenseStatus(mapLicenseStatus(raw, cachedKey))
    }, 24 * 3600 * 1000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  const handleLicenseActivate = useCallback(async (key: string) => {
    const api = (window as any).electronAPI?.license
    if (!api) return
    const raw = await api.activate(key)
    const cachedKey = await api.getCachedKey()
    // 신규 expired/leaked/revoked 활성화 시도는 cache 가 없어 license_key 가 안 보임.
    // 사용자가 카카오 문의 시 "내가 입력한 키" 를 알 수 있도록 입력 키를 fallback.
    const displayKey = cachedKey || key.trim().toUpperCase()
    setLicenseStatus(mapLicenseStatus(raw, displayKey))
  }, [])

  const handleLicenseRetry = useCallback(async () => {
    const api = (window as any).electronAPI?.license
    if (!api) return
    const raw = await api.verify()
    const cachedKey = await api.getCachedKey()
    setLicenseStatus(mapLicenseStatus(raw, cachedKey))
  }, [])

  // 라이센스가 ok 또는 offline_grace가 아니면 차단 화면 표시
  const licenseOk = licenseStatus.state === 'ok' || licenseStatus.state === 'offline_grace'
  if (!licenseOk) {
    return (
      <LicenseGate
        status={licenseStatus}
        onActivate={handleLicenseActivate}
        onRetry={handleLicenseRetry}
      />
    )
  }

  // Show splash screen on app start
  if (showSplash) {
    return (
      <SplashScreen
        pythonReady={pythonReady}
        statusMessage={appStatus?.message}
        progress={appStatus?.progress}
        onComplete={() => setShowSplash(false)}
      />
    )
  }

  return (
    <ErrorBoundary>
      <div className="flex h-full w-full max-w-full overflow-hidden">
        {/* 사이드바 */}
        <Sidebar />

        {/* 메인 영역 */}
        <div className="relative flex flex-col flex-1 overflow-hidden min-w-0" style={{ backgroundColor: 'var(--bg)' }}>
          {currentView === 'chat' ? (
            <ErrorBoundary>
              {/* 채팅 캔버스 */}
              <ChatCanvas />

              {/* 채팅 입력 (absolute 포지션) - v6.2: 현재 채팅의 프로젝트 ID 전달 */}
              <ChatInput folderId={currentChatFolderId} />
            </ErrorBoundary>
          ) : currentView === 'folder' ? (
            <ErrorBoundary>
              <FolderView />
            </ErrorBoundary>
          ) : currentView === 'tools-import' ? (
            <ErrorBoundary>
              <ToolsImportView />
            </ErrorBoundary>
          ) : null}
        </div>

        {/* 모달들 */}
        <ChatSearchModal />
        <CreateFolderModal />
        <AddFolderFileModal />
        <SettingsModal />
        {IS_BETA && <BetaSurveyMount />}
        <NotificationModal
          notification={notification}
          onClose={clearNotification}
          onDontShowAgain={(id) => {
            // Already handled in NotificationModal component
            console.log('Notification dismissed:', id)
          }}
          onAction={async (item) => {
            if (item.id === 'openai-key-required') {
              setTimeout(() => {
                openModal('settings', { tab: 'ai' })
              }, 0)
              return
            }
            const api = typeof window !== 'undefined' ? window.electronAPI : undefined
            if (!api) return

            // 업데이트 알림 → 메인 창 닫고 별도 progress 창 열기 + 다운로드 시작.
            // downloaded 이벤트 시 progress 창이 자동으로 install 트리거 → 추가 클릭 없이 완료.
            if (item.id.startsWith('update-available-') || item.id.startsWith('version-update-')) {
              const startFlow = (api.update as any)?.startInstallFlow
              if (typeof startFlow === 'function') {
                const r = await startFlow()
                if (!r?.success) console.error('[App] startInstallFlow failed:', r?.error)
              } else if (api.update?.download) {
                // 폴백 — 구 빌드 호환
                await api.update.download()
              }
              return
            }

            // 외부 URL이 있는 알림 (공지사항 등)
            if (item.actionUrl) {
              window.open(item.actionUrl, '_blank', 'noopener,noreferrer')
            }
          }}
        />
        <SignatureToolModal />
        <ToolEditModal />
        <PromptCustomModal />
        <PromptFullOverrideModal />

        {/* 팝오버들 */}
        <ChatOptionsPopover />
        <FolderMovePopover />
        <FolderOptionsPopover />
        <ProfilePopover />
        <SignatureOptionsPopover />
        <ToolOptionsPopover />
        <FileUploadPopover />


      </div>
    </ErrorBoundary>
  )
}

export default App

