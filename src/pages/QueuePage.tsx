import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Check,
  ChevronRight,
  CircleX,
  Clock3,
  Images,
  LoaderCircle,
  RefreshCcw,
  Search,
  Sparkles,
} from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router'
import { getGenerationRun, listGenerationRunsPage } from '../lib/api'
import { useAppStore } from '../store/appStore'
import type { GenerationJob, GenerationRun, GenerationRunStatus } from '../types'
import '../batch.css'

type Filter = 'all' | 'active' | 'review' | 'done'

const statusMeta: Record<GenerationRunStatus, { label: string; icon: typeof Check }> = {
  queued: { label: '排队中', icon: Clock3 },
  running: { label: '生成中', icon: LoaderCircle },
  completed: { label: '已完成', icon: Check },
  succeeded: { label: '已完成', icon: Check },
  failed: { label: '失败', icon: CircleX },
  cancelled: { label: '已取消', icon: CircleX },
}

function demoRun(job: GenerationJob): GenerationRun {
  const completed = job.status === 'succeeded'
  const candidateCount = completed ? 4 : job.status === 'running' ? Math.max(1, Math.floor(job.progress / 25)) : 0
  return {
    id: job.id,
    product: job.product,
    tasks: ['单品'],
    shots: [job.shot],
    variants: 4,
    concurrency: 1,
    status: job.status,
    progress: job.progress,
    candidateCount,
    completedCount: candidateCount,
    failedCount: job.status === 'failed' ? 1 : 0,
    pendingReviewCount: completed ? candidateCount : 0,
    selectedCount: 0,
    expectedCount: 4,
    message: '演示任务，不会调用本地生图服务',
    createdAt: job.createdAt,
    thumbnail: job.thumbnail,
    demo: true,
  }
}

function formatTime(value: string): string {
  if (!value) return '刚刚'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function shotLabel(shot: string): string {
  return ({ main: '主图', size: '尺寸图', 'lifestyle-scene': '场景图', detail: '细节图', comparison: '对比图' } as Record<string, string>)[shot] ?? shot
}

export function QueuePage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const storedRunId = useAppStore((state) => state.activeRunId)
  const focusedRunId = searchParams.get('run') ?? storedRunId
  const jobs = useAppStore((state) => state.jobs)
  const apiOnline = useAppStore((state) => state.apiOnline)
  const demoMode = useAppStore((state) => state.demoMode)
  const setActiveRunId = useAppStore((state) => state.setActiveRunId)
  const [runs, setRuns] = useState<GenerationRun[]>([])
  const [runTotal, setRunTotal] = useState(0)
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [source, setSource] = useState<'live' | 'demo'>('demo')
  const [error, setError] = useState<string>()
  const [refreshing, setRefreshing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date>()
  const loadSequence = useRef(0)
  const loadInFlight = useRef(false)

  const loadRuns = useCallback(async (manual = false) => {
    if (!manual && loadInFlight.current) return
    const sequence = ++loadSequence.current
    loadInFlight.current = true
    if (manual) setRefreshing(true)
    try {
      if (!apiOnline || demoMode) {
        setRuns(jobs.map(demoRun))
        setRunTotal(jobs.length)
        setSource('demo')
        setError(undefined)
        setLastUpdated(new Date())
        return
      }
      const page = await listGenerationRunsPage(200)
      if (sequence !== loadSequence.current) return
      const items = [...page.items]
      if (focusedRunId && !items.some((run) => run.id === focusedRunId)) {
        try {
          const focusedRun = await getGenerationRun(focusedRunId)
          if (sequence !== loadSequence.current) return
          items.unshift(focusedRun)
        } catch {
          if (sequence !== loadSequence.current) return
          // The run may have been removed between list and detail requests.
        }
      }
      if (sequence !== loadSequence.current) return
      setRuns(items)
      setRunTotal(page.total)
      setSource('live')
      setError(undefined)
      setLastUpdated(new Date())
    } catch (reason) {
      if (sequence !== loadSequence.current) return
      setRuns(jobs.map(demoRun))
      setRunTotal(jobs.length)
      setSource('demo')
      setError(reason instanceof Error ? reason.message : '无法读取本地生成队列')
      setLastUpdated(new Date())
    } finally {
      if (sequence === loadSequence.current) {
        loadInFlight.current = false
        setRefreshing(false)
      }
    }
  }, [apiOnline, demoMode, focusedRunId, jobs])

  useEffect(() => {
    void loadRuns()
    const timer = window.setInterval(() => void loadRuns(), 2000)
    return () => {
      window.clearInterval(timer)
      loadSequence.current += 1
      loadInFlight.current = false
    }
  }, [loadRuns])

  const visible = useMemo(() => runs.filter((run) => {
    const query = search.trim().toLocaleLowerCase()
    if (query && !`${run.product} ${run.tasks.join(' ')} ${run.shots.join(' ')}`.toLocaleLowerCase().includes(query)) return false
    if (filter === 'active') return run.status === 'queued' || run.status === 'running'
    if (filter === 'review') return run.candidateCount > run.selectedCount && run.candidateCount > 0
    if (filter === 'done') return ['completed', 'succeeded', 'failed', 'cancelled'].includes(run.status)
    return true
  }), [filter, runs, search])

  const activeCount = runs.filter((run) => run.status === 'queued' || run.status === 'running').length
  const candidateCount = runs.reduce((sum, run) => sum + run.candidateCount, 0)
  const pendingReview = runs.reduce((sum, run) => sum + run.pendingReviewCount, 0)
  const overallExpected = runs.reduce((sum, run) => sum + run.expectedCount, 0)
  const completedProgressUnits = runs.reduce(
    (sum, run) => sum + run.expectedCount * (run.progress / 100),
    0,
  )
  const overallProgress = overallExpected
    ? Math.round((completedProgressUnits / overallExpected) * 100)
    : 0

  return (
    <div className="queue-page page-pad batch-queue-page">
      {source === 'demo' && (
        <section className="demo-data-banner">
          <Sparkles size={18} />
          <div><strong>当前展示演示队列</strong><span>{error ? `真实队列读取失败：${error}` : '演示任务不会触发生图、删除文件或写入审核结果。'}</span></div>
        </section>
      )}

      <section className="queue-overview panel batch-queue-overview">
        <div className="queue-gauge" style={{ '--queue-progress': `${overallProgress}%` } as React.CSSProperties}><span><strong>{activeCount}</strong><small>活跃批次</small></span></div>
        <div className="queue-stat"><small>本地执行进度</small><strong>{overallProgress}%</strong><div><i style={{ width: `${overallProgress}%` }} /></div></div>
        <div className="queue-stat"><small>窗口内已生成候选</small><strong>{candidateCount} / {overallExpected}</strong><span className="positive">最新窗口 {runs.length} / 全部 {runTotal} 个批次</span></div>
        <div className="queue-stat"><small>等待审核</small><strong>{pendingReview} 张</strong><span>仅保留后进入正式资产</span></div>
        <button className="button secondary" onClick={() => void loadRuns(true)} disabled={refreshing}>{refreshing ? <LoaderCircle className="spin" size={15} /> : <RefreshCcw size={15} />}立即刷新</button>
      </section>

      <section className="queue-toolbar batch-queue-toolbar">
        <div className="segmented">
          <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>全部 <em>{runTotal}</em></button>
          <button className={filter === 'active' ? 'active' : ''} onClick={() => setFilter('active')}>进行中 <em>{activeCount}</em></button>
          <button className={filter === 'review' ? 'active' : ''} onClick={() => setFilter('review')}>待审核 <em>{pendingReview}</em></button>
          <button className={filter === 'done' ? 'active' : ''} onClick={() => setFilter('done')}>已结束</button>
        </div>
        <span />
        <small className="poll-state"><i className={source} />最新 200 · 每 2 秒同步 · {lastUpdated?.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) ?? '连接中'}</small>
        <div className="inline-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索商品或任务" /></div>
      </section>

      <section className="panel generation-run-list">
        <div className="generation-run-head"><span>批量任务</span><span>工作范围</span><span>状态</span><span>候选 / 进度</span><span>下一步</span></div>
        {visible.map((run) => {
          const meta = statusMeta[run.status]
          const StatusIcon = meta.icon
          const canReview = run.candidateCount > 0
          const workItemCount = run.items?.length ?? run.tasks.length * run.shots.length
          return (
            <article className={`generation-run-row ${focusedRunId === run.id ? 'focused' : ''}`} key={run.id}>
              <div className="run-identity">
                {run.thumbnail ? <img src={run.thumbnail} alt="" /> : <span className="run-placeholder"><Images size={21} /></span>}
                <p><strong>{run.product}</strong><small>{run.id} · {formatTime(run.createdAt)}</small>{run.demo && <em>演示</em>}</p>
              </div>
              <div className="run-scope"><strong>{run.tasks.join('、') || '未指定任务'}</strong><small>{run.shots.map(shotLabel).join('、')} · {workItemCount} 个工作项 · 每组 {run.variants} 张 · 并发 {run.concurrency}</small><small>{run.provider ? `${run.provider.channelName} · ${run.provider.quality} · ${run.provider.unitPrice.toFixed(4)} ${run.provider.currency}/张` : '旧版运行 · 未记录渠道'}</small></div>
              <div><span className={`job-status ${run.status}`}><StatusIcon size={12} className={run.status === 'running' ? 'spin' : ''} />{meta.label}</span>{run.message && <small className="run-message">{run.message}</small>}</div>
              <div className="run-progress-cell"><div><span>{run.candidateCount} / {run.expectedCount} 张</span><em>{run.progress}%</em></div><span className="run-progress"><i style={{ width: `${run.progress}%` }} /></span><small>{run.failedCount ? `${run.failedCount} 项失败` : run.pendingReviewCount ? `${run.pendingReviewCount} 张待审核` : '等待候选写入'}</small></div>
              <div className="run-next-action">
                {canReview ? <button onClick={() => { setActiveRunId(run.id); navigate(`/review?job_id=${encodeURIComponent(run.id)}`) }}>审核候选 <ChevronRight size={15} /></button> : <span>{run.status === 'failed' ? '查看本地日志' : '生成后可审核'}</span>}
              </div>
            </article>
          )
        })}
        {!visible.length && <div className="batch-empty"><Clock3 size={24} /><strong>当前筛选下没有批量任务</strong><span>{source === 'live' ? '从任务矩阵创建一组真实生成任务。' : '连接本地服务后将显示真实运行记录。'}</span></div>}
      </section>
      <p className="queue-footnote">列表、搜索、筛选与汇总按服务端最新 200 个批次计算；“全部”显示服务端总数，URL 指定的更早批次会额外加入当前窗口。真实队列采用不可变快照，且不使用浏览器计时模拟进度。</p>
    </div>
  )
}
