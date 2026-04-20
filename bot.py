import os, json, logging, asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
DATA_FILE = "reminders.json"
TIMEZONE = ZoneInfo("Europe/Kyiv")

# ── DATA ──────────────────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(data, uid):
    k = str(uid)
    if k not in data:
        data[k] = {"reminders": []}
    return data[k]

def now_kyiv():
    return datetime.now(TIMEZONE)

def fmt_dt(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TIMEZONE)
        return dt.strftime("%d.%m.%Y %H:%M")
    except:
        return iso_str

# ── AI ────────────────────────────────────────────────
async def ask_claude(prompt, max_tokens=400):
    if not ANTHROPIC_KEY:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status != 200:
                    logger.error(f"Claude error {r.status}: {await r.text()}")
                    return None
                data = await r.json()
                return data["content"][0]["text"]
    except Exception as e:
        logger.error(f"Claude failed: {e}")
        return None

# ── KEYBOARDS ─────────────────────────────────────────
def main_kb():
    return ReplyKeyboardMarkup([
        ["📋 Мої нагадування", "➕ Додати"],
        ["✅ Виконані", "🗑 Очистити виконані"],
    ], resize_keyboard=True)

# ── PARSE REMINDER WITH AI ────────────────────────────
async def parse_reminder(text, user_now):
    now_str = user_now.strftime("%Y-%m-%d %H:%M")
    weekday = user_now.strftime("%A")

    prompt = (
        f"Зараз: {now_str} ({weekday}), часовий пояс Київ (UTC+3).\n"
        f"Розбери нагадування і поверни ТІЛЬКИ валідний JSON:\n"
        f'{{"title":"назва задачі","datetime":"ISO8601 дата і час","repeat":"none або daily або weekly або monthly","repeat_info":"додаткова інфо якщо є"}}\n\n'
        f"Правила:\n"
        f"- 'завтра' = {(user_now + timedelta(days=1)).strftime('%Y-%m-%d')}\n"
        f"- 'через 2 години' = додай 2 години до зараз\n"
        f"- 'щопонеділка' = repeat:weekly\n"
        f"- 'щодня' = repeat:daily\n"
        f"- 'щомісяця 25 числа' = repeat:monthly\n"
        f"- Якщо час не вказано — постав 09:00\n"
        f"- datetime завжди у форматі: YYYY-MM-DDTHH:MM:00+03:00\n\n"
        f"Текст: \"{text}\""
    )

    res = await ask_claude(prompt, 300)
    if not res:
        return None

    cleaned = res.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                cleaned = part
                break

    try:
        return json.loads(cleaned)
    except Exception as e:
        logger.error(f"JSON parse error: {e}, raw: {res}")
        return None

# ── HANDLERS ──────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Привіт, {name}!\n\n"
        "Я бот-нагадувалка 🔔\n\n"
        "Просто напиши мені:\n"
        "• «нагадай завтра о 9 купити ліки»\n"
        "• «нагадай через 2 години зателефонувати»\n"
        "• «щопонеділка о 10 стендап»\n"
        "• «25 числа заплатити за квартиру»\n\n"
        "І я все запам'ятаю! ✨",
        reply_markup=main_kb(),
    )

async def show_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = load_data()
    u = get_user(data, uid)
    active = [r for r in u["reminders"] if not r.get("done")]

    if not active:
        await update.message.reply_text(
            "📋 Активних нагадувань немає\n\nНапишіть що нагадати і коли!",
            reply_markup=main_kb()
        )
        return

    text = "📋 *Активні нагадування:*\n\n"
    buttons = []
    for i, r in enumerate(active):
        repeat_label = {"daily": "🔄 щодня", "weekly": "🔄 щотижня", "monthly": "🔄 щомісяця"}.get(r.get("repeat","none"), "")
        text += f"{i+1}. {r['title']}\n"
        text += f"   🕐 {fmt_dt(r['datetime'])} {repeat_label}\n\n"
        buttons.append([
            InlineKeyboardButton(f"✅ {i+1}", callback_data=f"done_{r['id']}"),
            InlineKeyboardButton(f"🗑 {i+1}", callback_data=f"del_{r['id']}"),
        ])

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def show_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = load_data()
    u = get_user(data, uid)
    done = [r for r in u["reminders"] if r.get("done")]

    if not done:
        await update.message.reply_text("✅ Виконаних задач немає")
        return

    text = "✅ *Виконані задачі:*\n\n"
    for r in done[-10:]:
        text += f"• {r['title']} ({fmt_dt(r.get('done_at', r['datetime']))})\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def clear_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = load_data()
    u = get_user(data, uid)
    before = len(u["reminders"])
    u["reminders"] = [r for r in u["reminders"] if not r.get("done")]
    after = len(u["reminders"])
    save_data(data)
    await update.message.reply_text(f"🗑 Видалено {before - after} виконаних задач")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "📋 Мої нагадування": await show_reminders(update, context); return
    if text == "✅ Виконані": await show_done(update, context); return
    if text == "🗑 Очистити виконані": await clear_done(update, context); return
    if text == "➕ Додати":
        await update.message.reply_text(
            "Напишіть що і коли нагадати:\n\n"
            "Приклади:\n"
            "• «нагадай завтра о 10 зустріч»\n"
            "• «через 30 хвилин вийняти їжу»\n"
            "• «щопонеділка о 9 планування»\n"
            "• «25 числа оплата оренди»"
        )
        return

    msg = await update.message.reply_text("⏳ Аналізую...")
    user_now = now_kyiv()
    parsed = await parse_reminder(text, user_now)

    if not parsed or "title" not in parsed:
        await msg.edit_text(
            "❌ Не зміг розпізнати нагадування.\n\n"
            "Спробуйте написати чіткіше:\n"
            "«нагадай завтра о 10 купити хліб»"
        )
        return

    reminder_id = int(user_now.timestamp() * 1000)
    reminder = {
        "id": reminder_id,
        "title": parsed["title"],
        "datetime": parsed["datetime"],
        "repeat": parsed.get("repeat", "none"),
        "done": False,
        "created": user_now.isoformat(),
    }

    data = load_data()
    u = get_user(data, uid)
    u["reminders"].append(reminder)
    save_data(data)

    repeat_label = {"daily": "🔄 Повторюється щодня", "weekly": "🔄 Повторюється щотижня", "monthly": "🔄 Повторюється щомісяця"}.get(reminder["repeat"], "")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Виконано", callback_data=f"done_{reminder_id}"),
        InlineKeyboardButton("🗑 Видалити", callback_data=f"del_{reminder_id}"),
    ]])

    await msg.edit_text(
        f"✅ Нагадування збережено!\n\n"
        f"📝 {reminder['title']}\n"
        f"🕐 {fmt_dt(reminder['datetime'])}\n"
        f"{repeat_label}",
        reply_markup=kb
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = update.effective_user.id
    data = load_data()
    u = get_user(data, uid)

    if d.startswith("done_"):
        rid = int(d.split("_")[1])
        for r in u["reminders"]:
            if r["id"] == rid:
                r["done"] = True
                r["done_at"] = now_kyiv().isoformat()
                break
        save_data(data)
        await q.edit_message_text(f"✅ Виконано: {q.message.text.split(chr(10))[1] if chr(10) in q.message.text else ''}")

    elif d.startswith("del_"):
        rid = int(d.split("_")[1])
        u["reminders"] = [r for r in u["reminders"] if r["id"] != rid]
        save_data(data)
        await q.edit_message_text("🗑 Нагадування видалено")

# ── SCHEDULER ─────────────────────────────────────────
async def check_reminders(app):
    while True:
        try:
            now = now_kyiv()
            data = load_data()
            changed = False

            for uid, udata in data.items():
                for r in udata.get("reminders", []):
                    if r.get("done"):
                        continue
                    try:
                        remind_dt = datetime.fromisoformat(r["datetime"])
                        if remind_dt.tzinfo is None:
                            remind_dt = remind_dt.replace(tzinfo=TIMEZONE)

                        diff = (now - remind_dt).total_seconds()
                        if 0 <= diff <= 60:
                            await app.bot.send_message(
                                chat_id=int(uid),
                                text=f"🔔 *Нагадування!*\n\n📝 {r['title']}",
                                parse_mode="Markdown",
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("✅ Виконано", callback_data=f"done_{r['id']}")
                                ]])
                            )

                            repeat = r.get("repeat", "none")
                            if repeat == "none":
                                r["done"] = True
                                r["done_at"] = now.isoformat()
                            elif repeat == "daily":
                                r["datetime"] = (remind_dt + timedelta(days=1)).isoformat()
                            elif repeat == "weekly":
                                r["datetime"] = (remind_dt + timedelta(weeks=1)).isoformat()
                            elif repeat == "monthly":
                                month = remind_dt.month + 1
                                year = remind_dt.year
                                if month > 12:
                                    month = 1
                                    year += 1
                                r["datetime"] = remind_dt.replace(year=year, month=month).isoformat()
                            changed = True
                    except Exception as e:
                        logger.error(f"Reminder check error: {e}")

            if changed:
                save_data(data)

        except Exception as e:
            logger.error(f"Scheduler error: {e}")

        await asyncio.sleep(30)

# ── MAIN ──────────────────────────────────────────────
async def post_init(app):
    asyncio.create_task(check_reminders(app))

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Reminder bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
