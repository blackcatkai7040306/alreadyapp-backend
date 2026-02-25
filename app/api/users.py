import logging
from fastapi import APIRouter, HTTPException, Query

from pydantic import BaseModel, Field

from app.api.subscription import _fetch_subscription_from_stripe
from app.core.supabase_client import get_supabase

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}")
async def get_user_info(user_id: int):
    """
    Get user info with synced subscription (from Stripe) and number of stories this user created.
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

    sr = supabase.table("Stories").select("id").eq("user_id", user_id).execute()
    story_count = len(sr.data or [])
    user["story_count"] = story_count
    return user


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
