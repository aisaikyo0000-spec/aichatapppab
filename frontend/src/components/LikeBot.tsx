import { useState } from 'react'
import { api } from '../api'

interface Props {
  onClose: () => void
  onToast: (message: string, kind: 'error' | 'info') => void
}

export default function LikeBot({ onClose, onToast }: Props) {
  const [profileText, setProfileText] = useState('')
  const [condition, setCondition] = useState('')
  const [generating, setGenerating] = useState(false)
  const [candidates, setCandidates] = useState<string[]>([])
  const [selected, setSelected] = useState<string | null>(null)

  const generate = async () => {
    if (!profileText.trim() || generating) return
    setGenerating(true)
    setCandidates([])
    setSelected(null)
    try {
      const r = await api.generateLikeMessage(profileText.trim(), condition.trim())
      setCandidates(r.candidates)
      setSelected(r.message)
    } catch (e) {
      onToast(e instanceof Error ? e.message : '生成に失敗しました', 'error')
    } finally {
      setGenerating(false)
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="mx-4 flex w-full max-w-lg flex-col rounded-2xl bg-white shadow-2xl" style={{ maxHeight: '85vh' }}>
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <h2 className="text-base font-bold text-gray-800">👍 いいねBOT</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">相手のプロフィール文</label>
            <textarea
              value={profileText}
              onChange={(e) => setProfileText(e.target.value)}
              placeholder="相手のプロフィール文をここに貼り付け..."
              rows={6}
              className="w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm outline-none placeholder:text-gray-400 focus:border-emerald-400 resize-none"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">追加条件（任意）</label>
            <input
              value={condition}
              onChange={(e) => setCondition(e.target.value)}
              placeholder="例: カジュアルな感じで・絵文字なしで"
              className="w-full rounded-full border border-gray-200 bg-gray-50 px-4 py-2 text-sm outline-none placeholder:text-gray-400 focus:border-emerald-400"
            />
          </div>

          <button
            onClick={generate}
            disabled={!profileText.trim() || generating}
            className="w-full rounded-full bg-emerald-500 py-2.5 text-sm font-semibold text-white hover:bg-emerald-600 disabled:opacity-40"
          >
            {generating ? '生成中...' : '✨ メッセージを生成'}
          </button>

          {candidates.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-gray-500">生成されたメッセージ（クリックで選択）</p>
              {candidates.map((c, i) => (
                <div
                  key={i}
                  onClick={() => setSelected(c)}
                  className={`cursor-pointer rounded-xl border p-3 text-sm transition-colors ${
                    selected === c
                      ? 'border-emerald-400 bg-emerald-50'
                      : 'border-gray-200 bg-white hover:border-gray-300'
                  }`}
                >
                  <p className="whitespace-pre-wrap text-gray-800">{c}</p>
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        copy(c)
                      }}
                      className="rounded-full bg-gray-100 px-3 py-1 text-xs text-gray-600 hover:bg-gray-200"
                    >
                      📋 コピー
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {selected && (
          <div className="border-t border-gray-200 px-5 py-3">
            <button
              onClick={() => copy(selected)}
              className="w-full rounded-full bg-emerald-500 py-2.5 text-sm font-semibold text-white hover:bg-emerald-600"
            >
              📋 選択したメッセージをコピー
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
