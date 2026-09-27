import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import { buildCondition, REPLY_DIRECTIONS } from '../replyDirections'
import type { GenerationPreview } from '../types'
import PromptPreviewModal from './PromptPreviewModal'

interface ReplyCard {
  text: string
  historyId: number
  isAdopted: boolean
  revisionNote: string
  rating?: string | null
  ratingReason?: string | null
  showFeedback?: boolean
}

interface Props {
  contactId: number
  onSend: (content: string, historyId?: number) => void
  onMessage: (message: string, kind: 'error' | 'info') => void
  /** 敬語/ハイブリッド/タメ口の選択 */
  tone?: string
}

const GOOD_TAGS = ['自然な口語', '長さが丁度いい', '話題の拾い方が自然', '適度なカジュアルさ', '共感が良い']
const BAD_TAGS = ['AIっぽい', '堅苦しい', '長すぎる', '質問攻め', '話題を無視した', '知ったかぶり']

export default function GenerationPanel({ contactId, onSend, onMessage, tone }: Props) {
  const [direction, setDirection] = useState('')
  const [condition, setCondition] = useState('')
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [cards, setCards] = useState<ReplyCard[]>([])
  const [revising, setRevising] = useState<number | null>(null)
  const [revisionText, setRevisionText] = useState('')
  const [preview, setPreview] = useState<GenerationPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [aiQuestion, setAiQuestion] = useState<string | null>(null)
  const [questionAnswer, setQuestionAnswer] = useState('')
  const [savingAnswer, setSavingAnswer] = useState(false)
  const [genMeta, setGenMeta] = useState<{ buildVersion?: string; effectiveTone?: string } | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    // 相手を切り替えたら生成結果をクリアする
    setCards([])
    setError('')
    setRevising(null)
    setRevisionText('')
    setAiQuestion(null)
    setQuestionAnswer('')
    setGenMeta(null)
  }, [contactId])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && generating) cancelGenerate()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  })

  const cancelGenerate = () => {
    abortRef.current?.abort()
    setGenerating(false)
  }

  const effectiveCondition = buildCondition(direction, condition)

  const generate = async (opts?: { revision?: string; original?: string; mode?: 'normal' | 'followup' }) => {
    if (generating || !contactId) return
    setGenerating(true)
    setError('')
    const controller = new AbortController()
    abortRef.current = controller
    const reqMode = opts?.mode ?? 'normal'
    try {
      const result = await api.generate(
        {
          contact_id: contactId,
          condition: effectiveCondition,
          candidates: 3, // 常に3案生成
          revision_instruction: opts?.revision ?? '',
          original_generated: opts?.original ?? (cards.length > 0 ? cards.map((c, i) => `案${i + 1}: ${c.text}`).join('\n') : ''),
          tone,
          mode: reqMode,
        },
        controller.signal,
      )
      if (result.question) {
        setAiQuestion(result.question)
        setCards([])
        setGenMeta(null)
      } else {
        setAiQuestion(null)
        setGenMeta({
          buildVersion: result.build_version,
          effectiveTone: result.effective_tone,
        })
        const newCards: ReplyCard[] = result.replies.map((text, i) => ({
          text,
          historyId: result.history_ids[i],
          isAdopted: false,
          revisionNote: opts?.revision ? `修正: ${opts.revision}` : '',
          rating: null,
          ratingReason: null,
          showFeedback: false,
        }))
        setCards(newCards)
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        onMessage('生成をキャンセルしました', 'info')
      } else if (e instanceof ApiError) {
        setError(e.message)
      } else {
        setError(e instanceof Error ? e.message : '生成に失敗しました')
      }
    } finally {
      setGenerating(false)
      abortRef.current = null
    }
  }

  const submitQuestionAnswer = async () => {
    if (!aiQuestion || !questionAnswer.trim()) return
    setSavingAnswer(true)
    try {
      await api.saveUserKnowledge(aiQuestion, questionAnswer.trim())
      onMessage('回答を保存しました', 'info')
      setAiQuestion(null)
      setQuestionAnswer('')
      // 保存した回答を使って再生成
      generate()
    } catch (e) {
      onMessage(e instanceof Error ? e.message : '保存に失敗しました', 'error')
    } finally {
      setSavingAnswer(false)
    }
  }

  const copy = async (card: ReplyCard) => {
    try {
      await navigator.clipboard.writeText(card.text)
      onMessage('クリップボードにコピーしました', 'info')
      api.updateHistory(card.historyId, { is_copied: true }).catch(() => {})
    } catch {
      onMessage('コピーに失敗しました', 'error')
    }
  }

  const sendReply = async (card: ReplyCard) => {
    onSend(card.text, card.historyId)
    setCards((cs) => cs.map((c) => (c === card ? { ...c, isAdopted: true } : c)))
    api.updateHistory(card.historyId, { is_sent: true, is_adopted: true }).catch(() => {})
    let copied = false
    try {
      await navigator.clipboard.writeText(card.text)
      copied = true
      api.updateHistory(card.historyId, { is_copied: true }).catch(() => {})
    } catch {
      // コピー失敗は送信自体には影響しない
    }
    onMessage(copied ? '送信しました（クリップボードにコピー済み）' : '送信しました', 'info')
  }

  const submitRevision = (card: ReplyCard) => {
    const instruction = revisionText.trim()
    if (!instruction) return
    generate({ revision: instruction, original: card.text })
    setRevisionText('')
    setRevising(null)
  }

  const rateReply = async (card: ReplyCard, rating: 'good' | 'neutral' | 'bad') => {
    const newRating = card.rating === rating ? null : rating
    setCards((cs) =>
      cs.map((c) =>
        c === card ? { ...c, rating: newRating, showFeedback: newRating !== null } : c
      )
    )
    try {
      await api.updateHistory(card.historyId, { rating: newRating ?? '' })
      if (newRating) {
        onMessage(
          newRating === 'good'
            ? '高評価を保存しました（今後の生成に反映されます）'
            : newRating === 'bad'
            ? '低評価を保存しました（避ける傾向として反映されます）'
            : '評価を保存しました',
          'info'
        )
      }
    } catch (e) {
      onMessage('評価の保存に失敗しました', 'error')
    }
  }

  const updateReason = async (card: ReplyCard, reason: string) => {
    setCards((cs) => cs.map((c) => (c === card ? { ...c, ratingReason: reason } : c)))
    try {
      await api.updateHistory(card.historyId, { rating_reason: reason })
    } catch {
      // 無視
    }
  }

  const showPreview = async () => {
    setPreviewing(true)
    try {
      setPreview(await api.previewGeneration(contactId, effectiveCondition))
    } catch (e) {
      onMessage(e instanceof Error ? e.message : '送信内容の取得に失敗しました', 'error')
    } finally {
      setPreviewing(false)
    }
  }

  const inputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      generate()
    }
  }

  return (
    <div className="border-t border-gray-200 bg-white px-4 py-3">
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <label className="shrink-0 text-xs font-semibold text-gray-500">返信の方向</label>
          <select
            value={direction}
            onChange={(e) => setDirection(e.target.value)}
            disabled={generating}
            className="w-56 rounded-xl border border-gray-200 bg-white px-3 py-1.5 text-sm outline-none focus:border-emerald-400 disabled:opacity-60"
          >
            {REPLY_DIRECTIONS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
          <span className="text-xs text-gray-400">足りない指示は下の自由記述へ</span>
        </div>
        <div className="flex items-end gap-2">
          <div className="min-w-0 flex-1">
            <label className="mb-1 block text-xs font-semibold text-gray-500">
              追加の自由記述（空欄でもOK）
            </label>
            <input
              value={condition}
              onChange={(e) => setCondition(e.target.value)}
              onKeyDown={inputKeyDown}
              placeholder="例: 映画の話に持っていきたい / 質問しないで / 少しだけ距離を縮めたい"
              disabled={generating}
              className="w-full rounded-xl border border-gray-200 bg-gray-50 px-3.5 py-2 text-sm outline-none placeholder:text-gray-400 focus:border-emerald-400 focus:bg-white disabled:opacity-60"
            />
          </div>
          <button
            onClick={showPreview}
            disabled={generating || previewing}
            className="shrink-0 rounded-full border border-gray-300 px-3 py-2 text-xs text-gray-500 hover:bg-gray-50 disabled:opacity-50"
            title="今回AIへ渡される内容を確認する"
          >
            {previewing ? '確認中...' : '👁 送信内容'}
          </button>
          <button
            onClick={() => generate({ mode: 'followup' })}
            disabled={generating}
            className={`shrink-0 rounded-full px-4 py-2 text-sm font-bold text-white shadow transition-colors disabled:cursor-not-allowed ${
              generating ? 'bg-gray-400' : 'bg-amber-500 hover:bg-amber-600'
            }`}
            title="返信が途絶えている相手への自然な追いメッセージを3案生成します"
          >
            💬 追いメッセージ
          </button>
          <button
            onClick={() => generate()}
            disabled={generating}
            className={`shrink-0 rounded-full px-5 py-2 text-sm font-bold text-white shadow transition-colors disabled:cursor-not-allowed ${
              generating ? 'bg-gray-400' : 'bg-emerald-500 hover:bg-emerald-600'
            }`}
            title="3つの異なる戦略で返信を生成します（Ctrl+Enter）"
          >
            {generating ? (
              <span className="flex items-center gap-2">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/50 border-t-white" />
                3案生成中...
              </span>
            ) : (
              '3案を生成'
            )}
          </button>
          {generating && (
            <button
              onClick={cancelGenerate}
              className="shrink-0 rounded-full border border-gray-300 px-3 py-2 text-xs text-gray-500 hover:bg-gray-50"
              title="Esc でもキャンセルできます"
            >
              キャンセル
            </button>
          )}
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
            {error}
          </div>
        )}

        {aiQuestion && (
          <div className="rounded-2xl border border-amber-200 bg-amber-50 p-3">
            <div className="mb-2 text-sm font-semibold text-amber-700">AIが必要な情報を質問しています</div>
            <p className="mb-2 text-sm text-gray-700">{aiQuestion}</p>
            <div className="flex items-end gap-2">
              <input
                value={questionAnswer}
                onChange={(e) => setQuestionAnswer(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) submitQuestionAnswer()
                }}
                placeholder="回答を入力..."
                autoFocus
                className="min-w-0 flex-1 rounded-xl border border-gray-200 bg-white px-3.5 py-2 text-sm outline-none placeholder:text-gray-400 focus:border-amber-400"
              />
              <button
                onClick={submitQuestionAnswer}
                disabled={!questionAnswer.trim() || savingAnswer}
                className="shrink-0 rounded-full bg-amber-500 px-4 py-2 text-sm font-bold text-white hover:bg-amber-600 disabled:opacity-40"
              >
                {savingAnswer ? '保存中...' : '回答して再生成'}
              </button>
            </div>
            <div className="mt-1 text-[11px] text-gray-400">回答は保存され、今後の返信生成に活用されます</div>
          </div>
        )}

        {cards.length > 0 && (
          <div className="flex flex-col gap-2.5">
            <div className="flex items-center justify-between px-1">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-gray-500">
                  返信候補（3種類の異なるアプローチ・同等の文章量）
                </span>
                {genMeta && (
                  <span className="rounded bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 border border-emerald-200">
                    {genMeta.buildVersion || 'v3.1'}
                    {genMeta.effectiveTone && (
                      <span className="ml-1 font-bold">
                        | 適用: {genMeta.effectiveTone === 'tame' ? 'タメ口' : genMeta.effectiveTone === 'keigo' ? '敬語' : '混合'}
                      </span>
                    )}
                  </span>
                )}
              </div>
              <button
                onClick={() => generate()}
                disabled={generating}
                className="text-xs text-emerald-600 hover:text-emerald-700 hover:underline disabled:opacity-50"
                title="前回の言い換えを避け、別の返信戦略で3案を再生成"
              >
                🔄 別の切り口で再生成
              </button>
            </div>

            {cards.map((card, i) => (
              <div
                key={`${card.historyId}-${i}`}
                className={`rounded-2xl border p-3.5 ${
                  card.isAdopted
                    ? 'border-emerald-300 bg-emerald-50'
                    : 'border-gray-200 bg-gray-50'
                }`}
              >
                <div className="mb-1 flex items-center justify-between">
                  <span className="rounded bg-gray-200/80 px-2 py-0.5 text-[11px] font-bold text-gray-700">
                    案 {i + 1}
                  </span>
                  {card.revisionNote && (
                    <span className="text-[11px] text-gray-400">{card.revisionNote}</span>
                  )}
                </div>

                <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-gray-800">
                  {card.text}
                </p>

                <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 border-t border-gray-200/60 pt-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {card.isAdopted && (
                      <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-700">
                        ✓ 送信済み
                      </span>
                    )}
                    <button
                      onClick={() => copy(card)}
                      className="rounded-full border border-gray-300 bg-white px-3 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-100"
                    >
                      コピー
                    </button>
                    <button
                      onClick={() => sendReply(card)}
                      className="rounded-full bg-sky-500 px-3 py-1 text-xs font-semibold text-white hover:bg-sky-600"
                    >
                      送信（履歴に追加）
                    </button>
                    <button
                      onClick={() => setRevising(revising === i ? null : i)}
                      className="rounded-full border border-gray-300 bg-white px-3 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-100"
                    >
                      修正して再生成
                    </button>
                  </div>

                  {/* 評価ボタン (👍 / 😐 / 👎) */}
                  <div className="flex items-center gap-1">
                    <span className="text-[11px] text-gray-400">評価:</span>
                    <button
                      onClick={() => rateReply(card, 'good')}
                      className={`rounded-lg px-2 py-1 text-xs font-bold transition-colors ${
                        card.rating === 'good'
                          ? 'bg-emerald-500 text-white shadow-sm'
                          : 'bg-white text-gray-600 border border-gray-200 hover:bg-emerald-50 hover:text-emerald-700'
                      }`}
                      title="高評価（好みの傾向として学習）"
                    >
                      👍 良い
                    </button>
                    <button
                      onClick={() => rateReply(card, 'neutral')}
                      className={`rounded-lg px-2 py-1 text-xs font-bold transition-colors ${
                        card.rating === 'neutral'
                          ? 'bg-amber-400 text-white shadow-sm'
                          : 'bg-white text-gray-600 border border-gray-200 hover:bg-amber-50 hover:text-amber-700'
                      }`}
                      title="普通（中立）"
                    >
                      😐 普通
                    </button>
                    <button
                      onClick={() => rateReply(card, 'bad')}
                      className={`rounded-lg px-2 py-1 text-xs font-bold transition-colors ${
                        card.rating === 'bad'
                          ? 'bg-rose-500 text-white shadow-sm'
                          : 'bg-white text-gray-600 border border-gray-200 hover:bg-rose-50 hover:text-rose-700'
                      }`}
                      title="低評価（避ける傾向として学習）"
                    >
                      👎 微妙
                    </button>
                  </div>
                </div>

                {/* 評価理由（任意入力タグ/自由記述） */}
                {card.showFeedback && (
                  <div className="mt-2.5 rounded-xl border border-gray-200 bg-white p-2.5">
                    <div className="mb-1.5 flex items-center justify-between text-[11px] text-gray-500">
                      <span>評価理由（任意・クリックで保存）:</span>
                      <button
                        onClick={() =>
                          setCards((cs) =>
                            cs.map((c) => (c === card ? { ...c, showFeedback: false } : c))
                          )
                        }
                        className="text-gray-400 hover:text-gray-600"
                      >
                        ✕ 閉じる
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {(card.rating === 'good' ? GOOD_TAGS : BAD_TAGS).map((tag) => (
                        <button
                          key={tag}
                          onClick={() => updateReason(card, tag)}
                          className={`rounded-md px-2 py-0.5 text-xs transition-colors ${
                            card.ratingReason === tag
                              ? 'bg-blue-600 text-white font-medium'
                              : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                          }`}
                        >
                          {tag}
                        </button>
                      ))}
                    </div>
                    <div className="mt-1.5 flex items-center gap-1.5">
                      <input
                        value={card.ratingReason || ''}
                        onChange={(e) => updateReason(card, e.target.value)}
                        placeholder="自由入力（例: 〜の言い回しが自然、少し堅い）"
                        className="min-w-0 flex-1 rounded border border-gray-200 px-2 py-1 text-xs outline-none focus:border-blue-400"
                      />
                    </div>
                  </div>
                )}

                {revising === i && (
                  <div className="mt-2 flex w-full items-center gap-2 pt-1 border-t border-gray-200/60">
                    <input
                      value={revisionText}
                      onChange={(e) => setRevisionText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) submitRevision(card)
                      }}
                      placeholder="例: もっと短く / 映画の話だけにして / 質問しないで"
                      autoFocus
                      className="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-emerald-400"
                    />
                    <button
                      onClick={() => submitRevision(card)}
                      disabled={!revisionText.trim() || generating}
                      className="shrink-0 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-600 disabled:opacity-40"
                    >
                      再生成
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {preview && <PromptPreviewModal preview={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
