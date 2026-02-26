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
    morningTime_Reminder: datetime | None = Field(None, description="Morning reminder time (ISO datetime)")
    bedTime_Reminder: datetime | None = Field(None, description="Bedtime reminder time (ISO datetime)")
    is_MorningTime_Reminder: bool | None = Field(None, description="Morning reminder on/off")
    is_BedTime_Reminder: bool | None = Field(None, description="Bedtime reminder on/off")
    name: str | None = Field(None, description="User's name")
    email: str | None = Field(None, description="User's email")
    password: str | None = Field(None, description="User's password")
    location: str | None = Field(None, description="User's location")
    energyWord: str | None = Field(None, description="User's energy word")
    lovedOne: str | None = Field(None, description="User's loved one")
    sleepTime: int | None = Field(None, description="User's sleep time (minutes)")
    fcm_token: str | None = Field(None, description="FCM device token for push notifications")
    timezone: str | None = Field(None, description="IANA timezone (e.g. America/Los_Angeles) for reminder times")

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
        # Supabase column is timestamp (no tz); send naive "YYYY-MM-DD HH:MM:SS"
        payload["morningTime_Reminder"] = body.morningTime_Reminder.strftime("%Y-%m-%d %H:%M:%S")
    if body.bedTime_Reminder is not None:
        payload["bedTime_Reminder"] = body.bedTime_Reminder.strftime("%Y-%m-%d %H:%M:%S")
    if body.is_MorningTime_Reminder is not None:
        payload["is_MorningTime_Reminder"] = body.is_MorningTime_Reminder
    if body.is_BedTime_Reminder is not None:
        payload["is_BedTime_Reminder"] = body.is_BedTime_Reminder
    if body.sleepTime is not None:
        payload["sleepTime"] = body.sleepTime
    if body.name is not None:
        payload["name"] = body.name
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
    if body.fcm_token is not None:
        payload["fcm_token"] = body.fcm_token
    if body.timezone is not None:
        payload["timezone"] = body.timezone

    if not payload:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")

    supabase = get_supabase()
    try:
        r = supabase.table("Users").update(payload).eq("id", user_id).execute()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Supabase error: {e}")

    return {"updated": True, "data": r.data}
