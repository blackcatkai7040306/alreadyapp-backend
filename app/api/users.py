from fastapi import APIRouter, HTTPException

from pydantic import BaseModel, Field

from app.core.supabase_client import get_supabase

router = APIRouter(prefix="/users", tags=["users"])


class UserUpdateRequest(BaseModel):
    """Body for updating user settings. Only provided fields are updated."""

    speed: str | None = Field(None, description="Speed (text)")
    is_morning_reminder: bool | None = Field(None, description="Morning reminder on/off")
    is_bedtime_reminder: bool | None = Field(None, description="Bedtime reminder on/off")


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdateRequest):
    """
    Update Users table: Speed, Morning_Reminder, Bedtime_Reminder.
    Only fields present in the body are updated.
    """
    payload = {}
    if body.speed is not None:
        payload["speed"] = body.speed
    if body.is_morning_reminder is not None:
        payload["morning_Reminder"] = body.is_morning_reminder
    if body.is_bedtime_reminder is not None:
        payload["bedtime_Reminder"] = body.is_bedtime_reminder
    if body.name is not None:
        payload["name"] = body.username
    if body.email is not None:
        payload["email"] = body.email
    if body.password is not None:
        payload["password"] = body.password
    if not payload:
        raise HTTPException(status_code=400, detail="Provide at least one field: speed, is_morning_reminder, is_bedtime_reminder")

    supabase = get_supabase()
    try:
        r = supabase.table("Users").update(payload).eq("id", user_id).execute()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Supabase error: {e}")

    return {"updated": True, "data": r.data}
