import { useMemo, useState } from 'react'
import { AlertCircle, Check, ChevronRight, FileImage, FileText, Fingerprint, FolderOpen, LockKeyhole, ScanSearch, ShieldCheck } from 'lucide-react'
import { useAppStore } from '../store/appStore'

export function AssetsPage() {
  const [tab, setTab] = useState<'assets' | 'facts' | 'references'>('assets')
  const workspace = useAppStore((state) => state.workspace)
  const selectedProduct = useAppStore((state) => state.selectedProduct)
  const selectedTask = useAppStore((state) => state.selectedTask)
  const product = workspace?.products.find((item) => item.id === selectedProduct) ?? workspace?.products[0]
  const combination = workspace?.combinations?.find((item) => item.id === product?.id)
  const task = combination?.tasks.find((item) => item.name === selectedTask) ?? combination?.tasks[0]
  const sourceImages = product?.images ?? []
  const references = task?.references ?? []
  const sourcePath = product ? `workspace / 原始商品图 / ${product.id}` : 'workspace / 原始商品图'
  const taskPath = task?.relativePath ?? (product ? `组合/${product.id}` : '组合')
  const readiness = useMemo(() => {
    if (!task) return { label: '尚未准备任务', ok: false }
    if (!task.hasPrompts) return { label: '缺少 prompts.json', ok: false }
    if (!task.hasReferenceManifest || !task.referenceCount) return { label: '缺少参考图清单', ok: false }
    return { label: '任务文件可追溯', ok: true }
  }, [task])

  return (
    <div className="assets-page page-pad">
      <section className="asset-context-bar">
        <div><span className="product-avatar">{product?.thumbnail ? <img src={product.thumbnail} alt="" /> : <FileImage size={18} />}</span><p><small>商品工作区</small><strong>{product?.name ?? '没有可用商品'}</strong></p><ChevronRight size={16} /><span className="context-task">{task?.name ?? '未准备任务'}</span></div>
        <span className={`fingerprint ${readiness.ok ? '' : 'warning'}`}><Fingerprint size={14} />{readiness.label}</span>
      </section>

      <div className="wide-tabs">
        <button className={tab === 'assets' ? 'active' : ''} onClick={() => setTab('assets')}><FolderOpen size={16} />源素材 <em>{sourceImages.length}</em></button>
        <button className={tab === 'facts' ? 'active' : ''} onClick={() => setTab('facts')}><FileText size={16} />商品事实 <em>{Math.max(0, (product?.assetCount ?? 0) - sourceImages.length)}</em></button>
        <button className={tab === 'references' ? 'active' : ''} onClick={() => setTab('references')}><ScanSearch size={16} />任务参考 <em>{references.length} / 5</em></button>
      </div>

      {tab === 'assets' && (
        <section className="panel source-assets-panel">
          <div className="panel-heading"><div><small>{sourcePath}</small><h3>源商品图片</h3></div><span className="count-pill">只读扫描</span></div>
          {sourceImages.length ? <div className="source-file-grid">{sourceImages.map((image) => <article key={image.relativePath ?? image.url}><img src={image.url} alt={image.name} /><div><FileImage size={15} /><p><strong>{image.name}</strong><small>{image.sizeBytes ? `${Math.max(1, Math.round(image.sizeBytes / 1024))} KB` : '工作区图片'}</small></p></div></article>)}</div> : <div className="asset-empty"><FileImage size={26} /><strong>这个商品还没有图片</strong><p>请将源图片放入对应的“原始商品图”目录，或从画布连接本地 API 后导入。</p></div>}
        </section>
      )}

      {tab === 'facts' && (
        <section className="panel facts-panel">
          <div className="panel-heading"><div><small>证据层 · 当前只读</small><h3>商品事实来源</h3></div><span className="count-pill">{product?.assetCount ?? 0} 个源文件</span></div>
          <div className="truth-note"><LockKeyhole size={17} /><p><strong>事实由本地 Skill 从源文档提取</strong><span>当前网页尚未提供逐字段事实编辑器，因此不会展示伪造的属性、置信度或“已确认”状态。生成时仍以源资料和 prompts.json 为准。</span></p></div>
          <div className="manifest-preview"><div><FileText size={15} /><span><strong>{sourcePath}</strong><small>更新源文档后，在任务矩阵执行“刷新 Prompt 基线”</small></span></div></div>
        </section>
      )}

      {tab === 'references' && (
        <div className="reference-layout">
          <section className="panel candidate-panel">
            <div className="panel-heading"><div><small>{taskPath} / 参考图</small><h3>已发布任务参考</h3></div><span className="count-pill">{references.length} / 5</span></div>
            <div className="curation-help"><ShieldCheck size={17} /><p><strong>这些图片会真实进入当前任务</strong><span>参考图用于锁定商品身份与组合关系，不作为原构图模板。</span></p></div>
            {references.length ? <div className="candidate-grid">{references.map((image, index) => <article key={image.relativePath ?? image.url} className="candidate-card selected"><div className="candidate-image"><img src={image.url} alt={image.name} /><span className="candidate-check"><Check size={14} /></span></div><div><strong>{image.name}</strong><small>{index === 0 ? '身份锚点' : '任务参考'}</small></div></article>)}</div> : <div className="asset-empty"><AlertCircle size={26} /><strong>当前任务没有已发布参考图</strong><p>请先运行 Prepare，或按 Skill 目录协议补全“参考图”和 reference_manifest.json。</p></div>}
          </section>
          <aside className="panel manifest-panel">
            <div className="panel-heading"><div><small>任务单一事实源</small><h3>参考清单状态</h3></div><span className={`count-pill ${task?.hasReferenceManifest ? '' : 'warning'}`}>{task?.hasReferenceManifest ? '已存在' : '缺失'}</span></div>
            <div className="manifest-preview"><div><FileText size={15} /><span><strong>reference_manifest.json</strong><small>{task?.hasReferenceManifest ? '生成前会由 Skill 再次验证' : '缺失时真实生成会被阻止'}</small></span></div></div>
            <div className="manifest-checks"><p className={references.length ? '' : 'warning'}>{references.length ? <Check size={13} /> : <AlertCircle size={13} />}{references.length ? `${references.length} 张参考图已被工作区扫描` : '至少需要一张身份参考图'}</p><p className={task?.hasPrompts ? '' : 'warning'}>{task?.hasPrompts ? <Check size={13} /> : <AlertCircle size={13} />}{task?.hasPrompts ? `${task.promptCount} 份 Prompt 已准备` : 'prompts.json 尚未准备'}</p></div>
            <small className="publish-note"><LockKeyhole size={11} />网页当前不修改清单，避免出现“已发布”但磁盘未落地的状态。</small>
          </aside>
        </div>
      )}
    </div>
  )
}
