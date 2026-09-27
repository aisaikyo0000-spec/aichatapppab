"""ユーザー自身（AIが演じる主人公）のプロフィールAPI。

AI返信生成時に【SELF】として渡され、AIがこの人物になりきって返信を作成する。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import database
from ..schemas import UserKnowledgeCreate, UserProfileUpdate

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
def get_profile():
    return database.get_user_profile()


@router.put("")
def update_profile(body: UserProfileUpdate):
    data = body.model_dump(exclude_none=True)
    return database.save_user_profile(data)


@router.get("/knowledge")
def list_knowledge():
    return database.get_user_knowledge()


@router.post("/knowledge")
def save_knowledge(body: UserKnowledgeCreate):
    return database.save_user_knowledge(body.question, body.answer)


@router.delete("/knowledge/{knowledge_id}")
def delete_knowledge(knowledge_id: int):
    database.delete_user_knowledge(knowledge_id)
    return {"ok": True}
