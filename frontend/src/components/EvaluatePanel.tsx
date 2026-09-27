import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { ChatMessage, Contact, EvaluationResult } from '../types'

interface Props {
  contacts: Contact[]
  onToast: (message: string, kind: 'error' | 'info') => void
}

function parseConversation(text: string): ChatMessage[] {
  const messages: ChatMessage[] = []
  for (const line of text.split('\n')) {
    const t = line.trim()
    if (!t) continue
    if (t.startsWith('自分:')) {
      messages.push({ sender: 'self', content: t.slice(3).trim() })
    } else if (t.startsWith('相手:')) {
      messages.push({ sender: 'contact', content: t.slice(3).trim() })
    } else {
      messages.push({ sender: 'contact', content: t })
    }
  }
  return messages
}

function ScoreStars({ score }: { score: number }) {
  return (
    <span className="text-amber-400">
      {'★'.repeat(Math.max(0, Math.min(5, score)))}
      <span className="text-gray-300">{'★'.repeat(5 - Math.max(0, Math.min(5, score)))}</span>
    </span>
  )
}

export default function EvaluatePanel({ contacts, onToast }: Props) {
  const [mode, setMode] = useState<'contact' | 'custom'>('contact')
  const [contactId, setContactId] = useState<number | ''>('')
  const [conversationText, setConversationText] = useState('')
  const [reply, setReply] = useState('')
  const [result, setResult] = useState<EvaluationResult | null>(null)
  const [evaluating, setEvaluating] = useState(false)
  const [memo, setMemo] = useState('')
  const [corrected, setCorrected] = useState('')
  const [saving, setSaving] = useState(false)

  const loadConversation = useCallback(
    async (cid: number) => {
      try {
        const msgs = await api.listMessages(cid)
        const recent = msgs.slice(-10)
        setConversationText(
          recent
            .map((m) => `${m.sender === 'contact' ? '相手' : '自分'}: ${m.content}`)
            .join('\n'),
        )
      } catch (e) {
        onToast(e instanceof Error ? e.message : '会話の取得に失敗しました', 'error')
      }
    },
    [onToast],
  )

  useEffect(() => {
    if (mode === 'contact' && contactId !== '') loadConversation(contactId)
  }, [mode, contactId, loadConversation])

  const evaluate = async () => {
    if (!reply.trim()) {
      onToast('評価したい返信を入力してください', 'error')
      return
    }
    setEvaluating(true)
    setResult(null)
    try {
      const conversation =
        mode === 'contact' && contactId !== '' ? parseConversation(conversationText) : parseConversation(conversationText)
      const r = await api.evaluateReply(conversation, reply.trim())
      setResult(r)
      setCorrected(reply.trim())
    } catch (e) {
      onToast(e instanceof Error ? e.message : '評価に失敗しました', 'error')
    } finally {
      setEvaluating(false)
    }
  }

  const save = async () => {
    if (!result) return
    setSaving(true)
    try {
      const conversation =
        mode === 'contact' && contactId !== '' ? parseConversation(conversationText) : parseConversation(conversationText)
      await api.createTrainingExample({
        conversation,
        ai_response: reply.trim(),
        user_feedback: memo.trim(),
        corrected_response: corrected.trim() || reply.trim(),
        rating: result.overall.score,
      })
      onToast('学習データとして保存しました（次の生成に反映されます）', 'info')
      setMemo('')
    } catch (e) {
      onToast(e instanceof Error ? e.message : '保存に失敗しました', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 gap-4 overflow-y-auto p-4">
      <div className="flex w-1/2 flex-col gap-3">
        <div>
          <label className="mb-1 block text-sm font-semibold text-gray-600">会話の元</label>
          <div className="flex gap-2">
            <button
              onClick={() => setMode('contact')}
              className={`rounded-full px-3 py-1 text-xs font-semibold ${
                mode === 'contact'
                  ? 'bg-emerald-500 text-white'
                  : 'border border-gray-300 text-gray-600 hover:bg-gray-50'
              }`}
            >
              実際の相手の会話を使う
            </button>
            <button
              onClick={() => setMode('custom')}
              className={`rounded-full px-3 py-1 text-xs font-semibold ${
                mode === 'custom'
                  ? 'bg-emerald-500 text-white'
                  : 'border border-gray-300 text-gray-600 hover:bg-gray-50'
              }`}
            >
              会話を直接入力
            </button>
          </div>
        </div>

        {mode === 'contact' && (
          <select
            value={contactId}
            onChange={(e) => setContactId(e.target.value === '' ? '' : Number(e.target.value))}
            className="w-full rounded-xl border border-gray-200 bg-white px-3.5 py-2 text-sm outline-none focus:border-emerald-400"
          >
            <option value="">相手を選択</option>
            {contacts.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}

        <div>
          <label className="mb-1 block text-sm font-semibold text-gray-600">会話（直近10件）</label>
          <textarea
            value={conversationText}
            onChange={(e) => setConversationText(e.target.value)}
            rows={10}
            placeholder={
              mode === 'contact'
                ? '相手を選択すると自動で入力されます'
                : '1行に1メッセージ。「相手: 〜」「自分: 〜」の形式で入力'
            }
            className="w-full resize-none rounded-xl border border-gray-200 bg-gray-50 px-3.5 py-2 text-sm outline-none focus:border-emerald-400"
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-semibold text-gray-600">
            評価したい返信（自分が書いた文章）
          </label>
          <textarea
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            rows={4}
            placeholder="返信文章を入力してください"
            className="w-full resize-none rounded-xl border border-gray-200 px-3.5 py-2 text-sm outline-none focus:border-emerald-400"
          />
        </div>

        <div>
          <button
            onClick={evaluate}
            disabled={evaluating || !reply.trim()}
            className="rounded-full bg-violet-500 px-5 py-2 text-sm font-bold text-white hover:bg-violet-600 disabled:opacity-50"
          >
            {evaluating ? '評価中...' : 'AIに評価してもらう'}
          </button>
          <p className="mt-1 text-[11px] text-gray-400">
            AI評価は参考情報です。最終的な判断はあなたの評価を優先してください。
          </p>
        </div>
      </div>

      <div className="flex w-1/2 flex-col gap-3">
        {!result ? (
          <div className="flex flex-1 items-center justify-center rounded-xl border-2 border-dashed border-gray-200 text-sm text-gray-400">
            評価結果がここに表示されます
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-2">
              {result.items.map((item) => (
                <div key={item.key} className="rounded-xl border border-gray-200 bg-white p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-bold text-gray-700">{item.label}</span>
                    <ScoreStars score={item.score} />
                  </div>
                  {item.comment && <p className="mt-1 text-xs text-gray-500">{item.comment}</p>}
                </div>
              ))}
              <div className="rounded-xl border-2 border-violet-300 bg-violet-50 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-bold text-violet-700">総合評価</span>
                  <ScoreStars score={result.overall.score} />
                </div>
                {result.overall.comment && (
                  <p className="mt-1 text-xs text-gray-600">{result.overall.comment}</p>
                )}
              </div>
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-3">
              <h4 className="mb-2 text-sm font-bold text-gray-700">学習データとして保存</h4>
              <label className="mb-1 block text-xs font-semibold text-gray-500">修正版（任意）</label>
              <textarea
                value={corrected}
                onChange={(e) => setCorrected(e.target.value)}
                rows={2}
                className="mb-2 w-full resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-emerald-400"
              />
              <label className="mb-1 block text-xs font-semibold text-gray-500">メモ（任意）</label>
              <textarea
                value={memo}
                onChange={(e) => setMemo(e.target.value)}
                rows={2}
                placeholder="例: もっと砕けた感じが好き"
                className="mb-2 w-full resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-emerald-400"
              />
              <button
                onClick={save}
                disabled={saving}
                className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
              >
                {saving ? '保存中...' : '良い返信例として保存'}
              </button>
              <p className="mt-1 text-[11px] text-gray-400">
                保存した内容は、今後の返信生成時に「良い返信例」として参照されます。
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
