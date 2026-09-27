export interface ReplyDirection {
  value: string
  label: string
}

/** 返信の方向プリセット（プルダウン選択用） */
export const REPLY_DIRECTIONS: ReplyDirection[] = [
  { value: '', label: '方向を指定しない' },
  { value: '追いメッセージ（返信が途絶えた相手への自然な再開・話題提供）', label: '追いメッセージ・再開' },
  { value: 'かっこよく、余裕のある大人な印象の返信', label: 'かっこよく・余裕ある' },
  { value: '明るく元気で親しみやすい返信', label: '明るく元気' },
  { value: '甘めで好意を感じさせる返信', label: '甘め・好意アピール' },
  { value: 'クールで落ち着いた返信', label: 'クール・控えめ' },
  { value: '軽いノリで笑いを取る返信', label: '軽いノリ・笑い' },
  { value: '真面目で誠実な返信', label: '真面目・誠実' },
  { value: '相手に質問を投げかける返信', label: '質問する' },
  { value: '質問を中心に会話を広げる返信', label: '質問多めで広げる' },
  { value: '短めでテンポよく返す返信', label: '短め・テンポ良く' },
  { value: '丁寧で少し長めの返信', label: '丁寧・長め' },
]

/** 方向プリセットと自由記述を1つの生成条件にまとめる */
export function buildCondition(direction: string, freeText: string): string {
  const parts: string[] = []
  if (direction) parts.push(`返信の方向: ${direction}`)
  const text = freeText.trim()
  if (text) parts.push(text)
  return parts.join(' / ')
}
