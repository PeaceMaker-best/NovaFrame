export type ShotType = 'main' | 'size' | 'lifestyle-scene' | 'detail' | 'comparison'

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type GenerationRunStatus = 'queued' | 'running' | 'completed' | 'succeeded' | 'failed' | 'cancelled'

export type CandidateReviewStatus = 'pending' | 'selected'

export type ProviderQuality = 'low' | 'medium' | 'high'

export type ProviderSize = '1024x1024'

export type ProviderSelectionMode = 'default' | 'auto' | 'fixed'

export interface ProviderRates {
  low: number
  medium: number
  high: number
}

export interface ProviderChannel {
  id: string
  name: string
  baseUrl: string
  endpoint: string
  apiKeyHint: string
  hasApiKey: boolean
  model: string
  active: boolean
  currency: string
  rates: ProviderRates
  createdAt: string
  updatedAt: string
  lastUsedAt?: string
}

export interface ProviderRouting {
  mode: 'auto' | 'fixed'
  fixedChannelId?: string
  currency: string
  updatedAt?: string
}

export interface ProviderConfig {
  channels: ProviderChannel[]
  routing: ProviderRouting
  summary: {
    channelCount: number
    activeChannelCount: number
    pricedChannelCount: number
  }
}

export interface ProviderChannelInput {
  name: string
  baseUrl: string
  endpoint: string
  apiKey?: string
  model: string
  active: boolean
  currency: string
  rates: ProviderRates
}

export interface GenerationProviderSnapshot {
  channelId?: string
  channelName: string
  model: string
  quality: ProviderQuality
  size: ProviderSize
  unitPrice: number
  currency: string
  routingMode: string
  source?: string
}

export interface WorkspaceProduct {
  id: string
  name: string
  assetCount: number
  taskCount: number
  promptCount: number
  outputCount: number
  readiness: 'draft' | 'blocked' | 'ready' | 'stale'
  thumbnail?: string
  updatedAt?: string
  images?: WorkspaceImage[]
}

export interface WorkspaceSnapshot {
  root: string
  products: WorkspaceProduct[]
  accessories: Array<{ id: string; assetCount: number }>
  combinations?: WorkspaceCombination[]
  warnings?: string[]
  scannedAt?: string
  stats: {
    products: number
    accessories: number
    tasks: number
    prompts: number
    outputs: number
    pendingReview: number
  }
  liveGenerationEnabled: boolean
}

export interface WorkspaceImage {
  name: string
  relativePath?: string
  url: string
  sizeBytes?: number
  modifiedAt?: string
}

export interface WorkspaceShotSummary {
  folder: string
  imageCount: number
  images: WorkspaceImage[]
}

export interface WorkspaceTask {
  id: string
  name: string
  product: string
  relativePath?: string
  kind: 'standalone' | 'combination'
  hasPrompts: boolean
  promptCount: number
  preparedShots: ShotType[]
  referenceCount: number
  references?: WorkspaceImage[]
  hasReferenceManifest: boolean
  generatedImageCount: number
  shots: Record<ShotType, WorkspaceShotSummary>
}

export interface WorkspaceCombination {
  id: string
  name: string
  relativePath?: string
  taskCount: number
  promptCount: number
  generatedImageCount: number
  tasks: WorkspaceTask[]
}

export interface AssetItem {
  id: string
  name: string
  url: string
  kind: 'product' | 'reference' | 'output' | 'scene'
  dimensions?: string
  selected?: boolean
}

export interface CanvasNodeBase {
  id: string
  name?: string
  visible?: boolean
  locked?: boolean
  x: number
  y: number
  rotation?: number
  opacity?: number
}

export interface CanvasImageNode extends CanvasNodeBase {
  type: 'image'
  src: string
  width: number
  height: number
}

export interface CanvasTextNode extends CanvasNodeBase {
  type: 'text'
  text: string
  width: number
  fontSize: number
  fontFamily?: string
  fontStyle?: string
  fill: string
  align?: 'left' | 'center' | 'right'
}

export type CanvasNode = CanvasImageNode | CanvasTextNode

export interface GenerationJob {
  id: string
  title: string
  product: string
  shot: ShotType
  status: JobStatus
  progress: number
  createdAt: string
  thumbnail?: string
  error?: string
}

export interface GenerationRunItem {
  task: string
  shot: ShotType
}

export interface GenerationRunRequest {
  product: string
  tasks: string[]
  shots: ShotType[]
  /** Exact work items. When present, this is authoritative over the axis summaries. */
  items?: GenerationRunItem[]
  variants: number
  concurrency: number
  creativeBrief?: PromptDraft
  providerMode?: ProviderSelectionMode
  providerChannelId?: string
  quality?: ProviderQuality
  size?: ProviderSize
}

export interface GenerationRun {
  id: string
  product: string
  tasks: string[]
  shots: ShotType[]
  items?: GenerationRunItem[]
  variants: number
  concurrency: number
  status: GenerationRunStatus
  progress: number
  candidateCount: number
  completedCount: number
  failedCount: number
  pendingReviewCount: number
  selectedCount: number
  expectedCount: number
  message?: string
  error?: string
  createdAt: string
  updatedAt?: string
  thumbnail?: string
  demo?: boolean
  provider?: GenerationProviderSnapshot
}

export interface GenerationCandidate {
  id: string
  jobId: string
  product: string
  task: string
  shot: ShotType
  variant: number
  url: string
  reviewStatus: CandidateReviewStatus
  storageStatus?: 'staged' | 'promoted'
  name?: string
  relativePath?: string
  createdAt?: string
  width?: number
  height?: number
  score?: number
  model?: string
  quality?: string
  estimatedCost?: number
  elapsedSeconds?: number
  currency?: string
  providerChannelId?: string
  providerChannelName?: string
}

export type CanvasInsertMode = 'background' | 'layer'

export interface CanvasInsertRequest {
  requestId: string
  productId: string
  taskId: string
  shot: ShotType
  asset: AssetItem
  mode: CanvasInsertMode
}

export interface PromptDraft {
  subject: string
  environment: string
  composition: string
  negatives: string
  visibleText: string
}

export interface WorkflowResult {
  ok: boolean
  command: string[]
  stdout: string
  stderr?: string
}
