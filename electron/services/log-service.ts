// ============================================================
// log-service.ts
// ============================================================

interface LogAuthParams {
  success: boolean
  reason?: string
  ipHash?: string
  userAgent?: string
  metadata?: Record<string, unknown>
}

interface LogUsageParams {
  eventType: string
  eventData?: Record<string, unknown>
  sessionId?: string
}

export class LogService {
  async logAuth(_params: LogAuthParams): Promise<{ success: boolean; error?: string }> {
    return { success: true }
  }

  async logUsage(_params: LogUsageParams): Promise<{ success: boolean; error?: string }> {
    return { success: true }
  }

  enqueueUsage(_params: LogUsageParams): void {}

  async flushUsage(): Promise<{ success: boolean; sent: number; failed: number }> {
    return { success: true, sent: 0, failed: 0 }
  }
}

let logService: LogService | null = null

export function getLogService(): LogService {
  if (!logService) {
    logService = new LogService()
  }
  return logService
}
