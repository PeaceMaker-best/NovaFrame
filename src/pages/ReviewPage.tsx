import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  Check,
  Download,
  ImageIcon,
  Images,
  Layers3,
  LoaderCircle,
  Maximize2,
  RefreshCcw,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router'
import { deleteCandidate, listCandidates, selectCandidate } from '../lib/api'
import { groupReviewCandidates } from '../lib/reviewGrouping'
import { useAppStore } from '../store/appStore'
import type { CanvasInsertMode, GenerationCandidate, ShotType } from '../types'
import '../batch.css'

const demoCandidateSources = ['/demo/product-studio.png', '/demo/campaign.png', '/demo/interior.png', '/demo/food-ad.png']

function demoCandidates(jobId: string): GenerationCandidate[] {
  return demoCandidateSources.map((url, index) => ({
    id: `demo-candidate-${index + 1}`,
    jobId,
    product: 'MF-DEMO-001',
    task: '单品',
    shot: 'lifestyle-scene',
    variant: index + 1,
    url,
    reviewStatus: 'pending',
    storageStatus: 'staged',
    name: `自然晨光 · 候选 ${String.fromCharCode(65 + index)}`,
    width: 1024,
    height: 1024,
    score: 92 - index * 5,
    model: 'GPT Image 2',
    quality: 'Medium',
    elapsedSeconds: 36 + index * 1.4,
  }))
}

function shotLabel(shot: ShotType): string {
  return ({ main: '主图', size: '尺寸图', 'lifestyle-scene': '场景图', detail: '细节图', comparison: '对比图' })[shot]
}

export function ReviewPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const storedRunId = useAppStore((state) => state.activeRunId)
  const jobId = searchParams.get('job_id') ?? storedRunId ?? ''
  const apiOnline = useAppStore((state) => state.apiOnline)
  const demoMode = useAppStore((state) => state.demoMode)
  const notify = useAppStore((state) => state.notify)
  const queueCanvasInsert = useAppStore((state) => state.queueCanvasInsert)
  const refreshWorkspace = useAppStore((state) => state.refreshWorkspace)
  const [candidates, setCandidates] = useState<GenerationCandidate[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [activeId, setActiveId] = useState<string>()
  const [activeGroup, setActiveGroup] = useState('')
  const [source, setSource] = useState<'live' | 'demo'>('demo')
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string>()
  const loadSequence = useRef(0)

  const load = useCallback(async () => {
    const sequence = ++loadSequence.current
    setLoading(true)
    setCandidates([])
    setSelected(new Set())
    setActiveId(undefined)
    setActiveGroup('')
    if (!apiOnline || demoMode) {
      const items = demoCandidates(jobId || 'demo-review')
      if (sequence !== loadSequence.current) return
      setCandidates(items)
      setSource('demo')
      setError(undefined)
      setLoading(false)
      return
    }
    try {
      const items = await listCandidates({ jobId: jobId || undefined })
      if (sequence !== loadSequence.current) return
      setCandidates(items)
      setSelected(new Set(
        items
          .filter((candidate) => candidate.reviewStatus === 'selected')
          .map((candidate) => candidate.id),
      ))
      setSource('live')
      setError(undefined)
    } catch (reason) {
      if (sequence !== loadSequence.current) return
      setCandidates(demoCandidates(jobId || 'demo-review'))
      setSource('demo')
      setError(reason instanceof Error ? reason.message : '候选图读取失败')
    } finally {
      if (sequence === loadSequence.current) setLoading(false)
    }
  }, [apiOnline, demoMode, jobId])

  useEffect(() => {
    void load()
    return () => {
      loadSequence.current += 1
    }
  }, [load])

  const groups = useMemo(() => groupReviewCandidates(candidates), [candidates])

  useEffect(() => {
    if (!groups.some((group) => group.id === activeGroup)) setActiveGroup(groups[0]?.id ?? '')
  }, [activeGroup, groups])

  const currentGroup = groups.find((group) => group.id === activeGroup) ?? groups[0]
  const visibleCandidates = currentGroup?.items ?? []

  useEffect(() => {
    if (!visibleCandidates.some((candidate) => candidate.id === activeId)) setActiveId(visibleCandidates[0]?.id)
  }, [activeId, visibleCandidates])

  const active = visibleCandidates.find((candidate) => candidate.id === activeId) ?? visibleCandidates[0]
  const selectedInGroup = visibleCandidates.filter((candidate) => selected.has(candidate.id))
  const unselectedInGroup = visibleCandidates.filter((candidate) => !selected.has(candidate.id) && candidate.storageStatus !== 'promoted')
  const belongsToCurrentScope = (candidate: GenerationCandidate) => (
    !jobId || candidate.jobId === jobId
  )

  const toggleSelected = (id: string) => setSelected((current) => {
    const next = new Set(current)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  const removeOne = async (candidate: GenerationCandidate) => {
    if (loading || working || !belongsToCurrentScope(candidate)) return
    if (source === 'demo') {
      setCandidates((items) => items.filter((item) => item.id !== candidate.id))
      setSelected((items) => { const next = new Set(items); next.delete(candidate.id); return next })
      notify({ title: '已从演示视图隐藏', detail: '这是演示候选，没有删除任何本地文件。', tone: 'warning' })
      return
    }
    if (candidate.storageStatus === 'promoted' && !window.confirm('这张图片已正式保留。继续会删除正式资产及记录；若画布已引用它，画布也会出现断图。确定要永久删除吗？')) return
    setWorking(true)
    try {
      await deleteCandidate(candidate.id)
      setCandidates((items) => items.filter((item) => item.id !== candidate.id))
      setSelected((items) => { const next = new Set(items); next.delete(candidate.id); return next })
      try {
        await refreshWorkspace()
        notify({ title: '候选已清理', detail: `${candidate.name ?? `候选 ${candidate.variant}`} 的暂存文件与记录已删除。`, tone: 'success' })
      } catch (reason) {
        notify({
          title: '候选已删除，工作区刷新失败',
          detail: `删除已经生效；请稍后刷新：${reason instanceof Error ? reason.message : '工作区刷新失败'}。`,
          tone: 'warning',
        })
      }
    } catch (reason) {
      notify({ title: '删除失败', detail: reason instanceof Error ? reason.message : '本地候选仍被保留', tone: 'warning' })
    } finally {
      setWorking(false)
    }
  }

  const keepSelected = async () => {
    if (
      loading
      || working
      || selectedInGroup.some((candidate) => !belongsToCurrentScope(candidate))
    ) return
    if (!selectedInGroup.length) {
      notify({ title: '还没有勾选候选', detail: '请先勾选至少一张满意的图片。', tone: 'warning' })
      return
    }
    if (source === 'demo') {
      notify({ title: '演示选择未写入', detail: `当前勾选了 ${selectedInGroup.length} 张；连接本地服务后才能正式保留。`, tone: 'warning' })
      return
    }
    setWorking(true)
    try {
      const pending = selectedInGroup.filter((candidate) => candidate.reviewStatus !== 'selected')
      const results = await Promise.allSettled(pending.map((candidate) => selectCandidate(candidate.id)))
      const kept = results
        .filter((result): result is PromiseFulfilledResult<GenerationCandidate> => result.status === 'fulfilled')
        .map((result) => result.value)
      const keptById = new Map(kept.map((candidate) => [candidate.id, candidate]))
      setCandidates((items) => items.map((candidate) => keptById.get(candidate.id) ?? candidate))
      let refreshError: string | undefined
      if (kept.length) {
        try {
          await refreshWorkspace()
        } catch (reason) {
          refreshError = reason instanceof Error ? reason.message : '工作区刷新失败'
        }
      }
      const failed = results.length - kept.length
      notify({
        title: refreshError
          ? '候选已保留，工作区刷新失败'
          : failed ? '部分候选未能保留' : '所选候选已正式保留',
        detail: `${kept.length} 张已晋升为正式资产${failed ? `，${failed} 张失败` : ''}${refreshError ? `；请稍后刷新：${refreshError}` : ''}。`,
        tone: failed || refreshError ? 'warning' : 'success',
      })
    } catch (reason) {
      notify({ title: '保留流程未完成', detail: reason instanceof Error ? reason.message : '请稍后重试', tone: 'warning' })
    } finally {
      setWorking(false)
    }
  }

  const cleanup = async (items: GenerationCandidate[]) => {
    if (
      loading
      || working
      || items.some((candidate) => !belongsToCurrentScope(candidate))
    ) return
    if (!items.length) {
      notify({ title: '没有需要清理的候选', detail: '当前组中的图片都已勾选保留。', tone: 'neutral' })
      return
    }
    if (source === 'live' && !window.confirm(`将永久删除 ${items.length} 张未选候选及其本地暂存文件，是否继续？`)) return
    if (source === 'demo') {
      const ids = new Set(items.map((candidate) => candidate.id))
      setCandidates((current) => current.filter((candidate) => !ids.has(candidate.id)))
      notify({ title: '已清理演示视图', detail: '没有删除任何真实文件或线上记录。', tone: 'warning' })
      return
    }
    setWorking(true)
    try {
      const results = await Promise.allSettled(items.map((candidate) => deleteCandidate(candidate.id)))
      const deletedIds = new Set(items.filter((_, index) => results[index].status === 'fulfilled').map((candidate) => candidate.id))
      setCandidates((current) => current.filter((candidate) => !deletedIds.has(candidate.id)))
      setSelected((current) => new Set([...current].filter((id) => !deletedIds.has(id))))
      let refreshError: string | undefined
      if (deletedIds.size) {
        try {
          await refreshWorkspace()
        } catch (reason) {
          refreshError = reason instanceof Error ? reason.message : '工作区刷新失败'
        }
      }
      const failed = results.length - deletedIds.size
      notify({
        title: refreshError
          ? '候选已删除，工作区刷新失败'
          : failed ? '部分候选清理失败' : '未选候选已清理',
        detail: `已删除 ${deletedIds.size} 张${failed ? `，${failed} 张仍保留` : ''}${refreshError ? `；请稍后刷新：${refreshError}` : ''}。`,
        tone: failed || refreshError ? 'warning' : 'success',
      })
    } catch (reason) {
      notify({ title: '清理流程未完成', detail: reason instanceof Error ? reason.message : '请稍后重试', tone: 'warning' })
    } finally {
      setWorking(false)
    }
  }

  const sendToCanvas = async (candidate: GenerationCandidate, mode: CanvasInsertMode) => {
    if (loading || working || !belongsToCurrentScope(candidate)) return
    let canvasCandidate = candidate
    if (source === 'live' && (candidate.reviewStatus !== 'selected' || candidate.storageStatus !== 'promoted')) {
      setWorking(true)
      try {
        canvasCandidate = await selectCandidate(candidate.id)
        setCandidates((items) => items.map((item) => item.id === candidate.id ? canvasCandidate : item))
        setSelected((items) => new Set(items).add(candidate.id))
        notify({ title: '候选已先保留', detail: '正式资产准备完成，正在送入画布。', tone: 'success' })
      } catch (reason) {
        notify({ title: '无法送入画布', detail: reason instanceof Error ? reason.message : '候选晋升失败，暂存 URL 未被使用', tone: 'warning' })
        return
      } finally {
        setWorking(false)
      }
    }
    queueCanvasInsert({
      requestId: `canvas-insert-${Date.now()}-${canvasCandidate.id}`,
      productId: canvasCandidate.product,
      taskId: canvasCandidate.task,
      shot: canvasCandidate.shot,
      asset: {
        id: canvasCandidate.id,
        name: canvasCandidate.name ?? `${canvasCandidate.task} · ${shotLabel(canvasCandidate.shot)} · 候选 ${canvasCandidate.variant}`,
        url: canvasCandidate.url,
        kind: 'output',
        dimensions: canvasCandidate.width && canvasCandidate.height ? `${canvasCandidate.width} × ${canvasCandidate.height}` : undefined,
      },
      mode,
    })
    navigate(`/studio?product=${encodeURIComponent(canvasCandidate.product)}&task=${encodeURIComponent(canvasCandidate.task)}&shot=${encodeURIComponent(canvasCandidate.shot)}`)
  }

  return (
    <div className="batch-review-page">
      <aside className="batch-review-sidebar">
        <div className="batch-review-side-head"><button onClick={() => navigate(jobId ? `/queue?run=${encodeURIComponent(jobId)}` : '/queue')}><ArrowLeft size={16} /></button><div><small>{source === 'demo' ? '演示审核' : '真实候选审核'}</small><strong>{candidates.length} 张候选</strong></div><button onClick={() => void load()} disabled={loading || working}><RefreshCcw size={15} className={loading ? 'spin' : ''} /></button></div>
        {source === 'demo' && <div className="review-demo-note"><Sparkles size={14} /><span>{error ? `接口降级：${error}` : '演示选择不会写入或删除真实文件'}</span></div>}
        <div className="review-run-id"><span>生成批次</span><strong>{jobId || '未指定 · 展示全部'}</strong></div>
        <div className="review-group-list">
          {groups.map((group, index) => {
            const kept = group.items.filter((candidate) => candidate.reviewStatus === 'selected').length
            return <button key={group.id} className={group.id === currentGroup?.id ? 'active' : ''} onClick={() => setActiveGroup(group.id)}><img src={group.items[0].url} alt="" /><span><small>工作项 {index + 1} · {group.product} · {group.jobId}</small><strong>{group.task} · {shotLabel(group.shot)}</strong><em>{group.items.length} 张候选 · {kept} 张已保留</em></span><Check size={14} className={kept ? 'visible' : ''} /></button>
          })}
        </div>
      </aside>

      <main className="batch-review-main">
        <header className="batch-review-toolbar">
          <div><small>{currentGroup ? `${currentGroup.product} / ${currentGroup.task} / ${shotLabel(currentGroup.shot)}` : '等待候选'}</small><strong>候选四宫格</strong></div>
          <span>{selectedInGroup.length} / {visibleCandidates.length} 已勾选</span>
          <button onClick={() => void load()} disabled={loading || working}><RefreshCcw size={15} />刷新候选</button>
        </header>

        {loading ? <div className="review-loading"><LoaderCircle className="spin" size={28} /><strong>正在读取本地候选</strong></div> : visibleCandidates.length ? (
          <section className="candidate-review-grid">
            {visibleCandidates.map((candidate) => {
              const checked = selected.has(candidate.id)
              return (
                <article key={candidate.id} className={`${checked ? 'selected' : ''} ${candidate.id === active?.id ? 'active' : ''}`}>
                  <button className="candidate-review-image" onClick={() => setActiveId(candidate.id)} onDoubleClick={() => toggleSelected(candidate.id)} disabled={loading || working}><img src={candidate.url} alt={candidate.name ?? `候选 ${candidate.variant}`} /><span><Maximize2 size={14} />查看细节</span></button>
                  <label><input type="checkbox" checked={checked} onChange={() => toggleSelected(candidate.id)} disabled={loading || working} /><span className="candidate-select-box">{checked && <Check size={14} />}</span><strong>{candidate.name ?? `候选 ${String.fromCharCode(64 + candidate.variant)}`}</strong>{candidate.reviewStatus === 'selected' && <em>已保留</em>}</label>
                  <button className="candidate-trash" onClick={() => void removeOne(candidate)} disabled={loading || working} title="删除候选"><Trash2 size={15} /></button>
                  {candidate.score && <span className="candidate-score"><Sparkles size={12} />{candidate.score}</span>}
                </article>
              )
            })}
          </section>
        ) : <div className="review-loading"><Images size={30} /><strong>当前工作项没有候选图</strong><span>返回队列确认生成状态，或切换左侧工作项。</span></div>}

        <footer className="batch-review-actions">
          <div><small>审核原则</small><strong>勾选满意图片；未选候选只在确认后删除</strong></div>
          <button className="cleanup" onClick={() => void cleanup(unselectedInGroup)} disabled={loading || working || !visibleCandidates.length}><Trash2 size={16} />清理未选（{unselectedInGroup.length}）</button>
          <button className="keep" onClick={() => void keepSelected()} disabled={loading || working || !selectedInGroup.length}>{working ? <LoaderCircle className="spin" size={16} /> : <Check size={17} />}保留所选（{selectedInGroup.length}）</button>
        </footer>
      </main>

      <aside className="batch-review-inspector">
        {active ? <>
          <div className="review-inspector-title"><div><small>{shotLabel(active.shot)} · 候选 {active.variant}</small><h3>{active.name ?? `${active.task} 候选`}</h3></div><a href={active.url} download target="_blank" rel="noreferrer"><Download size={16} /></a></div>
          <div className="review-active-preview"><img src={active.url} alt="" /><span className={active.reviewStatus}>{active.reviewStatus === 'selected' ? <><Check size={13} />已正式保留</> : '本地暂存候选'}</span></div>
          <div className="review-detail-grid"><div><span>模型</span><strong>{active.model ?? '本地 Provider'}</strong></div><div><span>质量</span><strong>{active.quality ?? '—'}</strong></div><div><span>尺寸</span><strong>{active.width && active.height ? `${active.width} × ${active.height}` : '读取原图'}</strong></div><div><span>耗时</span><strong>{active.elapsedSeconds ? `${active.elapsedSeconds.toFixed(1)} s` : '—'}</strong></div></div>
          <section className="review-storage-explain"><h4>存储状态</h4><p><span className={active.storageStatus ?? 'staged'} />{active.storageStatus === 'promoted' ? '已晋升至正式商品资产，可继续进入画布和交付。' : '当前只在候选暂存区；删除后不会进入正式资产库。'}</p></section>
          <section className="review-canvas-actions"><h4>继续在画布加工</h4><p>暂存候选会先正式保留，再带入对应商品与图型画板。</p><button onClick={() => void sendToCanvas(active, 'background')} disabled={loading || working}><ImageIcon size={16} />作为底图</button><button onClick={() => void sendToCanvas(active, 'layer')} disabled={loading || working}><Layers3 size={16} />作为图层</button></section>
          <button className="review-single-delete" onClick={() => void removeOne(active)} disabled={loading || working}><X size={15} />删除当前候选</button>
        </> : <div className="review-inspector-empty"><Images size={30} /><strong>选择一张候选</strong><span>这里会显示图片参数、存储状态和画布入口。</span></div>}
      </aside>
    </div>
  )
}
