export function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  const hm = d.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' })
  if (sameDay) return hm
  const md = d.toLocaleDateString('ja-JP', { month: 'numeric', day: 'numeric' })
  return `${md} ${hm}`
}

/** 日付（例: 2024年5月12日）を返す */
export function formatDateJapanese(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
}

/** 指定した日時から今日までの経過日数（日付単位）を返す */
export function daysSince(iso: string): number | null {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const now = new Date()
  const targetDay = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  return Math.max(0, Math.floor((today - targetDay) / (1000 * 60 * 60 * 24)))
}

/** 短い日付（例: 2024/05/12）を返す */
export function formatDateShort(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}/${m}/${day}`
}

export function avatarColor(name: string): string {
  const palette = [
    'bg-rose-400',
    'bg-orange-400',
    'bg-amber-400',
    'bg-emerald-400',
    'bg-teal-400',
    'bg-sky-400',
    'bg-indigo-400',
    'bg-violet-400',
    'bg-fuchsia-400',
  ]
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return palette[h % palette.length]
}
