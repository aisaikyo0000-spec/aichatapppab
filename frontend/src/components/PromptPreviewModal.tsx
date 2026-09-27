import type { GenerationPreview } from '../types'
import Modal from './Modal'

interface Props {
  preview: GenerationPreview
  onClose: () => void
}

function joinText(lines: string[]): string {
  if (!lines || lines.length === 0) return '（なし）'
  return lines.join('\n')
}

function formatProfile(p: GenerationPreview['self_profile']): string {
  const entries: [string, string][] = [
    ['名前', p.name],
    ['年齢', p.age],
    ['性別', p.gender],
    ['職業', p.occupation],
    ['趣味', p.hobbies],
    ['性格', p.personality],
    ['話し方・文体', p.speaking_style],
    ['その他', p.profile],
  ]
  const lines = entries.filter(([, v]) => v.trim()).map(([k, v]) => `${k}: ${v}`)
  return lines.join('\n') || '（未設定）'
}

function Section({ title, body }: { title: string; body: string }) {
  return (
    <div>
      <h4 className="mb-1 text-xs font-bold text-gray-500">{title}</h4>
      <pre className="max-h-44 overflow-y-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-2.5 text-[11px] leading-relaxed text-gray-700">
        {body}
      </pre>
    </div>
  )
}

export default function PromptPreviewModal({ preview, onClose }: Props) {
  return (
    <Modal title="AIへ渡される内容" onClose={onClose} wide>
      <div className="flex flex-col gap-3">
        <p className="text-xs text-gray-500">
          Provider: <span className="font-semibold text-gray-700">{preview.provider}</span> / Model:{' '}
          <span className="font-semibold text-gray-700">{preview.model}</span>
        </p>
        <Section title="【CURRENT REQUEST】今回の返信条件" body={preview.condition || '（指定なし）'} />
        <Section
          title="【SELF】あなたのプロフィール（AIが演じる人物）"
          body={formatProfile(preview.self_profile)}
        />
        <Section title="【RULES】絶対ルール" body={joinText(preview.rules)} />
        <Section title="【REFERENCES】参考資料" body={joinText(preview.references)} />
        <Section title="【LEARNING MATERIALS】学習用テキスト" body={joinText(preview.learning_materials)} />
        <Section title="【TRAINING EXAMPLES】過去の良い返信例" body={joinText(preview.training_examples)} />
        <Section
          title="【CONTACT】相手の情報"
          body={[
            `名前: ${preview.contact.name}`,
            preview.contact.profile ? `プロフィール: ${preview.contact.profile}` : '',
            preview.contact.images.length > 0
              ? `プロフィール画像の説明: ${preview.contact.images.join(' / ')}`
              : '',
          ]
            .filter(Boolean)
            .join('\n') || '（なし）'}
        />
        <Section title="【CHAT HISTORY】会話履歴（直近のみ）" body={preview.chat_history} />
        <p className="text-[11px] text-gray-400">API Keyなどの秘密情報は含まれません。</p>
      </div>
    </Modal>
  )
}
