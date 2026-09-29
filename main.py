import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

UPI_ID = os.getenv("UPI_ID", "yourupi@upi").strip()

FREE_DAILY_LIMIT = int(os.getenv("FREE_DAILY_LIMIT", "1"))

PREMIUM_7_DAYS = int(os.getenv("PREMIUM_PRICE_7_DAYS", "49"))
PREMIUM_30_DAYS = int(os.getenv("PREMIUM_PRICE_30_DAYS", "99"))
PREMIUM_LIFETIME = int(os.getenv("PREMIUM_PRICE_LIFETIME", "499"))

QR_IMAGE_PATH = Path(os.getenv("QR_IMAGE_PATH", "premium_qr.png"))

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"
VIDEOS_FILE = DATA_DIR / "videos.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
PAYMENTS_FILE = DATA_DIR / "payments.json"


# ============================================================
# DEFAULT DATA
# ============================================================

DEFAULT_SETTINGS = {
    "channels": [
        {
            "username": "@Team_Being_Richer",
            "url": "https://t.me/Team_Being_Richer",
        }
    ]
}


def load_json(path, default):
    try:
        if not path.exists():
            save_json(path, default)
            return default

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception:
        return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    tmp.replace(path)


users = load_json(USERS_FILE, {})
videos = load_json(VIDEOS_FILE, {})
settings = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
payments = load_json(PAYMENTS_FILE, {})


# ============================================================
# HELPERS
# ============================================================

def is_admin(user_id):
    return user_id in ADMIN_IDS


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def parse_dt(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def get_user(user_id):
    uid = str(user_id)

    if uid not in users:
        users[uid] = {
            "id": user_id,
            "first_name": "",
            "username": "",
            "created_at": iso_now(),
            "premium_until": None,
            "free_date": None,
            "free_used": 0,
        }
        save_json(USERS_FILE, users)

    return users[uid]


def update_user(update):
    if not update.effective_user:
        return

    u = update.effective_user
    record = get_user(u.id)

    record["first_name"] = u.first_name or ""
    record["username"] = u.username or ""

    save_json(USERS_FILE, users)


def premium_active(record):
    premium_until = parse_dt(record.get("premium_until"))

    if not premium_until:
        return False

    return premium_until > now_utc()


def reset_daily_usage(record):
    today = now_utc().date().isoformat()

    if record.get("free_date") != today:
        record["free_date"] = today
        record["free_used"] = 0


def free_available(record):
    reset_daily_usage(record)
    return int(record.get("free_used", 0)) < FREE_DAILY_LIMIT


def use_free_video(record):
    reset_daily_usage(record)
    record["free_used"] = int(record.get("free_used", 0)) + 1
    save_json(USERS_FILE, users)


def channels():
    return settings.get("channels", [])


# ============================================================
# CHANNEL JOIN CHECK
# ============================================================

async def check_membership(user_id, context):
    required = channels()

    if not required:
        return True

    for channel in required:
        username = channel.get("username")

        if not username:
            continue

        try:
            member = await context.bot.get_chat_member(
                chat_id=username,
                user_id=user_id,
            )

            status = member.status

            if status in ("left", "kicked"):
                return False

        except Exception:
            # If channel check cannot be performed,
            # don't crash the bot.
            return False

    return True


def join_keyboard():
    buttons = []

    for channel in channels():
        buttons.append([
            InlineKeyboardButton(
                f"📢 Join {channel.get('username', 'Channel')}",
                url=channel.get("url"),
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ I Joined - Check",
            callback_data="check_join",
        )
    ])

    return InlineKeyboardMarkup(buttons)


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_user(update)

    user = update.effective_user
    record = get_user(user.id)

    payload = ""

    if context.args:
        payload = context.args[0]

    # Referral
    if payload.startswith("ref_"):
        try:
            ref_id = int(payload.replace("ref_", ""))

            if ref_id != user.id:
                record["referred_by"] = ref_id
                save_json(USERS_FILE, users)

        except Exception:
            pass

    joined = await check_membership(user.id, context)

    if not joined:
        await update.message.reply_text(
            "🔒 <b>Channel Join Required</b>\n\n"
            "Video dekhne ke liye pehle required channel join karo.\n\n"
            "Join karne ke baad <b>✅ I Joined - Check</b> dabao.",
            parse_mode=ParseMode.HTML,
            reply_markup=join_keyboard(),
        )
        return

    if payload and payload in videos:
        await send_video(update, context, payload)
        return

    premium_text = "❌ Free"

    if premium_active(record):
        premium_text = "💎 Premium Active"

    await update.message.reply_text(
        "🎬 <b>Welcome!</b>\n\n"
        f"Status: {premium_text}\n\n"
        "Video link se video receive karne ke liye "
        "apna deep-link open karo.\n\n"
        "💎 Premium ke liye /premium\n"
        "ℹ️ Help ke liye /help",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# VIDEO SENDING
# ============================================================

async def send_video(update, context, payload):
    user = update.effective_user
    record = get_user(user.id)

    if not premium_active(record):

        if not free_available(record):
            await update.message.reply_text(
                "⚠️ <b>Daily free limit complete.</b>\n\n"
                f"Aaj ke {FREE_DAILY_LIMIT} free video use ho chuke hain.\n\n"
                "💎 Unlimited access ke liye /premium",
                parse_mode=ParseMode.HTML,
            )
            return

    video_data = videos.get(payload)

    if not video_data:
        await update.message.reply_text(
            "❌ Video nahi mila."
        )
        return

    video_id = video_data

    caption = "🎬 Here is your video."

    if isinstance(video_data, dict):
        video_id = video_data.get("file_id")
        caption = video_data.get(
            "caption",
            "🎬 Here is your video."
        )

    try:
        await update.message.reply_video(
            video=video_id,
            caption=caption,
        )

        if not premium_active(record):
            use_free_video(record)

    except Exception as e:
        await update.message.reply_text(
            "❌ Video send nahi ho paya.\n"
            "Admin ko inform karo."
        )

        print("VIDEO ERROR:", e)


# ============================================================
# HELP
# ============================================================

async def help_command(update, context):
    update_user(update)

    text = (
        "🤖 <b>Bot Commands</b>\n\n"
        "/start - Start bot\n"
        "/premium - Premium plans\n"
        "/help - Help\n"
    )

    if is_admin(update.effective_user.id):
        text += (
            "\n👑 <b>Admin Commands</b>\n\n"
            "/stats\n"
            "/addvideo\n"
            "/removevideo CODE\n"
            "/listvideos\n"
            "/addchannel @channel URL\n"
            "/removechannel @channel\n"
            "/channels\n"
            "/broadcast MESSAGE\n"
            "/setqr\n"
            "/givepremium USER_ID DAYS\n"
            "/removepremium USER_ID\n"
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# PREMIUM
# ============================================================

async def premium(update, context):
    update_user(update)

    keyboard = [
        [
            InlineKeyboardButton(
                f"7 Days - ₹{PREMIUM_7_DAYS}",
                callback_data="plan:7",
            )
        ],
        [
            InlineKeyboardButton(
                f"30 Days - ₹{PREMIUM_30_DAYS}",
                callback_data="plan:30",
            )
        ],
        [
            InlineKeyboardButton(
                f"Lifetime - ₹{PREMIUM_LIFETIME}",
                callback_data="plan:lifetime",
            )
        ],
    ]

    await update.message.reply_text(
        "💎 <b>Premium Plans</b>\n\n"
        f"7 Days: ₹{PREMIUM_7_DAYS}\n"
        f"30 Days: ₹{PREMIUM_30_DAYS}\n"
        f"Lifetime: ₹{PREMIUM_LIFETIME}\n\n"
        f"UPI: <code>{UPI_ID}</code>\n\n"
        "Plan select karo:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def plan_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    plan = query.data.split(":")[1]

    if plan == "7":
        amount = PREMIUM_7_DAYS
        duration = 7
        name = "7 Days"

    elif plan == "30":
        amount = PREMIUM_30_DAYS
        duration = 30
        name = "30 Days"

    else:
        amount = PREMIUM_LIFETIME
        duration = 36500
        name = "Lifetime"

    payment_id = f"{user_id}_{int(now_utc().timestamp())}"

    payments[payment_id] = {
        "payment_id": payment_id,
        "user_id": user_id,
        "plan": name,
        "amount": amount,
        "duration": duration,
        "status": "waiting_screenshot",
        "created_at": iso_now(),
    }

    save_json(PAYMENTS_FILE, payments)

    await query.message.reply_text(
        "💳 <b>Payment Instructions</b>\n\n"
        f"Plan: <b>{name}</b>\n"
        f"Amount: <b>₹{amount}</b>\n\n"
        f"UPI ID:\n<code>{UPI_ID}</code>\n\n"
        "QR se payment karo.\n"
        "Payment ke baad <b>screenshot isi chat me bhejo</b>.\n\n"
        f"Payment ID:\n<code>{payment_id}</code>",
        parse_mode=ParseMode.HTML,
    )

    if QR_IMAGE_PATH.exists():
        try:
            await query.message.reply_photo(
                photo=str(QR_IMAGE_PATH),
                caption="📱 Scan QR and pay."
            )
        except Exception as e:
            print("QR ERROR:", e)


# ============================================================
# PAYMENT SCREENSHOT
# ============================================================

async def payment_photo(update, context):
    user = update.effective_user

    if not user:
        return

    # Admin QR setup
    if is_admin(user.id) and context.user_data.get("setting_qr"):
        photo = update.message.photo[-1]

        file = await context.bot.get_file(photo.file_id)
        await file.download_to_drive(str(QR_IMAGE_PATH))

        context.user_data["setting_qr"] = False

        await update.message.reply_text(
            "✅ Premium QR successfully updated."
        )
        return

    # Find latest waiting payment
    waiting = None

    for payment_id, payment in payments.items():
        if (
            payment.get("user_id") == user.id
            and payment.get("status") == "waiting_screenshot"
        ):
            waiting = (payment_id, payment)

    if not waiting:
        await update.message.reply_text(
            "❌ Koi pending payment nahi mila.\n"
            "Pehle /premium se plan select karo."
        )
        return

    payment_id, payment = waiting

    payment["status"] = "screenshot_submitted"
    payment["screenshot_file_id"] = update.message.photo[-1].file_id
    payment["submitted_at"] = iso_now()

    save_json(PAYMENTS_FILE, payments)

    await update.message.reply_text(
        "✅ Payment screenshot received.\n\n"
        "Admin verification ke baad premium activate hoga."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"payment_approve:{payment_id}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"payment_reject:{payment_id}",
            ),
        ]
    ])

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=payment["screenshot_file_id"],
                caption=(
                    "💰 <b>New Payment</b>\n\n"
                    f"User ID: <code>{user.id}</code>\n"
                    f"Username: @{user.username or 'none'}\n"
                    f"Plan: {payment['plan']}\n"
                    f"Amount: ₹{payment['amount']}\n"
                    f"Payment ID: <code>{payment_id}</code>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception as e:
            print("ADMIN PAYMENT ERROR:", e)


# ============================================================
# APPROVE / REJECT PAYMENT
# ============================================================

async def payment_action(update, context):
    query = update.callback_query

    if not is_admin(query.from_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return

    await query.answer()

    action, payment_id = query.data.split(":", 1)

    payment = payments.get(payment_id)

    if not payment:
        await query.message.reply_text("❌ Payment not found.")
        return

    if action == "payment_reject":
        payment["status"] = "rejected"
        save_json(PAYMENTS_FILE, payments)

        try:
            await context.bot.send_message(
                chat_id=payment["user_id"],
                text=(
                    "❌ <b>Payment rejected.</b>\n\n"
                    "Agar payment sahi hai to admin se contact karo."
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        await query.message.reply_text(
            f"❌ Payment {payment_id} rejected."
        )
        return

    # APPROVE
    user_id = payment["user_id"]
    record = get_user(user_id)

    duration = payment["duration"]

    if duration >= 36500:
        premium_until = now_utc() + timedelta(days=36500)
    else:
        old = parse_dt(record.get("premium_until"))

        start = old if old and old > now_utc() else now_utc()

        premium_until = start + timedelta(days=duration)

    record["premium_until"] = premium_until.isoformat()

    payment["status"] = "approved"
    payment["approved_at"] = iso_now()
    payment["approved_by"] = query.from_user.id

    save_json(USERS_FILE, users)
    save_json(PAYMENTS_FILE, payments)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 <b>Premium Activated!</b>\n\n"
                f"Plan: {payment['plan']}\n"
                f"Valid until: {premium_until.strftime('%d-%m-%Y %H:%M UTC')}\n\n"
                "Enjoy unlimited video access 💎"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        print("USER PREMIUM MESSAGE ERROR:", e)

    await query.message.reply_text(
        f"✅ Payment approved.\nUser: {user_id}"
    )


# ============================================================
# ADMIN STATS
# ============================================================

async def stats(update, context):
    if not is_admin(update.effective_user.id):
        return

    total_users = len(users)

    premium_users = sum(
        1 for u in users.values()
        if premium_active(u)
    )

    await update.message.reply_text(
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 Users: {total_users}\n"
        f"💎 Premium users: {premium_users}\n"
        f"🎬 Videos: {len(videos)}\n"
        f"📢 Channels: {len(channels())}",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# ADMIN VIDEO MANAGEMENT
# ============================================================

async def addvideo(update, context):
    if not is_admin(update.effective_user.id):
        return

    context.user_data["adding_video"] = True

    await update.message.reply_text(
        "🎬 Video add karne ke liye format bhejo:\n\n"
        "<code>CODE | TELEGRAM_FILE_ID | CAPTION</code>\n\n"
        "Example:\n"
        "<code>abc123 | BAACAg... | My Video</code>",
        parse_mode=ParseMode.HTML,
    )


async def admin_text(update, context):
    user = update.effective_user

    if not user:
        return

    if not is_admin(user.id):
        return

    text = update.message.text or ""

    # ADD VIDEO
    if context.user_data.get("adding_video"):
        parts = [x.strip() for x in text.split("|", 2)]

        if len(parts) < 2:
            await update.message.reply_text(
                "❌ Format wrong.\n\n"
                "CODE | FILE_ID | CAPTION"
            )
            return

        code = parts[0]
        file_id = parts[1]
        caption = parts[2] if len(parts) > 2 else "🎬 Here is your video."

        videos[code] = {
            "file_id": file_id,
            "caption": caption,
        }

        save_json(VIDEOS_FILE, videos)

        context.user_data["adding_video"] = False

        await update.message.reply_text(
            f"✅ Video added.\n\n"
            f"Code: <code>{code}</code>\n\n"
            f"https://t.me/{context.bot.username}?start={code}",
            parse_mode=ParseMode.HTML,
        )
        return

    # BROADCAST
    if context.user_data.get("broadcasting"):
        context.user_data["broadcasting"] = False

        sent = 0
        failed = 0

        for uid in list(users.keys()):
            try:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=text,
                )
                sent += 1

                await asyncio.sleep(0.05)

            except Exception:
                failed += 1

        await update.message.reply_text(
            f"📢 Broadcast complete.\n\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}"
        )
        return


# ============================================================
# REMOVE VIDEO
# ============================================================

async def removevideo(update, context):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "Use:\n/rem
