import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { BackupInfo } from '../types'
import ConfirmDialog from './ConfirmDialog'

interface Props {
  onToast: (message: string, kind: 'error' | 'info') => void
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export default function BackupPanel({ onToast }: Props) {
  const [backups, setBackups] = useState<BackupInfo[]>([])
  const [creating, setCreating] = useState(false)
  const [restoreTarget, setRestoreTarget] = useState<BackupInfo | null>(null)
  const [restoring, setRestoring] = useState(false)

  const load = useCallback(async () => {
    try {
      setBackups(await api.listBackups())
    } catch (e) {
      onToast(e instanceof Error ? e.message : 'バックアップ一覧の取得に失敗しました', 'error')
    }
  }, [onToast])

  useEffect(() => {
    load()
  }, [load])

  const create = async () => {
    setCreating(true)
    try {
      await api.createBackup()
      onToast('バックアップを作成しました', 'info')
      await load()
    } catch (e) {
      onToast(e instanceof Error ? e.message : 'バックアップの作成に失敗しました', 'error')
    } finally {
      setCreating(false)
    }
  }

  const restore = async (backup: BackupInfo) => {
    setRestoring(true)
    try {
      const r = await api.restoreBackup(backup.file_name)
      onToast(`${r.files}件のデータを復元しました。画面を再読み込みしてください`, 'info')
      await load()
    } catch (e) {
      onToast(e instanceof Error ? e.message : '復元に失敗しました', 'error')
    } finally {
      setRestoring(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-gray-500">
        相手・チャット・生成履歴・画像・ルール・参考資料・設定を <code>data/backups/</code>{' '}
        にZIPで保存します。復元すると現在のデータがバックアップの内容に置き換わります。
      </p>
      <button
        onClick={create}
        disabled={creating}
        className="self-start rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
      >
        {creating ? '作成中...' : 'バックアップを作成'}
      </button>

      <div className="flex flex-col gap-2">
        {backups.length === 0 && (
          <p className="text-xs text-gray-400">バックアップはまだありません</p>
        )}
        {backups.map((b) => (
          <div
            key={b.file_name}
            className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white p-2"
          >
            <span className="min-w-0 flex-1 truncate text-sm text-gray-700">{b.file_name}</span>
            <span className="shrink-0 text-xs text-gray-400">{formatSize(b.size)}</span>
            <button
              onClick={() => setRestoreTarget(b)}
              disabled={restoring}
              className="shrink-0 rounded-lg border border-gray-300 px-3 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40"
            >
              復元
            </button>
          </div>
        ))}
      </div>

      {restoreTarget && (
        <ConfirmDialog
          title="バックアップを復元"
          message={`「${restoreTarget.file_name}」の内容にデータを戻します。\n現在のデータは上書きされます。よろしいですか？`}
          confirmLabel="復元する"
          onConfirm={() => restore(restoreTarget)}
          onClose={() => setRestoreTarget(null)}
        />
      )}
    </div>
  )
}
