import { useState } from 'react'
import type { Message } from '../types'
import { formatTime } from '../format'

interface Props {
  message: Message
  onEdit: (id: number, content: string) => void
  onDelete: (message: Message) => void
}

export default function MessageBubble({ message, onEdit, onDelete }: Props) {
  const isSelf = message.sender === 'self'
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(message.content)

  const saveEdit = () => {
    const content = draft.trim()
    if (content && content !== message.content) onEdit(message.id, content)
    setEditing(false)
  }

  return (
    <div className={`group relative flex w-full ${isSelf ? 'justify-end' : 'justify-start'}`}>
      <button
        onClick={() => onDelete(message)}
        className="absolute -top-1.5 z-10 flex h-5 w-5 items-center justify-center rounded-full bg-gray-300 text-[10px] text-white opacity-0 shadow-sm transition-opacity hover:bg-red-400 group-hover:opacity-100"
        style={isSelf ? { left: -6 } : { right: -6 }}
        title="削除"
      >
        ×
      </button>
      <div className={`flex max-w-[70%] flex-col ${isSelf ? 'items-end' : 'items-start'}`}>
        {editing ? (
          <div className="flex w-80 max-w-full flex-col gap-1 rounded-2xl border border-gray-300 bg-white p-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={3}
              autoFocus
              className="w-full resize-none rounded-lg p-1 text-sm outline-none"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setEditing(false)}
                className="rounded-lg px-3 py-1 text-xs text-gray-500 hover:bg-gray-100"
              >
                キャンセル
              </button>
              <button
                onClick={saveEdit}
                className="rounded-lg bg-emerald-500 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-600"
              >
                保存
              </button>
            </div>
          </div>
        ) : (
          <>
            <div
              className={`whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-[15px] leading-relaxed shadow-sm ${
                isSelf
                  ? 'rounded-br-md bg-emerald-500 text-white'
                  : 'rounded-bl-md border border-gray-200 bg-white text-gray-800'
              }`}
            >
              {message.content}
            </div>
            <div className="mt-0.5 flex items-center gap-2">
              <span className="text-[11px] text-gray-400">{formatTime(message.created_at)}</span>
              <div className="hidden gap-1 group-hover:flex">
                <button
                  onClick={() => {
                    setDraft(message.content)
                    setEditing(true)
                  }}
                  className="rounded px-1.5 py-0.5 text-[11px] text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                  title="編集"
                >
                  ✎
                </button>
                <button
                  onClick={() => onDelete(message)}
                  className="rounded px-1.5 py-0.5 text-[11px] text-gray-400 hover:bg-red-50 hover:text-red-500"
                  title="削除"
                >
                  🗑
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
