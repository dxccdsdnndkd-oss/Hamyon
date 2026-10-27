"""
💰 HAMYON HUB — bitta faylli Telegram bot + Mini App
=====================================================
Xarajat/daromad hisobi (matn yoki OVOZLI xabar), hisobotlar, oylik limit,
admin panel, ob-havo, namoz vaqti, valyuta, eslatma, vazifalar, viktorina,
bonus/reyting, QR kod va Mini App — hammasi shu faylda.

RAILWAY (bepul) UCHUN:
  1) GitHub'ga FAQAT shu main.py ni yuklang → Railway: Deploy from GitHub repo.
  2) Variables:
       BOT_TOKEN      — @BotFather'dan
       ADMIN_IDS      — sizning Telegram ID (vergul bilan bir nechta)
       GROQ_API_KEY   — ovozli xabar uchun, BEPUL: console.groq.com → API Keys
       TZ_HOURS       — 5  (Toshkent)
  3) Settings → Networking → Generate Domain (Mini App uchun; WEBAPP_URL o'zi aniqlanadi).
  4) Ma'lumot o'chib ketmasligi uchun: Volume ulang (mount: /data) va DB_PATH=/data/data.db
Kutubxonalar (aiogram, aiohttp, aiosqlite) birinchi ishga tushganda o'zi o'rnatiladi.
Agar Railway o'rnatolmasa: requirements.txt yarating → aiogram, aiohttp, aiosqlite
"""
import asyncio
import hashlib
import hmac
import html
import importlib
import io
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote


def _bootstrap():
    need = {"aiohttp": "aiohttp", "aiosqlite": "aiosqlite", "aiogram": "aiogram>=3.13,<4"}
    missing = []
    for mod, pkg in need.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if not missing:
        return
    py = sys.executable
    attempts = [
        [py, "-m", "pip", "install", "-q", *missing],
        [py, "-m", "pip", "install", "-q", "--break-system-packages", *missing],
        ["uv", "pip", "install", "-q", "--python", py, *missing],
    ]
    subprocess.call([py, "-m", "ensurepip", "--upgrade"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for cmd in attempts:
        try:
            if subprocess.call(cmd) == 0:
                break
        except FileNotFoundError:
            continue
    importlib.invalidate_caches()


_bootstrap()

import aiohttp  # noqa: E402
import aiosqlite  # noqa: E402
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter  # noqa: E402
from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.state import State, StatesGroup  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    MenuButtonWebApp, Message, ReplyKeyboardMarkup, WebAppInfo,
)
from aiohttp import web  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hamyonhub")

# ───────────────────────── Sozlamalar ─────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
_dom = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
WEBAPP_URL = (os.getenv("WEBAPP_URL") or (f"https://{_dom}" if _dom else "")).rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
DB_PATH = os.getenv("DB_PATH", "data.db")
TZ_HOURS = int(os.getenv("TZ_HOURS", "5"))
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()}
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-large-v3")
WHISPER_LANG = os.getenv("WHISPER_LANG", "uz")  # rus tilida gapirsangiz: ru ; bo'sh qoldirsangiz avtomatik
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
TZ = timezone(timedelta(hours=TZ_HOURS))

db: aiosqlite.Connection
http: aiohttp.ClientSession
BOT: Bot
cache: dict = {}
r = Router()

# ───────────────────────── Kategoriyalar ─────────────────────────
CATS = {
    "food": "🍔 Oziq-ovqat", "transport": "🚕 Transport", "home": "🏠 Uy-kommunal", "comm": "📱 Aloqa",
    "clothes": "👕 Kiyim", "health": "💊 Salomatlik", "edu": "🎓 Ta'lim", "fun": "🎉 Ko'ngilochar",
    "other": "🧾 Boshqa", "income": "💵 Daromad",
}
KW = {
    "food": ["non", "go'sht", "gosht", "ovqat", "tushlik", "kechki", "nonushta", "restoran", "kafe", "bozor", "market",
             "oziq", "sabzi", "meva", "sut", "choy", "kofe", "coffee", "fastfood", "lavash", "osh", "burger", "pizza",
             "somsa", "shashlik", "non ", "produkt", "еда", "продукт", "обед", "ужин"],
    "transport": ["taksi", "taxi", "yandex", "benzin", "yoqilg", "metro", "avtobus", "transport", "mashina", "propan",
                  "parkovka", "yo'l kira", "yol kira", "такси", "бензин", "метро", "avto"],
    "home": ["kommunal", "ijara", "kvartira", "uy ", "svet", "elektr", "suv ", "gaz", "remont", "mebel", "аренда", "коммунал"],
    "comm": ["telefon", "internet", "aloqa", "paket", "uzmobile", "beeline", "ucell", "mobiuz", "tarif", "wifi", "связь"],
    "clothes": ["kiyim", "krossovka", "ko'ylak", "koylak", "shim", "kurtka", "poyabzal", "oyoq kiyim", "одежд", "обув"],
    "health": ["dori", "apteka", "shifokor", "vrach", "klinika", "tish", "tibbiy", "analiz", "kasalxona", "лекарств", "аптека"],
    "edu": ["kurs", "o'qish", "kitob", "kontrakt", "repetitor", "maktab", "universitet", "daftar", "ta'lim", "учеб", "книг"],
    "fun": ["kino", "o'yin", "oyin", "konsert", "dam olish", "karaoke", "tug'ilgan", "sovg'a", "sovga", "park", "kino", "игр", "развлеч"],
}
INC_KW = ["maosh", "oylik", "daromad", "tushdi", "avans", "premiya", "bonus", "stipendiya", "зарплат", "получил", "kelib tushdi"]
FILLER = {"so'm", "som", "sum", "сум", "uzs", "so'mga", "uchun", "ga", "ni", "to'ladim", "toladim", "berdim", "sarfladim",
          "xarajat", "harajat", "ishlatdim", "ketdi", "bugun", "kecha", "ta", "dona", "va", "ham", "man", "men", "oldim", "и", "за"}


def esc(s) -> str:
    return html.escape(str(s))


def money(n) -> str:
    return f"{int(round(n)):,}".replace(",", " ") + " so'm"


def today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def period_start(p: str) -> float:
    d = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if p == "day":
        return d.timestamp()
    if p == "week":
        return (d - timedelta(days=d.weekday())).timestamp()
    if p == "month":
        return d.replace(day=1).timestamp()
    return 0


# ───────────────────────── Summa va kategoriya parseri ─────────────────────────
NUM_W = {
    "bir": 1, "ikki": 2, "uch": 3, "to'rt": 4, "tort": 4, "besh": 5, "olti": 6, "yetti": 7, "sakkiz": 8,
    "to'qqiz": 9, "toqqiz": 9, "o'n": 10, "yigirma": 20, "o'ttiz": 30, "ottiz": 30, "qirq": 40, "ellik": 50,
    "oltmish": 60, "yetmish": 70, "saksan": 80, "to'qson": 90, "toqson": 90,
}
MULT = {
    "ming": 1_000, "тысяч": 1_000, "тысячи": 1_000, "тысяча": 1_000, "тыс": 1_000, "k": 1_000, "к": 1_000,
    "million": 1_000_000, "mln": 1_000_000, "млн": 1_000_000, "миллион": 1_000_000, "миллиона": 1_000_000,
    "миллионов": 1_000_000, "milliard": 1_000_000_000, "mlrd": 1_000_000_000,
}


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("ʻ", "'").replace("ʼ", "'").replace("`", "'").replace("‘", "'")
    return re.sub(r"(?<=\d)[ ,.](?=\d{3}(?!\d))", "", s)


def _isnum(t: str) -> bool:
    return t[0].isdigit() or t in NUM_W or t in MULT or t == "yuz"


def _eval_run(tk) -> float:
    total = cur = 0.0
    for t in tk:
        if t[0].isdigit():
            cur += float(t.replace(",", "."))
        elif t in NUM_W:
            cur += NUM_W[t]
        elif t == "yuz":
            cur = (cur or 1) * 100
        elif t in MULT:
            total += (cur or 1) * MULT[t]
            cur = 0
    return total + cur


def find_amount(text: str):
    """(summa, izoh) — topilmasa (0, '')"""
    s = norm(text)
    tk = re.findall(r"\d+(?:[.,]\d+)?|[a-zа-яёўқғҳ']+", s)
    i, n = 0, len(tk)
    while i < n:
        if _isnum(tk[i]):
            j = i
            while j < n and _isnum(tk[j]):
                j += 1
            val = _eval_run(tk[i:j])
            if val >= 100:
                rest = tk[:i] + tk[j:]
                note = " ".join(t for t in rest if t not in FILLER and not t.isdigit())[:60].strip()
                return int(round(val)), note
            i = j
        else:
            i += 1
    return 0, ""


def classify(text: str):
    s = " " + norm(text) + " "
    if any(k in s for k in INC_KW):
        return "inc", "income"
    for cat, kws in KW.items():
        if any(k in s for k in kws):
            return "exp", cat
    return "exp", "other"


def local_parse(text: str):
    items = []
    for seg in re.split(r"[;\n]+|,(?!\d)|\bva\b|\bhamda\b|\bи\b", norm(text)):
        amt, note = find_amount(seg)
        if amt:
            kind, cat = classify(seg)
            items.append({"amount": amt, "kind": kind, "cat": cat, "note": (note or CATS[cat].split(" ", 1)[1]).capitalize()})
    return items


LLM_SYS = (
    "Sen xarajat tahlilchisisan. Foydalanuvchi matni (o'zbek yoki rus tilida, ovozdan olingan bo'lishi mumkin) ichidan "
    "barcha xarajat yoki daromadlarni ajrat. Summa so'mda BUTUN son ('ming'=1000, 'million'=1000000, 'yigirma besh ming'=25000). "
    'Faqat JSON qaytar: {"items":[{"amount":int,"kind":"exp"|"inc","category":"food|transport|home|comm|clothes|health|edu|fun|other|income",'
    '"note":"2-3 so\'zli qisqa izoh"}]}. Daromad (maosh, tushum) bo\'lsa kind=inc, category=income. Xarajat yo\'q bo\'lsa items bo\'sh.'
)


async def llm_parse(text: str):
    try:
        body = {"model": LLM_MODEL, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": LLM_SYS}, {"role": "user", "content": text[:1000]}]}
        async with http.post("https://api.groq.com/openai/v1/chat/completions", json=body,
                             headers={"Authorization": f"Bearer {GROQ_KEY}"},
                             timeout=aiohttp.ClientTimeout(total=25)) as resp:
            j = await resp.json()
        data = json.loads(j["choices"][0]["message"]["content"])
        out = []
        for x in data.get("items", []):
            amt = int(float(x["amount"]))
            if amt <= 0 or amt > 10**12:
                continue
            kind = "inc" if x.get("kind") == "inc" else "exp"
            cat = x.get("category") if x.get("category") in CATS else "other"
            if kind == "inc":
                cat = "income"
            elif cat == "income":
                cat = "other"
            out.append({"amount": amt, "kind": kind, "cat": cat, "note": str(x.get("note", ""))[:60] or CATS[cat].split(" ", 1)[1]})
        return out
    except Exception:
        log.exception("llm_parse")
        return None


async def parse_items(text: str, voice: bool = False):
    if voice and GROQ_KEY:
        res = await llm_parse(text)
        return res or local_parse(text)
    items = local_parse(text)
    if not items and GROQ_KEY and re.search(r"\d|ming|million|yuz|bir|ikki|besh", text.lower()):
        items = await llm_parse(text) or []
    return items


async def transcribe(data: bytes) -> str:
    form = aiohttp.FormData()
    form.add_field("file", data, filename="voice.ogg", content_type="audio/ogg")
    form.add_field("model", WHISPER_MODEL)
    if WHISPER_LANG:
        form.add_field("language", WHISPER_LANG)
    form.add_field("temperature", "0")
    form.add_field("prompt", "Xarajat: non besh ming so'm, taksi o'n ikki ming, tushlik o'ttiz besh ming, maosh uch million.")
    async with http.post("https://api.groq.com/openai/v1/audio/transcriptions", data=form,
                         headers={"Authorization": f"Bearer {GROQ_KEY}"},
                         timeout=aiohttp.ClientTimeout(total=40)) as resp:
        j = await resp.json()
        if resp.status != 200:
            raise RuntimeError(str(j))
        return (j.get("text") or "").strip()


# ───────────────────────── DB ─────────────────────────
async def init_db():
    global db
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, name TEXT, coins INTEGER DEFAULT 0, streak INTEGER DEFAULT 0,
            last_bonus TEXT DEFAULT '', city TEXT DEFAULT '', quiz_day TEXT DEFAULT '', quiz_cnt INTEGER DEFAULT 0,
            ref INTEGER DEFAULT 0, joined INTEGER DEFAULT 0, banned INTEGER DEFAULT 0,
            last_seen INTEGER DEFAULT 0, limit_m INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS todos(
            id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, text TEXT, done INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS reminders(
            id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, text TEXT, at REAL, sent INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS expenses(
            id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, amount REAL, kind TEXT DEFAULT 'exp',
            cat TEXT DEFAULT 'other', note TEXT DEFAULT '', ts INTEGER);
        CREATE INDEX IF NOT EXISTS ix_exp ON expenses(uid, ts);
        """
    )
    for col in ("banned INTEGER DEFAULT 0", "last_seen INTEGER DEFAULT 0", "limit_m INTEGER DEFAULT 0"):
        try:
            await db.execute(f"ALTER TABLE users ADD COLUMN {col}")
        except Exception:
            pass
    await db.commit()


async def get_user(uid: int):
    cur = await db.execute("SELECT * FROM users WHERE id=?", (uid,))
    return await cur.fetchone()


async def ensure_user(uid: int, name: str, ref: int = 0):
    u = await get_user(uid)
    if u:
        if name and u["name"] != name:
            await db.execute("UPDATE users SET name=? WHERE id=?", (name, uid))
            await db.commit()
        return u, False
    await db.execute("INSERT INTO users(id,name,coins,ref,joined,last_seen) VALUES(?,?,?,?,?,?)",
                     (uid, name, 20, ref, int(time.time()), int(time.time())))
    await db.commit()
    return await get_user(uid), True


async def add_coins(uid: int, n: int):
    await db.execute("UPDATE users SET coins=coins+? WHERE id=?", (n, uid))
    await db.commit()


async def do_bonus(uid: int):
    u = await get_user(uid)
    t = today()
    if u["last_bonus"] == t:
        return False, 0, u["streak"], u["coins"]
    yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    streak = u["streak"] + 1 if u["last_bonus"] == yesterday else 1
    gain = 10 + min(streak, 7) * 5
    await db.execute("UPDATE users SET coins=coins+?, streak=?, last_bonus=? WHERE id=?", (gain, streak, t, uid))
    await db.commit()
    return True, gain, streak, u["coins"] + gain


async def summary(uid: int, p: str):
    cur = await db.execute(
        "SELECT kind,cat,SUM(amount) s FROM expenses WHERE uid=? AND ts>=? GROUP BY kind,cat", (uid, period_start(p)))
    rows = await cur.fetchall()
    exp = sum(x["s"] for x in rows if x["kind"] == "exp")
    inc = sum(x["s"] for x in rows if x["kind"] == "inc")
    by = {x["cat"]: x["s"] for x in rows if x["kind"] == "exp"}
    return exp, inc, by


async def save_items(uid: int, items):
    ids, lines = [], []
    for it in items:
        cur = await db.execute("INSERT INTO expenses(uid,amount,kind,cat,note,ts) VALUES(?,?,?,?,?,?)",
                               (uid, it["amount"], it["kind"], it["cat"], it["note"], int(time.time())))
        ids.append(cur.lastrowid)
        lines.append(item_line(it))
    await db.commit()
    return ids, lines


def item_line(it) -> str:
    sign = "➕" if it["kind"] == "inc" else "➖"
    return f"{sign} {CATS.get(it['cat'], it['cat'])} — {esc(it['note'])}: <b>{money(it['amount'])}</b>"


async def limit_warn(uid: int) -> str:
    u = await get_user(uid)
    lim = u["limit_m"] if u else 0
    if not lim:
        return ""
    exp, _, _ = await summary(uid, "month")
    if exp > lim:
        return f"\n\n🚨 <b>Oylik limitdan oshdingiz!</b> {money(exp)} / {money(lim)}"
    if exp >= 0.8 * lim:
        return f"\n\n⚠️ Limitning {int(exp / lim * 100)}% ishlatildi ({money(exp)} / {money(lim)})"
    return ""


# ───────────────────────── Tashqi API'lar (bepul) ─────────────────────────
async def jget(url: str, ttl: int = 300):
    c = cache.get(url)
    if c and time.time() - c[0] < ttl:
        return c[1]
    async with http.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        data = await resp.json(content_type=None)
    cache[url] = (time.time(), data)
    return data


async def geocode(city: str):
    d = await jget(f"https://geocoding-api.open-meteo.com/v1/search?name={quote(city)}&count=1&language=en", 3600)
    res = (d.get("results") or [None])[0]
    return (res["latitude"], res["longitude"], res["name"]) if res else None


WMO = [
    ({0}, "Ochiq ☀️"), ({1, 2, 3}, "Qisman bulutli ⛅"), ({45, 48}, "Tuman 🌫"),
    ({51, 53, 55, 56, 57}, "Mayda yomg'ir 🌦"), ({61, 63, 65, 66, 67, 80, 81, 82}, "Yomg'ir 🌧"),
    ({71, 73, 75, 77, 85, 86}, "Qor ❄️"), ({95, 96, 99}, "Momaqaldiroq ⛈"),
]


def wmo_text(code: int) -> str:
    for s, t in WMO:
        if code in s:
            return t
    return "—"


async def get_weather(city: str):
    g = await geocode(city)
    if not g:
        return None
    lat, lon, name = g
    d = await jget(
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=1", 600)
    c, dl = d["current"], d["daily"]
    return {"city": name, "temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
            "hum": c["relative_humidity_2m"], "wind": c["wind_speed_10m"], "desc": wmo_text(c["weather_code"]),
            "max": round(dl["temperature_2m_max"][0]), "min": round(dl["temperature_2m_min"][0])}


PRAYER_UZ = [("Fajr", "Bomdod"), ("Sunrise", "Quyosh"), ("Dhuhr", "Peshin"), ("Asr", "Asr"), ("Maghrib", "Shom"), ("Isha", "Xufton")]


async def get_prayer(city: str):
    g = await geocode(city)
    if not g:
        return None
    lat, lon, name = g
    d = await jget(f"https://api.aladhan.com/v1/timings?latitude={lat}&longitude={lon}&method=3&school=1", 1800)
    t = d["data"]["timings"]
    return {"city": name, "times": [[uz, t[k].split()[0]] for k, uz in PRAYER_UZ]}


CCYS = ["USD", "EUR", "RUB", "KZT", "GBP", "TRY", "CNY"]


async def get_rates():
    d = await jget("https://cbu.uz/uz/arkhiv-kursov-valyut/json/", 1800)
    out = []
    for x in d:
        if x["Ccy"] in CCYS:
            nom = float(x.get("Nominal") or 1)
            out.append({"ccy": x["Ccy"], "rate": float(x["Rate"]) / nom, "diff": float(x["Diff"]) / nom})
    out.sort(key=lambda z: CCYS.index(z["ccy"]))
    return out


# ───────────────────────── Viktorina ─────────────────────────
QUIZ = [
    ("O'zbekiston poytaxti qaysi shahar?", ["Samarqand", "Toshkent", "Buxoro", "Namangan"], 1),
    ("Eng katta okean qaysi?", ["Atlantika", "Hind", "Tinch", "Shimoliy Muz"], 2),
    ("Amir Temur qaysi shaharda tug'ilgan?", ["Shahrisabz (Kesh)", "Xiva", "Termiz", "Andijon"], 0),
    ("1 soatda necha daqiqa bor?", ["30", "60", "90", "100"], 1),
    ("Suvning kimyoviy formulasi?", ["CO2", "O2", "H2O", "NaCl"], 2),
    ("Quyosh sistemasidagi eng katta sayyora?", ["Yer", "Saturn", "Mars", "Yupiter"], 3),
    ("«Mirzo Ulug'bek» qaysi sohada mashhur?", ["Astronomiya", "Tibbiyot", "Shoirlik", "Savdo"], 0),
    ("Dunyodagi eng baland tog'?", ["K2", "Everest", "Elbrus", "Fuji"], 1),
    ("Telegram'ni kim yaratgan?", ["Pavel Durov", "Mark Sukerberg", "Ilon Mask", "Bill Geyts"], 0),
    ("Bir yilda necha oy bor?", ["10", "11", "12", "13"], 2),
    ("Eng tez quruqlikdagi hayvon?", ["Sher", "Gepard", "Ot", "Yo'lbars"], 1),
    ("Nechta qit'a bor (umumiy qabul qilingan)?", ["5", "6", "7", "8"], 2),
    ("Byudjetni rejalashtirishda 50/30/20 qoidasida 20% nima uchun?", ["Ko'ngilochar", "Jamg'arma", "Kommunal", "Transport"], 1),
]


async def award_quiz(uid: int, ok: bool) -> int:
    u = await get_user(uid)
    t = today()
    cnt = u["quiz_cnt"] if u["quiz_day"] == t else 0
    gain = 0
    if ok and cnt < 20:
        gain, cnt = 5, cnt + 1
    await db.execute("UPDATE users SET quiz_day=?, quiz_cnt=?, coins=coins+? WHERE id=?", (t, cnt, gain, uid))
    await db.commit()
    return gain


# ───────────────────────── Admin ma'lumotlari ─────────────────────────
async def stats_data():
    t0 = int(period_start("day"))
    q = lambda sql, a=(): db.execute(sql, a)  # noqa: E731
    n = (await (await q("SELECT COUNT(*) c FROM users")).fetchone())["c"]
    nn = (await (await q("SELECT COUNT(*) c FROM users WHERE joined>=?", (t0,))).fetchone())["c"]
    act = (await (await q("SELECT COUNT(*) c FROM users WHERE last_seen>=?", (t0,))).fetchone())["c"]
    bn = (await (await q("SELECT COUNT(*) c FROM users WHERE banned=1")).fetchone())["c"]
    e = await (await q("SELECT COUNT(*) c, COALESCE(SUM(CASE WHEN kind='exp' THEN amount END),0) s FROM expenses")).fetchone()
    return {"users": n, "new_today": nn, "active_today": act, "banned": bn, "records": e["c"], "total_exp": e["s"]}


async def users_data(limit: int = 20):
    cur = await db.execute(
        "SELECT u.id,u.name,u.coins,u.banned,COALESCE((SELECT SUM(amount) FROM expenses e WHERE e.uid=u.id AND e.kind='exp'),0) spent "
        "FROM users u ORDER BY u.last_seen DESC LIMIT ?", (limit,))
    return [dict(x) for x in await cur.fetchall()]


async def toggle_ban(uid: int):
    u = await get_user(uid)
    if not u:
        return None
    nb = 0 if u["banned"] else 1
    await db.execute("UPDATE users SET banned=? WHERE id=?", (nb, uid))
    await db.commit()
    return nb


async def do_broadcast(text: str, admin_id: int):
    cur = await db.execute("SELECT id FROM users WHERE banned=0")
    ids = [x["id"] for x in await cur.fetchall()]
    ok = 0
    for uid in ids:
        try:
            await BOT.send_message(uid, text)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    try:
        await BOT.send_message(admin_id, f"📢 E'lon yuborildi: {ok}/{len(ids)}")
    except Exception:
        pass


# ───────────────────────── Bot: klaviatura va holatlar ─────────────────────────
class S(StatesGroup):
    weather = State()
    prayer = State()
    rem_text = State()
    rem_when = State()
    qr = State()
    city = State()
    limit = State()
    bc = State()
    ban = State()


B_APP, B_REP, B_REC, B_LIM = "📱 Mini App", "📊 Hisobot", "🧾 So'nggilar", "💰 Limit"
B_W, B_P, B_C, B_R = "🌤 Ob-havo", "🕌 Namoz", "💱 Valyuta", "⏰ Eslatma"
B_Q, B_B, B_QR, B_T = "🧠 Viktorina", "🎁 Bonus", "🔳 QR kod", "🏆 Reyting"
B_I, B_CITY, B_ADM = "👥 Do'st taklif", "⚙️ Shahar", "🛠 Admin"

HELP = (
    "💡 <b>Xarajat qo'shish juda oson:</b>\n"
    "• Yozing: <code>taksi 15 ming</code>, <code>non 5000, tushlik 35 ming</code>\n"
    "• Daromad: <code>maosh 3 million</code>\n"
    "• 🎤 <b>Ovozli xabar</b> yuboring: «taksi o'n besh ming so'm»\n\n"
    "Hisobot — «📊 Hisobot», o'chirish — «🧾 So'nggilar»."
)


def menu(uid: int = 0) -> ReplyKeyboardMarkup:
    rows = [[B_APP, B_REP], [B_REC, B_LIM], [B_W, B_P], [B_C, B_R], [B_Q, B_B], [B_QR, B_T], [B_I, B_CITY]]
    if uid in ADMIN_IDS:
        rows.append([B_ADM])
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in row] for row in rows], resize_keyboard=True)


def app_kb():
    if not WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📱 Mini App'ni ochish", web_app=WebAppInfo(url=WEBAPP_URL))]])


class Guard(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = data.get("event_from_user")
        if u:
            try:
                row = await get_user(u.id)
                if row:
                    await db.execute("UPDATE users SET last_seen=? WHERE id=?", (int(time.time()), u.id))
                    await db.commit()
                    if row["banned"] and u.id not in ADMIN_IDS:
                        return
            except Exception:
                log.exception("guard")
        return await handler(event, data)


@r.message(CommandStart())
async def start(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    ref = 0
    if command.args and command.args.startswith("ref_"):
        try:
            ref = int(command.args[4:])
        except ValueError:
            ref = 0
    u, new = await ensure_user(m.from_user.id, m.from_user.full_name, ref if ref != m.from_user.id else 0)
    if new and ref and ref != m.from_user.id and await get_user(ref):
        await add_coins(ref, 50)
        try:
            await m.bot.send_message(ref, f"🎉 Do'stingiz {esc(m.from_user.full_name)} qo'shildi! +50 🪙")
        except Exception:
            pass
    await m.answer(
        f"Salom, <b>{esc(m.from_user.full_name)}</b>! 👋\n\n"
        "Men <b>Hamyon Hub</b> — xarajatlaringizni yurituvchi yordamchingizman.\n"
        "Yozib yoki 🎤 ovozli xabar bilan kiriting — men hisoblab, hisobot beraman.\n\n" + HELP,
        reply_markup=menu(m.from_user.id))
    if app_kb():
        await m.answer("Hammasi bir joyda:", reply_markup=app_kb())


@r.message(F.text == B_APP)
async def open_app(m: Message):
    await m.answer("Mini App:", reply_markup=app_kb()) if app_kb() else await m.answer("Mini App sozlanmagan (domen yo'q).")


# ───── Xarajat: ovozli xabar ─────
@r.message(F.voice | F.audio)
async def on_voice(m: Message, state: FSMContext):
    await state.clear()
    await ensure_user(m.from_user.id, m.from_user.full_name)
    if not GROQ_KEY:
        return await m.answer("🎤 Ovozli kiritish yoqilmagan (admin <code>GROQ_API_KEY</code> qo'shishi kerak).\n"
                              "Hozircha yozib yuboring: <code>taksi 15 ming</code>")
    f = m.voice or m.audio
    if (f.duration or 0) > 90:
        return await m.answer("Ovozli xabar 90 soniyadan oshmasin.")
    st = await m.answer("🎧 Tinglayapman...")
    try:
        buf = io.BytesIO()
        await m.bot.download(f, destination=buf)
        text = await transcribe(buf.getvalue())
    except Exception:
        log.exception("transcribe")
        return await st.edit_text("Ovozni o'qib bo'lmadi 😕 Qaytadan yuboring yoki yozib yuboring.")
    if not text:
        return await st.edit_text("Hech narsa eshitilmadi 😕 Aniqroq gapirib qayta yuboring.")
    items = await parse_items(text, voice=True)
    if not items:
        return await st.edit_text(f"🎤 «{esc(text)}»\n\nSumma topilmadi. Masalan: «taksi o'n besh ming so'm»")
    await reply_saved(st, m.from_user.id, items, heard=text, edit=True)


# ───── Xarajat: umumiy saqlash javobi ─────
async def reply_saved(msg: Message, uid: int, items, heard: str = "", edit: bool = False):
    ids, lines = await save_items(uid, items)
    head = f"🎤 «{esc(heard)}»\n\n" if heard else ""
    text = head + "✅ <b>Saqlandi:</b>\n" + "\n".join(lines) + await limit_warn(uid)
    if len(ids) == 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏷 Kategoriya", callback_data=f"xc:{ids[0]}"),
            InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"xd:{ids[0]}")]])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑 Hammasini bekor qilish", callback_data="xu:" + ",".join(map(str, ids)))]])
    await (msg.edit_text(text, reply_markup=kb) if edit else msg.answer(text, reply_markup=kb))


@r.callback_query(F.data.startswith("xd:"))
async def x_delete(c: CallbackQuery):
    await db.execute("DELETE FROM expenses WHERE id=? AND uid=?", (int(c.data[3:]), c.from_user.id))
    await db.commit()
    await c.message.edit_text("🗑 O'chirildi.")
    await c.answer()


@r.callback_query(F.data.startswith("xu:"))
async def x_undo(c: CallbackQuery):
    for i in c.data[3:].split(","):
        await db.execute("DELETE FROM expenses WHERE id=? AND uid=?", (int(i), c.from_user.id))
    await db.commit()
    await c.message.edit_text("🗑 Bekor qilindi.")
    await c.answer()


@r.callback_query(F.data.startswith("xc:"))
async def x_cat_menu(c: CallbackQuery):
    xid = c.data[3:]
    cats = list(CATS.items())
    rows = [[InlineKeyboardButton(text=v, callback_data=f"xs:{xid}:{k}") for k, v in cats[i:i + 2]] for i in range(0, len(cats), 2)]
    await c.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@r.callback_query(F.data.startswith("xs:"))
async def x_cat_set(c: CallbackQuery):
    _, xid, cat = c.data.split(":")
    if cat not in CATS:
        return await c.answer()
    kind = "inc" if cat == "income" else "exp"
    await db.execute("UPDATE expenses SET cat=?, kind=? WHERE id=? AND uid=?", (cat, kind, int(xid), c.from_user.id))
    await db.commit()
    cur = await db.execute("SELECT * FROM expenses WHERE id=? AND uid=?", (int(xid), c.from_user.id))
    row = await cur.fetchone()
    if not row:
        return await c.answer("Topilmadi")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏷 Kategoriya", callback_data=f"xc:{xid}"),
        InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"xd:{xid}")]])
    await c.message.edit_text("✅ <b>Yangilandi:</b>\n" + item_line({"amount": row["amount"], "kind": row["kind"], "cat": row["cat"], "note": row["note"]}), reply_markup=kb)
    await c.answer()


# ───── Hisobot, so'nggilar, limit ─────
async def report_view(uid: int, p: str):
    exp, inc, by = await summary(uid, p)
    names = {"day": "Bugun", "week": "Shu hafta", "month": "Shu oy"}
    t = (f"📊 <b>{names[p]}</b>\n\n💸 Xarajat: <b>{money(exp)}</b>\n💵 Daromad: <b>{money(inc)}</b>\n"
         f"⚖️ Balans: <b>{money(inc - exp)}</b>\n")
    if by:
        t += "\n"
        for cat, v in sorted(by.items(), key=lambda z: -z[1]):
            frac = v / exp if exp else 0
            t += f"{CATS.get(cat, cat)}\n{'█' * round(frac * 10)}{'░' * (10 - round(frac * 10))} {money(v)} ({int(frac * 100)}%)\n"
    else:
        t += "\nHali xarajat yo'q."
    if p == "month":
        u = await get_user(uid)
        if u and u["limit_m"]:
            t += f"\n🎯 Limit: {money(exp)} / {money(u['limit_m'])} ({int(exp / u['limit_m'] * 100)}%)"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=("• " if p == k else "") + v, callback_data=f"rp:{k}")
        for k, v in (("day", "Bugun"), ("week", "Hafta"), ("month", "Oy"))]])
    return t, kb


@r.message(F.text == B_REP)
async def rep_btn(m: Message):
    await ensure_user(m.from_user.id, m.from_user.full_name)
    t, kb = await report_view(m.from_user.id, "month")
    await m.answer(t, reply_markup=kb)


@r.callback_query(F.data.startswith("rp:"))
async def rep_cb(c: CallbackQuery):
    t, kb = await report_view(c.from_user.id, c.data[3:])
    try:
        await c.message.edit_text(t, reply_markup=kb)
    except Exception:
        pass
    await c.answer()


async def recent_view(uid: int):
    cur = await db.execute("SELECT * FROM expenses WHERE uid=? ORDER BY ts DESC, id DESC LIMIT 8", (uid,))
    rows = await cur.fetchall()
    if not rows:
        return "Hali yozuv yo'q.\n\n" + HELP, None
    lines, btns = [], []
    for i, x in enumerate(rows, 1):
        d = datetime.fromtimestamp(x["ts"], TZ).strftime("%d.%m %H:%M")
        sign = "+" if x["kind"] == "inc" else "-"
        lines.append(f"{i}. {CATS.get(x['cat'], x['cat'])} {esc(x['note'])} <b>{sign}{money(x['amount'])}</b> <i>{d}</i>")
        btns.append(InlineKeyboardButton(text=f"🗑 {i}", callback_data=f"rd:{x['id']}"))
    return "🧾 <b>So'nggi yozuvlar</b>\n\n" + "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[btns[i:i + 4] for i in range(0, len(btns), 4)])


@r.message(F.text == B_REC)
async def rec_btn(m: Message):
    t, kb = await recent_view(m.from_user.id)
    await m.answer(t, reply_markup=kb)


@r.callback_query(F.data.startswith("rd:"))
async def rec_del(c: CallbackQuery):
    await db.execute("DELETE FROM expenses WHERE id=? AND uid=?", (int(c.data[3:]), c.from_user.id))
    await db.commit()
    t, kb = await recent_view(c.from_user.id)
    await c.message.edit_text(t, reply_markup=kb)
    await c.answer("O'chirildi")


async def set_limit(m: Message, val: int):
    await ensure_user(m.from_user.id, m.from_user.full_name)
    await db.execute("UPDATE users SET limit_m=? WHERE id=?", (val, m.from_user.id))
    await db.commit()
    await m.answer(f"🎯 Oylik limit: <b>{money(val)}</b>" if val else "Limit o'chirildi.")


@r.message(F.text == B_LIM)
@r.message(Command("limit"))
async def limit_btn(m: Message, command: CommandObject = None, state: FSMContext = None):
    await ensure_user(m.from_user.id, m.from_user.full_name)
    if command and command.args:
        amt, _ = find_amount(command.args)
        return await set_limit(m, amt)
    u = await get_user(m.from_user.id)
    cur_l = f"Hozirgi limit: <b>{money(u['limit_m'])}</b>\n\n" if u["limit_m"] else ""
    await state.set_state(S.limit)
    await m.answer(cur_l + "Oylik xarajat limitini yozing (masalan <code>3 million</code>). O'chirish uchun <code>0</code>.")


@r.message(S.limit, F.text)
async def limit_set(m: Message, state: FSMContext):
    await state.clear()
    if m.text.strip() in ("0", "yo'q", "yoq"):
        return await set_limit(m, 0)
    amt, _ = find_amount(m.text)
    if not amt:
        return await m.answer("Summani tushunmadim 😕 /limit buyrug'i bilan qayta urinib ko'ring.")
    await set_limit(m, amt)


# ───── Shahar, ob-havo, namoz ─────
@r.message(F.text == B_CITY)
@r.message(Command("city"))
async def city_ask(m: Message, state: FSMContext):
    await state.set_state(S.city)
    await m.answer("Shahringiz nomini yozing (masalan: Toshkent, Samarqand):")


@r.message(S.city, F.text)
async def city_set(m: Message, state: FSMContext):
    g = await geocode(m.text.strip())
    if not g:
        return await m.answer("Shahar topilmadi 😕 Lotin harflarida qayta yozing.")
    await ensure_user(m.from_user.id, m.from_user.full_name)
    await db.execute("UPDATE users SET city=? WHERE id=?", (g[2], m.from_user.id))
    await db.commit()
    await state.clear()
    await m.answer(f"✅ Shahar saqlandi: <b>{esc(g[2])}</b>")


async def city_of(uid: int) -> str:
    u = await get_user(uid)
    return u["city"] if u else ""


async def send_weather(m: Message, city: str):
    w = await get_weather(city)
    if not w:
        return await m.answer("Shahar topilmadi 😕")
    await m.answer(f"📍 <b>{esc(w['city'])}</b>\n{w['desc']}\n🌡 {w['temp']}° (his etilishi {w['feels']}°)\n"
                   f"⬆️ {w['max']}°  ⬇️ {w['min']}°\n💧 Namlik {w['hum']}%  💨 Shamol {w['wind']} km/s")


@r.message(F.text == B_W)
async def weather_btn(m: Message, state: FSMContext):
    await ensure_user(m.from_user.id, m.from_user.full_name)
    c = await city_of(m.from_user.id)
    if c:
        return await send_weather(m, c)
    await state.set_state(S.weather)
    await m.answer("Qaysi shahar ob-havosi kerak?")


@r.message(S.weather, F.text)
async def weather_city(m: Message, state: FSMContext):
    await state.clear()
    await send_weather(m, m.text.strip())


async def send_prayer(m: Message, city: str):
    p = await get_prayer(city)
    if not p:
        return await m.answer("Shahar topilmadi 😕")
    lines = "\n".join(f"{'🌅' if n == 'Quyosh' else '🕌'} {n}: <b>{t}</b>" for n, t in p["times"])
    await m.answer(f"📍 <b>{esc(p['city'])}</b> — bugungi namoz vaqtlari\n\n{lines}")


@r.message(F.text == B_P)
async def prayer_btn(m: Message, state: FSMContext):
    await ensure_user(m.from_user.id, m.from_user.full_name)
    c = await city_of(m.from_user.id)
    if c:
        return await send_prayer(m, c)
    await state.set_state(S.prayer)
    await m.answer("Qaysi shahar uchun namoz vaqti kerak?")


@r.message(S.prayer, F.text)
async def prayer_city(m: Message, state: FSMContext):
    await state.clear()
    await send_prayer(m, m.text.strip())


@r.message(F.text == B_C)
async def rates_btn(m: Message):
    try:
        rates = await get_rates()
    except Exception:
        return await m.answer("Kurslarni olib bo'lmadi, keyinroq urinib ko'ring.")
    lines = []
    for x in rates:
        arrow = "🔺" if x["diff"] > 0 else "🔻" if x["diff"] < 0 else "▫️"
        lines.append(f"{arrow} 1 {x['ccy']} = <b>{money(x['rate'])}</b>")
    await m.answer("💱 <b>Markaziy bank kurslari</b>\n\n" + "\n".join(lines))


# ───── Eslatma ─────
def parse_when(s: str):
    s = s.strip().lower()
    mt = re.fullmatch(r"(\d+)\s*([mhd])", s)
    if mt:
        return time.time() + int(mt[1]) * {"m": 60, "h": 3600, "d": 86400}[mt[2]]
    mt = re.fullmatch(r"(\d{1,2})[:.](\d{2})", s)
    if mt:
        h, mi = int(mt[1]), int(mt[2])
        if h > 23 or mi > 59:
            return None
        now = datetime.now(TZ)
        t = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        return t.timestamp()
    return None


@r.message(F.text == B_R)
async def rem_btn(m: Message, state: FSMContext):
    await state.set_state(S.rem_text)
    await m.answer("Nimani eslatay? Matnini yozing:")


@r.message(S.rem_text, F.text)
async def rem_text(m: Message, state: FSMContext):
    await state.update_data(text=m.text[:300])
    await state.set_state(S.rem_when)
    await m.answer("Qachon? Masalan: <code>30m</code> (30 daqiqa), <code>2h</code> (2 soat), <code>1d</code> (1 kun) yoki <code>18:30</code>")


@r.message(S.rem_when, F.text)
async def rem_when(m: Message, state: FSMContext):
    at = parse_when(m.text)
    if not at:
        return await m.answer("Tushunmadim 😕 <code>30m</code>, <code>2h</code> yoki <code>18:30</code> ko'rinishida yozing.")
    data = await state.get_data()
    await ensure_user(m.from_user.id, m.from_user.full_name)
    await db.execute("INSERT INTO reminders(uid,text,at) VALUES(?,?,?)", (m.from_user.id, data["text"], at))
    await db.commit()
    await state.clear()
    await m.answer(f"✅ Eslatma o'rnatildi: <b>{datetime.fromtimestamp(at, TZ).strftime('%d.%m %H:%M')}</b>")


async def reminder_loop(bot: Bot):
    while True:
        try:
            cur = await db.execute("SELECT * FROM reminders WHERE sent=0 AND at<=?", (time.time(),))
            for row in await cur.fetchall():
                await db.execute("UPDATE reminders SET sent=1 WHERE id=?", (row["id"],))
                try:
                    await bot.send_message(row["uid"], f"⏰ <b>Eslatma:</b>\n{esc(row['text'])}")
                except Exception:
                    pass
            await db.commit()
        except Exception:
            log.exception("reminder loop")
        await asyncio.sleep(15)


@r.message(Command("add"))
async def add_todo(m: Message, command: CommandObject):
    if not command.args:
        return await m.answer("Misol: <code>/add Non olish</code>")
    await ensure_user(m.from_user.id, m.from_user.full_name)
    await db.execute("INSERT INTO todos(uid,text) VALUES(?,?)", (m.from_user.id, command.args[:200]))
    await db.commit()
    await m.answer("✅ Vazifa qo'shildi. Hammasini Mini App'da ko'rasiz.", reply_markup=app_kb())


# ───── Bonus, reyting, taklif, QR, viktorina ─────
@r.message(F.text == B_B)
async def bonus_btn(m: Message):
    await ensure_user(m.from_user.id, m.from_user.full_name)
    ok, gain, streak, coins = await do_bonus(m.from_user.id)
    if not ok:
        return await m.answer(f"Bugungi bonusni olgansiz ✅\n🔥 Seriya: {streak} kun • 🪙 {coins}\nErtaga qaytib keling!")
    await m.answer(f"🎁 +{gain} 🪙\n🔥 Ketma-ket {streak} kun\n💰 Jami: {coins} 🪙")


@r.message(F.text == B_T)
async def top_btn(m: Message):
    cur = await db.execute("SELECT name,coins FROM users WHERE banned=0 ORDER BY coins DESC LIMIT 10")
    rows = await cur.fetchall()
    medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 7
    text = "\n".join(f"{medals[i]} {esc(x['name'])} — <b>{x['coins']}</b> 🪙" for i, x in enumerate(rows))
    await m.answer("🏆 <b>Top 10</b>\n\n" + (text or "Hali hech kim yo'q"))


@r.message(F.text == B_I)
async def invite_btn(m: Message):
    me = await m.bot.get_me()
    await m.answer(f"Do'stingizni taklif qiling — har biri uchun <b>+50 🪙</b>!\n\nhttps://t.me/{me.username}?start=ref_{m.from_user.id}")


@r.message(F.text == B_QR)
async def qr_btn(m: Message, state: FSMContext):
    await state.set_state(S.qr)
    await m.answer("QR kodga aylantirmoqchi bo'lgan matn yoki havolani yuboring:")


@r.message(S.qr, F.text)
async def qr_make(m: Message, state: FSMContext):
    await state.clear()
    await m.answer_photo(f"https://api.qrserver.com/v1/create-qr-code/?size=500x500&data={quote(m.text[:900])}", caption="✅ Tayyor!")


def quiz_msg(qid: int):
    q, opts, _ = QUIZ[qid]
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=o, callback_data=f"qz:{qid}:{i}")] for i, o in enumerate(opts)])
    return f"🧠 <b>{esc(q)}</b>", kb


@r.message(F.text == B_Q)
async def quiz_btn(m: Message):
    await ensure_user(m.from_user.id, m.from_user.full_name)
    text, kb = quiz_msg(random.randrange(len(QUIZ)))
    await m.answer(text, reply_markup=kb)


@r.callback_query(F.data == "qz:next")
async def quiz_next(c: CallbackQuery):
    text, kb = quiz_msg(random.randrange(len(QUIZ)))
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@r.callback_query(F.data.startswith("qz:"))
async def quiz_answer(c: CallbackQuery):
    _, qid, idx = c.data.split(":")
    qid, idx = int(qid), int(idx)
    q, opts, right = QUIZ[qid]
    await ensure_user(c.from_user.id, c.from_user.full_name)
    gain = await award_quiz(c.from_user.id, idx == right)
    res = (f"✅ To'g'ri! {'+5 🪙' if gain else '(kunlik limit tugadi)'}" if idx == right
           else f"❌ Noto'g'ri. To'g'ri javob: <b>{esc(opts[right])}</b>")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➡️ Keyingisi", callback_data="qz:next")]])
    await c.message.edit_text(f"🧠 {esc(q)}\n\n{res}", reply_markup=kb)
    await c.answer()


# ───── ADMIN PANEL ─────
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def admin_kb():
    b = InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [b(text="📊 Statistika", callback_data="ad:stats"), b(text="👥 Foydalanuvchilar", callback_data="ad:users")],
        [b(text="📢 E'lon yuborish", callback_data="ad:bc"), b(text="🚫 Ban / Unban", callback_data="ad:ban")],
        [b(text="💾 Bazani yuklab olish", callback_data="ad:db")]])


@r.message(F.text == B_ADM)
@r.message(Command("admin"))
async def admin_panel(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer("🛠 <b>Admin panel</b>", reply_markup=admin_kb())


@r.callback_query(F.data.startswith("ad:"))
async def admin_cb(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    act = c.data[3:]
    if act == "stats":
        s = await stats_data()
        await c.message.answer(
            f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: <b>{s['users']}</b>\n🆕 Bugun yangi: {s['new_today']}\n"
            f"🟢 Bugun faol: {s['active_today']}\n🚫 Banlangan: {s['banned']}\n🧾 Yozuvlar: {s['records']}\n"
            f"💸 Jami xarajat: {money(s['total_exp'])}")
    elif act == "users":
        us = await users_data(15)
        lines = [f"{'🚫' if u['banned'] else '•'} <code>{u['id']}</code> {esc(u['name'])} — 🪙{u['coins']} 💸{money(u['spent'])}" for u in us]
        await c.message.answer("👥 <b>So'nggi faol foydalanuvchilar</b>\n\n" + "\n".join(lines))
    elif act == "bc":
        await state.set_state(S.bc)
        await c.message.answer("📢 Barcha foydalanuvchilarga yuboriladigan matnni yozing (bekor qilish: /cancel):")
    elif act == "ban":
        await state.set_state(S.ban)
        await c.message.answer("🚫 Ban/Unban qilinadigan foydalanuvchi ID sini yozing (bekor qilish: /cancel):")
    elif act == "db":
        await db.commit()
        await c.message.answer_document(FSInputFile(DB_PATH, filename="hamyonhub-backup.db"), caption="💾 Baza nusxasi")
    await c.answer()


@r.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi.", reply_markup=menu(m.from_user.id))


@r.message(S.bc, F.text)
async def bc_text(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.update_data(text=m.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Yuborish", callback_data="bc:yes"),
                                                InlineKeyboardButton(text="❌ Bekor", callback_data="bc:no")]])
    await m.answer("Quyidagi matn hammaga yuboriladi:\n\n" + m.text, reply_markup=kb, parse_mode=None)


@r.callback_query(F.data.startswith("bc:"))
async def bc_confirm(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    data = await state.get_data()
    await state.clear()
    if c.data == "bc:yes" and data.get("text"):
        await c.message.edit_text("📢 Yuborilmoqda...")
        asyncio.create_task(do_broadcast(data["text"], c.from_user.id))
    else:
        await c.message.edit_text("Bekor qilindi.")
    await c.answer()


@r.message(S.ban, F.text)
async def ban_id(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    if not m.text.strip().isdigit():
        return await m.answer("ID raqam bo'lishi kerak.")
    uid = int(m.text.strip())
    if uid in ADMIN_IDS:
        return await m.answer("Adminni banlab bo'lmaydi.")
    res = await toggle_ban(uid)
    await m.answer("Foydalanuvchi topilmadi." if res is None else ("🚫 Banlandi." if res else "✅ Ban olib tashlandi."))


# ───── Oddiy matn = xarajat (eng oxirida!) ─────
@r.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def on_text(m: Message):
    await ensure_user(m.from_user.id, m.from_user.full_name)
    items = await parse_items(m.text, voice=False)
    if not items:
        return await m.answer("Summani topa olmadim 🤔\n\n" + HELP)
    await reply_saved(m, m.from_user.id, items)


# ───────────────────────── Mini App API ─────────────────────────
def check_init(init_data: str):
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        h = data.pop("hash", None)
        s = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, s.encode(), hashlib.sha256).hexdigest()
        if not h or not hmac.compare_digest(calc, h):
            return None
        if time.time() - int(data.get("auth_date", 0)) > 86400:
            return None
        return json.loads(data["user"])
    except Exception:
        return None


def herr(cls, msg):
    return cls(text=json.dumps({"error": msg}), content_type="application/json")


async def need_user(req: web.Request):
    u = check_init(req.headers.get("X-Init-Data", ""))
    if not u:
        raise herr(web.HTTPUnauthorized, "auth")
    name = (u.get("first_name", "") + " " + u.get("last_name", "")).strip()
    row, _ = await ensure_user(u["id"], name)
    if row["banned"] and u["id"] not in ADMIN_IDS:
        raise herr(web.HTTPForbidden, "Siz bloklangansiz")
    return u["id"]


async def need_admin(req: web.Request):
    uid = await need_user(req)
    if uid not in ADMIN_IDS:
        raise herr(web.HTTPForbidden, "Faqat admin")
    return uid


def jerr(msg: str, status=400):
    return web.json_response({"error": msg}, status=status)


async def api_me(req):
    uid = await need_user(req)
    u = await get_user(uid)
    exp, inc, _ = await summary(uid, "month")
    return web.json_response({
        "id": uid, "name": u["name"], "coins": u["coins"], "streak": u["streak"], "city": u["city"],
        "bonus_ready": u["last_bonus"] != today(), "is_admin": uid in ADMIN_IDS, "cats": CATS,
        "month_exp": exp, "month_inc": inc, "limit": u["limit_m"], "voice": bool(GROQ_KEY)})


async def api_bonus(req):
    uid = await need_user(req)
    ok, gain, streak, coins = await do_bonus(uid)
    return web.json_response({"ok": ok, "gain": gain, "streak": streak, "coins": coins})


async def exp_payload(uid: int, p: str):
    exp, inc, by = await summary(uid, p)
    cur = await db.execute("SELECT id,amount,kind,cat,note,ts FROM expenses WHERE uid=? AND ts>=? ORDER BY ts DESC, id DESC LIMIT 100",
                           (uid, period_start(p)))
    return {"exp": exp, "inc": inc, "by": by, "items": [dict(x) for x in await cur.fetchall()]}


async def api_exp(req):
    uid = await need_user(req)
    if req.method == "POST":
        b = await req.json()
        try:
            amount = float(b.get("amount"))
        except Exception:
            return jerr("Summa noto'g'ri")
        kind = "inc" if b.get("kind") == "inc" else "exp"
        cat = b.get("category") if b.get("category") in CATS else "other"
        if kind == "inc":
            cat = "income"
        elif cat == "income":
            cat = "other"
        if not (0 < amount <= 10**12):
            return jerr("Summa noto'g'ri")
        await db.execute("INSERT INTO expenses(uid,amount,kind,cat,note,ts) VALUES(?,?,?,?,?,?)",
                         (uid, amount, kind, cat, str(b.get("note", ""))[:60], int(time.time())))
        await db.commit()
    p = req.query.get("p", "month")
    return web.json_response(await exp_payload(uid, p if p in ("day", "week", "month", "all") else "month"))


async def api_exp_delete(req):
    uid = await need_user(req)
    await db.execute("DELETE FROM expenses WHERE id=? AND uid=?", (int(req.match_info["id"]), uid))
    await db.commit()
    return web.json_response({"ok": True})


async def api_limit(req):
    uid = await need_user(req)
    b = await req.json()
    try:
        v = max(0, int(float(b.get("limit", 0))))
    except Exception:
        return jerr("Noto'g'ri qiymat")
    await db.execute("UPDATE users SET limit_m=? WHERE id=?", (v, uid))
    await db.commit()
    return web.json_response({"ok": True, "limit": v})


async def api_todos(req):
    uid = await need_user(req)
    if req.method == "POST":
        text = str((await req.json()).get("text", "")).strip()[:200]
        if text:
            await db.execute("INSERT INTO todos(uid,text) VALUES(?,?)", (uid, text))
            await db.commit()
    cur = await db.execute("SELECT id,text,done FROM todos WHERE uid=? ORDER BY done, id DESC LIMIT 200", (uid,))
    return web.json_response([dict(x) for x in await cur.fetchall()])


async def api_todo_toggle(req):
    uid = await need_user(req)
    await db.execute("UPDATE todos SET done=1-done WHERE id=? AND uid=?", (int(req.match_info["id"]), uid))
    await db.commit()
    return web.json_response({"ok": True})


async def api_todo_delete(req):
    uid = await need_user(req)
    await db.execute("DELETE FROM todos WHERE id=? AND uid=?", (int(req.match_info["id"]), uid))
    await db.commit()
    return web.json_response({"ok": True})


async def resolve_city(req, uid):
    city = req.query.get("city", "").strip()
    if city:
        if req.query.get("save") == "1":
            g = await geocode(city)
            if g:
                await db.execute("UPDATE users SET city=? WHERE id=?", (g[2], uid))
                await db.commit()
        return city
    return await city_of(uid)


async def api_weather(req):
    uid = await need_user(req)
    city = await resolve_city(req, uid)
    if not city:
        return jerr("Shahar kiriting")
    w = await get_weather(city)
    return web.json_response(w) if w else jerr("Shahar topilmadi", 404)


async def api_prayer(req):
    uid = await need_user(req)
    city = await resolve_city(req, uid)
    if not city:
        return jerr("Shahar kiriting")
    p = await get_prayer(city)
    return web.json_response(p) if p else jerr("Shahar topilmadi", 404)


async def api_rates(req):
    await need_user(req)
    return web.json_response(await get_rates())


async def api_quiz(req):
    uid = await need_user(req)
    if req.method == "GET":
        qid = random.randrange(len(QUIZ))
        return web.json_response({"id": qid, "q": QUIZ[qid][0], "options": QUIZ[qid][1]})
    b = await req.json()
    qid, a = int(b["id"]), int(b["a"])
    right = QUIZ[qid][2]
    gain = await award_quiz(uid, a == right)
    return web.json_response({"right": right, "correct": a == right, "gain": gain})


async def api_top(req):
    await need_user(req)
    cur = await db.execute("SELECT name,coins FROM users WHERE banned=0 ORDER BY coins DESC LIMIT 20")
    return web.json_response([dict(x) for x in await cur.fetchall()])


async def api_admin_stats(req):
    await need_admin(req)
    return web.json_response(await stats_data())


async def api_admin_users(req):
    await need_admin(req)
    return web.json_response(await users_data(30))


async def api_admin_ban(req):
    await need_admin(req)
    uid = int((await req.json())["id"])
    if uid in ADMIN_IDS:
        return jerr("Adminni banlab bo'lmaydi")
    res = await toggle_ban(uid)
    return web.json_response({"banned": res}) if res is not None else jerr("Topilmadi", 404)


async def api_admin_bc(req):
    admin = await need_admin(req)
    text = str((await req.json()).get("text", "")).strip()[:3500]
    if not text:
        return jerr("Matn bo'sh")
    asyncio.create_task(do_broadcast(text, admin))
    return web.json_response({"ok": True})


async def index(req):
    return web.Response(text=INDEX_HTML, content_type="text/html", headers={"Cache-Control": "no-store"})


async def health(req):
    return web.Response(text="ok")


def make_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/", index), web.get("/health", health),
        web.get("/api/me", api_me), web.post("/api/bonus", api_bonus),
        web.get("/api/exp", api_exp), web.post("/api/exp", api_exp), web.delete("/api/exp/{id}", api_exp_delete),
        web.post("/api/limit", api_limit),
        web.get("/api/todos", api_todos), web.post("/api/todos", api_todos),
        web.post("/api/todos/{id}/toggle", api_todo_toggle), web.delete("/api/todos/{id}", api_todo_delete),
        web.get("/api/weather", api_weather), web.get("/api/prayer", api_prayer), web.get("/api/rates", api_rates),
        web.get("/api/quiz", api_quiz), web.post("/api/quiz", api_quiz), web.get("/api/top", api_top),
        web.get("/api/admin/stats", api_admin_stats), web.get("/api/admin/users", api_admin_users),
        web.post("/api/admin/ban", api_admin_ban), web.post("/api/admin/broadcast", api_admin_bc),
    ])
    return app


# ───────────────────────── Mini App (HTML) ─────────────────────────
INDEX_HTML = r"""<!doctype html>
<html lang="uz"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Hamyon Hub</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:var(--tg-theme-bg-color,#fff);--tx:var(--tg-theme-text-color,#111);--hint:var(--tg-theme-hint-color,#8a8a8e);
--btn:var(--tg-theme-button-color,#2a7cf6);--btx:var(--tg-theme-button-text-color,#fff);--card:var(--tg-theme-secondary-bg-color,#f2f3f5);--ok:#2e9e5b;--bad:#d9453d}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.4 -apple-system,system-ui,Segoe UI,Roboto,sans-serif;padding:14px 14px 40px}
h2{margin:6px 0 14px;font-size:20px}
.card{background:var(--card);border-radius:16px;padding:14px;margin-bottom:12px}
.hero{background:linear-gradient(135deg,var(--btn),#7a5cf0);color:#fff;border-radius:20px;padding:18px;margin-bottom:12px}
.hero .big{font-size:30px;font-weight:700}.hero small{opacity:.85}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.tile{background:var(--card);border-radius:16px;padding:14px 8px;text-align:center;font-weight:600;border:0;color:var(--tx);font-size:15px}
.tile span{display:block;font-size:26px;margin-bottom:2px}.tile:active{transform:scale(.97)}
button.p{background:var(--btn);color:var(--btx);border:0;border-radius:12px;padding:12px 16px;font-size:15px;font-weight:600;width:100%}
button.p:disabled{opacity:.5}button.g{background:var(--card);color:var(--tx)}
input,select,textarea{width:100%;padding:12px;border-radius:12px;border:1px solid transparent;background:var(--card);color:var(--tx);font-size:16px;outline:none;font-family:inherit}
input:focus,textarea:focus{border-color:var(--btn)}
.row{display:flex;gap:8px;margin-bottom:10px}.row>*{flex:1}.row>button{flex:0 0 auto;width:auto}
.chips{display:flex;gap:6px;margin-bottom:12px}.chip{flex:1;padding:9px 4px;border-radius:10px;border:0;background:var(--card);color:var(--tx);font-size:14px}
.chip.on{background:var(--btn);color:var(--btx)}
.item{display:flex;align-items:center;gap:10px;background:var(--card);border-radius:12px;padding:12px;margin-bottom:8px}
.item .t{flex:1;word-break:break-word}.item.done .t{text-decoration:line-through;color:var(--hint)}
.item button{background:none;border:0;font-size:18px;color:var(--hint)}
.chk{width:22px;height:22px;border-radius:50%;border:2px solid var(--btn);flex:0 0 22px;display:flex;align-items:center;justify-content:center;font-size:13px;color:#fff}
.done .chk{background:var(--btn)}
.sp{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid rgba(128,128,128,.15)}.sp:last-child{border:0}
.opt{display:block;width:100%;text-align:left;background:var(--card);border:2px solid transparent;color:var(--tx);border-radius:12px;padding:14px;margin-bottom:8px;font-size:16px}
.opt.ok{border-color:var(--ok);background:rgba(46,158,91,.15)}.opt.no{border-color:var(--bad);background:rgba(217,69,61,.15)}
.hint{color:var(--hint);font-size:13px}.up{color:var(--ok)}.dn{color:var(--bad)}.hide{display:none}
.bar{margin-bottom:10px}.bl{display:flex;justify-content:space-between;font-size:14px;margin-bottom:3px}
.bt{height:8px;background:rgba(128,128,128,.2);border-radius:6px;overflow:hidden}.bt i{display:block;height:100%;background:var(--btn);border-radius:6px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#222;color:#fff;padding:10px 16px;border-radius:12px;opacity:0;transition:.25s;pointer-events:none;z-index:9;max-width:90%;text-align:center}
#toast.on{opacity:1}img.qr{width:100%;max-width:300px;display:block;margin:12px auto;border-radius:12px;background:#fff}
</style></head><body>
<div id="toast"></div>

<section id="home">
  <div class="hero">
    <small id="hi">Salom 👋</small>
    <div class="hint" style="color:#fff;opacity:.85;margin-top:6px">Shu oy xarajat</div>
    <div class="big"><span id="hExp">0</span> <small>so'm</small></div>
    <small>Daromad: <b id="hInc">0</b></small>
    <div id="hLim" class="hide" style="margin-top:10px"><div class="bt" style="background:rgba(255,255,255,.3)"><i id="hLimBar" style="background:#fff"></i></div><small id="hLimTx"></small></div>
    <div class="row" style="margin:12px 0 0"><button class="p" id="addNow" style="background:#fff;color:#4a3fd6">➕ Xarajat qo'shish</button></div>
    <small>🪙 <b id="coins">0</b> • 🔥 <b id="streak">0</b> kun</small>
    <button class="p" id="bonusBtn" style="margin-top:8px;background:rgba(255,255,255,.2)">🎁 Kunlik bonus</button>
  </div>
  <div class="grid">
    <button class="tile" data-go="exp"><span>💸</span>Xarajatlar</button>
    <button class="tile" data-go="todo"><span>📝</span>Vazifalar</button>
    <button class="tile" data-go="weather"><span>🌤</span>Ob-havo</button>
    <button class="tile" data-go="prayer"><span>🕌</span>Namoz vaqti</button>
    <button class="tile" data-go="rates"><span>💱</span>Valyuta</button>
    <button class="tile" data-go="quiz"><span>🧠</span>Viktorina</button>
    <button class="tile" data-go="qr"><span>🔳</span>QR kod</button>
    <button class="tile" data-go="top"><span>🏆</span>Reyting</button>
    <button class="tile" data-go="pomo"><span>🍅</span>Fokus taymer</button>
    <button class="tile hide" id="admTile" data-go="admin"><span>🛠</span>Admin</button>
  </div>
</section>

<section id="exp" class="hide">
  <h2>💸 Xarajatlar</h2>
  <div class="chips" id="chips">
    <button class="chip" data-p="day">Bugun</button><button class="chip" data-p="week">Hafta</button>
    <button class="chip on" data-p="month">Oy</button><button class="chip" data-p="all">Hammasi</button>
  </div>
  <div class="two">
    <div class="card" style="margin:0"><div class="hint">💸 Xarajat</div><b style="font-size:20px" class="dn" id="eExp">0</b></div>
    <div class="card" style="margin:0"><div class="hint">💵 Daromad</div><b style="font-size:20px" class="up" id="eInc">0</b></div>
  </div>
  <div class="card" id="eBars"></div>
  <div class="card">
    <b>➕ Yangi yozuv</b>
    <div class="row" style="margin-top:10px"><select id="fKind"><option value="exp">Xarajat</option><option value="inc">Daromad</option></select>
    <select id="fCat"></select></div>
    <div class="row"><input id="fAmt" type="number" inputmode="numeric" placeholder="Summa (so'm)"></div>
    <div class="row"><input id="fNote" placeholder="Izoh (ixtiyoriy)" maxlength="60"></div>
    <button class="p" id="fSave">Saqlash</button>
    <p class="hint" id="voiceHint">🎤 Tezroq usul: botga ovozli xabar yuboring — «taksi o'n besh ming so'm».</p>
  </div>
  <div class="card"><b>🎯 Oylik limit</b>
    <div class="row" style="margin-top:10px"><input id="limIn" type="number" inputmode="numeric" placeholder="0 = o'chirilgan"><button class="p" id="limSave">OK</button></div></div>
  <div id="eList"></div>
</section>

<section id="todo" class="hide"><h2>📝 Vazifalar</h2>
  <div class="row"><input id="todoIn" placeholder="Yangi vazifa..." maxlength="200"><button class="p" id="todoAdd">+</button></div><div id="todoList"></div></section>

<section id="weather" class="hide"><h2>🌤 Ob-havo</h2>
  <div class="row"><input id="wCity" placeholder="Shahar (lotin harflarida)"><button class="p" id="wGo">OK</button></div><div id="wOut"></div></section>

<section id="prayer" class="hide"><h2>🕌 Namoz vaqtlari</h2>
  <div class="row"><input id="pCity" placeholder="Shahar (lotin harflarida)"><button class="p" id="pGo">OK</button></div><div id="pOut"></div></section>

<section id="rates" class="hide"><h2>💱 Valyuta kurslari</h2>
  <div class="card" id="rOut">Yuklanmoqda...</div>
  <div class="card"><div class="row"><input id="cAmt" type="number" inputmode="decimal" placeholder="Summa" value="100"><select id="cCcy"></select></div><div id="cRes" class="hint"></div></div></section>

<section id="quiz" class="hide"><h2>🧠 Viktorina</h2>
  <div class="card"><b id="qQ">...</b></div><div id="qOpts"></div>
  <button class="p hide" id="qNext">➡️ Keyingisi</button><p class="hint">To'g'ri javob = +5 🪙 (kuniga 20 tagacha)</p></section>

<section id="qr" class="hide"><h2>🔳 QR kod</h2><input id="qrIn" placeholder="Matn yoki havola..."><img id="qrImg" class="qr hide" alt="QR"></section>

<section id="top" class="hide"><h2>🏆 Reyting</h2><div id="topList"></div></section>

<section id="pomo" class="hide"><h2>🍅 Fokus taymer</h2>
  <div class="card" style="text-align:center"><div id="pTime" style="font-size:56px;font-weight:700">25:00</div>
  <div class="row" style="margin-top:12px"><button class="p" id="pStart">Boshlash</button><button class="p g" id="pReset">Qayta</button></div>
  <div class="row"><button class="p g" data-m="25">25 daq</button><button class="p g" data-m="15">15 daq</button><button class="p g" data-m="5">5 daq</button></div></div></section>

<section id="admin" class="hide"><h2>🛠 Admin panel</h2>
  <div class="two" id="aStats"></div>
  <div class="card"><b>📢 E'lon yuborish</b>
    <textarea id="bcText" rows="3" placeholder="Hamma foydalanuvchilarga xabar..." style="margin:10px 0"></textarea>
    <button class="p" id="bcSend">Yuborish</button></div>
  <b>👥 Foydalanuvchilar</b><div id="aUsers" style="margin-top:10px"></div></section>

<script>
const tg=window.Telegram.WebApp;tg.ready();tg.expand();
const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>Math.round(n).toLocaleString('ru-RU');
async function api(path,opt={}){
  const r=await fetch('/api'+path,{...opt,headers:{'X-Init-Data':tg.initData,'Content-Type':'application/json'}});
  const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||'Xatolik');return j;}
function toast(t){const e=$('#toast');e.textContent=t;e.classList.add('on');setTimeout(()=>e.classList.remove('on'),2400);}
function haptic(t='light'){try{tg.HapticFeedback.impactOccurred(t)}catch(e){}}
const loaders={exp:loadExp,todo:loadTodos,weather:()=>loadWeather(),prayer:()=>loadPrayer(),rates:loadRates,quiz:loadQuiz,top:loadTop,admin:loadAdmin};
function go(id){
  document.querySelectorAll('section').forEach(s=>s.classList.toggle('hide',s.id!==id));
  if(id==='home'){tg.BackButton.hide();loadMe();}else tg.BackButton.show();
  if(loaders[id])loaders[id]().catch?.(e=>toast(e.message));
  window.scrollTo(0,0);}
tg.BackButton.onClick(()=>go('home'));
document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>{haptic();go(b.dataset.go)});
$('#addNow').onclick=()=>{go('exp');setTimeout(()=>$('#fAmt').focus(),150)};

let me={},cats={};
async function loadMe(){
  try{me=await api('/me');cats=me.cats;
    $('#hi').textContent='Salom, '+me.name+' 👋';
    $('#hExp').textContent=fmt(me.month_exp);$('#hInc').textContent=fmt(me.month_inc)+" so'm";
    $('#coins').textContent=me.coins;$('#streak').textContent=me.streak;
    $('#bonusBtn').disabled=!me.bonus_ready;
    $('#bonusBtn').textContent=me.bonus_ready?'🎁 Kunlik bonusni olish':'✅ Bugungi bonus olingan';
    $('#admTile').classList.toggle('hide',!me.is_admin);
    $('#voiceHint').classList.toggle('hide',false);
    if(me.limit>0){const p=Math.min(100,me.month_exp/me.limit*100);$('#hLim').classList.remove('hide');
      $('#hLimBar').style.width=p+'%';$('#hLimTx').textContent=`Limit: ${fmt(me.month_exp)} / ${fmt(me.limit)} (${Math.round(p)}%)`;}
    else $('#hLim').classList.add('hide');
    $('#fCat').innerHTML=Object.entries(cats).map(([k,v])=>`<option value="${k}">${v}</option>`).join('');
    $('#fCat').value='other';
  }catch(e){toast('Ulanishda xatolik')}}
$('#bonusBtn').onclick=async()=>{try{const r=await api('/bonus',{method:'POST'});haptic('medium');
  toast(r.ok?`+${r.gain} 🪙  (${r.streak} kun ketma-ket)`:'Bugun olingan');loadMe();}catch(e){toast(e.message)}};

// --- Xarajatlar ---
let per='month';
document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{per=c.dataset.p;
  document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===c));loadExp();});
$('#fKind').onchange=()=>{$('#fCat').value=$('#fKind').value==='inc'?'income':'other'};
async function loadExp(){
  if(!Object.keys(cats).length)await loadMe();
  const d=await api('/exp?p='+per);renderExp(d);$('#limIn').placeholder=me.limit?fmt(me.limit):"0 = o'chirilgan";}
function renderExp(d){
  $('#eExp').textContent=fmt(d.exp);$('#eInc').textContent=fmt(d.inc);
  const mx=Math.max(1,...Object.values(d.by));
  $('#eBars').innerHTML=Object.entries(d.by).sort((a,b)=>b[1]-a[1]).map(([c,v])=>
    `<div class="bar"><div class="bl"><span>${cats[c]||c}</span><b>${fmt(v)}</b></div><div class="bt"><i style="width:${v/mx*100}%"></i></div></div>`).join('')
    ||'<span class="hint">Hali ma\'lumot yo\'q</span>';
  $('#eList').innerHTML=d.items.map(x=>`<div class="item"><div class="t">${cats[x.cat]||x.cat}<br><span class="hint">${esc(x.note)} • ${new Date(x.ts*1000).toLocaleString('uz',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}</span></div>
    <b class="${x.kind==='inc'?'up':''}">${x.kind==='inc'?'+':'-'}${fmt(x.amount)}</b><button onclick="delExp(${x.id})">🗑</button></div>`).join('');}
async function delExp(id){await api('/exp/'+id,{method:'DELETE'});haptic();loadExp();}
$('#fSave').onclick=async()=>{
  const amount=parseFloat($('#fAmt').value);if(!(amount>0))return toast('Summani kiriting');
  try{const d=await api('/exp?p='+per,{method:'POST',body:JSON.stringify({amount,kind:$('#fKind').value,category:$('#fCat').value,note:$('#fNote').value})});
    $('#fAmt').value='';$('#fNote').value='';renderExp(d);haptic('medium');toast('✅ Saqlandi');loadMe();}catch(e){toast(e.message)}};
$('#limSave').onclick=async()=>{try{await api('/limit',{method:'POST',body:JSON.stringify({limit:$('#limIn').value||0})});toast('🎯 Limit saqlandi');$('#limIn').value='';await loadMe();loadExp();}catch(e){toast(e.message)}};

// --- Vazifalar ---
async function loadTodos(){renderTodos(await api('/todos'));}
function renderTodos(l){$('#todoList').innerHTML=l.length?l.map(t=>`<div class="item ${t.done?'done':''}"><div class="chk" onclick="toggleTodo(${t.id})">${t.done?'✓':''}</div><div class="t">${esc(t.text)}</div><button onclick="delTodo(${t.id})">🗑</button></div>`).join(''):'<p class="hint">Hozircha vazifa yo\'q.</p>';}
async function toggleTodo(id){await api(`/todos/${id}/toggle`,{method:'POST'});haptic();loadTodos();}
async function delTodo(id){await api(`/todos/${id}`,{method:'DELETE'});loadTodos();}
async function addTodo(){const v=$('#todoIn').value.trim();if(!v)return;renderTodos(await api('/todos',{method:'POST',body:JSON.stringify({text:v})}));$('#todoIn').value='';haptic();}
$('#todoAdd').onclick=addTodo;$('#todoIn').onkeydown=e=>{if(e.key==='Enter')addTodo()};

// --- Ob-havo / Namoz ---
async function loadWeather(city){$('#wOut').innerHTML='<p class="hint">Yuklanmoqda...</p>';
  try{const w=await api('/weather'+(city?`?city=${encodeURIComponent(city)}&save=1`:''));
    $('#wOut').innerHTML=`<div class="card" style="text-align:center"><div class="hint">📍 ${esc(w.city)}</div><div style="font-size:52px;font-weight:700">${w.temp}°</div><div>${esc(w.desc)}</div><div class="hint">His etilishi ${w.feels}° • ⬆️${w.max}° ⬇️${w.min}°</div><div class="hint">💧 ${w.hum}% • 💨 ${w.wind} km/s</div></div>`;
  }catch(e){$('#wOut').innerHTML=`<p class="hint">${esc(e.message)}</p>`}}
$('#wGo').onclick=()=>{const v=$('#wCity').value.trim();if(v)loadWeather(v)};
async function loadPrayer(city){$('#pOut').innerHTML='<p class="hint">Yuklanmoqda...</p>';
  try{const p=await api('/prayer'+(city?`?city=${encodeURIComponent(city)}&save=1`:''));
    $('#pOut').innerHTML=`<div class="card"><div class="hint" style="margin-bottom:6px">📍 ${esc(p.city)}</div>`+p.times.map(([n,t])=>`<div class="sp"><span>${esc(n)}</span><b>${esc(t)}</b></div>`).join('')+'</div>';
  }catch(e){$('#pOut').innerHTML=`<p class="hint">${esc(e.message)}</p>`}}
$('#pGo').onclick=()=>{const v=$('#pCity').value.trim();if(v)loadPrayer(v)};

// --- Valyuta ---
let rates=[];
async function loadRates(){try{rates=await api('/rates');
  $('#rOut').innerHTML=rates.map(x=>`<div class="sp"><span>1 ${x.ccy}</span><b>${fmt(x.rate)} so'm <span class="${x.diff>0?'up':'dn'}" style="font-size:12px">${x.diff>0?'▲':x.diff<0?'▼':''}</span></b></div>`).join('');
  $('#cCcy').innerHTML=rates.map(x=>`<option>${x.ccy}</option>`).join('');calc();}catch(e){$('#rOut').textContent="Kurslarni olib bo'lmadi"}}
function calc(){const a=parseFloat($('#cAmt').value)||0,x=rates.find(r=>r.ccy===$('#cCcy').value);if(!x)return;
  $('#cRes').innerHTML=`${fmt(a)} ${x.ccy} = <b>${fmt(a*x.rate)} so'm</b><br>${fmt(a)} so'm = <b>${(a/x.rate).toFixed(2)} ${x.ccy}</b>`;}
$('#cAmt').oninput=calc;$('#cCcy').onchange=calc;

// --- Viktorina ---
let curQ=null,locked=false;
async function loadQuiz(){locked=false;$('#qNext').classList.add('hide');curQ=await api('/quiz');$('#qQ').textContent=curQ.q;
  $('#qOpts').innerHTML=curQ.options.map((o,i)=>`<button class="opt" data-i="${i}">${esc(o)}</button>`).join('');
  document.querySelectorAll('.opt').forEach(b=>b.onclick=()=>answer(+b.dataset.i,b));}
async function answer(i,btn){if(locked)return;locked=true;
  const r=await api('/quiz',{method:'POST',body:JSON.stringify({id:curQ.id,a:i})});
  const o=document.querySelectorAll('.opt');o[r.right].classList.add('ok');if(!r.correct)btn.classList.add('no');
  haptic(r.correct?'medium':'heavy');toast(r.correct?(r.gain?"✅ To'g'ri! +5 🪙":"✅ To'g'ri! (limit tugadi)"):"❌ Noto'g'ri");$('#qNext').classList.remove('hide');}
$('#qNext').onclick=loadQuiz;

// --- QR ---
let qt;$('#qrIn').oninput=e=>{clearTimeout(qt);qt=setTimeout(()=>{const v=e.target.value.trim(),img=$('#qrImg');
  if(!v)return img.classList.add('hide');img.src='https://api.qrserver.com/v1/create-qr-code/?size=500x500&data='+encodeURIComponent(v);img.classList.remove('hide');},400)};

// --- Reyting ---
async function loadTop(){const l=await api('/top');const m=['🥇','🥈','🥉'];
  $('#topList').innerHTML=l.map((x,i)=>`<div class="item"><b>${m[i]||(i+1)+'.'}</b><div class="t">${esc(x.name)}</div><b>${x.coins} 🪙</b></div>`).join('');}

// --- Pomodoro ---
let pLeft=1500,pTimer=null,pTotal=1500;
function pShow(){$('#pTime').textContent=String(Math.floor(pLeft/60)).padStart(2,'0')+':'+String(pLeft%60).padStart(2,'0');}
$('#pStart').onclick=()=>{if(pTimer){clearInterval(pTimer);pTimer=null;$('#pStart').textContent='Davom etish';return;}
  $('#pStart').textContent='Pauza';pTimer=setInterval(()=>{pLeft--;pShow();
    if(pLeft<=0){clearInterval(pTimer);pTimer=null;$('#pStart').textContent='Boshlash';haptic('heavy');tg.showAlert('⏰ Vaqt tugadi! Dam oling.');pLeft=pTotal;pShow();}},1000);};
$('#pReset').onclick=()=>{clearInterval(pTimer);pTimer=null;pLeft=pTotal;pShow();$('#pStart').textContent='Boshlash';};
document.querySelectorAll('[data-m]').forEach(b=>b.onclick=()=>{pTotal=pLeft=+b.dataset.m*60;clearInterval(pTimer);pTimer=null;$('#pStart').textContent='Boshlash';pShow();});

// --- Admin ---
async function loadAdmin(){
  const s=await api('/admin/stats');
  $('#aStats').innerHTML=[['👥 Foydalanuvchi',s.users],['🆕 Bugun yangi',s.new_today],['🟢 Bugun faol',s.active_today],['🚫 Banlangan',s.banned],['🧾 Yozuvlar',s.records],['💸 Jami xarajat',fmt(s.total_exp)]]
    .map(([a,b])=>`<div class="card" style="margin:0"><div class="hint">${a}</div><b style="font-size:19px">${b}</b></div>`).join('');
  const u=await api('/admin/users');
  $('#aUsers').innerHTML=u.map(x=>`<div class="item"><div class="t">${esc(x.name)}<br><span class="hint">${x.id} • 🪙${x.coins} • 💸${fmt(x.spent)}</span></div><button onclick="banUser(${x.id})">${x.banned?'✅':'🚫'}</button></div>`).join('');}
async function banUser(id){try{const r=await api('/admin/ban',{method:'POST',body:JSON.stringify({id})});toast(r.banned?'🚫 Banlandi':'✅ Ban olindi');loadAdmin();}catch(e){toast(e.message)}}
$('#bcSend').onclick=()=>{const t=$('#bcText').value.trim();if(!t)return;
  tg.showConfirm("Hammaga yuborilsinmi?",async ok=>{if(!ok)return;try{await api('/admin/broadcast',{method:'POST',body:JSON.stringify({text:t})});$('#bcText').value='';toast('📢 Yuborilmoqda...');}catch(e){toast(e.message)}});};

loadMe();
</script></body></html>
"""


# ───────────────────────── Ishga tushirish ─────────────────────────
async def main():
    global http, BOT
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN kiritilmagan!")
    await init_db()
    http = aiohttp.ClientSession()
    BOT = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(Guard())
    dp.include_router(r)

    runner = web.AppRunner(make_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Web server: port %s | WEBAPP_URL=%s | ovoz=%s", PORT, WEBAPP_URL or "yo'q", bool(GROQ_KEY))

    if WEBAPP_URL:
        try:
            await BOT.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Ilova", web_app=WebAppInfo(url=WEBAPP_URL)))
        except Exception:
            log.exception("menu button")

    asyncio.create_task(reminder_loop(BOT))
    await BOT.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(BOT)


if __name__ == "__main__":
    asyncio.run(main())
