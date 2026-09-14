from __future__ import annotations

import asyncio
import html
import re
from datetime import date, datetime
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from sqlalchemy import select, func, text

from .config import settings
from .db import SessionLocal, User, Generation, DailySuggestion, CreditTransaction, AdminAuditLog, PaymentOrder, CouponRedemption, Coupon, SupportTicket
from .services import (
    GEN_COST,
    PLAN_PRICE,
    PLAN_VARIANTS,
    can_generate,
    get_or_create_user,
    get_user,
    is_admin,
    admin_role,
    can_manage_users,
    can_manage_credits,
    can_manage_bans,
    can_manage_roles,
    can_manage_marketing,
    find_users,
    get_user_by_id,
    adjust_user_credits,
    set_user_plan_by_admin,
    set_user_ban,
    admin_stats,
    profile_complete,
    record_generation,
    set_profile,
    set_profile_additional,
    PROFILE_LIMITS,
    SUPPORT_MESSAGE_LIMIT,
    support_rate_limit,
    create_support_ticket,
    list_open_tickets,
    list_user_tickets,
    list_recent_generations,
    get_generation,
    toggle_favorite,
    set_feedback,
    get_support_ticket,
    append_ticket_message,
    get_daily_suggestion,
    save_daily_suggestion,
    close_support_ticket,
    set_ticket_priority, assign_ticket_to_admin, mark_ticket_first_response, close_support_ticket_by_admin,
    get_admin_segment_users, create_coupon, list_coupons, deactivate_coupon, get_valid_coupon,
    weekly_pack_count,
    referral_reward_info,
    REFERRAL_MAX_PER_30_DAYS,
    REFERRAL_FIRST_REWARD,
    REFERRAL_REPEAT_REWARD,
    REFERRAL_INVITEE_REWARD,
    daily_free_image_limit,
    daily_free_images_remaining,
    create_payment_order,
    start_payment,
    normalize_coupon_code,
    verify_and_apply_payment,
    get_bot_control, set_bot_control, bot_access_status, generation_access_status, payments_enabled,
)
from .telegram import (
    content_menu,
    home_menu,
    length_menu,
    main_menu,
    admin_menu,
    admin_user_menu,
    admin_plan_menu,
    admin_activity_menu, admin_marketing_menu, admin_broadcast_segment_menu,
    admin_broadcast_confirm_menu, admin_coupon_menu, admin_coupon_type_menu, admin_coupon_plan_menu,
    coupon_plan_menu, studio_plus_menu,
    account_menu,
    history_menu,
    history_actions_menu,
    brief_menu,
    feedback_menu,
    brief_result_menu,
    campaign_menu,
    upgrade_menu,
    pack_menu,
    profile_menu,
    profile_flow_menu,
    profile_edit_menu,
    profile_optional_menu,
    back_home_menu,
    history_list_menu,
    support_menu,
    support_topic_menu,
    ticket_actions_menu,
    help_menu,
    remove_keyboard,
    send_message,
    delete_message,
    answer_callback,
    inline_generation_actions,
    daily_suggestion_menu,
    generation_processing_menu,
    send_photo_bytes,
    get_bot,
    close_bot,
    configure_bot_profile,
)
from .ai import generate, generate_image
from .payments import PaymentError


PENDING: dict[int, dict] = {}
BACKGROUND_TASKS: set[asyncio.Task] = set()
# Maps each user to their currently running generation task so it can be cancelled safely.
ACTIVE_GENERATION_TASKS: dict[int, asyncio.Task] = {}
# Only one AI generation may run for a Telegram user at a time.
# The current MVP uses a single polling worker, so this process-local gate is sufficient.
ACTIVE_GENERATION_USERS: set[int] = set()
PROCESSING_MESSAGES: dict[int, tuple[int, int]] = {}

KIND_LABELS = {
    "caption": "کپشن",
    "post": "پست",
    "story": "استوری",
    "reel": "سناریوی Reel",
    "product": "معرفی محصول",
    "ideas": "ایده محتوا",
    "rewrite": "بازنویسی",
    "pack": "بسته هفتگی",
    "brief": "مسیر محتوا",
    "campaign": "کمپین",
    "image": "تولید عکس",
}

CHAIN_KIND_LABELS = {
    "caption": "کپشن",
    "post": "پست",
    "story": "استوری",
    "reel": "سناریوی Reel",
    "product": "معرفی محصول",
}
CHAIN_DAY_RE = re.compile(
    r"^\s*(?:[-•🔹🔸🎬📱📝🛍️✍️]\s*)?روز\s*(?P<day>[0-9۰-۹]{1,2})\s*[:：\-–—]\s*"
    r"(?P<label>سناریوی\s+(?:Reel|ریل)|Reel|پست|استوری|کپشن|معرفی\s+محصول)"
    r"(?P<rest>.*)$", re.IGNORECASE
)
CHAIN_TOOL_RE = re.compile(
    r"^\s*(?:[-•🔹🔸🎬📱📝🛍️✍️]\s*)?"
    r"(?P<label>سناریوی\s+(?:Reel|ریل)|Reel|پست|استوری|کپشن|معرفی\s+محصول)"
    r"\s*[:：\-–—]\s*(?P<rest>.*)$", re.IGNORECASE
)

def _normalize_chain_digits(value: str) -> int:
    return int(value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))

def _chain_kind_from_label(label: str) -> str:
    normalized = re.sub(r"\s+", " ", label.strip()).lower()
    if "معرفی محصول" in normalized:
        return "product"
    if normalized == "پست":
        return "post"
    if normalized == "استوری":
        return "story"
    if normalized == "کپشن":
        return "caption"
    return "reel"

def extract_chain_items(output: str, source_kind: str) -> list[dict]:
    lines = output.splitlines()
    matches: list[tuple[int, re.Match[str], bool]] = []
    for idx, line in enumerate(lines):
        match = CHAIN_DAY_RE.match(line)
        if match:
            matches.append((idx, match, True))
        elif source_kind == "campaign":
            match = CHAIN_TOOL_RE.match(line)
            if match:
                matches.append((idx, match, False))
    items: list[dict] = []
    for position, (start, match, is_day) in enumerate(matches):
        if source_kind == "pack" and not is_day:
            continue
        end = matches[position + 1][0] if position + 1 < len(matches) else len(lines)
        kind = _chain_kind_from_label(match.group("label"))
        if source_kind == "pack":
            day = _normalize_chain_digits(match.group("day"))
            if not 1 <= day <= 7:
                continue
        else:
            day = None
        rest = re.sub(r"^\s*[:：\-–—]\s*", "", match.group("rest")).strip()
        body_lines = ([rest] if rest else []) + [line.strip() for line in lines[start + 1:end] if line.strip()]
        source = "\n".join(body_lines).strip()
        if not source:
            continue
        items.append({"index": len(items), "start": start, "end": end, "kind": kind, "label": CHAIN_KIND_LABELS[kind], "day": day, "source": source, "is_day": is_day})
    return items

def render_chain_output(output: str, generation_id: int, source_kind: str, premium: bool = False) -> str:
    lines = output.splitlines()
    items = extract_chain_items(output, source_kind)
    if not items:
        return html.escape(output) + ("\n\n⭐" if premium else "")
    replacements: dict[int, str] = {}
    for item in items:
        match = (CHAIN_DAY_RE if item["is_day"] else CHAIN_TOOL_RE).match(lines[item["start"]])
        if not match:
            continue
        url = f"https://t.me/{settings.bot_username.lstrip('@')}?start=chain_{generation_id}_{item['index']}_{1 if premium else 0}"
        anchor_html = f'<a href="{html.escape(url, quote=True)}">{html.escape(item["label"])}</a>'
        replacements[item["start"]] = html.escape(lines[item["start"]]).replace(html.escape(match.group("label")), anchor_html, 1)
    return "\n".join(replacements.get(i, html.escape(line)) for i, line in enumerate(lines)) + ("\n\n⭐" if premium else "")

ADMIN_SEGMENTS = {
    "👥 همه کاربران": "all",
    "🆓 رایگان": "free",
    "🟦 استارتر": "starter",
    "🟪 پرو": "pro",
    "🟫 استودیو": "studio",
    "✨ استودیو پلاس": "studio_plus",
    "🔥 فعال ۷ روز اخیر": "active_7d",
    "💤 غیرفعال ۳۰ روزه": "inactive_30d",
}

PLAN_LABELS = {"free": "رایگان", "starter": "استارتر", "pro": "پرو", "studio": "استودیو", "studio_plus": "استودیو پلاس"}
PRIORITY_LABELS = {"high": "🔴 بالا", "normal": "🟡 عادی", "low": "🟢 پایین"}

BUTTON_TO_KIND = {
    "کپشن": "caption",
    "پست": "post",
    "استوری": "story",
    "سناریوی Reel": "reel",
    "معرفی محصول": "product",
    "متن فروش": "product",  # legacy input compatibility; not shown in UI
}

LENGTH_BUTTONS = {
    "کوتاه": "short",
    "متوسط": "medium",
    "طولانی": "long",
}

DEFAULT_LENGTHS = {
    "post": "medium",
    "story": "short",
    "reel": "medium",
    "product": "medium",
}

CONTENT_DAILY_LIMIT = 3

# Keep user requests and generated outputs bounded per tool so prompts stay focused
# and Telegram/AI context is not wasted.
INPUT_LIMITS = {
    "caption": 1200,
    "post": 1600,
    "story": 1600,
    "reel": 1800,
    "product": 1200,
    "ideas": 1600,
    "rewrite": 3000,
    "brief": 1800,
    "pack": 2200,
    "campaign": 2200,
    "image": 1200,
}

def requested_variants(text: str, kind: str) -> int:
    # All tools default to one output; two outputs are explicit.
    one_markers = ("یک نسخه", "۱ نسخه", "1 نسخه", "فقط یکی", "فقط یک خروجی", "یک خروجی")
    two_markers = ("دو نسخه", "۲ نسخه", "2 نسخه", "دو تا", "دوتا", "دو خروجی", "۲ خروجی", "2 خروجی")
    if any(marker in text for marker in one_markers):
        return 1
    return 2 if any(marker in text for marker in two_markers) else 1


GENERIC_TOOL_REQUEST_MESSAGE = (
    "موضوع، محصول، هدف و هر اطلاعاتی که می‌خواهی در محتوا استفاده شود را بنویس. "
    "اگر طول خاصی می‌خواهی، همان‌جا بگو."
)
REWRITE_REQUEST_MESSAGE = (
    "متن یا درخواستت را بفرست؛ اگر برای ابزار AI دیگری پرامپت می‌خواهی، "
    "بنویس «پرامپت بنویس برای ...». "
)
IDEAS_REQUEST_MESSAGE = (
    "موضوع، محصول یا هدفت را بنویس تا یک ایده مشخص و مسیر اجرایی کوتاه و کامل برات بچینم. 💡"
)
CAMPAIGN_REQUEST_MESSAGE = (
    "هدف کمپینت را بنویس؛ مثلاً معرفی محصول، فروش ویژه یا یک مناسبت. 🚀"
)
PACK_REQUEST_MESSAGE = "برای بسته ۷ روزه، محصول، موضوع یا هدف اصلی را بنویس."
IMAGE_REQUEST_MESSAGE = (
    "توضیح تصویری که می‌خواهی را بنویس. مثلاً: یک عکس مینیمال از شلوار مشکی زنانه برای پست اینستاگرام."
)

def tool_request_message(kind: str) -> str:
    return {
        "rewrite": REWRITE_REQUEST_MESSAGE,
        "ideas": IDEAS_REQUEST_MESSAGE,
        "campaign": CAMPAIGN_REQUEST_MESSAGE,
        "pack": PACK_REQUEST_MESSAGE,
        "image": IMAGE_REQUEST_MESSAGE,
    }.get(kind, GENERIC_TOOL_REQUEST_MESSAGE)


SUPPORT_TOPICS = {
    "💳 مشکل پرداخت": "مشکل پرداخت",
    "⚙️ مشکل فنی": "مشکل فنی",
    "🎟️ مشکل اعتبار یا پلن": "مشکل اعتبار یا پلن",
    "💡 پیشنهاد یا انتقاد": "پیشنهاد یا انتقاد",
    "❓ سایر موارد": "سایر موارد",
}

HELP_TEXTS = {
    "✍️ راهنمای تولید محتوا": "✍️ تولید محتوا\n\nنوع محتوای موردنظر را انتخاب کن. همه ابزارها به‌صورت پیش‌فرض یک نسخه می‌دهند؛ اگر دو نسخه بخواهی، کافی است صریح بگویی. طول پیش‌فرض متناسب با هر ابزار تنظیم می‌شود و برای کپشن هم می‌توانی طول دلخواهت را داخل درخواست بنویسی.\n\n💡 نکته: وقتی یک خروجی کافی است، همان یک نسخه را بخواه.",
    "🧭 راهنمای مسیر محتوا": "🧭 مسیر محتوا\n\nلازم نیست بدانی کدام ابزار را انتخاب کنی. کافی است محصول، هدف یا مناسبتت را توضیح بده؛ محتواچی پیشنهاد می‌دهد چه فرمتی و چه ایده‌ای مناسب‌تر است و از همان‌جا می‌توانی وارد تولید شوی.\n\n💡 نکته: درخواستت را دقیق بنویس تا خروجی‌های اضافی کمتر شود.",
    "📦 راهنمای بسته هفتگی": "📦 بسته هفتگی\n\nبرای یک هدف مشخص، یک برنامه منسجم ۷ روزه می‌سازد؛ محتواها به هم مرتبط‌اند و تکراری نیستند. پلن رایگان هفته‌ای ۱ بسته دارد.\n\n💡 نکته: قبل از ساخت بسته، هدفت را دقیق مشخص کن تا همان یک بسته بیشتر به کارت بیاید.",
    "💡 راهنمای ایده محتوا": "💡 ایده محتوا\n\nموضوع یا هدفت را بده. محتواچی یک ایده مرکزی را واقعاً پرورش می‌دهد: زاویه، روایت، Hook یا جزئیات اجرایی لازم. لازم نیست حتماً شماره‌گذاری یا مرحله‌بندی ثابت داشته باشد. پلن رایگان روزی ۱ ایده دارد.\n\n💡 نکته: اگر ایده‌ای مناسب بود، به‌جای ساخت ایده جدید آن را بازنویسی یا تبدیل کن.",
    "📚 راهنمای محتوای من": "📚 محتوای من\n\nخروجی‌های اخیرت را ببین، موارد مهم را ذخیره کن و یک خروجی قبلی را برای بازنویسی یا ادامه مسیر دوباره استفاده کن.\n\n💡 نکته: از خروجی‌های قبلی بازنویسی یا تبدیل بگیر تا لازم نباشد از صفر تولید کنی.",
    "🚀 راهنمای کمپین": "🚀 کمپین\n\nبرای یک هدف مشخص مثل فروش، معرفی محصول یا مناسبت، یک کمپین کوچک و منسجم طراحی می‌کند و نقش هر محتوای اصلی و پشتیبان را مشخص می‌کند.\n\n💡 نکته: قبل از ساخت کمپین، هدف و پیشنهاد اصلی را مشخص کن تا خروجی هدفمندتری بگیری.",
    "🔄 راهنمای بازنویسی": "🔄 بازنویسی\n\nمتنت را بفرست تا با حفظ معنی و اطلاعات اصلی، روان‌تر و حرفه‌ای‌تر شود. پیش‌فرض یک نسخه است.\n\n💡 نکته: به‌جای تولید متن جدید، متن قبلی را بازنویسی کن.",
    "👤 راهنمای پروفایل برند": "👤 پروفایل برند\n\nحوزه فعالیت، مخاطب و لحن برندت بخش‌های اصلی و اجباری پروفایل‌اند. بعد از تکمیل آن‌ها می‌توانی اطلاعات تکمیلی مثل محصول/خدمت اصلی، تفاوت برند و هدف محتوا را هم اضافه کنی؛ این اطلاعات در خروجی‌ها استفاده می‌شوند.\n\n💡 نکته: پروفایل را دقیق کامل کن تا کمتر مجبور شوی درخواستت را دوباره توضیح بدهی.",
    "🎟️ راهنمای اعتبار": "🎟️ اعتبار\n\nهر بار که یک قابلیت تولید محتوا را اجرا می‌کنی، مقداری از اعتبارت مصرف می‌شود.\n\n🔹 هزینه‌های پلن‌های عادی: کپشن ۲ | پست ۴ | استوری ۴ | Reel ۴ | معرفی محصول ۴ | ایده محتوا ۲ | بازنویسی ۲ | مسیر محتوا ۲ | کمپین ۷ | بسته هفتگی ۷ | تولید عکس ۱۰ اعتبار.\n\n✨ هزینه‌های Studio+: کپشن ۴ | پست ۷ | استوری ۷ | Reel ۸ | معرفی محصول ۷ | ایده محتوا ۳ | بازنویسی ۶ | مسیر محتوا ۴ | کمپین ۱۵ | بسته هفتگی ۱۵ | عکس بعد از سهمیه رایگان ۲۰ اعتبار.\n\n🖼️ Studio+ روزانه ۱ عکس رایگان دارد؛ پس از مصرف سهمیه روزانه، هر عکس ۲۰ اعتبار مصرف می‌کند.\n\nدو نسخه‌ای که داخل یک درخواست مجاز هستند هزینه جداگانه ندارند؛ یعنی مثلاً دو کپشن همان هزینه یک کپشن را مصرف می‌کنند.\n\n🎁 اعتبار دعوت دوستان و پاداش‌های وفاداری هم به موجودی اضافه می‌شوند.",
}



@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await close_bot()


app = FastAPI(title="Mavazegar AI Telegram Bot", lifespan=lifespan)


def home_suggestion(user) -> str:
    return "خانه 🏠"

def history_hint(text: str) -> str:
    """Return a tiny, safe preview for history rows without changing the saved content."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^[#*_`\-–—\s]+", "", line)
        if not line:
            continue
        if len(line) > 42:
            line = line[:42].rstrip() + "…"
        return line
    return "بدون متن"


def profile_complete_sync(user) -> bool:
    return bool(user.niche and user.audience and user.tone)

def start_text() -> str:
    return (
        "سلام 👋\n\n"
        "به «محتواچی» خوش اومدی؛ کارخونه‌ی محتوای برندت داخل تلگرام. ✨\n\n"
        "محتواچی برای تولید محتوا ساخته شده، نه چت عمومی.\n\n"
        "🚀 مهم‌ترین ابزارها:\n"
        "• ✍️ کپشن، پست و معرفی محصول\n"
        "• 📱 استوری و سناریوی Reel\n"
        "• 💡 ایده‌های محتوایی و پیشنهاد روزانه\n"
        "• 📦 بسته هفتگی و کمپین محتوا\n"
        "• 🖼️ تولید تصویر برای محتوا\n"
        "• 🔄 بازنویسی و تبدیل محتوا\n\n"
        "👤 اول پروفایل برندت رو کامل کن تا خروجی‌ها بر اساس حوزه، مخاطب و لحن برند خودت ساخته بشن."
    )


@app.get("/payment/callback")
async def payment_callback(request: Request):
    authority = str(request.query_params.get("Authority") or "").strip()
    status = str(request.query_params.get("Status") or "").strip()
    if not authority:
        raise HTTPException(status_code=400, detail="شناسه پرداخت دریافت نشد.")

    async with SessionLocal() as session:
        order = await session.scalar(select(PaymentOrder).where(PaymentOrder.authority == authority))
        if not order:
            raise HTTPException(status_code=404, detail="سفارش پرداخت پیدا نشد.")
        result, ref_id = await verify_and_apply_payment(
            session, order, authority=authority, status=status
        )

    messages = {
        "paid": "پرداخت با موفقیت تأیید شد. پلن شما فعال شد.",
        "already_paid": "این پرداخت قبلاً تأیید شده است.",
        "cancelled": "پرداخت لغو شد یا با موفقیت تکمیل نشد.",
        "verify_failed": "تأیید پرداخت فعلاً انجام نشد؛ سفارش برای تلاش مجدد باز مانده است. اگر مبلغ از حساب شما کم شده، دوباره پرداخت نکن.",
        "failed": "این سفارش دیگر قابل پردازش نیست.",
    }
    text = messages.get(result, "وضعیت پرداخت مشخص نیست.")
    if ref_id:
        text += f"\nکد رهگیری: {ref_id}"
    return {"ok": result in {"paid", "already_paid"}, "result": result, "message": text}


@app.get("/health")
async def health():
    # Liveness endpoint: confirms only that the API process is running.
    return {"ok": True}


@app.get("/ready")
async def ready():
    # Readiness endpoint: confirms that the application can reach the database.
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="not ready")
    return {"ok": True}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_webhook_secret: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
):
    if settings.webhook_secret and x_webhook_secret != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="invalid secret")
    update = await request.json()
    await handle_update(update)
    return {"ok": True}


async def safe_send(chat_id: int, text: str, markup: dict | None = None, parse_mode: str | None = None) -> dict | None:
    # Telegram message limit is 4096 chars. Keep a safety margin.
    limit = 3800
    chunks = [text[i:i + limit] for i in range(0, len(text), limit)] or [""]
    last_message = None
    for index, chunk in enumerate(chunks):
        last_message = await send_message(chat_id, chunk, markup if index == len(chunks) - 1 else None, parse_mode=parse_mode)
    return last_message


async def send_processing_message(telegram_id: int, chat_id: int, text: str, markup: dict | None = None) -> None:
    message = await safe_send(chat_id, text, markup)
    message_id = message.get("message_id") if message else None
    if message_id:
        PROCESSING_MESSAGES[telegram_id] = (chat_id, message_id)


async def clear_processing_message(telegram_id: int) -> None:
    item = PROCESSING_MESSAGES.pop(telegram_id, None)
    if not item:
        return
    chat_id, message_id = item
    await delete_message(chat_id, message_id)


def start_background(coro, telegram_id: int | None = None) -> bool:
    """Start a background task, enforcing one active AI job per user."""
    if telegram_id is not None:
        if telegram_id in ACTIVE_GENERATION_USERS:
            # The coroutine was already constructed by the caller; close it so Python
            # does not emit an un-awaited coroutine warning.
            coro.close()
            return False
        ACTIVE_GENERATION_USERS.add(telegram_id)

    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    if telegram_id is not None:
        ACTIVE_GENERATION_TASKS[telegram_id] = task

    def _cleanup(done_task: asyncio.Task) -> None:
        BACKGROUND_TASKS.discard(done_task)
        if telegram_id is not None:
            ACTIVE_GENERATION_USERS.discard(telegram_id)
            if ACTIVE_GENERATION_TASKS.get(telegram_id) is done_task:
                ACTIVE_GENERATION_TASKS.pop(telegram_id, None)

    task.add_done_callback(_cleanup)
    return True


async def run_broadcast(admin_id: int, segment: str, message: str) -> None:
    """Send an admin broadcast outside the request session and report a summary."""
    try:
        async with SessionLocal() as session:
            admin = await session.get(User, admin_id)
            if not admin or not can_manage_marketing(admin):
                return
            users = await get_admin_segment_users(session, segment, 5000)
            recipient_count = len(users)

        sent = 0
        failed = 0
        for target in users:
            try:
                await send_message(target.telegram_id, message)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.06)

        async with SessionLocal() as session:
            admin = await session.get(User, admin_id)
            if admin:
                await audit_admin_action(
                    session, admin, "BROADCAST", None,
                    f"segment={segment}; recipients={recipient_count}; sent={sent}; failed={failed}",
                )
                await session.commit()
                await safe_send(
                    admin.telegram_id,
                    f"📣 ارسال تمام شد.\n\n👥 گیرندگان: {recipient_count:,}\n✅ ارسال موفق: {sent:,}\n⚠️ ناموفق: {failed:,}",
                    admin_menu(admin_role(admin) or "admin"),
                )
    except Exception as exc:
        print(f"Broadcast error: {type(exc).__name__}: {exc}")

async def cancel_active_generation(telegram_id: int) -> bool:
    """Cancel the user's current AI task, if one is still running."""
    task = ACTIVE_GENERATION_TASKS.get(telegram_id)
    if not task or task.done():
        return False
    task.cancel()
    return True


async def handle_update(update: dict) -> None:
    callback_query = update.get("callback_query")
    if callback_query:
        await handle_callback_update(callback_query)
        return
    message = update.get("message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    tg_user = message.get("from", {})
    text = (message.get("text") or "").strip()
    parts = text.split()

    start_payload = parts[1] if text.startswith("/start") and len(parts) > 1 else None
    referral_code = start_payload.removeprefix("ref_") if start_payload and start_payload.startswith("ref_") else None

    async with SessionLocal() as session:
        user, _ = await get_or_create_user(
            session,
            tg_user["id"],
            tg_user.get("username"),
            tg_user.get("first_name"),
            referral_code,
        )

        access_ok, access_reason = await bot_access_status(session, user)
        if not access_ok:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, access_reason, main_menu())
            return

        if user.is_banned and not is_admin(user):
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "دسترسی این حساب به ربات مسدود شده است.", remove_keyboard())
            return

        if is_admin(user) and text in {"/admin", "🛠️ پنل مدیریت"}:
            PENDING.pop(user.telegram_id, None)
            role = admin_role(user) or "admin"
            await safe_send(chat_id, f"🛠️ پنل مدیریت\n\nسطح دسترسی: {role}", admin_menu(role))
            return

        if is_admin(user) and text == "/admin_stats":
            if not can_manage_users(user):
                await safe_send(chat_id, "دسترسی کافی نداری.", admin_menu(admin_role(user) or "admin"))
                return
            stats = await admin_stats(session)
            await safe_send(chat_id, (
                "📊 آمار\n\n"
                f"👤 کاربران: {stats['users']}\n"
                f"🟢 فعال: {stats['active']}\n"
                f"🚫 مسدود: {stats['banned']}\n"
                f"✨ تولیدها: {stats['generations']}\n"
                f"💎 مجموع اعتبار موجود کاربران: {stats['credits']}\n"
                f"🎫 تیکت باز: {stats['open_tickets']}"
            ), admin_menu(admin_role(user) or "admin"))
            return

        if is_admin(user) and text == "⚙️ فعالیت ربات":
            if not can_manage_users(user):
                await safe_send(chat_id, "دسترسی کافی نداری.", admin_menu(admin_role(user) or "admin"))
                return
            status = {"bot_enabled": await get_bot_control(session, "bot_enabled"), "free_plans_enabled": await get_bot_control(session, "free_plans_enabled"), "payments_enabled": await payments_enabled(session)}
            await safe_send(chat_id, "⚙️ فعالیت ربات\n\n"
                f"🤖 ربات: {'فعال' if status['bot_enabled'] else 'غیرفعال'}\n"
                f"🆓 پلن‌های رایگان: {'فعال' if status['free_plans_enabled'] else 'غیرفعال'}\n"
                f"💳 پرداخت: {'فعال' if status['payments_enabled'] else 'غیرفعال'}", admin_activity_menu(status))
            return

        if is_admin(user) and text == "↩️ فعالیت ربات":
            if not can_manage_users(user):
                await safe_send(chat_id, "دسترسی کافی نداری.", admin_menu(admin_role(user) or "admin"))
                return
            status = {"bot_enabled": await get_bot_control(session, "bot_enabled"), "free_plans_enabled": await get_bot_control(session, "free_plans_enabled"), "payments_enabled": await payments_enabled(session)}
            await safe_send(chat_id, "⚙️ فعالیت ربات", admin_activity_menu(status))
            return

        if is_admin(user) and (text.startswith("🤖 ربات:") or text.startswith("🆓 پلن‌های رایگان:") or text.startswith("💳 پرداخت:")):
            if not can_manage_users(user):
                await safe_send(chat_id, "دسترسی کافی نداری.", admin_menu(admin_role(user) or "admin"))
                return
            key = "bot_enabled" if text.startswith("🤖 ربات:") else ("free_plans_enabled" if text.startswith("🆓 پلن‌های رایگان:") else "payments_enabled")
            current = await (payments_enabled(session) if key == "payments_enabled" else get_bot_control(session, key))
            if key == "payments_enabled" and not settings.payment_enabled and not current:
                status = {"bot_enabled": await get_bot_control(session, "bot_enabled"), "free_plans_enabled": await get_bot_control(session, "free_plans_enabled"), "payments_enabled": False}
                await safe_send(chat_id, "فعال‌سازی پرداخت از پنل ممکن نیست تا زمانی که PAYMENT_ENABLED=true در تنظیمات سرور قرار بگیرد.", admin_activity_menu(status))
                return
            await set_bot_control(session, user, key, not current)
            status = {"bot_enabled": await get_bot_control(session, "bot_enabled"), "free_plans_enabled": await get_bot_control(session, "free_plans_enabled"), "payments_enabled": await payments_enabled(session)}
            await safe_send(chat_id, f"تنظیم «{key}» روی {'فعال' if not current else 'غیرفعال'} قرار گرفت. {'⚠️' if key == 'payments_enabled' and not current and not settings.payment_enabled else '✅'}", admin_activity_menu(status))
            return

        if is_admin(user) and text == "👮 مدیریت نقش‌ها":
            if not can_manage_roles(user):
                await safe_send(chat_id, "این بخش فقط برای Super Admin است.", admin_menu(admin_role(user) or "admin"))
                return
            PENDING[user.telegram_id] = {"step": "admin_role_search"}
            await safe_send(chat_id, "Telegram ID یا username ادمین را بفرست.", admin_menu(admin_role(user) or "admin"))
            return

        if is_admin(user) and text == "👤 کاربران":
            if not can_manage_users(user):
                await safe_send(chat_id, "دسترسی کافی نداری.", admin_menu(admin_role(user) or "admin"))
                return
            PENDING[user.telegram_id] = {"step": "admin_user_search"}
            await safe_send(chat_id, "Telegram ID یا username کاربر را بفرست.", admin_menu(admin_role(user) or "admin"))
            return

        if is_admin(user) and text == "📊 آمار":
            if not can_manage_users(user):
                await safe_send(chat_id, "دسترسی کافی نداری.", admin_menu(admin_role(user) or "admin"))
                return
            stats = await admin_stats(session)
            plan_lines = ["", "💰 پلن‌ها", "خرید موفق از ابتدا | فعال فعلی"]
            for plan in ("free", "starter", "pro", "studio", "studio_plus"):
                plan_lines.append(f"{PLAN_LABELS[plan]}: {stats['purchased'][plan]} خرید | {stats['active_plans'][plan]} فعال")
            usage_lines = ["", "🤖 مصرف و فعالیت"]
            for row in stats["tool_usage"][:6]:
                usage_lines.append(f"{KIND_LABELS.get(row['kind'], row['kind'])}: {row['count']} تولید | {row['credits']:,} اعتبار")
            top_lines = ["", "🔥 بیشترین مصرف اعتبار"]
            if stats["top_consumers"]:
                for row in stats["top_consumers"]:
                    name = f"@{row['username']}" if row['username'] else str(row['telegram_id'])
                    top_lines.append(f"#{row['id']} {name}: {row['spent']:,}")
            else:
                top_lines.append("هنوز مصرفی ثبت نشده.")
            await safe_send(chat_id, (
                "📊 داشبورد مدیریت\n\n"
                f"👥 کاربران: {stats['users']} | 🟢 فعال: {stats['active']} | 🚫 مسدود: {stats['banned']}\n"
                f"🔥 فعال ۷ روز اخیر: {stats['active_7d']} | 💤 غیرفعال ۳۰ روزه: {stats['inactive_30d']}\n"
                f"💳 خرید موفق: {stats['paid_orders']} | 👤 خریدار یکتا: {stats['paid_users']} | 📈 تبدیل: {stats['conversion']}%\n"
                f"💰 درآمد کل: {stats['revenue_total']:,} تومان\n"
                f"💰 درآمد ۳۰ روز اخیر: {stats['revenue_30d']:,} تومان\n"
                f"🧾 میانگین خرید موفق: {stats['avg_order']:,} تومان\n"
                f"✨ تولیدها: {stats['generations']} | 💎 اعتبار باقی‌مانده کاربران: {stats['credits']:,}\n"
                f"💎 اعتبار مصرف‌شده: {stats['credits_consumed']:,} | 🎫 تیکت باز: {stats['open_tickets']}\n"
                + "\n".join(plan_lines) + "\n" + "\n".join(usage_lines) + "\n" + "\n".join(top_lines)
            ), admin_menu(admin_role(user) or "admin"))
            return

        if is_admin(user) and text == "📜 گزارش عملیات":
            if not can_manage_roles(user):
                await safe_send(chat_id, "این بخش فقط برای Super Admin است.", admin_menu(admin_role(user) or "admin"))
                return
            logs = (await session.execute(select(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(15))).scalars().all()
            lines = ["📜 آخرین عملیات ادمین:", ""]
            for log in logs:
                target_label = f"#{log.target_user_id}" if log.target_user_id else "-"
                lines.append(f"{log.action} — {target_label} — {log.created_at:%m/%d %H:%M}")
            await safe_send(chat_id, "\n".join(lines) if logs else "📜 هنوز عملیاتی ثبت نشده.", admin_menu(admin_role(user) or "admin"))
            return

        if is_admin(user) and text == "🎫 تیکت‌ها":
            tickets = await list_open_tickets(session, 30)
            lines = ["🎫 تیکت‌های باز", ""]
            for ticket in tickets:
                ticket_user = await session.get(User, ticket.user_id)
                name = ticket_user.first_name if ticket_user else "کاربر"
                assigned = f" | 👤 #{ticket.assigned_admin_id}" if ticket.assigned_admin_id else ""
                lines.append(f"#{ticket.id} — {PRIORITY_LABELS.get(ticket.priority, '🟡 عادی')} — {name} — {ticket.topic}{assigned}")
            if not tickets:
                lines.append("تیکت بازی وجود ندارد. ✅")
            else:
                lines.append("\nبرای دیدن جزئیات، شماره را مثل #12 بفرست.")
            PENDING[user.telegram_id] = {"step": "admin_ticket_select"} if tickets else {}
            await safe_send(chat_id, "\n".join(lines), admin_menu(admin_role(user) or "admin"))
            return

        if is_admin(user) and text == "📣 بازاریابی":
            if not can_manage_marketing(user):
                await safe_send(chat_id, "دسترسی بازاریابی نداری.", admin_menu(admin_role(user) or "admin"))
                return
            PENDING[user.telegram_id] = {"step": "admin_marketing"}
            await safe_send(chat_id, "📣 بازاریابی\n\nیکی از ابزارهای زیر را انتخاب کن.", admin_marketing_menu())
            return

        if is_admin(user) and text == "/tickets":
            tickets = await list_open_tickets(session)
            if not tickets:
                await safe_send(chat_id, "تیکت باز نداریم. ✅", main_menu())
                return
            lines = ["🆘 تیکت‌های باز:", ""]
            for ticket in tickets:
                ticket_user = await session.get(User, ticket.user_id)
                name = ticket_user.first_name if ticket_user else "کاربر"
                lines.append(f"#{ticket.id} — {ticket.topic} — {name} — {ticket.created_at:%Y-%m-%d %H:%M}")
            lines.append("\nبرای بستن: /close_ticket شماره")
            await safe_send(chat_id, "\n".join(lines), main_menu())
            return

        if is_admin(user) and text.startswith("/close_ticket"):
            parts_close = text.split()
            if len(parts_close) != 2 or not parts_close[1].isdigit():
                await safe_send(chat_id, "فرمت درست: /close_ticket شماره_تیکت", main_menu())
                return
            try:
                closed = await close_support_ticket_by_admin(session, user, int(parts_close[1]))
            except PermissionError as exc:
                await safe_send(chat_id, str(exc), main_menu())
                return
            await safe_send(chat_id, "تیکت بسته شد ✅" if closed else "تیکت پیدا نشد یا قبلاً بسته شده است.", main_menu())
            return

        if is_admin(user) and text.startswith("/reply_ticket"):
            parts_reply = text.split(maxsplit=2)
            if len(parts_reply) == 2 and parts_reply[1].isdigit():
                ticket = await get_support_ticket(session, int(parts_reply[1]))
                if not ticket:
                    await safe_send(chat_id, "تیکت پیدا نشد.", main_menu())
                    return
                PENDING[user.telegram_id] = {"step": "admin_reply", "ticket_id": ticket.id}
                await safe_send(chat_id, f"✉️ پاسخ به تیکت #{ticket.id} را بنویس:", main_menu())
                return
            if len(parts_reply) == 3 and parts_reply[1].isdigit():
                ticket = await get_support_ticket(session, int(parts_reply[1]))
                if not ticket:
                    await safe_send(chat_id, "تیکت پیدا نشد.", main_menu())
                    return
                await mark_ticket_first_response(session, user, ticket)
                await append_ticket_message(session, ticket, "admin", parts_reply[2])
                target = await session.get(User, ticket.user_id)
                if target:
                    await safe_send(target.telegram_id, f"🛠️ پاسخ پشتیبانی به تیکت #{ticket.id}\n\n{parts_reply[2]}", main_menu())
                await safe_send(chat_id, f"پاسخ تیکت #{ticket.id} ارسال شد. ✅", main_menu())
                return

        if text.startswith("👍") or text.startswith("👎"):
            m = re.search(r"#(\d+)", text)
            if m:
                g = await get_generation(session, int(m.group(1)), user.id)
                if g:
                    await set_feedback(session, g, "positive" if text.startswith("👍") else "negative")
                    await safe_send(chat_id, "بازخوردت ثبت شد؛ ممنون. 🙌", main_menu())
                    return

        if start_payload and start_payload.startswith("chain_"):
            chain_match = re.fullmatch(r"chain_(\d+)_(\d+)_(0|1)", start_payload)
            if not chain_match:
                await safe_send(chat_id, "این لینک تولید محتوا معتبر نیست.", main_menu())
                return
            source_generation = await get_generation(session, int(chain_match.group(1)), user.id)
            item_index = int(chain_match.group(2))
            premium = chain_match.group(3) == "1"
            if not source_generation or source_generation.kind not in {"pack", "campaign"}:
                await safe_send(chat_id, "این محتوای زنجیره‌ای پیدا نشد یا دیگر در دسترس نیست.", main_menu())
                return
            if user.telegram_id in ACTIVE_GENERATION_USERS:
                await safe_send(chat_id, "⏳ یک درخواست هنوز در حال انجام است. لطفاً تا آماده‌شدن نتیجه صبر کن.", home_menu())
                return
            items = extract_chain_items(source_generation.output_text, source_generation.kind)
            if item_index < 0 or item_index >= len(items):
                await safe_send(chat_id, "این بخش از محتوا دیگر قابل استفاده نیست.", main_menu())
                return
            item = items[item_index]
            if not await profile_complete(user):
                await safe_send(chat_id, "برای استفاده از این ابزار، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
                return
            ok, reason = await can_generate(session, user, item["kind"], premium=premium)
            if not ok:
                await safe_send(chat_id, reason, main_menu())
                return
            PENDING[user.telegram_id] = {"step": "processing", "kind": item["kind"], "premium": premium}
            await send_processing_message(user.telegram_id, chat_id, f"دارم {KIND_LABELS[item['kind']]} را از این بخش می‌سازم... ✨", generation_processing_menu())
            start_background(process_generation(user.telegram_id, chat_id, item["kind"], item["source"], DEFAULT_LENGTHS.get(item["kind"], "medium"), 1, premium), user.telegram_id)
            return
        if text.startswith("/start"):
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, start_text(), main_menu())
            return

        # Telegram slash commands are shortcuts to the same destinations as the
        # corresponding main-menu buttons. They intentionally override any
        # pending flow so a user can jump directly to another section.
        command = text.split()[0].split("@", 1)[0] if text.startswith("/") else ""
        if command == "/admin":
            if not is_admin(user):
                await safe_send(chat_id, "این دستور فقط برای ادمین است.", main_menu())
                return
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, f"🛠️ پنل مدیریت\n\nسطح دسترسی: {admin_role(user) or 'admin'}", admin_menu(admin_role(user) or 'admin'))
            return
        command_routes = {
            "/home": "خانه",
            "/content": "✍️ تولید محتوا",
            "/ideas": "💡 ایده محتوا",
            "/campaign": "🚀 کمپین",
            "/image": "🖼️ تولید عکس",
            "/profile": "👤 پروفایل برند",
            "/history": "🕘 اخیر",
            "/favorites": "⭐ ذخیره‌شده‌ها",
            "/credits": "📊 وضعیت من",
            "/referral": "🎁 دعوت دوستان",
            "/support": "🆘 پشتیبانی",
        }
        if command == "/rewrite":
            if not await profile_complete(user):
                await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
                return
            PENDING[user.telegram_id] = {"step": "rewrite_request", "kind": "rewrite"}
            await safe_send(chat_id, REWRITE_REQUEST_MESSAGE, back_home_menu())
            return
        if command in command_routes:
            text = command_routes[command]

        if text in {"↩️ برگشت", "بازگشت"}:
            state = PENDING.get(user.telegram_id)
            if is_admin(user) and state and state.get("step", "").startswith("admin_"):
                if await handle_admin_back(session, user, chat_id, state):
                    return
            if state:
                step = state.get("step")
                if step in {"coupon_code", "coupon_select_plan"}:
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به صفحه ارتقا برگشتی.", upgrade_menu())
                    return
                if step == "niche":
                    if state.get("editing"):
                        PENDING.pop(user.telegram_id, None)
                        await safe_send(chat_id, "به پروفایل برند برگشتی.", profile_menu())
                    else:
                        PENDING.pop(user.telegram_id, None)
                        await safe_send(chat_id, "به صفحه اصلی برگشتی. 👋", main_menu())
                    return
                if step == "audience":
                    state["step"] = "niche"
                    await safe_send(chat_id, "به مرحله قبل برگشتی. حوزه فعالیت برندت چیست؟", profile_edit_menu() if state.get("editing") else home_menu())
                    return
                if step == "tone":
                    state["step"] = "audience"
                    await safe_send(chat_id, "به مرحله قبل برگشتی. مخاطب اصلی برندت کیست؟", back_home_menu())
                    return
                if step == "kind":
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به صفحه اصلی برگشتی. 👋", main_menu())
                    return
                if step == "length":
                    state["step"] = "kind"
                    await safe_send(chat_id, "نوع محتوا را انتخاب کن:", content_menu())
                    return
                if step == "request":
                    PENDING[user.telegram_id] = {"step": "kind"}
                    await safe_send(chat_id, "به انتخاب نوع محتوا برگشتی.", content_menu())
                    return
                if step in {"rewrite_request", "transform_confirm", "brief_request"} and state.get("return_generation_id"):
                    g = await get_generation(session, state["return_generation_id"], user.id)
                    if g:
                        PENDING[user.telegram_id] = {"step": "history_view", "generation_id": g.id, "return_to_history": True}
                        await safe_send(chat_id, g.output_text, history_actions_menu(g.id, g.kind))
                        return
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به صفحه اصلی برگشتی. 👋", main_menu())
                    return
                if step == "rewrite_request":
                    PENDING[user.telegram_id] = {"step": "kind"}
                    await safe_send(chat_id, "به انتخاب نوع محتوا برگشتی.", content_menu())
                    return
                if step == "daily_refine_request":
                    item = await session.get(DailySuggestion, state.get("return_daily_id"))
                    if item and item.user_id == user.id:
                        PENDING[user.telegram_id] = {"step": "daily_view", "suggestion_id": item.id}
                        await safe_send(chat_id, item.output_text, daily_suggestion_menu(item.id))
                        return
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به صفحه اصلی برگشتی. 👋", main_menu())
                    return
                if step == "brief_request":
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به صفحه اصلی برگشتی. 👋", main_menu())
                    return
                if step in {"idea_request", "pack_request", "brief_request", "daily_request"}:
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به بخش قبلی برگشتی. 👋", main_menu())
                    return
                if step == "help":
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به حساب من برگشتی. 👤", account_menu())
                    return
                if step == "support_topic":
                    PENDING[user.telegram_id] = {}
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به پشتیبانی برگشتی. 🆘", support_menu())
                    return
                if step == "support_message":
                    PENDING[user.telegram_id] = {"step": "support_topic"}
                    await safe_send(chat_id, "موضوع درخواستت را انتخاب کن:", support_topic_menu())
                    return
                if step == "profile_main_offer":
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به پروفایل برند برگشتی.", profile_menu())
                    return
                if step == "profile_differentiator":
                    state["step"] = "profile_main_offer"
                    await safe_send(chat_id, "محصول یا خدمت اصلی برندت چیست؟", profile_optional_menu())
                    return
                if step == "profile_content_goal":
                    state["step"] = "profile_differentiator"
                    await safe_send(chat_id, "مزیت یا تفاوت اصلی برندت چیست؟", profile_optional_menu())
                    return
                if step == "history_select":
                    PENDING[user.telegram_id] = {}
                    await safe_send(chat_id, "به محتوای من برگشتی. 📚", history_menu())
                    return
                if step == "history_view":
                    if state.get("return_to_history"):
                        history_mode = state.get("history_mode", "recent")
                        items = await list_recent_generations(session, user, 8, favorites_only=(history_mode == "favorites"))
                        PENDING[user.telegram_id] = {"step": "history_select", "history_mode": history_mode}
                        if items:
                            title = "⭐ ذخیره‌شده‌ها" if history_mode == "favorites" else "🕘 خروجی‌های اخیر"
                            lines = [title, "", *[f"#{g.id} — {KIND_LABELS.get(g.kind, g.kind)} — {history_hint(g.output_text)}" for g in items], "", "برای دیدن یک خروجی، شماره‌اش را مثل #12 بفرست."]
                            await safe_send(chat_id, "\n".join(lines), history_list_menu())
                        else:
                            await safe_send(chat_id, ("⭐ ذخیره‌شده‌ها\n\nهنوز خروجی ذخیره‌شده‌ای نداری." if history_mode == "favorites" else "📚 محتوای من\n\nهنوز خروجی‌ای نداری."), history_menu())
                    else:
                        PENDING.pop(user.telegram_id, None)
                        await safe_send(chat_id, "به ابزارهای محتوا برگشتی.", content_menu())
                    return
                if step == "ticket_select":
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به پشتیبانی برگشتی. 🆘", support_menu())
                    return
                if step == "ticket_view":
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به فهرست تیکت‌ها برگشتی. 🎫", support_menu())
                    return
                if step == "ticket_reply":
                    PENDING.pop(user.telegram_id, None)
                    await safe_send(chat_id, "به تیکت برگشتی. 🎫", support_menu())
                    return
            await safe_send(chat_id, "به صفحه اصلی برگشتی. 👋", main_menu())
            return

        if text in {"⌂ خانه", "خانه", "/home"}:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "خانه 🏠", main_menu())
            return

        if text == "👤 حساب من":
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "👤 حساب من\n\nاز اینجا به تنظیمات، وضعیت، راهنما، دعوت و پشتیبانی دسترسی داری.", account_menu())
            return

        if text == "✨ پیشنهاد امروز":
            if user.telegram_id in ACTIVE_GENERATION_USERS:
                await safe_send(chat_id, "⏳ یک درخواست هنوز در حال انجام است. لطفاً تا آماده‌شدن نتیجه صبر کن.", home_menu())
                return
            if not await profile_complete(user):
                await safe_send(chat_id, "برای دریافت پیشنهاد شخصی، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
                return
            existing = await get_daily_suggestion(session, user, date.today().isoformat())
            if existing:
                PENDING[user.telegram_id] = {"step": "daily_view", "suggestion_id": existing.id}
                await safe_send(chat_id, existing.output_text, daily_suggestion_menu(existing.id))
                return
            PENDING[user.telegram_id] = {"step": "processing", "kind": "daily_suggestion"}
            await send_processing_message(user.telegram_id, chat_id, "دارم پیشنهاد امروز را برای برندت می‌سازم... ✨", generation_processing_menu())
            request = "برای امروز یک پیشنهاد محتوایی تازه و مناسب برای این برند بده. از زاویه‌ای متفاوت با پیشنهادهای معمول فکر کن."
            start_background(process_daily_suggestion(user.telegram_id, chat_id, request), user.telegram_id)
            return

        if text in {"🧭 مسیر محتوا", "🧭 پیشنهاد مسیر محتوا", "🧠 ساخت محتوا"}:
            if not await profile_complete(user):
                await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
                return
            PENDING[user.telegram_id] = {"step": "brief_request", "kind": "brief"}
            await safe_send(chat_id, "محصول، موضوع، مناسبت یا هدفت را خیلی طبیعی توضیح بده؛ لازم نیست نوع محتوا را مشخص کنی. 🧭", home_menu())
            return

        if text == "📚 محتوای من":
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "📚 محتوای من\n\nیکی را انتخاب کن:", history_menu())
            return

        if text == "🚀 کمپین":
            if user.telegram_id in ACTIVE_GENERATION_USERS:
                await safe_send(chat_id, "⏳ یک درخواست هنوز در حال انجام است. لطفاً تا آماده‌شدن نتیجه صبر کن.", home_menu())
                return
            if user.plan == "free" and not is_admin(user):
                await safe_send(chat_id, "🚀 کمپین در پلن رایگان فعال نیست. برای استفاده از این قابلیت پلن خودت را ارتقا بده. 💎", main_menu())
                return
            if not await profile_complete(user):
                await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
                return
            PENDING[user.telegram_id] = {"step": "campaign_request", "kind": "campaign"}
            await safe_send(chat_id, CAMPAIGN_REQUEST_MESSAGE, back_home_menu())
            return

        if text in {"🕘 اخیر", "🕘 محتوای من"}:
            items = await list_recent_generations(session, user, 8)
            if not items:
                await safe_send(chat_id, "هنوز خروجی‌ای نداری. اول یک محتوا تولید کن. ✨", history_menu())
                return
            lines = ["🕘 خروجی‌های اخیر:", ""]
            for g in items:
                label = KIND_LABELS.get(g.kind, g.kind)
                star = "⭐ " if g.favorite else ""
                lines.append(f"#{g.id} — {star}{label} — {history_hint(g.output_text)} — {g.created_at:%m/%d %H:%M}")
            lines.append("\nبرای دیدن یک خروجی، شماره‌اش را مثل #12 بفرست.")
            PENDING[user.telegram_id] = {"step": "history_select", "history_mode": "recent"}
            await safe_send(chat_id, "\n".join(lines), history_list_menu())
            return

        if text == "⭐ ذخیره‌شده‌ها":
            items = await list_recent_generations(session, user, 8, favorites_only=True)
            if not items:
                await safe_send(chat_id, "هنوز خروجی ذخیره‌شده‌ای نداری. ⭐", history_menu())
                return
            lines = ["⭐ ذخیره‌شده‌ها:", ""] + [f"#{g.id} — {KIND_LABELS.get(g.kind, g.kind)} — {history_hint(g.output_text)} — {g.created_at:%m/%d %H:%M}" for g in items]
            lines.append("\nبرای دیدن یک خروجی، شماره‌اش را مثل #12 بفرست.")
            PENDING[user.telegram_id] = {"step": "history_select", "history_mode": "favorites"}
            await safe_send(chat_id, "\n".join(lines), history_list_menu())
            return

        if text.startswith(("⭐ ذخیره #", "🔄 بازنویسی #", "✍️ کپشن #", "📱 استوری #", "🎬 سناریوی Reel #", "🛍️ معرفی محصول #", "🔗 ادامه #")):
            if user.telegram_id in ACTIVE_GENERATION_USERS and not text.startswith("⭐"):
                await safe_send(chat_id, "⏳ یک درخواست هنوز در حال انجام است. لطفاً تا آماده‌شدن نتیجه صبر کن.", home_menu())
                return
            m = re.search(r"#(\d+)", text)
            if m:
                g = await get_generation(session, int(m.group(1)), user.id)
                if g:
                    if text.startswith("⭐"):
                        fav = await toggle_favorite(session, g)
                        await safe_send(chat_id, ("ذخیره شد ⭐" if fav else "از ذخیره‌ها حذف شد."), history_actions_menu(g.id, g.kind))
                        return
                    if text.startswith("🔄"):
                        PENDING[user.telegram_id] = {"step": "rewrite_request", "kind": "rewrite", "prefill": g.output_text, "return_generation_id": g.id}
                        await safe_send(chat_id, "چه تغییری می‌خواهی روی این متن اعمال شود؟ اگر برای ابزار دیگری پرامپت می‌خواهی، بنویس «پرامپت بنویس برای ...». ", back_home_menu())
                        return
                    if text.startswith("🔗"):
                        PENDING[user.telegram_id] = {"step": "brief_request", "kind": "brief", "prefill": g.output_text, "return_generation_id": g.id}
                        await safe_send(chat_id, "اگر می‌خواهی این پیشنهاد را تغییر بدهم، بگو چه چیزی عوض شود.", back_home_menu())
                        return
                    target = "caption" if text.startswith("✍️") else "story" if text.startswith("📱") else "reel" if text.startswith("🎬") else "product"
                    PENDING[user.telegram_id] = {"step": "transform_confirm", "kind": target, "source": g.output_text, "return_generation_id": g.id}
                    await safe_send(chat_id, f"می‌خواهی این خروجی را به {KIND_LABELS[target]} تبدیل کنم؟\nاگر نکته‌ای برای تغییر داری همین‌جا بنویس.", back_home_menu())
                    return

        if text in {"👤 پروفایل برند", "/profile"}:
            if await profile_complete(user):
                extras = []
                if user.main_offer:
                    extras.append(f"🛍️ محصول/خدمت اصلی\n{user.main_offer}")
                if user.differentiator:
                    extras.append(f"✨ مزیت/تفاوت برند\n{user.differentiator}")
                if user.content_goal:
                    extras.append(f"🎯 هدف اصلی محتوا\n{user.content_goal}")
                extra_text = ("\n\n" + "\n\n".join(extras)) if extras else ""
                await safe_send(
                    chat_id,
                    "👤 پروفایل برند\n\n"
                    f"🏷️ حوزه فعالیت\n{user.niche}\n\n"
                    f"🎯 مخاطب اصلی\n{user.audience}\n\n"
                    f"🗣️ لحن برند\n{user.tone}" + extra_text,
                    profile_menu(),
                )
            else:
                PENDING[user.telegram_id] = {"step": "niche", "editing": False}
                await safe_send(
                    chat_id,
                    "پروفایل برند\n\nحوزه فعالیت برندت چیست؟\nمثلاً: فروش لباس زنانه",
                    home_menu(),
                )
            return

        if text == "🧩 تکمیل پروفایل":
            if not await profile_complete(user):
                PENDING[user.telegram_id] = {"step": "niche", "editing": False}
                await safe_send(chat_id, "اول سه بخش اصلی پروفایل را کامل کن. حوزه فعالیت برندت چیست؟", home_menu())
                return
            PENDING[user.telegram_id] = {"step": "profile_main_offer", "main_offer": user.main_offer, "differentiator": user.differentiator, "content_goal": user.content_goal}
            await safe_send(chat_id, "🧩 تکمیل پروفایل\n\nمحصول یا خدمت اصلی برندت چیست؟", profile_optional_menu())
            return

        if text == "✏️ تغییر پروفایل":
            PENDING[user.telegram_id] = {"step": "niche", "editing": True}
            await safe_send(
                chat_id,
                "✏️ تغییر پروفایل\n\nحوزه فعالیت جدید برندت چیست؟\nمثلاً: فروش لباس زنانه",
                profile_edit_menu(),
            )
            return

        if text == "✨ استودیو پلاس":
            PENDING.pop(user.telegram_id, None)
            if user.plan != "studio_plus" and not is_admin(user):
                await safe_send(chat_id, f"✨ استودیو پلاس\n\nاین بخش برای پلن فعلی شما فعال نیست. برای دسترسی به ابزارهای Studio+ باید به استودیو پلاس ارتقا بدهی.\n\n🎟️ {settings.studio_plus_credits:,} اعتبار\n🧠 مدل‌های قدرتمندتر برای ابزارهای Premium\n🖼️ روزی ۱ عکس رایگان\n\n💰 {PLAN_PRICE['studio_plus']:,} تومان در ماه", upgrade_menu())
                return
            PENDING[user.telegram_id] = {"step": "studio_plus_kind"}
            await safe_send(chat_id, "✨ Studio+\n\nابزار Premium موردنظرت را انتخاب کن.", studio_plus_menu())
            return

        if text in {"✍️ تولید محتوا", "/content"}:
            if not await profile_complete(user):
                await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
                return
            PENDING[user.telegram_id] = {"step": "kind"}
            await safe_send(chat_id, "نوع محتوای موردنظرت را انتخاب کن.", content_menu())
            return

        if text == "🖼️ Studio+ عکس":
            if user.plan != "studio_plus" and not is_admin(user):
                await safe_send(chat_id, "این ابزار فقط برای Studio+ است.", main_menu())
                return
            if not await profile_complete(user):
                await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
                return
            ok, reason = await can_generate(session, user, "image")
            if not ok:
                await safe_send(chat_id, reason, main_menu())
                return
            PENDING[user.telegram_id] = {"step": "image_request", "kind": "image", "premium": True}
            await safe_send(chat_id, "توضیح تصویری Studio+ را بنویس.", back_home_menu())
            return

        if text == "🖼️ تولید عکس":
            if not await profile_complete(user):
                await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
                return
            ok, reason = await can_generate(session, user, "image")
            if not ok:
                await safe_send(chat_id, reason, main_menu())
                return
            PENDING[user.telegram_id] = {"step": "image_request", "kind": "image"}
            await safe_send(chat_id, IMAGE_REQUEST_MESSAGE, back_home_menu())
            return

        if text in {"📦 بسته هفتگی", "📦 بسته محتوایی", "📦 Content Pack", "/pack"}:
            if user.telegram_id in ACTIVE_GENERATION_USERS:
                await safe_send(chat_id, "⏳ یک درخواست هنوز در حال انجام است. لطفاً تا آماده‌شدن نتیجه صبر کن.", home_menu())
                return
            if not await profile_complete(user):
                await safe_send(
                    chat_id,
                    "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤",
                    main_menu(),
                )
                return
            ok, reason = await can_generate(session, user, "pack")
            if not ok:
                await safe_send(chat_id, reason, main_menu())
                return
            PENDING[user.telegram_id] = {"step": "pack_request", "kind": "pack"}
            await safe_send(
                chat_id,
                PACK_REQUEST_MESSAGE,
                pack_menu(),
            )
            return

        if text in {"💡 ایده محتوا", "/ideas"}:
            if user.telegram_id in ACTIVE_GENERATION_USERS:
                await safe_send(chat_id, "⏳ یک درخواست هنوز در حال انجام است. لطفاً تا آماده‌شدن نتیجه صبر کن.", home_menu())
                return
            if not await profile_complete(user):
                await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
                return
            ok, reason = await can_generate(session, user, "ideas")
            if not ok:
                await safe_send(chat_id, reason, main_menu())
                return
            PENDING[user.telegram_id] = {"step": "idea_request", "kind": "ideas"}
            await safe_send(
                chat_id,
                "موضوع، محصول یا هدفت را بنویس تا یک ایده مشخص و مسیر اجرایی کوتاه و کامل برات بچینم. 💡",
                back_home_menu(),
            )
            return

        if text in {"🆘 پشتیبانی", "/support"}:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "🆘 پشتیبانی\n\nچه کاری می‌خواهی انجام بدهی؟", support_menu())
            return

        if text == "🆕 تیکت جدید":
            PENDING[user.telegram_id] = {"step": "support_topic"}
            await safe_send(chat_id, "موضوع درخواستت را انتخاب کن:", support_topic_menu())
            return

        if text == "🎫 تیکت‌های من":
            tickets = await list_user_tickets(session, user)
            if not tickets:
                await safe_send(chat_id, "هنوز تیکتی ثبت نکرده‌ای. 🆘", support_menu())
                return
            lines = ["🎫 تیکت‌های من", ""]
            for ticket in tickets:
                status = "باز 🟢" if ticket.status == "open" else "بسته 🔴"
                lines.append(f"#{ticket.id} — {ticket.topic} — {status}")
            lines.append("\nشماره تیکت را مثل «#12» بفرست تا جزئیاتش را ببینی.")
            PENDING[user.telegram_id] = {"step": "ticket_select"}
            await safe_send(chat_id, "\n".join(lines), support_menu())
            return

        if user.telegram_id not in PENDING and text.startswith("#") and text[1:].isdigit():
            ticket = await get_support_ticket(session, int(text[1:]), user.id)
            if ticket:
                PENDING[user.telegram_id] = {"step": "ticket_view", "ticket_id": ticket.id}
                status = "باز 🟢" if ticket.status == "open" else "بسته 🔴"
                await safe_send(chat_id, f"🎫 تیکت #{ticket.id}\n\nموضوع: {ticket.topic}\nوضعیت: {status}\n\n{ticket.message}", ticket_actions_menu(ticket.id, ticket.status == "open"))
                return

        if text in {"📚 راهنما", "/help"}:
            PENDING[user.telegram_id] = {"step": "help"}
            await safe_send(
                chat_id,
                "📚 راهنمای محتواچی\n\nبخش موردنظرت را انتخاب کن:",
                help_menu(),
            )
            return

        if text in HELP_TEXTS:
            PENDING[user.telegram_id] = {"step": "help"}
            await safe_send(chat_id, HELP_TEXTS[text], help_menu())
            return

        if text in {"🎁 دعوت دوستان", "/ref"}:
            PENDING.pop(user.telegram_id, None)
            link = f"https://t.me/{settings.bot_username.lstrip("@")}?start=ref_{user.referral_code}"
            successful_count, remaining, window_end = await referral_reward_info(session, user)
            if remaining > 0:
                if successful_count == 0:
                    reward_line = f"اولین دعوت موفق: +{REFERRAL_FIRST_REWARD} اعتبار"
                else:
                    reward_line = f"دعوت موفق بعدی: +{REFERRAL_REPEAT_REWARD} اعتبار"
                quota_line = f"این دوره: {successful_count}/{REFERRAL_MAX_PER_30_DAYS} دعوت موفق"
            else:
                reward_line = "سهمیه این دوره تکمیل شده است."
                quota_line = f"این دوره: {successful_count}/{REFERRAL_MAX_PER_30_DAYS} دعوت موفق"
            reset_line = f"ریست سهمیه: {window_end:%Y/%m/%d}" if window_end else "ریست: با اولین دعوت موفق، دوره ۳۰ روزه شروع می‌شود."
            await safe_send(
                chat_id,
                "🎁 دعوت دوستان\n\n"
                f"لینک دعوت تو:\n{link}\n\n"
                f"{reward_line}\n"
                f"{quota_line}\n"
                f"{reset_line}\n\n"
                f"دوستت بعد از اولین تولید محتوای موفق +{REFERRAL_INVITEE_REWARD} اعتبار می‌گیرد.\n"
                "هر کاربر فقط یک‌بار می‌تواند با لینک دعوت ثبت شود.",
                main_menu(),
            )
            return

        if text in {"💎 ارتقا", "/upgrade"}:
            PENDING.pop(user.telegram_id, None)
            payment_state = "فعال" if await payments_enabled(session) else "غیرفعال"
            await safe_send(
                chat_id,
                "💎 پلن‌های محتواچی\n\n"
                "🔹 استارتر\n"
                f"💰 {PLAN_PRICE['starter']:,} تومان در ماه\n"
                "🎟 ۱۲۰ اعتبار\n\n"
                "🔹 پرو\n"
                f"💰 {PLAN_PRICE['pro']:,} تومان در ماه\n"
                "🎟 ۳۵۰ اعتبار\n"
                "🖼️ روزی ۱ عکس رایگان\n\n"
                "🔹 استودیو\n"
                f"💰 {PLAN_PRICE['studio']:,} تومان در ماه\n"
                "🎟 ۱۰۰۰ اعتبار\n"
                "🖼️ روزی ۲ عکس رایگان\n\n"
                "✨ استودیو پلاس\n"
                f"💰 {PLAN_PRICE['studio_plus']:,} تومان در ماه\n"
                f"🎟 {settings.studio_plus_credits:,} اعتبار\n"
                "🧠 ابزارهای Studio+ با مدل‌های قدرتمندتر\n🖼️ روزی ۱ عکس رایگان\n\n"
                f"وضعیت پرداخت: {payment_state}.",
                upgrade_menu(),
            )
            return

        if text in {"📊 وضعیت من", "/status"}:
            PENDING.pop(user.telegram_id, None)
            if user.plan == "free":
                daily_left = max(0, 3 - (user.daily_free_used if user.daily_free_date == date.today().isoformat() else 0))
                idea_result = await session.scalar(select(func.count(Generation.id)).where(Generation.user_id == user.id, Generation.kind == "ideas", Generation.created_at >= datetime.combine(date.today(), datetime.min.time())))
                idea_left = max(0, 1 - int(idea_result or 0))
                daily_text = f"🧾 سهمیه تولید امروز: {daily_left} از ۳ درخواست باقی مانده\n💡 ایده محتوا: {idea_left} از ۱ ایده امروز باقی مانده\n"
            else:
                image_limit = daily_free_image_limit(user)
                image_remaining = daily_free_images_remaining(user)
                daily_text = f"🖼️ عکس رایگان امروز: {image_remaining} از {image_limit} باقی مانده\n"
            profile_text = "کامل و آماده تولید ✅" if await profile_complete(user) else "ناقص؛ اول تکمیلش کن ⚠️"
            plan_label = {'free':'رایگان','starter':'استارتر','pro':'پرو','studio':'استودیو','studio_plus':'استودیو پلاس'}.get(user.plan, user.plan)
            admin_note = "\n🛠️ حالت توسعه‌دهنده: نامحدود" if is_admin(user) else ""
            await safe_send(
                chat_id,
                "📊 وضعیت حساب\n\n"
                f"💎 پلن فعلی: {plan_label}\n"
                f"🎟️ اعتبار: {user.credits}\n"
                f"{daily_text}"
                f"🔥 تداوم فعالیت: {user.streak_days} روز\n"
                f"👤 پروفایل برند: {profile_text}\n\n"
                "💡 اعتبارها برای استفاده از قابلیت‌های تولید محتوا مصرف می‌شوند؛ هر قابلیت هزینه متفاوتی دارد."
                f"{admin_note}",
                main_menu(),
            )
            return

        if text == "کمک":
            await safe_send(chat_id, "📚 راهنمای محتواچی\n\nبخش موردنظرت را انتخاب کن:", help_menu())
            return

        if user.telegram_id in PENDING:
            await handle_pending(session, user, chat_id, text)
            return

        await safe_send(chat_id, "از منوی پایین یکی از ابزارها را انتخاب کن.", main_menu())




async def send_admin_user_view(session, admin, target, chat_id: int) -> None:
    role = admin_role(admin) or "admin"
    recent = await list_recent_generations(session, target, 3)
    role_label = admin_role(target) or "کاربر"
    status = "🚫 مسدود" if target.is_banned else "🟢 فعال"
    last_generation = await session.scalar(select(func.max(Generation.created_at)).where(Generation.user_id == target.id))
    last_ticket = await session.scalar(select(func.max(SupportTicket.created_at)).where(SupportTicket.user_id == target.id))
    last_activity = max([x for x in (last_generation, last_ticket) if x], default=None)
    activity_text = last_activity.strftime("%Y/%m/%d %H:%M") if last_activity else "هنوز ثبت نشده"
    text = (
        "👤 اطلاعات کاربر\n\n"
        f"ID داخلی: #{target.id}\n"
        f"Telegram ID: {target.telegram_id}\n"
        f"نام: {target.first_name or 'ندارد'}\n"
        f"Username: @{target.username or 'ندارد'}\n"
        f"پلن: {PLAN_LABELS.get(target.plan, target.plan)}\n"
        f"اعتبار: {target.credits:,}\n"
        f"وضعیت: {status}\n"
        f"نقش: {role_label}\n"
        f"ثبت‌نام: {target.created_at:%Y/%m/%d %H:%M}\n"
        f"آخرین فعالیت ثبت‌شده: {activity_text}\n"
        f"آخرین تولیدها: {len(recent)}"
    )
    await safe_send(chat_id, text, admin_user_menu(target.id, target.is_banned, can_manage_users(admin)))


async def send_admin_ticket_view(session, admin, ticket_id: int, chat_id: int) -> None:
    role = admin_role(admin) or "admin"
    if not is_admin(admin):
        PENDING.pop(admin.telegram_id, None)
        await safe_send(chat_id, "دسترسی پشتیبانی نداری.", admin_menu(role))
        return

    ticket = await get_support_ticket(session, ticket_id)
    if not ticket:
        PENDING.pop(admin.telegram_id, None)
        await safe_send(chat_id, "تیکت پیدا نشد.", admin_menu(role))
        return

    ticket_user = await session.get(User, ticket.user_id)
    user_name = ticket_user.first_name if ticket_user else "کاربر"
    username = f"@{ticket_user.username}" if ticket_user and ticket_user.username else "ندارد"

    status = "🟢 باز" if ticket.status == "open" else "🔴 بسته"
    priority = PRIORITY_LABELS.get(ticket.priority, "🟡 عادی")
    assigned = f"#{ticket.assigned_admin_id}" if ticket.assigned_admin_id else "اختصاص داده نشده"
    first_response = ticket.first_response_at.strftime("%Y/%m/%d %H:%M") if ticket.first_response_at else "ثبت نشده"

    text = (
        f"🎫 تیکت #{ticket.id}\n\n"
        f"کاربر: {user_name}\n"
        f"Username: {username}\n"
        f"موضوع: {ticket.topic}\n"
        f"وضعیت: {status}\n"
        f"اولویت: {priority}\n"
        f"مسئول: {assigned}\n"
        f"اولین پاسخ: {first_response}\n"
        f"ایجاد: {ticket.created_at:%Y/%m/%d %H:%M}\n\n"
        f"📝 متن تیکت:\n{ticket.message}"
    )

    keyboard = {
        "keyboard": [
            [{"text": "✉️ پاسخ"}],
            [{"text": "👤 اختصاص به من"}],
            [{"text": "🔴 اولویت بالا"}, {"text": "🟡 اولویت عادی"}],
            [{"text": "🟢 اولویت پایین"}],
            [{"text": "🔒 بستن تیکت"}],
            [{"text": "↩️ تیکت‌ها"}, {"text": "خانه"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }

    PENDING[admin.telegram_id] = {"step": "admin_ticket_view", "ticket_id": ticket.id}
    await safe_send(chat_id, text, keyboard)

async def handle_admin_back(session, admin, chat_id: int, state: dict) -> bool:
    step = state.get("step")
    role = admin_role(admin) or "admin"
    if not step or not step.startswith("admin_"):
        return False
    if step == "admin_marketing":
        PENDING.pop(admin.telegram_id, None); await safe_send(chat_id, "🛠️ پنل مدیریت", admin_menu(role))
    elif step == "admin_broadcast_segment":
        PENDING[admin.telegram_id] = {"step": "admin_marketing"}; await safe_send(chat_id, "📣 بازاریابی", admin_marketing_menu())
    elif step == "admin_broadcast_message":
        PENDING[admin.telegram_id] = {"step": "admin_broadcast_segment"}; await safe_send(chat_id, "گروه دریافت‌کنندگان را انتخاب کن:", admin_broadcast_segment_menu())
    elif step == "admin_broadcast_confirm":
        new_state = dict(state); new_state.pop("message", None); new_state["step"] = "admin_broadcast_message"; PENDING[admin.telegram_id] = new_state
        await safe_send(chat_id, "متن پیام را دوباره بفرست:", back_home_menu())
    elif step == "admin_coupon_menu":
        PENDING[admin.telegram_id] = {"step": "admin_marketing"}; await safe_send(chat_id, "📣 بازاریابی", admin_marketing_menu())
    elif step == "admin_coupon_code":
        PENDING[admin.telegram_id] = {"step": "admin_coupon_menu"}; await safe_send(chat_id, "🎟️ مدیریت کدهای تخفیف", admin_coupon_menu())
    elif step == "admin_coupon_type":
        PENDING[admin.telegram_id] = {"step": "admin_coupon_code"}; await safe_send(chat_id, "کد تخفیف را بنویس:", admin_coupon_menu())
    elif step == "admin_coupon_value":
        PENDING[admin.telegram_id] = {"step": "admin_coupon_type", "code": state.get("code", "")}; await safe_send(chat_id, "نوع تخفیف را انتخاب کن:", admin_coupon_type_menu())
    elif step == "admin_coupon_plan":
        PENDING[admin.telegram_id] = dict(state) | {"step": "admin_coupon_value"}; await safe_send(chat_id, "مقدار تخفیف را وارد کن:", back_home_menu())
    elif step == "admin_coupon_expiry":
        PENDING[admin.telegram_id] = dict(state) | {"step": "admin_coupon_plan"}; await safe_send(chat_id, "این تخفیف برای کدام پلن باشد؟", admin_coupon_plan_menu())
    elif step == "admin_coupon_deactivate":
        PENDING[admin.telegram_id] = {"step": "admin_coupon_menu"}; await safe_send(chat_id, "🎟️ مدیریت کدهای تخفیف", admin_coupon_menu())
    elif step == "admin_user_search":
        PENDING.pop(admin.telegram_id, None); await safe_send(chat_id, "🛠️ پنل مدیریت", admin_menu(role))
    elif step == "admin_select_user":
        PENDING[admin.telegram_id] = {"step": "admin_user_search"}; await safe_send(chat_id, "Telegram ID یا username کاربر را بفرست.", admin_menu(role))
    elif step == "admin_user_view":
        PENDING[admin.telegram_id] = {"step": "admin_user_search"}; await safe_send(chat_id, "Telegram ID یا username کاربر را بفرست.", admin_menu(role))
    elif step in {"admin_credit_add", "admin_credit_remove", "admin_plan", "admin_ban_reason"}:
        target = await get_user_by_id(session, state.get("target_user_id")) if state.get("target_user_id") else None
        if target:
            PENDING[admin.telegram_id] = {"step": "admin_user_view", "target_user_id": target.id}; await send_admin_user_view(session, admin, target, chat_id)
        else:
            PENDING[admin.telegram_id] = {"step": "admin_user_search"}; await safe_send(chat_id, "Telegram ID یا username کاربر را بفرست.", admin_menu(role))
    elif step == "admin_role_search":
        PENDING.pop(admin.telegram_id, None); await safe_send(chat_id, "🛠️ پنل مدیریت", admin_menu(role))
    elif step == "admin_role_select":
        PENDING[admin.telegram_id] = {"step": "admin_role_search"}; await safe_send(chat_id, "Telegram ID یا username ادمین را بفرست.", admin_menu(role))
    elif step == "admin_role_set":
        PENDING[admin.telegram_id] = {"step": "admin_role_select"}; await safe_send(chat_id, "شناسه داخلی کاربر را مثل #12 بفرست.", admin_menu(role))
    elif step == "admin_ticket_select":
        PENDING.pop(admin.telegram_id, None); await safe_send(chat_id, "🛠️ پنل مدیریت", admin_menu(role))
    elif step == "admin_reply":
        ticket_id = state.get("ticket_id")
        if ticket_id:
            PENDING[admin.telegram_id] = {"step": "admin_ticket_view", "ticket_id": ticket_id}; await send_admin_ticket_view(session, admin, ticket_id, chat_id)
        else:
            PENDING.pop(admin.telegram_id, None); await safe_send(chat_id, "🎫 تیکت‌ها", admin_menu(role))
    else:
        return False
    return True


async def handle_admin_pending(session, admin, chat_id: int, text: str, state: dict) -> None:
    role = admin_role(admin) or "admin"
    marketing_steps = {
        "admin_marketing", "admin_broadcast_segment", "admin_broadcast_message",
        "admin_broadcast_confirm", "admin_coupon_menu", "admin_coupon_code",
        "admin_coupon_type", "admin_coupon_value", "admin_coupon_plan",
        "admin_coupon_expiry", "admin_coupon_deactivate",
    }
    if state.get("step") in marketing_steps and not can_manage_marketing(admin):
        PENDING.pop(admin.telegram_id, None)
        await safe_send(chat_id, "دسترسی بازاریابی نداری.", admin_menu(role))
        return
    if text == "خانه":
        PENDING.pop(admin.telegram_id, None)
        await safe_send(chat_id, "خانه 🏠", main_menu())
        return
    if text == "↩️ پنل مدیریت":
        PENDING.pop(admin.telegram_id, None)
        await safe_send(chat_id, "🛠️ پنل مدیریت", admin_menu(role))
        return
    if state["step"] == "admin_ticket_select":
        if not text.startswith("#") or not text[1:].isdigit():
            await safe_send(chat_id, "شماره تیکت را مثل #12 بفرست.", admin_menu(role)); return
        await send_admin_ticket_view(session, admin, int(text[1:]), chat_id); return

    if state["step"] == "admin_ticket_view":
        ticket = await get_support_ticket(session, state["ticket_id"])
        if not ticket:
            PENDING.pop(admin.telegram_id, None)
            await safe_send(chat_id, "تیکت پیدا نشد.", admin_menu(role)); return
        if text == "✉️ پاسخ":
            PENDING[admin.telegram_id] = {"step": "admin_reply", "ticket_id": ticket.id}
            await safe_send(chat_id, f"✉️ پاسخ به تیکت #{ticket.id} را بنویس:", back_home_menu()); return
        if text == "👤 اختصاص به من":
            try:
                await assign_ticket_to_admin(session, admin, ticket.id)
                await send_admin_ticket_view(session, admin, ticket.id, chat_id)
            except PermissionError as exc:
                await safe_send(chat_id, str(exc), admin_menu(role))
            return
        priority_map = {"🔴 اولویت بالا": "high", "🟡 اولویت عادی": "normal", "🟢 اولویت پایین": "low"}
        if text in priority_map:
            try:
                await set_ticket_priority(session, admin, ticket.id, priority_map[text])
                await send_admin_ticket_view(session, admin, ticket.id, chat_id)
            except (PermissionError, ValueError) as exc:
                await safe_send(chat_id, str(exc), admin_menu(role))
            return
        if text == "🔒 بستن تیکت":
            closed = await close_support_ticket_by_admin(session, admin, ticket.id)
            PENDING.pop(admin.telegram_id, None)
            await safe_send(chat_id, "تیکت بسته شد. ✅" if closed else "تیکت قبلاً بسته شده است.", admin_menu(role)); return
        if text in {"↩️ تیکت‌ها", "خانه"}:
            PENDING.pop(admin.telegram_id, None)
            await safe_send(chat_id, "🎫 تیکت‌ها", admin_menu(role)); return

    if state["step"] == "admin_reply":
        if not text:
            await safe_send(chat_id, "پاسخ نمی‌تواند خالی باشد.", back_home_menu())
            return

        if len(text) > SUPPORT_MESSAGE_LIMIT:
            await safe_send(
                chat_id,
                f"پاسخ حداکثر {SUPPORT_MESSAGE_LIMIT} کاراکتر باشد.",
                back_home_menu(),
            )
            return

        ticket = await get_support_ticket(session, state["ticket_id"])
        if not ticket:
            PENDING.pop(admin.telegram_id, None)
            await safe_send(chat_id, "تیکت پیدا نشد.", admin_menu(role))
            return

        await mark_ticket_first_response(session, admin, ticket)
        await append_ticket_message(session, ticket, "admin", text)

        target_user = await session.get(User, ticket.user_id)
        PENDING.pop(admin.telegram_id, None)

        if target_user:
            await safe_send(
                target_user.telegram_id,
                f"🛠️ پاسخ پشتیبانی به تیکت #{ticket.id}\n\n{text}",
                main_menu(),
            )

        await safe_send(
            chat_id,
            f"پاسخ تیکت #{ticket.id} ارسال شد. ✅",
            admin_menu(role),
        )
        return

    if state["step"] == "admin_marketing":
        if text == "📣 ارسال پیام":
            PENDING[admin.telegram_id] = {"step": "admin_broadcast_segment"}
            await safe_send(chat_id, "گروه دریافت‌کنندگان را انتخاب کن:", admin_broadcast_segment_menu()); return
        if text == "🎟️ کدهای تخفیف":
            PENDING[admin.telegram_id] = {"step": "admin_coupon_menu"}
            await safe_send(chat_id, "🎟️ مدیریت کدهای تخفیف", admin_coupon_menu()); return
        if text in {"↩️ پنل مدیریت", "خانه"}:
            PENDING.pop(admin.telegram_id, None); await safe_send(chat_id, "🛠️ پنل مدیریت", admin_menu(role)); return

    if state["step"] == "admin_broadcast_segment":
        if text in {"↩️ بازاریابی", "خانه"}:
            PENDING.pop(admin.telegram_id, None); await safe_send(chat_id, "📣 بازاریابی", admin_marketing_menu()); return
        segment = ADMIN_SEGMENTS.get(text)
        if not segment:
            await safe_send(chat_id, "یکی از گروه‌های منو را انتخاب کن.", admin_broadcast_segment_menu()); return
        users = await get_admin_segment_users(session, segment, 5000)
        if not users:
            await safe_send(chat_id, "در این گروه کاربری برای ارسال وجود ندارد.", admin_broadcast_segment_menu()); return
        PENDING[admin.telegram_id] = {"step": "admin_broadcast_message", "segment": segment, "recipient_count": len(users)}
        await safe_send(chat_id, f"👥 تعداد دریافت‌کنندگان: {len(users):,}\n\nمتن پیام را بفرست. حداکثر ۳۵۰۰ کاراکتر.", back_home_menu()); return

    if state["step"] == "admin_broadcast_message":
        if len(text) > 3500 or not text.strip():
            await safe_send(chat_id, "متن پیام خالی یا بیش از ۳۵۰۰ کاراکتر است. دوباره بفرست.", back_home_menu()); return
        PENDING[admin.telegram_id] = {"step": "admin_broadcast_confirm", "segment": state["segment"], "recipient_count": state["recipient_count"], "message": text.strip()}
        await safe_send(chat_id, f"📣 پیش‌نمایش\n\nگیرندگان: {state['recipient_count']:,}\n\n{text.strip()}\n\nارسال شود؟", admin_broadcast_confirm_menu()); return

    if state["step"] == "admin_broadcast_confirm":
        if text == "✅ ارسال کن":
            PENDING.pop(admin.telegram_id, None)
            start_background(run_broadcast(admin.id, state["segment"], state["message"]))
            await safe_send(chat_id, "📣 ارسال شروع شد. نتیجه بعد از پایان برایت ارسال می‌شود.", admin_menu(role)); return
        if text in {"❌ لغو", "↩️ بازاریابی"}:
            PENDING[admin.telegram_id] = {"step": "admin_marketing"}
            await safe_send(chat_id, "ارسال لغو شد.", admin_marketing_menu()); return

    if state["step"] == "admin_coupon_menu":
        if text == "➕ ساخت کد":
            PENDING[admin.telegram_id] = {"step": "admin_coupon_code"}
            await safe_send(chat_id, "کد تخفیف را بنویس (مثل SAVE20):", admin_coupon_menu()); return
        if text == "📋 کدهای فعال":
            coupons = await list_coupons(session, True, 30)
            lines = ["🎟️ کدهای فعال", ""]
            for coupon in coupons:
                discount = f"{coupon.discount_percent}%" if coupon.discount_percent else f"{coupon.discount_toman:,} تومان"
                plan = PLAN_LABELS.get(coupon.applicable_plan, "همه پلن‌ها")
                exp = coupon.expires_at.strftime("%Y/%m/%d") if coupon.expires_at else "بدون انقضا"
                uses = int(await session.scalar(select(func.count(CouponRedemption.id)).where(CouponRedemption.coupon_id == coupon.id)) or 0)
                lines.append(f"{coupon.code} — {discount} — {plan} — {exp} — استفاده: {uses}")
            if not coupons: lines.append("کد فعالی وجود ندارد.")
            await safe_send(chat_id, "\n".join(lines), admin_coupon_menu()); return
        if text == "⛔ غیرفعال‌کردن کد":
            PENDING[admin.telegram_id] = {"step": "admin_coupon_deactivate"}
            await safe_send(chat_id, "کد موردنظر را بفرست:", admin_coupon_menu()); return
        if text in {"↩️ بازاریابی", "خانه"}:
            PENDING[admin.telegram_id] = {"step": "admin_marketing"}; await safe_send(chat_id, "📣 بازاریابی", admin_marketing_menu()); return

    if state["step"] == "admin_coupon_code":
        PENDING[admin.telegram_id] = {"step": "admin_coupon_type", "code": text.strip()}
        await safe_send(chat_id, "نوع تخفیف را انتخاب کن:", admin_coupon_type_menu()); return

    if state["step"] == "admin_coupon_type":
        if text not in {"٪ درصدی", "💵 مبلغ ثابت"}:
            await safe_send(chat_id, "نوع تخفیف را از منو انتخاب کن.", admin_coupon_type_menu()); return
        PENDING[admin.telegram_id] = {"step": "admin_coupon_value", "code": state["code"], "type": "percent" if text == "٪ درصدی" else "amount"}
        await safe_send(chat_id, "مقدار تخفیف را وارد کن. (مثلاً ۲۰ یا 100000):", back_home_menu()); return

    if state["step"] == "admin_coupon_value":
        if not text.isdigit() or int(text) <= 0:
            await safe_send(chat_id, "فقط یک عدد مثبت وارد کن.", back_home_menu()); return
        PENDING[admin.telegram_id] = {"step": "admin_coupon_plan", "code": state["code"], "type": state["type"], "value": int(text)}
        await safe_send(chat_id, "این تخفیف برای کدام پلن باشد؟", admin_coupon_plan_menu()); return

    if state["step"] == "admin_coupon_plan":
        plan_map = {"همه پلن‌ها": None, "استارتر": "starter", "پرو": "pro", "استودیو": "studio", "استودیو پلاس": "studio_plus"}
        if text not in plan_map:
            await safe_send(chat_id, "یک گزینه از منو انتخاب کن.", admin_coupon_plan_menu()); return
        new_state = dict(state)
        new_state.update({"step": "admin_coupon_expiry", "plan": plan_map[text]})
        PENDING[admin.telegram_id] = new_state
        await safe_send(chat_id, "تاریخ انقضا را به شکل 2026-12-31 بفرست یا «بدون انقضا».", back_home_menu()); return

    if state["step"] == "admin_coupon_expiry":
        expires_at = None
        if text != "بدون انقضا":
            try:
                expires_at = datetime.strptime(text.strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)
                if expires_at <= datetime.utcnow(): raise ValueError
            except ValueError:
                await safe_send(chat_id, "تاریخ باید به شکل 2026-12-31 و در آینده باشد.", back_home_menu()); return
        try:
            coupon = await create_coupon(session, admin, state["code"], discount_percent=state["value"] if state["type"] == "percent" else 0, discount_toman=state["value"] if state["type"] == "amount" else 0, applicable_plan=state["plan"], expires_at=expires_at)
        except (PermissionError, ValueError) as exc:
            await safe_send(chat_id, str(exc), admin_coupon_menu()); return
        PENDING[admin.telegram_id] = {"step": "admin_coupon_menu"}
        await safe_send(chat_id, f"کد {coupon.code} ساخته شد. ✅", admin_coupon_menu()); return

    if state["step"] == "admin_coupon_deactivate":
        try:
            ok = await deactivate_coupon(session, admin, text)
        except (PermissionError, ValueError) as exc:
            await safe_send(chat_id, str(exc), admin_coupon_menu()); return
        await safe_send(chat_id, "کد غیرفعال شد. ✅" if ok else "کد فعال پیدا نشد.", admin_coupon_menu()); return


    if state["step"] == "admin_role_search":
        users = await find_users(session, text, 8)
        if not users:
            await safe_send(chat_id, "کاربری پیدا نشد.", admin_menu(role))
            return
        lines = ["👮 نتایج:", ""]
        for u in users:
            lines.append(f"#{u.id} — {u.first_name or 'بدون نام'} — @{u.username or 'ندارد'} — {u.telegram_id} — {admin_role(u) or 'کاربر'}")
        lines.append("\nشناسه داخلی را مثل #12 بفرست.")
        PENDING[admin.telegram_id] = {"step": "admin_role_select"}
        await safe_send(chat_id, "\n".join(lines), admin_menu(role))
        return

    if state["step"] == "admin_role_select":
        if not text.startswith("#") or not text[1:].isdigit():
            await safe_send(chat_id, "شناسه را مثل #12 بفرست.", admin_menu(role))
            return
        target = await get_user_by_id(session, int(text[1:]))
        if not target:
            await safe_send(chat_id, "کاربر پیدا نشد.", admin_menu(role))
            return
        if target.id == admin.id:
            await safe_send(chat_id, "نقش Super Admin اصلی از تنظیمات سیستم می‌آید و از اینجا تغییر نمی‌کند.", admin_menu(role))
            return
        PENDING[admin.telegram_id] = {"step": "admin_role_set", "target_user_id": target.id}
        await safe_send(chat_id, f"نقش فعلی: {admin_role(target) or 'کاربر'}\nنقش جدید را بنویس: super_admin / admin / support / none", admin_menu(role))
        return

    if state["step"] == "admin_role_set":
        if text not in {"super_admin", "admin", "support", "none"}:
            await safe_send(chat_id, "یکی از این‌ها را بنویس: super_admin / admin / support / none", admin_menu(role))
            return
        target = await get_user_by_id(session, state["target_user_id"])
        if not target:
            PENDING.pop(admin.telegram_id, None)
            await safe_send(chat_id, "کاربر پیدا نشد.", admin_menu(role))
            return
        old = admin_role(target) or "none"
        if text == "super_admin" and admin_role(target) == "super_admin":
            await safe_send(chat_id, "این کاربر از قبل Super Admin است.", admin_menu(role))
            return
        target.admin_role = None if text == "none" else text
        await audit_admin_action(session, admin, "ROLE_CHANGE", target, f"{old}->{text}")
        await session.commit()
        PENDING.pop(admin.telegram_id, None)
        await safe_send(chat_id, f"نقش کاربر از {old} به {text} تغییر کرد. ✅", admin_menu(role))
        return

    if state["step"] == "admin_user_search":
        users = await find_users(session, text, 8)
        if not users:
            await safe_send(chat_id, "کاربری پیدا نشد. Telegram ID یا username را دقیق‌تر بفرست.", admin_menu(role))
            return
        lines = ["👤 نتایج جستجو:", ""]
        for u in users:
            lines.append(f"#{u.id} — {u.first_name or 'بدون نام'} — @{u.username or 'ندارد'} — {u.telegram_id}")
        lines.append("\nبرای انتخاب، شناسه داخلی مثل #12 را بفرست.")
        PENDING[admin.telegram_id] = {"step": "admin_select_user"}
        await safe_send(chat_id, "\n".join(lines), admin_menu(role))
        return

    if state["step"] == "admin_select_user":
        if not text.startswith("#") or not text[1:].isdigit():
            await safe_send(chat_id, "شناسه را مثل #12 بفرست.", admin_menu(role))
            return
        target = await get_user_by_id(session, int(text[1:]))
        if not target:
            await safe_send(chat_id, "کاربر پیدا نشد.", admin_menu(role))
            return
        PENDING[admin.telegram_id] = {"step": "admin_user_view", "target_user_id": target.id}
        await send_admin_user_view(session, admin, target, chat_id)
        return

    if state["step"] == "admin_user_view":
        target = await get_user_by_id(session, state["target_user_id"])
        if not target:
            PENDING.pop(admin.telegram_id, None)
            await safe_send(chat_id, "کاربر پیدا نشد.", admin_menu(role))
            return
        if text == "➕ اعتبار":
            if not can_manage_credits(admin):
                await safe_send(chat_id, "دسترسی تغییر اعتبار نداری.", admin_user_menu(target.id, target.is_banned))
                return
            PENDING[admin.telegram_id] = {"step": "admin_credit_add", "target_user_id": target.id}
            await safe_send(chat_id, "چند اعتبار اضافه کنم؟ فقط عدد مثبت بفرست.", admin_user_menu(target.id, target.is_banned))
            return
        if text == "➖ اعتبار":
            if not can_manage_credits(admin):
                await safe_send(chat_id, "دسترسی تغییر اعتبار نداری.", admin_user_menu(target.id, target.is_banned))
                return
            PENDING[admin.telegram_id] = {"step": "admin_credit_remove", "target_user_id": target.id}
            await safe_send(chat_id, "چند اعتبار کم کنم؟ فقط عدد مثبت بفرست.", admin_user_menu(target.id, target.is_banned))
            return
        if text == "📦 تغییر پلن":
            if not can_manage_credits(admin):
                await safe_send(chat_id, "دسترسی تغییر پلن نداری.", admin_user_menu(target.id, target.is_banned))
                return
            PENDING[admin.telegram_id] = {"step": "admin_plan", "target_user_id": target.id}
            await safe_send(chat_id, "پلن جدید را انتخاب کن. تغییر پلن، اعتبار پایه همان پلن را تنظیم می‌کند.", admin_plan_menu())
            return
        if text == "📜 تراکنش‌ها":
            txs = (await session.execute(select(CreditTransaction).where(CreditTransaction.user_id == target.id).order_by(CreditTransaction.id.desc()).limit(10))).scalars().all()
            lines = ["📜 آخرین تراکنش‌های اعتبار:", ""]
            lines += [f"{'+' if tx.amount > 0 else ''}{tx.amount} → {tx.balance_after} — {tx.reason}" for tx in txs] or ["تراکنشی ثبت نشده."]
            await safe_send(chat_id, "\n".join(lines), admin_user_menu(target.id, target.is_banned))
            return
        if text in {"🚫 Ban", "✅ رفع Ban"}:
            if not can_manage_bans(admin):
                await safe_send(chat_id, "دسترسی Ban نداری.", admin_user_menu(target.id, target.is_banned))
                return
            if text == "🚫 Ban":
                PENDING[admin.telegram_id] = {"step": "admin_ban_reason", "target_user_id": target.id}
                await safe_send(chat_id, "دلیل Ban را بنویس. برای دلیل کوتاه هم کافی است.", admin_user_menu(target.id, target.is_banned))
            else:
                try:
                    await set_user_ban(session, admin, target, False)
                    await safe_send(chat_id, "Ban کاربر برداشته شد. ✅", admin_user_menu(target.id, False))
                except (PermissionError, ValueError) as exc:
                    await safe_send(chat_id, str(exc), admin_user_menu(target.id, target.is_banned))
            return
        if text in {"↩️ پنل مدیریت", "خانه"}:
            PENDING.pop(admin.telegram_id, None)
            await safe_send(chat_id, "🛠️ پنل مدیریت", admin_menu(role))
            return

    if state["step"] in {"admin_credit_add", "admin_credit_remove"}:
        if not text.isdigit() or int(text) <= 0:
            await safe_send(chat_id, "فقط یک عدد مثبت بفرست.", admin_menu(role))
            return
        target = await get_user_by_id(session, state["target_user_id"])
        if not target:
            PENDING.pop(admin.telegram_id, None)
            await safe_send(chat_id, "کاربر پیدا نشد.", admin_menu(role))
            return
        amount = int(text) * (1 if state["step"] == "admin_credit_add" else -1)
        reason = "Admin credit adjustment"
        try:
            balance = await adjust_user_credits(session, admin, target, amount, reason)
        except (PermissionError, ValueError) as exc:
            await safe_send(chat_id, str(exc), admin_user_menu(target.id, target.is_banned))
            return
        PENDING[admin.telegram_id] = {"step": "admin_user_view", "target_user_id": target.id}
        await safe_send(chat_id, f"اعتبار با موفقیت تغییر کرد. موجودی جدید: {balance} 💎", admin_user_menu(target.id, target.is_banned))
        return

    if state["step"] == "admin_plan":
        plans = {"رایگان": "free", "استارتر": "starter", "پرو": "pro", "استودیو": "studio", "استودیو پلاس": "studio_plus"}
        if text not in plans:
            await safe_send(chat_id, "یکی از پلن‌های منو را انتخاب کن.", admin_plan_menu())
            return
        target = await get_user_by_id(session, state["target_user_id"])
        if not target:
            PENDING.pop(admin.telegram_id, None)
            await safe_send(chat_id, "کاربر پیدا نشد.", admin_menu(role))
            return
        try:
            await set_user_plan_by_admin(session, admin, target, plans[text])
        except (PermissionError, ValueError) as exc:
            await safe_send(chat_id, str(exc), admin_plan_menu())
            return
        PENDING[admin.telegram_id] = {"step": "admin_user_view", "target_user_id": target.id}
        await send_admin_user_view(session, admin, target, chat_id)
        return

    if state["step"] == "admin_ban_reason":
        target = await get_user_by_id(session, state["target_user_id"])
        if not target:
            PENDING.pop(admin.telegram_id, None)
            await safe_send(chat_id, "کاربر پیدا نشد.", admin_menu(role))
            return
        try:
            await set_user_ban(session, admin, target, True, text)
        except (PermissionError, ValueError) as exc:
            await safe_send(chat_id, str(exc), admin_user_menu(target.id, target.is_banned))
            return
        PENDING.pop(admin.telegram_id, None)
        await safe_send(chat_id, "کاربر Ban شد. 🚫", admin_user_menu(target.id, True))
        return

async def handle_pending(session, user, chat_id: int, text: str) -> None:
    state = PENDING.get(user.telegram_id)
    if state and state.get("step", "").startswith("admin_"):
        await handle_admin_pending(session, user, chat_id, text, state)
        return
    if not state:
        return
    if user.telegram_id in ACTIVE_GENERATION_USERS and state.get("step") != "processing":
        await safe_send(
            chat_id,
            "⏳ یک درخواست هنوز در حال انجام است. لطفاً تا آماده‌شدن نتیجه صبر کن.",
            home_menu(),
        )
        return

    if state["step"] == "coupon_code":
        try:
            normalized = normalize_coupon_code(text)
        except ValueError as exc:
            await safe_send(chat_id, str(exc), back_home_menu()); return
        if not normalized:
            await safe_send(chat_id, "کد تخفیف خالی است. دوباره بفرست.", back_home_menu()); return
        coupon = await session.scalar(select(Coupon).where(Coupon.code == normalized, Coupon.active.is_(True)))
        if not coupon or (coupon.expires_at and coupon.expires_at <= datetime.utcnow()):
            await safe_send(chat_id, "این کد تخفیف معتبر یا فعال نیست.", back_home_menu()); return
        if coupon.discount_percent <= 0 and coupon.discount_toman <= 0:
            await safe_send(chat_id, "این کد تخفیف قابل استفاده نیست.", upgrade_menu()); return
        plans = [coupon.applicable_plan] if coupon.applicable_plan else ["starter", "pro", "studio", "studio_plus"]
        plans = [plan for plan in plans if plan in PLAN_PRICE and plan != user.plan]
        if not plans:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "این کد تخفیف برای پلن قابل خریدی از طرف تو قابل استفاده نیست.", upgrade_menu()); return
        PENDING[user.telegram_id] = {"step": "coupon_select_plan", "coupon_code": normalized, "plans": plans}
        discount = f"{coupon.discount_percent}%" if coupon.discount_percent else f"{coupon.discount_toman:,} تومان"
        await safe_send(chat_id, f"کد {normalized} معتبر است ✅\nتخفیف: {discount}\n\nپلن موردنظر را انتخاب کن:", coupon_plan_menu(plans))
        return

    if state["step"] == "coupon_select_plan":
        labels = {"💳 خرید استارتر": "starter", "💳 خرید پرو": "pro", "💳 خرید استودیو": "studio", "💳 خرید استودیو پلاس": "studio_plus"}
        plan = labels.get(text)
        if plan not in state.get("plans", []):
            await safe_send(chat_id, "یک پلن از گزینه‌های بالا انتخاب کن.", coupon_plan_menu(state.get("plans", []))); return
        try:
            order, payment_url = await start_payment(session, user, plan, coupon_code=state["coupon_code"])
        except (PaymentError, ValueError) as exc:
            await safe_send(chat_id, str(exc)[:500], upgrade_menu()); return
        PENDING.pop(user.telegram_id, None)
        await safe_send(chat_id, f"💳 سفارش آماده شد.\n\nپلن: {PLAN_LABELS[plan]}\nقیمت اصلی: {order.original_amount_toman:,} تومان\n🎟️ تخفیف: {order.discount_toman:,} تومان\nمبلغ قابل پرداخت: {order.amount_toman:,} تومان\n\nبا زدن دکمه زیر وارد صفحه پرداخت می‌شوی.", {"inline_keyboard": [[{"text": "💳 پرداخت", "url": payment_url}], [{"text": "خانه", "callback_data": "home"}]]})
        return

    if state["step"] == "niche":
        if not text:
            await safe_send(chat_id, "این بخش نمی‌تواند خالی باشد. دوباره بنویس.", home_menu())
            return
        if len(text) > PROFILE_LIMITS["niche"]:
            await safe_send(chat_id, f"حوزه فعالیت حداکثر {PROFILE_LIMITS['niche']} کاراکتر باشد. دوباره بنویس.", home_menu())
            return
        state["niche"] = text
        state["step"] = "audience"
        await safe_send(chat_id, "🎯 مخاطب اصلی برندت کیست؟", back_home_menu())
        return

    if state["step"] == "audience":
        if not text:
            await safe_send(chat_id, "این بخش نمی‌تواند خالی باشد. دوباره بنویس.", home_menu())
            return
        if len(text) > PROFILE_LIMITS["audience"]:
            await safe_send(chat_id, f"مخاطب اصلی حداکثر {PROFILE_LIMITS['audience']} کاراکتر باشد. دوباره بنویس.", home_menu())
            return
        state["audience"] = text
        state["step"] = "tone"
        await safe_send(chat_id, "🗣️ لحن برندت چطور باشد؟\nمثلاً: صمیمی، مینیمال، لوکس، جدی، شوخ", back_home_menu())
        return

    if state["step"] == "tone":
        if not text:
            await safe_send(chat_id, "لحن برند نمی‌تواند خالی باشد. دوباره بنویس.", home_menu())
            return
        if len(text) > PROFILE_LIMITS["tone"]:
            await safe_send(chat_id, f"لحن برند حداکثر {PROFILE_LIMITS['tone']} کاراکتر باشد. دوباره بنویس.", home_menu())
            return
        try:
            await set_profile(session, user, state["niche"], state["audience"], text)
        except ValueError as exc:
            await safe_send(chat_id, str(exc), home_menu())
            return
        PENDING.pop(user.telegram_id, None)
        await safe_send(chat_id, "پروفایل برند ذخیره شد ✅\nحالا می‌توانی از ابزارهای محتوا استفاده کنی.", main_menu())
        return

    if state["step"] == "profile_main_offer":
        if text == "⏭️ رد کردن":
            pass
        elif text:
            if len(text) > PROFILE_LIMITS["main_offer"]:
                await safe_send(chat_id, f"حداکثر {PROFILE_LIMITS['main_offer']} کاراکتر باشد.", back_home_menu())
                return
            state["main_offer"] = text
        else:
            await safe_send(chat_id, "این بخش را بنویس یا رد کن.", back_home_menu())
            return
        state["step"] = "profile_differentiator"
        await safe_send(chat_id, "✨ مزیت یا تفاوت اصلی برندت چیست؟", profile_optional_menu())
        return

    if state["step"] == "profile_differentiator":
        if text == "⏭️ رد کردن":
            pass
        elif text:
            if len(text) > PROFILE_LIMITS["differentiator"]:
                await safe_send(chat_id, f"حداکثر {PROFILE_LIMITS['differentiator']} کاراکتر باشد.", back_home_menu())
                return
            state["differentiator"] = text
        else:
            await safe_send(chat_id, "این بخش را بنویس یا رد کن.", back_home_menu())
            return
        state["step"] = "profile_content_goal"
        await safe_send(chat_id, "🎯 هدف اصلی‌ات از تولید محتوا چیست؟\nمثلاً: فروش، جذب مخاطب، اعتمادسازی", profile_optional_menu())
        return

    if state["step"] == "profile_content_goal":
        if text == "⏭️ رد کردن":
            pass
        elif text:
            if len(text) > PROFILE_LIMITS["content_goal"]:
                await safe_send(chat_id, f"حداکثر {PROFILE_LIMITS['content_goal']} کاراکتر باشد.", back_home_menu())
                return
            state["content_goal"] = text
        else:
            await safe_send(chat_id, "این بخش را بنویس یا رد کن.", back_home_menu())
            return
        try:
            await set_profile_additional(
                session, user,
                state.get("main_offer"),
                state.get("differentiator"),
                state.get("content_goal"),
            )
        except ValueError as exc:
            await safe_send(chat_id, str(exc), profile_menu())
            return
        PENDING.pop(user.telegram_id, None)
        await safe_send(chat_id, "تکمیل پروفایل ذخیره شد ✅", profile_menu())
        return

    if state["step"] == "studio_plus_kind":
        mapping = {"✨ کپشن Premium":"caption", "✨ پست Premium":"post", "✨ استوری Premium":"story", "✨ Reel Premium":"reel", "✨ معرفی محصول Premium":"product", "✨ ایده Premium":"ideas", "✨ بازنویسی Premium":"rewrite", "✨ کمپین Premium":"campaign", "📦 بسته هفتگی Premium":"pack"}
        if text == "↩️ برگشت":
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "خانه 🏠", main_menu())
            return
        if text not in mapping:
            await safe_send(chat_id, "یکی از ابزارهای Studio+ را انتخاب کن.", studio_plus_menu())
            return
        kind = mapping[text]
        if not await profile_complete(user):
            await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
            return
        ok, reason = await can_generate(session, user, kind, premium=True)
        if not ok:
            await safe_send(chat_id, reason, main_menu())
            return
        PENDING[user.telegram_id] = {"step": "studio_plus_request", "kind": kind, "premium": True}
        await safe_send(chat_id, tool_request_message(kind) + " ⭐", back_home_menu())
        return

    if state["step"] == "studio_plus_request":
        kind = state["kind"]
        if not text:
            await safe_send(chat_id, "درخواست نمی‌تواند خالی باشد.", back_home_menu())
            return
        if not await profile_complete(user):
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
            return
        ok, reason = await can_generate(session, user, kind, premium=True)
        if not ok:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, reason, main_menu())
            return
        length = DEFAULT_LENGTHS.get(kind, "medium")
        if kind == "caption":
            if any(x in text for x in ("کوتاه", "مختصر", "فشرده")):
                length = "short"
            elif any(x in text for x in ("طولانی", "مفصل", "کامل")):
                length = "long"
            else:
                length = "medium"
        variants = requested_variants(text, kind)
        if kind in {"ideas", "campaign"}:
            variants = 1
        PENDING[user.telegram_id] = {"step": "processing", "kind": kind, "premium": True}
        await send_processing_message(user.telegram_id, chat_id, f"دارم {KIND_LABELS[kind]} را آماده می‌کنم... ⏳", generation_processing_menu())
        start_background(process_generation(user.telegram_id, chat_id, kind, text, length, variants, True), user.telegram_id)
        return

    if state["step"] == "kind":
        if text == "🔄 بازنویسی":
            state["kind"] = "rewrite"
            state["step"] = "rewrite_request"
            await safe_send(chat_id, "متن یا درخواستت را بفرست؛ اگر برای ابزار AI دیگری پرامپت می‌خواهی، بنویس «پرامپت بنویس برای ...». ", back_home_menu())
            return
        if text not in BUTTON_TO_KIND:
            await safe_send(chat_id, "یکی از انواع محتوا را از منوی پایین انتخاب کن.", content_menu())
            return

        kind = BUTTON_TO_KIND[text]
        state["kind"] = kind
        state["length"] = "medium"
        state["step"] = "request"
        await safe_send(chat_id, GENERIC_TOOL_REQUEST_MESSAGE, back_home_menu())
        return

    if state["step"] == "transform_confirm":
        if not text:
            await safe_send(chat_id, "درخواست تبدیل نمی‌تواند خالی باشد.", home_menu())
            return
        combined = state["source"] + "\n\nدستور تبدیل/تغییر کاربر: " + text
        kind = state["kind"]
        if kind == "caption": length = "medium"
        elif kind == "reel": length = "medium"
        else: length = DEFAULT_LENGTHS.get(kind, "medium")
        ok, reason = await can_generate(session, user, kind)
        if not ok:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, reason, main_menu())
            return
        PENDING[user.telegram_id] = {"step": "processing", "kind": kind}
        await send_processing_message(user.telegram_id, chat_id, f"دارم آن را به {KIND_LABELS[kind]} تبدیل می‌کنم... ✨", generation_processing_menu())
        start_background(process_generation(user.telegram_id, chat_id, kind, combined, length, 1), user.telegram_id)
        return

    if state["step"] == "daily_request":
        access_ok, access_reason = await generation_access_status(session, user)
        if not access_ok:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, access_reason, main_menu())
            return
        if not text:
            await safe_send(chat_id, "موضوع نمی‌تواند خالی باشد. دوباره بنویس.", brief_menu())
            return
        existing = await get_daily_suggestion(session, user, date.today().isoformat())
        if existing:
            PENDING[user.telegram_id] = {"step": "daily_view", "suggestion_id": existing.id}
            await safe_send(chat_id, existing.output_text, daily_suggestion_menu(existing.id))
            return
        state["step"] = "processing"
        await send_processing_message(user.telegram_id, chat_id, "دارم پیشنهاد امروز را برای برندت می‌سازم... ✨", generation_processing_menu())
        start_background(process_daily_suggestion(user.telegram_id, chat_id, text), user.telegram_id)
        return

    if state["step"] == "daily_refine_request":
        if not text:
            await safe_send(chat_id, "درخواست تغییر نمی‌تواند خالی باشد. دوباره بنویس.", back_home_menu())
            return
        state["step"] = "processing"
        await send_processing_message(user.telegram_id, chat_id, "دارم پیشنهاد امروز را با تغییراتت بازطراحی می‌کنم... ✨", generation_processing_menu())
        start_background(process_generation(user.telegram_id, chat_id, "brief", state.get("prefill", "") + "\n\nدرخواست تغییر: " + text, "medium", 1), user.telegram_id)
        return

    if state["step"] == "brief_request":
        if not text:
            await safe_send(chat_id, "Brief نمی‌تواند خالی باشد. دوباره توضیح بده.", back_home_menu())
            return
        state["step"] = "processing"
        await send_processing_message(user.telegram_id, chat_id, "دارم بهترین مسیر محتوا را برای این درخواست پیدا می‌کنم... ✨", generation_processing_menu())
        start_background(process_generation(user.telegram_id, chat_id, "brief", (state.get("prefill", "") + "\n\nادامه/هدف جدید: " + text) if state.get("prefill") else text, "medium", 1), user.telegram_id)
        return

    if state["step"] == "campaign_request":
        if not text:
            await safe_send(chat_id, "هدف کمپین نمی‌تواند خالی باشد.", back_home_menu())
            return
        state["step"] = "processing"
        await send_processing_message(user.telegram_id, chat_id, "دارم ساختار کمپین را می‌چینم... 🚀", home_menu())
        start_background(process_generation(user.telegram_id, chat_id, "campaign", text, "medium", 1), user.telegram_id)
        return

    if state["step"] == "history_select":
        if text.startswith("#") and text[1:].isdigit():
            g = await get_generation(session, int(text[1:]), user.id)
            if g:
                PENDING[user.telegram_id] = {"step": "history_view", "generation_id": g.id, "return_to_history": True, "history_mode": state.get("history_mode", "recent")}
                await safe_send(chat_id, g.output_text, history_actions_menu(g.id, g.kind))
                return
        await safe_send(chat_id, "شماره خروجی را مثل #12 بفرست.", history_list_menu())
        return

    if state["step"] == "history_view":
        # While viewing one history item, allow the user to jump directly to
        # another item by sending its #ID. This keeps history navigation
        # consistent with the initial history list instead of treating the
        # new #ID as an unsupported menu action.
        if text.startswith("#") and text[1:].isdigit():
            target = await get_generation(session, int(text[1:]), user.id)
            if target:
                PENDING[user.telegram_id] = {
                    "step": "history_view",
                    "generation_id": target.id,
                    "return_to_history": True,
                    "history_mode": state.get("history_mode", "recent"),
                }
                await safe_send(chat_id, target.output_text, history_actions_menu(target.id, target.kind))
                return
            await safe_send(chat_id, "این خروجی پیدا نشد. شماره را مثل #12 بفرست.", history_actions_menu(g.id, g.kind))
            return
        if text.startswith("⭐ ذخیره #"):
            fav = await toggle_favorite(session, g)
            await safe_send(chat_id, ("ذخیره شد ⭐" if fav else "از ذخیره‌ها حذف شد."), history_actions_menu(g.id, g.kind))
            return
        if text.startswith("👍") or text.startswith("👎"):
            await set_feedback(session, g, "positive" if text.startswith("👍") else "negative")
            await safe_send(chat_id, "بازخوردت ثبت شد؛ ممنون. 🙌", history_actions_menu(g.id, g.kind))
            return
        if text.startswith("🔄 بازنویسی #"):
            PENDING[user.telegram_id] = {"step": "rewrite_request", "kind": "rewrite", "prefill": g.output_text, "return_generation_id": g.id}
            await safe_send(chat_id, "چه تغییری می‌خواهی روی این متن اعمال شود؟ اگر برای ابزار دیگری پرامپت می‌خواهی، بنویس «پرامپت بنویس برای ...». ", back_home_menu())
            return
        if text.startswith("🔗 ادامه #"):
            PENDING[user.telegram_id] = {"step": "brief_request", "kind": "brief", "prefill": g.output_text, "return_generation_id": g.id}
            await safe_send(chat_id, "اگر می‌خواهی این پیشنهاد را تغییر بدهم، بگو چه چیزی عوض شود.", back_home_menu())
            return
        if text == "🕘 محتوای من":
            PENDING[user.telegram_id] = None
            await safe_send(chat_id, "📚 محتوای من", history_menu())
            return
        await safe_send(chat_id, "از گزینه‌های پایین استفاده کن.", history_actions_menu(g.id, g.kind))
        return

    if state["step"] == "idea_request":
        if not text:
            await safe_send(chat_id, "موضوعت نمی‌تواند خالی باشد. دوباره بنویس.", home_menu())
            return
        ok, reason = await can_generate(session, user, "ideas")
        if not ok:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, reason, main_menu())
            return
        state["step"] = "processing"
        await send_processing_message(user.telegram_id, chat_id, "دارم ایده را آماده می‌کنم... ⏳", generation_processing_menu())
        start_background(process_generation(user.telegram_id, chat_id, "ideas", text, "medium", 1), user.telegram_id)
        return

    if state["step"] == "rewrite_request":
        if not text:
            await safe_send(chat_id, "متن نمی‌تواند خالی باشد. دوباره بفرست.", home_menu())
            return
        variants = requested_variants(text, "rewrite")
        state["step"] = "processing"
        await send_processing_message(user.telegram_id, chat_id, "دارم متن را بازنویسی می‌کنم... ⏳", generation_processing_menu())
        start_background(process_generation(user.telegram_id, chat_id, "rewrite", (state.get("prefill", "") + "\n\nدرخواست تغییر: " + text) if state.get("prefill") else text, "medium", variants), user.telegram_id)
        return

    if state["step"] == "ticket_select":
        if text.startswith("#") and text[1:].isdigit():
            ticket = await get_support_ticket(session, int(text[1:]), user.id)
            if ticket:
                PENDING[user.telegram_id] = {"step": "ticket_view", "ticket_id": ticket.id}
                status = "باز 🟢" if ticket.status == "open" else "بسته 🔴"
                await safe_send(chat_id, f"🎫 تیکت #{ticket.id}\n\nموضوع: {ticket.topic}\nوضعیت: {status}\n\n{ticket.message}", ticket_actions_menu(ticket.id, ticket.status == "open"))
                return
        await safe_send(chat_id, "شماره تیکت را مثل «#12» بفرست.", support_menu())
        return

    if state["step"] == "ticket_view":
        ticket = await get_support_ticket(session, state["ticket_id"], user.id)
        if not ticket:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "این تیکت پیدا نشد.", support_menu())
            return
        if text == f"✉️ ادامه تیکت #{ticket.id}" and ticket.status == "open":
            PENDING[user.telegram_id] = {"step": "ticket_reply", "ticket_id": ticket.id}
            await safe_send(chat_id, "پیامت را برای ادامه این تیکت بنویس:", back_home_menu())
            return
        if text == "🔒 بستن تیکت" and ticket.status == "open":
            await close_support_ticket(session, ticket.id)
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, f"تیکت #{ticket.id} بسته شد. 🔒", support_menu())
            return
        await safe_send(chat_id, "از گزینه‌های پایین استفاده کن.", ticket_actions_menu(ticket.id, ticket.status == "open"))
        return

    if state["step"] == "ticket_reply":
        if not text:
            await safe_send(chat_id, "پیامت نمی‌تواند خالی باشد.", back_home_menu())
            return
        if len(text) > SUPPORT_MESSAGE_LIMIT:
            await safe_send(chat_id, f"متن پیام حداکثر {SUPPORT_MESSAGE_LIMIT} کاراکتر باشد.", back_home_menu())
            return
        ticket = await get_support_ticket(session, state["ticket_id"], user.id)
        if not ticket or ticket.status != "open":
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "این تیکت بسته شده یا پیدا نشد.", support_menu())
            return
        await append_ticket_message(session, ticket, "user", text)
        PENDING.pop(user.telegram_id, None)
        await safe_send(chat_id, f"پیامت به تیکت #{ticket.id} اضافه شد. ✅", support_menu())
        if settings.admin_telegram_id:
            await safe_send(settings.admin_telegram_id, f"✉️ پیام جدید در تیکت #{ticket.id}\n\n📌 موضوع: {ticket.topic}\n👤 کاربر: {user.first_name or 'بدون نام'}\n\n{text}\n\nپاسخ: /reply_ticket {ticket.id}\nبستن: /close_ticket {ticket.id}", main_menu())
        return

    if state["step"] == "admin_reply":
        if not text:
            await safe_send(chat_id, "پاسخ نمی‌تواند خالی باشد.", main_menu())
            return
        if len(text) > SUPPORT_MESSAGE_LIMIT:
            await safe_send(chat_id, f"پاسخ حداکثر {SUPPORT_MESSAGE_LIMIT} کاراکتر باشد.", main_menu())
            return
        ticket = await get_support_ticket(session, state["ticket_id"])
        if not ticket:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "تیکت پیدا نشد.", main_menu())
            return
        await mark_ticket_first_response(session, user, ticket)
        await append_ticket_message(session, ticket, "admin", text)
        target_user = await session.get(User, ticket.user_id)
        PENDING.pop(user.telegram_id, None)
        if target_user:
            await safe_send(target_user.telegram_id, f"🛠️ پاسخ پشتیبانی به تیکت #{ticket.id}\n\n{text}", main_menu())
        await safe_send(chat_id, f"پاسخ تیکت #{ticket.id} ارسال شد. ✅", main_menu())
        return

    if state["step"] == "length":
        if text not in LENGTH_BUTTONS:
            await safe_send(chat_id, "یکی از گزینه‌های کوتاه، متوسط یا طولانی را انتخاب کن.", length_menu())
            return
        state["length"] = LENGTH_BUTTONS[text]
        state["step"] = "request"
        await safe_send(chat_id, "موضوع، محصول، هدف و هر اطلاعاتی که می‌خواهی در کپشن استفاده شود را بنویس.", back_home_menu())
        return

    if state["step"] == "support_topic":
        if text not in SUPPORT_TOPICS:
            await safe_send(chat_id, "یکی از موضوعات پشتیبانی را انتخاب کن.", support_topic_menu())
            return
        state["topic"] = SUPPORT_TOPICS[text]
        state["step"] = "support_message"
        await safe_send(
            chat_id,
            f"📝 توضیح درخواستت را برای «{state['topic']}» بنویس.\n\nحداکثر {SUPPORT_MESSAGE_LIMIT} کاراکتر. لطفاً اطلاعات حساس مثل رمز عبور را ارسال نکن.",
            home_menu(),
        )
        return

    if state["step"] == "support_message":
        if not text:
            await safe_send(chat_id, "توضیحت نمی‌تواند خالی باشد. دوباره بنویس.", back_home_menu())
            return
        if len(text) > SUPPORT_MESSAGE_LIMIT:
            await safe_send(chat_id, f"متن تیکت حداکثر {SUPPORT_MESSAGE_LIMIT} کاراکتر باشد. دوباره کوتاه‌تر بنویس.", back_home_menu())
            return
        ok, reason = await support_rate_limit(session, user)
        if not ok:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, reason, main_menu())
            return
        ticket = await create_support_ticket(session, user, state["topic"], text)
        PENDING.pop(user.telegram_id, None)
        await safe_send(chat_id, f"تیکتت ثبت شد ✅\n\nشماره تیکت: #{ticket.id}\nموضوع: {ticket.topic}\n\nدرخواستت برای پشتیبانی ارسال شد.", main_menu())

        if settings.admin_telegram_id:
            admin_text = (
                f"🆘 تیکت جدید #{ticket.id}\n\n"
                f"👤 کاربر: {user.first_name or 'بدون نام'}\n"
                f"🆔 Telegram ID: {user.telegram_id}\n"
                f"🔗 Username: @{user.username if user.username else 'ندارد'}\n"
                f"📌 موضوع: {ticket.topic}\n\n"
                f"📝 متن درخواست:\n{ticket.message}\n\n"
                f"برای بستن: /close_ticket {ticket.id}"
            )
            await safe_send(settings.admin_telegram_id, admin_text, main_menu())
        return

    if state["step"] == "image_request":
        if not text:
            await safe_send(chat_id, "توضیح تصویر نمی‌تواند خالی باشد. دوباره بنویس.", back_home_menu())
            return
        ok, reason = await can_generate(
            session,
            user,
            "image",
            premium=bool(state.get("premium")),
        )
        if not ok:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, reason, main_menu())
            return
        PENDING[user.telegram_id] = {"step": "processing", "kind": "image", "premium": bool(state.get("premium"))}
        await send_processing_message(user.telegram_id, chat_id, "دارم تصویرت را می‌سازم... 🖼️✨", generation_processing_menu())
        async def do_image():
            try:
                image_bytes = await generate_image({"niche": user.niche, "audience": user.audience, "tone": user.tone, "main_offer": user.main_offer, "differentiator": user.differentiator, "content_goal": user.content_goal}, text, model_override=(settings.studio_plus_image_model if state.get("premium") else None))
                await send_photo_bytes(chat_id, image_bytes, "تصویرت آماده شد. ✨" + (" ⭐" if state.get("premium") else ""))
                async with SessionLocal() as image_session:
                    image_user = await get_user(image_session, user.telegram_id)
                    if not image_user:
                        raise RuntimeError("کاربر برای ثبت تصویر پیدا نشد.")
                    await record_generation(
                        image_session,
                        image_user,
                        "image",
                        text,
                        "[image generated]",
                        premium=bool(state.get("premium")),
                    )
                await clear_processing_message(user.telegram_id)
                PENDING.pop(user.telegram_id, None)
            except asyncio.CancelledError:
                await clear_processing_message(user.telegram_id)
                PENDING.pop(user.telegram_id, None)
                await safe_send(chat_id, "درخواست لغو شد. اعتبارت کم نشد. ✅", main_menu())
                raise
            except Exception as exc:
                await clear_processing_message(user.telegram_id)
                PENDING.pop(user.telegram_id, None)
                print(f"Image generation error for {user.telegram_id}: {type(exc).__name__}: {exc}")
                await safe_send(chat_id, "ساخت تصویر انجام نشد. دوباره تلاش کن.", main_menu())
        start_background(do_image(), user.telegram_id)
        return

    if state["step"] == "pack_request":
        kind = "pack"
        if not await profile_complete(user):
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
            return

        ok, reason = await can_generate(session, user, kind)
        if not ok:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, reason, main_menu())
            return

        max_input = INPUT_LIMITS.get(kind, 1800)
        if len(text) > max_input:
            await safe_send(chat_id, f"متن درخواست برای {KIND_LABELS[kind]} حداکثر {max_input} کاراکتر باشد. لطفاً کوتاه‌ترش کن.", home_menu())
            return

        state["step"] = "processing"
        PENDING[user.telegram_id] = state
        await send_processing_message(
            user.telegram_id,
            chat_id,
            "دارم بسته ۷ روزه را آماده می‌کنم؛ ممکن است کمی بیشتر زمان ببرد. ⏳",
            generation_processing_menu(),
        )
        start_background(
            process_generation(
                user.telegram_id,
                chat_id,
                kind,
                text,
                "medium",
                1,
            ),
            user.telegram_id,
        )
        return

    if state["step"] == "request":
        kind = state["kind"]
        length = state["length"]
        if kind == "caption":
            if any(x in text for x in ("کوتاه", "مختصر", "فشرده")):
                length = "short"
            elif any(x in text for x in ("طولانی", "مفصل", "کامل")):
                length = "long"
            else:
                length = "medium"

        if not await profile_complete(user):
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, "برای استفاده از این بخش، لطفاً اول پروفایل برندت را کامل کن. 👤", main_menu())
            return

        ok, reason = await can_generate(session, user, kind)
        if not ok:
            PENDING.pop(user.telegram_id, None)
            await safe_send(chat_id, reason, main_menu())
            return

        state["step"] = "processing"
        PENDING[user.telegram_id] = state
        await send_processing_message(
            user.telegram_id,
            chat_id,
            f"دارم {KIND_LABELS[kind]} را آماده می‌کنم... ⏳",
            generation_processing_menu(),
        )
        variants = requested_variants(text, kind)
        start_background(
            process_generation(
                user.telegram_id,
                chat_id,
                kind,
                text,
                length,
                variants,
            ),
            user.telegram_id,
        )
        return

    if state["step"] == "processing":
        await safe_send(
            chat_id,
            "درخواست قبلی هنوز در حال آماده‌شدن است. کمی صبر کن. ⏳",
            home_menu(),
        )


async def process_daily_suggestion(telegram_id: int, chat_id: int, request: str) -> None:
    async with SessionLocal() as session:
        user = await get_user(session, telegram_id)
        if not user:
            PENDING.pop(telegram_id, None)
            return
        try:
            output = await generate(
                {"niche": user.niche, "audience": user.audience, "tone": user.tone, "main_offer": user.main_offer, "differentiator": user.differentiator, "content_goal": user.content_goal, "plan": user.plan},
                "daily_suggestion", request, "medium", 1,
            )
            item = await save_daily_suggestion(session, user, date.today().isoformat(), output, request)
            await clear_processing_message(telegram_id)
            PENDING[telegram_id] = {"step": "daily_view", "suggestion_id": item.id}
            await safe_send(chat_id, output, daily_suggestion_menu(item.id))
        except asyncio.CancelledError:
            await clear_processing_message(telegram_id)
            PENDING.pop(telegram_id, None)
            await safe_send(chat_id, "درخواست لغو شد. اعتبارت کم نشد. ✅", main_menu())
            raise
        except httpx.ReadTimeout:
            await clear_processing_message(telegram_id)
            PENDING.pop(telegram_id, None)
            await safe_send(chat_id, "ساخت پیشنهاد امروز طول کشید و کامل نشد. دوباره تلاش کن.", main_menu())
        except Exception as exc:
            await clear_processing_message(telegram_id)
            PENDING.pop(telegram_id, None)
            print(f"Daily suggestion error for {telegram_id}: {type(exc).__name__}: {exc}")
            await safe_send(chat_id, "ساخت پیشنهاد امروز انجام نشد. لطفاً دوباره تلاش کن.", main_menu())


async def process_generation(
    telegram_id: int,
    chat_id: int,
    kind: str,
    request: str,
    length: str,
    requested_variant_count: int | None = None,
    premium: bool = False,
) -> None:
    async with SessionLocal() as session:
        user = await get_user(session, telegram_id)
        if not user:
            PENDING.pop(telegram_id, None)
            return

        try:
            variants = requested_variant_count or 1
            variants = max(1, min(variants, 2))
            if kind in {"pack", "ideas", "brief", "campaign"}:
                variants = 1

            output = await generate(
                {
                    "niche": user.niche,
                    "audience": user.audience,
                    "tone": user.tone,
                    "main_offer": user.main_offer,
                    "differentiator": user.differentiator,
                    "content_goal": user.content_goal,
                    "plan": user.plan,
                },
                kind,
                request,
                length,
                variants,
                model_override=(settings.studio_plus_ai_model if premium else None),
            )

            generation = await record_generation(session, user, kind, request, output, premium=premium)
            await clear_processing_message(telegram_id)
            PENDING[telegram_id] = {"step": "history_view", "generation_id": generation.id, "return_to_history": False}
            if kind == "brief":
                markup = brief_result_menu(generation.id)
            else:
                markup = inline_generation_actions(generation.id, kind)
            if kind in {"pack", "campaign"}:
                await safe_send(chat_id, render_chain_output(output, generation.id, kind, premium=premium), markup, parse_mode="HTML")
            else:
                await safe_send(chat_id, output + ("\n\n⭐" if premium else ""), markup)

        except asyncio.CancelledError:
            await clear_processing_message(telegram_id)
            PENDING.pop(telegram_id, None)
            await safe_send(chat_id, "درخواست لغو شد. اعتبارت کم نشد. ✅", main_menu())
            raise
        except httpx.ReadTimeout:
            await clear_processing_message(telegram_id)
            PENDING.pop(telegram_id, None)
            await safe_send(
                chat_id,
                "تولید این محتوا بیشتر از زمان مجاز طول کشید. برای بسته‌های بزرگ‌تر بعداً صف پردازش جداگانه اضافه می‌کنیم.",
                main_menu(),
            )
        except Exception as exc:
            await clear_processing_message(telegram_id)
            PENDING.pop(telegram_id, None)
            print(f"Generation error for {telegram_id}: {type(exc).__name__}: {exc}")
            await safe_send(
                chat_id,
                "تولید محتوا انجام نشد. لطفاً دوباره تلاش کن.",
                main_menu(),
            )


async def handle_callback_update(callback_query: dict) -> None:
    callback_id = callback_query["id"]
    data = callback_query.get("data", "")
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    tg_user = callback_query.get("from", {})
    if not chat_id or not tg_user.get("id"):
        await answer_callback(callback_id)
        return

    async with SessionLocal() as session:
        user = await get_user(session, tg_user["id"])
        if user and user.is_banned and not is_admin(user):
            await answer_callback(callback_id, "دسترسی این حساب مسدود شده است.")
            return
        if not user:
            await answer_callback(callback_id, "ابتدا /start را بزن.")
            return
        access_ok, access_reason = await bot_access_status(session, user)
        if not access_ok:
            await answer_callback(callback_id, access_reason[:180])
            return
        try:
            if data == "cancel_generation":
                cancelled = await cancel_active_generation(user.telegram_id)
                if cancelled:
                    await answer_callback(callback_id, "درخواست برای لغو علامت‌گذاری شد.")
                else:
                    await answer_callback(callback_id, "درخواستی برای لغو پیدا نشد.")
                return
            if data == "home":
                PENDING.pop(user.telegram_id, None)
                await answer_callback(callback_id)
                await safe_send(chat_id, home_suggestion(user), main_menu())
                return
            if data == "coupon:start":
                PENDING[user.telegram_id] = {"step": "coupon_code"}
                await answer_callback(callback_id)
                await safe_send(chat_id, "🎟️ کد تخفیف را بفرست:", back_home_menu())
                return
            if data.startswith("buy:"):
                plan = data.split(":", 1)[1]
                if plan not in PLAN_PRICE:
                    await answer_callback(callback_id, "پلن نامعتبر است.")
                    return
                if user.plan == plan:
                    await answer_callback(callback_id, "این پلن همین حالا فعال است.")
                    return
                try:
                    order, payment_url = await start_payment(session, user, plan)
                except (PaymentError, ValueError) as exc:
                    await answer_callback(callback_id, str(exc)[:180])
                    return
                await answer_callback(callback_id, "سفارش پرداخت آماده شد.")
                await safe_send(
                    chat_id,
                    f"💳 سفارش #{order.id} آماده است.\n\n"
                    f"پلن: {PLAN_LABELS.get(plan, plan)}\n"
                    + (f"قیمت اصلی: {order.original_amount_toman:,} تومان\n🎟️ تخفیف: {order.discount_toman:,} تومان\n" if order.discount_toman else "")
                    + f"مبلغ قابل پرداخت: {order.amount_toman:,} تومان\n\n"
                    + "برای پرداخت روی دکمه زیر بزن. بعد از بازگشت، پرداخت به‌صورت خودکار تأیید می‌شود.",
                    {"inline_keyboard": [[{"text": "💳 پرداخت", "url": payment_url}], [{"text": "خانه", "callback_data": "home"}]]},
                )
                return
            if data.startswith("upgrade_help:"):
                topic = data.split(":", 1)[1]
                if topic != "credit":
                    await answer_callback(callback_id, "این راهنما دیگر فعال نیست.")
                    return
                await answer_callback(callback_id)
                await safe_send(chat_id, HELP_TEXTS["🎟️ راهنمای اعتبار"], upgrade_menu())
                return
            if data == "history":
                await answer_callback(callback_id)
                items = await list_recent_generations(session, user, 8)
                if not items:
                    await safe_send(chat_id, "هنوز خروجی‌ای نداری. ✨", history_menu())
                else:
                    lines = ["📚 محتوای من", "", *[f"#{g.id} — {KIND_LABELS.get(g.kind, g.kind)} — {history_hint(g.output_text)}" for g in items], "", "برای دیدن یک خروجی، شماره‌اش را مثل #12 بفرست."]
                    PENDING[user.telegram_id] = {"step": "history_select", "history_mode": "recent"}
                    await safe_send(chat_id, "\n".join(lines), history_list_menu())
                return
            if data.startswith("fb:") or data.startswith("fav:"):
                parts = data.split(":")
                gid = int(parts[1])
                g = await get_generation(session, gid, user.id)
                if not g:
                    await answer_callback(callback_id, "خروجی پیدا نشد.")
                    return
                if data.startswith("fb:"):
                    await set_feedback(session, g, "positive" if parts[2] == "p" else "negative")
                    await answer_callback(callback_id, "بازخوردت ثبت شد 🙌")
                else:
                    fav = await toggle_favorite(session, g)
                    await answer_callback(callback_id, "ذخیره شد ⭐" if fav else "از ذخیره‌ها حذف شد.")
                return
            if data.startswith("rw:"):
                if user.telegram_id in ACTIVE_GENERATION_USERS:
                    await answer_callback(callback_id, "یک درخواست هنوز در حال انجام است. صبر کن.")
                    return
                gid = int(data.split(":")[1])
                g = await get_generation(session, gid, user.id)
                if not g:
                    await answer_callback(callback_id, "خروجی پیدا نشد.")
                    return
                PENDING[user.telegram_id] = {"step": "rewrite_request", "kind": "rewrite", "prefill": g.output_text, "return_generation_id": g.id}
                await answer_callback(callback_id)
                await safe_send(chat_id, "چه تغییری می‌خواهی روی این متن اعمال شود؟ اگر برای ابزار دیگری پرامپت می‌خواهی، بنویس «پرامپت بنویس برای ...». ", back_home_menu())
                return
            if data.startswith("tr:") or data.startswith("daily:"):
                if user.telegram_id in ACTIVE_GENERATION_USERS:
                    await answer_callback(callback_id, "یک درخواست هنوز در حال انجام است. صبر کن.")
                    return
                parts = data.split(":")
                gid = int(parts[1]); kind = parts[2]
                source = None
                if parts[0] == "daily":
                    item = await session.get(DailySuggestion, gid)
                    if not item or item.user_id != user.id:
                        await answer_callback(callback_id, "پیشنهاد پیدا نشد.")
                        return
                    source = item.output_text
                else:
                    g = await get_generation(session, gid, user.id)
                    if not g:
                        await answer_callback(callback_id, "خروجی پیدا نشد.")
                        return
                    source = g.output_text
                ok, reason = await can_generate(session, user, kind)
                if not ok:
                    await answer_callback(callback_id, reason[:180])
                    return
                await answer_callback(callback_id, "در حال آماده‌سازی…")
                PENDING[user.telegram_id] = {"step": "processing", "kind": kind}
                await send_processing_message(user.telegram_id, chat_id, f"دارم {KIND_LABELS[kind]} را از این محتوا آماده می‌کنم... ✨", generation_processing_menu())
                variants = 1
                start_background(process_generation(user.telegram_id, chat_id, kind, source, DEFAULT_LENGTHS.get(kind, "medium"), variants), user.telegram_id)
                return
            if data.startswith("refine:"):
                if user.telegram_id in ACTIVE_GENERATION_USERS:
                    await answer_callback(callback_id, "یک درخواست هنوز در حال انجام است. صبر کن.")
                    return
                gid = int(data.split(":")[1])
                g = await get_generation(session, gid, user.id)
                if g:
                    PENDING[user.telegram_id] = {"step": "brief_request", "kind": "brief", "prefill": g.output_text, "return_generation_id": g.id}
                    await answer_callback(callback_id)
                    await safe_send(chat_id, "اگر می‌خواهی این پیشنهاد را تغییر بدهم، بگو چه چیزی عوض شود.", back_home_menu())
                    return
                daily = await session.get(DailySuggestion, gid)
                if daily and daily.user_id == user.id:
                    PENDING[user.telegram_id] = {"step": "daily_refine_request", "kind": "brief", "prefill": daily.output_text, "return_daily_id": daily.id}
                    await answer_callback(callback_id)
                    await safe_send(chat_id, "اگر می‌خواهی این پیشنهاد را تغییر بدهم، بگو چه چیزی عوض شود.", back_home_menu())
                    return
            await answer_callback(callback_id)
        except Exception as exc:
            print(f"Callback error for {user.telegram_id}: {type(exc).__name__}: {exc}")
            await answer_callback(callback_id, "انجام نشد؛ دوباره تلاش کن.")


async def verify_database_connection() -> None:
    """Fail early with a clear message when the configured PostgreSQL is unavailable."""
    from sqlalchemy import text

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise RuntimeError(
            "PostgreSQL is unavailable. Start the Docker container 'mohtavachi-postgres' "
            "and make sure localhost:5432 is reachable."
        ) from exc


async def polling_loop() -> None:
    await verify_database_connection()
    print("PostgreSQL connection: PASS")
    offset = None
    telegram_bot = get_bot()
    try:
        await configure_bot_profile()
        print("Bot profile description: PASS")
    except Exception as exc:
        print(f"Bot profile description update skipped: {type(exc).__name__}: {exc}")

    try:
        while True:
            try:
                updates = await telegram_bot.get_updates(
                    offset=offset,
                    timeout=30,
                    allowed_updates=["message", "callback_query"],
                )
            except Exception as exc:
                print(f"Telegram polling error: {type(exc).__name__}: {exc}")
                await asyncio.sleep(3)
                continue

            for update in updates:
                offset = update.update_id + 1
                try:
                    await handle_update(update.model_dump(by_alias=True, exclude_none=True))
                except Exception as exc:
                    # Keep one bad update from killing the polling loop and make
                    # handler failures distinguishable from network failures.
                    print(f"Update handling error: {type(exc).__name__}: {exc}")
    finally:
        await close_bot()


if __name__ == "__main__":
    asyncio.run(polling_loop())
