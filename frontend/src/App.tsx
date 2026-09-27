import { useCallback, useEffect, useRef, useState } from 'react'
import type { Contact } from './types'
import { api } from './api'
import ContactList from './components/ContactList'
import ChatArea from './components/ChatArea'
import ContactModal from './components/ContactModal'
import SettingsModal from './components/SettingsModal'
import LearningView from './components/LearningView'
import LikeBot from './components/LikeBot'
import MyInfoModal from './components/MyInfoModal'

interface Toast {
  id: number
  message: string
  kind: 'error' | 'info'
}

export default function App() {
  const [contacts, setContacts] = useState<Contact[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [activeId, setActiveId] = useState<number | null>(null)
  const [contactModal, setContactModal] = useState<{ open: boolean; contact?: Contact | null }>({
    open: false,
  })
  const [showSettings, setShowSettings] = useState(false)
  const [showLikeBot, setShowLikeBot] = useState(false)
  const [showMyInfo, setShowMyInfo] = useState(false)
  const [view, setView] = useState<'chat' | 'learning'>('chat')
  const [isNarrow, setIsNarrow] = useState<boolean>(() => window.innerWidth < 1024)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [appVersion, setAppVersion] = useState<string>('v4.0')
  const toastId = useRef(0)

  useEffect(() => {
    api
      .getHealth()
      .then((res) => {
        if (res.prompt_version) {
          setAppVersion(res.prompt_version)
        }
      })
      .catch(() => {})
  }, [])

  // 狭い画面(半画面など)では一覧と詳細を全画面で切り替える
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 1023px)')
    const update = () => setIsNarrow(mq.matches)
    update()
    mq.addEventListener('change', update)
    return () => mq.removeEventListener('change', update)
  }, [])

  const refreshContacts = useCallback(async () => {
    try {
      // 検索キーワードは相手名・自由記述・メッセージ本文に一致する
      setContacts(await api.listContacts(searchQuery))
    } catch (e) {
      showToast(e instanceof Error ? e.message : '相手一覧の取得に失敗しました', 'error')
    }
  }, [searchQuery])

  // 検索入力は少し待ってからBackendへ問い合わせる（連打対策）
  useEffect(() => {
    const t = setTimeout(refreshContacts, 250)
    return () => clearTimeout(t)
  }, [refreshContacts])

  const showToast = useCallback((message: string, kind: 'error' | 'info') => {
    const id = ++toastId.current
    setToasts((ts) => [...ts, { id, message, kind }])
    setTimeout(() => setToasts((ts) => ts.filter((t) => t.id !== id)), 3500)
  }, [])

  const activeContact = contacts.find((c) => c.id === activeId) ?? null

  // 狭い画面で詳細から一覧へ戻る
  const goToList = useCallback(() => {
    setActiveId(null)
    setView('chat')
  }, [])

  return (
    <div className="flex h-full flex-col bg-gray-100">
      {/* 上部バー */}
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-gray-200 bg-white px-4">
        <div className="flex items-center gap-2">
          <h1 className="text-base font-bold text-gray-800">
            <span className="mr-1.5">💬</span>Matching Reply Assistant
          </h1>
          <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 border border-emerald-300">
            {appVersion}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              if (view !== 'learning') refreshContacts()
              setView(view === 'learning' ? 'chat' : 'learning')
            }}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors ${
              view === 'learning'
                ? 'border-emerald-400 bg-emerald-50 text-emerald-600'
                : 'border-gray-300 text-gray-600 hover:bg-gray-50'
            }`}
            title="学習（AI練習・返信評価）"
          >
            🎓 学習
          </button>
          <button
            onClick={() => setShowLikeBot(true)}
            className="flex items-center gap-1.5 rounded-full border border-pink-300 bg-pink-50 px-3 py-1.5 text-sm text-pink-600 hover:bg-pink-100"
            title="いいねBOT（プロフィールからメッセージ生成）"
          >
            👍 いいねBOT
          </button>
          <button
            onClick={() => setShowMyInfo(true)}
            className="flex items-center gap-1.5 rounded-full border border-blue-300 bg-blue-50 px-3 py-1.5 text-sm text-blue-600 hover:bg-blue-100"
            title="自分の情報（返信生成時に参照される）"
          >
            📋 自分の情報
          </button>
          <button
            onClick={() => setShowSettings(true)}
            className="flex items-center gap-1.5 rounded-full border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
            title="設定"
          >
            ⚙ 設定
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* 広い画面: 一覧を常時表示 / 狭い画面: 詳細表示中は一覧を隠す */}
        {(!isNarrow || (!activeContact && view !== 'learning')) && (
          <ContactList
            contacts={contacts}
            search={searchQuery}
            onSearchChange={setSearchQuery}
            activeId={activeId}
            onSelect={(id) => {
              setActiveId(id)
              setView('chat')
            }}
            onAdd={() => setContactModal({ open: true, contact: null })}
            onEdit={(contact) => setContactModal({ open: true, contact })}
            onChanged={refreshContacts}
            onError={(msg) => showToast(msg, 'error')}
            fullWidth={isNarrow}
          />
        )}

        {view === 'learning' ? (
          <LearningView
            contacts={contacts}
            onToast={showToast}
            onBack={isNarrow ? goToList : undefined}
          />
        ) : activeContact ? (
          <ChatArea
            key={activeContact.id}
            contact={activeContact}
            onContactsChanged={refreshContacts}
            onToast={showToast}
            onBack={isNarrow ? goToList : undefined}
          />
        ) : isNarrow ? null : (
          <div className="flex flex-1 items-center justify-center">
            <div className="text-center text-gray-400">
              <div className="text-4xl">💬</div>
              <p className="mt-2 text-sm">左の一覧から相手を選択してください</p>
            </div>
          </div>
        )}
      </div>

      {contactModal.open && (
        <ContactModal
          contact={contactModal.contact}
          onClose={() => setContactModal({ open: false })}
          onSaved={async (contact) => {
            await refreshContacts()
            setActiveId(contact.id)
          }}
          onToast={showToast}
        />
      )}

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} onToast={showToast} />}
      {showLikeBot && <LikeBot onClose={() => setShowLikeBot(false)} onToast={showToast} />}
      {showMyInfo && <MyInfoModal onClose={() => setShowMyInfo(false)} onToast={showToast} />}

      {/* トースト通知 */}
      <div className="pointer-events-none fixed left-1/2 top-14 z-[60] flex -translate-x-1/2 flex-col items-center gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`rounded-full px-4 py-2 text-sm font-medium text-white shadow-lg ${
              t.kind === 'error' ? 'bg-red-500' : 'bg-gray-800/90'
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </div>
  )
}
