"""Stories endpoint: list stories for a user with desire name from Desires table."""

from fastapi import APIRouter, HTTPException, Query

from app.core.supabase_client import get_supabase

router = APIRouter(prefix="/stories", tags=["stories"])


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
