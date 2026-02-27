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

    # Use service_role key in .env so RLS doesn't return empty; tables are Stories, Desires
    r = supabase.table("Stories").select("*").eq("user_id", uid).execute()
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


def _user_has_subscription(supabase, user_id: int) -> bool:
    """True if user has stripe_subscription_id (considered subscribed)."""
    r = supabase.table("Users").select("stripe_subscription_id").eq("id", user_id).execute()
    rows = list(r.data or [])
    if not rows:
        return False
    sub_id = rows[0].get("stripe_subscription_id") or rows[0].get("stripe_subscription_Id")
    return bool(sub_id and str(sub_id).strip())


def _count_stories_generated_today(supabase, user_id: int) -> int:
    """Count stories created today (UTC) by this user."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_iso = today_start.isoformat()
    r = supabase.table("Stories").select("id", count="exact").eq("user_id", user_id).gte("created_at", today_iso).execute()
    return getattr(r, "count", None) if getattr(r, "count", None) is not None else len(r.data or [])


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

    # Non-subscribers: max 1 story per day
    if not _user_has_subscription(supabase, user_id):
        today_count = _count_stories_generated_today(supabase, user_id)
        if today_count >= 1:
            raise HTTPException(
                status_code=403,
                detail="Non-subscribers can generate only 1 story per day. Subscribe to create more.",
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
