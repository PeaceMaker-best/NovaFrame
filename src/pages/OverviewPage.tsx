import { useEffect, useState } from 'react'
import {
  ArrowRight,
  Check,
  ChevronRight,
  CircleAlert,
  Clock3,
  FileStack,
  FolderPlus,
  Images,
  MoreHorizontal,
  Play,
  Plus,
  Sparkles,
  WandSparkles,
} from 'lucide-react'
import { Link } from 'react-router'
import { demoWorkspace } from '../lib/demo'
import { listGenerationRuns } from '../lib/api'
import { useAppStore } from '../store/appStore'
import type { GenerationRun } from '../types'

const statusMeta = {
  ready: { label: '可生成', icon: Check },
  stale: { label: '需刷新', icon: Clock3 },
  blocked: { label: '资料阻塞', icon: CircleAlert },
  draft: { label: '草稿', icon: Clock3 },
}

export function OverviewPage() {
  const workspace = useAppStore((state) => state.workspace) ?? demoWorkspace
  const apiOnline = useAppStore((state) => state.apiOnline)
  const demoMode = useAppStore((state) => state.demoMode)
  const [runs, setRuns] = useState<GenerationRun[]>([])
  const today = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date()).replace('星期', '，星期')
  const promptCoverage = Math.min(100, Math.round((workspace.stats.prompts / Math.max(workspace.stats.tasks * 5, 1)) * 100))
  const productOutputMax = Math.max(1, ...workspace.products.map((product) => product.outputCount))
  const productOutputBars = workspace.products.slice(0, 7).map((product) => ({
    id: product.id,
    name: product.name,
    count: product.outputCount,
    height: product.outputCount ? Math.max(14, Math.round((product.outputCount / productOutputMax) * 100)) : 0,
  }))
  const currentRun = runs.find((run) => run.status === 'running')
    ?? runs.find((run) => run.status === 'queued')
    ?? runs[0]
  const runThumbnail = currentRun?.thumbnail
    ?? workspace.products.find((product) => product.id === currentRun?.product)?.thumbnail

  useEffect(() => {
    if (!apiOnline || demoMode) {
      setRuns([])
      return
    }
    let cancelled = false
    const loadRuns = () => { void listGenerationRuns().then((items) => { if (!cancelled) setRuns(items) }).catch(() => { if (!cancelled) setRuns([]) }) }
    loadRuns()
    const timer = window.setInterval(loadRuns, 2500)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [apiOnline, demoMode, workspace.stats.pendingReview])

  return (
    <div className="overview-page">
      <section className="welcome-row ali-hero">
        <div className="hero-copy">
          <div className="hero-kicker"><span>AI 视觉生产平台</span><i />本地优先 · 安全可控</div>
          <p>{today}</p>
          <h2>让每一次创作，<br /><em>更快成为好生意。</em></h2>
          <span>{workspace.stats.pendingReview} 张图片等待审核，{workspace.stats.tasks} 个生产任务已进入工作区。</span>
          <div className="welcome-actions">
            <Link className="button primary" to="/studio"><Plus size={17} />开始智能创作</Link>
            <Link className="button secondary" to="/matrix">查看生产任务 <ArrowRight size={16} /></Link>
          </div>
        </div>
        <div className="hero-showcase" aria-label="商品图智能创作示例">
          <div className="hero-image-card">
            <img src="/demo/product-studio.png" alt="AI 生成的商品场景图" />
            <span className="hero-image-status"><i />场景图已生成</span>
            <span className="hero-image-tag">自然晨光 · 电商视觉</span>
          </div>
          <div className="hero-floating-card">
            <span><WandSparkles size={15} /> 智能生产中</span>
            <strong>68%</strong>
            <i><b /></i>
            <small>预计 2 分钟完成</small>
          </div>
        </div>
      </section>

      <section className="metric-grid">
        <Link className="metric-card featured" to="/assets">
          <div className="metric-head">
            <div className="metric-icon"><WandSparkles size={20} /></div>
            <div className="metric-heading"><small>正式输出</small><span>已审核并保留的资产</span></div>
            <em>本地资产库</em>
          </div>
          <div className="metric-body">
            <div className="metric-value"><strong>{workspace.stats.outputs}</strong><span>张</span></div>
            {workspace.stats.outputs > 0 ? <div className="metric-product-bars" aria-label={`各商品正式输出：${productOutputBars.map((item) => `${item.name} ${item.count} 张`).join('，')}`}>
              {productOutputBars.map((item) => <i key={item.id} title={`${item.name} · ${item.count} 张`} style={{ height: `${item.height}%` }} />)}
            </div> : <div className="metric-empty-signal"><i /><span>等待首个正式资产</span></div>}
          </div>
          <div className="metric-footer"><span><i />覆盖 {workspace.stats.products} 个商品</span><strong>查看资产 <ArrowRight size={14} /></strong></div>
        </Link>
        <Link className="metric-card review" to="/review">
          <div className="metric-head"><div className="metric-icon mint"><Images size={20} /></div><div className="metric-heading"><small>待审核</small><span>需要人工决策</span></div><em className={workspace.stats.pendingReview ? 'attention' : 'stable'}>{workspace.stats.pendingReview ? '待处理' : '已清空'}</em></div>
          <div className="metric-body"><div className="metric-value"><strong>{workspace.stats.pendingReview}</strong><span>张候选图</span></div></div>
          <div className="metric-footer"><span>{workspace.stats.pendingReview ? '保留满意结果，清理其余候选' : '当前没有积压'}</span><strong>开始审核 <ArrowRight size={14} /></strong></div>
        </Link>
        <Link className="metric-card tasks" to="/matrix">
          <div className="metric-head"><div className="metric-icon sand"><FileStack size={20} /></div><div className="metric-heading"><small>生产任务</small><span>单品与配件组合</span></div><em>{workspace.stats.products} 商品</em></div>
          <div className="metric-body"><div className="metric-value"><strong>{workspace.stats.tasks}</strong><span>组任务</span></div></div>
          <div className="metric-footer"><span>按 5 类电商图型组织</span><strong>查看矩阵 <ArrowRight size={14} /></strong></div>
        </Link>
        <Link className="metric-card coverage" to="/matrix">
          <div className="metric-head"><div className="metric-icon coral"><Sparkles size={20} /></div><div className="metric-heading"><small>Prompt 覆盖</small><span>结构化生成准备度</span></div><em className={promptCoverage === 100 ? 'stable' : 'attention'}>{promptCoverage === 100 ? '完整' : '待补充'}</em></div>
          <div className="metric-body"><div className="metric-value"><strong>{promptCoverage}<sup>%</sup></strong><span>{workspace.stats.prompts} 份 Prompt</span></div></div>
          <div className="metric-footer"><span>{workspace.stats.tasks * 5} 个目标图型</span><strong>检查准备度 <ArrowRight size={14} /></strong></div>
        </Link>
      </section>

      <section className="overview-columns">
        <div className="panel recent-projects">
          <div className="panel-heading">
            <div><small>继续工作</small><h3>最近项目</h3></div>
            <Link className="text-button" to="/matrix">查看全部 <ChevronRight size={15} /></Link>
          </div>
          <div className="project-list">
            {workspace.products.slice(0, 3).map((project, index) => {
              const meta = statusMeta[project.readiness]
              const StatusIcon = meta.icon
              const completion = Math.min(100, Math.round((project.outputCount / Math.max(project.promptCount, 1)) * 100))
              return (
                <article className="project-row" key={project.id}>
                  <Link to={index === 0 ? '/studio' : '/matrix'} className="project-thumb">
                    <img src={project.thumbnail ?? `/demo/${index === 1 ? 'interior' : 'product-studio'}.png`} alt="" />
                    {index === 0 && <span><Play size={14} fill="currentColor" /></span>}
                  </Link>
                  <div className="project-copy">
                    <div className="project-title"><strong>{project.name}</strong><span className={`readiness ${project.readiness}`}><StatusIcon size={12} />{meta.label}</span></div>
                    <small>{project.taskCount} 个任务 · {project.outputCount} 张输出 · {project.updatedAt ?? '最近更新'}</small>
                    <div className="project-progress"><span><i style={{ width: `${completion}%` }} /></span><em>{completion}%</em></div>
                  </div>
                  <button className="icon-button small" aria-label="更多"><MoreHorizontal size={17} /></button>
                </article>
              )
            })}
          </div>
        </div>

        <div className="panel production-pulse">
          <div className="panel-heading">
            <div><small>生产脉搏</small><h3>当前流水线</h3></div>
            <span className={`live-label ${currentRun?.status === 'running' ? '' : 'idle'}`}><i />{currentRun?.status === 'running' ? '运行中' : currentRun ? '最近运行' : '暂无运行'}</span>
          </div>
          {currentRun ? <>
            <div className={`pulse-feature ${runThumbnail ? '' : 'no-image'}`}>
              {runThumbnail && <img src={runThumbnail} alt="当前运行商品" />}
              <div className="pulse-gradient" />
              <div className="pulse-copy">
                <span>{currentRun.status === 'running' ? '正在生成' : currentRun.status === 'queued' ? '等待执行' : currentRun.status === 'failed' ? '运行失败' : '生成结束'} · {currentRun.progress}%</span>
                <strong>{currentRun.product}</strong>
                <small>{currentRun.tasks.length} 个任务 · {currentRun.candidateCount} / {currentRun.expectedCount} 张候选</small>
                <div className="pulse-progress"><i style={{ width: `${currentRun.progress}%` }} /></div>
              </div>
            </div>
            <div className="pipeline-steps">
              <div className="done"><span><Check size={13} /></span><p><strong>任务门槛</strong><small>已通过</small></p></div><i />
              <div className="done"><span><Check size={13} /></span><p><strong>运行快照</strong><small>已固化</small></p></div><i />
              <div className={currentRun.status === 'running' || currentRun.status === 'queued' ? 'active' : currentRun.status === 'failed' ? '' : 'done'}><span>{currentRun.status === 'completed' ? <Check size={13} /> : '3'}</span><p><strong>候选生成</strong><small>{currentRun.completedCount} 完成 · {currentRun.failedCount} 失败</small></p></div><i />
              <div className={currentRun.pendingReviewCount ? 'active' : ''}><span>4</span><p><strong>人工审核</strong><small>{currentRun.pendingReviewCount} 张待处理</small></p></div>
            </div>
          </> : <div className="pulse-empty"><Clock3 size={26} /><strong>还没有真实生成记录</strong><p>从任务矩阵或画布提交后，这里会展示服务端记录的实时进度。</p></div>}
          <Link to="/queue" className="full-link">打开生成队列 <ArrowRight size={15} /></Link>
        </div>
      </section>

      <section className="quick-start panel">
        <div><small>快速开始</small><h3>选择一条生产路径</h3></div>
        <Link to="/matrix" className="quick-card"><span className="quick-icon product"><Images size={20} /></span><p><strong>商品图工作流</strong><small>五类镜头、配件组合、规则校验</small></p><ChevronRight size={18} /></Link>
        <Link to="/studio" className="quick-card"><span className="quick-icon canvas"><Sparkles size={20} /></span><p><strong>自由画布</strong><small>参考图、构图、变体自由编排</small></p><ChevronRight size={18} /></Link>
        <Link to="/assets" className="quick-card"><span className="quick-icon import"><FolderPlus size={20} /></span><p><strong>检查现有目录</strong><small>查看已扫描的源素材与任务参考</small></p><ChevronRight size={18} /></Link>
      </section>
    </div>
  )
}
