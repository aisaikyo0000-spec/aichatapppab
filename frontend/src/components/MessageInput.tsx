import { useRef, useState } from 'react'

interface Props {
  onSend: (sender: 'contact' | 'self', content: string) => void
  disabled?: boolean
  onToast?: (message: string, kind: 'error' | 'info') => void
}

export default function MessageInput({ onSend, disabled, onToast }: Props) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)

  const send = (sender: 'contact' | 'self') => {
    const content = text.trim()
    if (!content || disabled) return
    onSend(sender, content)
    navigator.clipboard?.writeText(content).then(
      () => onToast?.('送信しました（クリップボードにコピー済み）', 'info'),
      () => onToast?.('送信しました', 'info'),
    )
    setText('')
    if (ref.current) ref.current.style.height = 'auto'
  }

  const autoResize = () => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }

  return (
    <div className="border-t border-gray-200 bg-white px-4 py-3">
      <textarea
        ref={ref}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          autoResize()
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault()
            send('contact')
          }
        }}
        placeholder="メッセージを入力 (Ctrl+Enter で「相手として送信」)"
        rows={1}
        className="max-h-40 w-full resize-none rounded-xl border border-gray-200 bg-gray-50 px-4 py-2.5 text-[15px] outline-none placeholder:text-gray-400 focus:border-emerald-400 focus:bg-white"
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-[11px] text-gray-400">
          実際のマッチングアプリからコピーした相手のメッセージは「相手として送信」で記録します
        </span>
        <div className="flex shrink-0 gap-2">
          <button
            onClick={() => send('contact')}
            disabled={disabled || !text.trim()}
            className="rounded-full border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40"
          >
            相手として送信
          </button>
          <button
            onClick={() => send('self')}
            disabled={disabled || !text.trim()}
            className="rounded-full bg-emerald-500 px-4 py-1.5 text-sm font-semibold text-white hover:bg-emerald-600 disabled:opacity-40"
          >
            自分として送信
          </button>
        </div>
      </div>
    </div>
  )
}
