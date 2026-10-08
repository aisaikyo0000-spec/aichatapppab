export interface Contact {
  id: number
  name: string
  profile: string
  is_pinned: boolean
  is_archived: boolean
  created_at: string
  updated_at: string
  last_message?: string
  last_message_at?: string | null
  /** この人から最初に返信が来た日時 */
  first_contact_message_at?: string | null
  /** 相手から受信したメッセージの累計通数 */
  contact_message_count?: number
  /** 先頭の登録画像。未登録なら null */
  profile_image_url?: string | null
}

export interface Message {
  id: number
  contact_id: number
  sender: 'contact' | 'self'
  content: string
  source?: 'manual' | 'generated' | 'imported' | 'legacy_unknown'
  generation_history_id?: number | null
  created_at: string
  updated_at: string
}

export interface ModelInfo {
  name: string
  tier?: string
}

export interface ProviderInfo {
  name: string
  models: string[]
  model_infos?: ModelInfo[]
}

export interface Settings {
  provider: string
  model: string
  has_api_key: boolean
  api_key_env: boolean
  temperature: number
  max_tokens: number
  history_limit: number
  fallback_provider: string
  fallback_model: string
  has_fallback_api_key: boolean
  fallback_api_key_env: boolean
  providers: ProviderInfo[]
}

export interface GenerationResult {
  replies: string[]
  history_ids: number[]
  question?: string
  batch_id?: number | null
  build_version?: string
  prompt_version?: string
  requested_tone?: string
  effective_tone?: string
  tone_validation?: string
  style_scores?: number[]
  strategy?: TappleStrategy
}

export interface TappleStrategy {
  action: 'continue' | 'clarify' | 'invite' | 'wait' | 'stop'
  rationale: string
  evidence: string[]
  invite_example?: string | null
}

export type Sendability = 'sendable' | 'minor_edit' | 'major_edit' | 'rejected'

export interface EvaluationItem {
  id: number
  generation_batch_id: number | null
  history_id: number
  candidate_index: number
  counterpart_intent: string
  naturalness_score: number | null
  style_score: number | null
  final_score: number | null
  generated_text: string
  counterpart_message: string
  contact_id: number | null
  human_rating: string | null
  human_feedback: string
  feedback_tags: string[]
  sendability: Sendability | null
  created_at: string
  updated_at: string
}

export interface UserProfile {
  name: string
  gender: string
  age: string
  occupation: string
  hobbies: string
  personality: string
  speaking_style: string
  profile: string
  my_info: string
  updated_at: string
}

export interface GenerationPreview {
  provider: string
  model: string
  rules: string[]
  references: string[]
  learning_materials: string[]
  training_examples: string[]
  self_profile: UserProfile
  contact: {
    name: string
    profile: string
    images: string[]
  }
  chat_history: string
  condition: string
  learned_preferences?: string
}

export interface HistoryItem {
  id: number
  contact_id: number | null
  provider: string
  model: string
  current_condition: string
  generated_text: string
  revision_instruction: string
  revised_text: string
  is_adopted: boolean
  is_copied: boolean
  is_sent: boolean
  rating?: string | null
  rating_reason?: string | null
  tone?: string
  counterpart_message?: string
  created_at: string
}


export type KnowledgeType = 'rules' | 'references' | 'training'

export interface KnowledgeFile {
  id: number
  type: KnowledgeType
  file_name: string
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface BackupInfo {
  file_name: string
  size: number
  created_at: string
}

export interface ContactImage {
  id: number
  contact_id: number
  description: string
  sort_order: number
  created_at: string
  url: string
}

export interface ContactAiSettings {
  provider: string | null
  model: string | null
  temperature: number | null
  max_tokens: number | null
  history_limit: number | null
  knowledge_file_ids: number[] | null
}

export interface ChatMessage {
  sender: 'contact' | 'self'
  content: string
}

export interface TrainingSessionSummary {
  id: number
  contact_id: number | null
  persona_name: string
  last_message: string
  created_at: string
  updated_at: string
}

export interface TrainingSession {
  id: number
  contact_id: number | null
  persona_name: string
  persona_profile: string
  persona_profile_display: string
  persona_gender?: string
  persona_age?: string
  persona_hobbies?: string
  persona_personality?: string
  persona_style?: string
  messages: ChatMessage[]
  created_at: string
  updated_at: string
}

export interface EvaluationItem {
  key: string
  label: string
  score: number
  comment: string
}

export interface EvaluationResult {
  items: EvaluationItem[]
  overall: EvaluationItem
}

export interface UserKnowledge {
  id: number
  question: string
  answer: string
  created_at: string
}

export interface TrainingExample {
  id: number
  conversation: ChatMessage[]
  ai_response: string
  user_feedback: string
  corrected_response: string
  rating: number | null
  created_at: string
}
