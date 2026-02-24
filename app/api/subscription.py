"""Stripe subscription: checkout (mobile opens URL), webhook, status for Restore Purchase."""

import logging
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

import stripe
from app.core.config import settings
from app.core.supabase_client import get_supabase

router = APIRouter(prefix="/subscription", tags=["subscription"])

# Plan identifiers matching paywall: annual ($199/year), weekly ($7.99/week)
PLAN_ANNUAL = "annual"
PLAN_WEEKLY = "weekly"


def _get_price_id(plan: str) -> str:
    if plan == PLAN_ANNUAL:
        return settings.STRIPE_PRICE_ID_ANNUAL
    if plan == PLAN_WEEKLY:
        return settings.STRIPE_PRICE_ID_WEEKLY
    raise ValueError(f"plan must be {PLAN_ANNUAL!r} or {PLAN_WEEKLY!r}")


class CreateCheckoutRequest(BaseModel):
    user_id: int = Field(..., description="Your app user id (stored in client_reference_id)")
    plan: str = Field(..., description="annual or weekly")
    success_url: str = Field(..., description="URL to redirect after success (e.g. myapp://subscription/success)")
    cancel_url: str = Field(..., description="URL to redirect if user cancels (e.g. myapp://subscription/cancel)")
    customer_email: str | None = Field(None, description="Optional; prefill Stripe Checkout email")


@router.post("/checkout")
async def create_checkout(body: CreateCheckoutRequest):
    """
    Create a Stripe Checkout Session (7-day trial, then recurring).
    Mobile app should open the returned URL in a browser/WebView.
    """
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe is not configured")
    try:
        price_id = _get_price_id(body.plan)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not price_id:
        raise HTTPException(status_code=503, detail=f"Stripe price not configured for plan {body.plan}")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[
                {
                    "price": price_id,
                    "quantity": 1,
                }
            ],
            subscription_data={
                "trial_period_days": settings.STRIPE_TRIAL_DAYS,
            },
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            client_reference_id=str(body.user_id),
            customer_email=body.customer_email or None,
        )
    except stripe.StripeError as e:
        logging.exception("Stripe checkout error: %s", e)
        raise HTTPException(status_code=502, detail="Checkout failed")

    return {"url": session.url, "session_id": session.id}


@router.get("/status")
async def subscription_status(user_id: int = Query(..., description="App user id")):
    """
    Return subscription status for Restore Purchase and feature gating.
    Requires Users table columns: stripe_customer_id, stripe_subscription_id, subscription_status, subscription_plan, trial_end.
    """
    supabase = get_supabase()
    r = supabase.table("Users").select(
        "stripe_customer_id",
        "stripe_subscription_id",
        "subscription_status",
        "subscription_plan",
        "trial_end",
    ).eq("id", user_id).execute()
    rows = list(r.data or [])
    if not rows:
        return {"active": False, "plan": None, "trial_end": None, "status": None}
    row = rows[0]
    status = row.get("subscription_status") or row.get("Subscription_Status")
    plan = row.get("subscription_plan") or row.get("Subscription_Plan")
    trial_end = row.get("trial_end") or row.get("Trial_End")
    # active if trialing or active (Stripe subscription statuses)
    active = str(status).lower() in ("active", "trialing")
    return {
        "active": active,
        "plan": plan,
        "trial_end": trial_end,
        "status": status,
    }


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """
    Stripe webhook: verify signature and handle checkout.session.completed,
    customer.subscription.updated, customer.subscription.deleted.
    Configure in Stripe Dashboard: URL https://your-api/api/subscription/webhook
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    payload = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")
    except stripe.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

    supabase = get_supabase()

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("client_reference_id")
        if not user_id:
            logging.warning("checkout.session.completed missing client_reference_id")
            return {"received": True}
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            logging.warning("checkout.session.completed invalid user_id: %s", user_id)
            return {"received": True}
        subscription_id = session.get("subscription")
        customer_id = session.get("customer")
        if not subscription_id or not customer_id:
            logging.warning("checkout.session.completed missing subscription or customer")
            return {"received": True}
        # Fetch subscription for trial_end and plan
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
        except stripe.StripeError as e:
            logging.exception("Failed to retrieve subscription %s: %s", subscription_id, e)
            return {"received": True}
        trial_end_ts = getattr(sub, "trial_end", None) or sub.get("trial_end")
        items = (sub.get("items") or {}).get("data") or []
        price_id = (items[0].get("price") or {}).get("id") if items else None
        plan = PLAN_ANNUAL if price_id == settings.STRIPE_PRICE_ID_ANNUAL else (PLAN_WEEKLY if price_id == settings.STRIPE_PRICE_ID_WEEKLY else None) or "unknown"
        status = (getattr(sub, "status", None) or sub.get("status") or "active").lower()
        trial_end_iso = datetime_from_timestamp(trial_end_ts).isoformat() if trial_end_ts else None
        try:
            supabase.table("Users").update({
                "stripe_customer_id": customer_id,
                "stripe_subscription_id": subscription_id,
                "subscription_status": status,
                "subscription_plan": plan,
                "trial_end": trial_end_iso,
            }).eq("id", uid).execute()
        except Exception as e:
            logging.exception("Failed to update Users with subscription: %s", e)
        return {"received": True}

    if event["type"] == "customer.subscription.updated":
        sub = event["data"]["object"]
        subscription_id = sub["id"]
        status = (sub.get("status") or "active").lower()
        trial_end = sub.get("trial_end")
        items = (sub.get("items") or {}).get("data") or []
        price_id = (items[0].get("price") or {}).get("id") if items else None
        plan = PLAN_ANNUAL if price_id == settings.STRIPE_PRICE_ID_ANNUAL else (PLAN_WEEKLY if price_id == settings.STRIPE_PRICE_ID_WEEKLY else None) or "unknown"
        payload = {"subscription_status": status, "subscription_plan": plan}
        if trial_end is not None:
            payload["trial_end"] = datetime_from_timestamp(trial_end).isoformat()
        try:
            supabase.table("Users").update(payload).eq("stripe_subscription_id", subscription_id).execute()
        except Exception as e:
            logging.exception("Failed to update subscription: %s", e)
        return {"received": True}

    if event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        subscription_id = sub["id"]
        try:
            supabase.table("Users").update({
                "subscription_status": "canceled",
            }).eq("stripe_subscription_id", subscription_id).execute()
        except Exception as e:
            logging.exception("Failed to mark subscription canceled: %s", e)
        return {"received": True}

    return {"received": True}


def datetime_from_timestamp(ts: int) -> "datetime":
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc)
