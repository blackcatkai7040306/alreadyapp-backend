"""Stories endpoint: list stories for a user; generate story content via Claude."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.core.claude import generate_story
from app.core.supabase_client import get_supabase

router = APIRouter(prefix="/stories", tags=["stories"])

# Onboarding inputs for story generation (from Already Done flow)
CATEGORIES = ("Love", "Money", "Career", "Health", "Home")
ENERGY_WORDS = ("Powerful", "Peaceful", "Abundant", "Grateful", "Confident")


class GenerateStoryRequest(BaseModel):
    first_name: str = Field(..., min_length=1, description="User's first name")
    dream_place: str = Field(..., min_length=1, description="Where their dream life takes place (city or country)")
    energy_word: str = Field(..., description="Energy word: Powerful, Peaceful, Abundant, Grateful, Confident")
    category: str = Field(..., description="Category: Love, Money, Career, Health, Home")
    describe_whats_already_yours: str = Field(..., min_length=1, description="User's description, past tense")
    someone_you_love: str | None = Field(None, description="Someone they love (optional)")

    @model_validator(mode="after")
    def check_energy_and_category(self):
        if self.energy_word not in ENERGY_WORDS:
            raise ValueError(f"energy_word must be one of: {ENERGY_WORDS}")
        if self.category not in CATEGORIES:
            raise ValueError(f"category must be one of: {CATEGORIES}")
        return self


@router.get("")
async def get_stories(user_id: str = Query(..., description="Filter stories by this user ID")):
    """Get all stories for the given user_id, with desire_name from Desires (Desires.id = Stories.desire_id)."""
    supabase = get_supabase()
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="user_id must be an integer")

    # Use service_role key in .env so RLS doesn't return empty; tables are Stories, Desires
    r = supabase.table("Stories").select("*").eq("user_id", uid).execute()
    rows = list(r.data or [])
    print(rows)
    if not rows:
        return {"stories": []}

    desire_ids = list({s["desire_id"] for s in rows if s.get("desire_id") is not None})
    name_by_id = {}
    if desire_ids:
        dr = supabase.table("Desires").select("id, name").in_("id", desire_ids).execute()
        name_by_id = {d["id"]: d.get("name") for d in (dr.data or [])}

    for row in rows:
        row["desire_name"] = name_by_id.get(row.get("desire_id"))

    return {"stories": rows}


@router.post("/generate")
async def generate_story_content(body: GenerateStoryRequest):
    """Generate story content from onboarding inputs using Claude. Returns the narrative text (past tense)."""
    try:
        content = await generate_story(
            first_name=body.first_name,
            dream_place=body.dream_place,
            energy_word=body.energy_word,
            category=body.category,
            describe_whats_already_yours=body.describe_whats_already_yours,
            someone_you_love=body.someone_you_love,
        )
    except ValueError as e:
        if "ANTHROPIC_API_KEY" in str(e):
            raise HTTPException(status_code=503, detail="Story generation is not configured")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Story generation failed: {e!s}")
    return {"content": content}
