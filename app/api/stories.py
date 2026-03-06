"""Stories endpoint: list stories for a user; generate story theme and story via Claude."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.core.claude import generate_story, generate_deepen_story
from app.core.config import CATEGORIES, ENERGY_WORDS
from app.core.story_audio import generate_and_store_story_audio
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


class DeepenStoryRequest(BaseModel):
    """Request body for generating a deepening continuation of an existing story."""

    user_id: int = Field(..., description="User who owns the original story")
    story_id: int = Field(..., description="Original story id to deepen")
    name: str = Field(..., min_length=1, description="User's first name")
    location: str = Field(..., min_length=1, description="Where their dream life takes place (city or country)")
    energyWord: str = Field(..., description="Energy word: Powerful, Peaceful, Abundant, Grateful, Confident")
    lovedOne: str | None = Field(None, description="Someone they love (optional)")
    dreamLocation: str | None = Field(None, description="Dream destination (optional; falls back to location)")

    @model_validator(mode="after")
    def check_energy(self):
        if self.energyWord not in ENERGY_WORDS:
            raise ValueError(f"energyWord must be one of: {ENERGY_WORDS}")
        return self


@router.get("")
async def get_stories(user_id: str = Query(..., description="Filter stories by this user ID")):
    supabase = get_supabase()
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="user_id must be an integer")

    # Use service_role key in .env so RLS doesn't return empty; only non-deleted stories; only stories with voice_id set (not null, not empty string)
    r = supabase.table("Stories").select("*").eq("user_id", uid).or_("is_deleted.eq.false,is_deleted.is.null").execute()
    rows = list(r.data or [])
    rows = [s for s in rows if (s.get("voice_id") or "").strip()]
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
    r = supabase.table("Stories").select("id", "user_id").eq("id", story_id).or_("is_deleted.eq.false,is_deleted.is.null,voice_id.is.null").execute()
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

    # Non-subscribers: limit to 1 story per day (UTC). Monthly/annual (trialing or active) get unlimited.
    user_row = supabase.table("Users").select("subscription_plan", "subscription_status").eq("id", user_id).execute()
    user_data = (user_row.data or [])
    plan = (user_data[0].get("subscription_plan") or user_data[0].get("Subscription_Plan") or "").lower() if user_data else ""
    status = (user_data[0].get("subscription_status") or user_data[0].get("Subscription_Status") or "").lower() if user_data else ""
    is_subscribed = plan in ("monthly", "annual") and status in ("trialing", "active")
    if not is_subscribed:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        r_today = supabase.table("Stories").select("id", count="exact").eq("user_id", user_id).gte("created_at", today_start).or_("is_deleted.eq.false,is_deleted.is.null").execute()
        count_today = getattr(r_today, "count", None)
        if count_today is None:
            count_today = len(r_today.data or []) if r_today.data is not None else 0
        if (count_today or 0) >= 1:
            raise HTTPException(
                status_code=403,
                detail="Free users can generate up to 1 story per day. Subscribe to monthly or annual for unlimited stories.",
            )

    desire_id = _get_desire_id_by_name(supabase, desireCategory)
    r = supabase.table("Stories").select("id", "theme", count="exact").eq("user_id", user_id).eq("desire_id", desire_id).or_("is_deleted.eq.false,is_deleted.is.null").order("id").execute()
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


@router.post("/deepen")
async def deepen_story(body: DeepenStoryRequest):
    """Generate a deepening continuation of an existing story. Requires Stories.parent_story_id and Stories.deepening_level columns."""
    supabase = get_supabase()
    user_id = body.user_id
    story_id = body.story_id

    # Load original story and verify ownership (include voice_id for auto audio after deepen)
    r_orig = supabase.table("Stories").select("id", "user_id", "theme", "story", "desire_id", "voice_id").eq("id", story_id).or_("is_deleted.eq.false,is_deleted.is.null").execute()
    orig_rows = list(r_orig.data or [])
    if not orig_rows:
        raise HTTPException(status_code=404, detail="Story not found")
    orig = orig_rows[0]
    story_user_id = orig.get("user_id") or orig.get("userId")
    if story_user_id != user_id:
        raise HTTPException(status_code=403, detail="Story does not belong to this user")

    original_theme = (orig.get("theme") or "").strip() or "Manifestation"
    original_story_text = (orig.get("story") or "").strip()
    desire_id = orig.get("desire_id")
    if desire_id is None:
        raise HTTPException(status_code=400, detail="Original story has no desire_id")

    # Get desire category for prompt
    dr = supabase.table("Desires").select("desireCategory").eq("id", desire_id).execute()
    desire_rows = list(dr.data or [])
    original_desire_category = desire_rows[0].get("desireCategory", "Life") if desire_rows else "Life"
    # Existing deepenings for this story (requires parent_story_id, deepening_level on Stories)
    r_deepen = supabase.table("Stories").select("id", "story", "deepening_level").eq("parent_story_id", story_id).or_("is_deleted.eq.false,is_deleted.is.null").execute()
    deepen_rows = list(r_deepen.data or [])
    # Sort by deepening_level ascending and take last for "previous" text
    def _level(row):
        v = row.get("deepening_level") or row.get("deepeningLevel") or 0
        return int(v) if v is not None else 0
    deepen_rows.sort(key=_level)
    previous_story_text = (deepen_rows[-1].get("story") or "").strip() if deepen_rows else original_story_text
    deepening_count = len(deepen_rows) + 1

    # Same subscription limit as generate: free = 1 story per day (UTC)
    user_row = supabase.table("Users").select("subscription_plan", "subscription_status").eq("id", user_id).execute()
    user_data = (user_row.data or [])
    plan = (user_data[0].get("subscription_plan") or user_data[0].get("Subscription_Plan") or "").lower() if user_data else ""
    status = (user_data[0].get("subscription_status") or user_data[0].get("Subscription_Status") or "").lower() if user_data else ""
    is_subscribed = plan in ("monthly", "annual") and status in ("trialing", "active")
    if not is_subscribed:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        r_today = supabase.table("Stories").select("id", count="exact").eq("user_id", user_id).gte("created_at", today_start).or_("is_deleted.eq.false,is_deleted.is.null").execute()
        count_today = getattr(r_today, "count", None)
        if count_today is None:
            count_today = len(r_today.data or []) if r_today.data is not None else 0
        if (count_today or 0) >= 1:
            raise HTTPException(
                status_code=403,
                detail="Free users can generate up to 1 story per day. Subscribe to monthly or annual for unlimited stories.",
            )

    try:
        theme, story = await generate_deepen_story(
            user_name=body.name,
            location=body.location,
            energy_word=body.energyWord,
            loved_one_name=body.lovedOne or "Not provided",
            dream_location=body.dreamLocation or body.location,
            original_desire_category=original_desire_category,
            original_theme=original_theme,
            previous_story_text=previous_story_text or "(No previous story)",
            deepening_count=deepening_count,
        )
    except ValueError as e:
        if "ANTHROPIC_API_KEY" in str(e):
            raise HTTPException(status_code=503, detail="Story generation is not configured")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Deepen story generation failed: {e!s}")

    insert_payload = {
        "theme": theme,
        "user_id": user_id,
        "desire_id": desire_id,
        "story": story,
        "parent_story_id": story_id,
        "deepening_level": deepening_count,
    }
    try:
        r = supabase.table("Stories").insert(insert_payload).execute()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to store deepening story: {e!s}")

    rows = list(r.data or [])
    created = rows[0] if rows else {}
    new_story_id = created.get("id")

    # Generate audio using original story's voice_id (same as generate_audio endpoint)
    orig_voice_id = (orig.get("voice_id") or orig.get("voiceId") or "").strip()
    if new_story_id and orig_voice_id:
        try:
            await generate_and_store_story_audio(
                story_id=new_story_id,
                voice_id=orig_voice_id,
            )
        except Exception as e:
            logging.warning("Auto-generate audio for deepening story %s failed: %s", new_story_id, e)

    return {
        "id": new_story_id,
        "theme": theme,
        "story": story,
        "deepening_level": deepening_count,
        "parent_story_id": story_id,
    }
