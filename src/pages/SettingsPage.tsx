import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, BadgeDollarSign, Check, ChevronRight, CircleAlert, Database, FolderCog, KeyRound, Pencil, Plus, PlugZap, Route, ShieldCheck, Sparkles, TerminalSquare, X } from 'lucide-react'
import { createProviderChannel, getProviderConfig, setProviderChannelActive, updateProviderChannel, updateProviderRouting } from '../lib/api'
import { useAppStore } from '../store/appStore'
import type { ProviderChannel, ProviderChannelInput, ProviderConfig, ProviderQuality } from '../types'

const emptyChannel: ProviderChannelInput = {
  name: '',
  baseUrl: 'https://api.openai.com/v1',
  endpoint: '/images/edits',
  apiKey: '',
  model: 'gpt-image-2',
  active: true,
  currency: 'CNY',
  rates: { low: 0, medium: 0, high: 0 },
}

const qualityLabels: Record<ProviderQuality, string> = { low: '低', medium: '中', high: '高' }

function channelDraft(channel: ProviderChannel): ProviderChannelInput {
  return {
    name: channel.name,
    baseUrl: channel.baseUrl,
    endpoint: channel.endpoint,
    apiKey: '',
    model: channel.model,
    active: channel.active,
    currency: channel.currency,
    rates: { ...channel.rates },
  }
}

export function SettingsPage() {
  const workspace = useAppStore((state) => state.workspace)
  const apiOnline = useAppStore((state) => state.apiOnline)
  const notify = useAppStore((state) => state.notify)
  const liveGenerationEnabled = workspace?.liveGenerationEnabled === true
  const [config, setConfig] = useState<ProviderConfig>()
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingId, setEditingId] = useState<string>()
  const [draft, setDraft] = useState<ProviderChannelInput>(emptyChannel)
  const [routingMode, setRoutingMode] = useState<'auto' | 'fixed'>('auto')
  const [fixedChannelId, setFixedChannelId] = useState('')
  const [routingCurrency, setRoutingCurrency] = useState('CNY')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const value = await getProviderConfig()
      setConfig(value)
      setRoutingMode(value.routing.mode)
      setFixedChannelId(value.routing.fixedChannelId ?? '')
      setRoutingCurrency(value.routing.currency)
    } catch (error) {
      notify({ title: '渠道配置载入失败', detail: error instanceof Error ? error.message : '请检查本地 API', tone: 'warning' })
    } finally {
      setLoading(false)
    }
  }, [notify])

  useEffect(() => { void load() }, [load])

  const activeChannels = useMemo(() => config?.channels.filter((channel) => channel.active) ?? [], [config])
  const cheapestLow = useMemo(() => activeChannels
    .filter((channel) => channel.currency === routingCurrency && channel.rates.low > 0)
    .sort((a, b) => a.rates.low - b.rates.low)[0], [activeChannels, routingCurrency])

  const openCreate = () => {
    setEditingId(undefined)
    setDraft({ ...emptyChannel, currency: routingCurrency, rates: { ...emptyChannel.rates } })
    setEditorOpen(true)
  }

  const openEdit = (channel: ProviderChannel) => {
    setEditingId(channel.id)
    setDraft(channelDraft(channel))
    setEditorOpen(true)
  }

  const saveChannel = async () => {
    if (!draft.name.trim() || !draft.baseUrl.trim() || (!editingId && !draft.apiKey?.trim())) {
      notify({ title: '渠道信息不完整', detail: '名称、Base URL 与首次保存的 API Key 均为必填项', tone: 'warning' })
      return
    }
    setSaving(true)
    try {
      if (editingId) await updateProviderChannel(editingId, draft)
      else await createProviderChannel(draft)
      setEditorOpen(false)
      await load()
      notify({ title: editingId ? '渠道已更新' : '新渠道已注册', detail: '密钥已加密保存在本地服务端', tone: 'success' })
    } catch (error) {
      notify({ title: '无法保存渠道', detail: error instanceof Error ? error.message : '请检查输入', tone: 'warning' })
    } finally {
      setSaving(false)
    }
  }

  const toggleChannel = async (channel: ProviderChannel) => {
    try {
      await setProviderChannelActive(channel.id, !channel.active)
      await load()
      notify({ title: channel.active ? '渠道已停用' : '渠道已启用', detail: channel.name, tone: 'neutral' })
    } catch (error) {
      notify({ title: '状态更新失败', detail: error instanceof Error ? error.message : '请稍后重试', tone: 'warning' })
    }
  }

  const saveRouting = async () => {
    if (routingMode === 'fixed' && !fixedChannelId) {
      notify({ title: '请选择固定渠道', detail: '固定模式必须绑定一个当前启用的渠道', tone: 'warning' })
      return
    }
    setSaving(true)
    try {
      await updateProviderRouting({ mode: routingMode, fixedChannelId: routingMode === 'fixed' ? fixedChannelId : undefined, currency: routingCurrency })
      await load()
      notify({ title: '默认路由已保存', detail: routingMode === 'auto' ? `将自动选择 ${routingCurrency} 下费率最低的可用渠道` : '新批次将使用固定渠道', tone: 'success' })
    } catch (error) {
      notify({ title: '默认路由保存失败', detail: error instanceof Error ? error.message : '请检查渠道状态', tone: 'warning' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="settings-page page-pad">
      <div className="settings-nav panel">
        <button className="active"><Route size={16} />渠道与路由<ChevronRight size={14} /></button>
        <button disabled><FolderCog size={16} />工作区管理<small>规划中</small></button>
        <button disabled><ShieldCheck size={16} />规则与合规<small>规划中</small></button>
        <button disabled><Database size={16} />数据与备份<small>规划中</small></button>
        <button disabled><TerminalSquare size={16} />开发者选项<small>规划中</small></button>
      </div>

      <div className="settings-main provider-settings">
        <section className="panel settings-section provider-hero">
          <div className="settings-heading">
            <div><small>GPT-IMAGE-2 ROUTER</small><h3>渠道与成本控制</h3><p>统一注册兼容渠道、维护三档图片费率，并在每次生成前由服务端完成选路。已入队批次会锁定当时的渠道与价格。</p></div>
            <button className="button lime" onClick={openCreate}><Plus size={16} />注册新渠道</button>
          </div>
          <div className="provider-metrics">
            <div><span><PlugZap size={17} /></span><p><small>启用渠道</small><strong>{loading ? '—' : `${config?.summary.activeChannelCount ?? 0} / ${config?.summary.channelCount ?? 0}`}</strong></p></div>
            <div><span><BadgeDollarSign size={17} /></span><p><small>当前最低低质量费率</small><strong>{cheapestLow ? `${cheapestLow.rates.low.toFixed(4)} ${cheapestLow.currency}` : '待配置'}</strong></p></div>
            <div><span><ShieldCheck size={17} /></span><p><small>凭据保护</small><strong>本地加密 · 不返回浏览器</strong></p></div>
          </div>
        </section>

        <section className="panel settings-section routing-section">
          <div className="settings-heading"><div><small>DEFAULT ROUTING</small><h3>默认生成路由</h3><p>画布和矩阵可逐批覆盖；没有覆盖时使用这里的工作区默认值。</p></div><span className={`service-state ${apiOnline ? 'online' : ''}`}><i />{apiOnline ? 'API 正常' : 'API 未连接'}</span></div>
          <div className="routing-controls">
            <div className="routing-mode-switch">
              <button className={routingMode === 'auto' ? 'active' : ''} onClick={() => setRoutingMode('auto')}><Sparkles size={16} /><span><strong>Auto 最低价</strong><small>按质量筛选明确费率</small></span></button>
              <button className={routingMode === 'fixed' ? 'active' : ''} onClick={() => setRoutingMode('fixed')}><Route size={16} /><span><strong>固定渠道</strong><small>始终使用指定渠道</small></span></button>
            </div>
            <label><span>结算币种</span><select value={routingCurrency} onChange={(event) => setRoutingCurrency(event.target.value)}><option value="CNY">CNY 人民币</option><option value="USD">USD 美元</option></select></label>
            {routingMode === 'fixed' && <label className="fixed-provider-select"><span>默认渠道</span><select value={fixedChannelId} onChange={(event) => setFixedChannelId(event.target.value)}><option value="">请选择</option>{activeChannels.map((channel) => <option value={channel.id} key={channel.id}>{channel.name}</option>)}</select></label>}
            <button className="button dark" onClick={saveRouting} disabled={saving}>保存默认路由</button>
          </div>
          <div className="routing-note"><Activity size={15} /><span>{routingMode === 'auto' ? `自动模式只在 ${routingCurrency} 渠道中比较本次质量对应的非零费率。接口失败不会静默切换，避免重复计费。` : '固定渠道停用后，新批次会被阻止提交；已经入队的批次仍使用其加密快照。'}</span></div>
        </section>

        <section className="panel settings-section channel-section">
          <div className="settings-heading"><div><small>CHANNEL REGISTRY</small><h3>已注册渠道</h3><p>API Key 永不回显；编辑时留空表示沿用当前凭据。</p></div></div>
          {loading ? <div className="channel-empty"><span className="spinner" />正在读取本地渠道</div> : !config?.channels.length ? (
            <div className="channel-empty"><KeyRound size={24} /><strong>还没有可管理的渠道</strong><p>注册第一个 GPT Image 2 或兼容渠道，填入真实费率后即可使用 Auto。</p><button className="button lime" onClick={openCreate}><Plus size={15} />注册渠道</button></div>
          ) : <div className="channel-list">
            {config.channels.map((channel) => <article className={`managed-channel ${channel.active ? '' : 'inactive'}`} key={channel.id}>
              <div className="channel-identity"><span className="provider-icon cloud"><Sparkles size={19} /></span><p><strong>{channel.name}</strong><small>{channel.model} · {channel.baseUrl}</small></p></div>
              <div className="channel-rates">{(['low', 'medium', 'high'] as ProviderQuality[]).map((quality) => <p key={quality}><small>{qualityLabels[quality]}质量</small><strong>{channel.rates[quality] > 0 ? channel.rates[quality].toFixed(4) : '—'} <em>{channel.currency}</em></strong></p>)}</div>
              <div className="channel-security"><small>凭据</small><strong>{channel.apiKeyHint || '未配置'}</strong><span>{channel.lastUsedAt ? '已有运行使用' : '尚未调用'}</span></div>
              <span className={`provider-status ${channel.active ? '' : 'neutral'}`}>{channel.active ? '已启用' : '已停用'}</span>
              <div className="channel-actions"><button onClick={() => openEdit(channel)} title="编辑渠道"><Pencil size={15} /></button><button className={`toggle ${channel.active ? 'on' : ''}`} onClick={() => void toggleChannel(channel)} aria-label={channel.active ? `停用 ${channel.name}` : `启用 ${channel.name}`} aria-pressed={channel.active}><i /></button></div>
            </article>)}
          </div>}
        </section>

        <section className="panel settings-section safety-compact">
          <span className="shield-icon"><KeyRound size={19} /></span><div><strong>{liveGenerationEnabled ? '真实图片生成已开放' : '真实图片生成仍由服务端门控'}</strong><small>{liveGenerationEnabled ? '渠道配置可用；每次调用将写入渠道、模型、质量与估算成本。' : '配置渠道不会自动产生费用；仍需设置 MUSEFORGE_ENABLE_LIVE_GENERATION=true 并重启 API。'}</small></div><span className={`service-state ${liveGenerationEnabled ? 'online' : ''}`}><i />{liveGenerationEnabled ? 'LIVE' : 'SAFE'}</span>
        </section>
      </div>

      {editorOpen && <div className="provider-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditorOpen(false) }}>
        <div className="provider-modal" role="dialog" aria-modal="true" aria-labelledby="provider-editor-title">
          <div className="provider-modal-heading"><div><small>{editingId ? 'EDIT CHANNEL' : 'NEW CHANNEL'}</small><h3 id="provider-editor-title">{editingId ? '编辑渠道' : '注册 GPT Image 2 渠道'}</h3><p>密钥提交后只进入本地后端加密存储。</p></div><button onClick={() => setEditorOpen(false)} aria-label="关闭"><X size={18} /></button></div>
          <div className="provider-form-grid">
            <label><span>渠道名称</span><input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如 OpenAI 官方" /></label>
            <label><span>模型 ID</span><input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} placeholder="gpt-image-2" /></label>
            <label className="wide"><span>Base URL</span><input value={draft.baseUrl} onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} placeholder="https://api.openai.com/v1" /></label>
            <label><span>编辑接口路径</span><input value={draft.endpoint} onChange={(event) => setDraft({ ...draft, endpoint: event.target.value })} placeholder="/images/edits" /></label>
            <label><span>结算币种</span><select value={draft.currency} onChange={(event) => setDraft({ ...draft, currency: event.target.value })}><option value="CNY">CNY</option><option value="USD">USD</option></select></label>
            <label className="wide"><span>API Key {editingId && <em>留空沿用 {config?.channels.find((item) => item.id === editingId)?.apiKeyHint}</em>}</span><input type="password" value={draft.apiKey} onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })} autoComplete="new-password" placeholder={editingId ? '不修改则留空' : 'sk-…'} /></label>
          </div>
          <div className="rate-editor"><div><strong>每张图片估算费率</strong><small>用于 Auto 选路与成本记录，不代表渠道最终账单。</small></div><div>{(['low', 'medium', 'high'] as ProviderQuality[]).map((quality) => <label key={quality}><span>{qualityLabels[quality]}质量</span><input type="number" min="0" step="0.0001" value={draft.rates[quality]} onChange={(event) => setDraft({ ...draft, rates: { ...draft.rates, [quality]: Math.max(0, Number(event.target.value)) } })} /><em>{draft.currency} / 张</em></label>)}</div></div>
          <div className="provider-form-note"><CircleAlert size={15} /><span>建议一个渠道只使用一个结算币种。Auto 不做汇率换算，也不会把未知费率当作 0 元渠道。</span></div>
          <div className="provider-modal-actions"><button className="button" onClick={() => setEditorOpen(false)}>取消</button><button className="button dark" onClick={() => void saveChannel()} disabled={saving}>{saving ? '正在保存' : editingId ? '保存修改' : '加密并注册'}</button></div>
        </div>
      </div>}
    </div>
  )
}
