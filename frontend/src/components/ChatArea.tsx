import { useEffect, useRef, useState } from 'react'
import type { Contact, Message } from '../types'
import { api } from '../api'
import { avatarColor, daysSince, formatDateJapanese } from '../format'
import MessageBubble from './MessageBubble'
import MessageInput from './MessageInput'
import GenerationPanel from './GenerationPanel'
import HistoryModal from './HistoryModal'
import ConfirmDialog from './ConfirmDialog'

interface Props {
  contact: Contact
  onContactsChanged: () => void
  onToast: (message: string, kind: 'error' | 'info') => void
  /** 狭い画面で一覧へ戻る */
  onBack?: () => void
}

export default function ChatArea({ contact, onContactsChanged, onToast, onBack }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(true)
  const [deleteTarget, setDeleteTarget] = useState<Message | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [tone, setTone] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  // 相手から受信したメッセージ統計（初返信日・相手からの通数）
  const contactMessages = messages.filter((m) => m.sender === 'contact')
  const firstContactMessage = contactMessages.length > 0 ? contactMessages[0] : null
  const rawFirstContactAt = firstContactMessage
    ? firstContactMessage.created_at
    : contact.first_contact_message_at
  const firstContactDateStr = rawFirstContactAt ? formatDateJapanese(rawFirstContactAt) : null
  const firstContactDays = rawFirstContactAt ? daysSince(rawFirstContactAt) : null
  const contactMsgCount = Math.max(contactMessages.length, contact.contact_message_count ?? 0)

  useEffect(() => {
    setTone('')
  }, [contact.id])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .listMessages(contact.id)
      .then((ms) => {
        if (!cancelled) setMessages(ms)
      })
      .catch((e) => onToast(e instanceof Error ? e.message : 'メッセージの取得に失敗しました', 'error'))
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [contact.id, onToast])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'auto' })
  }, [messages])

  const send = async (
    sender: 'contact' | 'self',
    content: string,
    source: 'manual' | 'generated' | 'imported' | 'legacy_unknown' = 'manual',
    generationHistoryId?: number | null,
  ) => {
    try {
      const msg = await api.createMessage(contact.id, sender, content, source, generationHistoryId)
      setMessages((ms) => [...ms, msg])
      onContactsChanged()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '送信に失敗しました', 'error')
    }
  }

  const edit = async (id: number, content: string) => {
    try {
      const updated = await api.updateMessage(id, content)
      setMessages((ms) => ms.map((m) => (m.id === id ? updated : m)))
      onContactsChanged()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '編集に失敗しました', 'error')
    }
  }

  const remove = async (message: Message) => {
    try {
      await api.deleteMessage(message.id)
      setMessages((ms) => ms.filter((m) => m.id !== message.id))
      onContactsChanged()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '削除に失敗しました', 'error')
    }
  }

  const exportChat = () => {
    const a = document.createElement('a')
    a.href = api.exportChatUrl(contact.id)
    a.download = `${contact.name}_chat.txt`
    a.click()
  }

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col bg-gray-100">
      {/* ヘッダー */}
      <div className="flex items-center gap-3 border-b border-gray-200 bg-white px-4 py-2.5">
        {onBack && (
          <button
            onClick={onBack}
            className="shrink-0 rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
            title="相手一覧に戻る"
          >
            ← 一覧
          </button>
        )}
        {contact.profile_image_url ? (
          <div className="h-9 w-9 shrink-0 overflow-hidden rounded-full bg-gray-200">
            <img
              src={contact.profile_image_url}
              alt=""
              className="h-full w-full object-cover"
              onError={(e) => {
                ;(e.target as HTMLImageElement).style.display = 'none'
              }}
            />
          </div>
        ) : (
          <div
            className={`flex h-9 w-9 items-center justify-center rounded-full text-base font-bold text-white ${avatarColor(contact.name)}`}
          >
            {contact.name.slice(0, 1)}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-bold text-gray-800">
              {contact.is_pinned && <span className="mr-1 text-amber-500">★</span>}
              {contact.name}
            </span>
            <div className="flex items-center gap-1.5 text-xs">
              <span
                className="inline-flex items-center gap-1 rounded bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 border border-blue-200"
                title={firstContactDateStr ? `初返信日時: ${firstContactDateStr}` : 'まだ相手からの返信はありません'}
              >
                📅 {firstContactDays !== null ? `初返信から${firstContactDays}日` : '未受信'}
              </span>
              <span
                className="inline-flex items-center gap-1 rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 border border-emerald-200"
                title="相手から届いたメッセージの合計通数"
              >
                💬 相手から: {contactMsgCount}通
              </span>
            </div>
          </div>
          {contact.profile && (
            <div className="truncate text-xs text-gray-500">{contact.profile}</div>
          )}
        </div>
        {/* トーン選択（セグメントコントロール） */}
        <div className="flex items-center rounded-full border border-gray-300 bg-gray-50 p-0.5 text-xs">
          <button
            onClick={() => setTone('')}
            className={`rounded-full px-2.5 py-1 font-medium transition-colors ${
              tone === '' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-800'
            }`}
            title="学習実績から自動判定"
          >
            自動
          </button>
          <button
            onClick={() => setTone('keigo')}
            className={`rounded-full px-2.5 py-1 font-medium transition-colors ${
              tone === 'keigo' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-500 hover:text-blue-700'
            }`}
            title="敬語ベース（です・ます調）"
          >
            敬語
          </button>
          <button
            onClick={() => setTone('hybrid')}
            className={`rounded-full px-2.5 py-1 font-medium transition-colors ${
              tone === 'hybrid' ? 'bg-purple-600 text-white shadow-sm' : 'text-gray-500 hover:text-purple-700'
            }`}
            title="ハイブリッド（会話敬語＋親しみ）"
          >
            混合
          </button>
          <button
            onClick={() => setTone('tame')}
            className={`rounded-full px-2.5 py-1 font-medium transition-colors ${
              tone === 'tame' ? 'bg-orange-600 text-white shadow-sm' : 'text-gray-500 hover:text-orange-700'
            }`}
            title="タメ口（カジュアル口調・敬語禁止）"
          >
            タメ口
          </button>
        </div>

        <button
          onClick={() => setShowHistory(true)}
          className="shrink-0 rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
          title="過去のAI生成結果を確認・復元"
        >
          🕘 履歴
        </button>
        <button
          onClick={exportChat}
          className="shrink-0 rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
          title="チャット履歴をテキストでエクスポート"
        >
          ⬇ エクスポート
        </button>
      </div>

      {/* メッセージ一覧 */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {loading ? (
          <div className="pt-8 text-center text-sm text-gray-400">読み込み中...</div>
        ) : messages.length === 0 ? (
          <div className="pt-8 text-center text-sm text-gray-400">
            まだメッセージがありません。
            <br />
            実際のマッチングアプリからコピーした相手のメッセージを入力しましょう。
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {messages.map((m) => (
              <MessageBubble
                key={m.id}
                message={m}
                onEdit={edit}
                onDelete={setDeleteTarget}
              />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* AI返信生成 */}
      <GenerationPanel
        contactId={contact.id}
        onSend={(text, historyId) => send('self', text, 'generated', historyId)}
        onMessage={onToast}
        tone={tone}
      />

      {/* メッセージ入力 */}
      <MessageInput onSend={send} onToast={onToast} />

      {deleteTarget && (
        <ConfirmDialog
          title="メッセージを削除"
          message="このメッセージを削除します。よろしいですか？"
          onConfirm={() => remove(deleteTarget)}
          onClose={() => setDeleteTarget(null)}
        />
      )}

      {showHistory && (
        <HistoryModal
          contactId={contact.id}
          onClose={() => setShowHistory(false)}
          onSend={(content) => send('self', content, 'manual')}
          onToast={onToast}
        />
      )}

    </div>
  )
}
