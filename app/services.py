from __future__ import annotations
from datetime import timedelta, date, datetime, timezone
from sqlalchemy import select, func, or_, exists, not_, desc
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from .db import (User, Generation, Referral, PaymentOrder, SupportTicket, DailySuggestion, CreditTransaction,
                 AdminAuditLog, BotControl, Coupon, CouponRedemption)
from .config import settings

PLAN_CREDITS = {"free": 15, "starter": 120, "pro": 350, "studio": 1000, "studio_plus": settings.studio_plus_credits}
PLAN_PRICE = {
    "starter": settings.starter_price,
    "pro": settings.pro_price,
    "studio": settings.studio_price,
    "studio_plus": settings.studio_plus_price,
}

GEN_COST = {
    "caption": 2,
    "post": 4,
    "story": 4,
    "reel": 4,
    "product": 4,
    "ideas": 2,
    "rewrite": 2,
    "brief": 2,
    "campaign": 7,
    "pack": 7,
    "image": 10,
}

# Studio+ uses its own credit economy because its text generation is routed
# through the premium model. Normal-plan generation costs must remain stable.
STUDIO_PLUS_GEN_COST = {
    "caption": 4,
    "post": 7,
    "story": 7,
    "reel": 8,
    "product": 7,
    "ideas": 3,
    "rewrite": 6,
    "brief": 4,
    "campaign": 15,
    "pack": 15,
    "image": 20,
}

PLAN_VARIANTS = {"free": 2, "starter": 2, "pro": 2, "studio": 2, "studio_plus": 2}
PROFILE_LIMITS = {"niche": 100, "audience": 160, "tone": 80, "main_offer": 180, "differentiator": 220, "content_goal": 180}
SUPPORT_MESSAGE_LIMIT = 1200
SUPPORT_COOLDOWN_SECONDS = 60
SUPPORT_DAILY_LIMIT = 5
FREE_PACK_WEEKLY_LIMIT = 1
FREE_IDEA_DAILY_LIMIT = 1
FREE_CONTENT_DAILY_LIMIT = 3
REFERRAL_FIRST_REWARD = 15
REFERRAL_REPEAT_REWARD = 5
REFERRAL_MAX_PER_30_DAYS = 4
REFERRAL_WINDOW_DAYS = 30
REFERRAL_INVITEE_REWARD = 15


def today() -> str:
    return date.today().isoformat()


def week_start() -> datetime:
    d = date.today()
    monday = d - timedelta(days=d.weekday())
    return datetime.combine(monday, datetime.min.time())



async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str | None, first_name: str | None, referral_code: str | None) -> tuple[User, bool]:
    user = await get_user(session, telegram_id)
    if user:
        return user, False
    code = f"u{telegram_id}"
    inviter = None
    if referral_code and referral_code != code:
        inviter = await session.scalar(select(User).where(User.referral_code == referral_code))
    user = User(telegram_id=telegram_id, username=username, first_name=first_name, referral_code=code, referred_by_user_id=inviter.id if inviter else None)
    session.add(user)
    await session.flush()
    if inviter:
        session.add(Referral(inviter_id=inviter.id, invitee_id=user.id))
    await session.commit()
    return user, True


async def profile_complete(user: User) -> bool:
    return bool(user.niche and user.audience and user.tone)


async def set_profile(session: AsyncSession, user: User, niche: str, audience: str, tone: str | None = None) -> None:
    niche = niche.strip()
    audience = audience.strip()
    tone = (tone or user.tone or "صمیمی و حرفه‌ای").strip()
    if len(niche) > PROFILE_LIMITS["niche"]:
        raise ValueError(f"حوزه فعالیت حداکثر {PROFILE_LIMITS['niche']} کاراکتر باشد.")
    if len(audience) > PROFILE_LIMITS["audience"]:
        raise ValueError(f"مخاطب اصلی حداکثر {PROFILE_LIMITS['audience']} کاراکتر باشد.")
    if len(tone) > PROFILE_LIMITS["tone"]:
        raise ValueError(f"لحن برند حداکثر {PROFILE_LIMITS['tone']} کاراکتر باشد.")
    user.niche, user.audience, user.tone = niche, audience, tone
    await session.commit()




async def set_profile_additional(
    session: AsyncSession,
    user: User,
    main_offer: str | None = None,
    differentiator: str | None = None,
    content_goal: str | None = None,
) -> None:
    values = {
        "main_offer": (main_offer or "").strip() or None,
        "differentiator": (differentiator or "").strip() or None,
        "content_goal": (content_goal or "").strip() or None,
    }
    for field, value in values.items():
        if value and len(value) > PROFILE_LIMITS[field]:
            raise ValueError(f"{field} بیش از حد طولانی است.")
    user.main_offer = values["main_offer"]
    user.differentiator = values["differentiator"]
    user.content_goal = values["content_goal"]
    await session.commit()

async def weekly_pack_count(session: AsyncSession, user: User) -> int:
    result = await session.scalar(select(func.count(Generation.id)).where(
        Generation.user_id == user.id,
        Generation.kind == "pack",
        Generation.created_at >= week_start(),
    ))
    return int(result or 0)


def daily_free_image_limit(user: User) -> int:
    if user.plan == "studio_plus":
        return 1
    if user.plan == "studio":
        return 2
    if user.plan == "pro":
        return 1
    return 0


def daily_free_images_used(user: User) -> int:
    if user.daily_image_free_date != today():
        return 0
    return int(user.daily_image_free_used or 0)


def daily_free_images_remaining(user: User) -> int:
    return max(0, daily_free_image_limit(user) - daily_free_images_used(user))


async def can_use_free_image(session: AsyncSession, user: User) -> bool:
    return daily_free_images_remaining(user) > 0


async def can_generate(session: AsyncSession, user: User, kind: str, premium: bool = False) -> tuple[bool, str]:
    if premium and user.plan != "studio_plus" and not is_admin(user):
        return False, "این ابزار فقط برای Studio+ فعال است."
    access_ok, access_reason = await generation_access_status(session, user)
    if not access_ok:
        return False, access_reason
    if is_admin(user):
        return True, ""
    if kind == "pack" and user.plan == "free":
        used = await weekly_pack_count(session, user)
        if used >= FREE_PACK_WEEKLY_LIMIT:
            next_date = (week_start() + timedelta(days=7)).date()
            return False, f"📦 سهمیه بسته هفتگی رایگان این هفته‌ات استفاده شده.\n\nدوباره از دوشنبه {next_date:%Y/%m/%d} می‌توانی ۱ بسته رایگان بگیری."
    if kind == "ideas" and user.plan == "free":
        start_of_day = datetime.combine(date.today(), datetime.min.time())
        used = await session.scalar(select(func.count(Generation.id)).where(
            Generation.user_id == user.id,
            Generation.kind == "ideas",
            Generation.created_at >= start_of_day,
        ))
        if int(used or 0) >= FREE_IDEA_DAILY_LIMIT:
            return False, "💡 سهمیه رایگان ایده محتوا برای امروز استفاده شده. فردا دوباره ۱ ایده رایگان داری."
    cost = STUDIO_PLUS_GEN_COST[kind] if premium else GEN_COST[kind]
    if kind == "image" and daily_free_images_remaining(user) > 0:
        return True, ""
    if user.plan == "free":
        if user.daily_free_date != today():
            user.daily_free_date, user.daily_free_used = today(), 0
        if user.daily_free_used >= 3:
            return False, "سهمیه رایگان امروز تمام شده. برای ادامه اعتبار بخر یا پلن بگیر."
        if user.credits < cost:
            return False, "اعتبار آزمایشی کافی نیست."
        return True, ""
    if user.credits < cost:
        return False, "اعتبار این دوره تمام شده. ارتقا یا خرید اعتبار تکمیلی لازم است."
    return True, ""


async def referral_reward_info(session: AsyncSession, user: User) -> tuple[int, int, datetime | None]:
    """Return (successful_count_in_current_30_day_cycle, remaining, cycle_end)."""
    now = datetime.utcnow()
    cycle_start = user.referral_cycle_started_at
    if not cycle_start or now >= cycle_start + timedelta(days=REFERRAL_WINDOW_DAYS):
        return 0, REFERRAL_MAX_PER_30_DAYS, None
    count = int(user.referral_cycle_count or 0)
    remaining = max(0, REFERRAL_MAX_PER_30_DAYS - count)
    return count, remaining, cycle_start + timedelta(days=REFERRAL_WINDOW_DAYS)


async def record_generation(
    session: AsyncSession,
    user: User,
    kind: str,
    request: str,
    output: str,
    premium: bool = False,
) -> Generation:
    if premium and user.plan != "studio_plus" and not is_admin(user):
        raise PermissionError("این ابزار فقط برای Studio+ فعال است.")
    cost = STUDIO_PLUS_GEN_COST[kind] if premium else GEN_COST[kind]
    admin = is_admin(user)
    free_image = (kind == "image" and not admin and daily_free_images_remaining(user) > 0)

    if not admin:
        if free_image:
            if user.daily_image_free_date != today():
                user.daily_image_free_date = today()
                user.daily_image_free_used = 0
            user.daily_image_free_used += 1
            recorded_cost = 0
        else:
            user.credits -= cost
            recorded_cost = cost
            if user.plan == "free":
                if user.daily_free_date != today():
                    user.daily_free_date, user.daily_free_used = today(), 0
                user.daily_free_used += 1
    else:
        recorded_cost = 0

    d = today()
    if user.last_generation_date == (date.today() - timedelta(days=1)).isoformat():
        user.streak_days += 1
    elif user.last_generation_date != d:
        user.streak_days = 1
    user.last_generation_date = d
    if not admin and user.streak_days > 0 and user.streak_days % 7 == 0:
        user.credits += 10

    generation = Generation(
        user_id=user.id,
        kind=kind,
        input_text=request,
        output_text=output,
        cost_credits=recorded_cost,
    )
    session.add(generation)

    if not user.referral_rewarded and user.referred_by_user_id:
        referral = await session.scalar(select(Referral).where(
            Referral.invitee_id == user.id,
            Referral.inviter_id == user.referred_by_user_id,
        ))
        if referral and not referral.rewarded:
            inviter = await session.get(User, user.referred_by_user_id)
            if inviter:
                successful_count, _, _ = await referral_reward_info(session, inviter)
                if successful_count < REFERRAL_MAX_PER_30_DAYS:
                    now = datetime.utcnow()
                    if not inviter.referral_cycle_started_at or now >= inviter.referral_cycle_started_at + timedelta(days=REFERRAL_WINDOW_DAYS):
                        inviter.referral_cycle_started_at = now
                        inviter.referral_cycle_count = 0
                        successful_count = 0
                    inviter_reward = REFERRAL_FIRST_REWARD if successful_count == 0 else REFERRAL_REPEAT_REWARD
                    inviter.credits += inviter_reward
                    user.credits += REFERRAL_INVITEE_REWARD
                    inviter.referral_cycle_count += 1
                    referral.rewarded = True
                    referral.rewarded_at = now
                    user.referral_rewarded = True

    await session.commit()
    return generation


def normalize_coupon_code(code: str | None) -> str | None:
    if not code:
        return None
    value = str(code).strip().upper()
    if not value or len(value) > 64 or not value.replace("_", "").replace("-", "").isalnum():
        raise ValueError("کد تخفیف نامعتبر است.")
    return value


def coupon_discount_amount(coupon: Coupon, base_amount_toman: int) -> int:
    if coupon.discount_percent > 0 and coupon.discount_toman > 0:
        raise ValueError("تنظیمات کد تخفیف نامعتبر است.")
    if coupon.discount_percent:
        return min(base_amount_toman, (base_amount_toman * coupon.discount_percent) // 100)
    return min(base_amount_toman, coupon.discount_toman)


async def get_valid_coupon(
    session: AsyncSession, user: User, code: str, plan: str, *, lock: bool = False
) -> tuple[Coupon, int] | None:
    normalized = normalize_coupon_code(code)
    if not normalized or plan not in PLAN_PRICE:
        return None
    stmt = select(Coupon).where(Coupon.code == normalized, Coupon.active.is_(True))
    if lock:
        stmt = stmt.with_for_update()
    coupon = await session.scalar(stmt)
    if not coupon:
        return None
    if coupon.expires_at and coupon.expires_at <= datetime.utcnow():
        return None
    if coupon.applicable_plan and coupon.applicable_plan != plan:
        return None
    if coupon.discount_percent == 0 and coupon.discount_toman == 0:
        return None
    discount = coupon_discount_amount(coupon, PLAN_PRICE[plan])
    if discount <= 0 or discount >= PLAN_PRICE[plan]:
        return None
    return coupon, discount


async def create_coupon(
    session: AsyncSession, admin: User, code: str,
    *, discount_percent: int = 0, discount_toman: int = 0,
    applicable_plan: str | None = None, expires_at: datetime | None = None,
) -> Coupon:
    if not can_manage_users(admin):
        raise PermissionError("دسترسی مدیریت کد تخفیف نداری.")
    normalized = normalize_coupon_code(code)
    if not normalized:
        raise ValueError("کد تخفیف خالی است.")
    if discount_percent and discount_toman:
        raise ValueError("فقط یکی از درصد یا مبلغ ثابت را وارد کن.")
    if discount_percent and not 1 <= int(discount_percent) <= 90:
        raise ValueError("درصد تخفیف باید بین ۱ تا ۹۰ باشد.")
    if discount_toman and int(discount_toman) <= 0:
        raise ValueError("مبلغ تخفیف باید مثبت باشد.")
    if not discount_percent and not discount_toman:
        raise ValueError("مقدار تخفیف مشخص نشده است.")
    if applicable_plan not in {None, *PLAN_PRICE.keys()}:
        raise ValueError("پلن تخفیف نامعتبر است.")
    if expires_at and expires_at <= datetime.utcnow():
        raise ValueError("تاریخ انقضا باید در آینده باشد.")
    exists_coupon = await session.scalar(select(Coupon).where(Coupon.code == normalized))
    if exists_coupon:
        raise ValueError("این کد تخفیف از قبل وجود دارد.")
    coupon = Coupon(
        code=normalized, discount_percent=int(discount_percent), discount_toman=int(discount_toman),
        applicable_plan=applicable_plan, expires_at=expires_at, active=True, created_by_admin_id=admin.id,
    )
    session.add(coupon)
    await audit_admin_action(session, admin, "COUPON_CREATE", None, f"code={normalized}; percent={discount_percent}; amount={discount_toman}; plan={applicable_plan or 'all'}")
    await session.commit()
    return coupon


async def list_coupons(session: AsyncSession, active_only: bool = True, limit: int = 20) -> list[Coupon]:
    stmt = select(Coupon)
    if active_only:
        stmt = stmt.where(Coupon.active.is_(True))
    return list((await session.execute(stmt.order_by(Coupon.created_at.desc()).limit(limit))).scalars().all())


async def deactivate_coupon(session: AsyncSession, admin: User, code: str) -> bool:
    if not can_manage_users(admin):
        raise PermissionError("دسترسی مدیریت کد تخفیف نداری.")
    normalized = normalize_coupon_code(code)
    coupon = await session.scalar(select(Coupon).where(Coupon.code == normalized).with_for_update())
    if not coupon or not coupon.active:
        return False
    coupon.active = False
    await audit_admin_action(session, admin, "COUPON_DEACTIVATE", None, f"code={normalized}")
    await session.commit()
    return True


async def create_payment_order(session: AsyncSession, user: User, plan: str, coupon_code: str | None = None) -> PaymentOrder:
    if plan not in PLAN_PRICE:
        raise ValueError("پلن پرداختی نامعتبر است.")
    locked_user = await session.get(User, user.id, with_for_update=True)
    if not locked_user:
        raise ValueError("کاربر پیدا نشد.")
    now = datetime.utcnow()
    recent_cutoff = now - timedelta(minutes=max(1, int(settings.payment_order_reuse_minutes)))
    existing = await session.scalar(
        select(PaymentOrder).where(
            PaymentOrder.user_id == locked_user.id,
            PaymentOrder.status == "pending",
            PaymentOrder.created_at >= recent_cutoff,
        ).order_by(PaymentOrder.created_at.desc()).limit(1)
    )
    if existing:
        normalized = normalize_coupon_code(coupon_code)
        if normalized and existing.coupon_code and existing.coupon_code != normalized:
            raise ValueError("یک پرداخت دیگر برای این حساب در حال انجام است؛ ابتدا همان پرداخت را تکمیل کن.")
        if existing.plan != plan:
            raise ValueError("یک پرداخت دیگر برای این حساب هنوز در حال انجام است؛ ابتدا همان پرداخت را تکمیل یا لغو کن.")
        if existing.authority:
            return existing
    original_amount_toman = PLAN_PRICE[plan]
    discount_toman = 0
    selected_coupon = None
    if coupon_code:
        valid = await get_valid_coupon(session, locked_user, coupon_code, plan, lock=True)
        if not valid:
            raise ValueError("کد تخفیف برای این خرید معتبر نیست.")
        selected_coupon, discount_toman = valid
    amount_toman = original_amount_toman - discount_toman
    amount_rial = amount_toman * 10
    if not (0 < original_amount_toman <= 214_748_364) or not (0 < amount_toman <= 214_748_364) or not (0 < amount_rial <= 2_147_483_647):
        raise ValueError("مبلغ پلن خارج از محدوده مجاز است.")
    order = PaymentOrder(
        user_id=locked_user.id,
        plan=plan,
        amount_toman=amount_toman,
        amount_rial=amount_rial,
        original_amount_toman=original_amount_toman,
        coupon_code=selected_coupon.code if selected_coupon else None,
        discount_toman=discount_toman,
        provider="zarinpal",
        status="created",
    )
    session.add(order)
    await session.commit()
    return order


async def start_payment(
    session: AsyncSession,
    user: User,
    plan: str,
    mobile: str | None = None,
    email: str | None = None,
    coupon_code: str | None = None,
) -> tuple[PaymentOrder, str]:
    """Create a local order, request a gateway authority, and persist it.

    The user's plan/credits are never changed here. Entitlements belong to the
    verification step after the gateway confirms the payment.
    """
    from .payments import PaymentError, create_checkout, payment_url_from_authority

    if not await payments_enabled(session):
        raise PaymentError("پرداخت در حال حاضر غیرفعال است.")

    order = await create_payment_order(session, user, plan, coupon_code=coupon_code)
    if order.status == "pending" and order.authority:
        return order, payment_url_from_authority(order.authority)
    try:
        checkout = await create_checkout(order=order, mobile=mobile, email=email)
    except PaymentError as exc:
        order.status = "request_failed"
        order.failure_reason = str(exc)[:1000]
        await session.commit()
        raise

    order.authority = checkout.authority
    order.status = "pending"
    await session.commit()
    return order, checkout.payment_url


async def activate_plan(session: AsyncSession, user: User, plan: str) -> None:
    if plan not in PLAN_CREDITS:
        raise ValueError("پلن نامعتبر است.")
    user.plan, user.credits = plan, PLAN_CREDITS[plan]
    await session.commit()


async def verify_and_apply_payment(
    session: AsyncSession,
    order: PaymentOrder,
    *,
    authority: str,
    status: str,
    client=None,
) -> tuple[str, str | None]:
    """Verify a gateway callback and atomically grant the purchased plan once."""
    from .payments import PaymentError, ZarinPalClient

    authority = str(authority or "").strip()
    status = str(status or "").strip().upper()

    if not authority or len(authority) > 255:
        return "verify_failed", None
    if order.provider != "zarinpal":
        return "verify_failed", None
    if order.authority != authority:
        return "verify_failed", None
    if order.status == "paid":
        return "already_paid", order.transaction_ref
    if order.status not in {"pending", "created"}:
        return "failed", order.transaction_ref

    # The callback Status is informational. Do not let an unauthenticated NOK
    # callback cancel a live order; keep it retryable for a later valid callback.
    if status not in {"OK", "NOK"}:
        return "verify_failed", None
    if status != "OK":
        return "cancelled", None

    if order.plan not in PLAN_CREDITS:
        return "verify_failed", None
    expected_base = int(order.original_amount_toman or order.amount_toman)
    expected_discount = int(order.discount_toman or 0)
    expected_amount = expected_base - expected_discount
    if (
        not isinstance(order.amount_toman, int)
        or not isinstance(order.amount_rial, int)
        or expected_base <= 0
        or expected_discount < 0
        or expected_amount <= 0
        or order.amount_toman != expected_amount
        or order.amount_rial != expected_amount * 10
        or order.amount_rial > 2_147_483_647
    ):
        return "verify_failed", None

    gateway = client or ZarinPalClient()
    try:
        result = await gateway.verify_payment(amount_rial=order.amount_rial, authority=authority)
    except PaymentError as exc:
        # Keep the order pending: a timeout/malformed provider response can be
        # transient, and a later callback/verification can safely retry.
        order.failure_reason = str(exc)[:1000]
        try:
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
        return "verify_failed", None

    if result.code not in {100, 101}:
        order.status = "verify_failed"
        order.failure_reason = result.message[:1000] or f"کد verification: {result.code}"
        try:
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
        return "verify_failed", None

    if result.code == 100 and not result.ref_id:
        order.status = "verify_failed"
        order.failure_reason = "درگاه تراکنش موفق را بدون کد رهگیری برگرداند."
        try:
            await session.commit()
        except SQLAlchemyError:
            await session.rollback()
        return "verify_failed", None

    user = await session.get(User, order.user_id, with_for_update=True)
    if not user:
        return "verify_failed", None

    locked_order = await session.get(PaymentOrder, order.id, with_for_update=True)
    if not locked_order:
        return "verify_failed", None
    if locked_order.status == "paid":
        return "already_paid", locked_order.transaction_ref

    old_credits = int(user.credits or 0)
    new_credits = PLAN_CREDITS[locked_order.plan]
    if locked_order.coupon_code:
        coupon = await session.scalar(select(Coupon).where(Coupon.code == locked_order.coupon_code).with_for_update())
        if not coupon:
            return "verify_failed", None
        session.add(CouponRedemption(coupon_id=coupon.id, user_id=user.id, payment_order_id=locked_order.id))
    locked_order.status = "paid"
    locked_order.transaction_ref = result.ref_id
    locked_order.failure_reason = None
    locked_order.verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
    user.plan = locked_order.plan
    user.credits = new_credits
    session.add(
        CreditTransaction(
            user_id=user.id,
            admin_user_id=None,
            amount=new_credits - old_credits,
            balance_after=new_credits,
            reason=f"خرید پلن {locked_order.plan} با پرداخت #{locked_order.id}",
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # Unique transaction_ref (or another integrity guard) failed. Roll back
        # everything so no partial entitlement can survive.
        await session.rollback()
        return "verify_failed", None
    except SQLAlchemyError:
        await session.rollback()
        return "verify_failed", None
    return "paid", result.ref_id


async def get_open_support_ticket(session: AsyncSession, user: User) -> SupportTicket | None:
    return await session.scalar(select(SupportTicket).where(SupportTicket.user_id == user.id, SupportTicket.status == "open").order_by(SupportTicket.created_at.desc()))


async def get_support_ticket(session: AsyncSession, ticket_id: int, user_id: int | None = None) -> SupportTicket | None:
    query = select(SupportTicket).where(SupportTicket.id == ticket_id)
    if user_id is not None:
        query = query.where(SupportTicket.user_id == user_id)
    return await session.scalar(query)


async def list_user_tickets(session: AsyncSession, user: User, limit: int = 10) -> list[SupportTicket]:
    result = await session.execute(select(SupportTicket).where(SupportTicket.user_id == user.id).order_by(SupportTicket.created_at.desc()).limit(limit))
    return list(result.scalars().all())


async def append_ticket_message(session: AsyncSession, ticket: SupportTicket, sender: str, message: str) -> None:
    label = "👤 کاربر" if sender == "user" else "🛠 پشتیبانی"
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    ticket.message = f"{ticket.message}\n\n{label} • {stamp}\n{message.strip()[:SUPPORT_MESSAGE_LIMIT]}"
    if ticket.status == "closed":
        ticket.status = "open"
        ticket.closed_at = None
    await session.commit()


async def support_rate_limit(session: AsyncSession, user: User) -> tuple[bool, str]:
    open_ticket = await get_open_support_ticket(session, user)
    if open_ticket:
        return False, f"یک تیکت باز داری (#{open_ticket.id}). می‌توانی از «تیکت‌های من» همان تیکت را ادامه بدهی."
    now = datetime.utcnow()
    recent = await session.scalar(select(SupportTicket).where(SupportTicket.user_id == user.id).order_by(SupportTicket.created_at.desc()).limit(1))
    if recent and (now - recent.created_at).total_seconds() < SUPPORT_COOLDOWN_SECONDS:
        return False, "برای جلوگیری از اسپم، لطفاً کمی بعد دوباره تیکت ارسال کن."
    start_of_day = datetime.combine(date.today(), datetime.min.time())
    count = await session.scalar(select(func.count(SupportTicket.id)).where(SupportTicket.user_id == user.id, SupportTicket.created_at >= start_of_day))
    if (count or 0) >= SUPPORT_DAILY_LIMIT:
        return False, "سقف روزانه تیکت پشتیبانی پر شده. اگر موضوعت ادامه همان درخواست است، از تیکت قبلی ادامه بده."
    return True, ""


async def create_support_ticket(session: AsyncSession, user: User, topic: str, message: str) -> SupportTicket:
    ticket = SupportTicket(user_id=user.id, topic=topic.strip(), message=message.strip()[:SUPPORT_MESSAGE_LIMIT])
    session.add(ticket)
    await session.commit()
    return ticket


async def list_recent_generations(session: AsyncSession, user: User, limit: int = 8, favorites_only: bool = False) -> list[Generation]:
    q = select(Generation).where(Generation.user_id == user.id)
    if favorites_only:
        q = q.where(Generation.favorite.is_(True))
    q = q.order_by(Generation.created_at.desc()).limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_generation(session: AsyncSession, generation_id: int, user_id: int | None = None) -> Generation | None:
    q = select(Generation).where(Generation.id == generation_id)
    if user_id is not None:
        q = q.where(Generation.user_id == user_id)
    return await session.scalar(q)


async def toggle_favorite(session: AsyncSession, generation: Generation) -> bool:
    generation.favorite = not bool(generation.favorite)
    await session.commit()
    return bool(generation.favorite)


async def set_feedback(session: AsyncSession, generation: Generation, value: str) -> None:
    generation.feedback = value
    await session.commit()


async def list_open_tickets(session: AsyncSession, limit: int = 20) -> list[SupportTicket]:
    priority_rank = {"high": 0, "normal": 1, "low": 2}
    result = await session.execute(select(SupportTicket).where(SupportTicket.status == "open").order_by(SupportTicket.created_at.asc()).limit(limit))
    tickets = list(result.scalars().all())
    tickets.sort(key=lambda t: (priority_rank.get(t.priority, 1), t.created_at or datetime.min))
    return tickets


async def close_support_ticket(session: AsyncSession, ticket_id: int) -> bool:
    ticket = await session.get(SupportTicket, ticket_id)
    if not ticket or ticket.status != "open":
        return False
    ticket.status, ticket.closed_at = "closed", datetime.utcnow()
    await session.commit()
    return True


async def close_support_ticket_by_admin(session: AsyncSession, admin: User, ticket_id: int) -> bool:
    if not is_admin(admin):
        raise PermissionError("دسترسی بستن تیکت نداری.")
    ticket = await session.get(SupportTicket, ticket_id, with_for_update=True)
    if not ticket or ticket.status != "open":
        return False
    ticket.status, ticket.closed_at = "closed", datetime.utcnow()
    await audit_admin_action(session, admin, "TICKET_CLOSE", None, f"ticket={ticket.id}")
    await session.commit()
    return True


async def set_ticket_priority(session: AsyncSession, admin: User, ticket_id: int, priority: str) -> bool:
    if not is_admin(admin):
        raise PermissionError("دسترسی پشتیبانی نداری.")
    if priority not in {"high", "normal", "low"}:
        raise ValueError("اولویت نامعتبر است.")
    ticket = await session.get(SupportTicket, ticket_id)
    if not ticket:
        return False
    ticket.priority = priority
    await audit_admin_action(session, admin, "TICKET_PRIORITY", None, f"ticket={ticket.id}; priority={priority}")
    await session.commit()
    return True


async def assign_ticket_to_admin(session: AsyncSession, admin: User, ticket_id: int) -> bool:
    if not is_admin(admin):
        raise PermissionError("دسترسی پشتیبانی نداری.")
    ticket = await session.get(SupportTicket, ticket_id)
    if not ticket:
        return False
    ticket.assigned_admin_id = admin.id
    await audit_admin_action(session, admin, "TICKET_ASSIGN", None, f"ticket={ticket.id}; admin={admin.id}")
    await session.commit()
    return True


async def mark_ticket_first_response(session: AsyncSession, admin: User, ticket: SupportTicket) -> None:
    if ticket.first_response_at is None:
        ticket.first_response_at = datetime.utcnow()
        await audit_admin_action(session, admin, "TICKET_FIRST_RESPONSE", None, f"ticket={ticket.id}")


async def get_daily_suggestion(session: AsyncSession, user: User, suggestion_date: str) -> DailySuggestion | None:
    return await session.scalar(select(DailySuggestion).where(
        DailySuggestion.user_id == user.id, DailySuggestion.suggestion_date == suggestion_date
    ))


async def save_daily_suggestion(session: AsyncSession, user: User, suggestion_date: str, output_text: str, request_text: str = "") -> DailySuggestion:
    item = await get_daily_suggestion(session, user, suggestion_date)
    if item:
        return item
    item = DailySuggestion(user_id=user.id, suggestion_date=suggestion_date, output_text=output_text, request_text=request_text)
    session.add(item)
    await session.commit()
    return item


async def get_admin_segment_users(session: AsyncSession, segment: str, limit: int = 10000) -> list[User]:
    now = datetime.utcnow()
    cutoff7 = now - timedelta(days=7)
    cutoff30 = now - timedelta(days=30)
    stmt = select(User).where(User.is_banned.is_(False))
    if segment in PLAN_CREDITS:
        stmt = stmt.where(User.plan == segment)
    elif segment == "active_7d":
        recent_gen = exists(select(Generation.id).where(Generation.user_id == User.id, Generation.created_at >= cutoff7))
        recent_ticket = exists(select(SupportTicket.id).where(SupportTicket.user_id == User.id, SupportTicket.created_at >= cutoff7))
        stmt = stmt.where(or_(User.created_at >= cutoff7, recent_gen, recent_ticket))
    elif segment == "inactive_30d":
        recent_gen = exists(select(Generation.id).where(Generation.user_id == User.id, Generation.created_at >= cutoff30))
        recent_ticket = exists(select(SupportTicket.id).where(SupportTicket.user_id == User.id, SupportTicket.created_at >= cutoff30))
        stmt = stmt.where(User.created_at < cutoff30, not_(recent_gen), not_(recent_ticket))
    elif segment != "all":
        raise ValueError("گروه کاربری نامعتبر است.")
    result = await session.execute(stmt.order_by(User.id.asc()).limit(limit))
    return list(result.scalars().all())

# ---------------- Runtime controls ----------------
CONTROL_DEFAULTS = {"bot_enabled": True, "free_plans_enabled": True, "payments_enabled": settings.payment_enabled}


async def get_bot_control(session: AsyncSession, key: str) -> bool:
    if key not in CONTROL_DEFAULTS:
        raise ValueError("کنترل نامعتبر است.")
    item = await session.scalar(select(BotControl).where(BotControl.key == key))
    return bool(item.enabled) if item else bool(CONTROL_DEFAULTS[key])


async def set_bot_control(session: AsyncSession, admin: User, key: str, enabled: bool) -> None:
    if not can_manage_users(admin):
        raise PermissionError("دسترسی تغییر تنظیمات ربات نداری.")
    if key not in CONTROL_DEFAULTS:
        raise ValueError("کنترل نامعتبر است.")
    item = await session.scalar(select(BotControl).where(BotControl.key == key))
    if item is None:
        item = BotControl(key=key, enabled=enabled)
        session.add(item)
    else:
        item.enabled = enabled
    await audit_admin_action(session, admin, "BOT_CONTROL", None, f"{key}={enabled}")
    await session.commit()


async def bot_access_status(session: AsyncSession, user: User) -> tuple[bool, str]:
    if is_admin(user):
        return True, ""
    if not await get_bot_control(session, "bot_enabled"):
        return False, "ربات در حال حاضر برای همه کاربران موقتاً غیرفعال است. لطفاً بعداً دوباره تلاش کن."
    return True, ""


async def generation_access_status(session: AsyncSession, user: User) -> tuple[bool, str]:
    ok, reason = await bot_access_status(session, user)
    if not ok:
        return ok, reason
    if not is_admin(user) and user.plan == "free" and not await get_bot_control(session, "free_plans_enabled"):
        return False, "دسترسی تولید در پلن رایگان فعلاً غیرفعال است. برای ادامه، پلن خودت را ارتقا بده."
    return True, ""


async def payments_enabled(session: AsyncSession) -> bool:
    return bool(settings.payment_enabled and await get_bot_control(session, "payments_enabled"))


# ---------------- Admin management ----------------
ADMIN_ROLES = {"super_admin", "admin", "support"}


def admin_role(user: User) -> str | None:
    """Return effective role. The configured bootstrap admin is always super admin."""
    if settings.admin_telegram_id and user.telegram_id == settings.admin_telegram_id:
        return "super_admin"
    return user.admin_role if user.admin_role in ADMIN_ROLES else None


def is_admin(user: User) -> bool:
    return admin_role(user) is not None


def can_manage_users(user: User) -> bool:
    return admin_role(user) in {"super_admin", "admin"}


def can_manage_credits(user: User) -> bool:
    return admin_role(user) in {"super_admin", "admin"}


def can_manage_bans(user: User) -> bool:
    return admin_role(user) in {"super_admin", "admin"}


def can_manage_roles(user: User) -> bool:
    return admin_role(user) == "super_admin"


def can_manage_marketing(user: User) -> bool:
    return admin_role(user) in {"super_admin", "admin"}


async def find_users(session: AsyncSession, query: str, limit: int = 10) -> list[User]:
    query = query.strip()
    if not query:
        return []
    stmt = select(User).order_by(User.id.desc()).limit(limit)
    if query.isdigit():
        stmt = select(User).where(User.telegram_id == int(query)).limit(limit)
    else:
        pattern = f"%{query.lstrip('@')}%"
        stmt = select(User).where(
            (User.username.ilike(pattern)) | (User.first_name.ilike(pattern))
        ).order_by(User.id.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def audit_admin_action(
    session: AsyncSession,
    admin: User,
    action: str,
    target: User | None = None,
    details: str = "",
) -> AdminAuditLog:
    log = AdminAuditLog(
        admin_user_id=admin.id,
        action=action,
        target_user_id=target.id if target else None,
        details=details[:4000],
    )
    session.add(log)
    return log


async def adjust_user_credits(
    session: AsyncSession,
    admin: User,
    target: User,
    amount: int,
    reason: str,
) -> int:
    if not can_manage_credits(admin):
        raise PermissionError("دسترسی تغییر اعتبار نداری.")
    if amount == 0:
        raise ValueError("مقدار اعتبار نمی‌تواند صفر باشد.")
    if target.id == admin.id and amount < 0:
        raise ValueError("نمی‌توانی اعتبار حساب ادمین خودت را کم کنی.")
    new_balance = target.credits + amount
    if new_balance < 0:
        raise ValueError("اعتبار کاربر نمی‌تواند منفی شود.")
    target.credits = new_balance
    session.add(CreditTransaction(
        user_id=target.id,
        admin_user_id=admin.id,
        amount=amount,
        balance_after=new_balance,
        reason=reason[:255],
    ))
    await audit_admin_action(session, admin, "CREDIT_ADJUST", target, f"amount={amount}; reason={reason[:255]}; balance={new_balance}")
    await session.commit()
    return new_balance


async def set_user_plan_by_admin(
    session: AsyncSession,
    admin: User,
    target: User,
    plan: str,
) -> None:
    if not can_manage_credits(admin):
        raise PermissionError("دسترسی تغییر پلن نداری.")
    if plan not in PLAN_CREDITS:
        raise ValueError("پلن نامعتبر است.")
    if target.id == admin.id:
        raise ValueError("تغییر پلن حساب ادمین اصلی از این بخش مجاز نیست.")
    if admin_role(target) == "super_admin":
        raise ValueError("تغییر پلن Super Admin مجاز نیست.")
    old_plan = target.plan
    old_credits = target.credits
    new_credits = PLAN_CREDITS[plan]
    target.plan = plan
    target.credits = new_credits
    credit_delta = new_credits - old_credits
    if credit_delta:
        session.add(CreditTransaction(
            user_id=target.id,
            admin_user_id=admin.id,
            amount=credit_delta,
            balance_after=new_credits,
            reason=f"Admin plan change: {old_plan}->{plan}"[:255],
        ))
    await audit_admin_action(
        session, admin, "PLAN_CHANGE", target,
        f"{old_plan}->{plan}; credits {old_credits}->{new_credits}; delta={credit_delta}",
    )
    await session.commit()


async def set_user_ban(
    session: AsyncSession,
    admin: User,
    target: User,
    banned: bool,
    reason: str = "",
) -> None:
    if not can_manage_bans(admin):
        raise PermissionError("دسترسی Ban نداری.")
    if target.id == admin.id:
        raise ValueError("نمی‌توانی خودت را Ban کنی.")
    target_role = admin_role(target)
    if banned and target_role == "super_admin":
        raise ValueError("نمی‌توانی Super Admin را Ban کنی.")
    target.is_banned = banned
    target.ban_reason = reason[:255] if banned and reason else None
    target.banned_at = datetime.utcnow() if banned else None
    action = "BAN" if banned else "UNBAN"
    await audit_admin_action(session, admin, action, target, reason[:255])
    await session.commit()


async def admin_stats(session: AsyncSession) -> dict:
    now = datetime.utcnow()
    cutoff7 = now - timedelta(days=7)
    cutoff30 = now - timedelta(days=30)
    users = int(await session.scalar(select(func.count(User.id))) or 0)
    active = int(await session.scalar(select(func.count(User.id)).where(User.is_banned.is_(False))) or 0)
    banned = int(await session.scalar(select(func.count(User.id)).where(User.is_banned.is_(True))) or 0)
    generations = int(await session.scalar(select(func.count(Generation.id))) or 0)
    credits = int(await session.scalar(select(func.coalesce(func.sum(User.credits), 0))) or 0)
    tickets = int(await session.scalar(select(func.count(SupportTicket.id)).where(SupportTicket.status == "open")) or 0)
    paid_orders = int(await session.scalar(select(func.count(PaymentOrder.id)).where(PaymentOrder.status == "paid")) or 0)
    revenue_total = int(await session.scalar(select(func.coalesce(func.sum(PaymentOrder.amount_toman), 0)).where(PaymentOrder.status == "paid")) or 0)
    revenue_30d = int(await session.scalar(select(func.coalesce(func.sum(PaymentOrder.amount_toman), 0)).where(PaymentOrder.status == "paid", PaymentOrder.verified_at >= cutoff30)) or 0)
    paid_users = int(await session.scalar(select(func.count(func.distinct(PaymentOrder.user_id))).where(PaymentOrder.status == "paid")) or 0)
    avg_order = int(revenue_total / paid_orders) if paid_orders else 0
    conversion = round((paid_users / users) * 100, 1) if users else 0.0
    recent_gen = exists(select(Generation.id).where(Generation.user_id == User.id, Generation.created_at >= cutoff30))
    recent_ticket = exists(select(SupportTicket.id).where(SupportTicket.user_id == User.id, SupportTicket.created_at >= cutoff30))
    active_7d = int(await session.scalar(select(func.count(User.id)).where(User.is_banned.is_(False), or_(User.created_at >= cutoff7, exists(select(Generation.id).where(Generation.user_id == User.id, Generation.created_at >= cutoff7)), exists(select(SupportTicket.id).where(SupportTicket.user_id == User.id, SupportTicket.created_at >= cutoff7))))) or 0)
    inactive_30d = int(await session.scalar(select(func.count(User.id)).where(User.is_banned.is_(False), User.created_at < cutoff30, not_(recent_gen), not_(recent_ticket))) or 0)
    credits_consumed = int(await session.scalar(select(func.coalesce(func.sum(Generation.cost_credits), 0)).where(Generation.cost_credits > 0)) or 0)
    purchased = {}
    active_plans = {}
    for plan in PLAN_CREDITS:
        purchased[plan] = int(await session.scalar(select(func.count(PaymentOrder.id)).where(PaymentOrder.plan == plan, PaymentOrder.status == "paid")) or 0)
        active_plans[plan] = int(await session.scalar(select(func.count(User.id)).where(User.plan == plan, User.is_banned.is_(False))) or 0)
    tool_rows = await session.execute(select(Generation.kind, func.count(Generation.id), func.coalesce(func.sum(Generation.cost_credits), 0)).group_by(Generation.kind).order_by(func.sum(Generation.cost_credits).desc()))
    tool_usage = [{"kind": r[0], "count": int(r[1] or 0), "credits": int(r[2] or 0)} for r in tool_rows.all()]
    top_rows = await session.execute(select(User.id, User.telegram_id, User.username, func.coalesce(func.sum(Generation.cost_credits), 0).label("spent")).join(Generation, Generation.user_id == User.id).group_by(User.id).order_by(desc("spent")).limit(5))
    top_consumers = [{"id": int(r[0]), "telegram_id": int(r[1]), "username": r[2], "spent": int(r[3] or 0)} for r in top_rows.all()]
    return {"users": users, "active": active, "banned": banned, "generations": generations, "credits": credits, "open_tickets": tickets,
            "paid_orders": paid_orders, "revenue_total": revenue_total, "revenue_30d": revenue_30d, "paid_users": paid_users,
            "avg_order": avg_order, "conversion": conversion, "active_7d": active_7d, "inactive_30d": inactive_30d,
            "credits_consumed": credits_consumed, "purchased": purchased, "active_plans": active_plans,
            "tool_usage": tool_usage, "top_consumers": top_consumers}

