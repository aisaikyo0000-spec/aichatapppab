import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { TrainingExample } from '../types'
import { formatTime } from '../format'
import ConfirmDialog from './ConfirmDialog'

interface Props {
  onToast: (message: string, kind: 'error' | 'info') => void
}

export default function MaterialsPanel({ onToast }: Props) {
  const [items, setItems] = useState<TrainingExample[]>([])
  const [deleteTarget, setDeleteTarget] = useState<TrainingExample | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await api.listTrainingExamples())
    } catch (e) {
      onToast(e instanceof Error ? e.message : '学習データの取得に失敗しました', 'error')
    } finally {
      setLoading(false)
    }
  }, [onToast])

  useEffect(() => {
    load()
  }, [load])

  const remove = async (item: TrainingExample) => {
    try {
      await api.deleteTrainingExample(item.id)
      await load()
      onToast('削除しました', 'info')
    } catch (e) {
      onToast(e instanceof Error ? e.message : '削除に失敗しました', 'error')
    }
  }

  const conversationPreview = (item: TrainingExample) => {
    const lines = item.conversation
      .slice(-2)
      .map((m) => `${m.sender === 'contact' ? '相手' : '自分'}: ${m.content}`)
    return lines.join('\n')
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto p-4">
      <p className="mb-3 text-xs text-gray-500">
        返信評価で保存した「良い返信例」とフィードバックです。生成時に高評価・最近のものがAIへ参照されます（将来のRAG / Fine-tuning用の構造化データ）。
      </p>
      {loading ? (
        <div className="py-6 text-center text-sm text-gray-400">読み込み中...</div>
      ) : items.length === 0 ? (
        <div className="py-6 text-center text-sm text-gray-400">
          学習データはまだありません。
          <br />
          「返信評価」タブから評価結果を保存するとここに表示されます。
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {items.map((item) => (
            <div key={item.id} className="rounded-xl border border-gray-200 bg-white p-3">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-[11px] text-gray-400">{formatTime(item.created_at)}</span>
                {item.rating != null && (
                  <span className="text-amber-400">
                    {'★'.repeat(item.rating)}
                    <span className="text-gray-300">{'★'.repeat(5 - item.rating)}</span>
                  </span>
                )}
              </div>
              {conversationPreview(item) && (
                <pre className="mb-1 whitespace-pre-wrap rounded bg-gray-50 p-2 text-[11px] text-gray-500">
                  {conversationPreview(item)}
                </pre>
              )}
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-800">
                {item.corrected_response || item.ai_response}
              </p>
              {item.user_feedback && (
                <p className="mt-1 text-xs text-gray-500">メモ: {item.user_feedback}</p>
              )}
              <div className="mt-2 flex justify-end">
                <button
                  onClick={() => setDeleteTarget(item)}
                  className="rounded px-2 py-0.5 text-xs text-red-500 hover:bg-red-50"
                >
                  削除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="学習データを削除"
          message="この学習データを削除します。よろしいですか？"
          onConfirm={() => remove(deleteTarget)}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </div>
  )
}
