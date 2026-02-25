"""Desires table: list all desires (id, name)."""

from fastapi import APIRouter

from app.core.supabase_client import get_supabase

router = APIRouter(prefix="/desires", tags=["desires"])


@router.get("")
async def get_desires():
    """Get all rows from Desires table (id, name)."""
    supabase = get_supabase()
    r = supabase.table("Desires").select("id, name").execute()
    rows = list(r.data or [])
    return {"desires": rows}
