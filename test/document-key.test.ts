import { describe, expect, it } from 'vitest'
import { buildActiveKeyForComparison, generateDocumentKey } from '../electron/main/document-key'

describe('document-key', () => {
  it('builds doc-scheme key from active document id', () => {
    const request = 'v1:doc:42'
    const active = { activeDocumentId: 42, activePath: 'C:\\tmp\\a.hwp' }
    expect(buildActiveKeyForComparison(request, active)).toBe('v1:doc:42')
  })

  it('builds path-scheme key using normalized active path', () => {
    const request = generateDocumentKey({ path: 'C:\\Docs\\Spec.HWP' })
    expect(request).toBeDefined()

    const active = { activePath: 'c:/docs/spec.hwp' }
    expect(buildActiveKeyForComparison(request!, active)).toBe(request)
  })

  it('returns undefined when scheme key cannot be reconstructed', () => {
    expect(buildActiveKeyForComparison('v1:doc:99', { activeDocumentId: 0 })).toBeUndefined()
    expect(buildActiveKeyForComparison('v1:path:abcdef', { activePath: '' })).toBeUndefined()
  })
})

