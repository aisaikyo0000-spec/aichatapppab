import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { buildCondition, REPLY_DIRECTIONS } from '../replyDirections'
import type { Contact, TrainingSession, TrainingSessionSummary } from '../types'
import Modal from './Modal'

interface ProfileFields {
  persona_gender: string
  persona_age: string
  persona_hobbies: string
  persona_personality: string
  persona_style: string
  persona_profile: string
}

const EMPTY_PROFILE: ProfileFields = {
  persona_gender: '女性',
  persona_age: '',
  persona_hobbies: '',
  persona_personality: '',
  persona_style: '',
  persona_profile: '',
}

function ProfileFieldsForm({
  value,
  onChange,
}: {
  value: ProfileFields
  onChange: (v: ProfileFields) => void
}) {
  const set = (key: keyof ProfileFields) => (e: { target: { value: string } }) =>
    onChange({ ...value, [key]: e.target.value })
  return (
    <div className="flex flex-col gap-1.5">
      <div className="grid grid-cols-2 gap-1.5">
        <select
          value={value.persona_gender}
          onChange={set('persona_gender')}
          className="rounded border border-gray-200 bg-white px-2 py-1 text-xs outline-none focus:border-emerald-400"
        >
          <option value="">性別（未設定）</option>
          <option value="女性">女性</option>
          <option value="男性">男性</option>
          <option value="その他">その他</option>
        </select>
        <input
          value={value.persona_age}
          onChange={set('persona_age')}
          placeholder="年齢（例: 20代）"
          className="rounded border border-gray-200 px-2 py-1 text-xs outline-none focus:border-emerald-400"
        />
      </div>
      <input
        value={value.persona_hobbies}
        onChange={set('persona_hobbies')}
        placeholder="趣味（例: カフェ巡り・映画）"
        className="rounded border border-gray-200 px-2 py-1 text-xs outline-none focus:border-emerald-400"
      />
      <input
        value={value.persona_personality}
        onChange={set('persona_personality')}
        placeholder="性格（例: 明るく穏やか）"
        className="rounded border border-gray-200 px-2 py-1 text-xs outline-none focus:border-emerald-400"
      />
      <input
        value={value.persona_style}
        onChange={set('persona_style')}
        placeholder="話し方・文体（例: 丁寧語、絵文字多め、短文）"
        className="rounded border border-gray-200 px-2 py-1 text-xs outline-none focus:border-emerald-400"
      />
      <textarea
        value={value.persona_profile}
        onChange={set('persona_profile')}
        placeholder="自由記述（その他伝えたいこと）"
        rows={2}
        className="w-full resize-none rounded border border-gray-200 px-2 py-1 text-xs outline-none focus:border-emerald-400"
      />
    </div>
  )
}

interface Props {
  contacts: Contact[]
  onToast: (message: string, kind: 'error' | 'info') => void
}

export default function PracticePanel({ contacts, onToast }: Props) {
  const [sessions, setSessions] = useState<TrainingSessionSummary[]>([])
  const [active, setActive] = useState<TrainingSession | null>(null)
  const [input, setInput] = useState('')
  const [condition, setCondition] = useState('')
  const [direction, setDirection] = useState('')
  const [sending, setSending] = useState(false)
  const [replyPending, setReplyPending] = useState(false)
  const [replyFailed, setReplyFailed] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [revision, setRevision] = useState('')
  const [revising, setRevising] = useState(false)
  const [aiQuestion, setAiQuestion] = useState<string | null>(null)
  const [questionAnswer, setQuestionAnswer] = useState('')
  const [showNew, setShowNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [newContactId, setNewContactId] = useState<number | ''>('')
  const [newProfile, setNewProfile] = useState<ProfileFields>(EMPTY_PROFILE)
  const [editSession, setEditSession] = useState<TrainingSession | null>(null)
  const [editProfile, setEditProfile] = useState<ProfileFields>(EMPTY_PROFILE)
  const [savingProfile, setSavingProfile] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await api.listTrainingSessions())
    } catch (e) {
      onToast(e instanceof Error ? e.message : '練習履歴の取得に失敗しました', 'error')
    }
  }, [onToast])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'auto' })
  }, [active?.messages])

  // 生成した返信（改行付き）が全文見えるように、入力欄を内容に合わせて自動リサイズする
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [input])

  const create = async () => {
    if (!newName.trim() && newContactId === '') {
      onToast('相手の名前を入力するか、相手を選択してください', 'error')
      return
    }
    try {
      const session = await api.createTrainingSession(
        newContactId === '' ? null : newContactId,
        newName.trim() || '',
        newContactId === '' ? newProfile : undefined,
      )
      setShowNew(false)
      setNewName('')
      setNewContactId('')
      setNewProfile(EMPTY_PROFILE)
      await loadSessions()
      setActive(session)
      onToast('練習を開始しました（最初のメッセージを送ってください）', 'info')
    } catch (e) {
      onToast(e instanceof Error ? e.message : '練習の作成に失敗しました', 'error')
    }
  }

  const open = async (id: number) => {
    try {
      setActive(await api.getTrainingSession(id))
      setAiQuestion(null)
      setQuestionAnswer('')
    } catch (e) {
      onToast(e instanceof Error ? e.message : '読み込みに失敗しました', 'error')
    }
  }

  const openEdit = (s: TrainingSession) => {
    setEditProfile({
      persona_gender: s.persona_gender || '',
      persona_age: s.persona_age || '',
      persona_hobbies: s.persona_hobbies || '',
      persona_personality: s.persona_personality || '',
      persona_style: s.persona_style || '',
      persona_profile: s.persona_profile || '',
    })
    setEditSession(s)
  }

  const saveProfile = async () => {
    if (!editSession) return
    setSavingProfile(true)
    try {
      const updated = await api.updateTrainingSession(editSession.id, editProfile)
      setActive(updated)
      setEditSession(null)
      await loadSessions()
      onToast('プロフィールを保存しました', 'info')
    } catch (e) {
      onToast(e instanceof Error ? e.message : '保存に失敗しました', 'error')
    } finally {
      setSavingProfile(false)
    }
  }

  const remove = async (s: TrainingSessionSummary) => {
    try {
      await api.deleteTrainingSession(s.id)
      if (active?.id === s.id) setActive(null)
      await loadSessions()
      onToast('練習を削除しました', 'info')
    } catch (e) {
      onToast(e instanceof Error ? e.message : '削除に失敗しました', 'error')
    }
  }

  const send = async () => {
    const content = input.trim()
    if (!content || !active || sending) return
    setSending(true)
    setReplyFailed(false)
    try {
      // 1. 自分のメッセージを即座に保存・表示する（AI返信を待たない）
      const r1 = await api.addTrainingMessage(active.id, content)
      setActive((a) => (a ? { ...a, messages: r1.messages } : a))
      setInput('')
      if (inputRef.current) inputRef.current.style.height = 'auto'
      await loadSessions()
      // 2. AIの返信を生成する（失敗しても自分のメッセージは送信済み）
      setReplyPending(true)
      try {
        const r2 = await api.trainingAiReply(active.id, condition)
        setActive((a) => (a ? { ...a, messages: r2.messages } : a))
      } catch (e2) {
        setReplyFailed(true)
        onToast(
          e2 instanceof Error ? `相手の返信の生成に失敗しました: ${e2.message}` : '相手の返信の生成に失敗しました',
          'error',
        )
      }
    } catch (e) {
      onToast(e instanceof Error ? e.message : '送信に失敗しました', 'error')
    } finally {
      setSending(false)
      setReplyPending(false)
    }
  }

  const retryReply = async () => {
    if (!active || replyPending) return
    setReplyPending(true)
    setReplyFailed(false)
    try {
      const r2 = await api.trainingAiReply(active.id, condition)
      setActive((a) => (a ? { ...a, messages: r2.messages } : a))
    } catch (e2) {
      setReplyFailed(true)
      onToast(e2 instanceof Error ? e2.message : '再生成に失敗しました', 'error')
    } finally {
      setReplyPending(false)
    }
  }

  const generateMyReply = async () => {
    if (!active || generating) return
    setGenerating(true)
    setAiQuestion(null)
    setQuestionAnswer('')
    try {
      const r = await api.trainingMyReply(active.id, buildCondition(direction, condition))
      if (r.question) {
        setAiQuestion(r.question)
      } else {
        setInput(r.reply)
      }
    } catch (e) {
      onToast(e instanceof Error ? e.message : '生成に失敗しました', 'error')
    } finally {
      setGenerating(false)
    }
  }

  const saveAnswerAndRegenerate = async () => {
    if (!aiQuestion || !questionAnswer.trim() || !active) return
    try {
      await api.saveUserKnowledge(aiQuestion, questionAnswer.trim())
      setAiQuestion(null)
      setQuestionAnswer('')
      await generateMyReply()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '保存に失敗しました', 'error')
    }
  }

  const revise = async () => {
    const instruction = revision.trim()
    const original = input.trim()
    if (!active || revising || !instruction || !original) return
    setRevising(true)
    try {
      const r = await api.reviseMyReply(active.id, {
        condition: buildCondition(direction, condition),
        revision_instruction: instruction,
        original_generated: original,
      })
      setInput(r.reply)
      setRevision('')
    } catch (e) {
      onToast(e instanceof Error ? e.message : '修正に失敗しました', 'error')
    } finally {
      setRevising(false)
    }
  }

  return (
    <div className="flex h-full min-h-0">
      {/* セッション一覧 */}
      <div className="flex w-52 shrink-0 flex-col border-r border-gray-200 bg-white">
        <div className="border-b border-gray-200 p-2">
          <button
            onClick={() => setShowNew((v) => !v)}
            className="w-full rounded-lg bg-emerald-500 px-3 py-1.5 text-sm font-semibold text-white hover:bg-emerald-600"
          >
            ＋ 新しい練習
          </button>
          {showNew && (
            <div className="mt-2 flex flex-col gap-1.5 rounded-lg border border-gray-200 p-2">
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="架空の相手の名前（任意）"
                className="w-full rounded border border-gray-200 px-2 py-1 text-xs outline-none focus:border-emerald-400"
              />
              <select
                value={newContactId}
                onChange={(e) => setNewContactId(e.target.value === '' ? '' : Number(e.target.value))}
                className="w-full rounded border border-gray-200 bg-white px-2 py-1 text-xs outline-none focus:border-emerald-400"
              >
                <option value="">実際の相手から選ぶ（任意）</option>
                {contacts.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              {newContactId === '' && (
                <div className="rounded border border-violet-100 bg-violet-50 p-1.5">
                  <div className="mb-1 text-[11px] font-semibold text-violet-600">相手のプロフィール（任意）</div>
                  <ProfileFieldsForm value={newProfile} onChange={setNewProfile} />
                </div>
              )}
              <button
                onClick={create}
                className="rounded bg-emerald-500 px-2 py-1 text-xs font-semibold text-white hover:bg-emerald-600"
              >
                作成して開始
              </button>
            </div>
          )}
        </div>
        <div className="flex-1 overflow-y-auto">
          {sessions.length === 0 && (
            <div className="px-3 py-6 text-center text-xs text-gray-400">
              練習履歴はまだありません
            </div>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`group flex items-stretch border-b border-gray-100 ${active?.id === s.id ? 'bg-emerald-50' : ''}`}
            >
              <button
                onClick={() => open(s.id)}
                className="block min-w-0 flex-1 px-3 py-2 text-left hover:bg-gray-50"
              >
                <div className="truncate text-sm font-semibold text-gray-800">{s.persona_name}</div>
                <div className="truncate text-xs text-gray-500">{s.last_message || '（会話なし）'}</div>
              </button>
              <button
                onClick={() => remove(s)}
                title="練習を削除"
                className="hidden shrink-0 items-center justify-center px-2 text-xs text-gray-400 hover:text-red-500 group-hover:flex"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* 練習チャット */}
      <div className="flex min-w-0 flex-1 flex-col bg-gray-100">
        <div className="flex items-center gap-2 border-b border-gray-200 bg-white px-4 py-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-violet-400 text-sm font-bold text-white">
            {active ? active.persona_name.slice(0, 1) : '練'}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold text-gray-800">
              {active ? `${active.persona_name}（練習）` : 'AI練習'}
            </div>
            {active?.persona_profile_display && (
              <div className="truncate text-xs text-gray-500">{active.persona_profile_display}</div>
            )}
          </div>
          {active && active.contact_id === null && (
            <button
              onClick={() => openEdit(active)}
              className="shrink-0 rounded-full border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-100"
              title="相手のプロフィールを編集"
            >
              ✏ プロフィール
            </button>
          )}
          <select
            value={direction}
            onChange={(e) => setDirection(e.target.value)}
            disabled={!active}
            title="自分の返信の方向（生成する返信の雰囲気）"
            className="w-40 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-xs outline-none focus:border-emerald-400 disabled:opacity-50"
          >
            {REPLY_DIRECTIONS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
          <input
            value={condition}
            onChange={(e) => setCondition(e.target.value)}
            placeholder="練習テーマ（例: カフェの話）"
            disabled={!active}
            className="w-44 rounded-full border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs outline-none focus:border-emerald-400 disabled:opacity-50"
          />
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          {!active ? (
            <div className="pt-10 text-center text-sm text-gray-400">
              「新しい練習」をクリックして、架空の相手との会話練習を始めましょう
            </div>
          ) : active.messages.length === 0 ? (
            <div className="pt-10 text-center text-sm text-gray-400">
              最初のメッセージを送って会話を始めましょう
              <br />
              （送信後、AIが相手の返信を作成します）
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {active.messages.map((m, i) => (
                <div key={i} className={`flex w-full ${m.sender === 'self' ? 'justify-end' : 'justify-start'}`}>
                  <div
                    className={`max-w-[70%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-[15px] leading-relaxed shadow-sm ${
                      m.sender === 'self'
                        ? 'rounded-br-md bg-emerald-500 text-white'
                        : 'rounded-bl-md border border-gray-200 bg-white text-gray-800'
                    }`}
                  >
                    {m.content}
                  </div>
                </div>
              ))}
              {replyPending && (
                <div className="flex justify-start">
                  <div className="rounded-2xl rounded-bl-md border border-gray-200 bg-white px-3.5 py-2 text-sm text-gray-500 shadow-sm">
                    相手が返信を考えています…
                  </div>
                </div>
              )}
              {replyFailed && !replyPending && (
                <div className="flex justify-center">
                  <button
                    onClick={retryReply}
                    className="rounded-full border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-600 hover:bg-red-100"
                  >
                    相手の返信の生成に失敗しました。再試行する
                  </button>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        {editSession && (
          <Modal title="相手のプロフィールを編集" onClose={() => setEditSession(null)}>
            <div className="mb-4 max-h-[50vh] overflow-y-auto">
              <div className="mb-1 text-xs text-gray-500">
                「{editSession.persona_name}」のプロフィールを設定します。AI練習中の相手の性格・話し方に反映されます。
              </div>
              <ProfileFieldsForm value={editProfile} onChange={setEditProfile} />
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setEditSession(null)}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                キャンセル
              </button>
              <button
                onClick={saveProfile}
                disabled={savingProfile}
                className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
              >
                {savingProfile ? '保存中...' : '保存'}
              </button>
            </div>
          </Modal>
        )}

        <div className="border-t border-gray-200 bg-white px-4 py-3">
          {aiQuestion && (
            <div className="mb-3 rounded-xl border border-amber-300 bg-amber-50 p-4">
              <p className="mb-2 text-sm font-medium text-amber-800">💡 返信作成に質問があります</p>
              <p className="mb-2 text-sm text-gray-700">{aiQuestion}</p>
              <div className="flex gap-2">
                <input
                  value={questionAnswer}
                  onChange={(e) => setQuestionAnswer(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      saveAnswerAndRegenerate()
                    }
                  }}
                  placeholder="回答を入力..."
                  className="min-w-0 flex-1 rounded-full border border-amber-200 bg-white px-4 py-1.5 text-xs outline-none focus:border-amber-400"
                />
                <button
                  onClick={saveAnswerAndRegenerate}
                  disabled={!questionAnswer.trim()}
                  className="shrink-0 rounded-full bg-amber-500 px-4 py-1.5 text-xs font-semibold text-white hover:bg-amber-600 disabled:opacity-40"
                >
                  回答して再生成
                </button>
              </div>
            </div>
          )}
          <div className="mb-2 flex gap-2">
            <input
              value={revision}
              onChange={(e) => setRevision(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  revise()
                }
              }}
              placeholder="生成した返信への修正指示（例: もっと短く・絵文字を増やして・質問を1つに）"
              disabled={!active || !input.trim() || revising || sending}
              className="min-w-0 flex-1 rounded-full border border-amber-200 bg-amber-50 px-4 py-1.5 text-xs outline-none placeholder:text-amber-400 focus:border-amber-400 disabled:opacity-50"
            />
            <button
              onClick={revise}
              disabled={!active || revising || sending || !revision.trim() || !input.trim()}
              title="修正指示は履歴に記録され、品質改善に利用されます"
              className="shrink-0 rounded-full bg-amber-500 px-4 py-1.5 text-xs font-semibold text-white hover:bg-amber-600 disabled:opacity-40"
            >
              {revising ? '修正中...' : '🔧 修正して再生成'}
            </button>
          </div>
          <div className="flex gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault()
                  send()
                }
              }}
              placeholder={active ? '返信を入力 (Ctrl+Enterで送信)' : '先に練習を作成してください'}
              disabled={!active || sending}
              rows={2}
              className="min-w-0 max-h-[200px] flex-1 resize-none overflow-y-auto rounded-xl border border-gray-200 bg-gray-50 px-4 py-2.5 text-[15px] outline-none placeholder:text-gray-400 focus:border-emerald-400 disabled:opacity-50"
            />
            <button
              onClick={generateMyReply}
              disabled={!active || generating || sending}
              title="自分のプロフィール（設定→自分のプロフィール）に合わせて返信を作成"
              className="shrink-0 rounded-full border border-emerald-300 bg-white px-4 py-2 text-sm font-semibold text-emerald-600 hover:bg-emerald-50 disabled:opacity-40"
            >
              {generating ? '生成中...' : '✨ 自分の返信を生成'}
            </button>
            <button
              onClick={send}
              disabled={!active || sending || generating || !input.trim()}
              className="shrink-0 rounded-full bg-emerald-500 px-5 py-2 text-sm font-semibold text-white hover:bg-emerald-600 disabled:opacity-40"
            >
              {sending ? '送信中...' : '送信'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
