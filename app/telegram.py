from __future__ import annotations

from aiogram import Bot
from aiogram.types import BufferedInputFile

from .config import settings

# Lazily create the shared Bot inside the running asyncio event loop.
# Constructing Bot during module import can create its aiohttp session before
# the application's event loop exists, which is problematic on Windows.
_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=settings.bot_token)
    return _bot



BOT_SHORT_DESCRIPTION = "✨ محتواچی؛ کارخونه محتوای برندت، مستقیم داخل تلگرام."
BOT_DESCRIPTION = (
    "╭─ ✨ محتواچی\n"
    "│ کارخونه محتوای برندت داخل تلگرام\n"
    "├─ ✍️ کپشن و پست\n"
    "├─ 📱 استوری و سناریوی Reel\n"
    "├─ 💡 ایده و مسیر محتوا\n"
    "├─ 📦 بسته هفتگی و کمپین\n"
    "├─ 🖼️ تولید تصویر\n"
    "╰─ 🔄 بازنویسی و تبدیل محتوا"
)

async def configure_bot_profile() -> None:
    bot = get_bot()
    await bot.set_my_short_description(
        short_description=BOT_SHORT_DESCRIPTION, language_code="fa"
    )
    await bot.set_my_description(
        description=BOT_DESCRIPTION, language_code="fa"
    )

async def close_bot() -> None:
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None


async def send_message(
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
    timeout: float = 30,
    parse_mode: str | None = None,
) -> dict:
    message = await get_bot().send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
        disable_web_page_preview=True,
        request_timeout=timeout,
    )
    return message.model_dump(by_alias=True, exclude_none=True)


async def delete_message(chat_id: int, message_id: int, timeout: float = 15) -> None:
    try:
        await get_bot().delete_message(chat_id=chat_id, message_id=message_id, request_timeout=timeout)
    except Exception as exc:
        # Deleting a transient progress message is best-effort; never break the generation.
        print(f"Telegram deleteMessage failed: {type(exc).__name__}: {exc}")


async def send_photo_bytes(
    chat_id: int,
    image_bytes: bytes,
    caption: str | None = None,
    timeout: float = 120,
) -> None:
    photo = BufferedInputFile(image_bytes, filename="image.png")
    await get_bot().send_photo(
        chat_id=chat_id,
        photo=photo,
        caption=caption,
        request_timeout=timeout,
    )


async def answer_callback(callback_query_id: str, text: str | None = None) -> None:
    await get_bot().answer_callback_query(
        callback_query_id=callback_query_id,
        text=text,
        request_timeout=15,
    )

def main_menu() -> dict:
    return {"keyboard": [
        [{"text": "👤 پروفایل برند"}, {"text": "✨ پیشنهاد امروز"}],
        [{"text": "✍️ تولید محتوا"}],
        [{"text": "🧭 مسیر محتوا"}, {"text": "💡 ایده محتوا"}],
        [{"text": "📦 بسته هفتگی"}, {"text": "🚀 کمپین"}],
        [{"text": "📚 محتوای من"}, {"text": "💎 ارتقا"}],
        [{"text": "✨ استودیو پلاس"}],
        [{"text": "👤 حساب من"}],
    ], "resize_keyboard": True}

def studio_plus_menu() -> dict:
    return {"keyboard": [
        [{"text": "✨ کپشن Premium"}, {"text": "✨ پست Premium"}],
        [{"text": "✨ استوری Premium"}, {"text": "✨ Reel Premium"}],
        [{"text": "✨ معرفی محصول Premium"}, {"text": "✨ ایده Premium"}],
        [{"text": "✨ بازنویسی Premium"}, {"text": "✨ کمپین Premium"}],
        [{"text": "📦 بسته هفتگی Premium"}],
        [{"text": "🖼️ Studio+ عکس"}],
        [{"text": "↩️ برگشت"}, {"text": "خانه"}],
    ], "resize_keyboard": True, "is_persistent": True}


def account_menu() -> dict:
    return {"keyboard": [
        [{"text": "📊 وضعیت من"}, {"text": "🎁 دعوت دوستان"}],
        [{"text": "📚 راهنما"}, {"text": "🆘 پشتیبانی"}],
        [{"text": "خانه"}],
    ], "resize_keyboard": True, "is_persistent": True}

def remove_keyboard() -> dict:
    return {"remove_keyboard": True}

def home_menu() -> dict:
    return {"keyboard": [[{"text": "خانه"}]], "resize_keyboard": True, "is_persistent": True}

def generation_processing_menu() -> dict:
    return {"inline_keyboard": [[{"text": "✖️ لغو درخواست", "callback_data": "cancel_generation"}]]}

def back_home_menu() -> dict:
    return {"keyboard": [[{"text": "↩️ برگشت"}, {"text": "خانه"}]], "resize_keyboard": True, "is_persistent": True}

def profile_flow_menu() -> dict:
    return back_home_menu()

def profile_menu() -> dict:
    return {"keyboard": [[{"text": "✏️ تغییر پروفایل"}], [{"text": "🧩 تکمیل پروفایل"}], [{"text": "خانه"}]], "resize_keyboard": True}

def profile_edit_menu() -> dict:
    return back_home_menu()

def profile_optional_menu() -> dict:
    return {"keyboard": [[{"text": "⏭️ رد کردن"}], [{"text": "↩️ برگشت"}, {"text": "خانه"}]], "resize_keyboard": True, "is_persistent": True}

def content_menu() -> dict:
    return {"keyboard": [[{"text": "کپشن"}, {"text": "پست"}], [{"text": "استوری"}, {"text": "سناریوی Reel"}], [{"text": "معرفی محصول"}, {"text": "🔄 بازنویسی"}], [{"text": "🖼️ تولید عکس"}], [{"text": "خانه"}]], "resize_keyboard": True, "is_persistent": True}

def length_menu() -> dict:
    return back_home_menu() | {"keyboard": [[{"text": "کوتاه"}, {"text": "متوسط"}, {"text": "طولانی"}], [{"text": "↩️ برگشت"}, {"text": "خانه"}]]}

def pack_menu() -> dict:
    return back_home_menu()

def support_menu() -> dict:
    return {"keyboard": [[{"text": "🆕 تیکت جدید"}, {"text": "🎫 تیکت‌های من"}], [{"text": "↩️ برگشت"}, {"text": "خانه"}]], "resize_keyboard": True, "is_persistent": True}

def support_topic_menu() -> dict:
    return {"keyboard": [[{"text": "💳 مشکل پرداخت"}, {"text": "⚙️ مشکل فنی"}], [{"text": "🎟️ مشکل اعتبار یا پلن"}, {"text": "💡 پیشنهاد یا انتقاد"}], [{"text": "❓ سایر موارد"}], [{"text": "↩️ برگشت"}, {"text": "خانه"}]], "resize_keyboard": True}

def ticket_actions_menu(ticket_id: int, can_reply: bool = True) -> dict:
    rows = []
    if can_reply:
        rows.append([{"text": f"✉️ ادامه تیکت #{ticket_id}"}])
    rows += [[{"text": "🔒 بستن تیکت"}], [{"text": "🎫 تیکت‌های من"}, {"text": "↩️ برگشت"}], [{"text": "خانه"}]]
    return {"keyboard": rows, "resize_keyboard": True}

def history_menu() -> dict:
    return {"keyboard": [[{"text": "🕘 اخیر"}, {"text": "⭐ ذخیره‌شده‌ها"}], [{"text": "خانه"}]], "resize_keyboard": True}

def history_list_menu() -> dict:
    return back_home_menu()

def inline_generation_actions(generation_id: int, kind: str) -> dict:
    rows = [
        [{"text": "👍", "callback_data": f"fb:{generation_id}:p"}, {"text": "👎", "callback_data": f"fb:{generation_id}:n"}],
    ]
    # Campaign/weekly-pack outputs use inline links embedded in the text.
    # Keep only feedback buttons so no obsolete action panel is attached below.
    if kind in {"pack", "campaign"}:
        return {"inline_keyboard": rows}
    if kind not in {"ideas"}:
        rows.append([
            {"text": "🔄 بازنویسی", "callback_data": f"rw:{generation_id}"},
            {"text": "⭐ ذخیره", "callback_data": f"fav:{generation_id}"},
        ])
    if kind in {"post", "story", "reel", "caption", "product"}:
        rows.append([
            {"text": "📝 پست", "callback_data": f"tr:{generation_id}:post"},
            {"text": "✍️ کپشن", "callback_data": f"tr:{generation_id}:caption"},
        ])
        rows.append([
            {"text": "📱 استوری", "callback_data": f"tr:{generation_id}:story"},
        ])
        rows.append([
            {"text": "🎬 سناریوی Reel", "callback_data": f"tr:{generation_id}:reel"},
            {"text": "🛍️ معرفی محصول", "callback_data": f"tr:{generation_id}:product"},
        ])
    if kind == "ideas":
        rows.append([{"text": "⭐ ذخیره", "callback_data": f"fav:{generation_id}"}, {"text": "خانه", "callback_data": "home"}])
    else:
        rows.append([{"text": "📚 محتوای من", "callback_data": "history"}, {"text": "خانه", "callback_data": "home"}])
    return {"inline_keyboard": rows}

def history_actions_menu(generation_id: int, kind: str) -> dict:
    return inline_generation_actions(generation_id, kind)

def brief_menu() -> dict:
    return {"keyboard": [[{"text": "✨ پیشنهاد محتوا"}], [{"text": "خانه"}]], "resize_keyboard": True}

def brief_result_menu(generation_id: int) -> dict:
    return {"inline_keyboard": [
        [{"text": "✍️ ساخت کپشن", "callback_data": f"tr:{generation_id}:caption"}, {"text": "🎬 سناریوی Reel", "callback_data": f"tr:{generation_id}:reel"}],
        [{"text": "📱 ساخت استوری", "callback_data": f"tr:{generation_id}:story"}, {"text": "🛍️ معرفی محصول", "callback_data": f"tr:{generation_id}:product"}],
        [{"text": "🎯 تغییر پیشنهاد", "callback_data": f"refine:{generation_id}"}],
        [{"text": "👍", "callback_data": f"fb:{generation_id}:p"}, {"text": "👎", "callback_data": f"fb:{generation_id}:n"}],
        [{"text": "خانه", "callback_data": "home"}],
    ]}

def daily_suggestion_menu(generation_id: int) -> dict:
    return {"inline_keyboard": [
        [{"text": "✍️ کپشن", "callback_data": f"daily:{generation_id}:caption"}, {"text": "🎬 سناریوی Reel", "callback_data": f"daily:{generation_id}:reel"}],
        [{"text": "📱 استوری", "callback_data": f"daily:{generation_id}:story"}, {"text": "🛍️ معرفی محصول", "callback_data": f"daily:{generation_id}:product"}],
        [{"text": "🔄 بازنویسی ایده", "callback_data": f"refine:{generation_id}"}],
        [{"text": "خانه", "callback_data": "home"}],
    ]}

def feedback_menu(generation_id: int) -> dict:
    return inline_generation_actions(generation_id, "post")

def campaign_menu() -> dict:
    return home_menu()

def upgrade_menu() -> dict:
    return {"inline_keyboard": [
        [{"text": "💳 خرید استارتر", "callback_data": "buy:starter"}],
        [{"text": "💳 خرید پرو", "callback_data": "buy:pro"}],
        [{"text": "💳 خرید استودیو", "callback_data": "buy:studio"}],
        [{"text": "✨ خرید استودیو پلاس", "callback_data": "buy:studio_plus"}],
        [{"text": "🎟️ کد تخفیف", "callback_data": "coupon:start"}],
        [{"text": "🎟️ راهنمای اعتبار", "callback_data": "upgrade_help:credit"}],
    ]}


def admin_menu(role: str = "admin") -> dict:
    if role == "support":
        rows = [
            [{"text": "🎫 تیکت‌ها"}],
            [{"text": "خانه"}],
        ]
    elif role == "admin":
        rows = [
            [{"text": "👤 کاربران"}, {"text": "📊 آمار"}],
            [{"text": "🎫 تیکت‌ها"}, {"text": "📣 بازاریابی"}],
            [{"text": "⚙️ فعالیت ربات"}],
            [{"text": "خانه"}],
        ]
    else:
        rows = [
            [{"text": "👤 کاربران"}, {"text": "📊 آمار"}],
            [{"text": "🎫 تیکت‌ها"}, {"text": "📣 بازاریابی"}],
            [{"text": "📜 گزارش عملیات"}, {"text": "⚙️ فعالیت ربات"}],
            [{"text": "👮 مدیریت نقش‌ها"}],
            [{"text": "خانه"}],
        ]
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


def admin_marketing_menu() -> dict:
    return {"keyboard": [
        [{"text": "📣 ارسال پیام"}, {"text": "🎟️ کدهای تخفیف"}],
        [{"text": "↩️ پنل مدیریت"}, {"text": "خانه"}],
    ], "resize_keyboard": True, "is_persistent": True}


def admin_broadcast_segment_menu() -> dict:
    return {"keyboard": [
        [{"text": "👥 همه کاربران"}],
        [{"text": "🆓 رایگان"}, {"text": "🟦 استارتر"}],
        [{"text": "🟪 پرو"}, {"text": "🟫 استودیو"}],
        [{"text": "✨ استودیو پلاس"}],
        [{"text": "🔥 فعال ۷ روز اخیر"}, {"text": "💤 غیرفعال ۳۰ روزه"}],
        [{"text": "↩️ بازاریابی"}, {"text": "خانه"}],
    ], "resize_keyboard": True, "is_persistent": True}


def admin_broadcast_confirm_menu() -> dict:
    return {"keyboard": [[{"text": "✅ ارسال کن"}, {"text": "❌ لغو"}], [{"text": "↩️ بازاریابی"}]], "resize_keyboard": True}


def admin_coupon_menu() -> dict:
    return {"keyboard": [
        [{"text": "➕ ساخت کد"}, {"text": "📋 کدهای فعال"}],
        [{"text": "⛔ غیرفعال‌کردن کد"}],
        [{"text": "↩️ بازاریابی"}, {"text": "خانه"}],
    ], "resize_keyboard": True, "is_persistent": True}


def admin_coupon_type_menu() -> dict:
    return {"keyboard": [[{"text": "٪ درصدی"}, {"text": "💵 مبلغ ثابت"}], [{"text": "↩️ بازاریابی"}]], "resize_keyboard": True}


def admin_coupon_plan_menu() -> dict:
    return {"keyboard": [
        [{"text": "همه پلن‌ها"}],
        [{"text": "استارتر"}, {"text": "پرو"}],
        [{"text": "استودیو"}, {"text": "استودیو پلاس"}],
        [{"text": "↩️ بازاریابی"}],
    ], "resize_keyboard": True}


def coupon_plan_menu(plans: list[str]) -> dict:
    labels = {"starter": "استارتر", "pro": "پرو", "studio": "استودیو", "studio_plus": "استودیو پلاس"}
    rows = []
    for plan in plans:
        rows.append([{"text": f"💳 خرید {labels[plan]}"}])
    rows.append([{ "text": "↩️ ارتقا" }, {"text": "خانه"}])
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


def admin_activity_menu(status: dict) -> dict:
    def label(enabled: bool) -> str:
        return "فعال ✅" if enabled else "غیرفعال ⛔"
    return {"keyboard": [
        [{"text": f"🤖 ربات: {label(status['bot_enabled'])}"}],
        [{"text": f"🆓 پلن‌های رایگان: {label(status['free_plans_enabled'])}"}],
        [{"text": f"💳 پرداخت: {label(status['payments_enabled'])}"}],
        [{"text": "↩️ پنل مدیریت"}, {"text": "خانه"}],
    ], "resize_keyboard": True, "is_persistent": True}


def admin_user_menu(user_id: int, is_banned: bool, can_manage: bool = True) -> dict:
    rows = [
        [{"text": "➕ اعتبار"}, {"text": "➖ اعتبار"}],
        [{"text": "📦 تغییر پلن"}, {"text": "📜 تراکنش‌ها"}],
    ]
    rows.append([{ "text": "✅ رفع Ban" if is_banned else "🚫 Ban" }])
    rows.append([{ "text": "↩️ پنل مدیریت" }, {"text": "خانه"}])
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


def admin_plan_menu() -> dict:
    return {"keyboard": [[{"text": "رایگان"}, {"text": "استارتر"}], [{"text": "پرو"}, {"text": "استودیو"}], [{"text": "استودیو پلاس"}], [{"text": "↩️ برگشت"}, {"text": "خانه"}]], "resize_keyboard": True}

def help_menu() -> dict:
    return {"keyboard": [[{"text": "✍️ راهنمای تولید محتوا"}, {"text": "🧭 راهنمای پیشنهاد مسیر محتوا"}], [{"text": "📦 راهنمای بسته هفتگی"}, {"text": "💡 راهنمای ایده محتوا"}], [{"text": "📚 راهنمای محتوای من"}, {"text": "🚀 راهنمای کمپین"}], [{"text": "🎟️ راهنمای اعتبار"}], [{"text": "👤 راهنمای پروفایل برند"}], [{"text": "↩️ برگشت"}, {"text": "خانه"}]], "resize_keyboard": True}
