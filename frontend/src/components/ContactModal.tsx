import { useCallback, useEffect, useRef, useState } from 'react'
import type { Contact, ContactAiSettings, ContactImage, KnowledgeFile, KnowledgeType, Settings } from '../types'
import { api } from '../api'
import Modal from './Modal'
import ConfirmDialog from './ConfirmDialog'

interface Props {
  contact?: Contact | null
  onClose: () => void
  onSaved: (contact: Contact) => void
  onToast: (message: string, kind: 'error' | 'info') => void
}

interface PendingImage {
  key: string
  file: File
  url: string
  description: string
}

let pendingKey = 0

function knowledgeLabel(type: KnowledgeType): string {
  if (type === 'rules') return '【ルール】'
  if (type === 'training') return '【学習】'
  return '【参考】'
}

export default function ContactModal({ contact, onClose, onSaved, onToast }: Props) {
  const [name, setName] = useState(contact?.name ?? '')
  const [profile, setProfile] = useState(contact?.profile ?? '')
  const [saving, setSaving] = useState(false)

  const [images, setImages] = useState<ContactImage[]>([])
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([])
  const [uploading, setUploading] = useState(false)
  const [deleteImage, setDeleteImage] = useState<ContactImage | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const isEdit = Boolean(contact)

  const [settings, setSettings] = useState<Settings | null>(null)
  const [knowledge, setKnowledge] = useState<KnowledgeFile[]>([])
  const [override, setOverride] = useState(false)
  const [ai, setAi] = useState<ContactAiSettings>({
    provider: null,
    model: null,
    temperature: null,
    max_tokens: null,
    history_limit: null,
    knowledge_file_ids: null,
  })
  const [aiSaving, setAiSaving] = useState(false)

  // アンマウント時にObjectURLを解放する（最新のリストをrefで参照）
  const pendingRef = useRef<PendingImage[]>([])
  pendingRef.current = pendingImages
  useEffect(() => {
    return () => {
      for (const p of pendingRef.current) URL.revokeObjectURL(p.url)
    }
  }, [])

  useEffect(() => {
    if (!contact) return
    api
      .listContactImages(contact.id)
      .then(setImages)
      .catch((e) => onToast(e instanceof Error ? e.message : '画像の取得に失敗しました', 'error'))
    Promise.all([api.getContactAiSettings(contact.id), api.getSettings(), api.listKnowledge()])
      .then(([aiSettings, s, k]) => {
        setAi(aiSettings)
        setSettings(s)
        setKnowledge(k)
        setOverride(
          Boolean(
            aiSettings.provider ||
              aiSettings.model ||
              aiSettings.temperature != null ||
              aiSettings.max_tokens != null ||
              aiSettings.history_limit != null ||
              aiSettings.knowledge_file_ids,
          ),
        )
      })
      .catch((e) => onToast(e instanceof Error ? e.message : '設定の取得に失敗しました', 'error'))
  }, [contact, onToast])

  const save = async () => {
    const n = name.trim()
    if (!n) {
      onToast('名前は必須です', 'error')
      return
    }
    setSaving(true)
    try {
      const saved = contact
        ? await api.updateContact(contact.id, { name: n, profile })
        : await api.createContact(n, profile)
      if (!contact && pendingImages.length > 0) {
        try {
          for (const p of pendingImages) {
            await api.uploadContactImage(saved.id, p.file, p.description)
          }
        } catch (e) {
          onToast(
            '相手は追加しましたが画像のアップロードに失敗しました: ' +
              (e instanceof Error ? e.message : ''),
            'error',
          )
        }
      }
      onToast(contact ? '相手情報を更新しました' : '相手を追加しました', 'info')
      onSaved(saved)
      onClose()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '保存に失敗しました', 'error')
    } finally {
      setSaving(false)
    }
  }

  // 編集モード: 追加した画像を即アップロードする
  const uploadFiles = useCallback(
    async (files: FileList | File[]) => {
      if (!contact || uploading) return
      const list = Array.from(files).filter((f) => f.type.startsWith('image/'))
      if (list.length === 0) return
      if (images.length + list.length > 20) {
        onToast('画像は最大20枚までです', 'error')
        return
      }
      setUploading(true)
      try {
        for (const f of list) {
          const img = await api.uploadContactImage(contact.id, f, '')
          setImages((ims) => [...ims, img])
        }
        onToast('画像を追加しました', 'info')
      } catch (e) {
        onToast(e instanceof Error ? e.message : '画像のアップロードに失敗しました', 'error')
      } finally {
        setUploading(false)
      }
    },
    [contact, uploading, images.length, onToast],
  )

  // 追加モード: 相手作成後にまとめてアップロードするため、まずプレビューだけ保持する
  const addPendingFiles = useCallback(
    (files: FileList | File[]) => {
      const list = Array.from(files).filter((f) => f.type.startsWith('image/'))
      if (list.length === 0) return
      if (images.length + pendingImages.length + list.length > 20) {
        onToast('画像は最大20枚までです', 'error')
        return
      }
      setPendingImages((ps) => [
        ...ps,
        ...list.map((f) => ({
          key: `p${pendingKey++}`,
          file: f,
          url: URL.createObjectURL(f),
          description: '',
        })),
      ])
    },
    [images.length, pendingImages.length, onToast],
  )

  const handleFiles = useCallback(
    (files: FileList | File[]) => {
      if (contact) {
        void uploadFiles(files)
      } else {
        addPendingFiles(files)
      }
    },
    [contact, uploadFiles, addPendingFiles],
  )

  // モーダル内のどこでも（テキスト欄以外で）ペーストすれば画像として追加される
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      const files = e.clipboardData?.files
      if (files && files.length) {
        e.preventDefault()
        handleFiles(files)
      }
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [handleFiles])

  const removePending = (p: PendingImage) => {
    URL.revokeObjectURL(p.url)
    setPendingImages((ps) => ps.filter((x) => x.key !== p.key))
  }

  const updatePendingDescription = (key: string, description: string) => {
    setPendingImages((ps) => ps.map((p) => (p.key === key ? { ...p, description } : p)))
  }

  const saveImageDescription = async (img: ContactImage, description: string) => {
    try {
      const updated = await api.updateContactImage(img.id, description)
      setImages((ims) => ims.map((i) => (i.id === img.id ? updated : i)))
    } catch (e) {
      onToast(e instanceof Error ? e.message : '説明の保存に失敗しました', 'error')
    }
  }

  const removeImage = async (img: ContactImage) => {
    try {
      await api.deleteContactImage(img.id)
      setImages((ims) => ims.filter((i) => i.id !== img.id))
      onToast('画像を削除しました', 'info')
    } catch (e) {
      onToast(e instanceof Error ? e.message : '画像の削除に失敗しました', 'error')
    }
  }

  const saveAi = async () => {
    if (!contact) return
    setAiSaving(true)
    try {
      const payload: ContactAiSettings = override
        ? {
            provider: ai.provider,
            model: ai.model,
            temperature: ai.temperature,
            max_tokens: ai.max_tokens,
            history_limit: ai.history_limit,
            knowledge_file_ids: ai.knowledge_file_ids && ai.knowledge_file_ids.length > 0 ? ai.knowledge_file_ids : null,
          }
        : {
            provider: null,
            model: null,
            temperature: null,
            max_tokens: null,
            history_limit: null,
            knowledge_file_ids: null,
          }
      await api.updateContactAiSettings(contact.id, payload)
      onToast('相手のAI設定を保存しました', 'info')
    } catch (e) {
      onToast(e instanceof Error ? e.message : '保存に失敗しました', 'error')
    } finally {
      setAiSaving(false)
    }
  }

  const models = settings?.providers.find((p) => p.name === ai.provider)?.models ?? []
  const modelInfos = settings?.providers.find((p) => p.name === ai.provider)?.model_infos ?? []

  const tierLabel = (tier?: string) => {
    if (tier === 'tier1') return ' 💎有料'
    return ''
  }

  const imageCount = images.length + pendingImages.length

  return (
    <Modal title={contact ? '相手を編集' : '相手を追加'} onClose={onClose} wide>
      <div className="flex flex-col gap-4">
        <div>
          <label className="mb-1 block text-sm font-semibold text-gray-600">
            名前 <span className="text-red-500">*</span>
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例: さくら"
            autoFocus
            className="w-full rounded-xl border border-gray-200 px-3.5 py-2 text-sm outline-none focus:border-emerald-400"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-semibold text-gray-600">自由記述</label>
          <textarea
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
            placeholder="例: カフェ巡りが好き。犬を飼っている。AI生成時に相手情報として利用されます"
            rows={3}
            className="w-full resize-none rounded-xl border border-gray-200 px-3.5 py-2 text-sm outline-none focus:border-emerald-400"
          />
        </div>

        {/* 画像管理（追加時・編集時どちらでも利用可能） */}
        <div>
          <label className="mb-1 block text-sm font-semibold text-gray-600">
            プロフィール画像（{imageCount}/20枚）
          </label>
          <div
            className="flex flex-wrap gap-2 rounded-xl border-2 border-dashed border-gray-300 bg-gray-50 p-3"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              handleFiles(e.dataTransfer.files)
            }}
            onPaste={(e) => {
              if (e.clipboardData?.files?.length) handleFiles(e.clipboardData.files)
            }}
          >
            {images.map((img, idx) => (
              <div key={img.id} className="w-36">
                <div className="relative">
                  <img
                    src={img.url}
                    alt=""
                    className="h-24 w-full rounded-lg object-cover"
                    onError={(e) => {
                      ;(e.target as HTMLImageElement).style.display = 'none'
                    }}
                  />
                  {idx === 0 && (
                    <span className="absolute left-1 top-1 rounded-full bg-emerald-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                      プロフ
                    </span>
                  )}
                  <button
                    onClick={() => setDeleteImage(img)}
                    className="absolute right-1 top-1 rounded-full bg-black/60 px-1.5 py-0.5 text-[10px] text-white hover:bg-red-500"
                    title="削除"
                  >
                    ✕
                  </button>
                </div>
                <input
                  value={img.description}
                  onChange={(e) => saveImageDescription(img, e.target.value)}
                  placeholder="画像の説明"
                  className="mt-1 w-full rounded border border-gray-200 px-2 py-1 text-[11px] outline-none focus:border-emerald-400"
                />
              </div>
            ))}
            {pendingImages.map((p, pIdx) => (
              <div key={p.key} className="w-36">
                <div className="relative">
                  <img
                    src={p.url}
                    alt=""
                    className="h-24 w-full rounded-lg object-cover"
                    onError={(e) => {
                      ;(e.target as HTMLImageElement).style.display = 'none'
                    }}
                  />
                  {pIdx === 0 && (
                    <span className="absolute left-1 top-1 rounded-full bg-emerald-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                      プロフ
                    </span>
                  )}
                  <button
                    onClick={() => removePending(p)}
                    className="absolute right-1 top-1 rounded-full bg-black/60 px-1.5 py-0.5 text-[10px] text-white hover:bg-red-500"
                    title="削除"
                  >
                    ✕
                  </button>
                </div>
                <input
                  value={p.description}
                  onChange={(e) => updatePendingDescription(p.key, e.target.value)}
                  placeholder="画像の説明"
                  className="mt-1 w-full rounded border border-gray-200 px-2 py-1 text-[11px] outline-none focus:border-emerald-400"
                />
              </div>
            ))}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading || imageCount >= 20}
              className="flex h-24 w-36 flex-col items-center justify-center gap-1 rounded-lg border border-gray-300 text-xs text-gray-500 hover:bg-white disabled:opacity-50"
            >
              {uploading ? 'アップロード中...' : '＋ 追加'}
              <span className="text-[10px] text-gray-400">クリック / ドラッグ&ドロップ / ペースト</span>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files) handleFiles(e.target.files)
                e.target.value = ''
              }}
            />
          </div>
          <p className="mt-1 text-[11px] text-gray-400">
            1枚目の画像がプロフィール画像として相手一覧・チャットに表示されます。
          </p>
          {!contact && (
            <p className="text-[11px] text-gray-400">
              追加する画像は「保存」時に相手と一緒にアップロードされます（最大20枚）。各画像の説明文は生成時のAIに渡されます。
            </p>
          )}
        </div>

        {isEdit && contact && (
          <>
            {/* 相手ごとのAI設定 */}
            <div>
              <label className="mb-1 block text-sm font-semibold text-gray-600">
                この相手だけのAI設定
              </label>
              <label className="mb-2 flex items-center gap-2 text-xs text-gray-600">
                <input
                  type="checkbox"
                  checked={!override}
                  onChange={(e) => setOverride(!e.target.checked)}
                  className="h-4 w-4 accent-emerald-500"
                />
                全体設定を使う（オフにするとこの相手専用の設定を入力できます）
              </label>
              {override && (
                <div className="flex flex-col gap-3 rounded-xl border border-gray-200 bg-gray-50 p-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="mb-1 block text-xs font-semibold text-gray-500">Provider</label>
                      <select
                        value={ai.provider ?? ''}
                        onChange={(e) => {
                          const next = e.target.value
                          setAi((a) => ({ ...a, provider: next || null }))
                          const m = settings?.providers.find((p) => p.name === next)?.models[0]
                          if (m) setAi((a) => ({ ...a, model: m }))
                        }}
                        className="w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm outline-none focus:border-emerald-400"
                      >
                        <option value="">（全体設定）</option>
                        {settings?.providers.map((p) => (
                          <option key={p.name} value={p.name}>
                            {p.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-semibold text-gray-500">Model</label>
                      <select
                        value={ai.model ?? ''}
                        onChange={(e) => setAi((a) => ({ ...a, model: e.target.value || null }))}
                        className="w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm outline-none focus:border-emerald-400"
                      >
                        <option value="">（全体設定）</option>
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
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="mb-1 block text-xs font-semibold text-gray-500">Temperature</label>
                      <input
                        type="number"
                        value={ai.temperature ?? ''}
                        onChange={(e) =>
                          setAi((a) => ({
                            ...a,
                            temperature: e.target.value === '' ? null : Number(e.target.value),
                          }))
                        }
                        min={0}
                        max={2}
                        step={0.1}
                        placeholder="全体設定"
                        className="w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm outline-none focus:border-emerald-400"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-semibold text-gray-500">Max Tokens</label>
                      <input
                        type="number"
                        value={ai.max_tokens ?? ''}
                        onChange={(e) =>
                          setAi((a) => ({
                            ...a,
                            max_tokens: e.target.value === '' ? null : Number(e.target.value),
                          }))
                        }
                        min={1}
                        max={8192}
                        placeholder="全体設定"
                        className="w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm outline-none focus:border-emerald-400"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-semibold text-gray-500">履歴送信件数</label>
                      <input
                        type="number"
                        value={ai.history_limit ?? ''}
                        onChange={(e) =>
                          setAi((a) => ({
                            ...a,
                            history_limit: e.target.value === '' ? null : Number(e.target.value),
                          }))
                        }
                        min={1}
                        max={500}
                        placeholder="全体設定"
                        className="w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm outline-none focus:border-emerald-400"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-semibold text-gray-500">
                      この相手に使うルール・参考資料・学習用（未選択なら全体設定の有効ファイルを使用）
                    </label>
                    <div className="max-h-36 overflow-y-auto rounded-lg border border-gray-200 bg-white p-2">
                      {knowledge.length === 0 && (
                        <p className="text-xs text-gray-400">登録されているファイルはありません</p>
                      )}
                      {knowledge.map((f) => {
                        const checked = ai.knowledge_file_ids?.includes(f.id) ?? false
                        return (
                          <label key={f.id} className="flex items-center gap-2 py-0.5 text-xs text-gray-700">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) => {
                                const ids = ai.knowledge_file_ids ? [...ai.knowledge_file_ids] : []
                                if (e.target.checked && !ids.includes(f.id)) ids.push(f.id)
                                if (!e.target.checked) {
                                  const i = ids.indexOf(f.id)
                                  if (i >= 0) ids.splice(i, 1)
                                }
                                setAi((a) => ({ ...a, knowledge_file_ids: ids }))
                              }}
                              className="h-3.5 w-3.5 accent-emerald-500"
                            />
                            <span className="min-w-0 flex-1 truncate">
                              {knowledgeLabel(f.type)}
                              {f.file_name}
                            </span>
                          </label>
                        )
                      })}
                    </div>
                  </div>
                  <div className="flex justify-end">
                    <button
                      onClick={saveAi}
                      disabled={aiSaving}
                      className="rounded-lg bg-emerald-500 px-4 py-1.5 text-xs font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
                    >
                      {aiSaving ? '保存中...' : 'AI設定を保存'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </>
        )}

        <div className="flex justify-end gap-2 border-t border-gray-100 pt-3">
          <button
            onClick={onClose}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
          >
            閉じる
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

      {deleteImage && (
        <ConfirmDialog
          title="画像を削除"
          message="この画像を削除します。よろしいですか？"
          onConfirm={() => removeImage(deleteImage)}
          onClose={() => setDeleteImage(null)}
        />
      )}
    </Modal>
  )
}
