import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { KnowledgeFile, KnowledgeType } from '../types'

interface Props {
  onToast: (message: string, kind: 'error' | 'info') => void
}

interface AddForm {
  type: KnowledgeType
  file_name: string
  content: string
}

interface EditForm {
  file: KnowledgeFile
  file_name: string
  content: string
}

const TYPE_LABEL: Record<KnowledgeType, string> = {
  rules: 'ルール',
  references: '参考資料',
  training: '学習用',
}

export default function KnowledgePanel({ onToast }: Props) {
  const [rules, setRules] = useState<KnowledgeFile[]>([])
  const [references, setReferences] = useState<KnowledgeFile[]>([])
  const [training, setTraining] = useState<KnowledgeFile[]>([])
  const [adding, setAdding] = useState<AddForm | null>(null)
  const [editing, setEditing] = useState<EditForm | null>(null)
  const [loadingContent, setLoadingContent] = useState(false)
  const [saving, setSaving] = useState(false)
  const [reloading, setReloading] = useState(false)

  const load = useCallback(async () => {
    try {
      const [r, ref, tr] = await Promise.all([
        api.listKnowledge('rules'),
        api.listKnowledge('references'),
        api.listKnowledge('training'),
      ])
      setRules(r)
      setReferences(ref)
      setTraining(tr)
    } catch (e) {
      onToast(e instanceof Error ? e.message : '読み込みに失敗しました', 'error')
    }
  }, [onToast])

  useEffect(() => {
    load()
  }, [load])

  const reloadFromFolders = async () => {
    setReloading(true)
    try {
      const res = await api.reloadKnowledge()
      await load()
      const parts = (Object.keys(res.counts) as KnowledgeType[])
        .filter((t) => res.counts[t].added > 0 || res.counts[t].removed > 0)
        .map((t) => `${TYPE_LABEL[t]}: +${res.counts[t].added} / -${res.counts[t].removed}`)
      onToast(
        parts.length > 0
          ? `フォルダから再読み込みしました（${parts.join('、')}）`
          : 'フォルダに変更はありませんでした',
        'info',
      )
    } catch (e) {
      onToast(e instanceof Error ? e.message : '再読み込みに失敗しました', 'error')
    } finally {
      setReloading(false)
    }
  }

  const toggle = async (f: KnowledgeFile) => {
    try {
      await api.updateKnowledge(f.id, { enabled: !f.enabled })
      await load()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '更新に失敗しました', 'error')
    }
  }

  const startEdit = async (f: KnowledgeFile) => {
    setLoadingContent(true)
    try {
      const detail = await api.getKnowledge(f.id)
      setEditing({ file: f, file_name: detail.file_name, content: detail.content })
    } catch (e) {
      onToast(e instanceof Error ? e.message : '内容の読み込みに失敗しました', 'error')
    } finally {
      setLoadingContent(false)
    }
  }

  const saveEdit = async () => {
    if (!editing || !editing.file_name.trim()) return
    setSaving(true)
    try {
      await api.updateKnowledge(editing.file.id, {
        file_name: editing.file_name.trim(),
        content: editing.content,
      })
      onToast('保存しました', 'info')
      setEditing(null)
      await load()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '保存に失敗しました', 'error')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (f: KnowledgeFile) => {
    try {
      await api.deleteKnowledge(f.id)
      await load()
      onToast('削除しました', 'info')
    } catch (e) {
      onToast(e instanceof Error ? e.message : '削除に失敗しました', 'error')
    }
  }

  const add = async () => {
    if (!adding || !adding.file_name.trim() || !adding.content.trim()) return
    setSaving(true)
    try {
      await api.createKnowledge(adding.type, adding.file_name.trim(), adding.content)
      onToast('追加しました', 'info')
      setAdding(null)
      await load()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '追加に失敗しました', 'error')
    } finally {
      setSaving(false)
    }
  }

  const renderList = (items: KnowledgeFile[], type: KnowledgeType) => (
    <div className="flex flex-col gap-2">
      {items.length === 0 && (
        <p className="text-xs text-gray-400">登録されているファイルはありません</p>
      )}
      {items.map((f) => (
        <div
          key={f.id}
          className={`flex items-center gap-2 rounded-lg border p-2 ${
            f.enabled ? 'border-gray-200 bg-white' : 'border-gray-200 bg-gray-100 opacity-60'
          }`}
        >
          <input
            type="checkbox"
            checked={f.enabled}
            onChange={() => toggle(f)}
            title="有効/無効"
            className="h-4 w-4 accent-emerald-500"
          />
          <span className="min-w-0 flex-1 truncate text-sm text-gray-700">{f.file_name}</span>
          <button
            onClick={() => startEdit(f)}
            disabled={loadingContent}
            className="shrink-0 rounded px-2 py-0.5 text-xs text-gray-500 hover:bg-gray-100"
            title="編集"
          >
            編集
          </button>
          <button
            onClick={() => remove(f)}
            className="shrink-0 rounded px-2 py-0.5 text-xs text-red-500 hover:bg-red-50"
            title="削除"
          >
            削除
          </button>
        </div>
      ))}
      <button
        onClick={() => setAdding({ type, file_name: '', content: '' })}
        className="self-start rounded-lg border border-dashed border-gray-300 px-3 py-1 text-xs text-gray-500 hover:bg-gray-50"
      >
        ＋ ファイルを追加
      </button>
    </div>
  )

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs text-gray-500">
          プロジェクトの <code className="rounded bg-gray-100 px-1">knowledge/</code> フォルダ内の各サブフォルダ（下記セクション名の横に表示）に
          TXTファイルを置くと、起動時に自動で読み込まれます。ボタンでいつでも再読み込みできます。
        </p>
        <button
          onClick={reloadFromFolders}
          disabled={reloading}
          className="shrink-0 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          {reloading ? '読み込み中...' : '🔄 フォルダから再読み込み'}
        </button>
      </div>

      <div>
        <h3 className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-bold text-gray-700">
          ルール（RULES）
          <code className="rounded bg-gray-100 px-1 text-[11px] font-normal text-gray-500">knowledge/rules/</code>
          <span className="text-xs font-normal text-gray-400">必ず守る絶対ルール</span>
        </h3>
        {renderList(rules, 'rules')}
      </div>
      <div>
        <h3 className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-bold text-gray-700">
          参考資料（REFERENCES）
          <code className="rounded bg-gray-100 px-1 text-[11px] font-normal text-gray-500">knowledge/references/</code>
          <span className="text-xs font-normal text-gray-400">判断材料として参考にする</span>
        </h3>
        {renderList(references, 'references')}
      </div>
      <div>
        <h3 className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-bold text-gray-700">
          学習用（LEARNING）
          <code className="rounded bg-gray-100 px-1 text-[11px] font-normal text-gray-500">knowledge/training/</code>
          <span className="text-xs font-normal text-gray-400">文体・知識・好みの参考にする</span>
        </h3>
        {renderList(training, 'training')}
      </div>

      {adding && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3">
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-sm font-bold text-gray-700">{TYPE_LABEL[adding.type]}を追加</h4>
            <button onClick={() => setAdding(null)} className="text-xs text-gray-400 hover:text-gray-600">
              閉じる
            </button>
          </div>
          <input
            value={adding.file_name}
            onChange={(e) => setAdding({ ...adding, file_name: e.target.value })}
            placeholder="ファイル名（例: 基本ルール）"
            className="mb-2 w-full rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm outline-none focus:border-emerald-400"
          />
          <textarea
            value={adding.content}
            onChange={(e) => setAdding({ ...adding, content: e.target.value })}
            placeholder={'1行に1項目ずつ入力（例）\n自然な日本語にする\n相手の発言を無視しない'}
            rows={5}
            className="mb-2 w-full resize-none rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-emerald-400"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setAdding(null)}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
            >
              キャンセル
            </button>
            <button
              onClick={add}
              disabled={saving || !adding.file_name.trim() || !adding.content.trim()}
              className="rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-600 disabled:opacity-40"
            >
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </div>
      )}

      {editing && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3">
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-sm font-bold text-gray-700">
              {TYPE_LABEL[editing.file.type]}を編集
            </h4>
            <button onClick={() => setEditing(null)} className="text-xs text-gray-400 hover:text-gray-600">
              閉じる
            </button>
          </div>
          <input
            value={editing.file_name}
            onChange={(e) => setEditing({ ...editing, file_name: e.target.value })}
            placeholder="ファイル名（例: 基本ルール）"
            className="mb-2 w-full rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm outline-none focus:border-emerald-400"
          />
          <textarea
            value={editing.content}
            onChange={(e) => setEditing({ ...editing, content: e.target.value })}
            placeholder="内容を入力"
            rows={8}
            className="mb-2 w-full resize-y rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-emerald-400"
          />
          <div className="flex items-center justify-between gap-2">
            <p className="text-[11px] text-gray-400">保存するとフォルダのTXTファイルにも書き戻されます</p>
            <div className="flex gap-2">
              <button
                onClick={() => setEditing(null)}
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
              >
                キャンセル
              </button>
              <button
                onClick={saveEdit}
                disabled={saving || !editing.file_name.trim()}
                className="rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-600 disabled:opacity-40"
              >
                {saving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
