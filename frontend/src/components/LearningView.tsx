import { useState } from 'react'
import type { Contact } from '../types'
import PracticePanel from './PracticePanel'
import EvaluatePanel from './EvaluatePanel'
import MaterialsPanel from './MaterialsPanel'

interface Props {
  contacts: Contact[]
  onToast: (message: string, kind: 'error' | 'info') => void
  /** 狭い画面で一覧へ戻る */
  onBack?: () => void
}

type Tab = 'practice' | 'evaluate' | 'materials'

const TABS: { key: Tab; label: string }[] = [
  { key: 'practice', label: 'AI練習' },
  { key: 'evaluate', label: '返信評価' },
  { key: 'materials', label: '学習資料' },
]

export default function LearningView({ contacts, onToast, onBack }: Props) {
  const [tab, setTab] = useState<Tab>('practice')

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col bg-gray-100">
      <div className="flex shrink-0 items-center gap-1 border-b border-gray-200 bg-white px-4">
        {onBack && (
          <button
            onClick={onBack}
            className="mr-1 shrink-0 rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
            title="相手一覧に戻る"
          >
            ← 一覧
          </button>
        )}
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 px-4 py-2.5 text-sm font-semibold transition-colors ${
              tab === t.key
                ? 'border-emerald-500 text-emerald-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1">
        {tab === 'practice' && <PracticePanel contacts={contacts} onToast={onToast} />}
        {tab === 'evaluate' && <EvaluatePanel contacts={contacts} onToast={onToast} />}
        {tab === 'materials' && <MaterialsPanel onToast={onToast} />}
      </div>
    </div>
  )
}
