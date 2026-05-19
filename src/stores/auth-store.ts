// ============================================================
// auth-store.ts
// 인증 stub — 인증 없이 항상 통과
// ============================================================

import { create } from 'zustand'
import { useSettingsStore } from './settings-store'

interface AuthState {
  isAuthenticated: boolean
  authData: null
  isLoading: boolean
  error: string | null

  login: () => Promise<boolean>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
  refreshToken: () => Promise<boolean>
  ensureValidToken: () => Promise<boolean>

  getRole: () => null
  getPromptEditScope: () => 'partial'
  getFeatureFlag: (_key: string) => undefined
  canEditPrompt: (_scope: 'none' | 'partial' | 'full') => boolean
}

export const useAuthStore = create<AuthState>((set) => ({
  isAuthenticated: true,
  authData: null,
  isLoading: false,
  error: null,

  login: async () => {
    set({ isAuthenticated: true })
    return true
  },

  logout: async () => {
    await window.electronAPI.auth.logout()
    set({ isAuthenticated: false, error: null })
    useSettingsStore.getState().setEmail(null)
  },

  checkAuth: async () => {
    set({ isAuthenticated: true, isLoading: false })
  },

  refreshToken: async () => true,

  ensureValidToken: async () => true,

  getRole: () => null,

  getPromptEditScope: () => 'partial',

  getFeatureFlag: (_key: string) => undefined,

  canEditPrompt: (scope: 'none' | 'partial' | 'full') => {
    return scope === 'none' || scope === 'partial'
  }
}))
