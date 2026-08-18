import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Check,
  CircleDot,
  ImageIcon,
  LoaderCircle,
  Minus,
  Play,
  RefreshCw,
  Search,
  Settings2,
  Sparkles,
} from 'lucide-react'
import { useNavigate } from 'react-router'
import { createGenerationRun, getProviderConfig, prepareWorkflow } from '../lib/api'
import { demoWorkspace } from '../lib/demo'
import {
  isMatrixCellSelected,
  matrixAxisSelectionState,
  matrixCellKey,
  selectAllMatrixCells,
  selectedMatrixItems,
  toggleMatrixCell,
  toggleMatrixColumn,
  toggleMatrixRow,
} from '../lib/matrixSelection'
import { resolveProviderEstimate } from '../lib/providerRouting'
import { useAppStore } from '../store/appStore'
import type { ProviderConfig, ProviderQuality, ShotType, WorkspaceTask } from '../types'
import '../batch.css'

const columns: Array<{ id: ShotType; label: string; short: string }> = [
  { id: 'main', label: '主图', short: 'MAIN' },
  { id: 'size', label: '尺寸图', short: 'SIZE' },
  { id: 'lifestyle-scene', label: '场景图', short: 'SCENE' },
  { id: 'detail', label: '细节图', short: 'DETAIL' },
  { id: 'comparison', label: '对比图', short: 'COMPARE' },
]
const shotIds = columns.map(({ id }) => id)
const defaultShotIds: ShotType[] = ['main', 'lifestyle-scene']

type CellState = 'ready' | 'review' | 'blocked'

const cellMeta: Record<CellState, { label: string; icon: typeof Check }> = {
  ready: { label: '可生成', icon: CircleDot },
  review: { label: '已有候选', icon: Sparkles },
  blocked: { label: '资料阻塞', icon: AlertTriangle },
}

function demoTasks(product: string): WorkspaceTask[] {
  const shots = Object.fromEntries(columns.map(({ id, label }) => [id, {
    folder: label,
    imageCount: id === 'main' ? 1 : 0,
    images: id === 'main' ? [{ name: '演示候选', url: '/demo/product-studio.png' }] : [],
  }])) as WorkspaceTask['shots']
  return [{
    id: `${product}/单品`,
    name: '单品',
    product,
    kind: 'standalone',
    hasPrompts: true,
    promptCount: 5,
    preparedShots: [...shotIds],
    referenceCount: 2,
    hasReferenceManifest: true,
    generatedImageCount: 1,
    shots,
  }]
}

function taskState(task: WorkspaceTask, shot: ShotType): CellState {
  if (
    !task.hasPrompts
    || !task.preparedShots.includes(shot)
    || !task.hasReferenceManifest
    || task.referenceCount < 1
  ) return 'blocked'
  if ((task.shots[shot]?.imageCount ?? 0) > 0) return 'review'
  return 'ready'
}

export function MatrixPage() {
  const navigate = useNavigate()
  const workspace = useAppStore((state) => state.workspace) ?? demoWorkspace
  const demoMode = useAppStore((state) => state.demoMode)
  const apiOnline = useAppStore((state) => state.apiOnline)
  const selectedProduct = useAppStore((state) => state.selectedProduct)
  const setSelectedProduct = useAppStore((state) => state.setSelectedProduct)
  const setSelectedTask = useAppStore((state) => state.setSelectedTask)
  const setSelectedShot = useAppStore((state) => state.setSelectedShot)
  const setActiveRunId = useAppStore((state) => state.setActiveRunId)
  const refreshWorkspace = useAppStore((state) => state.refreshWorkspace)
  const notify = useAppStore((state) => state.notify)

  const [selectedCells, setSelectedCells] = useState<Set<string>>(new Set())
  const [variants, setVariants] = useState(4)
  const [concurrency, setConcurrency] = useState(2)
  const [providerConfig, setProviderConfig] = useState<ProviderConfig>()
  const [providerChoice, setProviderChoice] = useState('default')
  const [quality, setQuality] = useState<ProviderQuality>('low')
  const [search, setSearch] = useState('')
  const [preparing, setPreparing] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!workspace.products.some((product) => product.id === selectedProduct) && workspace.products[0]) {
      setSelectedProduct(workspace.products[0].id)
    }
  }, [selectedProduct, setSelectedProduct, workspace.products])

  useEffect(() => {
    if (!apiOnline || demoMode) return
    void getProviderConfig().then(setProviderConfig).catch(() => undefined)
  }, [apiOnline, demoMode])

  const product = workspace.products.find((item) => item.id === selectedProduct) ?? workspace.products[0]
  const combination = workspace.combinations?.find((item) => item.id === product?.id)
  const tasks = useMemo(
    () => combination?.tasks ?? (demoMode && product ? demoTasks(product.id) : []),
    [combination, demoMode, product],
  )
  const taskKey = JSON.stringify(
    tasks.map((task) => [task.id, task.name, task.promptCount]),
  )
  const taskNames = useMemo(() => tasks.map((task) => task.name), [tasks])

  useEffect(() => {
    setSelectedCells(tasks[0] ? selectAllMatrixCells([tasks[0].name], defaultShotIds) : new Set())
  }, [product?.id, taskKey])

  const visibleTasks = tasks.filter((task) => task.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
  const selectedItems = useMemo(
    () => selectedMatrixItems(selectedCells, taskNames, shotIds),
    [selectedCells, taskNames],
  )
  const selectedTaskNames = new Set(selectedItems.map(({ task }) => task))
  const selectedShotIds = new Set(selectedItems.map(({ shot }) => shot))
  const selectedCellCount = selectedItems.length
  const expectedCount = selectedCellCount * variants
  const generatedCount = tasks.reduce((sum, task) => sum + task.generatedImageCount, 0)
  const blockedCount = tasks.reduce((sum, task) => sum + columns.filter(({ id }) => taskState(task, id) === 'blocked').length, 0)
  const taskByName = new Map(tasks.map((task) => [task.name, task]))
  const selectedBlockedCount = selectedItems.reduce((sum, item) => {
    const task = taskByName.get(item.task)
    return sum + (task && taskState(task, item.shot) === 'blocked' ? 1 : 0)
  }, 0)
  const liveReady = apiOnline && !demoMode && workspace.liveGenerationEnabled
  const {
    activeChannels,
    selectedChannel,
    effectiveChannel,
    fixedUnavailable,
  } = resolveProviderEstimate(providerConfig, providerChoice, quality)
  const unavailableFixedRoute = fixedUnavailable || (
    providerChoice === 'default'
    && providerConfig?.routing.mode === 'fixed'
    && !effectiveChannel
  )
  const estimatedCost = effectiveChannel
    ? expectedCount * effectiveChannel.rates[quality]
    : 0
  const routeCaption = providerChoice === 'auto'
    ? `Auto · ${effectiveChannel?.name ?? `${providerConfig?.routing.currency ?? 'CNY'} 最低价`}`
    : selectedChannel
      ? selectedChannel.name
      : `工作区默认 · ${effectiveChannel?.name ?? (unavailableFixedRoute ? '固定渠道不可用' : '待解析')}`

  const toggleTask = (task: string) => setSelectedCells((current) => toggleMatrixRow(current, task, shotIds))

  const toggleShot = (shot: ShotType) => setSelectedCells((current) => toggleMatrixColumn(current, taskNames, shot))

  const openCell = (task: string, shot: ShotType) => {
    setSelectedTask(task)
    setSelectedShot(shot)
    navigate(`/studio?product=${encodeURIComponent(product?.id ?? selectedProduct)}&task=${encodeURIComponent(task)}&shot=${encodeURIComponent(shot)}`)
  }

  const prepare = async () => {
    if (!product) return
    if (!apiOnline || demoMode) {
      notify({ title: '当前为演示数据', detail: '连接本地服务后才能刷新真实 Prompt。', tone: 'warning' })
      return
    }
    setPreparing(true)
    try {
      await prepareWorkflow(product.id)
      await refreshWorkspace()
      notify({ title: 'Prompt 基线已刷新', detail: `${product.id} 的任务资料已重新扫描。`, tone: 'success' })
    } catch (error) {
      notify({ title: 'Prompt 准备失败', detail: error instanceof Error ? error.message : '请检查 Skill 日志', tone: 'warning' })
    } finally {
      setPreparing(false)
    }
  }

  const generate = async () => {
    if (!product || !selectedItems.length) {
      notify({ title: '还不能开始生成', detail: '请至少选择一个工作项。', tone: 'warning' })
      return
    }
    if (selectedBlockedCount) {
      notify({ title: '所选范围包含资料阻塞项', detail: `请先处理 ${selectedBlockedCount} 个缺少 Prompt 或参考图清单的工作项。`, tone: 'warning' })
      return
    }
    if (!liveReady) {
      notify({
        title: demoMode ? '演示模式不会创建真实任务' : '真实生图尚未开启',
        detail: demoMode ? '请先连接本地 NovaFrame 服务。' : '请到连接与设置中开启实时生图。',
        tone: 'warning',
      })
      return
    }
    if (unavailableFixedRoute) {
      notify({
        title: '固定渠道当前不可用',
        detail: '所选固定渠道已停用或不存在，请重新选择渠道后再提交。',
        tone: 'warning',
      })
      return
    }
    const costNotice = estimatedCost > 0
      ? `按当前渠道估算约 ${estimatedCost.toFixed(4)} ${effectiveChannel?.currency ?? ''}`
      : '当前渠道没有可展示的费率，实际调用仍可能产生费用'
    if (!window.confirm(`将提交 ${expectedCount} 张付费候选，${costNotice}。是否继续？`)) return
    setSubmitting(true)
    try {
      const run = await createGenerationRun({
        product: product.id,
        tasks: [...selectedTaskNames],
        shots: [...selectedShotIds],
        items: selectedItems,
        variants,
        concurrency,
        providerMode: providerChoice === 'default' ? 'default' : providerChoice === 'auto' ? 'auto' : 'fixed',
        providerChannelId: selectedChannel?.id,
        quality,
        size: '1024x1024',
      })
      setActiveRunId(run.id)
      notify({ title: '批量任务已进入本地队列', detail: `预计生成 ${expectedCount} 张候选图 · ${run.provider?.channelName ?? routeCaption}`, tone: 'success' })
      navigate(`/queue?run=${encodeURIComponent(run.id)}`)
    } catch (error) {
      notify({ title: '创建批量任务失败', detail: error instanceof Error ? error.message : '本地工作流未响应', tone: 'warning' })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="matrix-page page-pad batch-matrix-page">
      <section className="matrix-summary batch-matrix-summary">
        <div className="summary-copy">
          <span className="product-avatar"><img src={product?.thumbnail ?? '/demo/product-cutout.png'} alt="" /></span>
          <div>
            <small>当前商品</small>
            <select
              value={product?.id ?? ''}
              onChange={(event) => {
                setSelectedCells(new Set())
                setSelectedProduct(event.target.value)
              }}
              aria-label="选择商品"
            >
              {workspace.products.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <p>{tasks.length} 组任务 · {tasks.length * columns.length} 个图型工作项</p>
          </div>
        </div>
        <div className="matrix-summary-stat"><span>{generatedCount}</span><small>已有输出</small></div>
        <div className="matrix-summary-stat"><span>{selectedCellCount}</span><small>本次工作项</small></div>
        <div className="matrix-summary-stat warning"><span>{blockedCount}</span><small>资料阻塞</small></div>
        <button className="button secondary" onClick={prepare} disabled={preparing}>{preparing ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}刷新 Prompt</button>
      </section>

      {!liveReady && (
        <section className="generation-gate" role="status">
          <AlertTriangle size={19} />
          <div><strong>{demoMode ? '当前展示的是演示数据' : '实时生图开关尚未开启'}</strong><p>{demoMode ? '可以浏览完整工作流，但不会伪造本地任务或候选图。' : '设置 MUSEFORGE_ENABLE_LIVE_GENERATION=true 并重启本地服务后，才可提交批量任务。'}</p></div>
          <button onClick={() => navigate('/settings')}><Settings2 size={15} />连接与设置</button>
        </section>
      )}

      <section className="matrix-toolbar batch-matrix-toolbar">
        <div className="inline-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索任务或配件" /></div>
        <button className="filter-button" onClick={() => setSelectedCells(selectAllMatrixCells(taskNames, shotIds))}>全选工作项</button>
        <button className="filter-button" onClick={() => setSelectedCells(new Set())}>清空选择</button>
        <span className="toolbar-spacer" />
        <span className="selection-count">已选择 {selectedCellCount} 个工作项 · {selectedTaskNames.size} 个任务 / {selectedShotIds.size} 种图型</span>
      </section>

      <section className="matrix-table panel batch-matrix-table">
        <div className="matrix-head">
          <div className="task-col">选择任务 / 配件</div>
          {columns.map((column) => {
            const selectionState = matrixAxisSelectionState(
              selectedCells,
              taskNames.map((task) => matrixCellKey(task, column.id)),
            )
            return (
              <button
                key={column.id}
                className={`${selectionState !== 'none' ? 'selected-axis' : ''} ${selectionState === 'partial' ? 'partial-axis' : ''}`}
                onClick={() => toggleShot(column.id)}
                aria-label={`${selectionState === 'all' ? '取消选择' : '选择'}整列${column.label}`}
              >
                <span className="axis-checkbox">{selectionState === 'all' ? <Check size={12} /> : selectionState === 'partial' ? <Minus size={12} /> : null}</span>
                <span>{column.label}</span><small>{column.short}</small>
              </button>
            )
          })}
          <div className="more-col" />
        </div>
        {visibleTasks.map((task) => {
          const rowSelectionState = matrixAxisSelectionState(
            selectedCells,
            shotIds.map((shot) => matrixCellKey(task.name, shot)),
          )
          return (
            <div className={`matrix-row ${rowSelectionState !== 'none' ? 'selected-task-row' : ''}`} key={task.id}>
              <button
                className="task-col task-selector"
                onClick={() => toggleTask(task.name)}
                aria-label={`${rowSelectionState === 'all' ? '取消选择' : '选择'}${task.name}整行`}
              >
                <span className="axis-checkbox">{rowSelectionState === 'all' ? <Check size={12} /> : rowSelectionState === 'partial' ? <Minus size={12} /> : null}</span>
                <span className={`task-symbol ${task.kind === 'standalone' ? 'standalone' : ''}`}>{task.kind === 'standalone' ? 'P' : '＋'}</span>
                <p><strong>{task.name}</strong><small>{task.kind === 'standalone' ? '主商品' : '配件组合'} · {task.referenceCount} 张参考</small></p>
              </button>
              {columns.map((column) => {
                const state = taskState(task, column.id)
                const meta = cellMeta[state]
                const Icon = meta.icon
                const image = task.shots[column.id]?.images[0]
                const selected = isMatrixCellSelected(selectedCells, task.name, column.id)
                return (
                  <button
                    key={`${task.id}-${column.id}`}
                    className={`matrix-cell ${state} ${selected ? 'selected' : ''}`}
                    onClick={() => setSelectedCells((current) => toggleMatrixCell(current, task.name, column.id))}
                    onDoubleClick={() => openCell(task.name, column.id)}
                    aria-pressed={selected}
                  >
                    <span className="cell-checkbox">{selected && <Check size={12} />}</span>
                    {image ? <img src={image.url} alt="" /> : <span className="cell-placeholder"><Icon size={18} /></span>}
                    <strong><Icon size={12} />{meta.label}</strong>
                    <small>{state === 'blocked' ? '缺 Prompt 或参考图清单' : state === 'review' ? `${task.shots[column.id].imageCount} 张输出` : '预检资料完整'}</small>
                  </button>
                )
              })}
              <div className="more-col"><ImageIcon size={16} /></div>
            </div>
          )
        })}
        {!visibleTasks.length && <div className="batch-empty"><Search size={22} /><strong>没有匹配的真实任务</strong><span>请先运行 Skill 准备 prompts 与 reference manifest。</span></div>}
        <div className="matrix-legend"><span><i className="review" />已有候选</span><span><i className="ready" />可生成</span><span><i className="blocked" />资料阻塞</span><small>单击精确选择工作项；行列按钮可批量选择；双击进入画布</small></div>
      </section>

      <section className="batch-selection-bar">
        <div className="batch-selection-copy"><small>本次批量任务</small><strong>{selectedCellCount} 个精确工作项 × {variants} 个候选</strong><span className={selectedBlockedCount ? 'blocked-copy' : ''}>{selectedBlockedCount ? `${selectedBlockedCount} 个所选工作项资料阻塞，暂不可提交` : `预计生成 ${expectedCount} 张${estimatedCost > 0 ? ` · 约 ${estimatedCost.toFixed(4)} ${effectiveChannel?.currency}` : ''} · 每个工作项保留独立审核组`}</span></div>
        <label>每组候选<select value={variants} onChange={(event) => setVariants(Number(event.target.value))}><option value={1}>1 张</option><option value={2}>2 张</option><option value={4}>4 张</option></select></label>
        <label className="provider-route-field">本批次渠道<select value={providerChoice} onChange={(event) => setProviderChoice(event.target.value)}><option value="default">工作区默认</option><option value="auto">Auto 最低价</option>{activeChannels.map((channel) => <option key={channel.id} value={channel.id}>{channel.name}</option>)}</select></label>
        <label>质量<select value={quality} onChange={(event) => setQuality(event.target.value as ProviderQuality)}><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label>
        <label>本地并发<select value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))}><option value={1}>1</option><option value={2}>2</option><option value={4}>4</option><option value={6}>6</option></select></label>
        <button className="button dark" onClick={generate} disabled={submitting || !liveReady || expectedCount === 0}>{submitting ? <LoaderCircle size={16} className="spin" /> : <Play size={16} />}生成 {expectedCount} 张候选</button>
      </section>
    </div>
  )
}
