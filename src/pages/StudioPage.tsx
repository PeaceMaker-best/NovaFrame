import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlignCenter,
  AlignHorizontalDistributeCenter,
  AlignStartHorizontal,
  AlignEndHorizontal,
  AlignStartVertical,
  AlignEndVertical,
  AlignVerticalDistributeCenter,
  ArrowDownToLine,
  Check,
  ChevronDown,
  CircleAlert,
  Copy,
  Download,
  Eye,
  EyeOff,
  Grip,
  Hand,
  ImagePlus,
  Layers3,
  LockKeyhole,
  LockOpen,
  Maximize2,
  Minus,
  MousePointer2,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Redo2,
  ScanSearch,
  Settings2,
  Sparkles,
  TextCursorInput,
  Trash2,
  Undo2,
  WandSparkles,
  Zap,
} from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router'
import { ARTBOARD, StudioCanvas, type CanvasViewport, type StudioCanvasHandle } from '../components/StudioCanvas'
import { createGenerationRun, getProviderConfig, importWorkspaceAsset, listCandidates, loadCanvas, previewWorkflow, saveCanvas } from '../lib/api'
import { resolveProviderEstimate } from '../lib/providerRouting'
import { useAppStore } from '../store/appStore'
import type { AssetItem, CanvasNode, GenerationReferenceImageInput, PromptDraft, ProviderConfig, ProviderQuality, ShotType } from '../types'
import '../studio-enhancements.css'

const MAX_SUBMITTED_REFERENCES = 3
const MAX_SUBMITTED_REFERENCE_BYTES = 12 * 1024 * 1024
const SUPPORTED_REFERENCE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

const shotOptions: Array<{ id: ShotType; label: string; short: string }> = [
  { id: 'main', label: '主图', short: 'MAIN' },
  { id: 'size', label: '尺寸图', short: 'SIZE' },
  { id: 'lifestyle-scene', label: '场景图', short: 'SCENE' },
  { id: 'detail', label: '细节图', short: 'DETAIL' },
  { id: 'comparison', label: '对比图', short: 'COMPARE' },
]

const outputByShot: Record<ShotType, string> = {
  main: '/demo/product-studio.png',
  size: '/demo/campaign.png',
  'lifestyle-scene': '/demo/product-studio.png',
  detail: '/demo/fashion.png',
  comparison: '/demo/interior.png',
}

function defaultPrompt(product: string): PromptDraft {
  return {
    subject: `${product} · 商品身份、结构、数量与材质以工作区事实和参考图为准`,
    environment: '',
    composition: '',
    negatives: '',
    visibleText: '',
  }
}

type SaveState = 'loading' | 'dirty' | 'saving' | 'saved' | 'load-error' | 'save-error'

type CanvasSnapshot = {
  nodes: CanvasNode[]
  prompt: PromptDraft
  shot: ShotType
  task: string
  viewport: CanvasViewport
}

type GenerationContext = {
  canvasId: string
  revision: number
  hydrationEpoch: number
  snapshot: CanvasSnapshot
  product: string
  productLabel: string
  task: string
  shot: ShotType
  variants: number
  providerChoice: string
  providerConfig?: ProviderConfig
  quality: ProviderQuality
  referenceImages: PendingReferenceImage[]
}

type PendingReferenceImage = GenerationReferenceImageInput & {
  id: string
  size: number
}

type CachedCanvas = {
  snapshot: CanvasSnapshot
  pendingSync: boolean
}

const canvasSaveQueues = new Map<string, Promise<unknown>>()

function cloneCanvasSnapshot(snapshot: CanvasSnapshot): CanvasSnapshot {
  return {
    ...snapshot,
    nodes: snapshot.nodes.map((node) => ({ ...node })),
    prompt: { ...snapshot.prompt },
    viewport: { ...snapshot.viewport },
  }
}

function cloneProviderConfig(config: ProviderConfig | undefined): ProviderConfig | undefined {
  if (!config) return undefined
  return {
    channels: config.channels.map((channel) => ({ ...channel, rates: { ...channel.rates } })),
    routing: { ...config.routing },
    summary: { ...config.summary },
  }
}

function readImageDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error(`无法读取参考图：${file.name}`))
    reader.readAsDataURL(file)
  })
}

function readCanvasCache(id: string): CachedCanvas | undefined {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(`museforge-canvas:${id}`) ?? 'null') as unknown
    if (!value || typeof value !== 'object') return undefined
    const record = value as Record<string, unknown>
    const snapshot = (record.snapshot && typeof record.snapshot === 'object' ? record.snapshot : record) as Partial<CanvasSnapshot>
    if (!Array.isArray(snapshot.nodes) || !snapshot.prompt || typeof snapshot.prompt !== 'object') return undefined
    return {
      snapshot: snapshot as CanvasSnapshot,
      pendingSync: record.pendingSync === true,
    }
  } catch {
    return undefined
  }
}

function writeCanvasCache(id: string, snapshot: CanvasSnapshot, pendingSync: boolean): void {
  try {
    window.sessionStorage.setItem(`museforge-canvas:${id}`, JSON.stringify({ snapshot, pendingSync }))
  } catch {
    // The SQLite document remains authoritative when session storage is unavailable.
  }
}

function normalizeViewport(value: unknown): CanvasViewport | undefined {
  if (!value || typeof value !== 'object') return undefined
  const viewport = value as Partial<CanvasViewport>
  if (![viewport.x, viewport.y, viewport.zoom].every((item) => typeof item === 'number' && Number.isFinite(item))) return undefined
  return {
    x: viewport.x as number,
    y: viewport.y as number,
    zoom: Math.min(1.5, Math.max(0.35, viewport.zoom as number)),
    mode: viewport.mode === 'fit' ? 'fit' : 'custom',
  }
}

function shortHash(value: string): string {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(36)
}

function canvasDocumentId(product: string, task: string, shot: ShotType): string {
  const identity = `${product}|${task}|${shot}`
  return `${product.slice(0, 30)}-${task.slice(0, 30)}-${shot}-${shortHash(identity)}`
}

function initialNodes(shot: ShotType, demoMode = false): CanvasNode[] {
  if (!demoMode) return []
  return [
    { id: 'scene-background', name: '画板底图', locked: true, type: 'image', src: outputByShot[shot], x: ARTBOARD.x, y: ARTBOARD.y, width: ARTBOARD.width, height: ARTBOARD.height },
    { id: 'headline', name: '主标题', type: 'text', text: 'HYDRATION, REIMAGINED.', x: 344, y: 184, width: 445, fontSize: 25, fontStyle: 'bold', fill: '#173f38' },
    { id: 'subline', name: '副标题', type: 'text', text: 'A quiet ritual for bright summer mornings', x: 347, y: 218, width: 360, fontSize: 13, fill: '#42665f' },
  ]
}

function nodeBounds(node: CanvasNode) {
  const lineCount = node.type === 'text' ? Math.max(1, node.text.split('\n').length) : 1
  const height = node.type === 'image' ? node.height : node.fontSize * 1.1 * lineCount
  return { x: node.x, y: node.y, width: node.width, height, right: node.x + node.width, bottom: node.y + height }
}

function imageDimensions(src: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const image = new Image()
    image.onload = () => resolve({ width: image.naturalWidth || 1, height: image.naturalHeight || 1 })
    image.onerror = () => resolve({ width: 1, height: 1 })
    image.src = src
  })
}

function nodeLabel(node: CanvasNode): string {
  if (node.name) return node.name
  if (node.id === 'scene-background') return '画板底图'
  if (node.type === 'text') return node.text.trim().slice(0, 24) || '文字图层'
  return '图片图层'
}

function AssetPanel({
  onAdd,
  onImport,
  projectAssets,
  resultAssets,
  product,
  taskLabel,
  referenceCount,
  nodes,
  selectedIds,
  onSelect,
  onUpdateNode,
  onMoveLayer,
  disabled,
}: {
  onAdd: (asset: AssetItem) => void
  onImport: (file: File) => void
  projectAssets: AssetItem[]
  resultAssets: AssetItem[]
  product: string
  taskLabel: string
  referenceCount: number
  nodes: CanvasNode[]
  selectedIds: string[]
  onSelect: (id: string, additive?: boolean) => void
  onUpdateNode: (id: string, patch: Partial<CanvasNode>) => void
  onMoveLayer: (id: string, direction: 'up' | 'down') => void
  disabled: boolean
}) {
  const [tab, setTab] = useState<'assets' | 'results' | 'layers'>('assets')
  const [editingId, setEditingId] = useState<string>()
  const [draftName, setDraftName] = useState('')
  const filtered = tab === 'results' ? resultAssets : projectAssets

  const importFile = (file?: File) => {
    if (disabled || !file?.type.startsWith('image/')) return
    onImport(file)
  }

  const finishRename = (node: CanvasNode) => {
    if (disabled) {
      setEditingId(undefined)
      return
    }
    const name = draftName.trim()
    if (name && name !== nodeLabel(node)) onUpdateNode(node.id, { name })
    setEditingId(undefined)
  }

  return (
    <aside className="asset-panel">
      <div className="panel-tabs compact">
        <button className={tab === 'assets' ? 'active' : ''} onClick={() => setTab('assets')}>素材</button>
        <button className={tab === 'results' ? 'active' : ''} onClick={() => setTab('results')}>结果</button>
        <button className={tab === 'layers' ? 'active' : ''} onClick={() => setTab('layers')}>图层</button>
      </div>
      {tab !== 'layers' ? (
        <>
          <label className="upload-zone"><ImagePlus size={18} /><span><strong>导入素材</strong><small>PNG / JPG / WebP</small></span><input type="file" accept="image/*" disabled={disabled} onChange={(event) => { importFile(event.target.files?.[0]); event.target.value = '' }} /></label>
          <div className="asset-section-title"><span>{tab === 'results' ? '已保留结果' : '项目素材与参考'}</span><em>{filtered.length}</em></div>
          {filtered.length ? <div className="asset-grid">
            {filtered.map((asset) => (
              <button key={asset.id} className="asset-tile" disabled={disabled} onClick={() => onAdd(asset)} title={`添加 ${asset.name}`}>
                <img src={asset.url} alt={asset.name} />
                <span className={`asset-kind ${asset.kind}`}>{asset.kind === 'product' ? '商品' : asset.kind === 'output' ? '结果' : asset.kind === 'scene' ? '场景' : '参考'}</span>
                <small>{asset.name}</small>
                <i><Plus size={13} /></i>
              </button>
            ))}
          </div> : <div className="asset-empty"><ImagePlus size={24} /><strong>{tab === 'results' ? '当前画布还没有保留结果' : '当前商品还没有可用图片'}</strong><p>{tab === 'results' ? '在审核页选择满意候选后，会自动出现在这里。' : '将图片导入当前商品工作区后即可加入画布。'}</p></div>}
          {tab === 'assets' && <><div className="asset-section-title"><span>工作区事实</span><LockKeyhole size={13} /></div><div className="fact-mini-card"><strong>{product}</strong><p>{taskLabel} · {referenceCount} 张任务参考图<br />生成时以 prompts.json 与 reference_manifest.json 为准</p></div></>}
        </>
      ) : (
        <div className="layer-panel">
          <div className="asset-section-title"><span>图层顺序</span><em>{nodes.length}</em></div>
          <p className="layer-help">顶部图层覆盖底部 · Shift 点击可多选</p>
          <div className="layer-list">
            {[...nodes].reverse().map((node, reverseIndex) => {
              const actualIndex = nodes.length - 1 - reverseIndex
              const active = selectedIds.includes(node.id)
              return <div key={node.id} className={`layer-row ${active ? 'active' : ''} ${node.visible === false ? 'hidden' : ''}`}>
                <div className="layer-main" role="button" tabIndex={0} onClick={(event) => onSelect(node.id, event.shiftKey || event.metaKey || event.ctrlKey)} onDoubleClick={() => { if (!disabled) { setEditingId(node.id); setDraftName(nodeLabel(node)) } }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onSelect(node.id, event.shiftKey || event.metaKey || event.ctrlKey) }}>
                  <span>{node.type === 'image' ? <ImagePlus size={15} /> : <TextCursorInput size={15} />}</span>
                  {editingId === node.id ? <input autoFocus value={draftName} onChange={(event) => setDraftName(event.target.value)} onBlur={() => finishRename(node)} onKeyDown={(event) => { if (event.key === 'Enter') finishRename(node); if (event.key === 'Escape') setEditingId(undefined) }} onClick={(event) => event.stopPropagation()} /> : <strong>{nodeLabel(node)}</strong>}
                </div>
                <button title={node.visible === false ? '显示图层' : '隐藏图层'} disabled={disabled} onClick={() => onUpdateNode(node.id, { visible: node.visible === false })}>{node.visible === false ? <EyeOff size={14} /> : <Eye size={14} />}</button>
                <button title={node.locked || node.id === 'scene-background' ? '已锁定' : '锁定图层'} disabled={disabled || node.id === 'scene-background'} onClick={() => onUpdateNode(node.id, { locked: !node.locked })}>{node.locked || node.id === 'scene-background' ? <LockKeyhole size={14} /> : <LockOpen size={14} />}</button>
                <span className="layer-order"><button disabled={disabled || actualIndex >= nodes.length - 1} onClick={() => onMoveLayer(node.id, 'up')}>↑</button><button disabled={disabled || actualIndex <= 1} onClick={() => onMoveLayer(node.id, 'down')}>↓</button></span>
              </div>
            })}
          </div>
        </div>
      )}
    </aside>
  )
}

function Inspector({
  prompt,
  setPrompt,
  referenceAssets,
  referenceCount,
  submittedReferences,
  onAddReferences,
  onRemoveReference,
  variants,
  setVariants,
  providerConfig,
  providerChoice,
  setProviderChoice,
  quality,
  setQuality,
  preflightState,
  selectedNodes,
  onUpdateSelected,
  onGenerate,
  onPreflight,
  onAlign,
  onDistribute,
  onLayerUp,
  onLayerDown,
  generating,
  canvasVersion,
}: {
  prompt: PromptDraft
  setPrompt: (value: PromptDraft) => void
  referenceAssets: AssetItem[]
  referenceCount: number
  submittedReferences: PendingReferenceImage[]
  onAddReferences: (files: FileList | null) => void
  onRemoveReference: (id: string) => void
  variants: number
  setVariants: (value: number) => void
  providerConfig?: ProviderConfig
  providerChoice: string
  setProviderChoice: (value: string) => void
  quality: ProviderQuality
  setQuality: (value: ProviderQuality) => void
  preflightState: 'idle' | 'checking' | 'passed' | 'failed'
  selectedNodes: CanvasNode[]
  onUpdateSelected: (patch: Partial<CanvasNode>) => void
  onGenerate: () => void
  onPreflight: () => void
  onAlign: (alignment: 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom') => void
  onDistribute: (axis: 'horizontal' | 'vertical') => void
  onLayerUp: () => void
  onLayerDown: () => void
  generating: boolean
  canvasVersion?: number
}) {
  const [tab, setTab] = useState<'generate' | 'properties' | 'history'>('generate')
  const [advanced, setAdvanced] = useState(false)
  const selectedNode = selectedNodes.length === 1 ? selectedNodes[0] : undefined
  const selectedIsBackground = selectedNode?.id === 'scene-background'
  const activeChannels = providerConfig?.channels.filter((channel) => channel.active) ?? []
  const selectedChannel = activeChannels.find((channel) => channel.id === providerChoice)
  const providerLabel = providerChoice === 'auto' ? 'Auto 最低价' : selectedChannel?.name ?? '工作区默认'

  useEffect(() => {
    if (selectedNodes.length) setTab('properties')
  }, [selectedNode?.id, selectedNodes.length])

  return (
    <aside className="inspector-panel" aria-busy={generating} inert={generating ? true : undefined}>
      <div className="panel-tabs">
        <button className={tab === 'generate' ? 'active' : ''} onClick={() => setTab('generate')}><WandSparkles size={15} />生成</button>
        <button className={tab === 'properties' ? 'active' : ''} onClick={() => setTab('properties')}><Settings2 size={15} />属性</button>
        <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}><Layers3 size={15} />版本</button>
      </div>

      {tab === 'generate' && (
        <div className="inspector-scroll">
          <div className="inspector-section">
            <div className="section-heading"><span>任务参考图</span><small>{referenceCount} / 5</small></div>
            {referenceAssets.length ? <div className="reference-strip">
              {referenceAssets.map((asset, index) => <div key={asset.id}><img src={asset.url} alt={asset.name} /><span>{index === 0 ? '身份锚点' : `参考 ${index + 1}`}</span></div>)}
            </div> : <div className="reference-empty"><CircleAlert size={16} />当前任务没有已发布参考图</div>}
            <p className="helper-copy"><LockKeyhole size={12} />来源：任务目录 reference_manifest.json；画布不会复制图片数据。</p>
          </div>

          <div className="inspector-section submitted-reference-section">
            <div className="section-heading"><span>本次参考图</span><small>{submittedReferences.length} / {MAX_SUBMITTED_REFERENCES}</small></div>
            <div className="submitted-reference-grid">
              {submittedReferences.map((reference, index) => (
                <div className="submitted-reference-card" key={reference.id}>
                  <img src={reference.dataUrl} alt={reference.filename} />
                  <span>临时参考 {index + 1}</span>
                  <button type="button" onClick={() => onRemoveReference(reference.id)} disabled={generating} aria-label={`移除 ${reference.filename}`}><Trash2 size={13} /></button>
                </div>
              ))}
              {submittedReferences.length < MAX_SUBMITTED_REFERENCES && (
                <label className="submitted-reference-upload">
                  <ImagePlus size={19} />
                  <span>添加图片</span>
                  <small>PNG / JPG / WebP</small>
                  <input type="file" accept="image/png,image/jpeg,image/webp" multiple disabled={generating} onChange={(event) => { onAddReferences(event.target.files); event.target.value = '' }} />
                </label>
              )}
            </div>
            <p className="helper-copy"><LockKeyhole size={12} />仅用于本次生成，会与文字指令一起进入运行快照，不写入素材库。</p>
          </div>

          <div className="inspector-section prompt-fields">
            <div className="section-heading"><span>本次创意指令</span><small>将写入运行快照</small></div>
            <label><span>商品主体 <em>事实锁定</em></span><textarea value={prompt.subject} disabled rows={3} /></label>
            <label><span>场景与氛围</span><textarea value={prompt.environment} disabled={generating} onChange={(event) => setPrompt({ ...prompt, environment: event.target.value })} rows={3} /></label>
            <label><span>构图</span><textarea value={prompt.composition} disabled={generating} onChange={(event) => setPrompt({ ...prompt, composition: event.target.value })} rows={2} /></label>
            <label><span>画面文字</span><input value={prompt.visibleText} disabled={generating} onChange={(event) => setPrompt({ ...prompt, visibleText: event.target.value })} /></label>
            <label><span>负面约束</span><textarea className="negative" value={prompt.negatives} disabled={generating} onChange={(event) => setPrompt({ ...prompt, negatives: event.target.value })} rows={2} /></label>
          </div>

          <div className="inspector-section">
            <button className="advanced-toggle" disabled={generating} onClick={() => setAdvanced((value) => !value)}><span>生成参数</span><span>{variants} 个候选 · {providerLabel}</span><ChevronDown size={15} className={advanced ? 'rotated' : ''} /></button>
            {advanced && <div className="advanced-grid truthful provider-parameters"><label>候选数<select value={variants} disabled={generating} onChange={(event) => setVariants(Number(event.target.value))}><option value="2">2</option><option value="4">4</option><option value="6">6</option></select></label><label>质量<select value={quality} disabled={generating} onChange={(event) => setQuality(event.target.value as ProviderQuality)}><option value="low">低质量</option><option value="medium">中质量</option><option value="high">高质量</option></select></label><label className="wide">本批次渠道<select value={providerChoice} disabled={generating} onChange={(event) => setProviderChoice(event.target.value)}><option value="default">工作区默认</option><option value="auto">Auto · 同币种最低价</option>{activeChannels.map((channel) => <option key={channel.id} value={channel.id}>{channel.name} · {channel.rates[quality] > 0 ? `${channel.rates[quality].toFixed(4)} ${channel.currency}` : '费率未标注'}</option>)}</select></label><div className="server-parameter wide"><span>模型 / 尺寸 / 路由</span><strong>{selectedChannel?.model ?? '由默认路由决定'} · 1024 × 1024</strong><small>选路在服务端完成并固化到本次运行，不会把 API Key 写入画布。</small></div></div>}
          </div>

          <div className={`preflight-card ${preflightState}`}>
            <div><span>{preflightState === 'failed' ? <CircleAlert size={13} /> : <Check size={13} />}</span><p><strong>{preflightState === 'passed' ? 'Skill 门槛检查已通过' : preflightState === 'failed' ? '存在生成阻塞项' : preflightState === 'checking' ? '正在检查任务文件' : '生成前需要执行门槛检查'}</strong><small>商品事实、参考清单、Prompt 结构与图型范围</small></p></div>
            <button onClick={onPreflight} disabled={generating || preflightState === 'checking'}>{preflightState === 'checking' ? '检查中' : '开始检查'}</button>
          </div>
        </div>
      )}

      {tab === 'properties' && (
        <div className="inspector-scroll property-panel">
          {selectedNodes.length ? (
            <>
              <div className="selection-summary"><span>{selectedNodes.length > 1 ? <Layers3 size={17} /> : selectedNode?.type === 'image' ? <ImagePlus size={17} /> : <TextCursorInput size={17} />}</span><p><small>当前选择</small><strong>{selectedNodes.length > 1 ? `${selectedNodes.length} 个图层` : selectedNode?.id === 'scene-background' ? '画板底图（已锁定）' : selectedNode?.type === 'image' ? '图片图层' : '文字图层'}</strong></p></div>
              {selectedNode && <><div className="property-grid"><label>X<input type="number" disabled={selectedIsBackground || selectedNode.locked} value={Math.round(selectedNode.x)} onChange={(event) => onUpdateSelected({ x: Number(event.target.value) })} /></label><label>Y<input type="number" disabled={selectedIsBackground || selectedNode.locked} value={Math.round(selectedNode.y)} onChange={(event) => onUpdateSelected({ y: Number(event.target.value) })} /></label></div>
              {selectedNode.type === 'text' && <><label className="property-label">文字内容<textarea value={selectedNode.text} onChange={(event) => onUpdateSelected({ text: event.target.value } as Partial<CanvasNode>)} /></label><div className="property-grid"><label>字号<input type="number" value={selectedNode.fontSize} onChange={(event) => onUpdateSelected({ fontSize: Number(event.target.value) } as Partial<CanvasNode>)} /></label><label>颜色<input type="color" value={selectedNode.fill} onChange={(event) => onUpdateSelected({ fill: event.target.value } as Partial<CanvasNode>)} /></label></div></>}</>}
              <div className="inspector-section layer-actions"><div className="section-heading"><span>对齐</span><small>{selectedNodes.length > 1 ? '相对选择范围' : '相对画板'}</small></div><div className="alignment-grid"><button title="左对齐" onClick={() => onAlign('left')}><AlignStartVertical size={16} /></button><button title="水平居中" onClick={() => onAlign('center')}><AlignCenter size={16} /></button><button title="右对齐" onClick={() => onAlign('right')}><AlignEndVertical size={16} /></button><button title="顶对齐" onClick={() => onAlign('top')}><AlignStartHorizontal size={16} /></button><button title="垂直居中" onClick={() => onAlign('middle')}><AlignCenter size={16} className="rotate-icon" /></button><button title="底对齐" onClick={() => onAlign('bottom')}><AlignEndHorizontal size={16} /></button></div><div className="distribution-actions"><button disabled={selectedNodes.length < 3} onClick={() => onDistribute('horizontal')}><AlignHorizontalDistributeCenter size={15} />水平分布</button><button disabled={selectedNodes.length < 3} onClick={() => onDistribute('vertical')}><AlignVerticalDistributeCenter size={15} />垂直分布</button></div><div><button className="row-action" disabled={selectedIsBackground || selectedNodes.length !== 1} onClick={onLayerUp}><Layers3 size={15} />上移一层</button><button className="row-action" disabled={selectedIsBackground || selectedNodes.length !== 1} onClick={onLayerDown}><ArrowDownToLine size={15} />下移一层</button></div></div>
            </>
          ) : <div className="empty-inspector"><MousePointer2 size={28} /><strong>选择一个或多个元素</strong><p>拖拽空白区域可框选；Shift 点击追加选择，随后可批量对齐与分布。</p></div>}
        </div>
      )}

      {tab === 'history' && (
        <div className="inspector-scroll version-list">
          <div className="version-item current"><span>V{canvasVersion ?? 1}</span><p><strong>当前已保存画布</strong><small>每个图型独立保存 · 自动同步</small></p><em>当前</em></div>
          <p className="version-note">完整版本回溯尚未开启；当前只展示数据库中真实存在的最新版。</p>
        </div>
      )}

      <div className="generate-footer">
        <button className="generate-button" onClick={onGenerate} disabled={generating}>
          {generating ? <><span className="spinner" />正在加入队列</> : <><Sparkles size={18} />生成 {variants} 个候选</>}
        </button>
        <small><kbd>⌘</kbd><kbd>↵</kbd> 入队 · 候选仅在本地暂存</small>
      </div>
    </aside>
  )
}

export function StudioPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedProduct = useAppStore((state) => state.selectedProduct)
  const selectedTask = useAppStore((state) => state.selectedTask)
  const selectedShot = useAppStore((state) => state.selectedShot)
  const setSelectedProduct = useAppStore((state) => state.setSelectedProduct)
  const setSelectedTask = useAppStore((state) => state.setSelectedTask)
  const setSelectedShot = useAppStore((state) => state.setSelectedShot)
  const jobs = useAppStore((state) => state.jobs)
  const activeRunId = useAppStore((state) => state.activeRunId)
  const addJob = useAppStore((state) => state.addJob)
  const updateJob = useAppStore((state) => state.updateJob)
  const setActiveRunId = useAppStore((state) => state.setActiveRunId)
  const notify = useAppStore((state) => state.notify)
  const apiOnline = useAppStore((state) => state.apiOnline)
  const demoMode = useAppStore((state) => state.demoMode)
  const workspace = useAppStore((state) => state.workspace)
  const refreshWorkspace = useAppStore((state) => state.refreshWorkspace)
  const canvasInsertRequest = useAppStore((state) => state.canvasInsertRequest)
  const consumeCanvasInsert = useAppStore((state) => state.consumeCanvasInsert)

  const [nodes, setNodes] = useState<CanvasNode[]>([])
  const [past, setPast] = useState<CanvasNode[][]>([])
  const [future, setFuture] = useState<CanvasNode[][]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [tool, setTool] = useState<'select' | 'hand'>('select')
  const [spaceHand, setSpaceHand] = useState(false)
  const [viewport, setViewport] = useState<CanvasViewport>({ x: 0, y: 0, zoom: 0.78, mode: 'fit' })
  const [prompt, setPromptState] = useState(() => defaultPrompt(selectedProduct))
  const [variants, setVariants] = useState(4)
  const [providerConfig, setProviderConfig] = useState<ProviderConfig>()
  const [providerChoice, setProviderChoice] = useState('default')
  const [quality, setQuality] = useState<ProviderQuality>('low')
  const [submittedReferences, setSubmittedReferences] = useState<PendingReferenceImage[]>([])
  const [preflightState, setPreflightState] = useState<'idle' | 'checking' | 'passed' | 'failed'>('idle')
  const [generating, setGenerating] = useState(false)
  const [resultAssets, setResultAssets] = useState<AssetItem[]>([])
  const [saveState, setSaveState] = useState<SaveState>('loading')
  const [canvasVersion, setCanvasVersion] = useState<number>()
  const [reloadToken, setReloadToken] = useState(0)
  const [canvasFocused, setCanvasFocused] = useState(false)
  const [assetPanelOpen, setAssetPanelOpen] = useState(true)
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const hydrating = useRef(true)
  const nodesRef = useRef(nodes)
  const promptRef = useRef(prompt)
  const viewportRef = useRef(viewport)
  const revisions = useRef(new Map<string, number>())
  const dirtyCanvases = useRef(new Set<string>())
  const loadFailedCanvases = useRef(new Set<string>())
  const insertInFlight = useRef<string | undefined>(undefined)
  const switchSequence = useRef(0)
  const generationInFlight = useRef(false)
  const canvasHydrationEpoch = useRef(0)
  const canvasRef = useRef<StudioCanvasHandle>(null)
  const urlContextReady = useRef(false)
  const skipNextUrlSync = useRef(false)
  const canvasId = canvasDocumentId(selectedProduct, selectedTask, selectedShot)
  const hydrationKey = `${canvasId}-${reloadToken}`
  const renderedHydrationKey = useRef(hydrationKey)
  if (renderedHydrationKey.current !== hydrationKey) {
    renderedHydrationKey.current = hydrationKey
    hydrating.current = true
  }
  const activeCanvasId = useRef(canvasId)
  const activeTool = spaceHand ? 'hand' : tool

  useEffect(() => {
    if (!apiOnline || demoMode) return
    void getProviderConfig().then(setProviderConfig).catch(() => undefined)
  }, [apiOnline, demoMode])

  const selectedProductRecord = workspace?.products.find((product) => product.id === selectedProduct)
  const selectedTaskRecord = workspace?.combinations
    ?.find((combination) => combination.id === selectedProduct)
    ?.tasks.find((task) => task.name === selectedTask)
  const projectAssets = useMemo<AssetItem[]>(() => {
    const source = (selectedProductRecord?.images ?? []).map((image) => ({
      id: image.relativePath ?? image.url,
      name: image.name,
      url: image.url,
      kind: 'product' as const,
    }))
    const references = (selectedTaskRecord?.references ?? []).map((image) => ({
      id: image.relativePath ?? image.url,
      name: image.name,
      url: image.url,
      kind: 'reference' as const,
    }))
    return [...source, ...references.filter((reference) => !source.some((asset) => asset.id === reference.id))]
  }, [selectedProductRecord?.images, selectedTaskRecord?.references])
  const referenceAssets = useMemo<AssetItem[]>(() => (selectedTaskRecord?.references ?? []).map((image) => ({
    id: image.relativePath ?? image.url,
    name: image.name,
    url: image.url,
    kind: 'reference',
  })), [selectedTaskRecord?.references])

  const addSubmittedReferences = useCallback(async (files: FileList | null) => {
    const selected = Array.from(files ?? [])
    if (!selected.length) return
    const invalidType = selected.find((file) => !SUPPORTED_REFERENCE_TYPES.has(file.type))
    if (invalidType) {
      notify({ title: '参考图格式不支持', detail: `${invalidType.name} 不是 PNG、JPG 或 WebP 图片`, tone: 'warning' })
      return
    }
    const oversized = selected.find((file) => file.size > MAX_SUBMITTED_REFERENCE_BYTES)
    if (oversized) {
      notify({ title: '参考图太大', detail: `${oversized.name} 超过 12 MiB`, tone: 'warning' })
      return
    }
    const available = MAX_SUBMITTED_REFERENCES - submittedReferences.length
    if (available <= 0) return
    const accepted = selected.slice(0, available)
    try {
      const additions = await Promise.all(accepted.map(async (file, index) => ({
        id: `reference-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 7)}`,
        filename: file.name,
        dataUrl: await readImageDataUrl(file),
        size: file.size,
      })))
      setSubmittedReferences((current) => [...current, ...additions].slice(0, MAX_SUBMITTED_REFERENCES))
      if (selected.length > accepted.length) {
        notify({ title: '最多添加 3 张本次参考图', detail: '其余图片未加入本次生成', tone: 'neutral' })
      }
    } catch (error) {
      notify({ title: '无法读取参考图', detail: error instanceof Error ? error.message : '请重新选择图片', tone: 'warning' })
    }
  }, [notify, submittedReferences.length])

  const removeSubmittedReference = useCallback((id: string) => {
    setSubmittedReferences((current) => current.filter((reference) => reference.id !== id))
  }, [])

  useEffect(() => {
    if (!workspace || urlContextReady.current) return
    const requestedProduct = searchParams.get('product')
    const product = workspace.products.some((item) => item.id === requestedProduct)
      ? requestedProduct as string
      : selectedProduct
    const tasks = workspace.combinations?.find((item) => item.id === product)?.tasks ?? []
    const requestedTask = searchParams.get('task')
    const task = tasks.some((item) => item.name === requestedTask)
      ? requestedTask as string
      : tasks.some((item) => item.name === selectedTask) ? selectedTask : tasks[0]?.name ?? selectedTask
    const requestedShot = searchParams.get('shot') as ShotType | null
    const shot = requestedShot && shotOptions.some((item) => item.id === requestedShot)
      ? requestedShot
      : selectedShot
    setSelectedProduct(product)
    setSelectedTask(task)
    setSelectedShot(shot)
    urlContextReady.current = true
    skipNextUrlSync.current = true
    const resolved = new URLSearchParams(searchParams)
    resolved.set('product', product)
    resolved.set('task', task)
    resolved.set('shot', shot)
    if (resolved.toString() !== searchParams.toString()) setSearchParams(resolved, { replace: true })
  }, [searchParams, selectedProduct, selectedShot, selectedTask, setSearchParams, setSelectedProduct, setSelectedShot, setSelectedTask, workspace])

  useEffect(() => {
    if (!urlContextReady.current) return
    if (skipNextUrlSync.current) {
      skipNextUrlSync.current = false
      return
    }
    const next = new URLSearchParams(searchParams)
    next.set('product', selectedProduct)
    next.set('task', selectedTask)
    next.set('shot', selectedShot)
    if (next.toString() !== searchParams.toString()) setSearchParams(next, { replace: true })
  }, [searchParams, selectedProduct, selectedShot, selectedTask, setSearchParams])

  const selectLayer = (id: string, additive = false) => {
    setSelectedIds((current) => additive
      ? current.includes(id) ? current.filter((item) => item !== id) : [...current, id]
      : [id])
  }

  useEffect(() => {
    if (!workspace) return
    let cancelled = false
    if (!apiOnline || demoMode) {
      setResultAssets([])
      return () => { cancelled = true }
    }
    void listCandidates({ reviewStatus: 'selected' })
      .then((candidates) => {
        if (cancelled) return
        setResultAssets(candidates
          .filter((candidate) => candidate.product === selectedProduct && candidate.task === selectedTask && candidate.shot === selectedShot && candidate.url)
          .map((candidate) => ({ id: candidate.id, name: candidate.name ?? `${candidate.task} · 候选 ${candidate.variant}`, url: candidate.url, kind: 'output', dimensions: candidate.width && candidate.height ? `${candidate.width} × ${candidate.height}` : undefined })))
      })
      .catch(() => { if (!cancelled) setResultAssets([]) })
    return () => { cancelled = true }
  }, [activeRunId, apiOnline, demoMode, selectedProduct, selectedShot, selectedTask])

  const markDirty = useCallback(() => {
    if (!generationInFlight.current && !hydrating.current && !loadFailedCanvases.current.has(activeCanvasId.current)) {
      const id = activeCanvasId.current
      revisions.current.set(id, (revisions.current.get(id) ?? 0) + 1)
      dirtyCanvases.current.add(id)
      writeCanvasCache(id, {
        nodes: nodesRef.current,
        prompt: promptRef.current,
        shot: selectedShot,
        task: selectedTask,
        viewport: viewportRef.current,
      }, true)
      setSaveState('dirty')
    }
  }, [selectedShot, selectedTask])

  const commitNodes = useCallback((next: CanvasNode[]) => {
    if (generationInFlight.current) return
    const previous = nodesRef.current
    nodesRef.current = next
    setPast((items) => [...items.slice(-19), previous])
    setNodes(next)
    setFuture([])
    markDirty()
  }, [markDirty])

  const setPrompt = (next: PromptDraft) => {
    if (generationInFlight.current) return
    promptRef.current = next
    setPromptState(next)
    setPreflightState('idle')
    markDirty()
  }

  const changeViewport = useCallback((next: CanvasViewport) => {
    if (generationInFlight.current) return
    viewportRef.current = next
    setViewport(next)
    markDirty()
  }, [markDirty])

  const persistCanvas = useCallback(async (
    id: string,
    snapshot: CanvasSnapshot,
    revision: number,
    updateActiveUi: boolean,
    withNotice = false,
  ): Promise<boolean> => {
    writeCanvasCache(id, snapshot, true)
    if (!apiOnline) {
      if (updateActiveUi && activeCanvasId.current === id) setSaveState('saved')
      if (withNotice) notify({ title: '画布已保存到当前会话', detail: '连接本地服务后会自动写入数据库', tone: 'neutral' })
      return true
    }
    if (updateActiveUi && activeCanvasId.current === id) setSaveState('saving')
    const previousSave = canvasSaveQueues.get(id) ?? Promise.resolve()
    const operation = previousSave
      .catch(() => undefined)
      .then(() => saveCanvas(id, snapshot))
    const queueTail = operation.then(() => undefined, () => undefined)
    canvasSaveQueues.set(id, queueTail)
    void queueTail.then(() => {
      if (canvasSaveQueues.get(id) === queueTail) canvasSaveQueues.delete(id)
    })
    try {
      const saved = await operation
      const stillCurrentRevision = (revisions.current.get(id) ?? 0) === revision
      if (stillCurrentRevision) {
        dirtyCanvases.current.delete(id)
        writeCanvasCache(id, snapshot, false)
      }
      if (updateActiveUi && activeCanvasId.current === id) {
        setSaveState(stillCurrentRevision ? 'saved' : 'dirty')
        if (typeof saved.version === 'number') setCanvasVersion(saved.version)
      }
      if (withNotice) notify({ title: '画布已保存', detail: `${snapshot.task} · ${snapshot.shot}`, tone: 'success' })
      return true
    } catch (error) {
      if (updateActiveUi && activeCanvasId.current === id) setSaveState('save-error')
      if (withNotice) notify({ title: '保存失败', detail: error instanceof Error ? error.message : '未知错误', tone: 'warning' })
      return false
    }
  }, [apiOnline, notify])

  const save = useCallback(async (withNotice = false): Promise<boolean> => {
    if (hydrating.current || saveState === 'load-error') return false
    const id = activeCanvasId.current
    const revision = revisions.current.get(id) ?? 0
    const snapshot: CanvasSnapshot = {
      nodes: nodesRef.current,
      prompt: promptRef.current,
      shot: selectedShot,
      task: selectedTask,
      viewport: viewportRef.current,
    }
    return persistCanvas(id, snapshot, revision, true, withNotice)
  }, [persistCanvas, saveState, selectedShot, selectedTask])

  useEffect(() => {
    canvasHydrationEpoch.current += 1
    let cancelled = false
    activeCanvasId.current = canvasId
    hydrating.current = true
    setSaveState('loading')
    setSelectedIds([])
    setPast([])
    setFuture([])
    const hydrate = async () => {
      const cached = readCanvasCache(canvasId)
      try {
        const stored = apiOnline ? await loadCanvas(canvasId) : undefined
        if (cancelled) return
        const source = cached?.pendingSync ? cached.snapshot : (stored ?? cached?.snapshot)
        const promptDefaults = defaultPrompt(selectedProduct)
        const nextNodes = Array.isArray(source?.nodes) ? source.nodes as CanvasNode[] : initialNodes(selectedShot, demoMode)
        const nextPrompt = source?.prompt && typeof source.prompt === 'object'
          ? { ...promptDefaults, ...source.prompt as PromptDraft }
          : promptDefaults
        const nextViewport = normalizeViewport(source?.viewport) ?? { x: 0, y: 0, zoom: 0.78, mode: 'fit' as const }
        nodesRef.current = nextNodes
        promptRef.current = nextPrompt
        viewportRef.current = nextViewport
        setNodes(nextNodes)
        setPromptState(nextPrompt)
        setViewport(nextViewport)
        setCanvasVersion(typeof stored?.version === 'number' ? stored.version : undefined)
        loadFailedCanvases.current.delete(canvasId)
        dirtyCanvases.current.delete(canvasId)
        revisions.current.set(canvasId, 0)
        const hasPendingLocalChanges = cached?.pendingSync === true
        const needsSync = apiOnline && (hasPendingLocalChanges || !stored)
        if (needsSync) {
          revisions.current.set(canvasId, 1)
          dirtyCanvases.current.add(canvasId)
          writeCanvasCache(canvasId, { nodes: nextNodes, prompt: nextPrompt, shot: selectedShot, task: selectedTask, viewport: nextViewport }, true)
          setSaveState('dirty')
        } else if (!apiOnline && hasPendingLocalChanges) {
          revisions.current.set(canvasId, 1)
          dirtyCanvases.current.add(canvasId)
          writeCanvasCache(canvasId, { nodes: nextNodes, prompt: nextPrompt, shot: selectedShot, task: selectedTask, viewport: nextViewport }, true)
          setSaveState('saved')
        } else {
          if (source) writeCanvasCache(canvasId, { nodes: nextNodes, prompt: nextPrompt, shot: selectedShot, task: selectedTask, viewport: nextViewport }, false)
          setSaveState('saved')
        }
      } catch (error) {
        if (!cancelled) {
          const fallback = cached?.snapshot
          const promptDefaults = defaultPrompt(selectedProduct)
          const nextNodes = Array.isArray(fallback?.nodes) ? fallback.nodes : initialNodes(selectedShot, demoMode)
          const nextPrompt = fallback?.prompt && typeof fallback.prompt === 'object' ? { ...promptDefaults, ...fallback.prompt } : promptDefaults
          const nextViewport = normalizeViewport(fallback?.viewport) ?? { x: 0, y: 0, zoom: 0.78, mode: 'fit' as const }
          nodesRef.current = nextNodes
          promptRef.current = nextPrompt
          viewportRef.current = nextViewport
          setNodes(nextNodes)
          setPromptState(nextPrompt)
          setViewport(nextViewport)
          setCanvasVersion(undefined)
          loadFailedCanvases.current.add(canvasId)
          dirtyCanvases.current.delete(canvasId)
          setSaveState('load-error')
          notify({ title: '画布加载失败', detail: error instanceof Error ? error.message : '请重新加载后再编辑', tone: 'warning' })
        }
      } finally {
        if (!cancelled) hydrating.current = false
      }
    }
    void hydrate()
    return () => {
      cancelled = true
      if (activeCanvasId.current !== canvasId || !dirtyCanvases.current.has(canvasId) || loadFailedCanvases.current.has(canvasId)) return
      const revision = revisions.current.get(canvasId) ?? 0
      const snapshot: CanvasSnapshot = {
        nodes: nodesRef.current,
        prompt: promptRef.current,
        shot: selectedShot,
        task: selectedTask,
        viewport: viewportRef.current,
      }
      writeCanvasCache(canvasId, snapshot, true)
      void persistCanvas(canvasId, snapshot, revision, false)
    }
  }, [apiOnline, canvasId, demoMode, notify, persistCanvas, reloadToken, selectedProduct, selectedShot, selectedTask, workspace])

  useEffect(() => {
    const cacheBeforeUnload = () => {
      const id = activeCanvasId.current
      if (!dirtyCanvases.current.has(id) || loadFailedCanvases.current.has(id)) return
      writeCanvasCache(id, {
        nodes: nodesRef.current,
        prompt: promptRef.current,
        shot: selectedShot,
        task: selectedTask,
        viewport: viewportRef.current,
      }, true)
    }
    window.addEventListener('beforeunload', cacheBeforeUnload)
    return () => window.removeEventListener('beforeunload', cacheBeforeUnload)
  }, [selectedShot, selectedTask])

  useEffect(() => {
    if (saveState !== 'dirty') return
    const timer = window.setTimeout(() => { void save(false) }, 850)
    return () => window.clearTimeout(timer)
  }, [save, saveState])

  const undo = useCallback(() => {
    if (generationInFlight.current) return
    const previous = past.at(-1)
    if (!previous) return
    const current = nodesRef.current
    setFuture((items) => [current, ...items])
    nodesRef.current = previous
    setNodes(previous)
    setPast((items) => items.slice(0, -1))
    markDirty()
  }, [markDirty, past])

  const redo = useCallback(() => {
    if (generationInFlight.current) return
    const next = future[0]
    if (!next) return
    setPast((items) => [...items, nodesRef.current])
    nodesRef.current = next
    setNodes(next)
    setFuture((items) => items.slice(1))
    markDirty()
  }, [future, markDirty])

  const addAsset = useCallback(async (asset: AssetItem, mode: 'background' | 'layer' = 'layer') => {
    if (generationInFlight.current) return false
    const targetCanvasId = activeCanvasId.current
    const natural = await imageDimensions(asset.url)
    if (generationInFlight.current || activeCanvasId.current !== targetCanvasId || hydrating.current || loadFailedCanvases.current.has(targetCanvasId)) return false
    const currentNodes = nodesRef.current
    if (mode === 'background') {
      const scale = Math.max(ARTBOARD.width / natural.width, ARTBOARD.height / natural.height)
      const width = natural.width * scale
      const height = natural.height * scale
      const background: CanvasNode = {
        id: 'scene-background',
        name: '画板底图',
        locked: true,
        type: 'image',
        src: asset.url,
        x: ARTBOARD.x + (ARTBOARD.width - width) / 2,
        y: ARTBOARD.y + (ARTBOARD.height - height) / 2,
        width,
        height,
      }
      commitNodes([background, ...currentNodes.filter((node) => node.id !== 'scene-background')])
      setSelectedIds([])
    } else {
      const scale = Math.min(320 / natural.width, 320 / natural.height)
      const width = Math.max(24, natural.width * scale)
      const height = Math.max(24, natural.height * scale)
      const offset = (currentNodes.length % 4) * 20
      const node: CanvasNode = {
        id: `image-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        name: asset.name,
        type: 'image',
        src: asset.url,
        x: ARTBOARD.x + (ARTBOARD.width - width) / 2 + offset,
        y: ARTBOARD.y + (ARTBOARD.height - height) / 2 + offset,
        width,
        height,
      }
      commitNodes([...currentNodes, node])
      setSelectedIds([node.id])
    }
    notify({ title: mode === 'background' ? '已设为画板底图' : '素材已加入画布', detail: asset.name, tone: 'success' })
    return true
  }, [commitNodes, notify])

  const importAsset = useCallback(async (file: File) => {
    if (generationInFlight.current) return
    if (!apiOnline || demoMode) {
      notify({ title: '需要连接本地工作区', detail: '素材不会以内嵌 base64 形式写入画布；连接 API 后可稳定导入。', tone: 'warning' })
      return
    }
    try {
      const imported = await importWorkspaceAsset(file, selectedProduct)
      const asset: AssetItem = {
        id: imported.relativePath ?? imported.url,
        name: file.name,
        url: imported.url,
        kind: 'product',
      }
      await refreshWorkspace()
      await addAsset(asset)
    } catch (error) {
      notify({ title: '素材导入失败', detail: error instanceof Error ? error.message : '请检查图片格式与大小', tone: 'warning' })
    }
  }, [addAsset, apiOnline, demoMode, notify, refreshWorkspace, selectedProduct])

  useEffect(() => {
    const request = canvasInsertRequest
    if (!request || insertInFlight.current === request.requestId || generating) return
    const targetsAnotherCanvas = request.productId !== selectedProduct || request.taskId !== selectedTask || request.shot !== selectedShot
    if (targetsAnotherCanvas) {
      insertInFlight.current = request.requestId
      const sequence = ++switchSequence.current
      void (async () => {
        const hasPendingChanges = dirtyCanvases.current.has(activeCanvasId.current) || saveState === 'save-error' || saveState === 'saving'
        if (hasPendingChanges && saveState !== 'load-error') {
          const saved = await save(false)
          if (!saved) {
            notify({ title: '未切换画布', detail: '当前画布保存失败，请重试后再送入候选图', tone: 'warning' })
            consumeCanvasInsert()
            return
          }
        }
        if (sequence !== switchSequence.current) return
        setSelectedProduct(request.productId)
        setSelectedTask(request.taskId)
        setSelectedShot(request.shot)
      })().finally(() => {
        if (insertInFlight.current === request.requestId) insertInFlight.current = undefined
      })
      return
    }
    if (hydrating.current) return
    insertInFlight.current = request.requestId
    void addAsset(request.asset, request.mode)
      .then((inserted) => {
        if (inserted) consumeCanvasInsert()
        else notify({ title: '候选尚未插入', detail: '目标画布在素材加载期间发生切换，请返回后重试。', tone: 'warning' })
      })
      .finally(() => {
        insertInFlight.current = undefined
      })
  }, [addAsset, canvasInsertRequest, consumeCanvasInsert, generating, notify, save, saveState, selectedProduct, selectedShot, selectedTask, setSelectedProduct, setSelectedShot, setSelectedTask])

  const addText = () => {
    if (generationInFlight.current) return
    const node: CanvasNode = { id: `text-${Date.now()}`, name: '新文字', type: 'text', text: '输入标题', x: 380, y: 680, width: 280, fontSize: 24, fontStyle: 'bold', fill: '#173f38' }
    commitNodes([...nodesRef.current, node])
    setSelectedIds([node.id])
  }

  const deleteSelected = useCallback(() => {
    const removable = new Set(nodesRef.current.filter((node) => selectedIds.includes(node.id) && node.id !== 'scene-background' && !node.locked).map((node) => node.id))
    if (!removable.size) return
    commitNodes(nodesRef.current.filter((node) => !removable.has(node.id)))
    setSelectedIds([])
  }, [commitNodes, selectedIds])

  const duplicateSelected = useCallback(() => {
    const currentNodes = nodesRef.current
    const sources = currentNodes.filter((node) => selectedIds.includes(node.id) && node.id !== 'scene-background' && !node.locked)
    if (!sources.length) return
    const stamp = Date.now()
    const copies = sources.map((source, index) => ({ ...source, id: `${source.type}-${stamp}-${index}`, name: `${nodeLabel(source)} 副本`, x: source.x + 24, y: source.y + 24 } as CanvasNode))
    commitNodes([...currentNodes, ...copies])
    setSelectedIds(copies.map((node) => node.id))
  }, [commitNodes, selectedIds])

  const updateSelected = (patch: Partial<CanvasNode>) => {
    const selected = new Set(selectedIds)
    if (!selected.size) return
    commitNodes(nodesRef.current.map((node) => selected.has(node.id) && node.id !== 'scene-background' && !node.locked ? ({ ...node, ...patch } as CanvasNode) : node))
  }

  const updateNode = (id: string, patch: Partial<CanvasNode>) => {
    commitNodes(nodesRef.current.map((node) => node.id === id ? ({ ...node, ...patch } as CanvasNode) : node))
  }

  const alignSelected = (alignment: 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom') => {
    const active = nodesRef.current.filter((node) => selectedIds.includes(node.id) && node.id !== 'scene-background' && !node.locked)
    if (!active.length) return
    const bounds = active.map(nodeBounds)
    const target = active.length === 1 ? { left: ARTBOARD.x, right: ARTBOARD.x + ARTBOARD.width, top: ARTBOARD.y, bottom: ARTBOARD.y + ARTBOARD.height }
      : { left: Math.min(...bounds.map((box) => box.x)), right: Math.max(...bounds.map((box) => box.right)), top: Math.min(...bounds.map((box) => box.y)), bottom: Math.max(...bounds.map((box) => box.bottom)) }
    const selected = new Set(active.map((node) => node.id))
    commitNodes(nodesRef.current.map((node) => {
      if (!selected.has(node.id)) return node
      const box = nodeBounds(node)
      if (alignment === 'left') return { ...node, x: target.left }
      if (alignment === 'center') return { ...node, x: (target.left + target.right - box.width) / 2 }
      if (alignment === 'right') return { ...node, x: target.right - box.width }
      if (alignment === 'top') return { ...node, y: target.top }
      if (alignment === 'middle') return { ...node, y: (target.top + target.bottom - box.height) / 2 }
      return { ...node, y: target.bottom - box.height }
    }))
  }

  const distributeSelected = (axis: 'horizontal' | 'vertical') => {
    const active = nodesRef.current.filter((node) => selectedIds.includes(node.id) && node.id !== 'scene-background' && !node.locked)
    if (active.length < 3) return
    const ordered = [...active].sort((a, b) => axis === 'horizontal' ? a.x - b.x : a.y - b.y)
    const boxes = ordered.map(nodeBounds)
    const start = axis === 'horizontal' ? boxes[0].x : boxes[0].y
    const end = axis === 'horizontal' ? boxes.at(-1)!.right : boxes.at(-1)!.bottom
    const occupied = boxes.reduce((sum, box) => sum + (axis === 'horizontal' ? box.width : box.height), 0)
    const gap = (end - start - occupied) / (ordered.length - 1)
    const positions = new Map<string, number>()
    let cursor = start
    ordered.forEach((node, index) => {
      positions.set(node.id, cursor)
      cursor += (axis === 'horizontal' ? boxes[index].width : boxes[index].height) + gap
    })
    commitNodes(nodesRef.current.map((node) => positions.has(node.id) ? { ...node, [axis === 'horizontal' ? 'x' : 'y']: positions.get(node.id)! } : node))
  }

  const moveLayer = (id: string, direction: 'up' | 'down') => {
    if (id === 'scene-background') return
    const currentNodes = nodesRef.current
    const index = currentNodes.findIndex((node) => node.id === id)
    const target = direction === 'up' ? index + 1 : index - 1
    if (index < 0 || target < 1 || target >= currentNodes.length) return
    const next = [...currentNodes]
    ;[next[index], next[target]] = [next[target], next[index]]
    commitNodes(next)
  }

  const nudgeSelected = useCallback((dx: number, dy: number) => {
    const currentNodes = nodesRef.current
    const selected = new Set(currentNodes.filter((node) => selectedIds.includes(node.id) && node.id !== 'scene-background' && !node.locked).map((node) => node.id))
    if (!selected.size) return
    commitNodes(currentNodes.map((item) => selected.has(item.id) ? { ...item, x: item.x + dx, y: item.y + dy } : item))
  }, [commitNodes, selectedIds])

  const generate = useCallback(async () => {
    if (generationInFlight.current || generating) return
    if (hydrating.current || saveState === 'load-error') {
      notify({ title: '暂时无法生成', detail: '请先重新加载当前画布，避免使用未确认的内容', tone: 'warning' })
      return
    }
    if (activeCanvasId.current !== canvasId) {
      notify({ title: '暂时无法生成', detail: '画布上下文正在切换，请载入完成后再试', tone: 'warning' })
      return
    }
    const revision = revisions.current.get(canvasId) ?? 0
    const context: GenerationContext = {
      canvasId,
      revision,
      hydrationEpoch: canvasHydrationEpoch.current,
      snapshot: cloneCanvasSnapshot({
        nodes: nodesRef.current,
        prompt: promptRef.current,
        shot: selectedShot,
        task: selectedTask,
        viewport: viewportRef.current,
      }),
      product: selectedProduct,
      productLabel: selectedProductRecord?.name ?? selectedProduct,
      task: selectedTask,
      shot: selectedShot,
      variants,
      providerChoice,
      providerConfig: cloneProviderConfig(providerConfig),
      quality,
      referenceImages: submittedReferences.map((reference) => ({ ...reference })),
    }
    const assertContextUnchanged = () => {
      const currentRevision = revisions.current.get(context.canvasId) ?? 0
      if (
        hydrating.current
        || activeCanvasId.current !== context.canvasId
        || currentRevision !== context.revision
        || canvasHydrationEpoch.current !== context.hydrationEpoch
      ) {
        throw new Error('生成准备期间画布或图型已发生变化，未提交付费任务；请重新确认后生成')
      }
    }
    generationInFlight.current = true
    setGenerating(true)
    if (apiOnline && !demoMode && workspace?.liveGenerationEnabled) {
      try {
        setPreflightState('checking')
        await previewWorkflow(context.product, context.shot, context.task)
        assertContextUnchanged()
        setPreflightState('passed')
        const {
          effectiveChannel,
          fixedUnavailable,
        } = resolveProviderEstimate(context.providerConfig, context.providerChoice, context.quality)
        const unavailableFixedRoute = fixedUnavailable || (
          context.providerChoice === 'default'
          && context.providerConfig?.routing.mode === 'fixed'
          && !effectiveChannel
        )
        if (unavailableFixedRoute) {
          throw new Error('所选固定渠道已停用或不存在，请重新选择渠道后再生成')
        }
        const estimatedCost = effectiveChannel
          ? context.variants * effectiveChannel.rates[context.quality]
          : 0
        const costNotice = estimatedCost > 0
          ? `按当前渠道估算约 ${estimatedCost.toFixed(4)} ${effectiveChannel?.currency ?? ''}`
          : '当前渠道没有可展示的费率，实际调用仍可能产生费用'
        const shotName = shotOptions.find((shot) => shot.id === context.shot)?.label ?? context.shot
        const qualityName = ({ low: '低', medium: '中', high: '高' } as Record<ProviderQuality, string>)[context.quality]
        const channelName = effectiveChannel?.name
          ?? (context.providerChoice === 'auto' ? 'Auto（待服务端解析）' : '工作区默认（待服务端解析）')
        const confirmed = window.confirm([
          '即将提交付费生成任务：',
          `商品：${context.productLabel}${context.productLabel === context.product ? '' : `（${context.product}）`}`,
          `任务：${context.task}`,
          `图型：${shotName}`,
          `渠道：${channelName}`,
          `质量：${qualityName}`,
          `候选数：${context.variants} 张`,
          `本次参考图：${context.referenceImages.length} 张`,
          `费用：${costNotice}`,
          '',
          '确认继续？',
        ].join('\n'))
        if (!confirmed) return
        assertContextUnchanged()
        if (dirtyCanvases.current.has(context.canvasId)) {
          const saved = await persistCanvas(
            context.canvasId,
            context.snapshot,
            context.revision,
            true,
            false,
          )
          if (!saved) throw new Error('当前画布保存失败，已停止提交生成任务')
          assertContextUnchanged()
        }
        assertContextUnchanged()
        const run = await createGenerationRun({
          product: context.product,
          tasks: [context.task],
          shots: [context.shot],
          items: [{ task: context.task, shot: context.shot }],
          variants: context.variants,
          concurrency: 1,
          creativeBrief: context.snapshot.prompt,
          referenceImages: context.referenceImages.map(({ filename, dataUrl }) => ({ filename, dataUrl })),
          providerMode: context.providerChoice === 'default' ? 'default' : context.providerChoice === 'auto' ? 'auto' : 'fixed',
          providerChannelId: context.providerChoice !== 'default' && context.providerChoice !== 'auto' ? context.providerChoice : undefined,
          quality: context.quality,
          size: '1024x1024',
        })
        setActiveRunId(run.id)
        setSubmittedReferences([])
        notify({ title: '已交给本地 Skill 执行', detail: `运行 ${run.id} · ${context.variants} 个候选 · ${run.provider?.channelName ?? channelName}`, tone: 'success' })
        navigate(`/queue?run=${encodeURIComponent(run.id)}`)
      } catch (error) {
        setPreflightState('failed')
        notify({ title: '无法启动本地生成', detail: error instanceof Error ? error.message : '请检查 Skill 与参考图门槛', tone: 'warning' })
      } finally {
        generationInFlight.current = false
        setGenerating(false)
      }
      return
    }
    if (apiOnline && !workspace?.liveGenerationEnabled) {
      notify({ title: '真实生成尚未开启', detail: '请在本地环境设置 MUSEFORGE_ENABLE_LIVE_GENERATION=true 后重启 API', tone: 'warning' })
      generationInFlight.current = false
      setGenerating(false)
      return
    }
    const id = `demo-${Date.now()}`
    addJob({ id, title: `${context.task} · ${shotOptions.find((shot) => shot.id === context.shot)?.label} · 演示候选`, product: context.product, shot: context.shot, status: 'queued', progress: 0, createdAt: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) })
    notify({ title: '演示任务（未调用生图服务）', detail: '用于预览队列与审核交互', tone: 'neutral' })
    window.setTimeout(() => {
      updateJob(id, { status: 'running', progress: 68 })
      generationInFlight.current = false
      setGenerating(false)
    }, 500)
    window.setTimeout(() => updateJob(id, { status: 'succeeded', progress: 100, thumbnail: outputByShot[context.shot] }), 2200)
  }, [addJob, apiOnline, canvasId, demoMode, generating, navigate, notify, persistCanvas, providerChoice, providerConfig, quality, saveState, selectedProduct, selectedProductRecord?.name, selectedShot, selectedTask, setActiveRunId, submittedReferences, updateJob, variants, workspace?.liveGenerationEnabled])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      const editing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable
      const interactive = Boolean(target.closest('button, a, [role="button"], [contenteditable="true"]'))
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); void generate(); return }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') { event.preventDefault(); void save(true); return }
      if (editing || interactive || !canvasFocused || hydrating.current || saveState === 'load-error') return
      if (event.code === 'Space') { event.preventDefault(); setSpaceHand(true); return }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redo() : undo(); return }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'd') { event.preventDefault(); duplicateSelected(); return }
      if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); deleteSelected(); return }
      if (event.key === 'Escape') { setSelectedIds([]); return }
      if (event.key === '0') { event.preventDefault(); canvasRef.current?.fitArtboard(); return }
      if (event.key === '1') { event.preventDefault(); canvasRef.current?.zoomTo(1); return }
      const step = event.shiftKey ? 10 : 1
      if (event.key === 'ArrowLeft') { event.preventDefault(); nudgeSelected(-step, 0) }
      if (event.key === 'ArrowRight') { event.preventDefault(); nudgeSelected(step, 0) }
      if (event.key === 'ArrowUp') { event.preventDefault(); nudgeSelected(0, -step) }
      if (event.key === 'ArrowDown') { event.preventDefault(); nudgeSelected(0, step) }
      if (event.key.toLowerCase() === 'v') setTool('select')
      if (event.key.toLowerCase() === 'h') setTool('hand')
    }
    const onKeyUp = (event: KeyboardEvent) => { if (event.code === 'Space') setSpaceHand(false) }
    const onBlur = () => setSpaceHand(false)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', onBlur)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', onBlur)
    }
  }, [canvasFocused, deleteSelected, duplicateSelected, generate, nudgeSelected, redo, save, saveState, undo])

  const preflight = async () => {
    if (generationInFlight.current) return
    if (!apiOnline || demoMode) {
      setPreflightState('idle')
      notify({ title: '未执行真实预检', detail: '连接本地 API 后才能检查工作区 Prompt 与参考图清单', tone: 'neutral' })
      return
    }
    setPreflightState('checking')
    try {
      await previewWorkflow(selectedProduct, selectedShot, selectedTask)
      setPreflightState('passed')
      notify({ title: 'Skill 预检通过', detail: '参考图与 Prompt 结构有效', tone: 'success' })
    } catch (error) {
      setPreflightState('failed')
      notify({ title: '预检发现阻塞项', detail: error instanceof Error ? error.message : '请检查参考图清单', tone: 'warning' })
    }
  }

  const exportCanvas = () => {
    try {
      if (saveState === 'load-error' || hydrating.current) throw new Error('画布尚未可靠载入，请重新加载后再导出')
      const dataUrl = canvasRef.current?.exportPng(1024)
      if (!dataUrl) throw new Error('画板尚未准备完成')
      const link = document.createElement('a')
      link.href = dataUrl
      link.download = `${selectedProduct}-${selectedTask}-${selectedShot}-1024.png`
      link.click()
      notify({ title: '画板已导出', detail: '1024 × 1024 PNG，不含安全线与操作框', tone: 'success' })
    } catch (error) {
      notify({ title: '暂时无法导出', detail: error instanceof Error ? error.message : '请稍后重试', tone: 'warning' })
    }
  }

  const switchShot = async (shot: ShotType) => {
    if (generationInFlight.current || generating || shot === selectedShot) return
    const sequence = ++switchSequence.current
    const hasPendingChanges = dirtyCanvases.current.has(activeCanvasId.current) || saveState === 'save-error' || saveState === 'saving'
    if (hasPendingChanges && saveState !== 'load-error') {
      const saved = await save(false)
      if (sequence !== switchSequence.current) return
      if (!saved) {
        notify({ title: '未切换图型', detail: '当前画布保存失败，请重试后再切换', tone: 'warning' })
        return
      }
    }
    if (sequence !== switchSequence.current) return
    setSelectedShot(shot)
  }

  const selectedNodes = useMemo(() => selectedIds.map((id) => nodes.find((node) => node.id === id)).filter((node): node is CanvasNode => Boolean(node)), [nodes, selectedIds])
  const primarySelectedId = selectedIds.at(-1)
  const saveLabel = saveState === 'loading'
    ? '正在加载画布'
    : saveState === 'dirty'
      ? '有未保存更改'
      : saveState === 'saving'
        ? '保存中…'
        : saveState === 'load-error'
          ? '加载失败'
          : saveState === 'save-error'
            ? '保存失败'
            : apiOnline ? '已自动保存' : '当前会话已保存'

  return (
    <div className="studio-page">
      <div className="studio-subbar">
        <div className="shot-switcher">
          {shotOptions.map((shot) => <button key={shot.id} className={selectedShot === shot.id ? 'active' : ''} disabled={generating} onClick={() => { void switchShot(shot.id) }}><span>{shot.label}</span><small>{shot.short}</small></button>)}
        </div>
        <div className="studio-status"><span className={`autosave ${saveState}`}><i />{saveLabel}</span><button className="button ghost" disabled={saveState === 'loading' || saveState === 'load-error'} onClick={exportCanvas}><Download size={15} />导出 1024</button><button className="button dark" disabled={generating || saveState === 'loading' || saveState === 'load-error'} onClick={() => { void generate() }}><Zap size={15} />生成候选</button></div>
      </div>
      <div className={`studio-workspace ${assetPanelOpen ? '' : 'asset-panel-collapsed'} ${inspectorOpen ? '' : 'inspector-collapsed'}`} onPointerDownCapture={(event) => setCanvasFocused(Boolean((event.target as HTMLElement).closest('.canvas-column')))}>
        {(saveState === 'loading' || saveState === 'load-error') && (
          <div className="canvas-load-blocker" role={saveState === 'load-error' ? 'alert' : 'status'}>
            <div>
              <CircleAlert size={26} />
              <strong>{saveState === 'load-error' ? '当前画布未能可靠载入' : '正在载入画布'}</strong>
              <p>{saveState === 'load-error' ? '已暂停编辑、生成和自动保存，避免默认内容覆盖原稿。' : '正在恢复节点、Prompt 与视图位置，请稍候。'}</p>
              {saveState === 'load-error' && <button className="button dark" onClick={() => setReloadToken((value) => value + 1)}>重新加载</button>}
            </div>
          </div>
        )}
        <AssetPanel onAdd={(asset) => { void addAsset(asset) }} onImport={(file) => { void importAsset(file) }} projectAssets={projectAssets} resultAssets={resultAssets} product={selectedProduct} taskLabel={selectedTask} referenceCount={selectedTaskRecord?.referenceCount ?? 0} nodes={nodes} selectedIds={selectedIds} onSelect={selectLayer} onUpdateNode={updateNode} onMoveLayer={moveLayer} disabled={generating} />
        <section className="canvas-column">
          <button className="panel-rail-toggle left" onClick={() => setAssetPanelOpen((value) => !value)} title={assetPanelOpen ? '收起素材面板' : '展开素材面板'} aria-label={assetPanelOpen ? '收起素材面板' : '展开素材面板'}>{assetPanelOpen ? <PanelLeftClose size={17} /> : <PanelLeftOpen size={17} />}</button>
          <button className="panel-rail-toggle right" onClick={() => setInspectorOpen((value) => !value)} title={inspectorOpen ? '收起生成面板' : '展开生成面板'} aria-label={inspectorOpen ? '收起生成面板' : '展开生成面板'}>{inspectorOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}</button>
          <div className="floating-toolbar">
            <button className={activeTool === 'select' ? 'active' : ''} disabled={generating} onClick={() => setTool('select')} title="选择工具 (V)"><MousePointer2 size={17} /></button>
            <button className={activeTool === 'hand' ? 'active' : ''} disabled={generating} onClick={() => setTool('hand')} title="抓手工具 (H / Space)"><Hand size={17} /></button>
            <span />
            <button onClick={addText} disabled={generating} title="添加文字"><TextCursorInput size={17} /></button>
            <button onClick={() => { if (projectAssets[0]) void addAsset(projectAssets[0]) }} disabled={generating || !projectAssets.length} title="添加项目图片"><ImagePlus size={17} /></button>
            <span />
            <button onClick={undo} disabled={generating || !past.length} title="撤销"><Undo2 size={17} /></button>
            <button onClick={redo} disabled={generating || !future.length} title="重做"><Redo2 size={17} /></button>
            <button onClick={duplicateSelected} disabled={generating || !selectedNodes.some((node) => node.id !== 'scene-background' && !node.locked)} title="复制"><Copy size={17} /></button>
            <button onClick={deleteSelected} disabled={generating || !selectedNodes.some((node) => node.id !== 'scene-background' && !node.locked)} title="删除"><Trash2 size={17} /></button>
          </div>
          {saveState !== 'loading' && <StudioCanvas key={hydrationKey} ref={canvasRef} nodes={nodes} onNodesChange={commitNodes} selectedIds={selectedIds} onSelectionChange={setSelectedIds} tool={activeTool} viewport={viewport} onViewportChange={changeViewport} artboardLabel={`${selectedTask} · ${shotOptions.find((shot) => shot.id === selectedShot)?.label ?? ''}`} readOnly={generating} />}
          {saveState === 'saved' && nodes.length === 0 && <div className="canvas-empty-state"><span><WandSparkles size={24} /></span><strong>从真实商品素材开始</strong><p>选择一张源图建立第一层，或先添加文字搭建设计骨架。空白画布不会自动混入示例内容。</p><div>{projectAssets[0] ? <button className="primary" disabled={generating} onClick={() => { void addAsset(projectAssets[0]) }}><ImagePlus size={15} />放入商品图</button> : <button className="primary" disabled={generating} onClick={() => navigate('/assets')}><ImagePlus size={15} />检查商品素材</button>}<button onClick={addText} disabled={generating}><TextCursorInput size={15} />添加文字</button></div></div>}
          <div className="zoom-control"><button disabled={generating} onClick={() => canvasRef.current?.zoomTo(viewport.zoom - 0.1)}><Minus size={14} /></button><input type="range" min="35" max="150" value={Math.round(viewport.zoom * 100)} disabled={generating} onChange={(event) => canvasRef.current?.zoomTo(Number(event.target.value) / 100)} /><span>{Math.round(viewport.zoom * 100)}%</span><button disabled={generating} onClick={() => canvasRef.current?.zoomTo(viewport.zoom + 0.1)}><Plus size={14} /></button><button disabled={generating} onClick={() => canvasRef.current?.fitArtboard()} title="适应画板 (0)"><Maximize2 size={14} /></button></div>
          <div className="canvas-hint"><Grip size={13} />空白拖拽框选 · Shift 多选 · 自动吸附 · 方向键微移</div>
          <button className="queue-peek" onClick={() => navigate(activeRunId ? `/queue?run=${encodeURIComponent(activeRunId)}` : '/queue')}><span><i />{activeRunId ? `真实运行 ${activeRunId.slice(0, 8)} 已接入队列` : demoMode && jobs.filter((job) => job.status === 'running').length ? `${jobs.filter((job) => job.status === 'running').length} 个演示任务生成中` : '本地候选暂存已启用'}</span><strong>查看运行队列 <ChevronDown size={14} /></strong></button>
        </section>
        <Inspector prompt={prompt} setPrompt={setPrompt} referenceAssets={referenceAssets} referenceCount={selectedTaskRecord?.referenceCount ?? 0} submittedReferences={submittedReferences} onAddReferences={(files) => { void addSubmittedReferences(files) }} onRemoveReference={removeSubmittedReference} variants={variants} setVariants={setVariants} providerConfig={providerConfig} providerChoice={providerChoice} setProviderChoice={setProviderChoice} quality={quality} setQuality={setQuality} preflightState={preflightState} selectedNodes={selectedNodes} onUpdateSelected={updateSelected} onGenerate={() => { void generate() }} onPreflight={() => { void preflight() }} onAlign={alignSelected} onDistribute={distributeSelected} onLayerUp={() => primarySelectedId && moveLayer(primarySelectedId, 'up')} onLayerDown={() => primarySelectedId && moveLayer(primarySelectedId, 'down')} generating={generating} canvasVersion={canvasVersion} />
      </div>
      <div className="studio-warning"><ScanSearch size={14} /><span>生成依据：任务 Prompt + 已发布参考图 + 本次参考图与文字指令</span><strong>{submittedReferences.length ? `已添加 ${submittedReferences.length} 张临时参考` : selectedTaskRecord?.hasReferenceManifest ? '参考清单已发布' : '参考清单缺失'}</strong></div>
    </div>
  )
}
