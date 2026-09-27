import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Settings } from '../types'
import Modal from './Modal'
import KnowledgePanel from './KnowledgePanel'
import BackupPanel from './BackupPanel'
import ProfilePanel from './ProfilePanel'

interface Props {
  onClose: () => void
  onToast: (message: string, kind: 'error' | 'info') => void
}

type Tab = 'basic' | 'profile' | 'knowledge' | 'backup'

const TABS: { key: Tab; label: string }[] = [
  { key: 'basic', label: '基本設定' },
  { key: 'profile', label: '自分のプロフィール' },
  { key: 'knowledge', label: 'ルール・参考資料' },
  { key: 'backup', label: 'バックアップ' },
]

export default function SettingsModal({ onClose, onToast }: Props) {
  const [tab, setTab] = useState<Tab>('basic')
  const [settings, setSettings] = useState<Settings | null>(null)
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [fallbackProvider, setFallbackProvider] = useState('')
  const [fallbackModel, setFallbackModel] = useState('')
  const [fallbackApiKey, setFallbackApiKey] = useState('')
  const [clearApiKey, setClearApiKey] = useState(false)
  const [clearFallbackApiKey, setClearFallbackApiKey] = useState(false)
  const [temperature, setTemperature] = useState('0.8')
  const [maxTokens, setMaxTokens] = useState('512')
  const [historyLimit, setHistoryLimit] = useState('50')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api
      .getSettings()
      .then((s) => {
        setSettings(s)
        setProvider(s.provider)
        setModel(s.model)
        setFallbackProvider(s.fallback_provider)
        setFallbackModel(s.fallback_model)
        setTemperature(String(s.temperature))
        setMaxTokens(String(s.max_tokens))
        setHistoryLimit(String(s.history_limit))
      })
      .catch((e) => onToast(e instanceof Error ? e.message : '設定の取得に失敗しました', 'error'))
  }, [onToast])

  const providerInfo = settings?.providers.find((p) => p.name === provider)
  const models = providerInfo?.models ?? []
  const modelInfos = providerInfo?.model_infos ?? []
  const fallbackProviderInfo = settings?.providers.find((p) => p.name === fallbackProvider)
  const fallbackModels = fallbackProviderInfo?.models ?? []
  const fallbackModelInfos = fallbackProviderInfo?.model_infos ?? []

  const tierLabel = (tier?: string) => {
    if (tier === 'tier1') return ' 💎有料'
    return ''
  }

  const save = async () => {
    setSaving(true)
    try {
      // API Keyは入力があった時だけ送る（空欄のまま保存しても消えない）。
      // 明示的に消す場合はクリアボタンで clear_api_key を送る。
      const payload: Record<string, unknown> = {
        provider,
        model,
        temperature: Number(temperature),
        max_tokens: Number(maxTokens),
        history_limit: Number(historyLimit),
        fallback_provider: fallbackProvider,
        fallback_model: fallbackModel,
      }
      if (clearApiKey) {
        payload.clear_api_key = true
      } else if (apiKey.trim()) {
        payload.api_key = apiKey.trim()
      }
      if (clearFallbackApiKey) {
        payload.clear_fallback_api_key = true
      } else if (fallbackApiKey.trim()) {
        payload.fallback_api_key = fallbackApiKey.trim()
      }
      await api.updateSettings(payload)
      onToast('設定を保存しました', 'info')
      onClose()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '保存に失敗しました', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title="設定" onClose={onClose} wide>
      <div className="mb-4 flex gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-semibold transition-colors ${
              tab === t.key
                ? 'border-emerald-500 text-emerald-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'basic' &&
        (settings ? (
          <div className="flex flex-col gap-4">
            <div>
              <label className="mb-1 block text-sm font-semibold text-gray-600">AI Provider</label>
              <select
                value={provider}
                onChange={(e) => {
                  const next = e.target.value
                  setProvider(next)
                  const m = settings.providers.find((p) => p.name === next)?.models[0]
                  if (m) setModel(m)
                }}
                className="w-full rounded-xl border border-gray-200 px-3.5 py-2 text-sm outline-none focus:border-emerald-400"
              >
                {settings.providers.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1 block text-sm font-semibold text-gray-600">Model</label>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="w-full rounded-xl border border-gray-200 px-3.5 py-2 text-sm outline-none focus:border-emerald-400"
              >
                {model && !models.includes(model) && (
                  <option value={model}>{model}（現在利用不可）</option>
                )}
                {models.map((m) => {
                  const info = modelInfos.find((i) => i.name === m)
                  return (
                    <option key={m} value={m}>
                      {m}{tierLabel(info?.tier)}
                    </option>
                  )
                })}
              </select>
            </div>

            <div>
              <label className="mb-1 block text-sm font-semibold text-gray-600">
                API Key
                {settings.has_api_key && !settings.api_key_env && (
                  <span className="ml-2 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-normal text-emerald-700">
                    設定済み
                  </span>
                )}
                {settings.has_api_key && settings.api_key_env && (
                  <span className="ml-2 rounded-full bg-sky-100 px-2 py-0.5 text-xs font-normal text-sky-700">
                    環境変数（.env）
                  </span>
                )}
              </label>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => {
                    setApiKey(e.target.value)
                    if (e.target.value) setClearApiKey(false)
                  }}
                  placeholder={
                    settings.has_api_key
                      ? '変更する場合のみ入力（空のまま保存で変更なし）'
                      : 'sk-... を入力してください'
                  }
                  className="w-full rounded-xl border border-gray-200 px-3.5 py-2 text-sm outline-none focus:border-emerald-400"
                />
                {settings.has_api_key && !settings.api_key_env && (
                  <button
                    onClick={() => {
                      setClearApiKey(true)
                      setApiKey('')
                    }}
                    title="設定画面で登録したキーを削除（削除後は.envのキーが使われます）"
                    className="shrink-0 rounded-xl border border-gray-200 px-3 text-sm text-gray-500 hover:bg-gray-50"
                  >
                    クリア
                  </button>
                )}
              </div>
              <p className="mt-1 text-xs text-gray-400">
                API Keyはプロバイダごとに保存されます。.env（CEREBRAS_API_KEY / NVIDIA_API_KEY / GEMINI_API_KEY）に書けば、
                モデル切り替えやサーバー再起動後も消えません。空欄のまま保存しても変更されません。
              </p>
            </div>

            <div className="rounded-xl border border-dashed border-gray-300 p-3">
              <div className="mb-2 text-sm font-semibold text-gray-600">
                フォールバック（主プロバイダがレート制限等で失敗した時に自動で切り替え）
              </div>
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1 block text-xs font-semibold text-gray-500">
                      フォールバック Provider
                    </label>
                    <select
                      value={fallbackProvider}
                      onChange={(e) => {
                        const next = e.target.value
                        setFallbackProvider(next)
                        const m = settings.providers.find((p) => p.name === next)?.models[0]
                        if (m) setFallbackModel(m)
                      }}
                      className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm outline-none focus:border-emerald-400"
                    >
                      <option value="">（未設定）</option>
                      {settings.providers.map((p) => (
                        <option key={p.name} value={p.name}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-semibold text-gray-500">
                      フォールバック Model
                    </label>
                    <select
                      value={fallbackModel}
                      onChange={(e) => setFallbackModel(e.target.value)}
                      className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm outline-none focus:border-emerald-400"
                    >
                      {fallbackModels.length === 0 && fallbackModel && (
                        <option value={fallbackModel}>{fallbackModel}</option>
                      )}
                      {fallbackModels.map((m) => {
                        const info = fallbackModelInfos.find((i) => i.name === m)
                        return (
                          <option key={m} value={m}>
                            {m}{tierLabel(info?.tier)}
                          </option>
                        )
                      })}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-semibold text-gray-500">
                    フォールバック API Key
                    {settings.has_fallback_api_key && !settings.fallback_api_key_env && (
                      <span className="ml-2 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-normal text-emerald-700">
                        設定済み
                      </span>
                    )}
                    {settings.has_fallback_api_key && settings.fallback_api_key_env && (
                      <span className="ml-2 rounded-full bg-sky-100 px-2 py-0.5 text-xs font-normal text-sky-700">
                        環境変数（.env）
                      </span>
                    )}
                  </label>
                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={fallbackApiKey}
                      onChange={(e) => {
                        setFallbackApiKey(e.target.value)
                        if (e.target.value) setClearFallbackApiKey(false)
                      }}
                      placeholder={
                        settings.has_fallback_api_key
                          ? '変更する場合のみ入力（空のまま保存で変更なし）'
                          : 'フォールバック用API Key'
                      }
                      className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm outline-none focus:border-emerald-400"
                    />
                    {settings.has_fallback_api_key && !settings.fallback_api_key_env && (
                      <button
                        onClick={() => {
                          setClearFallbackApiKey(true)
                          setFallbackApiKey('')
                        }}
                        title="設定画面で登録したキーを削除（削除後は.envのキーが使われます）"
                        className="shrink-0 rounded-xl border border-gray-200 px-3 text-sm text-gray-500 hover:bg-gray-50"
                      >
                        クリア
                      </button>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-gray-400">
                    未設定ならフォールバック先プロバイダの.envキー（例: NVIDIA_API_KEY）が使われます。
                  </p>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="mb-1 block text-sm font-semibold text-gray-600">Temperature</label>
                <input
                  type="number"
                  value={temperature}
                  onChange={(e) => setTemperature(e.target.value)}
                  min={0}
                  max={2}
                  step={0.1}
                  className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm outline-none focus:border-emerald-400"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-semibold text-gray-600">Max Tokens</label>
                <input
                  type="number"
                  value={maxTokens}
                  onChange={(e) => setMaxTokens(e.target.value)}
                  min={1}
                  max={8192}
                  className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm outline-none focus:border-emerald-400"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-semibold text-gray-600">履歴送信件数</label>
                <input
                  type="number"
                  value={historyLimit}
                  onChange={(e) => setHistoryLimit(e.target.value)}
                  min={1}
                  max={500}
                  title="AIへ送信する直近のメッセージ数"
                  className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm outline-none focus:border-emerald-400"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={onClose}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                キャンセル
              </button>
              <button
                onClick={save}
                disabled={saving}
                className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
              >
                {saving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        ) : (
          <div className="py-6 text-center text-sm text-gray-400">読み込み中...</div>
        ))}

      {tab === 'profile' && <ProfilePanel onClose={onClose} onToast={onToast} />}
      {tab === 'knowledge' && <KnowledgePanel onToast={onToast} />}
      {tab === 'backup' && <BackupPanel onToast={onToast} />}
    </Modal>
  )
}
