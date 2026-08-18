import { demoWorkspace } from './demo'
import { appendUniqueById, initialStableTotal } from './pagination'
import type {
  CandidateReviewStatus,
  GenerationCandidate,
  GenerationRun,
  GenerationRunItem,
  GenerationRunRequest,
  GenerationRunStatus,
  GenerationProviderSnapshot,
  ProviderChannel,
  ProviderChannelInput,
  ProviderConfig,
  ProviderQuality,
  ProviderRouting,
  ShotType,
  WorkflowResult,
  WorkspaceCombination,
  WorkspaceImage,
  WorkspaceShotSummary,
  WorkspaceSnapshot,
  WorkspaceTask,
} from '../types'

const jsonHeaders = { 'Content-Type': 'application/json' }
const shotTypes: ShotType[] = ['main', 'size', 'lifestyle-scene', 'detail', 'comparison']

type JsonRecord = Record<string, unknown>

class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asRecord(value: unknown): JsonRecord {
  return isRecord(value) ? value : {}
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asNumber(value: unknown, fallback = 0): number {
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) ? number : fallback
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function asShots(value: unknown): ShotType[] {
  const values = asStrings(value)
  return values.filter((item): item is ShotType => shotTypes.includes(item as ShotType))
}

function asGenerationRunItems(value: unknown): GenerationRunItem[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((rawItem) => {
    const item = asRecord(rawItem)
    const task = asString(item.task)
    const [shot] = asShots([item.shot])
    return task && shot ? [{ task, shot }] : []
  })
}

function normalizeProviderSnapshot(value: unknown): GenerationProviderSnapshot | undefined {
  const item = asRecord(value)
  if (!Object.keys(item).length) return undefined
  const quality = asString(item.quality, 'low') as ProviderQuality
  return {
    channelId: asString(item.channelId ?? item.channel_id) || undefined,
    channelName: asString(item.channelName ?? item.channel_name, '未命名渠道'),
    model: asString(item.model, 'gpt-image-2'),
    quality: ['low', 'medium', 'high'].includes(quality) ? quality : 'low',
    size: '1024x1024',
    unitPrice: Math.max(0, asNumber(item.unitPrice ?? item.unit_price)),
    currency: asString(item.currency, 'USD'),
    routingMode: asString(item.routingMode ?? item.routing_mode, 'legacy'),
    source: asString(item.source) || undefined,
  }
}

function normalizeProviderChannel(value: unknown): ProviderChannel {
  const item = asRecord(value)
  const rates = asRecord(item.rates)
  return {
    id: asString(item.id),
    name: asString(item.name),
    baseUrl: asString(item.baseUrl ?? item.base_url),
    endpoint: asString(item.endpoint, '/images/edits'),
    apiKeyHint: asString(item.apiKeyHint ?? item.api_key_hint),
    hasApiKey: Boolean(item.hasApiKey ?? item.has_api_key),
    model: asString(item.model, 'gpt-image-2'),
    active: Boolean(item.active),
    currency: asString(item.currency, 'CNY'),
    rates: {
      low: Math.max(0, asNumber(rates.low)),
      medium: Math.max(0, asNumber(rates.medium)),
      high: Math.max(0, asNumber(rates.high)),
    },
    createdAt: asString(item.createdAt ?? item.created_at),
    updatedAt: asString(item.updatedAt ?? item.updated_at),
    lastUsedAt: asString(item.lastUsedAt ?? item.last_used_at) || undefined,
  }
}

function normalizeProviderRouting(value: unknown): ProviderRouting {
  const item = asRecord(value)
  return {
    mode: item.mode === 'fixed' ? 'fixed' : 'auto',
    fixedChannelId: asString(item.fixedChannelId ?? item.fixed_channel_id) || undefined,
    currency: asString(item.currency, 'CNY'),
    updatedAt: asString(item.updatedAt ?? item.updated_at) || undefined,
  }
}

function errorMessage(body: unknown, fallback: string): string {
  const data = asRecord(body)
  const detail = data.detail
  if (typeof detail === 'string') return detail
  if (isRecord(detail) && typeof detail.message === 'string') return detail.message
  return asString(data.message, fallback)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  const text = await response.text()
  let body: unknown
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }
  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(body, response.statusText || `请求失败 (${response.status})`))
  }
  return body as T
}

function normalizeImage(value: unknown): WorkspaceImage {
  const item = asRecord(value)
  return {
    name: asString(item.name),
    relativePath: asString(item.relativePath ?? item.relative_path) || undefined,
    url: asString(item.url),
    sizeBytes: asNumber(item.sizeBytes ?? item.size_bytes) || undefined,
    modifiedAt: asString(item.modifiedAt ?? item.modified_at) || undefined,
  }
}

function normalizeShot(value: unknown, shot: ShotType): WorkspaceShotSummary {
  const item = asRecord(value)
  return {
    folder: asString(item.folder, shot),
    imageCount: asNumber(item.imageCount ?? item.image_count),
    images: Array.isArray(item.images) ? item.images.map(normalizeImage) : [],
  }
}

function normalizeTask(value: unknown): WorkspaceTask {
  const item = asRecord(value)
  const rawShots = asRecord(item.shots)
  const shots = Object.fromEntries(shotTypes.map((shot) => [shot, normalizeShot(rawShots[shot], shot)])) as Record<ShotType, WorkspaceShotSummary>
  const hasPrompts = Boolean(item.hasPrompts ?? item.has_prompts)
  const promptCount = asNumber(item.promptCount ?? item.prompt_count)
  const rawPreparedShots = item.preparedShots ?? item.prepared_shots
  const preparedShots = rawPreparedShots === undefined
    ? hasPrompts && promptCount >= shotTypes.length ? [...shotTypes] : []
    : asShots(rawPreparedShots)
  return {
    id: asString(item.id),
    name: asString(item.name),
    product: asString(item.product),
    relativePath: asString(item.relativePath ?? item.relative_path) || undefined,
    kind: item.kind === 'combination' ? 'combination' : 'standalone',
    hasPrompts,
    promptCount,
    preparedShots,
    referenceCount: asNumber(item.referenceCount ?? item.reference_count),
    references: Array.isArray(item.references) ? item.references.map(normalizeImage) : [],
    hasReferenceManifest: Boolean(item.hasReferenceManifest ?? item.has_reference_manifest),
    generatedImageCount: asNumber(item.generatedImageCount ?? item.generated_image_count),
    shots,
  }
}

function normalizeCombination(value: unknown): WorkspaceCombination {
  const item = asRecord(value)
  return {
    id: asString(item.id),
    name: asString(item.name),
    relativePath: asString(item.relativePath ?? item.relative_path) || undefined,
    taskCount: asNumber(item.taskCount ?? item.task_count),
    promptCount: asNumber(item.promptCount ?? item.prompt_count),
    generatedImageCount: asNumber(item.generatedImageCount ?? item.generated_image_count),
    tasks: Array.isArray(item.tasks) ? item.tasks.map(normalizeTask) : [],
  }
}

function normalizeWorkspace(value: unknown): WorkspaceSnapshot {
  const item = asRecord(value)
  const stats = asRecord(item.stats)
  const products = Array.isArray(item.products) ? item.products.map((value) => {
    const product = asRecord(value)
    return {
      id: asString(product.id),
      name: asString(product.name, asString(product.id)),
      assetCount: asNumber(product.assetCount ?? product.asset_count),
      taskCount: asNumber(product.taskCount ?? product.task_count),
      promptCount: asNumber(product.promptCount ?? product.prompt_count),
      outputCount: asNumber(product.outputCount ?? product.output_count),
      readiness: ['draft', 'blocked', 'ready', 'stale'].includes(asString(product.readiness))
        ? asString(product.readiness) as 'draft' | 'blocked' | 'ready' | 'stale'
        : 'draft' as const,
      thumbnail: asString(product.thumbnail) || undefined,
      updatedAt: asString(product.updatedAt ?? product.updated_at) || undefined,
      images: Array.isArray(product.images) ? product.images.map(normalizeImage) : [],
    }
  }) : []
  const accessories = Array.isArray(item.accessories) ? item.accessories.map((value) => {
    const accessory = asRecord(value)
    return { id: asString(accessory.id), assetCount: asNumber(accessory.assetCount ?? accessory.asset_count) }
  }) : []
  return {
    root: asString(item.root),
    products,
    accessories,
    combinations: Array.isArray(item.combinations) ? item.combinations.map(normalizeCombination) : [],
    warnings: asStrings(item.warnings),
    scannedAt: asString(item.scannedAt ?? item.scanned_at) || undefined,
    stats: {
      products: asNumber(stats.products),
      accessories: asNumber(stats.accessories),
      tasks: asNumber(stats.tasks),
      prompts: asNumber(stats.prompts),
      outputs: asNumber(stats.outputs),
      pendingReview: asNumber(stats.pendingReview ?? stats.pending_review),
    },
    liveGenerationEnabled: Boolean(item.liveGenerationEnabled ?? item.live_generation_enabled),
  }
}

function normalizeRun(value: unknown): GenerationRun {
  const item = asRecord(value)
  const payload = asRecord(item.request ?? item.payload)
  const tasks = asStrings(item.tasks ?? payload.tasks)
  const shots = asShots(item.shots ?? payload.shots)
  const items = asGenerationRunItems(item.items ?? payload.items)
  const variants = Math.max(1, asNumber(item.variants ?? payload.variants, 1))
  const statusValue = asString(item.status, 'queued')
  const status = (['queued', 'running', 'completed', 'succeeded', 'failed', 'cancelled'].includes(statusValue)
    ? statusValue
    : 'queued') as GenerationRunStatus
  const expectedCount = Math.max(0, asNumber(
    item.expectedCount ?? item.expected_count ?? item.expected_candidate_count ?? item.totalCandidates ?? item.total_candidates,
    (items.length || tasks.length * shots.length) * variants,
  ))
  const candidateCount = Math.max(0, asNumber(
    item.candidateCount ?? item.candidate_count ?? item.generatedCandidates ?? item.generated_candidates,
  ))
  const selectedCount = Math.max(0, asNumber(item.selectedCount ?? item.selected_count))
  const inferredProgress = expectedCount > 0 ? Math.round((candidateCount / expectedCount) * 100) : 0
  const terminal = ['completed', 'succeeded', 'failed', 'cancelled'].includes(status)
  return {
    id: asString(item.id ?? item.job_id),
    product: asString(item.product ?? payload.product),
    tasks,
    shots,
    items: items.length ? items : undefined,
    variants,
    concurrency: Math.max(1, asNumber(item.concurrency ?? payload.concurrency, 1)),
    status,
    progress: Math.min(100, Math.max(0, asNumber(item.progress, terminal ? 100 : inferredProgress))),
    candidateCount,
    completedCount: Math.max(0, asNumber(item.completedCount ?? item.completed_count)),
    failedCount: Math.max(0, asNumber(item.failedCount ?? item.failed_count)),
    pendingReviewCount: Math.max(0, asNumber(
      item.pendingReviewCount ?? item.pending_review_count,
      candidateCount - selectedCount,
    )),
    selectedCount,
    expectedCount,
    message: asString(item.message) || undefined,
    error: asString(item.error ?? item.stderr) || undefined,
    createdAt: asString(item.createdAt ?? item.created_at),
    updatedAt: asString(item.updatedAt ?? item.updated_at) || undefined,
    thumbnail: asString(item.thumbnail ?? item.thumbnail_url) || undefined,
    provider: normalizeProviderSnapshot(item.provider ?? payload.provider),
  }
}

function normalizeCandidate(value: unknown): GenerationCandidate {
  const item = asRecord(value)
  const metadata = asRecord(item.metadata)
  const shotValue = asString(item.shot, 'main')
  const reviewStatusValue = asString(item.reviewStatus ?? item.review_status ?? item.decision, 'pending')
  const storageStatus = item.storage_status === 'promoted' ? 'promoted' : item.storage_status === 'staged' ? 'staged' : undefined
  const relativePath = asString(item.relativePath ?? item.relative_path) || undefined
  const stableAssetUrl = storageStatus === 'promoted' && relativePath
    ? `/api/workspace/assets/${relativePath.split('/').map(encodeURIComponent).join('/')}`
    : undefined
  return {
    id: asString(item.id),
    jobId: asString(item.jobId ?? item.job_id ?? item.run_id),
    product: asString(item.product),
    task: asString(item.task),
    shot: shotTypes.includes(shotValue as ShotType) ? shotValue as ShotType : 'main',
    variant: Math.max(1, asNumber(item.variant ?? item.variant_index ?? item.candidate_index, 1)),
    url: stableAssetUrl ?? asString(item.url ?? item.image_url),
    reviewStatus: (reviewStatusValue === 'selected' ? 'selected' : 'pending') as CandidateReviewStatus,
    storageStatus,
    name: asString(item.name) || undefined,
    relativePath,
    createdAt: asString(item.createdAt ?? item.created_at) || undefined,
    width: asNumber(item.width) || undefined,
    height: asNumber(item.height) || undefined,
    score: asNumber(item.score ?? item.quality_score) || undefined,
    model: asString(item.model) || undefined,
    quality: asString(item.quality) || undefined,
    estimatedCost: asNumber(item.estimatedCost ?? item.estimated_cost) || undefined,
    elapsedSeconds: asNumber(item.elapsedSeconds ?? item.elapsed_seconds) || undefined,
    currency: asString(item.currency ?? metadata.currency) || undefined,
    providerChannelId: asString(item.providerChannelId ?? item.provider_channel_id ?? metadata.provider_channel_id) || undefined,
    providerChannelName: asString(item.providerChannelName ?? item.provider_channel_name ?? metadata.provider_channel_name) || undefined,
  }
}

export async function checkHealth(): Promise<boolean> {
  try {
    await request('/api/health')
    return true
  } catch {
    return false
  }
}

export async function loadWorkspace(): Promise<{ data: WorkspaceSnapshot; demo: boolean }> {
  try {
    return { data: normalizeWorkspace(await request<unknown>('/api/workspace')), demo: false }
  } catch {
    return { data: demoWorkspace, demo: true }
  }
}

export function prepareWorkflow(product: string): Promise<WorkflowResult> {
  return request('/api/workflow/prepare', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ product, refreshPrompts: true }),
  })
}

export function previewWorkflow(product: string, shot?: string, task?: string): Promise<WorkflowResult> {
  return request('/api/workflow/preview', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ product, tasks: task ? [task] : [], shots: shot ? [shot] : [] }),
  })
}

export function saveCanvas(id: string, document: unknown): Promise<{ ok: boolean; version: number }> {
  return request(`/api/canvases/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify({ document }),
  })
}

export async function loadCanvas(id: string): Promise<Record<string, unknown> | undefined> {
  try {
    return await request<Record<string, unknown>>(`/api/canvases/${encodeURIComponent(id)}`)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return undefined
    throw error
  }
}

export async function importWorkspaceAsset(file: File, product: string): Promise<WorkspaceImage> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('无法读取本地图片'))
    reader.readAsDataURL(file)
  })
  const result = await request<unknown>('/api/workspace/assets/import', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ product, filename: file.name, dataUrl }),
  })
  return normalizeImage(result)
}

export async function createGenerationRun(payload: GenerationRunRequest): Promise<GenerationRun> {
  const result = await request<unknown>('/api/generation-runs', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(payload),
  })
  return normalizeRun(result)
}

export async function getProviderConfig(): Promise<ProviderConfig> {
  const result = asRecord(await request<unknown>('/api/provider-config'))
  const summary = asRecord(result.summary)
  return {
    channels: Array.isArray(result.channels) ? result.channels.map(normalizeProviderChannel) : [],
    routing: normalizeProviderRouting(result.routing),
    summary: {
      channelCount: asNumber(summary.channelCount ?? summary.channel_count),
      activeChannelCount: asNumber(summary.activeChannelCount ?? summary.active_channel_count),
      pricedChannelCount: asNumber(summary.pricedChannelCount ?? summary.priced_channel_count),
    },
  }
}

function providerChannelBody(input: ProviderChannelInput, includeKey: boolean): Record<string, unknown> {
  return {
    name: input.name,
    base_url: input.baseUrl,
    endpoint: input.endpoint,
    ...(includeKey && input.apiKey ? { api_key: input.apiKey } : {}),
    model: input.model,
    active: input.active,
    currency: input.currency,
    rates: input.rates,
  }
}

export async function createProviderChannel(input: ProviderChannelInput): Promise<ProviderChannel> {
  return normalizeProviderChannel(await request<unknown>('/api/provider-channels', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(providerChannelBody(input, true)),
  }))
}

export async function updateProviderChannel(id: string, input: ProviderChannelInput): Promise<ProviderChannel> {
  return normalizeProviderChannel(await request<unknown>(`/api/provider-channels/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: jsonHeaders,
    body: JSON.stringify(providerChannelBody(input, Boolean(input.apiKey))),
  }))
}

export async function setProviderChannelActive(id: string, active: boolean): Promise<ProviderChannel> {
  return normalizeProviderChannel(await request<unknown>(`/api/provider-channels/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: jsonHeaders,
    body: JSON.stringify({ active }),
  }))
}

export async function updateProviderRouting(input: Pick<ProviderRouting, 'mode' | 'fixedChannelId' | 'currency'>): Promise<ProviderRouting> {
  return normalizeProviderRouting(await request<unknown>('/api/provider-routing', {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify({ mode: input.mode, fixed_channel_id: input.fixedChannelId, currency: input.currency }),
  }))
}

export interface ApiPage<T> {
  items: T[]
  total: number
}

export async function listGenerationRunsPage(
  limit = 50,
  offset = 0,
): Promise<ApiPage<GenerationRun>> {
  const search = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  })
  const result = await request<unknown>(`/api/generation-runs?${search.toString()}`)
  const data = asRecord(result)
  const items = Array.isArray(result) ? result : Array.isArray(data.items) ? data.items : Array.isArray(data.runs) ? data.runs : []
  const normalized = items.map(normalizeRun)
  return {
    items: normalized,
    total: Math.max(normalized.length, asNumber(data.total, normalized.length)),
  }
}

export async function listGenerationRuns(): Promise<GenerationRun[]> {
  return (await listGenerationRunsPage()).items
}

export async function getGenerationRun(id: string): Promise<GenerationRun> {
  return normalizeRun(await request<unknown>(`/api/generation-runs/${encodeURIComponent(id)}`))
}

export async function listCandidates(filters: { jobId?: string; reviewStatus?: CandidateReviewStatus } = {}): Promise<GenerationCandidate[]> {
  // A run can contain at most 3000 planned candidates, so a scoped review uses
  // one stable server snapshot. Unscoped browsing keeps the smaller global cap.
  const pageSize = filters.jobId ? 3000 : 500
  const candidates = new Map<string, GenerationCandidate>()
  let offset = 0
  let stableTotal: number | undefined
  while (stableTotal === undefined || offset < stableTotal) {
    const search = new URLSearchParams({
      limit: String(pageSize),
      offset: String(offset),
    })
    if (filters.jobId) search.set('job_id', filters.jobId)
    if (filters.reviewStatus) search.set('review_status', filters.reviewStatus)
    const result = await request<unknown>(`/api/candidates?${search.toString()}`)
    const data = asRecord(result)
    const rawItems = Array.isArray(result)
      ? result
      : Array.isArray(data.items)
        ? data.items
        : Array.isArray(data.candidates)
          ? data.candidates
          : []
    const page = rawItems.map(normalizeCandidate)
    appendUniqueById(candidates, page)
    if (stableTotal === undefined) {
      stableTotal = initialStableTotal(asNumber(data.total, page.length), page.length)
    }
    if (!page.length || page.length < pageSize) break
    offset += page.length
  }
  return [...candidates.values()]
}

export async function selectCandidate(id: string): Promise<GenerationCandidate> {
  return normalizeCandidate(await request<unknown>(`/api/candidates/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: jsonHeaders,
    body: JSON.stringify({ decision: 'selected' }),
  }))
}

export async function deleteCandidate(id: string): Promise<void> {
  await request<unknown>(`/api/candidates/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
