"""APIの入出力スキーマ。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    profile: str = Field(default="", max_length=2000)


class ContactUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    profile: Optional[str] = Field(default=None, max_length=2000)
    is_pinned: Optional[bool] = None
    is_archived: Optional[bool] = None


class ContactOut(BaseModel):
    id: int
    name: str
    profile: str
    is_pinned: bool
    is_archived: bool
    created_at: str
    updated_at: str
    last_message: str = ""
    last_message_at: str | None = None
    first_contact_message_at: str | None = None
    contact_message_count: int = 0
    profile_image_url: str | None = None


class MessageCreate(BaseModel):
    sender: Literal["contact", "self"]
    content: str = Field(min_length=1, max_length=10000)
    source: Literal["manual", "generated", "imported", "legacy_unknown"] = "manual"
    generation_history_id: Optional[int] = None


class MessageUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class MessageOut(BaseModel):
    id: int
    contact_id: int
    sender: str
    content: str
    source: str = "manual"
    generation_history_id: Optional[int] = None
    created_at: str
    updated_at: str


class GenerateRequest(BaseModel):
    contact_id: int
    condition: str = Field(default="", max_length=2000)
    candidates: int = Field(default=3, ge=1, le=3)
    revision_instruction: str = Field(default="", max_length=2000)
    original_generated: str = Field(default="", max_length=10000)
    tone: str = Field(default="", max_length=20)  # keigo, hybrid, tame
    mode: str = Field(default="normal", max_length=20)  # normal, followup



class HistoryUpdate(BaseModel):
    is_adopted: Optional[bool] = None
    is_copied: Optional[bool] = None
    is_sent: Optional[bool] = None
    rating: Optional[str] = Field(default=None, max_length=20)  # 'good', 'neutral', 'bad', or ''
    rating_reason: Optional[str] = Field(default=None, max_length=500)  # 任意理由



class SettingsUpdate(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    # api_keyは「空文字なら変更しない」扱い（誤ってキーを消さないため）。
    # 明示的に削除するには clear_api_key=True を送る。
    api_key: Optional[str] = None
    clear_api_key: Optional[bool] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=8192)
    history_limit: Optional[int] = Field(default=None, ge=1, le=500)
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None
    fallback_api_key: Optional[str] = None
    clear_fallback_api_key: Optional[bool] = None


class KnowledgeFileCreate(BaseModel):
    type: Literal["rules", "references", "training"]
    file_name: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)


class KnowledgeFileUpdate(BaseModel):
    enabled: Optional[bool] = None
    file_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    content: Optional[str] = None


class ContactAiSettings(BaseModel):
    """相手ごとのAI設定。Noneは「全体設定を使う」を意味する。"""

    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=8192)
    history_limit: Optional[int] = Field(default=None, ge=1, le=500)
    knowledge_file_ids: Optional[list[int]] = None


class ImageUpdate(BaseModel):
    description: str = Field(default="", max_length=500)
    sort_order: Optional[int] = None


class BackupRestore(BaseModel):
    file_name: str = Field(min_length=1, max_length=200)


class TrainingSessionCreate(BaseModel):
    contact_id: Optional[int] = None
    persona_name: Optional[str] = Field(default=None, max_length=100)
    persona_gender: Optional[str] = Field(default=None, max_length=20)
    persona_age: Optional[str] = Field(default=None, max_length=20)
    persona_hobbies: Optional[str] = Field(default=None, max_length=500)
    persona_personality: Optional[str] = Field(default=None, max_length=1000)
    persona_style: Optional[str] = Field(default=None, max_length=2000)
    persona_profile: Optional[str] = Field(default=None, max_length=2000)


class TrainingSessionUpdate(BaseModel):
    """練習セッションのペルソナ情報の部分更新。Noneのフィールドは変更しない。"""

    persona_name: Optional[str] = Field(default=None, max_length=100)
    persona_gender: Optional[str] = Field(default=None, max_length=20)
    persona_age: Optional[str] = Field(default=None, max_length=20)
    persona_hobbies: Optional[str] = Field(default=None, max_length=500)
    persona_personality: Optional[str] = Field(default=None, max_length=1000)
    persona_style: Optional[str] = Field(default=None, max_length=2000)
    persona_profile: Optional[str] = Field(default=None, max_length=2000)


class TrainingMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class TrainingReplyRequest(BaseModel):
    condition: str = Field(default="", max_length=2000)


class TrainingRevisionRequest(BaseModel):
    """生成した自分の返信への修正指示。"""

    condition: str = Field(default="", max_length=2000)
    revision_instruction: str = Field(min_length=1, max_length=2000)
    original_generated: str = Field(min_length=1, max_length=10000)


class ConversationMessage(BaseModel):
    sender: Literal["contact", "self"]
    content: str = Field(min_length=1, max_length=5000)


class TrainingEvaluateRequest(BaseModel):
    conversation: list[ConversationMessage] = Field(default_factory=list)
    reply: str = Field(min_length=1, max_length=5000)


class UserProfileOut(BaseModel):
    """AIが演じるユーザー自身のプロフィール。"""

    name: str = ""
    gender: str = ""
    age: str = ""
    occupation: str = ""
    hobbies: str = ""
    personality: str = ""
    speaking_style: str = ""
    profile: str = ""
    my_info: str = ""
    updated_at: str = ""


class UserProfileUpdate(BaseModel):
    """プロフィールの部分更新。Noneのフィールドは変更しない。"""

    name: Optional[str] = Field(default=None, max_length=100)
    gender: Optional[str] = Field(default=None, max_length=20)
    age: Optional[str] = Field(default=None, max_length=20)
    occupation: Optional[str] = Field(default=None, max_length=100)
    hobbies: Optional[str] = Field(default=None, max_length=500)
    personality: Optional[str] = Field(default=None, max_length=1000)
    speaking_style: Optional[str] = Field(default=None, max_length=2000)
    profile: Optional[str] = Field(default=None, max_length=2000)
    my_info: Optional[str] = Field(default=None, max_length=5000)


class UserKnowledgeCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=2000)


class UserKnowledgeOut(BaseModel):
    id: int
    question: str
    answer: str
    created_at: str


class TrainingExampleCreate(BaseModel):
    conversation: list[ConversationMessage] = Field(default_factory=list)
    ai_response: str = Field(default="", max_length=10000)
    user_feedback: str = Field(default="", max_length=2000)
    corrected_response: str = Field(default="", max_length=10000)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
