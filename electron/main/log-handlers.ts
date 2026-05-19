// ============================================================
// log-handlers.ts
// auth/usage 로그 및 device_id IPC 핸들러
// ============================================================

import { ipcMain } from 'electron'
import { getLogService } from '../services/log-service'
import { getDeviceIdService } from '../services/device-id-service'

export function registerLogHandlers() {
  const logService = getLogService()
  const deviceIdService = getDeviceIdService()

  // log:auth - 인증 로그 전송
  ipcMain.handle('log:auth', async (_event, params) => {
    return await logService.logAuth(params)
  })

  // log:usage - 사용량 로그 전송
  ipcMain.handle('log:usage', async (_event, params) => {
    logService.enqueueUsage(params)
    return { success: true }
  })

  // device:getId - 디바이스 ID 가져오기
  ipcMain.handle('device:getId', async () => {
    try {
      const deviceId = await deviceIdService.getDeviceId()
      return { success: true, deviceId }
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : 'device_id_unavailable' }
    }
  })

  // device:getInfo - 디바이스 정보 가져오기
  ipcMain.handle('device:getInfo', async () => {
    try {
      const info = await deviceIdService.getDeviceInfo()
      return { success: true, data: info }
    } catch (error) {
      return { success: false, error: error instanceof Error ? error.message : 'device_info_unavailable' }
    }
  })

  // device:regenerateId - 디바이스 ID 재생성 (테스트/디버그용)
  ipcMain.handle('device:regenerateId', async () => {
    const deviceId = await deviceIdService.regenerateDeviceId()
    return { success: true, deviceId }
  })
}
