import { useEffect, useState } from 'react'
import { api } from '../api'
import type { UserProfile } from '../types'

interface Props {
  onClose: () => void
  onToast: (message: string, kind: 'error' | 'info') => void
}

const EMPTY: UserProfile = {
  name: '',
  gender: '',
  age: '',
  occupation: '',
  hobbies: '',
  personality: '',
  speaking_style: '',
  profile: '',
  my_info: '',
  updated_at: '',
}

const FIELDS: { key: keyof UserProfile; label: string; placeholder: string; textarea?: boolean }[] = [
  { key: 'name', label: '名前（ニックネーム）', placeholder: '例: ゆうた' },
  { key: 'gender', label: '性別', placeholder: '例: 男性（任意）' },
  { key: 'age', label: '年齢', placeholder: '例: 20代（任意）' },
  { key: 'occupation', label: '職業', placeholder: '例: ITエンジニア（任意）' },
  { key: 'hobbies', label: '趣味', placeholder: '例: 映画・ラーメン屋巡り・ランニング', textarea: true },
  { key: 'personality', label: '性格', placeholder: '例: 明るく穏やか、人見知りしない', textarea: true },
  {
    key: 'speaking_style',
    label: '話し方・文体',
    placeholder: '例: 丁寧語、絵文字は控えめ、短文',
    textarea: true,
  },
  {
    key: 'profile',
    label: '自由記述（その他伝えたいこと）',
    placeholder: '例: 休日はのんびり派。デートはカフェが好き',
    textarea: true,
  },
]

export default function ProfilePanel({ onClose, onToast }: Props) {
  const [form, setForm] = useState<UserProfile>(EMPTY)
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api
      .getProfile()
      .then((p) => {
        setForm(p)
        setLoaded(true)
      })
      .catch((e) => onToast(e instanceof Error ? e.message : 'プロフィールの取得に失敗しました', 'error'))
  }, [onToast])

  const setField = (key: keyof UserProfile) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  const save = async () => {
    setSaving(true)
    try {
      await api.updateProfile({
        name: form.name,
        gender: form.gender,
        age: form.age,
        occupation: form.occupation,
        hobbies: form.hobbies,
        personality: form.personality,
        speaking_style: form.speaking_style,
        profile: form.profile,
      })
      onToast('プロフィールを保存しました', 'info')
      onClose()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '保存に失敗しました', 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!loaded) {
    return <div className="py-6 text-center text-sm text-gray-400">読み込み中...</div>
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="rounded-xl bg-emerald-50 p-3 text-xs leading-relaxed text-emerald-800">
        ここで設定したプロフィールは、<b>AIがあなたになりきって返信を作成するとき</b>に【SELF】として
        渡されます。名前・年齢・職業・趣味・話し方などを入力すると、あなたらしい返信になります。
        未入力の項目は省略されます。
      </p>

      <div className="grid grid-cols-3 gap-3">
        {FIELDS.filter((f) => !f.textarea).map((f) => (
          <div key={f.key}>
            <label className="mb-1 block text-sm font-semibold text-gray-600">{f.label}</label>
            <input
              value={form[f.key]}
              onChange={setField(f.key)}
              placeholder={f.placeholder}
              className="w-full rounded-xl border border-gray-200 px-3.5 py-2 text-sm outline-none focus:border-emerald-400"
            />
          </div>
        ))}
      </div>

      {FIELDS.filter((f) => f.textarea).map((f) => (
        <div key={f.key}>
          <label className="mb-1 block text-sm font-semibold text-gray-600">{f.label}</label>
          <textarea
            value={form[f.key]}
            onChange={setField(f.key)}
            placeholder={f.placeholder}
            rows={f.key === 'profile' ? 3 : 2}
            className="w-full resize-none rounded-xl border border-gray-200 px-3.5 py-2 text-sm outline-none focus:border-emerald-400"
          />
        </div>
      ))}

      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={onClose}
          className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
        >
          キャンセル
        </button>
        <button
          onClick={save}
          disabled={saving}
          className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          {saving ? '保存中...' : '保存'}
        </button>
      </div>
    </div>
  )
}
