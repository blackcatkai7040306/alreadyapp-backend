"""Stories endpoint: list stories for a user; generate story theme and story via Claude."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.core.claude import generate_story
from app.core.config import CATEGORIES, ENERGY_WORDS
from app.core.supabase_client import get_supabase

router = APIRouter(prefix="/stories", tags=["stories"])


class GenerateStoryRequest(BaseModel):
    user_id: int = Field(..., description="User who owns this story")
    name: str = Field(..., min_length=1, description="User's first name")
    location: str = Field(..., min_length=1, description="Where their dream life takes place (city or country)")
    energyWord: str = Field(..., description="Energy word: Powerful, Peaceful, Abundant, Grateful, Confident")
    desireCategory: str = Field(..., description="Category: Love, Money, Career, Health, Home")
    desireDescription: str = Field(..., min_length=1, description="User's description, past tense")
    lovedOne: str | None = Field(None, description="Someone they love (optional)")

    @model_validator(mode="after")
    def check_energy_and_category(self):
        if self.energyWord not in ENERGY_WORDS:
            raise ValueError(f"energyWord must be one of: {ENERGY_WORDS}")
        if self.desireCategory not in CATEGORIES:
            raise ValueError(f"desireCategory must be one of: {CATEGORIES}")
        return self


@router.get("")
async def get_stories(user_id: str = Query(..., description="Filter stories by this user ID")):
    supabase = get_supabase()
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="user_id must be an integer")

    # Use service_role key in .env so RLS doesn't return empty; only non-deleted stories (is_deleted = false or null)
    r = supabase.table("Stories").select("*").eq("user_id", uid).or_("is_deleted.eq.false").execute()
    rows = list(r.data or [])
    if not rows:
        return {"stories": []}

    desire_ids = list({s["desire_id"] for s in rows if s.get("desire_id") is not None})
    name_by_id = {}
    if desire_ids:
        dr = supabase.table("Desires").select("id, desireCategory").in_("id", desire_ids).execute()
        name_by_id = {d["id"]: d.get("desireCategory") for d in (dr.data or [])}

    for row in rows:
        row["desire_name"] = name_by_id.get(row.get("desire_id"))

    return {"stories": rows}


@router.delete("/{story_id}")
async def delete_story(
    story_id: int,
    user_id: int | None = Query(None, description="Optional: verify the story belongs to this user"),
):
    """Soft-delete a story by id (sets is_deleted). Optionally pass user_id to ensure ownership."""
    supabase = get_supabase()
    r = supabase.table("Stories").select("id", "user_id").eq("id", story_id).execute()
    rows = list(r.data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="Story not found")
    row = rows[0]
    if user_id is not None:
        story_user_id = row.get("user_id") or row.get("userId")
        if story_user_id != user_id:
            raise HTTPException(status_code=403, detail="Story does not belong to this user")
    try:
        supabase.table("Stories").update({"is_deleted": True}).eq("id", story_id).execute()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to delete story: {e!s}")
    return {"ok": True, "story_id": story_id}


def _get_desire_id_by_name(supabase, category: str) -> int:
    """Look up Desires.id by Desires.desireCategory. Raises if not found."""
    r = supabase.table("Desires").select("id").eq("desireCategory", category).execute()
    rows = list(r.data or [])
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No desire found for category {category!r}. Add a row in Desires with desireCategory={category!r}.",
        )
    row = rows[0]
    desire_id = row.get("id") or row.get("Id")
    if desire_id is None:
        raise HTTPException(status_code=500, detail="Desires row missing id")
    return int(desire_id)


@router.post("/generate")
async def generate_story_content(body: GenerateStoryRequest):
    # Match variable names to GenerateStoryRequest field names (self.user_id, self.name, ...)
    user_id = body.user_id
    name = body.name
    location = body.location
    energyWord = body.energyWord
    desireCategory = body.desireCategory
    desireDescription = body.desireDescription
    lovedOne = body.lovedOne

    supabase = get_supabase()

    # Non-subscribers: limit to 1 story per day (UTC). Weekly/annual (trialing or active) get unlimited.
    user_row = supabase.table("Users").select("subscription_plan", "subscription_status").eq("id", user_id).execute()
    user_data = (user_row.data or [])
    plan = (user_data[0].get("subscription_plan") or user_data[0].get("Subscription_Plan") or "").lower() if user_data else ""
    status = (user_data[0].get("subscription_status") or user_data[0].get("Subscription_Status") or "").lower() if user_data else ""
    is_subscribed = plan in ("weekly", "annual") and status in ("trialing", "active")
    if not is_subscribed:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        r_today = supabase.table("Stories").select("id", count="exact").eq("user_id", user_id).gte("created_at", today_start).execute()
        count_today = getattr(r_today, "count", None)
        if count_today is None:
            count_today = len(r_today.data or []) if r_today.data is not None else 0
        if (count_today or 0) >= 1:
            raise HTTPException(
                status_code=403,
                detail="Free users can generate up to 1 story per day. Subscribe to weekly or annual for unlimited stories.",
            )

    desire_id = _get_desire_id_by_name(supabase, desireCategory)
    r = supabase.table("Stories").select("id", "theme", count="exact").eq("user_id", user_id).eq("desire_id", desire_id).order("id").execute()
    rows = list(r.data or [])
    existing_count = r.count if getattr(r, "count", None) is not None else len(rows)
    story_count = existing_count + 1
    previous_story_themes = [
        (s.get("theme") or "").strip()
        for s in rows
        if (s.get("theme") or "").strip()
    ]
    try:
        theme, story = await generate_story(
            name=name,
            location=location,
            energyWord=energyWord,
            desireCategory=desireCategory,
            desireDescription=desireDescription,
            lovedOne=lovedOne,
            storyCount=story_count,
            previousStoryThemes=previous_story_themes,
        )
    except ValueError as e:
        if "ANTHROPIC_API_KEY" in str(e):
            raise HTTPException(status_code=503, detail="Story generation is not configured")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Story generation failed: {e!s}")

    try:
        r = supabase.table("Stories").insert({
            "theme": theme,
            "user_id": user_id,
            "desire_id": desire_id,
            "story": story,
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to store story: {e!s}")

    rows = list(r.data or [])
    created = rows[0] if rows else {}
    return {
        "id": created.get("id"),
        "theme": theme,
        "story": story,
    }
