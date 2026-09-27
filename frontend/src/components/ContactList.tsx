import { useState } from 'react'
import type { Contact } from '../types'
import { api } from '../api'
import { avatarColor, formatTime } from '../format'
import ConfirmDialog from './ConfirmDialog'

interface Props {
  contacts: Contact[]
  search: string
  onSearchChange: (q: string) => void
  activeId: number | null
  onSelect: (id: number) => void
  onAdd: () => void
  onEdit: (contact: Contact) => void
  onChanged: () => void
  onError: (message: string) => void
  /** 狭い画面で一覧を全幅表示する */
  fullWidth?: boolean
}

export default function ContactList({
  contacts,
  search,
  onSearchChange,
  activeId,
  onSelect,
  onAdd,
  onEdit,
  onChanged,
  onError,
  fullWidth,
}: Props) {
  const [menuFor, setMenuFor] = useState<number | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Contact | null>(null)

  const togglePin = async (contact: Contact) => {
    try {
      await api.updateContact(contact.id, { is_pinned: !contact.is_pinned })
      onChanged()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'エラーが発生しました')
    }
  }

  const archive = async (contact: Contact) => {
    try {
      await api.updateContact(contact.id, { is_archived: true })
      onChanged()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'エラーが発生しました')
    }
  }

  const remove = async (contact: Contact) => {
    try {
      await api.deleteContact(contact.id)
      onChanged()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'エラーが発生しました')
    }
  }

  return (
    <div
      className={`flex h-full flex-col border-r border-gray-200 bg-white ${fullWidth ? 'w-full' : 'w-72'}`}
    >
      <div className="flex items-center gap-2 border-b border-gray-200 px-3 py-3">
        <input
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="相手名で検索"
          className="w-full rounded-full bg-gray-100 px-4 py-1.5 text-sm outline-none placeholder:text-gray-400 focus:bg-white focus:ring-2 focus:ring-emerald-300"
        />
        <button
          onClick={onAdd}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-xl font-bold text-white shadow hover:bg-emerald-600"
          title="相手を追加"
        >
          ＋
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {contacts.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-gray-400">
            {search ? '該当する相手がいません' : '相手を追加しましょう'}
          </div>
        )}
        {contacts.map((c) => (
          <div
            key={c.id}
            className={`group relative flex cursor-pointer items-center gap-3 border-b border-gray-100 px-3 py-2.5 transition-colors ${
              c.id === activeId ? 'bg-emerald-50' : 'hover:bg-gray-50'
            }`}
            onClick={() => onSelect(c.id)}
            onMouseLeave={() => setMenuFor(null)}
          >
            {c.profile_image_url ? (
              <div className="h-11 w-11 shrink-0 overflow-hidden rounded-full bg-gray-200">
                <img
                  src={c.profile_image_url}
                  alt=""
                  className="h-full w-full object-cover"
                  onError={(e) => {
                    ;(e.target as HTMLImageElement).style.display = 'none'
                  }}
                />
              </div>
            ) : (
              <div
                className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-lg font-bold text-white ${avatarColor(c.name)}`}
              >
                {c.name.slice(0, 1)}
              </div>
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-1">
                <span className="truncate text-sm font-semibold text-gray-800">
                  {c.is_pinned && <span className="mr-1 text-amber-500">★</span>}
                  {c.name}
                </span>
                <div className="flex shrink-0 items-center gap-1.5 text-[11px] text-gray-400">
                  {c.contact_message_count !== undefined && c.contact_message_count > 0 && (
                    <span
                      className="rounded bg-emerald-50 px-1 py-0.5 text-[10px] font-medium text-emerald-700 border border-emerald-200"
                      title="相手からの受信通数"
                    >
                      {c.contact_message_count}通
                    </span>
                  )}
                  {c.last_message_at && <span>{formatTime(c.last_message_at)}</span>}
                </div>
              </div>
              {c.last_message && (
                <div className="mt-0.5 truncate text-xs text-gray-500">{c.last_message}</div>
              )}
            </div>

            {menuFor === c.id && (
              <div
                className="absolute right-2 top-10 z-20 w-36 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-sm shadow-lg"
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  className="block w-full px-3 py-1.5 text-left hover:bg-gray-50"
                  onClick={() => {
                    onEdit(c)
                    setMenuFor(null)
                  }}
                >
                  編集
                </button>
                <button
                  className="block w-full px-3 py-1.5 text-left hover:bg-gray-50"
                  onClick={() => {
                    togglePin(c)
                    setMenuFor(null)
                  }}
                >
                  {c.is_pinned ? 'ピン留めを解除' : 'ピン留め'}
                </button>
                <button
                  className="block w-full px-3 py-1.5 text-left hover:bg-gray-50"
                  onClick={() => {
                    archive(c)
                    setMenuFor(null)
                  }}
                >
                  アーカイブ
                </button>
                <button
                  className="block w-full px-3 py-1.5 text-left text-red-500 hover:bg-red-50"
                  onClick={() => {
                    setDeleteTarget(c)
                    setMenuFor(null)
                  }}
                >
                  削除
                </button>
              </div>
            )}
            <button
              className="hidden h-6 w-6 shrink-0 items-center justify-center rounded-full text-gray-400 hover:bg-gray-200 group-hover:flex"
              onClick={(e) => {
                e.stopPropagation()
                setMenuFor(menuFor === c.id ? null : c.id)
              }}
              title="メニュー"
            >
              ⋮
            </button>
          </div>
        ))}
      </div>

      {deleteTarget && (
        <ConfirmDialog
          title="相手を削除"
          message={`「${deleteTarget.name}」を削除します。\nこの相手とのチャット・画像・生成履歴もすべて完全に削除されます。よろしいですか？`}
          onConfirm={() => remove(deleteTarget)}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </div>
  )
}
