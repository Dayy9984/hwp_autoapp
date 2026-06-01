// 라이센스 verify 토큰 + device_id 를 Python 측에 푸시.
// 호출 시점:
//   - 라이센스 활성화 직후
//   - 30분마다 (토큰 갱신 + Python 재시작 후 재등록)
//   - python:start 직후

import { app } from 'electron'
import { getPythonBridge } from './python-bridge'
import { getAgentBridge } from './agent-bridge'
import { licenseBridge } from './license-bridge'
import { getDeviceInfo } from './license-device-id'

const REFRESH_INTERVAL_MS = 30 * 60 * 1000  // 30분

let pushTimer: NodeJS.Timeout | null = null

export async function pushBetaCredsNow(): Promise<void> {
  try {
    const token = (licenseBridge as any).pickAuthToken?.() as string | null
    if (!token) return
    const deviceId = getDeviceInfo().device_id
    const payload = { token, device_id: deviceId }
    // hwp_com_process (PythonBridge) + agent_process (AgentBridge) 둘 다 별개 Python process — 각각 푸시.
    const hwpBridge: any = getPythonBridge(app.getAppPath())
    if (hwpBridge) {
      await hwpBridge.call('beta:set_creds', payload).catch(() => {})
    }
    const agentBridge: any = getAgentBridge(app.getAppPath())
    if (agentBridge) {
      await agentBridge.call('beta:set_creds', payload).catch(() => {})
    }
  } catch {
    // silent — telemetry 보조 기능이라 본 흐름 영향 X
  }
}

export function startBetaCredsPushLoop(): void {
  if (pushTimer) return
  pushBetaCredsNow().catch(() => {})
  pushTimer = setInterval(() => pushBetaCredsNow().catch(() => {}), REFRESH_INTERVAL_MS)
  if (pushTimer.unref) pushTimer.unref()
}
