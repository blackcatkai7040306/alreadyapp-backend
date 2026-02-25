import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from pydantic import BaseModel, Field

from app.api.subscription import _fetch_subscription_from_stripe
from app.core.supabase_client import get_supabase

router = APIRouter(prefix="/users", tags=["users"])


def _days_since(date_value) -> int:
    """Return days between date_value and now (UTC). If date_value is missing or invalid, return 0."""
    if date_value is None:
        return 0
    try:
        if isinstance(date_value, str):
            dt = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
        else:
            dt = date_value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0, delta.days)
    except (TypeError, ValueError):
        return 0


@router.get("/{user_id}")
async def get_user_info(user_id: int):
    """
    Get user info with synced subscription, story_count (complete), day_streak (days since signup),
    and active (number of stories not deleted). Stories table has is_deleted.
    """
    supabase = get_supabase()
    r = supabase.table("Users").select("*").eq("id", user_id).execute()
    rows = list(r.data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="User not found")
    user = dict(rows[0])
    subscription_id = user.get("stripe_subscription_id") or user.get("stripe_subscription_Id")

    if subscription_id:
        synced = _fetch_subscription_from_stripe(str(subscription_id))
        if synced:
            user["subscription_status"] = synced["status"]
            user["subscription_plan"] = synced["plan"]
            user["trial_end"] = synced["trial_end"]
            try:
                supabase.table("Users").update({
                    "subscription_status": synced["status"],
                    "subscription_plan": synced["plan"],
                    "trial_end": synced["trial_end"],
                }).eq("id", user_id).execute()
            except Exception as e:
                logging.exception("Failed to sync subscription in get_user_info: %s", e)

    sr = supabase.table("Stories").select("id, is_deleted").eq("user_id", user_id).execute()
    stories = sr.data or []
    story_count = len(stories)
    active = sum(1 for s in stories if not (s.get("is_deleted")))
    user["story_count"] = story_count
    user["complete"] = story_count
    user["day_streak"] = _days_since(user.get("created_at"))
    user["active"] = active
    return user


class UserUpdateRequest(BaseModel):
    """Body for updating user settings. Only provided fields are updated."""

    speed: str | None = Field(None, description="Speed (text)")
    morningTime_Reminder: bool | None = Field(None, description="Morning reminder on/off")
    bedTime_Reminder: bool | None = Field(None, description="Bedtime reminder on/off")
    name: str | None = Field(None, description="User's name")
    email: str | None = Field(None, description="User's email")
    password: str | None = Field(None, description="User's password")
    location: str | None = Field(None, description="User's location")
    energyWord: str | None = Field(None, description="User's energy word")
    lovedOne: str | None = Field(None, description="User's loved one")


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdateRequest):
    """
    Update Users table: Speed, Morning_Reminder, Bedtime_Reminder.
    Only fields present in the body are updated.
    """
    payload = {}
    if body.speed is not None:
        payload["speed"] = body.speed
    if body.morningTime_Reminder is not None:
        payload["morningTime_Reminder"] = body.is_morning_reminder
    if body.bedTime_Reminder is not None:
        payload["bedTime_Reminder"] = body.is_bedtime_reminder
    if body.name is not None:
        payload["name"] = body.username
    if body.email is not None:
        payload["email"] = body.email
    if body.password is not None:
        payload["password"] = body.password
    if body.location is not None:
        payload["location"] = body.location
    if body.energyWord is not None:
        payload["energyWord"] = body.energyWord
    if body.lovedOne is not None:
        payload["lovedOne"] = body.lovedOne
  
    if not payload:
        raise HTTPException(status_code=400, detail="Provide at least one field: speed, is_morning_reminder, is_bedtime_reminder")

    supabase = get_supabase()
    try:
        r = supabase.table("Users").update(payload).eq("id", user_id).execute()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Supabase error: {e}")

    return {"updated": True, "data": r.data}
