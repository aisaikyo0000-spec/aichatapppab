import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { HistoryItem } from '../types'
import { formatTime } from '../format'
import Modal from './Modal'

interface Props {
  contactId: number
  onClose: () => void
  onSend: (content: string) => void
  onToast: (message: string, kind: 'error' | 'info') => void
}

export default function HistoryModal({ contactId, onClose, onSend, onToast }: Props) {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await api.listHistory(contactId))
    } catch (e) {
      onToast(e instanceof Error ? e.message : '生成履歴の取得に失敗しました', 'error')
    } finally {
      setLoading(false)
    }
  }, [contactId, onToast])

  useEffect(() => {
    load()
  }, [load])

  const updateFlag = async (item: HistoryItem, field: 'is_adopted' | 'is_copied' | 'is_sent', value: boolean) => {
    try {
      await api.updateHistory(item.id, { [field]: value })
      await load()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '更新に失敗しました', 'error')
    }
  }

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      onToast('クリップボードにコピーしました', 'info')
    } catch {
      onToast('コピーに失敗しました', 'error')
    }
  }

  const text = (item: HistoryItem) => (item.revised_text ? item.revised_text : item.generated_text)

  return (
    <Modal title="生成履歴" onClose={onClose} wide>
      <div className="flex flex-col gap-2">
        {loading ? (
          <div className="py-6 text-center text-sm text-gray-400">読み込み中...</div>
        ) : items.length === 0 ? (
          <div className="py-6 text-center text-sm text-gray-400">生成履歴はまだありません</div>
        ) : (
          items.map((item) => (
            <div key={item.id} className="rounded-xl border border-gray-200 bg-white p-3">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-[11px] text-gray-400">
                  {formatTime(item.created_at)} · {item.provider}/{item.model}
                </span>
                <span className="flex gap-1 text-[11px]">
                  {item.is_adopted && <span className="text-emerald-500">採用済み</span>}
                  {item.is_copied && <span className="text-gray-400">コピー済み</span>}
                  {item.is_sent && <span className="text-sky-500">送信済み</span>}
                </span>
              </div>
              {item.current_condition && (
                <div className="mb-1 text-[11px] text-amber-600">条件: {item.current_condition}</div>
              )}
              {item.revision_instruction && (
                <div className="mb-1 text-[11px] text-gray-400">
                  修正指示: {item.revision_instruction}
                </div>
              )}
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-800">
                {text(item)}
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <button
                  onClick={() => updateFlag(item, 'is_adopted', !item.is_adopted)}
                  className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                    item.is_adopted
                      ? 'border-emerald-400 bg-emerald-500 text-white'
                      : 'border-gray-300 text-gray-600 hover:bg-gray-100'
                  }`}
                >
                  {item.is_adopted ? '採用を解除' : '採用'}
                </button>
                <button
                  onClick={() => {
                    copy(text(item))
                    updateFlag(item, 'is_copied', true)
                  }}
                  className="rounded-full border border-gray-300 px-3 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-100"
                >
                  コピー
                </button>
                <button
                  onClick={() => {
                    onSend(text(item))
                    updateFlag(item, 'is_sent', true)
                  }}
                  className="rounded-full bg-sky-500 px-3 py-1 text-xs font-semibold text-white hover:bg-sky-600"
                >
                  送信（履歴に追加）
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </Modal>
  )
}
