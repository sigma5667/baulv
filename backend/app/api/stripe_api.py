import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.db.models.user import User
from app.auth import get_current_user

router = APIRouter()

PRICE_MAP = {
    "basis": settings.stripe_price_basis,
    "pro": settings.stripe_price_pro,
}

PLAN_FROM_PRICE: dict[str, str] = {}


def _init_stripe():
    stripe.api_key = settings.stripe_secret_key
    if settings.stripe_price_basis:
        PLAN_FROM_PRICE[settings.stripe_price_basis] = "basis"
    if settings.stripe_price_pro:
        PLAN_FROM_PRICE[settings.stripe_price_pro] = "pro"


_init_stripe()


async def _ensure_stripe_customer(user: User, db: AsyncSession) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Stripe ist noch nicht konfiguriert.")
    customer = stripe.Customer.create(email=user.email, name=user.full_name)
    user.stripe_customer_id = customer.id
    await db.flush()
    return customer.id


@router.post("/checkout")
async def create_checkout_session(
    plan: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Stripe ist noch nicht konfiguriert.")

    if plan == "enterprise":
        raise HTTPException(400, "Enterprise-Plan: Bitte kontaktieren Sie uns direkt.")

    price_id = PRICE_MAP.get(plan)
    if not price_id:
        raise HTTPException(400, f"Ungültiger Plan: {plan}")

    customer_id = await _ensure_stripe_customer(user, db)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card", "sepa_debit"],
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{settings.frontend_url}/app/subscription?success=true",
        cancel_url=f"{settings.frontend_url}/app/subscription?canceled=true",
        metadata={"user_id": str(user.id)},
    )
    return {"checkout_url": session.url}


@router.post("/portal")
async def create_portal_session(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Stripe ist noch nicht konfiguriert.")
    if not user.stripe_customer_id:
        raise HTTPException(400, "Kein aktives Abonnement gefunden.")

    session = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=f"{settings.frontend_url}/app/subscription",
    )
    return {"portal_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    if not settings.stripe_webhook_secret:
        raise HTTPException(503, "Webhook secret nicht konfiguriert.")

    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(400, "Ungültige Webhook-Signatur.")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        customer_id = data["customer"]
        status = data["status"]
        price_id = data["items"]["data"][0]["price"]["id"] if data["items"]["data"] else None
        plan = PLAN_FROM_PRICE.get(price_id, "basis") if price_id else "basis"

        from sqlalchemy import select
        result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
        user = result.scalars().first()
        if user:
            if status == "active":
                user.subscription_plan = plan
            user.stripe_subscription_id = data["id"]
            await db.flush()

    elif event_type == "customer.subscription.deleted":
        customer_id = data["customer"]
        from sqlalchemy import select
        result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
        user = result.scalars().first()
        if user:
            user.subscription_plan = "basis"
            user.stripe_subscription_id = None
            await db.flush()

    elif event_type == "invoice.payment_failed":
        customer_id = data["customer"]
        # Could send notification email here
        pass

    return {"status": "ok"}
