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
TIMEZONE = ZoneInfo("Europe/Kyiv")
DATA_FILE = "megabot_data.json"

# ══════════════════════════════════════════
# ДАНІ
# ══════════════════════════════════════════
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
        data[k] = {
            "section": "main",
            "finance": {"months": {}, "settings": {"target": 5000, "mono_token": ""}},
            "reminders": [],
            "signals": {"active": False},
        }
    return data[k]

def now_kyiv():
    return datetime.now(TIMEZONE)

def mk():
    return now_kyiv().strftime("%Y-%m")

def ml(key):
    y, m = key.split("-")
    names = ["","Січень","Лютий","Березень","Квітень","Травень","Червень","Липень","Серпень","Вересень","Жовтень","Листопад","Грудень"]
    return f"{names[int(m)]} {y}"

def fmt(n): return f"{n:,.0f} ₴".replace(",", " ")
def fmtd(n): return f"${n:.2f}"

# ══════════════════════════════════════════
# AI
# ══════════════════════════════════════════
async def ask_claude(prompt, max_tokens=600):
    if not ANTHROPIC_KEY:
        logger.error("ANTHROPIC_KEY not set!")
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status != 200:
                    logger.error(f"Claude error {r.status}: {await r.text()}")
                    return None
                d = await r.json()
                return d["content"][0]["text"]
    except Exception as e:
        logger.error(f"Claude failed: {e}")
        return None

# ══════════════════════════════════════════
# МЕНЮ
# ══════════════════════════════════════════
def main_kb():
    return ReplyKeyboardMarkup([
        ["💰 Фінанси", "📈 Сигнали"],
        ["🔔 Нагадування", "ℹ️ Про бота"],
    ], resize_keyboard=True)

def finance_kb():
    return ReplyKeyboardMarkup([
        ["📊 Дашборд", "➕ Додати"],
        ["🏦 Monobank", "🤖 AI Аналіз"],
        ["📋 Записи", "💡 Інвестиції"],
        ["⚙️ Налаштування", "🏠 Головне меню"],
    ], resize_keyboard=True)

def signals_kb():
    return ReplyKeyboardMarkup([
        ["₿ Крипто", "💱 Форекс"],
        ["🔍 Аналіз монети", "📊 Топ можливості"],
        ["📡 Авто-сигнали", "📚 Навчання"],
        ["🏠 Головне меню"],
    ], resize_keyboard=True)

def reminders_kb():
    return ReplyKeyboardMarkup([
        ["📋 Мої нагадування", "✅ Виконані"],
        ["🗑 Очистити виконані", "🏠 Головне меню"],
    ], resize_keyboard=True)

# ══════════════════════════════════════════
# ФІНАНСИ — категорії
# ══════════════════════════════════════════
CATEGORIES = {
    "food": ("🛒", "Їжа та продукти"),
    "rent": ("🏠", "Оренда / комуналка"),
    "transport": ("🚗", "Транспорт"),
    "fun": ("🎉", "Розваги та кафе"),
    "health": ("💊", "Здоров'я"),
    "clothes": ("👕", "Одяг"),
    "savings": ("💰", "Накопичення"),
    "invest": ("📈", "Інвестиції"),
    "other": ("📦", "Інше"),
}
INCOME_TYPES = {
    "salary": ("💼", "Зарплата"),
    "freelance": ("💻", "Фріланс"),
    "passive": ("📊", "Пасивний дохід"),
    "gift": ("🎁", "Подарунок"),
    "other_income": ("➕", "Інше"),
}

async def get_rate():
    try:
        async with aiohttp.ClientSession() as s:
            # Bybit: отримуємо ціну USDT в USD (завжди ~1), а курс UAH беремо з exchangerate
            async with s.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    data = await r.json()
                    uah_rate = data.get("rates", {}).get("UAH", 41.5)
                    return float(uah_rate)
    except Exception as e:
        logger.warning(f"Rate fetch failed: {e}")
    return 41.5

# ══════════════════════════════════════════
# ФІНАНСИ — функції
# ══════════════════════════════════════════
async def finance_dashboard(update, u):
    month = u["finance"]["months"].get(mk(), {"income": [], "expenses": []})
    inc = sum(i["amount_uah"] for i in month.get("income", []))
    exp = sum(e["amount_uah"] for e in month.get("expenses", []))
    bal = inc - exp
    sav = sum(e["amount_uah"] for e in month.get("expenses", []) if e.get("category") in ["savings","invest"])
    tgt = u["finance"]["settings"].get("target", 5000)
    rate = await get_rate()
    pct = min(100, int(sav/tgt*100)) if tgt > 0 else 0
    bar = "█"*int(pct/10) + "░"*(10-int(pct/10))
    cats = {}
    for e in month.get("expenses", []):
        c = e.get("category","other")
        cats[c] = cats.get(c,0) + e["amount_uah"]
    text = (
        f"💰 *ФІНАНСИ — {ml(mk())}*\n"
        f"💱 Курс: {fmt(rate)}\n\n"
        f"📈 Дохід: {fmt(inc)}\n"
        f"📉 Витрати: {fmt(exp)}\n"
        f"💳 Баланс: {fmt(bal)}\n"
        f"💰 Накопичено: {fmt(sav)}\n\n"
        f"🎯 Ціль: {fmt(tgt)}\n"
        f"`{bar}` {pct}%"
    )
    if cats:
        text += "\n\n📊 *По категоріям:*\n"
        for cid, amt in sorted(cats.items(), key=lambda x: -x[1]):
            icon, name = CATEGORIES.get(cid, ("📦", cid))
            p = int(amt/exp*100) if exp > 0 else 0
            text += f"{icon} {name}: {fmt(amt)} ({p}%)\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def finance_ai_input(update, u, text, uid, data):
    rate = await get_rate()
    msg = await update.message.reply_text("⏳ Аналізую...")
    cats = ", ".join([f"{k}: {v[1]}" for k,v in CATEGORIES.items()])
    incs = ", ".join([f"{k}: {v[1]}" for k,v in INCOME_TYPES.items()])
    prompt = (
        f"Фінансовий асистент. Курс USDT/UAH: {rate:.2f}.\n"
        f"Поверни ТІЛЬКИ JSON без markdown:\n"
        f'{"{"}"type":"expense або income","amount":число,"currency":"UAH або USDT або USD","amount_uah":сума,"category":"catId","income_type":"typeId","desc":"опис","message":"підтвердження"{"}"}\n'
        f"Витрати: {cats}\nДоходи: {incs}\n"
        f'Якщо не фінанси: {"{"}"error":"текст"{"}"}\n'
        f'Текст: "{text}"'
    )
    res = await ask_claude(prompt, 300)
    if not res:
        await msg.edit_text("❌ AI недоступний. Скористайтесь ➕ Додати")
        return
    cleaned = res.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"): part = part[4:].strip()
            if part.startswith("{"): cleaned = part; break
    try:
        parsed = json.loads(cleaned)
    except:
        await msg.edit_text("❌ Не вдалось розпізнати")
        return
    if "error" in parsed:
        await msg.edit_text(f"ℹ️ {parsed['error']}")
        return
    m = mk()
    if m not in u["finance"]["months"]:
        u["finance"]["months"][m] = {"income": [], "expenses": []}
    entry = {"id": int(now_kyiv().timestamp()*1000), "amount": parsed.get("amount",0),
             "currency": parsed.get("currency","UAH"), "amount_uah": parsed.get("amount_uah", parsed.get("amount",0)),
             "rate_used": rate if parsed.get("currency") != "UAH" else None,
             "desc": parsed.get("desc",""), "date": now_kyiv().isoformat()}
    if parsed["type"] == "expense":
        entry["category"] = parsed.get("category","other")
        u["finance"]["months"][m]["expenses"].append(entry)
        icon, cname = CATEGORIES.get(entry["category"], ("📦","Інше"))
        cs = f" ({fmtd(entry['amount'])} × ₴{rate:.2f})" if entry["currency"] != "UAH" else ""
        resp = f"✅ {parsed.get('message','Додано!')}\n\n{icon} {cname}\n💸 {fmt(entry['amount_uah'])}{cs}"
    else:
        entry["income_type"] = parsed.get("income_type","other_income")
        u["finance"]["months"][m]["income"].append(entry)
        icon, iname = INCOME_TYPES.get(entry["income_type"], ("➕","Інше"))
        cs = f" ({fmtd(entry['amount'])} × ₴{rate:.2f})" if entry["currency"] != "UAH" else ""
        resp = f"✅ {parsed.get('message','Додано!')}\n\n{icon} {iname}\n💰 {fmt(entry['amount_uah'])}{cs}"
    save_data(data)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Дашборд", callback_data="fin_dashboard"), InlineKeyboardButton("🗑 Видалити", callback_data=f"fin_del_{parsed['type']}_{entry['id']}_{m}")]])
    await msg.edit_text(resp, reply_markup=kb)

# ══════════════════════════════════════════
# СИГНАЛИ — технічний аналіз
# ══════════════════════════════════════════
CRYPTO_PAIRS = ["BTC/USD","ETH/USD","BNB/USD","SOL/USD","XRP/USD","DOGE/USD","AVAX/USD","LINK/USD","ARB/USD","OP/USD"]
CRYPTO_SYMBOLS = {p: p.replace("/USD","") for p in CRYPTO_PAIRS}

FOREX_PAIRS = {"EUR/USD":"EUR/USD","GBP/USD":"GBP/USD","USD/JPY":"USD/JPY","AUD/USD":"AUD/USD","USD/CAD":"USD/CAD","EUR/GBP":"EUR/GBP","NZD/USD":"NZD/USD","EUR/JPY":"EUR/JPY","GBP/JPY":"GBP/JPY","USD/CHF":"USD/CHF"}

TWELVE_KEY = os.environ.get("TWELVE_KEY", "")

async def get_twelvedata_klines(symbol, interval="5min", limit=100):
    if not TWELVE_KEY:
        logger.error("TWELVE_KEY not set!")
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.twelvedata.com/time_series",
                params={"symbol": symbol, "interval": interval, "outputsize": limit, "apikey": TWELVE_KEY},
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("status") == "error":
                        logger.error(f"TwelveData error for {symbol}: {data.get('message')}")
                        return None
                    values = data.get("values", [])
                    if not values:
                        return None
                    # Повертаємо від старіших до новіших
                    return list(reversed(values))
    except Exception as e:
        logger.error(f"TwelveData error {symbol}: {e}")
    return None
async def analyze_symbol(symbol, timeframe="5min", market="crypto"):
    candles = await get_twelvedata_klines(symbol, timeframe, 100)
    if not candles or len(candles) < 30:
        logger.warning(f"Not enough candles for {symbol}: {len(candles) if candles else 0}")
        return None
    try:
        o = [float(c["open"]) for c in candles]
        h = [float(c["high"]) for c in candles]
        l = [float(c["low"]) for c in candles]
        c = [float(c["close"]) for c in candles]
        vol = [float(c.get("volume", 0) or 0) for c in candles]
    except (KeyError, ValueError) as e:
        logger.error(f"Candle parse error {symbol}: {e}")
        return None

    rsi = calc_rsi(c)
    e9, e21, e50 = calc_ema(c,9), calc_ema(c,21), calc_ema(c,50)
    ml2, ms, _ = calc_macd(c)
    bbu, bbm, bbl = calc_bb(c)
    sk, _ = calc_stoch(h, l, c)
    atr = calc_atr(h, l, c)
    pats = detect_patterns(o, h, l, c)

    avg_vol = sum(vol[-10:-1])/9 if len(vol)>10 and sum(vol)>0 else 1
    vr = vol[-1]/avg_vol if avg_vol > 0 else 1.0

    sc, dir, rs = score_signal(rsi, ml2, ms, bbu, bbl, bbm, c[-1], e9, e21, e50, sk, vr, pats)
    if dir == "NEUTRAL": return None

    entry = c[-1]
    if dir == "LONG": sl=entry-atr*1.5; tp1=entry+atr*1.5; tp2=entry+atr*3; tp3=entry+atr*5
    else: sl=entry+atr*1.5; tp1=entry-atr*1.5; tp2=entry-atr*3; tp3=entry-atr*5

    tf_label = {"1min":"1м","5min":"5м","15min":"15м","1h":"1г","4h":"4г"}.get(timeframe, timeframe)

    if market == "forex":
        display_name = FOREX_PAIRS.get(symbol, symbol)
        is_jpy = "JPY" in symbol
    else:
        display_name = symbol
        is_jpy = False

    return {"symbol": display_name, "raw_symbol": symbol, "market": market,
            "price": c[-1], "score": sc, "direction": dir, "reasons": rs,
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "atr": atr, "timeframe": tf_label, "time": now_kyiv().strftime("%H:%M:%S"),
            "is_jpy": is_jpy}

async def analyze_crypto(symbol, tf="5m"):
    # Конвертуємо таймфрейм Binance → TwelveData
    tf_map = {"1m":"1min","3m":"3min","5m":"5min","15m":"15min","1h":"1h","4h":"4h"}
    td_tf = tf_map.get(tf, "5min")
    # Конвертуємо символ BTCUSDT → BTC/USD
    if symbol.endswith("USDT"):
        td_symbol = symbol[:-4] + "/USD"
    elif symbol.endswith("USD"):
        td_symbol = symbol[:-3] + "/USD"
    else:
        td_symbol = symbol
    return await analyze_symbol(td_symbol, td_tf, "crypto")

async def analyze_forex(symbol, timeframe="5min"):
    return await analyze_symbol(symbol, timeframe, "forex")

def fmt_signal(sig):
    emoji = "🟢 LONG" if sig["direction"] == "LONG" else "🔴 SHORT"
    bar = "█"*(sig["score"]//10) + "░"*(10-sig["score"]//10)
    is_forex = sig.get("market") == "forex"
    is_jpy = sig.get("is_jpy", False)
    d = 3 if is_jpy else (5 if is_forex else 4)
    def f(v): return f"{v:.{d}f}"
    rr = abs(sig["tp1"]-sig["entry"]) / abs(sig["sl"]-sig["entry"]) if sig["sl"] != sig["entry"] else 0
    market_label = "💱 Форекс" if is_forex else "₿ Крипто"
    t = (f"{'='*26}\n⚡ *{sig['symbol']}* | {emoji}\n{market_label} | {sig['timeframe']} | ⏰ {sig['time']}\n{'='*26}\n\n"
         f"💰 Ціна: *{f(sig['price'])}*\n\n📊 Сила: `{bar}` {sig['score']}%\n\n"
         f"🎯 *Рівні:*\n• Вхід: `{f(sig['entry'])}`\n• SL: `{f(sig['sl'])}`\n"
         f"• TP1: `{f(sig['tp1'])}`\n• TP2: `{f(sig['tp2'])}`\n• TP3: `{f(sig['tp3'])}`\n• R/R: 1:{rr:.1f}\n\n"
         f"📋 *Причини:*\n")
    for r in sig["reasons"][:5]: t += f"• {r}\n"
    t += "\n⚠️ _Не є фінансовою порадою!_"
    return t

# Аліаси для сумісності
fmt_forex_signal = fmt_signal

def calc_rsi(c, p=14):
    if len(c)<p+1: return 50
    g=[max(c[i]-c[i-1],0) for i in range(1,len(c))]
    l=[max(c[i-1]-c[i],0) for i in range(1,len(c))]
    ag=sum(g[-p:])/p; al=sum(l[-p:])/p
    return 100 if al==0 else 100-(100/(1+ag/al))

def calc_ema(c, p):
    if len(c)<p: return c[-1] if c else 0
    k=2/(p+1); e=sum(c[:p])/p
    for x in c[p:]: e=x*k+e*(1-k)
    return e

def calc_macd(c):
    if len(c)<26: return 0,0,0
    m=calc_ema(c,12)-calc_ema(c,26); s=calc_ema(c,9) if len(c)>=9 else m
    return m,s,m-s

def calc_bb(c, p=20):
    if len(c)<p: return c[-1],c[-1],c[-1]
    r=c[-p:]; mid=sum(r)/p; std=(sum((x-mid)**2 for x in r)/p)**0.5
    return mid+2*std,mid,mid-2*std

def calc_stoch(h,l,c,p=14):
    if len(c)<p: return 50,50
    hi=max(h[-p:]); lo=min(l[-p:])
    return (100 if hi==lo else (c[-1]-lo)/(hi-lo)*100), 50

def calc_atr(h,l,c,p=14):
    if len(c)<2: return abs(c[-1]*0.002) if c else 0
    trs=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,min(len(c),p+1))]
    return sum(trs)/len(trs) if trs else abs(c[-1]*0.002)

def detect_patterns(o,h,l,c):
    pats=[]
    if len(c)<3: return pats
    body=abs(c[-1]-o[-1]); rng=h[-1]-l[-1]
    if rng==0: return pats
    uw=h[-1]-max(c[-1],o[-1]); lw=min(c[-1],o[-1])-l[-1]
    if lw>body*2 and uw<body*0.3: pats.append("🔨 Hammer")
    if uw>body*2 and lw<body*0.3: pats.append("⭐ Shooting Star")
    if body<rng*0.1: pats.append("➕ Doji")
    if len(c)>=2:
        pb=abs(c[-2]-o[-2]); cb=abs(c[-1]-o[-1])
        if cb>pb*1.5:
            if c[-1]>o[-1] and c[-2]<o[-2]: pats.append("🟢 Bullish Engulfing")
            elif c[-1]<o[-1] and c[-2]>o[-2]: pats.append("🔴 Bearish Engulfing")
    if len(c)>=3:
        if all(c[i]>o[i] for i in [-3,-2,-1]): pats.append("🟢🟢🟢 Three Soldiers")
        if all(c[i]<o[i] for i in [-3,-2,-1]): pats.append("🔴🔴🔴 Three Crows")
    return pats

def score_signal(rsi,macd,ms,bbu,bbl,bbm,close,e9,e21,e50,sk,vr,pats):
    sc=0; dir="NEUTRAL"; rs=[]
    if rsi<30: sc+=20; rs.append(f"📉 RSI={rsi:.1f} перепроданість"); dir="LONG"
    elif rsi<40: sc+=10; rs.append(f"📉 RSI={rsi:.1f}"); dir="LONG"
    elif rsi>70: sc+=20; rs.append(f"📈 RSI={rsi:.1f} перекупленість"); dir="SHORT"
    elif rsi>60: sc+=10; rs.append(f"📈 RSI={rsi:.1f}"); dir="SHORT"
    else: rs.append(f"➡️ RSI={rsi:.1f}")
    if macd>ms and macd>0: sc+=15; rs.append("✅ MACD бичачий крос"); dir="LONG" if dir!="SHORT" else dir
    elif macd<ms and macd<0: sc+=15; rs.append("✅ MACD ведмежий крос"); dir="SHORT" if dir!="LONG" else dir
    elif macd>ms: sc+=7; rs.append("🟡 MACD слабкий бичачий")
    elif macd<ms: sc+=7; rs.append("🟡 MACD слабкий ведмежий")
    if close<=bbl: sc+=15; rs.append("🟢 Нижня BB"); dir="LONG" if dir!="SHORT" else dir
    elif close>=bbu: sc+=15; rs.append("🔴 Верхня BB"); dir="SHORT" if dir!="LONG" else dir
    if e9>e21>e50: sc+=15; rs.append("📈 EMA бичачий тренд"); dir="LONG" if dir!="SHORT" else dir
    elif e9<e21<e50: sc+=15; rs.append("📉 EMA ведмежий тренд"); dir="SHORT" if dir!="LONG" else dir
    elif e9>e21: sc+=7; rs.append("🟡 EMA короткостроковий")
    if sk<20: sc+=10; rs.append(f"🟢 Stoch={sk:.0f}")
    elif sk>80: sc+=10; rs.append(f"🔴 Stoch={sk:.0f}")
    if vr>2: sc+=10; rs.append(f"🔥 Обсяг x{vr:.1f}")
    elif vr>1.5: sc+=5; rs.append(f"📊 Обсяг x{vr:.1f}")
    if pats: sc+=min(15,len(pats)*5); rs.extend(pats[:2])
    return min(sc,100), dir, rs



# ══════════════════════════════════════════
# НАГАДУВАННЯ
# ══════════════════════════════════════════
def fmt_dt(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=TIMEZONE)
        return dt.strftime("%d.%m.%Y %H:%M")
    except: return iso_str

async def parse_reminder(text):
    user_now = now_kyiv()
    prompt = (
        f"Зараз: {user_now.strftime('%Y-%m-%d %H:%M')} (Київ UTC+3).\n"
        f"Розбери нагадування, поверни ТІЛЬКИ JSON:\n"
        f'{"{"}"title":"назва","datetime":"YYYY-MM-DDTHH:MM:00+03:00","repeat":"none або daily або weekly або monthly"{"}"}\n'
        f"- 'завтра' = {(user_now+timedelta(days=1)).strftime('%Y-%m-%d')}\n"
        f"- 'через 2 години' = додай 2 години\n"
        f"- без часу = 09:00\n"
        f'Текст: "{text}"'
    )
    res = await ask_claude(prompt, 200)
    if not res: return None
    cleaned = res.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"): part = part[4:].strip()
            if part.startswith("{"): cleaned = part; break
    try: return json.loads(cleaned)
    except: return None

# ══════════════════════════════════════════
# ГОЛОВНІ ОБРОБНИКИ
# ══════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.first_name
    data = load_data()
    u = get_user(data, uid)
    u["section"] = "main"
    save_data(data)
    await update.message.reply_text(
        f"👋 Привіт, {name}!\n\n"
        "Я твій персональний асистент 🤖\n\n"
        "💰 *Фінанси* — облік доходів і витрат\n"
        "📈 *Сигнали* — торгові сигнали крипто/форекс\n"
        "🔔 *Нагадування* — не забудь нічого важливого\n\n"
        "Обери розділ:",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id
    data = load_data()
    u = get_user(data, uid)
    section = u.get("section", "main")
    state = context.user_data.get("state")

    # ── ГОЛОВНЕ МЕНЮ ──
    if text == "🏠 Головне меню":
        u["section"] = "main"; save_data(data)
        await update.message.reply_text("Головне меню:", reply_markup=main_kb()); return

    if text == "💰 Фінанси":
        u["section"] = "finance"; save_data(data)
        rate = await get_rate()
        await update.message.reply_text(
            f"💰 *Розділ Фінанси*\n\nКурс USDT: {fmt(rate)}\n\nОберіть дію:",
            parse_mode="Markdown", reply_markup=finance_kb()); return

    if text == "📈 Сигнали":
        u["section"] = "signals"; save_data(data)
        await update.message.reply_text("📈 *Розділ Сигнали*\n\nОберіть дію:", parse_mode="Markdown", reply_markup=signals_kb()); return

    if text == "🔔 Нагадування":
        u["section"] = "reminders"; save_data(data)
        await update.message.reply_text(
            "🔔 *Розділ Нагадування*\n\nПросто напишіть що і коли нагадати:\n"
            "• «нагадай завтра о 9 купити ліки»\n"
            "• «через 30 хвилин подзвонити»\n"
            "• «щопонеділка о 10 стендап»",
            parse_mode="Markdown", reply_markup=reminders_kb()); return

    if text == "ℹ️ Про бота":
        await update.message.reply_text(
            "🤖 *Мега-бот v1.0*\n\n"
            "💰 Фінанси — облік з Monobank та USDT\n"
            "📈 Сигнали — RSI, MACD, BB, EMA, патерни\n"
            "🔔 Нагадування — природна мова + повтори\n\n"
            "Powered by Claude AI ✨",
            parse_mode="Markdown", reply_markup=main_kb()); return

    # ══ ФІНАНСИ ══
    if section == "finance":
        if text == "📊 Дашборд": await finance_dashboard(update, u); return

        if text == "➕ Додати":
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📉 Витрата", callback_data="fin_add_exp"), InlineKeyboardButton("📈 Дохід", callback_data="fin_add_inc")]])
            await update.message.reply_text("Що додати?", reply_markup=kb); return

        if text == "🏦 Monobank":
            token = u["finance"]["settings"].get("mono_token","")
            if not token:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Ввести токен", callback_data="fin_mono_enter")]])
                await update.message.reply_text("🏦 Підключіть Monobank\n\n1. api.monobank.ua\n2. QR-код авторизація\n3. Надішліть токен", reply_markup=kb)
            else:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Синхронізувати", callback_data="fin_mono_sync")], [InlineKeyboardButton("❌ Відключити", callback_data="fin_mono_disc")]])
                await update.message.reply_text("🏦 Monobank підключено ✅", reply_markup=kb)
            return

        if text == "🤖 AI Аналіз":
            msg = await update.message.reply_text("🤖 Аналізую...")
            month = u["finance"]["months"].get(mk(), {"income":[],"expenses":[]})
            inc = sum(i["amount_uah"] for i in month.get("income",[]))
            exp = sum(e["amount_uah"] for e in month.get("expenses",[]))
            sav = sum(e["amount_uah"] for e in month.get("expenses",[]) if e.get("category") in ["savings","invest"])
            cats = {}
            for e in month.get("expenses",[]): c=e.get("category","other"); cats[c]=cats.get(c,0)+e["amount_uah"]
            cat_str = ", ".join([f"{CATEGORIES.get(k,('',''))[1]}: {fmt(v)}" for k,v in cats.items()]) or "немає"
            tgt = u["finance"]["settings"].get("target",5000)
            pct = int(sav/inc*100) if inc>0 else 0
            prompt = (f"Фінансовий аналітик. Українська мова.\nДані за {ml(mk())}:\nДохід: {fmt(inc)}, Витрати: {fmt(exp)}, Накопичено: {fmt(sav)} ({pct}%), Ціль: {fmt(tgt)}\nКатегорії: {cat_str}\n\nАналіз:\n📊 Загальна картина — 2 речення\n🔴 На чому зекономити — 2-3 пункти\n✅ Що добре — 1-2 пункти\n💡 Порада дня")
            res = await ask_claude(prompt, 600)
            if not res: await msg.edit_text("❌ AI недоступний"); return

            header = f"🤖 *AI Аналіз — {ml(mk())}*\n\n"
            full = header + res
            if len(full) <= 4000:
                await msg.edit_text(full, parse_mode="Markdown")
            else:
                await msg.edit_text(header + res[:4000-len(header)], parse_mode="Markdown")
                remaining = res[4000-len(header):]
                while remaining:
                    await update.message.reply_text(remaining[:4000])
                    remaining = remaining[4000:]
            return

        if text == "💡 Інвестиції":
            msg = await update.message.reply_text("💡 Формую рекомендації...")
            month = u["finance"]["months"].get(mk(), {"income":[],"expenses":[]})
            inc = sum(i["amount_uah"] for i in month.get("income",[]))
            exp = sum(e["amount_uah"] for e in month.get("expenses",[]))
            bal = max(0, inc-exp)
            rate = await get_rate()
            prompt = (f"Фінансовий консультант. Українська. Попередь про ризики.\nВільний капітал: {fmt(bal)} ({fmtd(bal/rate)}), Дохід: {fmt(inc)}\nДай 4 варіанти: ОВДП, депозит, ETF, стейкінг USDT на Binance.\nДля кожного: дохідність за місяць, конкретна сума, ризик.")
            res = await ask_claude(prompt, 1000)
            if not res: await msg.edit_text("❌ AI недоступний"); return

            header = f"💡 *Куди вкласти*\n\n⚠️ Загальна інформація, не порада.\n\n💳 Капітал: {fmt(bal)}\n\n"
            full = header + res
            if len(full) <= 4000:
                await msg.edit_text(full, parse_mode="Markdown")
            else:
                await msg.edit_text(header + res[:4000-len(header)], parse_mode="Markdown")
                remaining = res[4000-len(header):]
                while remaining:
                    await update.message.reply_text(remaining[:4000])
                    remaining = remaining[4000:]
            return

        if text == "📋 Записи":
            month = u["finance"]["months"].get(mk(), {"income":[],"expenses":[]})
            inc_list = month.get("income",[])[-5:]
            exp_list = month.get("expenses",[])[-10:]
            if not inc_list and not exp_list:
                await update.message.reply_text("📋 Записів немає"); return
            t = f"📋 *Записи — {ml(mk())}*\n\n"
            if inc_list:
                t += "💰 Доходи:\n"
                for i in reversed(inc_list):
                    icon,name=INCOME_TYPES.get(i.get("income_type","other_income"),("➕","Інше"))
                    t += f"{icon} {i['desc']} — {fmt(i['amount_uah'])}\n"
            if exp_list:
                t += "\n📉 Витрати:\n"
                for e in reversed(exp_list):
                    icon,name=CATEGORIES.get(e.get("category","other"),("📦","Інше"))
                    t += f"{icon} {e['desc']} — {fmt(e['amount_uah'])}\n"
            await update.message.reply_text(t, parse_mode="Markdown"); return

        if text == "⚙️ Налаштування":
            tgt = u["finance"]["settings"].get("target",5000)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎯 Змінити ціль", callback_data="fin_set_target")]])
            await update.message.reply_text(f"⚙️ Налаштування\n\nЦіль накопичень: {fmt(tgt)}", reply_markup=kb); return

        if state == "fin_set_target":
            try:
                t = float(text.replace(" ","").replace(",","."))
                u["finance"]["settings"]["target"] = t; save_data(data)
                context.user_data["state"] = None
                await update.message.reply_text(f"✅ Ціль: {fmt(t)}", reply_markup=finance_kb())
            except: await update.message.reply_text("❌ Введіть число, наприклад: 5000")
            return

        if state == "fin_mono_token":
            u["finance"]["settings"]["mono_token"] = text.strip(); save_data(data)
            context.user_data["state"] = None
            await update.message.reply_text("✅ Токен збережено!", reply_markup=finance_kb()); return

        if state == "fin_add_amount":
            try:
                amount = float(text.replace(" ","").replace(",","."))
                context.user_data["fin_amount"] = amount
                context.user_data["state"] = None
                cat_type = context.user_data.get("fin_type","expense")
                if cat_type == "expense":
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"{v[0]} {v[1]}", callback_data=f"fin_cat_{k}")] for k,v in CATEGORIES.items()])
                    await update.message.reply_text("Оберіть категорію:", reply_markup=kb)
                else:
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"{v[0]} {v[1]}", callback_data=f"fin_inc_{k}")] for k,v in INCOME_TYPES.items()])
                    await update.message.reply_text("Тип доходу:", reply_markup=kb)
            except: await update.message.reply_text("❌ Введіть число")
            return

        # AI розпізнавання фінансів
        await finance_ai_input(update, u, text, uid, data)
        return

    # ══ СИГНАЛИ ══
    if section == "signals":
        if text == "₿ Крипто":
            msg = await update.message.reply_text("₿ Сканую крипту...")
            res = []
            for i,p in enumerate(CRYPTO_PAIRS[:8]):
                await msg.edit_text(f"₿ Сканую... {i+1}/8\n{p}")
                s = await analyze_crypto(p,"5m")
                if s and s["score"]>=60: res.append(s)
                await asyncio.sleep(0.3)
            if not res: await msg.edit_text("😐 Немає сильних сигналів зараз"); return
            res.sort(key=lambda x:-x["score"])
            t = "₿ *Крипто сигнали:*\n\n"
            for s in res[:5]:
                e="🟢" if s["direction"]=="LONG" else "🔴"
                t += f"{e} *{s['symbol']}* — {s['score']}% | ${s['price']:.4f}\n"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"📊 {r['symbol']}", callback_data=f"sig_detail_{r['symbol']}")] for r in res[:5]])
            await msg.edit_text(t, parse_mode="Markdown", reply_markup=kb); return

        if text == "💱 Форекс":
            await update.message.reply_text("💱 Оберіть пару:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(name, callback_data=f"sig_fx_{sym}")] for sym,name in FOREX_PAIRS.items()])); return

        if text == "🔍 Аналіз монети":
            context.user_data["state"] = "sig_coin"
            await update.message.reply_text("Введіть монету: BTC, ETH, SOL..."); return

        if text == "📊 Топ можливості":
            msg = await update.message.reply_text("🔍 Скануємо крипто і форекс...")
            res = []
            for p in CRYPTO_PAIRS[:5]:
                s = await analyze_crypto(p,"5m")
                if s and s["score"]>=65: res.append(s)
                await asyncio.sleep(0.3)
            for sym in list(FOREX_PAIRS.keys())[:5]:
                await msg.edit_text(f"🔍 Скануємо форекс... {sym}")
                s = await analyze_forex(sym)
                if s and s["score"]>=60: res.append(s)
                await asyncio.sleep(0.5)
            res.sort(key=lambda x:-x["score"])
            if not res: await msg.edit_text("😐 Немає сигналів >60% зараз"); return
            t = "🏆 *Топ можливості:*\n\n"
            for s in res[:6]:
                e="🟢" if s["direction"]=="LONG" else "🔴"
                m="💱" if s.get("market")=="forex" else "₿"
                p=f"{s['price']:.5f}" if s.get("market")=="forex" else f"${s['price']:.4f}"
                t += f"{m}{e} *{s['symbol']}* — {s['score']}% | {p}\n"
            btns = []
            for r in res[:6]:
                if r.get("market") == "forex":
                    btns.append([InlineKeyboardButton(f"💱 {r['symbol']}", callback_data=f"sig_fx_{r['raw_symbol']}")])
                else:
                    btns.append([InlineKeyboardButton(f"₿ {r['symbol']}", callback_data=f"sig_detail_{r['symbol']}")])
            await msg.edit_text(t, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

        if text == "📡 Авто-сигнали":
            u["signals"]["active"] = not u["signals"].get("active",False); save_data(data)
            await update.message.reply_text(f"📡 Авто-сигнали: {'✅ Увімкнено' if u['signals']['active'] else '❌ Вимкнено'}"); return

        if text == "📚 Навчання":
            await update.message.reply_text(
                "📚 *Навчання:*\n\n"
                "RSI <30 → LONG (перепроданість)\n"
                "RSI >70 → SHORT (перекупленість)\n\n"
                "MACD крос вгору → бичачий сигнал\n"
                "BB нижня → підтримка\n"
                "EMA 9>21>50 → бичачий тренд\n\n"
                "🟢 LONG — купуєте, заробляєте на рості\n"
                "🔴 SHORT — продаєте, заробляєте на падінні\n\n"
                "SL — стоп-лос, TP — тейк-профіт\n"
                "Ризик не більше 1-2% депозиту!",
                parse_mode="Markdown"); return

        if state == "sig_coin":
            context.user_data["state"] = None
            raw = text.strip().upper().replace("USDT","").replace("USD","").replace("/","")
            sym = raw + "/USD"
            msg = await update.message.reply_text(f"🔍 Аналізую {sym}...")
            s = await analyze_crypto(sym, "5m")
            if not s: await msg.edit_text(f"❌ {sym} не знайдено або немає сигналу"); return
            await msg.edit_text(fmt_signal(s), parse_mode="Markdown"); return

        # Автовизначення монети
        clean = text.strip().upper().replace("USDT","").replace("USD","").replace("/","")
        if 2<=len(clean)<=8 and clean.isalpha():
            sym = clean + "/USD"
            msg = await update.message.reply_text(f"🔍 Аналізую {sym}...")
            s = await analyze_crypto(sym, "5m")
            if s: await msg.edit_text(fmt_signal(s), parse_mode="Markdown")
            else: await msg.edit_text(f"❌ {sym} не знайдено або немає сигналу")
        return

    # ══ НАГАДУВАННЯ ══
    if section == "reminders":
        if text == "📋 Мої нагадування":
            active = [r for r in u["reminders"] if not r.get("done")]
            if not active:
                await update.message.reply_text("📋 Активних нагадувань немає\n\nНапишіть що і коли нагадати!"); return
            t = "📋 *Активні нагадування:*\n\n"
            btns = []
            for i,r in enumerate(active):
                rep = {"daily":"🔄 щодня","weekly":"🔄 щотижня","monthly":"🔄 щомісяця"}.get(r.get("repeat","none"),"")
                t += f"{i+1}. {r['title']}\n   🕐 {fmt_dt(r['datetime'])} {rep}\n\n"
                btns.append([InlineKeyboardButton(f"✅ {i+1}", callback_data=f"rem_done_{r['id']}"), InlineKeyboardButton(f"🗑 {i+1}", callback_data=f"rem_del_{r['id']}")])
            await update.message.reply_text(t, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

        if text == "✅ Виконані":
            done = [r for r in u["reminders"] if r.get("done")]
            if not done: await update.message.reply_text("✅ Виконаних немає"); return
            t = "✅ *Виконані:*\n\n" + "".join([f"• {r['title']} ({fmt_dt(r.get('done_at',r['datetime']))})\n" for r in done[-10:]])
            await update.message.reply_text(t, parse_mode="Markdown"); return

        if text == "🗑 Очистити виконані":
            before = len(u["reminders"])
            u["reminders"] = [r for r in u["reminders"] if not r.get("done")]
            save_data(data)
            await update.message.reply_text(f"🗑 Видалено {before-len(u['reminders'])} виконаних"); return

        # AI розпізнавання нагадування
        msg = await update.message.reply_text("⏳ Аналізую нагадування...")
        parsed = await parse_reminder(text)
        if not parsed or "title" not in parsed:
            await msg.edit_text("❌ Не вдалось розпізнати.\n\nПриклад: «нагадай завтра о 10 зустріч»"); return
        rid = int(now_kyiv().timestamp()*1000)
        reminder = {"id":rid,"title":parsed["title"],"datetime":parsed["datetime"],"repeat":parsed.get("repeat","none"),"done":False,"created":now_kyiv().isoformat()}
        u["reminders"].append(reminder); save_data(data)
        rep = {"daily":"🔄 Щодня","weekly":"🔄 Щотижня","monthly":"🔄 Щомісяця"}.get(reminder["repeat"],"")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Виконано", callback_data=f"rem_done_{rid}"), InlineKeyboardButton("🗑 Видалити", callback_data=f"rem_del_{rid}")]])
        await msg.edit_text(f"✅ Нагадування збережено!\n\n📝 {reminder['title']}\n🕐 {fmt_dt(reminder['datetime'])}\n{rep}", reply_markup=kb)
        return

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    d = q.data; uid = update.effective_user.id
    data = load_data(); u = get_user(data, uid)

    # ФІНАНСИ callbacks
    if d == "fin_dashboard":
        await finance_dashboard(update, u); return

    if d == "fin_add_exp":
        context.user_data["fin_type"] = "expense"; context.user_data["state"] = "fin_add_amount"
        await q.edit_message_text("Введіть суму витрати (₴ або USDT):"); return

    if d == "fin_add_inc":
        context.user_data["fin_type"] = "income"; context.user_data["state"] = "fin_add_amount"
        await q.edit_message_text("Введіть суму доходу:"); return

    if d.startswith("fin_cat_"):
        cat = d.split("_")[2]; amount = context.user_data.get("fin_amount",0)
        rate = await get_rate()
        m = mk()
        if m not in u["finance"]["months"]: u["finance"]["months"][m] = {"income":[],"expenses":[]}
        icon,name = CATEGORIES.get(cat,("📦","Інше"))
        entry = {"id":int(now_kyiv().timestamp()*1000),"amount":amount,"currency":"UAH","amount_uah":amount,"desc":name,"category":cat,"date":now_kyiv().isoformat()}
        u["finance"]["months"][m]["expenses"].append(entry); save_data(data)
        await q.edit_message_text(f"✅ Додано!\n\n{icon} {name}\n💸 {fmt(amount)}"); return

    if d.startswith("fin_inc_"):
        itype = d.split("_")[2]; amount = context.user_data.get("fin_amount",0)
        m = mk()
        if m not in u["finance"]["months"]: u["finance"]["months"][m] = {"income":[],"expenses":[]}
        icon,name = INCOME_TYPES.get(itype,("➕","Інше"))
        entry = {"id":int(now_kyiv().timestamp()*1000),"amount":amount,"currency":"UAH","amount_uah":amount,"desc":name,"income_type":itype,"date":now_kyiv().isoformat()}
        u["finance"]["months"][m]["income"].append(entry); save_data(data)
        await q.edit_message_text(f"✅ Додано!\n\n{icon} {name}\n💰 {fmt(amount)}"); return

    if d.startswith("fin_del_"):
        parts = d.split("_"); etype,eid,emk = parts[2],int(parts[3]),parts[4]
        if etype=="expense": u["finance"]["months"][emk]["expenses"]=[e for e in u["finance"]["months"][emk].get("expenses",[]) if e["id"]!=eid]
        else: u["finance"]["months"][emk]["income"]=[i for i in u["finance"]["months"][emk].get("income",[]) if i["id"]!=eid]
        save_data(data); await q.edit_message_text("🗑 Видалено"); return

    if d == "fin_set_target":
        context.user_data["state"] = "fin_set_target"
        await q.edit_message_text("🎯 Введіть суму цілі накопичень (₴):"); return

    if d == "fin_mono_enter":
        context.user_data["state"] = "fin_mono_token"
        await q.edit_message_text("🔑 Введіть токен Monobank:"); return

    if d == "fin_mono_disc":
        u["finance"]["settings"]["mono_token"] = ""; save_data(data)
        await q.edit_message_text("✅ Monobank відключено"); return

    if d == "fin_mono_sync":
        token = u["finance"]["settings"].get("mono_token","")
        if not token: await q.edit_message_text("❌ Токен не встановлено"); return
        await q.edit_message_text("⏳ Синхронізую...")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://api.monobank.ua/personal/client-info", headers={"X-Token":token}, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status!=200: await q.edit_message_text("❌ Невірний токен"); return
                    info = await r.json()
                accounts = info.get("accounts",[])
                account = next((a for a in accounts if a.get("currencyCode")==980), accounts[0] if accounts else None)
                if not account: await q.edit_message_text("❌ Рахунки не знайдені"); return
                now = now_kyiv()
                from_ts = int(datetime(now.year,now.month,1,tzinfo=TIMEZONE).timestamp())
                to_ts = int(now.timestamp())
                await asyncio.sleep(1)
                async with s.get(f"https://api.monobank.ua/personal/statement/{account['id']}/{from_ts}/{to_ts}", headers={"X-Token":token}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status!=200: await q.edit_message_text("❌ Помилка отримання виписки"); return
                    transactions = await r.json()
            if not isinstance(transactions,list): await q.edit_message_text("✅ Нових транзакцій немає"); return
            m = mk()
            if m not in u["finance"]["months"]: u["finance"]["months"][m] = {"income":[],"expenses":[]}
            existing = set(e.get("mono_id") for e in u["finance"]["months"][m].get("expenses",[])+u["finance"]["months"][m].get("income",[]) if e.get("mono_id"))
            mcc_map = {"food":[5411,5412,5441,5451,5462,5499,5812,5813,5814],"transport":[4111,4121,4511,5541,5542,7523],"health":[5122,5912,8011,8021,8062],"fun":[5815,5816,7832,7841,7922,7993]}
            count=0
            for tx in transactions:
                if tx["id"] in existing: continue
                amount=abs(tx["amount"])/100; desc=tx.get("description") or tx.get("comment") or "Mono"
                mcc=tx.get("mcc",0); cat="other"
                for cid,mccs in mcc_map.items():
                    if mcc in mccs: cat=cid; break
                entry={"id":int(now_kyiv().timestamp()*1000)+count,"mono_id":tx["id"],"amount":amount,"currency":"UAH","amount_uah":amount,"desc":desc,"date":datetime.fromtimestamp(tx["time"],TIMEZONE).isoformat(),"source":"monobank"}
                if tx["amount"]<0: entry["category"]=cat; u["finance"]["months"][m]["expenses"].append(entry)
                else: entry["income_type"]="other_income"; u["finance"]["months"][m]["income"].append(entry)
                count+=1
            save_data(data)
            await q.edit_message_text("✅ Все актуально!" if count==0 else f"✅ Додано {count} транзакцій 🏦")
        except Exception as e: await q.edit_message_text(f"❌ {str(e)[:100]}"); return

    # СИГНАЛИ callbacks
    if d.startswith("sig_detail_"):
        # символ може бути BTC/USD або просто BTC
        raw_sym = "_".join(d.split("_")[2:])  # BTC/USD може містити /
        sym = raw_sym if "/" in raw_sym else raw_sym + "/USD"
        await q.edit_message_text(f"🔍 Аналізую {sym}...")
        s = await analyze_crypto(sym, "5m")
        if s: await q.edit_message_text(fmt_signal(s), parse_mode="Markdown")
        else: await q.edit_message_text(f"❌ {sym} немає сигналу"); return

    if d.startswith("sig_fx_"):
        parts = d.split("_")
        sym = parts[2]
        tf = parts[3] if len(parts) > 3 else None
        name = FOREX_PAIRS.get(sym, sym)

        if not tf:
            # Показуємо вибір таймфрейму
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ 5 хвилин", callback_data=f"sig_fx_{sym}_5min"),
                 InlineKeyboardButton("📊 15 хвилин", callback_data=f"sig_fx_{sym}_15min")],
                [InlineKeyboardButton("⏰ 1 година", callback_data=f"sig_fx_{sym}_1h"),
                 InlineKeyboardButton("📅 4 години", callback_data=f"sig_fx_{sym}_4h")],
            ])
            await q.edit_message_text(f"💱 *{name}*\n\nОберіть таймфрейм:", parse_mode="Markdown", reply_markup=kb)
            return

        await q.edit_message_text(f"💱 Аналізую {name} ({tf})...")
        if not TWELVE_KEY:
            await q.edit_message_text("❌ TWELVE_KEY не встановлено!\n\nДодайте ключ з twelvedata.com в Railway Variables.")
            return
        s = await analyze_forex(sym, tf)
        if s:
            await q.edit_message_text(fmt_forex_signal(s), parse_mode="Markdown")
        else:
            await q.edit_message_text(f"😐 *{name}* — немає чіткого сигналу\n\nRSI в нейтральній зоні, спробуйте інший таймфрейм.", parse_mode="Markdown")
        return

    # НАГАДУВАННЯ callbacks
    if d.startswith("rem_done_"):
        rid = int(d.split("_")[2])
        for r in u["reminders"]:
            if r["id"]==rid: r["done"]=True; r["done_at"]=now_kyiv().isoformat(); break
        save_data(data); await q.edit_message_text("✅ Виконано!"); return

    if d.startswith("rem_del_"):
        rid = int(d.split("_")[2])
        u["reminders"] = [r for r in u["reminders"] if r["id"]!=rid]
        save_data(data); await q.edit_message_text("🗑 Видалено"); return

# ══════════════════════════════════════════
# ПЛАНУВАЛЬНИКИ
# ══════════════════════════════════════════
async def reminder_scheduler(app):
    while True:
        try:
            now = now_kyiv(); data = load_data(); changed = False
            for uid, udata in data.items():
                for r in udata.get("reminders",[]):
                    if r.get("done"): continue
                    try:
                        dt = datetime.fromisoformat(r["datetime"])
                        if dt.tzinfo is None: dt = dt.replace(tzinfo=TIMEZONE)
                        diff = (now-dt).total_seconds()
                        if 0<=diff<=60:
                            await app.bot.send_message(int(uid), f"🔔 *Нагадування!*\n\n📝 {r['title']}", parse_mode="Markdown",
                                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Виконано", callback_data=f"rem_done_{r['id']}")]]))
                            repeat = r.get("repeat","none")
                            if repeat=="none": r["done"]=True; r["done_at"]=now.isoformat()
                            elif repeat=="daily": r["datetime"]=(dt+timedelta(days=1)).isoformat()
                            elif repeat=="weekly": r["datetime"]=(dt+timedelta(weeks=1)).isoformat()
                            elif repeat=="monthly":
                                m=dt.month+1; y=dt.year
                                if m>12: m=1; y+=1
                                r["datetime"]=dt.replace(year=y,month=m).isoformat()
                            changed=True
                    except Exception as e: logger.error(f"Reminder error: {e}")
            if changed: save_data(data)
        except Exception as e: logger.error(f"Reminder scheduler error: {e}")
        await asyncio.sleep(30)

async def signal_scheduler(app):
    sent = {}
    while True:
        try:
            data = load_data()
            active = {uid:u for uid,u in data.items() if u.get("signals",{}).get("active")}
            if active:
                for p in CRYPTO_PAIRS[:6]:
                    s = await analyze_crypto(p,"5m")
                    if s and s["score"]>=65:
                        k = f"{p}_{s['direction']}_{int(s['price']*100)}"
                        if k not in sent:
                            sent[k] = now_kyiv().timestamp()
                            for uid in active:
                                try: await app.bot.send_message(int(uid), fmt_signal(s), parse_mode="Markdown")
                                except Exception as e: logger.error(f"Signal send: {e}")
                    await asyncio.sleep(0.5)
            now_ts = now_kyiv().timestamp()
            sent = {k:v for k,v in sent.items() if now_ts-v<1800}
        except Exception as e: logger.error(f"Signal scheduler: {e}")
        await asyncio.sleep(300)

async def post_init(app):
    asyncio.create_task(reminder_scheduler(app))
    asyncio.create_task(signal_scheduler(app))

def main():
    if not BOT_TOKEN: logger.error("BOT_TOKEN not set!"); return
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Megabot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
