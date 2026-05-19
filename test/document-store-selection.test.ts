import { beforeEach, describe, expect, it } from 'vitest'
import { useDocumentStore, type OpenDocument } from '../src/stores/document-store'

const resetStoreState = () => {
  useDocumentStore.setState({
    openDocuments: [],
    selectedDocument: null,
    uploadedFiles: [],
    isLoading: false,
    lastUpdated: null,
    pageInfo: null,
    selectionInfo: null,
  })
}

describe('document-store selection refresh', () => {
  beforeEach(() => {
    resetStoreState()
  })

  it('keeps selection by documentId when active document switches metadata', () => {
    const selected: OpenDocument = {
      id: 'doc-a',
      name: 'A.hwp',
      path: 'C:\\docs\\A.hwp',
      type: 'hwp',
      index: 0,
      documentId: 123,
    }

    useDocumentStore.getState().selectDocument(selected)
    useDocumentStore.getState().setOpenDocuments([
      {
        ...selected,
        id: 'doc-a-new',
        name: 'A-renamed.hwp',
        index: 1,
      },
    ])

    expect(useDocumentStore.getState().selectedDocument?.documentId).toBe(123)
    expect(useDocumentStore.getState().selectedDocument?.name).toBe('A-renamed.hwp')
  })

  it('keeps selection by normalized path when documentId is missing', () => {
    const selected: OpenDocument = {
      id: 'doc-b',
      name: 'B.hwp',
      path: 'C:\\docs\\B.hwp',
      type: 'hwp',
      index: 0,
    }

    useDocumentStore.getState().selectDocument(selected)
    useDocumentStore.getState().setOpenDocuments([
      {
        ...selected,
        id: 'doc-b-new',
        path: 'c:/docs/b.hwp',
        index: 2,
      },
    ])

    expect(useDocumentStore.getState().selectedDocument?.id).toBe('doc-b-new')
    expect(useDocumentStore.getState().selectedDocument?.path).toBe('c:/docs/b.hwp')
  })
})

