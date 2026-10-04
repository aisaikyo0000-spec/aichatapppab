import type {
  BackupInfo,
  ChatMessage,
  Contact,
  ContactAiSettings,
  ContactImage,
  EvaluationResult,
  GenerationPreview,
  GenerationResult,
  HistoryItem,
  KnowledgeFile,
  KnowledgeType,
  Message,
  Settings,
  TrainingExample,
  UserKnowledge,
  UserProfile,
  TrainingSession,
  TrainingSessionSummary,
} from './types'

const API_BASE = '/api'

export class ApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const headers = {
    ...(options.headers as Record<string, string> | undefined),
  }
  // FormDataはブラウザにContent-Type(multipart boundary)を自動設定させる
  if (!(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(API_BASE + path, {
    ...options,
    headers,
    signal,
  })
  if (!res.ok) {
    let detail: unknown = null
    try {
      detail = await res.json()
    } catch {
      // ignore
    }
    const d = detail as { detail?: { message?: string; code?: string } | string } | null
    const inner = d?.detail
    const message =
      typeof inner === 'object' && inner
        ? inner.message || `エラーが発生しました (HTTP ${res.status})`
        : typeof inner === 'string'
          ? inner
          : `エラーが発生しました (HTTP ${res.status})`
    const code = typeof inner === 'object' && inner ? inner.code : undefined
    throw new ApiError(message, res.status, code)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  listContacts: (search = '') =>
    request<Contact[]>(`/contacts?search=${encodeURIComponent(search)}`),

  createContact: (name: string, profile: string) =>
    request<Contact>('/contacts', {
      method: 'POST',
      body: JSON.stringify({ name, profile }),
    }),

  updateContact: (
    id: number,
    patch: Partial<Pick<Contact, 'name' | 'profile' | 'is_pinned' | 'is_archived'>>,
  ) => request<Contact>(`/contacts/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),

  deleteContact: (id: number) =>
    request<void>(`/contacts/${id}`, { method: 'DELETE' }),

  listMessages: (contactId: number) =>
    request<Message[]>(`/contacts/${contactId}/messages`),

  createMessage: (
    contactId: number,
    sender: 'contact' | 'self',
    content: string,
    source: 'manual' | 'generated' | 'imported' | 'legacy_unknown' = 'manual',
    generationHistoryId?: number | null,
  ) =>
    request<Message>(`/contacts/${contactId}/messages`, {
      method: 'POST',
      body: JSON.stringify({
        sender,
        content,
        source,
        generation_history_id: generationHistoryId ?? null,
      }),
    }),

  updateMessage: (id: number, content: string) =>
    request<Message>(`/messages/${id}`, { method: 'PATCH', body: JSON.stringify({ content }) }),

  deleteMessage: (id: number) =>
    request<void>(`/messages/${id}`, { method: 'DELETE' }),

  generate: (
    payload: {
      contact_id: number
      condition: string
      candidates: number
      revision_instruction?: string
      original_generated?: string
      tone?: string
      mode?: 'normal' | 'followup'
    },
    signal?: AbortSignal,
  ) =>
    request<GenerationResult>('/generate', { method: 'POST', body: JSON.stringify(payload) }, signal),

  previewGeneration: (contactId: number, condition: string, mode?: 'normal' | 'followup') =>
    request<GenerationPreview>('/generate/preview', {
      method: 'POST',
      body: JSON.stringify({ contact_id: contactId, condition, mode: mode ?? 'normal' }),
    }),

  updateHistory: (
    id: number,
    patch: Partial<{
      is_adopted: boolean
      is_copied: boolean
      is_sent: boolean
      rating: string
      rating_reason: string
    }>,
  ) => request<HistoryItem>(`/history/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),

  saveEvaluation: (
    historyId: number,
    patch: {
      rating?: string | null
      feedback?: string
      feedback_tags?: string[]
      sendability?: import('./types').Sendability | null
    },
  ) =>
    request<import('./types').EvaluationItem>('/evaluations', {
      method: 'POST',
      body: JSON.stringify({ history_id: historyId, ...patch }),
    }),

  listEvaluations: (params?: { batch_id?: number; rating?: string; sendability?: string }) => {
    const q = new URLSearchParams()
    if (params?.batch_id !== undefined) q.set('batch_id', String(params.batch_id))
    if (params?.rating) q.set('rating', params.rating)
    if (params?.sendability) q.set('sendability', params.sendability)
    const suffix = q.toString() ? `?${q.toString()}` : ''
    return request<import('./types').EvaluationItem[]>(`/evaluations${suffix}`)
  },


  listHistory: (contactId: number) =>
    request<HistoryItem[]>(`/history?contact_id=${contactId}`),

  listKnowledge: (type?: KnowledgeType) =>
    request<KnowledgeFile[]>(`/knowledge${type ? `?type=${type}` : ''}`),

  createKnowledge: (type: KnowledgeType, file_name: string, content: string) =>
    request<KnowledgeFile>('/knowledge', {
      method: 'POST',
      body: JSON.stringify({ type, file_name, content }),
    }),

  reloadKnowledge: () =>
    request<{
      counts: Record<KnowledgeType, { added: number; removed: number }>
      added: number
      removed: number
    }>('/knowledge/reload', { method: 'POST' }),

  getKnowledge: (id: number) =>
    request<KnowledgeFile & { content: string }>(`/knowledge/${id}`),

  updateKnowledge: (
    id: number,
    patch: { enabled?: boolean; file_name?: string; content?: string },
  ) =>
    request<KnowledgeFile>(`/knowledge/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  deleteKnowledge: (id: number) =>
    request<void>(`/knowledge/${id}`, { method: 'DELETE' }),

  createBackup: () =>
    request<BackupInfo>('/backup', { method: 'POST' }),

  listBackups: () => request<BackupInfo[]>('/backup'),

  restoreBackup: (file_name: string) =>
    request<{ restored: string; files: number }>('/backup/restore', {
      method: 'POST',
      body: JSON.stringify({ file_name }),
    }),

  listContactImages: (contactId: number) =>
    request<ContactImage[]>(`/contacts/${contactId}/images`),

  uploadContactImage: (contactId: number, file: File, description: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('description', description)
    return request<ContactImage>(`/contacts/${contactId}/images`, {
      method: 'POST',
      body: form,
    })
  },

  updateContactImage: (id: number, description: string) =>
    request<ContactImage>(`/images/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ description }),
    }),

  deleteContactImage: (id: number) =>
    request<void>(`/images/${id}`, { method: 'DELETE' }),

  getContactAiSettings: (contactId: number) =>
    request<ContactAiSettings>(`/contacts/${contactId}/ai-settings`),

  updateContactAiSettings: (contactId: number, payload: ContactAiSettings) =>
    request<ContactAiSettings>(`/contacts/${contactId}/ai-settings`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  listTrainingSessions: () => request<TrainingSessionSummary[]>('/training/sessions'),

  createTrainingSession: (
    contactId: number | null,
    personaName: string,
    profile?: {
      persona_gender?: string
      persona_age?: string
      persona_hobbies?: string
      persona_personality?: string
      persona_style?: string
      persona_profile?: string
    },
  ) =>
    request<TrainingSession>('/training/sessions', {
      method: 'POST',
      body: JSON.stringify({ contact_id: contactId, persona_name: personaName, ...profile }),
    }),

  updateTrainingSession: (
    id: number,
    patch: {
      persona_name?: string
      persona_gender?: string
      persona_age?: string
      persona_hobbies?: string
      persona_personality?: string
      persona_style?: string
      persona_profile?: string
    },
  ) =>
    request<TrainingSession>(`/training/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  getTrainingSession: (id: number) => request<TrainingSession>(`/training/sessions/${id}`),

  deleteTrainingSession: (id: number) =>
    request<void>(`/training/sessions/${id}`, { method: 'DELETE' }),

  addTrainingMessage: (id: number, content: string) =>
    request<{ messages: ChatMessage[] }>(`/training/sessions/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),

  trainingAiReply: (id: number, condition: string) =>
    request<{ reply: string; messages: ChatMessage[] }>(`/training/sessions/${id}/ai-reply`, {
      method: 'POST',
      body: JSON.stringify({ condition }),
    }),

  trainingMyReply: (id: number, condition: string) =>
    request<{ reply: string; question?: string }>(`/training/sessions/${id}/my-reply`, {
      method: 'POST',
      body: JSON.stringify({ condition }),
    }),

  reviseMyReply: (
    id: number,
    payload: { condition: string; revision_instruction: string; original_generated: string },
  ) =>
    request<{ reply: string }>(`/training/sessions/${id}/revise-my-reply`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listTrainingRevisions: (limit = 100) =>
    request<{
      id: number
      session_id: number | null
      original_generated: string
      revision_instruction: string
      revised_text: string
      created_at: string
    }[]>(`/training/revisions?limit=${limit}`),

  evaluateReply: (conversation: ChatMessage[], reply: string) =>
    request<EvaluationResult>('/training/evaluate', {
      method: 'POST',
      body: JSON.stringify({ conversation, reply }),
    }),

  createTrainingExample: (payload: {
    conversation: ChatMessage[]
    ai_response: string
    user_feedback: string
    corrected_response: string
    rating: number | null
  }) =>
    request<{ id: number }>('/training/examples', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listTrainingExamples: () => request<TrainingExample[]>('/training/examples'),

  deleteTrainingExample: (id: number) =>
    request<void>(`/training/examples/${id}`, { method: 'DELETE' }),

  getSettings: () => request<Settings>('/settings'),

  updateSettings: (patch: Record<string, unknown>) =>
    request<Settings>('/settings', { method: 'PUT', body: JSON.stringify(patch) }),

  getProfile: () => request<UserProfile>('/profile'),

  updateProfile: (patch: Partial<Omit<UserProfile, 'updated_at'>>) =>
    request<UserProfile>('/profile', { method: 'PUT', body: JSON.stringify(patch) }),

  exportChatUrl: (contactId: number) => `${API_BASE}/contacts/${contactId}/export`,

  listUserKnowledge: () => request<UserKnowledge[]>('/profile/knowledge'),

  saveUserKnowledge: (question: string, answer: string) =>
    request<UserKnowledge>('/profile/knowledge', {
      method: 'POST',
      body: JSON.stringify({ question, answer }),
    }),

  deleteUserKnowledge: (id: number) =>
    request<void>(`/profile/knowledge/${id}`, { method: 'DELETE' }),

  generateLikeMessage: (profileText: string, condition: string) =>
    request<{ message: string; candidates: string[] }>('/like-bot/generate', {
      method: 'POST',
      body: JSON.stringify({ profile_text: profileText, condition }),
    }),

  getHealth: () =>
    request<{
      status: string
      build_version: string
      prompt_version: string
      pid: number
      db_path: string
    }>('/health'),
}
