import { useEffect, useState } from 'react'
import { api } from '../api'

interface Props {
  onClose: () => void
  onToast: (message: string, kind: 'error' | 'info') => void
}

export default function MyInfoModal({ onClose, onToast }: Props) {
  const [info, setInfo] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.getProfile().then((p) => setInfo(p.my_info || '')).catch(() => {})
  }, [])

  const save = async () => {
    setSaving(true)
    try {
      await api.updateProfile({ my_info: info })
      onToast('自分の情報を保存しました', 'info')
      onClose()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '保存に失敗しました', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="mx-4 flex w-full max-w-lg flex-col rounded-2xl bg-white shadow-2xl" style={{ maxHeight: '85vh' }}>
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <h2 className="text-base font-bold text-gray-800">📋 自分の情報</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg">✕</button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          <p className="text-xs text-gray-500">
            返信生成時にAIが参照する「自分」の情報です。好み・制限・条件などを自由に書いてください。
            <br />例: お酒は飲まない、魚アレルギー、最近引っ越しした、ITエンジニア、休日はカフェ巡りが好き
          </p>
          <textarea
            value={info}
            onChange={(e) => setInfo(e.target.value)}
            placeholder={"お酒は飲まない\n魚アレルギーがある\n休日はカフェ巡りが好き\n最近引っ越しした"}
            rows={10}
            className="w-full rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm outline-none placeholder:text-gray-400 focus:border-emerald-400 resize-none"
          />
        </div>
        <div className="border-t border-gray-200 px-5 py-3">
          <button
            onClick={save}
            disabled={saving}
            className="w-full rounded-full bg-emerald-500 py-2.5 text-sm font-semibold text-white hover:bg-emerald-600 disabled:opacity-40"
          >
            {saving ? '保存中...' : '💾 保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
