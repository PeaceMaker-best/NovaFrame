import { create } from 'zustand'
import type { CanvasInsertRequest, GenerationJob, ShotType, WorkspaceSnapshot } from '../types'
import { initialJobs } from '../lib/demo'
import { loadWorkspace } from '../lib/api'

type Toast = { id: string; title: string; detail?: string; tone?: 'success' | 'warning' | 'neutral' }

interface AppState {
  workspace?: WorkspaceSnapshot
  demoMode: boolean
  apiOnline: boolean
  selectedProduct: string
  selectedTask: string
  selectedShot: ShotType
  jobs: GenerationJob[]
  activeRunId?: string
  canvasInsertRequest?: CanvasInsertRequest
  toasts: Toast[]
  setWorkspace: (workspace: WorkspaceSnapshot, demoMode: boolean) => void
  refreshWorkspace: () => Promise<void>
  setApiOnline: (value: boolean) => void
  setSelectedProduct: (id: string) => void
  setSelectedTask: (id: string) => void
  setSelectedShot: (shot: ShotType) => void
  addJob: (job: GenerationJob) => void
  updateJob: (id: string, patch: Partial<GenerationJob>) => void
  setActiveRunId: (id?: string) => void
  queueCanvasInsert: (request: CanvasInsertRequest) => void
  consumeCanvasInsert: () => CanvasInsertRequest | undefined
  notify: (toast: Omit<Toast, 'id'>) => void
  removeToast: (id: string) => void
}

export const useAppStore = create<AppState>((set, get) => ({
  demoMode: true,
  apiOnline: false,
  selectedProduct: 'MF-DEMO-001',
  selectedTask: '单品',
  selectedShot: 'lifestyle-scene',
  jobs: initialJobs,
  toasts: [],
  setWorkspace: (workspace, demoMode) => set({ workspace, demoMode }),
  refreshWorkspace: async () => {
    const workspace = await loadWorkspace()
    set({ workspace: workspace.data, demoMode: workspace.demo })
  },
  setApiOnline: (apiOnline) => set({ apiOnline }),
  setSelectedProduct: (selectedProduct) => set({ selectedProduct }),
  setSelectedTask: (selectedTask) => set({ selectedTask }),
  setSelectedShot: (selectedShot) => set({ selectedShot }),
  addJob: (job) => set((state) => ({ jobs: [job, ...state.jobs] })),
  updateJob: (id, patch) => set((state) => ({
    jobs: state.jobs.map((job) => (job.id === id ? { ...job, ...patch } : job)),
  })),
  setActiveRunId: (activeRunId) => set({ activeRunId }),
  queueCanvasInsert: (canvasInsertRequest) => set({ canvasInsertRequest }),
  consumeCanvasInsert: () => {
    const request = get().canvasInsertRequest
    set({ canvasInsertRequest: undefined })
    return request
  },
  notify: (toast) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    set((state) => ({ toasts: [...state.toasts, { ...toast, id }] }))
    window.setTimeout(() => set((state) => ({ toasts: state.toasts.filter((item) => item.id !== id) })), 3600)
  },
  removeToast: (id) => set((state) => ({ toasts: state.toasts.filter((item) => item.id !== id) })),
}))
