import Modal from './Modal'

interface Props {
  title: string
  message: string
  confirmLabel?: string
  onConfirm: () => void
  onClose: () => void
}

export default function ConfirmDialog({
  title,
  message,
  confirmLabel = '削除する',
  onConfirm,
  onClose,
}: Props) {
  return (
    <Modal title={title} onClose={onClose}>
      <p className="whitespace-pre-wrap text-sm text-gray-700">{message}</p>
      <div className="mt-5 flex justify-end gap-2">
        <button
          onClick={onClose}
          className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
        >
          キャンセル
        </button>
        <button
          onClick={() => {
            onConfirm()
            onClose()
          }}
          className="rounded-lg bg-red-500 px-4 py-2 text-sm font-semibold text-white hover:bg-red-600"
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  )
}
