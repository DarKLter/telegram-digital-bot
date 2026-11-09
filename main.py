import os
import json
import logging
import asyncio
import re
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, List, Set
from flask import Flask, request, jsonify

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    ChatMember,
    ChatMemberUpdated,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError, RetryAfter

# ================= CONFIGURATION & CONSTANTS =================

DATA_DIR = os.getenv("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_FILE = os.getenv("CONFIG_FILE", os.path.join(DATA_DIR, "bot_config.json"))
STATE_FILE = os.getenv("STATE_FILE", os.path.join(DATA_DIR, "vault_state.json"))
DB_FILE = os.getenv("DB_FILE", os.path.join(DATA_DIR, "vault_database.db"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("DigitalVaultBot")

# Primary Admin Telegram ID from environment
ENV_ADMIN_ID = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_ID")
PRIMARY_ADMIN_TELEGRAM_ID = (
    int(ENV_ADMIN_ID) if ENV_ADMIN_ID and ENV_ADMIN_ID.isdigit() else 5331007152
)

# Master Bot Owner Telegram IDs (Super Admins)
SUPER_ADMIN_IDS: List[int] = [
    int(x.strip())
    for x in os.getenv("SUPER_ADMIN_IDS", str(PRIMARY_ADMIN_TELEGRAM_ID)).split(",")
    if x.strip().isdigit()
]
if PRIMARY_ADMIN_TELEGRAM_ID not in SUPER_ADMIN_IDS:
    SUPER_ADMIN_IDS.insert(0, PRIMARY_ADMIN_TELEGRAM_ID)

# Default Tenant Configuration Template
DEFAULT_TENANT_CONFIG: Dict[str, Any] = {
    "main_hub_id": 0,
    "admin_proof_group_id": 0,
    "admin_telegram_id": PRIMARY_ADMIN_TELEGRAM_ID,
    "proof_archive_id": 0,
    "index_vault_ids": [],
    "shop_name": "Digital Vault Store",
    "primary_owner_id": PRIMARY_ADMIN_TELEGRAM_ID,
    "co_owners": [],
    "user_id_labels": {},
    "indexing_settings": {
        "auto_index": True,
        "custom_label": "Lesson",
        "forward_to_hub": True,
        "history_scan_depth": 2000,
    },
    "payments": {
        "pay_gcash": "📱 **GCash Payment**\nNumber: `09171234567`\nAccount Name: Admin",
        "pay_maya": "📱 **Maya Payment**\nNumber: `09171234567`\nAccount Name: Admin",
        "pay_bank": "🏦 **MariBank / Bank Transfer**\nAccount Number: `1234-5678-9012`\nBank: MariBank\nAccount Name: Admin",
    },
    "messages": {
        "welcome": "👋 **Welcome to {shop_name}, {name}!**\n\nGet access to our complete library of courses, ebooks, software, and files.\n\nPlease select your preferred payment method below:",
        "help": (
            "📖 **MEMBER GUIDE: HOW TO BUY & USE THE DIGITAL VAULT**\n\n"
            "Welcome **{name}** to **{shop_name}**!\n\n"
            "ℹ️ **ACCESS POLICY & RULES:**\n"
            "• **Members:** You are permitted to use `/start` (to checkout) and `/help` (for instructions).\n"
            "• **Admins & Owners:** Store & hub controls (`/setup`, `/admin`, `/sethub`, `/setindex`, `/reload`, etc.) are locked to verified Administrators and can ONLY be run inside Bot Private DM or your configured SetAdmin group.\n\n"
            "─── 🛒 **STEP-BY-STEP PURCHASING GUIDE** ───\n"
            "1️⃣ **Start Checkout:** Type `/start` in private DM.\n"
            "2️⃣ **Select Payment Method:** Choose GCash, Maya, Bank Transfer, or Crypto.\n"
            "3️⃣ **Make Transfer:** Send exact amount using provided account details.\n"
            "4️⃣ **Submit Receipt:** Send photo/screenshot or transaction reference number directly here.\n"
            "5️⃣ **Receive Single-Use Link:** Once verified by admin, you will receive a single-use invite link to the Main Directory Hub!\n\n"
            "─── 🤖 **WANT TO USE THIS BOT IN YOUR OWN GROUP/CHANNEL?** ───\n"
            "• Bot deployment for your own group/channel is a **paid feature**.\n"
            "• Type `/start` and select '🤖 Buy Bot License' to purchase full access!\n\n"
            "─── 📚 **SEARCHING MATERIALS** ───\n"
            "• Search indexed files anytime using `/search <keyword>`.\n\n"
            "─── ⚙️ **STORE OWNERS & MANAGERS** ───\n"
            "• Type `/setup` in private DM to open the Setup Wizard.\n"
            "• Type `/admin` in private DM or in SetAdmin group to open Admin Control Desk."
        ),
        "receipt": "⌛ **Receipt Submitted!**\n\nThank you {name}. Your payment submission has been forwarded to our admin team for verification. Once approved, you will automatically receive your single-use invite link here!",
        "message_receipt": "⌛ **Payment Details Received!**\n\nThank you {name}. Your transaction message has been forwarded to our admin team for verification. Once approved, you will automatically receive your invite link here!",
    },
    "moderation": {
        "enabled": True,
        "bad_words": ["scam", "hack", "free money", "gambling", "crypto free"],
        "block_links": True,
        "whitelisted_domains": ["t.me", "telegram.org", "drive.google.com"],
    },
    "security": {
        "phishing_keywords": [
            "bot token",
            "session file",
            "otp code",
            "telegram login",
            "stealer",
            "token grabber",
        ],
        "suspicious_extensions": [".exe", ".scr", ".bat", ".cmd", ".vbs", ".apk"],
    },
}

DEFAULT_GLOBAL_CONFIG: Dict[str, Any] = {
    "bot_token": os.getenv("TELEGRAM_TOKEN", "9118312201:AAFvZ6-cRZZCZqtbSz9dXTChxKYIKkTt9e8"),
    "primary_admin_id": PRIMARY_ADMIN_TELEGRAM_ID,
    "primary_store_owner_id": PRIMARY_ADMIN_TELEGRAM_ID,
    "super_admin_ids": SUPER_ADMIN_IDS,
    "licensed_tenants": [PRIMARY_ADMIN_TELEGRAM_ID] + [s for s in SUPER_ADMIN_IDS if s != PRIMARY_ADMIN_TELEGRAM_ID],
    "bot_license_pricing": "₱500 (or $10 USD) Lifetime Commercial Access",
    "bot_license_payments": {
        "pay_gcash": "📱 **GCash Payment (Bot License)**\nNumber: `09171234567`\nAccount Name: Real Bot Owner\nAmount: ₱500",
        "pay_maya": "📱 **Maya Payment (Bot License)**\nNumber: `09171234567`\nAccount Name: Real Bot Owner\nAmount: ₱500",
        "pay_bank": "🏦 **Bank Transfer (Bot License)**\nAccount Number: `1234-5678-9012`\nBank: MariBank\nAccount Name: Real Bot Owner\nAmount: ₱500",
        "pay_crypto": "💰 **USDT (TRC-20) Payment (Bot License)**\nAddress: `TXYZ1234567890ABCDEF`\nAmount: $10.00 USDT",
    },
    "ai_engine": {
        "enabled": True,
        "provider": "gemini",
        "api_key": os.getenv("GEMINI_API_KEY", ""),
        "model": "gemini-2.5-flash",
        "base_url": "",
        "temperature": 0.7,
        "max_tokens": 1000,
        "system_instruction": (
            "You are the brilliant, charming, and helpful female digital assistant and AI brain of this digital vault and store. "
            "You speak politely with natural wit, warmth, and intelligence. You have comprehensive knowledge of the course modules, "
            "ebooks, software files, and educational videos available in our vaults. You actively help users search for resources "
            "using /search <keyword>, explain store payments (GCash, Maya, Bank Transfer, USDT), and explain how customers can purchase "
            "a commercial Bot License to deploy this bot to their own channels and groups. If anyone greets, mentions, or talks to you, "
            "reply helpfully and engagingly."
        ),
    },
    "settings": {
        "allow_admin_member_cmds": False,
    },
    "tenants": {},
    "chat_tenant_map": {},
    "cloned_bots": {},
}

NOTIFIED_ROOMS_CACHE: Set[int] = set()
ACTIVE_INDEXING_TASKS: Dict[int, Dict[str, Any]] = {}

# In-memory logging ring-buffer for /showlog and /logs
SYSTEM_LOGS: List[Dict[str, Any]] = []

def log_system_event(
    event_type: str,
    message: str,
    chat_id: Optional[int] = None,
    user_id: Optional[int] = None,
    status: str = "INFO",
):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = {
        "timestamp": timestamp,
        "type": event_type,
        "message": message,
        "chat_id": chat_id,
        "user_id": user_id,
        "status": status,
    }
    SYSTEM_LOGS.append(entry)
    if len(SYSTEM_LOGS) > 250:
        SYSTEM_LOGS.pop(0)
    logger.info(f"[{event_type}] ({status}) {message} [Chat: {chat_id}, User: {user_id}]")

# ================= DATABASE INITIALIZATION & RAPID SEARCH =================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS indexed_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_unique_id TEXT UNIQUE,
            file_id TEXT NOT NULL,
            file_name TEXT,
            file_type TEXT,
            mime_type TEXT,
            file_size INTEGER,
            caption TEXT,
            category_name TEXT,
            module_title TEXT,
            chat_id INTEGER,
            chat_title TEXT,
            message_id INTEGER,
            thread_id INTEGER,
            direct_link TEXT,
            owner_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_files_search 
        ON indexed_files(file_name, caption, module_title, file_type)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_msg 
        ON indexed_files(chat_id, message_id)
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_checkpoints (
            chat_id INTEGER PRIMARY KEY,
            last_scanned_id INTEGER,
            highest_id INTEGER,
            total_indexed INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS registered_users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            username TEXT,
            phone_number TEXT,
            email TEXT,
            is_licensed INTEGER DEFAULT 0,
            license_purchased_at TIMESTAMP,
            notified_owner INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()

# ================= USER REGISTRATION & PROFILE DATABASE HELPERS =================

def db_get_user(user_id: int) -> Optional[Dict[str, Any]]:
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM registered_users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}")
        return None

def db_save_or_update_user(
    user_id: int,
    full_name: str,
    username: Optional[str] = None,
    phone_number: Optional[str] = None,
    email: Optional[str] = None,
    is_licensed: Optional[int] = None,
) -> Tuple[Dict[str, Any], bool]:
    """
    Saves or updates user profile in database.
    Returns (user_dict, is_new_account)
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM registered_users WHERE user_id=?", (user_id,))
        existing = cursor.fetchone()

        is_new = existing is None

        if is_new:
            licensed_val = is_licensed if is_licensed is not None else (1 if user_id in SUPER_ADMIN_IDS else 0)
            cursor.execute(
                """
                INSERT INTO registered_users (user_id, full_name, username, phone_number, email, is_licensed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (user_id, full_name, username, phone_number, email, licensed_val),
            )
        else:
            current = dict(existing)
            new_name = full_name or current.get("full_name")
            new_uname = username if username is not None else current.get("username")
            new_phone = phone_number if phone_number is not None else current.get("phone_number")
            new_email = email if email is not None else current.get("email")
            new_licensed = is_licensed if is_licensed is not None else current.get("is_licensed", 0)

            cursor.execute(
                """
                UPDATE registered_users
                SET full_name=?, username=?, phone_number=?, email=?, is_licensed=?, updated_at=CURRENT_TIMESTAMP
                WHERE user_id=?
                """,
                (new_name, new_uname, new_phone, new_email, new_licensed, user_id),
            )

        conn.commit()
        cursor.execute("SELECT * FROM registered_users WHERE user_id=?", (user_id,))
        updated_row = dict(cursor.fetchone())
        conn.close()
        return updated_row, is_new
    except Exception as e:
        logger.error(f"Error saving user {user_id}: {e}")
        return {
            "user_id": user_id,
            "full_name": full_name,
            "username": username,
            "phone_number": phone_number,
            "email": email,
            "is_licensed": 0,
        }, False

def db_set_user_licensed(user_id: int, licensed: bool = True):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        val = 1 if licensed else 0
        cursor.execute(
            """
            UPDATE registered_users
            SET is_licensed=?, license_purchased_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE user_id=?
            """,
            (val, user_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error setting license for {user_id}: {e}")

def db_get_user_by_id_or_username(identifier: str) -> Optional[Dict[str, Any]]:
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        clean = identifier.strip().lstrip("@")
        if clean.isdigit():
            cursor.execute("SELECT * FROM registered_users WHERE user_id=?", (int(clean),))
        else:
            cursor.execute("SELECT * FROM registered_users WHERE LOWER(username)=LOWER(?)", (clean,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error querying user {identifier}: {e}")
        return None

def db_get_all_users(limit: int = 30) -> List[Dict[str, Any]]:
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM registered_users ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching all users: {e}")
        return []

def db_count_users() -> Dict[str, int]:
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM registered_users")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM registered_users WHERE phone_number IS NOT NULL AND phone_number != ''")
        with_phone = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM registered_users WHERE email IS NOT NULL AND email != ''")
        with_email = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM registered_users WHERE is_licensed = 1")
        licensed = cursor.fetchone()[0]
        conn.close()
        return {"total": total, "with_phone": with_phone, "with_email": with_email, "licensed": licensed}
    except Exception as e:
        logger.error(f"Error counting users: {e}")
        return {"total": 0, "with_phone": 0, "with_email": 0, "licensed": 0}

def db_get_files_for_chats(chat_ids: List[int], limit: int = 25) -> List[Dict[str, Any]]:
    if not chat_ids:
        return []
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in chat_ids)
        cursor.execute(
            f"SELECT * FROM indexed_files WHERE chat_id IN ({placeholders}) ORDER BY id DESC LIMIT ?",
            (*chat_ids, limit),
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching files for chats {chat_ids}: {e}")
        return []

def db_insert_file(record: Dict[str, Any]) -> bool:
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO indexed_files (
                file_unique_id, file_id, file_name, file_type, mime_type,
                file_size, caption, category_name, module_title, chat_id,
                chat_title, message_id, thread_id, direct_link, owner_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_unique_id) DO UPDATE SET
                file_name=excluded.file_name,
                caption=excluded.caption,
                direct_link=excluded.direct_link,
                module_title=excluded.module_title,
                file_size=excluded.file_size
            """,
            (
                record.get("file_unique_id"),
                record.get("file_id"),
                record.get("file_name"),
                record.get("file_type"),
                record.get("mime_type"),
                record.get("file_size", 0),
                record.get("caption"),
                record.get("category_name"),
                record.get("module_title"),
                record.get("chat_id"),
                record.get("chat_title"),
                record.get("message_id"),
                record.get("thread_id"),
                record.get("direct_link"),
                record.get("owner_id"),
            ),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error inserting into indexed_files: {e}")
        return False

def db_delete_chat_files(chat_id: int) -> int:
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM indexed_files WHERE chat_id=?", (chat_id,))
        count = cursor.rowcount
        conn.commit()
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Error deleting files for chat {chat_id}: {e}")
        return 0

def db_search_files(query_term: str, limit: int = 15) -> List[Dict[str, Any]]:
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        term = f"%{query_term.strip()}%"
        cursor.execute(
            """
            SELECT * FROM indexed_files
            WHERE file_name LIKE ? OR caption LIKE ? OR module_title LIKE ? OR category_name LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (term, term, term, term, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"Database search error: {e}")
        return []

def db_search_files_by_type(query_term: str = "", file_type: str = "", limit: int = 15) -> List[Dict[str, Any]]:
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        conditions = []
        params = []
        if file_type:
            conditions.append("file_type = ?")
            params.append(file_type)
        if query_term.strip():
            term = f"%{query_term.strip()}%"
            conditions.append("(file_name LIKE ? OR caption LIKE ? OR module_title LIKE ? OR category_name LIKE ?)")
            params.extend([term, term, term, term])
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM indexed_files {where_clause} ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, tuple(params))
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"Database search by type error: {e}")
        return []

def db_get_stats(chat_id: Optional[int] = None) -> Dict[str, int]:
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        if chat_id:
            cursor.execute("SELECT COUNT(*) FROM indexed_files WHERE chat_id=?", (chat_id,))
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM indexed_files WHERE chat_id=? AND file_type='video'", (chat_id,))
            videos = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM indexed_files WHERE chat_id=? AND file_type='ebook'", (chat_id,))
            ebooks = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM indexed_files WHERE chat_id=? AND file_type='app_software'", (chat_id,))
            software = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM indexed_files WHERE chat_id=? AND file_type='code_archive'", (chat_id,))
            code = cursor.fetchone()[0]
        else:
            cursor.execute("SELECT COUNT(*) FROM indexed_files")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM indexed_files WHERE file_type='video'")
            videos = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM indexed_files WHERE file_type='ebook'")
            ebooks = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM indexed_files WHERE file_type='app_software'")
            software = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM indexed_files WHERE file_type='code_archive'")
            code = cursor.fetchone()[0]
        conn.close()
        return {
            "total": total,
            "videos": videos,
            "ebooks": ebooks,
            "software": software,
            "code": code,
        }
    except Exception as e:
        logger.error(f"Database stats error: {e}")
        return {"total": 0, "videos": 0, "ebooks": 0, "software": 0, "code": 0}

def db_update_scan_checkpoint(chat_id: int, last_scanned_id: int, highest_id: int, total_indexed: int):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO scan_checkpoints (chat_id, last_scanned_id, highest_id, total_indexed, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                last_scanned_id=excluded.last_scanned_id,
                highest_id=excluded.highest_id,
                total_indexed=excluded.total_indexed,
                updated_at=CURRENT_TIMESTAMP
            """,
            (chat_id, last_scanned_id, highest_id, total_indexed),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error updating scan checkpoint for {chat_id}: {e}")

init_db()

# ================= PERSISTENT CONFIG & STATE HELPERS =================

def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                data.setdefault("tenants", {})
                data.setdefault("chat_tenant_map", {})
                data.setdefault("cloned_bots", {})
                data.setdefault("primary_admin_id", PRIMARY_ADMIN_TELEGRAM_ID)
                data.setdefault("primary_store_owner_id", PRIMARY_ADMIN_TELEGRAM_ID)
                data.setdefault("super_admin_ids", SUPER_ADMIN_IDS)
                data.setdefault("licensed_tenants", [PRIMARY_ADMIN_TELEGRAM_ID] + SUPER_ADMIN_IDS)
                data.setdefault("bot_license_pricing", DEFAULT_GLOBAL_CONFIG["bot_license_pricing"])
                data.setdefault("bot_license_payments", DEFAULT_GLOBAL_CONFIG["bot_license_payments"])
                data.setdefault("bot_token", DEFAULT_GLOBAL_CONFIG["bot_token"])
                data.setdefault("settings", DEFAULT_GLOBAL_CONFIG["settings"])
                data.setdefault("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
                for k, v in DEFAULT_GLOBAL_CONFIG["ai_engine"].items():
                    data["ai_engine"].setdefault(k, v)
                if not data["ai_engine"].get("api_key") and os.getenv("GEMINI_API_KEY"):
                    data["ai_engine"]["api_key"] = os.getenv("GEMINI_API_KEY")
                return data
        except Exception as e:
            logger.error(f"Error loading config file: {e}")
            return DEFAULT_GLOBAL_CONFIG
    return DEFAULT_GLOBAL_CONFIG

def save_config(config_data: Dict[str, Any]):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving config file: {e}")

def load_state() -> Dict[Any, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                raw = json.load(f)
                res = {}
                for k, v in raw.items():
                    try:
                        res[int(k)] = v
                    except ValueError:
                        res[k] = v
                return res
        except Exception as e:
            logger.error(f"Error loading state file: {e}")
            return {}
    return {}

def save_state(state_data: Dict[Any, Any]):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state_data, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving state file: {e}")

GLOBAL_CONFIG = load_config()
VAULT_STATE = load_state()
PENDING_PROMOTIONS: Dict[int, str] = {}

app = Flask(__name__)

# ================= HELPER FUNCTIONS =================

def normalize_title(title: str) -> str:
    return title.strip().lower()

def get_primary_admin_id() -> int:
    cfg_id = GLOBAL_CONFIG.get("primary_admin_id") or GLOBAL_CONFIG.get("primary_store_owner_id")
    if cfg_id and int(cfg_id) != 0:
        return int(cfg_id)
    if SUPER_ADMIN_IDS:
        return SUPER_ADMIN_IDS[0]
    return PRIMARY_ADMIN_TELEGRAM_ID

def is_super_admin(user_id: int) -> bool:
    return user_id in GLOBAL_CONFIG.get("super_admin_ids", SUPER_ADMIN_IDS) or user_id == get_primary_admin_id()

def is_user_licensed(user_id: int) -> bool:
    """
    Checks if a user has purchased a commercial Bot License to use the bot
    in their own group/channel. The real owner and super admins are always licensed.
    """
    if is_super_admin(user_id):
        return True
    licensed_list = GLOBAL_CONFIG.get("licensed_tenants", [])
    if user_id in licensed_list or str(user_id) in [str(x) for x in licensed_list]:
        return True
    u = db_get_user(user_id)
    if u and u.get("is_licensed"):
        return True
    return False

def get_tenant_config(owner_id: int) -> Dict[str, Any]:
    owner_str = str(owner_id)
    tenants = GLOBAL_CONFIG.setdefault("tenants", {})
    if owner_str not in tenants:
        new_tenant = json.loads(json.dumps(DEFAULT_TENANT_CONFIG))
        new_tenant["primary_owner_id"] = owner_id
        new_tenant["admin_telegram_id"] = get_primary_admin_id()
        tenants[owner_str] = new_tenant
        save_config(GLOBAL_CONFIG)
    tenant = tenants[owner_str]
    tenant.setdefault("index_vault_ids", [])
    tenant.setdefault("admin_proof_group_id", 0)
    tenant.setdefault("admin_telegram_id", get_primary_admin_id())
    tenant.setdefault("main_hub_id", 0)
    tenant.setdefault("proof_archive_id", 0)
    tenant.setdefault("user_id_labels", {})
    return tenant

def get_tenant_by_chat(chat_id: int) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    chat_str = str(chat_id)
    chat_map = GLOBAL_CONFIG.get("chat_tenant_map", {})
    if chat_str in chat_map:
        owner_id = chat_map[chat_str]
        return owner_id, get_tenant_config(owner_id)

    for owner_key, tenant in GLOBAL_CONFIG.get("tenants", {}).items():
        if chat_id in [
            tenant.get("main_hub_id"),
            tenant.get("admin_proof_group_id"),
            tenant.get("proof_archive_id"),
        ] or chat_id in tenant.get("index_vault_ids", []):
            return int(owner_key), tenant

    primary_id = get_primary_admin_id()
    return primary_id, get_tenant_config(primary_id)

def get_target_admin_destinations(owner_id: int, client_id: int) -> List[int]:
    """
    CRITICAL USER SPECIFICATION:
    Payment accept/reject are automatically sent to setadmin (admin_proof_group_id).
    If setadmin is NOT set, automatically send to the bot DM (the store owner / primary_owner_id).
    Client ID is NEVER included so buyer never receives approval controls!
    """
    destinations: List[int] = []
    tenant_cfg = get_tenant_config(owner_id)

    # Priority 1: If setadmin group is configured, send directly to setadmin!
    admin_group = tenant_cfg.get("admin_proof_group_id")
    if admin_group and int(admin_group) != 0 and int(admin_group) != client_id:
        destinations.append(int(admin_group))
        return destinations

    # Priority 2: If setadmin is NOT set, route to the bot DM (admin_telegram_id or primary_owner_id)
    admin_dm_id = tenant_cfg.get("admin_telegram_id")
    if admin_dm_id and int(admin_dm_id) != 0 and int(admin_dm_id) != client_id:
        destinations.append(int(admin_dm_id))

    store_owner = tenant_cfg.get("primary_owner_id", owner_id)
    if store_owner and int(store_owner) != 0 and int(store_owner) != client_id:
        if int(store_owner) not in destinations:
            destinations.append(int(store_owner))

    if not destinations:
        global_admin = get_primary_admin_id()
        if global_admin != client_id:
            destinations.append(global_admin)

    return destinations


async def is_admin_or_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    chat = update.effective_chat

    if not user:
        if chat and chat.type in ["channel", "group", "supergroup"]:
            return True
        return False

    user_id = user.id
    if is_super_admin(user_id):
        return True

    if chat:
        owner_id, tenant_cfg = get_tenant_by_chat(chat.id)
        if owner_id and (user_id == owner_id or user_id in tenant_cfg.get("co_owners", []) or user_id == tenant_cfg.get("admin_telegram_id")):
            return True

        if chat.type in ["group", "supergroup", "channel"]:
            try:
                member = await context.bot.get_chat_member(chat.id, user_id)
                if member.status in ["administrator", "creator"]:
                    return True
            except Exception as e:
                logger.debug(f"Chat member check note: {e}")

    owner_str = str(user_id)
    if owner_str in GLOBAL_CONFIG.get("tenants", {}):
        tenant = GLOBAL_CONFIG["tenants"][owner_str]
        if (
            tenant.get("main_hub_id")
            or tenant.get("admin_proof_group_id")
            or tenant.get("index_vault_ids")
        ):
            return True

    return False

# ================= REAL-TIME REAL OWNER NOTIFICATION ENGINE =================

async def notify_real_owner_new_user(
    bot,
    user_id: int,
    full_name: str,
    username: Optional[str] = None,
    phone_number: Optional[str] = None,
    email: Optional[str] = None,
    event_tag: str = "NEW_USER_STARTED",
):
    """
    Sends real-time notification to the real owner of the bot when someone
    or a new account starts chatting with the bot, capturing:
    - Profile Full Name
    - Telegram User ID & Username
    - Phone Number
    - Gmail / Email Address
    - Timestamp & Direct Profile Link
    """
    real_owner_id = get_primary_admin_id()
    username_str = f"@{username}" if username else "No username"
    phone_str = phone_number if phone_number else "⚠️ Pending Submission"
    email_str = email if email else "⚠️ Pending Submission"
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    profile_link = f"tg://user?id={user_id}"
    licensed_badge = "👑 LICENSED TENANT" if is_user_licensed(user_id) else "🔒 UNLICENSED (Store Member)"

    alert_text = (
        f"🔔 **[REAL BOT OWNER ALERT: {event_tag}]**\n\n"
        f"👤 **Profile Name:** {full_name}\n"
        f"🆔 **Telegram User ID:** `{user_id}`\n"
        f"💬 **Username:** {username_str}\n"
        f"📱 **Phone Number:** `{phone_str}`\n"
        f"📧 **Gmail / Email:** `{email_str}`\n"
        f"📅 **Timestamp:** `{timestamp}`\n"
        f"🛡️ **Bot Status:** {licensed_badge}\n\n"
        f"🔗 **Direct User Link:** [{full_name}]({profile_link})"
    )
    log_system_event(
        event_tag,
        f"User {user_id} ({full_name}) | Phone: {phone_str} | Gmail: {email_str}",
        user_id=user_id,
        status="ALERT",
    )

    # Route to Real Bot Owner and Super Admins
    destinations = [real_owner_id]
    for sa_id in SUPER_ADMIN_IDS:
        if sa_id not in destinations:
            destinations.append(sa_id)

    for dest_id in destinations:
        try:
            await safe_send_message(bot, dest_id, alert_text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed notifying real owner {dest_id}: {e}")

# ================= LOCATION SECURITY & EDIT PERMISSIONS =================

async def check_settings_permission_or_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command_name: str,
    allow_initial_setadmin: bool = False,
) -> bool:
    """
    STRICT SETTINGS LOCATION ENFORCEMENT & COMMERCIAL LICENSE CHECK:
    1. User must be a Licensed Tenant or Bot Owner (deploying bot is not free).
    2. Settings changes, edits, or updates of any group or channel are ONLY allowed:
       - Inside Bot Private DM
       - Inside the configured SetAdmin room (Admin Verification Group)
    """
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return False

    user_id = user.id

    # 1. Super Admin (Real Bot Owner) is NEVER blocked anywhere
    if is_super_admin(user_id):
        return True

    # 2. Informational / Read-only commands can be viewed by any admin/owner in any room or DM
    view_only_commands = ["admin", "help", "viewvault", "vaultstats", "aistatus", "config", "openlink", "search", "courses", "videos", "ebooks", "mybot"]
    if command_name.lower() in view_only_commands:
        if await is_admin_or_owner(update, context):
            return True

    # 3. Commercial License Verification (NOT FREE to use in own group/channel)
    if not is_user_licensed(user_id):
        price = GLOBAL_CONFIG.get("bot_license_pricing", DEFAULT_GLOBAL_CONFIG["bot_license_pricing"])
        lock_text = (
            "🔒 **BOT COMMERCIAL LICENSE REQUIRED!**\n\n"
            "Using this bot to manage your **own groups and channels is a paid feature**!\n"
            "Just like our digital store, running your automated indexing vault, receipt reviewer, "
            "and member hub requires an active Bot License.\n\n"
            f"💰 **License Price:** `{price}`\n\n"
            "✨ **FULL ACCESS INCLUDES:**\n"
            "• Auto-indexing all ebooks, videos, APKs & software\n"
            "• Branded store checkout with your GCash, Maya & Bank\n"
            "• Single-use hub invites & fraud detection\n"
            "• Full step-by-step setup guide and admin dashboard!\n"
            "• Autonomous Bot Cloning engine (@BotFather style)\n\n"
            "👉 _Click '💳 Buy Bot License' below to purchase and activate full access._"
        )
        keyboard = [
            [InlineKeyboardButton("💳 Buy Bot License for My Channels", callback_data="buybot_license_info")],
            [InlineKeyboardButton("🛒 Browse Digital Store", callback_data=f"mode_customer_{get_primary_admin_id()}")],
        ]
        await msg.reply_text(lock_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        log_system_event("UNLICENSED_ACCESS_ATTEMPT", f"User {user_id} tried /{command_name} without license", chat_id=chat.id, user_id=user_id, status="WARN")
        return False

    # 4. Administrator or Owner Status check
    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Administrator or Owner permission required.", parse_mode="Markdown")
        log_system_event("AUTH_DENIED", f"User {user_id} denied on /{command_name}", chat_id=chat.id, user_id=user_id, status="WARN")
        return False

    tenant_cfg = get_tenant_config(user_id)
    admin_group_id = tenant_cfg.get("admin_proof_group_id", 0)

    # Rule A: Bot Private DM is ALWAYS allowed
    if chat.type == "private":
        return True

    # Rule B: Inside the configured SetAdmin room (Admin Verification Group) is ALWAYS allowed
    if admin_group_id and int(chat.id) == int(admin_group_id):
        return True

    # Rule C: Allow /setadmin in any group so an admin can bind or update that group
    if allow_initial_setadmin:
        return True

    # Otherwise: Block settings changes in any other room!
    bot_info = await context.bot.get_me()
    admin_group_info = f"`{admin_group_id}`" if admin_group_id else "Not registered yet (run `/setadmin` here or in DM)"
    warning_text = (
        f"⛔ **SETTINGS EDIT RESTRICTED!**\n\n"
        f"For security, group/channel settings can **ONLY be edited or updated inside:**\n"
        f"1️⃣ **Bot Private DM** ([@{bot_info.username}](https://t.me/{bot_info.username}))\n"
        f"2️⃣ Your registered **Admin Verification Group (`/setadmin`)**\n\n"
        f"📍 **Current Room:** `{chat.title}` (`{chat.id}`)\n"
        f"🛡️ **SetAdmin Room:** {admin_group_info}\n\n"
        f"👉 _Please open private DM with @{bot_info.username} or run this inside your SetAdmin room._"
    )
    await msg.reply_text(warning_text, parse_mode="Markdown", disable_web_page_preview=True)
    log_system_event(
        "SETTINGS_LOCATION_BLOCKED",
        f"Blocked /{command_name} in non-SetAdmin chat {chat.id} ({chat.title})",
        chat_id=chat.id,
        user_id=user_id,
        status="SECURITY",
    )
    return False

# ================= MULTI-ROOM SETTINGS SYNC VERIFICATION =================

async def broadcast_settings_sync(
    bot,
    owner_id: int,
    notify_rooms: bool = True,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Tests and verifies settings update propagation across ALL connected groups and channels:
    - Main Directory Hub (main_hub_id)
    - Admin Verification Group (admin_proof_group_id)
    - Proof Archive (proof_archive_id)
    - All Index Content Vaults (index_vault_ids)
    """
    tenant_cfg = get_tenant_config(owner_id)
    shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")
    stats = db_get_stats()
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    rooms_to_check: List[Tuple[int, str]] = []

    hub_id = tenant_cfg.get("main_hub_id")
    if hub_id and int(hub_id) != 0:
        rooms_to_check.append((int(hub_id), "MAIN DIRECTORY HUB"))

    admin_id = tenant_cfg.get("admin_proof_group_id")
    if admin_id and int(admin_id) != 0:
        rooms_to_check.append((int(admin_id), "ADMIN VERIFICATION GROUP"))

    proof_id = tenant_cfg.get("proof_archive_id")
    if proof_id and int(proof_id) != 0:
        rooms_to_check.append((int(proof_id), "PROOF ARCHIVE"))

    for v_id in tenant_cfg.get("index_vault_ids", []):
        if v_id and int(v_id) != 0 and int(v_id) not in [r[0] for r in rooms_to_check]:
            rooms_to_check.append((int(v_id), "INDEX CONTENT VAULT"))

    results: List[Dict[str, Any]] = []

    if not rooms_to_check:
        log_system_event("SYNC_EMPTY", f"No rooms connected for owner {owner_id}", user_id=owner_id, status="WARN")
        return results, "ℹ️ No rooms or channels registered yet. Connect rooms via `/sethub`, `/setadmin`, `/setproof`, `/setindex`."

    for chat_id, role in rooms_to_check:
        room_title = f"Room {chat_id}"
        room_ok = False
        error_msg = ""
        try:
            chat_obj = await bot.get_chat(chat_id)
            room_title = chat_obj.title or room_title

            if notify_rooms:
                sync_msg = (
                    f"🔄 **[SYSTEM CONFIG SYNC UPDATE]**\n\n"
                    f"✅ **Settings Live & Synced in this room!**\n"
                    f"• 🏪 **Store:** {shop_name}\n"
                    f"• 🏷️ **Role:** {role}\n"
                    f"• 📁 **Indexed Files in DB:** `{stats['total']}`\n"
                    f"• 📅 **Timestamp:** `{timestamp}`\n"
                    f"• ⚡ **Status:** Active & Updating Normally"
                )
                res = await safe_send_message(bot, chat_id, sync_msg, parse_mode="Markdown")
                if res:
                    room_ok = True
                else:
                    room_ok = False
                    error_msg = "Send failed (check bot send permissions)"
            else:
                room_ok = True
        except Exception as e:
            room_ok = False
            error_msg = str(e)

        status_label = "✅ UPDATING & SYNCED" if room_ok else f"❌ FAILED: {error_msg}"
        results.append({
            "chat_id": chat_id,
            "title": room_title,
            "role": role,
            "ok": room_ok,
            "error": error_msg,
            "status_label": status_label,
        })
        log_system_event(
            "ROOM_SYNC",
            f"Sync to {role} ({room_title} | {chat_id}): {'SUCCESS' if room_ok else 'FAILED - ' + error_msg}",
            chat_id=chat_id,
            user_id=owner_id,
            status="SUCCESS" if room_ok else "ERROR",
        )

    summary_lines = [
        "📊 **MULTI-ROOM SETTINGS UPDATE & SYNC REPORT**",
        f"🏪 **Store:** `{shop_name}`",
        f"📅 **Synced At:** `{timestamp}`",
        "────────────────────────────────────────",
    ]
    for r in results:
        status_icon = "✅" if r["ok"] else "❌"
        summary_lines.append(f"{status_icon} **{r['role']}:** `{r['title']}` (`{r['chat_id']}`)\n   👉 {r['status_label']}")

    summary_lines.append("────────────────────────────────────────")
    synced_count = sum(1 for r in results if r["ok"])
    summary_lines.append(f"📈 **Total Status:** `{synced_count}/{len(results)}` rooms actively updating.")

    return results, "\n".join(summary_lines)

# ================= MESSAGE SAFE SEND HELPERS =================

async def safe_send_message(
    bot,
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = "Markdown",
    reply_markup: Optional[Any] = None,
    disable_web_page_preview: bool = True,
):
    try:
        return await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception as e:
        logger.warning(f"Markdown send failed to {chat_id} ({e}). Retrying plain text...")
        clean_text = (
            text.replace("**", "").replace("__", "").replace("`", "").replace("*", "").replace("_", "")
        )
        try:
            return await bot.send_message(
                chat_id=int(chat_id),
                text=clean_text,
                parse_mode=None,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        except Exception as err:
            logger.error(f"Critical failure sending message to {chat_id}: {err}")
            return None

async def safe_send_photo(
    bot,
    chat_id: int,
    photo: str,
    caption: str,
    reply_markup: Optional[Any] = None,
    parse_mode: Optional[str] = "Markdown",
):
    try:
        return await bot.send_photo(
            chat_id=int(chat_id),
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except Exception as e:
        logger.warning(f"Photo send failed to {chat_id} ({e}). Retrying plain text caption...")
        clean_caption = caption.replace("**", "").replace("`", "").replace("*", "").replace("_", "")
        try:
            return await bot.send_photo(
                chat_id=int(chat_id),
                photo=photo,
                caption=clean_caption,
                reply_markup=reply_markup,
                parse_mode=None,
            )
        except Exception as err:
            logger.error(f"Critical failure sending photo to {chat_id}: {err}")
            return None

# ================= EXTRACTOR FOR FILE & MEDIA METADATA =================

def extract_media_and_record(
    post_or_msg,
    chat_id: int,
    chat_title: str,
    chat_username: Optional[str],
    owner_id: int,
    main_hub_id: int = 0,
    prefix_label: str = "Lesson",
) -> Optional[Dict[str, Any]]:
    has_media = bool(
        post_or_msg.video
        or post_or_msg.document
        or post_or_msg.audio
        or post_or_msg.photo
        or post_or_msg.animation
    )
    extracted_text = (post_or_msg.text or post_or_msg.caption or "").strip()
    clean_chat_id = str(chat_id).replace("-100", "").lstrip("-")
    thread_id = getattr(post_or_msg, "message_thread_id", None) or 0

    if chat_id not in VAULT_STATE:
        VAULT_STATE[chat_id] = {
            "category_name": chat_title or f"Vault {chat_id}",
            "active_course": "General Library",
            "courses": {},
        }
    else:
        VAULT_STATE[chat_id]["category_name"] = chat_title

    # CASE 1: Pure Text Post (Module Header)
    if (post_or_msg.text and not has_media) or (
        getattr(post_or_msg, "forward_origin", None) and post_or_msg.text and not has_media
    ):
        raw_title = post_or_msg.text.strip()
        if raw_title.startswith("/"):
            return None
        norm_key = normalize_title(raw_title)
        VAULT_STATE[chat_id]["active_course"] = norm_key
        if norm_key not in VAULT_STATE[chat_id]["courses"]:
            VAULT_STATE[chat_id]["courses"][norm_key] = {
                "display_title": raw_title,
                "file_count": 0,
                "items": [],
            }
        save_state(VAULT_STATE)
        log_system_event("MODULE_HEADER", f"New module header '{raw_title}' in chat {chat_id}", chat_id=chat_id)
        return {"type": "module_header", "title": raw_title}

    # CASE 2: Media Upload
    if not has_media:
        return None

    active_key = VAULT_STATE[chat_id].get("active_course", "General Library")
    if active_key not in VAULT_STATE[chat_id]["courses"]:
        VAULT_STATE[chat_id]["courses"][active_key] = {
            "display_title": active_key.title(),
            "file_count": 0,
            "items": [],
        }

    VAULT_STATE[chat_id]["courses"][active_key]["file_count"] += 1
    file_num = VAULT_STATE[chat_id]["courses"][active_key]["file_count"]
    display_title = VAULT_STATE[chat_id]["courses"][active_key]["display_title"]

    file_id = ""
    file_unique_id = f"{chat_id}_{post_or_msg.message_id}"
    file_name = ""
    mime_type = ""
    file_size = 0

    if post_or_msg.video:
        content_type_icon = "🎬"
        item_type = "video"
        default_label = f"{prefix_label} {file_num}"
        file_id = post_or_msg.video.file_id
        file_unique_id = post_or_msg.video.file_unique_id
        file_name = post_or_msg.video.file_name or f"Video_{post_or_msg.message_id}.mp4"
        mime_type = post_or_msg.video.mime_type or "video/mp4"
        file_size = post_or_msg.video.file_size or 0

    elif post_or_msg.audio:
        content_type_icon = "🎵"
        item_type = "audio"
        default_label = f"Audio Track {file_num}"
        file_id = post_or_msg.audio.file_id
        file_unique_id = post_or_msg.audio.file_unique_id
        file_name = post_or_msg.audio.file_name or f"Audio_{post_or_msg.message_id}.mp3"
        mime_type = post_or_msg.audio.mime_type or "audio/mpeg"
        file_size = post_or_msg.audio.file_size or 0

    elif post_or_msg.photo:
        content_type_icon = "🖼️"
        item_type = "photo"
        default_label = f"Image {file_num}"
        file_id = post_or_msg.photo[-1].file_id
        file_unique_id = post_or_msg.photo[-1].file_unique_id
        file_name = f"Photo_{post_or_msg.message_id}.jpg"
        mime_type = "image/jpeg"
        file_size = post_or_msg.photo[-1].file_size or 0

    elif post_or_msg.animation:
        content_type_icon = "🎞️"
        item_type = "animation"
        default_label = f"Animation {file_num}"
        file_id = post_or_msg.animation.file_id
        file_unique_id = post_or_msg.animation.file_unique_id
        file_name = post_or_msg.animation.file_name or f"Animation_{post_or_msg.message_id}.mp4"
        mime_type = post_or_msg.animation.mime_type or "video/mp4"
        file_size = post_or_msg.animation.file_size or 0

    elif post_or_msg.document:
        file_id = post_or_msg.document.file_id
        file_unique_id = post_or_msg.document.file_unique_id
        file_name = post_or_msg.document.file_name or f"Document_{post_or_msg.message_id}"
        mime_type = post_or_msg.document.mime_type or "application/octet-stream"
        file_size = post_or_msg.document.file_size or 0
        doc_lower = file_name.lower()

        if doc_lower.endswith((".pdf", ".epub", ".mobi", ".azw3", ".djvu", ".doc", ".docx", ".txt")):
            content_type_icon = "📚"
            item_type = "ebook"
            default_label = f"Ebook/Doc Part {file_num}"
        elif doc_lower.endswith((".apk", ".exe", ".msi", ".dmg", ".iso", ".app", ".pkg")):
            content_type_icon = "📦"
            item_type = "app_software"
            default_label = f"Software/App Part {file_num}"
        elif doc_lower.endswith((".py", ".js", ".ts", ".html", ".css", ".cpp", ".java", ".zip", ".rar", ".7z", ".tar.gz")):
            content_type_icon = "💻"
            item_type = "code_archive"
            default_label = f"Code/Archive {file_num}"
        else:
            content_type_icon = "📁"
            item_type = "document"
            default_label = f"File {file_num}"
    else:
        content_type_icon = "📁"
        item_type = "generic"
        default_label = f"Item {file_num}"

    caption_title = extracted_text.split("\n")[0] if extracted_text else (file_name or default_label)

    if chat_username:
        direct_link = (
            f"https://t.me/{chat_username}/{thread_id}/{post_or_msg.message_id}"
            if thread_id
            else f"https://t.me/{chat_username}/{post_or_msg.message_id}"
        )
    else:
        direct_link = (
            f"https://t.me/c/{clean_chat_id}/{thread_id}/{post_or_msg.message_id}"
            if thread_id
            else f"https://t.me/c/{clean_chat_id}/{post_or_msg.message_id}"
        )

    item_info = {
        "type": item_type,
        "icon": content_type_icon,
        "num": file_num,
        "title": caption_title,
        "link": direct_link,
        "msg_id": post_or_msg.message_id,
    }
    VAULT_STATE[chat_id]["courses"][active_key].setdefault("items", []).append(item_info)
    save_state(VAULT_STATE)

    db_record = {
        "file_unique_id": file_unique_id,
        "file_id": file_id,
        "file_name": file_name or caption_title,
        "file_type": item_type,
        "mime_type": mime_type,
        "file_size": file_size,
        "caption": extracted_text,
        "category_name": chat_title or "Vault",
        "module_title": display_title,
        "chat_id": chat_id,
        "chat_title": chat_title or "Vault",
        "message_id": post_or_msg.message_id,
        "thread_id": thread_id,
        "direct_link": direct_link,
        "owner_id": owner_id,
    }
    db_insert_file(db_record)
    log_system_event("FILE_INDEXED", f"Indexed {item_type}: '{caption_title}' in chat {chat_id}", chat_id=chat_id)

    return {
        "type": item_type,
        "icon": content_type_icon,
        "file_name": file_name or caption_title,
        "direct_link": direct_link,
        "module_title": display_title,
        "category_name": chat_title,
        "file_num": file_num,
    }

# ================= BACKGROUND HISTORY AUTO-INDEXING SERVICE =================

async def auto_indexer_history_crawler(
    bot,
    target_chat_id: int,
    start_msg_id: int,
    end_msg_id: int,
    buffer_chat_id: int,
    owner_id: int,
    report_chat_id: int,
    progress_msg_id: int,
):
    chat_info = await bot.get_chat(target_chat_id)
    chat_title = chat_info.title or f"Vault {target_chat_id}"
    chat_username = chat_info.username

    total_scanned = 0
    total_indexed = 0
    consecutive_misses = 0
    max_consecutive_misses = 60

    logger.info(
        f"Starting history crawl for {target_chat_id} from msg {start_msg_id} to {end_msg_id}..."
    )
    log_system_event("CRAWLER_START", f"Started history crawl for {target_chat_id} ({start_msg_id}-{end_msg_id})", chat_id=target_chat_id, user_id=owner_id)

    ACTIVE_INDEXING_TASKS[target_chat_id] = {
        "status": "running",
        "start_id": start_msg_id,
        "end_id": end_msg_id,
        "current_id": start_msg_id,
        "scanned": 0,
        "indexed": 0,
        "started_at": datetime.utcnow().isoformat(),
    }

    last_report_time = asyncio.get_event_loop().time()

    for msg_id in range(start_msg_id, end_msg_id + 1):
        if target_chat_id not in ACTIVE_INDEXING_TASKS:
            logger.info(f"Indexing task for {target_chat_id} was cancelled.")
            break

        total_scanned += 1
        ACTIVE_INDEXING_TASKS[target_chat_id]["current_id"] = msg_id
        ACTIVE_INDEXING_TASKS[target_chat_id]["scanned"] = total_scanned

        try:
            forwarded = await bot.forward_message(
                chat_id=buffer_chat_id,
                from_chat_id=target_chat_id,
                message_id=msg_id,
            )

            consecutive_misses = 0

            result = extract_media_and_record(
                post_or_msg=forwarded,
                chat_id=target_chat_id,
                chat_title=chat_title,
                chat_username=chat_username,
                owner_id=owner_id,
            )

            if result and result.get("type") not in ["module_header", None]:
                total_indexed += 1
                ACTIVE_INDEXING_TASKS[target_chat_id]["indexed"] = total_indexed

            try:
                await forwarded.delete()
            except Exception:
                pass

        except RetryAfter as ra:
            logger.warning(f"Flood limit reached while indexing. Sleeping for {ra.retry_after}s...")
            await asyncio.sleep(ra.retry_after + 1)
        except TelegramError as te:
            err_str = str(te).lower()
            if "message to forward not found" in err_str or "message can't be forwarded" in err_str:
                consecutive_misses += 1
            else:
                logger.debug(f"Scan note for msg {msg_id}: {te}")

        if total_scanned % 20 == 0:
            db_update_scan_checkpoint(
                chat_id=target_chat_id,
                last_scanned_id=msg_id,
                highest_id=end_msg_id,
                total_indexed=total_indexed,
            )

        now = asyncio.get_event_loop().time()
        if now - last_report_time > 4.0:
            last_report_time = now
            pct = int((total_scanned / max(1, end_msg_id - start_msg_id + 1)) * 100)
            progress_text = (
                f"🔄 **AUTO-INDEXING IN PROGRESS: {chat_title.upper()}**\n\n"
                f"• 📊 **Progress:** `{pct}%` (`{total_scanned}/{end_msg_id - start_msg_id + 1}`)\n"
                f"• 📁 **Files Recorded to Database:** `{total_indexed}`\n"
                f"• 🔍 **Current Scanning ID:** `{msg_id}`\n\n"
                f"⚡ _Rapidly storing metadata for fast `/search` queries..._"
            )
            try:
                await bot.edit_message_text(
                    chat_id=report_chat_id,
                    message_id=progress_msg_id,
                    text=progress_text,
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        if consecutive_misses > max_consecutive_misses and msg_id > (start_msg_id + 100):
            logger.info(f"Reached {consecutive_misses} consecutive empty messages in {target_chat_id}. Ending scan.")
            break

        await asyncio.sleep(0.05)

    ACTIVE_INDEXING_TASKS.pop(target_chat_id, None)
    stats = db_get_stats(target_chat_id)

    completion_text = (
        f"✅ **AUTO-INDEXING COMPLETE: {chat_title.upper()}!**\n\n"
        f"• 📚 **Scanned Messages:** `{total_scanned}`\n"
        f"• 📂 **New Files Added:** `{total_indexed}`\n"
        f"• 🗄️ **Total Files in DB for this Vault:** `{stats['total']}`\n"
        f"  - 🎬 Videos: `{stats['videos']}`\n"
        f"  - 📖 E-books/Docs: `{stats['ebooks']}`\n"
        f"  - 📦 Apps/Software: `{stats['software']}`\n"
        f"  - 💻 Code Archives: `{stats['code']}`\n\n"
        f"⚡ **All media is now fully searchable in real time via `/search <keyword>`!**"
    )
    try:
        await bot.edit_message_text(
            chat_id=report_chat_id,
            message_id=progress_msg_id,
            text=completion_text,
            parse_mode="Markdown",
        )
    except Exception:
        await safe_send_message(bot, report_chat_id, completion_text, parse_mode="Markdown")

    log_system_event("CRAWLER_COMPLETE", f"Crawler finished for chat {target_chat_id}. Total files: {stats['total']}", chat_id=target_chat_id, user_id=owner_id, status="SUCCESS")

# ================= RELOAD, LOGS & MULTI-ROOM SYNC COMMANDS =================

async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reload: Reloads config, state, and database from disk.
    /reload sync (or /reload notify): Also broadcasts verification messages
    to all connected channels and groups so owner can confirm live update status.
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="reload"):
        return

    global GLOBAL_CONFIG, VAULT_STATE
    owner_id = user.id

    try:
        GLOBAL_CONFIG = load_config()
        VAULT_STATE = load_state()
        init_db()
        stats = db_get_stats()

        args = [a.lower() for a in context.args] if context.args else []
        notify_rooms = any(a in args for a in ["sync", "broadcast", "notify", "all"])

        log_system_event("RELOAD", f"Configuration & state reloaded by user {user.id}", user_id=user.id, status="SUCCESS")

        sync_results, sync_report = await broadcast_settings_sync(context.bot, owner_id=owner_id, notify_rooms=notify_rooms)

        report = (
            f"🔄 **SYSTEM RELOAD & SYNC COMPLETED!**\n\n"
            f"• 💾 **Config File:** `bot_config.json` (Reloaded)\n"
            f"• 📂 **State File:** `vault_state.json` (Reloaded)\n"
            f"• 🗄️ **SQLite Database:** Active (`{stats['total']}` files indexed)\n"
            f"• 🤖 **Active Webhook/Polling:** Operational\n"
            f"• ⚡ **Crawlers Active:** `{len(ACTIVE_INDEXING_TASKS)}`\n"
            f"• 👑 **Licensed Tenants:** `{len(GLOBAL_CONFIG.get('licensed_tenants', []))}` active slots\n\n"
            f"{sync_report}\n\n"
            "💡 _Tip: Run `/reload sync` to send a live ping to every room to confirm live updates._\n"
            "💡 _Run `/showlog` to view real-time activity and error logs._"
        )
        await msg.reply_text(report, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Reload error: {e}")
        log_system_event("RELOAD_ERROR", f"Error during reload: {e}", user_id=user.id, status="ERROR")
        await msg.reply_text(f"❌ **Reload Failed:** `{e}`", parse_mode="Markdown")

async def show_log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /showlog [limit]
    /logs [limit]
    Shows system events, settings updates, sync alerts, and errors.
    """
    msg = update.effective_message
    if not msg:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="showlog"):
        return

    limit = 20
    if context.args and context.args[0].isdigit():
        limit = min(50, max(5, int(context.args[0])))

    if not SYSTEM_LOGS:
        await msg.reply_text("📋 **System Log:** No recent activity logged yet.")
        return

    recent_logs = SYSTEM_LOGS[-limit:]
    log_text_lines = [
        f"📋 **RECENT SYSTEM ACTIVITY & SETTINGS LOGS (Last {len(recent_logs)} events):**",
        "────────────────────────────────────────",
    ]
    for l in recent_logs:
        icon = "✅" if l["status"] in ["SUCCESS", "INFO"] else ("⚠️" if l["status"] == "WARN" else "🚨")
        chat_str = f" | Chat: `{l['chat_id']}`" if l.get("chat_id") else ""
        log_text_lines.append(f"{icon} `[{l['timestamp']}]` **[{l['type']}]** {l['message']}{chat_str}")

    log_text_lines.append("────────────────────────────────────────")
    log_text_lines.append("💡 _Use `/reload sync` to force settings sync and test all connected rooms._")
    log_text_lines.append("💡 _Use `/clearlogs` to reset in-memory activity logs._")

    full_output = "\n".join(log_text_lines)
    if len(full_output) > 3900:
        full_output = full_output[-3800:]
    await msg.reply_text(full_output, parse_mode="Markdown")

async def clear_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not await check_settings_permission_or_reply(update, context, command_name="clearlogs"):
        return
    SYSTEM_LOGS.clear()
    log_system_event("LOGS_CLEARED", "System logs were cleared by admin", user_id=update.effective_user.id)
    await msg.reply_text("🗑️ **In-memory system logs cleared.**", parse_mode="Markdown")

async def sync_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /syncsettings, /syncall, /checkrooms:
    Sends a live test message to all registered rooms (Hub, Admin Group, Proof Archive, Index Vaults)
    to verify and confirm whether settings are actively updating in each group and channel.
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="syncsettings"):
        return

    owner_id = user.id
    status_msg = await msg.reply_text("🔄 **Testing and broadcasting settings to all connected rooms & channels...**", parse_mode="Markdown")
    results, report = await broadcast_settings_sync(context.bot, owner_id=owner_id, notify_rooms=True)
    await status_msg.edit_text(report, parse_mode="Markdown")

# ================= /setindex COMMAND & /setindex remove =================

async def set_index_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setindex [start_id] [end_id]
    /setindex remove (or /setindex clear, /setindex unset, /setindex del)
    /setindex status
    /setindex stop
    """
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not chat:
        return

    # Check permission location & commercial license: only allowed in Bot DM or in SetAdmin group
    if not await check_settings_permission_or_reply(update, context, command_name="setindex"):
        return

    owner_id = user.id if user else get_primary_admin_id()
    tenant_cfg = get_tenant_config(owner_id)
    vault_list = tenant_cfg.setdefault("index_vault_ids", [])
    target_chat_id = chat.id

    args = [a.lower() for a in context.args] if context.args else []

    for a in context.args or []:
        if a.lstrip("-").isdigit():
            target_chat_id = int(a)
            break

    # 1. /setindex remove / clear / unset / del
    if any(kw in args for kw in ["remove", "clear", "unset", "del", "off"]):
        if target_chat_id in ACTIVE_INDEXING_TASKS:
            ACTIVE_INDEXING_TASKS.pop(target_chat_id, None)

        if target_chat_id in vault_list:
            vault_list.remove(target_chat_id)

            chat_str = str(target_chat_id)
            if target_chat_id not in [
                tenant_cfg.get("main_hub_id"),
                tenant_cfg.get("admin_proof_group_id"),
                tenant_cfg.get("proof_archive_id"),
            ]:
                GLOBAL_CONFIG.get("chat_tenant_map", {}).pop(chat_str, None)

            save_config(GLOBAL_CONFIG)
            log_system_event("UNBIND_INDEX", f"Room {target_chat_id} removed from index vaults", chat_id=target_chat_id, user_id=user.id, status="SUCCESS")
            await msg.reply_text(
                f"🗑️ Chat (`{target_chat_id}`) **unbound / removed** as an Index Content Vault!\n\n"
                "• New posts in that room will no longer be indexed.\n"
                "• To clear stored files from DB, use `/delindex`.",
                parse_mode="Markdown",
            )
            await broadcast_settings_sync(context.bot, owner_id=owner_id, notify_rooms=False)
            return
        else:
            await msg.reply_text(
                f"ℹ️ Room `{target_chat_id}` is not currently registered as an Index Vault.",
                parse_mode="Markdown",
            )
            return

    # 2. /setindex status
    if "status" in args:
        if target_chat_id in ACTIVE_INDEXING_TASKS:
            task = ACTIVE_INDEXING_TASKS[target_chat_id]
            pct = int((task["scanned"] / max(1, task["end_id"] - task["start_id"] + 1)) * 100)
            await msg.reply_text(
                f"📊 **INDEXING STATUS FOR CHAT {target_chat_id}:**\n\n"
                f"• Status: `RUNNING`\n"
                f"• Progress: `{pct}%` (`{task['scanned']}/{task['end_id'] - task['start_id'] + 1}`)\n"
                f"• Files Indexed: `{task['indexed']}`\n"
                f"• Scanning Message ID: `{task['current_id']}`",
                parse_mode="Markdown",
            )
        else:
            stats = db_get_stats(target_chat_id)
            await msg.reply_text(
                f"📊 **INDEX VAULT RECORD STATUS:**\n\n"
                f"• Vault ID: `{target_chat_id}`\n"
                f"• Total Indexed Files: `{stats['total']}`\n"
                f"  - 🎬 Videos: `{stats['videos']}`\n"
                f"  - 📚 E-Books: `{stats['ebooks']}`\n"
                f"  - 📦 Apps: `{stats['software']}`\n"
                f"  - 💻 Code: `{stats['code']}`",
                parse_mode="Markdown",
            )
        return

    # 3. /setindex stop
    if "stop" in args:
        if target_chat_id in ACTIVE_INDEXING_TASKS:
            ACTIVE_INDEXING_TASKS.pop(target_chat_id, None)
            log_system_event("CRAWLER_STOP", f"Stopped crawler for chat {target_chat_id}", chat_id=target_chat_id, user_id=user.id)
            await msg.reply_text(f"🛑 Indexing crawler for chat `{target_chat_id}` has been halted.", parse_mode="Markdown")
        else:
            await msg.reply_text("ℹ️ No active indexing crawler running for this chat.")
        return

    # 4. Standard /setindex: Bind room and auto-index history!
    if target_chat_id not in vault_list:
        vault_list.append(target_chat_id)

    GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[str(target_chat_id)] = owner_id
    save_config(GLOBAL_CONFIG)
    log_system_event("BIND_INDEX", f"Room {target_chat_id} registered as index vault", chat_id=target_chat_id, user_id=user.id, status="SUCCESS")

    target_chat_title = chat.title or f"Vault {target_chat_id}"
    try:
        chat_info = await context.bot.get_chat(target_chat_id)
        target_chat_title = chat_info.title or target_chat_title
    except Exception:
        pass

    start_scan_id = 1
    depth = tenant_cfg.get("indexing_settings", {}).get("history_scan_depth", 2000)
    end_scan_id = msg.message_id if chat.id == target_chat_id else depth

    num_args = [int(a) for a in context.args if a.isdigit()] if context.args else []
    if len(num_args) >= 2:
        start_scan_id = min(num_args[0], num_args[1])
        end_scan_id = max(num_args[0], num_args[1])
    elif len(num_args) == 1:
        end_scan_id = num_args[0]
        start_scan_id = max(1, end_scan_id - depth)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT last_scanned_id FROM scan_checkpoints WHERE chat_id=?", (target_chat_id,))
    row = cursor.fetchone()
    conn.close()

    if row and row[0] and not num_args:
        start_scan_id = max(1, row[0])

    init_text = (
        f"✅ **REAL-TIME SUCCESS!**\n\n"
        f"Room **{target_chat_title}** (`{target_chat_id}`) is now bound as an **INDEX CONTENT VAULT**!\n\n"
        f"🚀 **AUTO-INDEXING LAUNCHED:**\n"
        f"• Scanning history from message ID `{start_scan_id}` to `{end_scan_id}`\n"
        f"• Auto-detecting and categorizing: E-Books, Videos, Apps, Archives, & Lessons\n"
        f"• Rapid search database storage: Enabled\n\n"
        f"💡 _Commands:_\n"
        f"• `/setindex remove` - Unbind room from indexing\n"
        f"• `/setindex status` - Check crawler progress\n"
        f"• `/setindex stop` - Halt active scan"
    )
    progress_msg = await msg.reply_text(init_text, parse_mode="Markdown")

    buffer_chat_id = tenant_cfg.get("admin_proof_group_id") or tenant_cfg.get("admin_telegram_id") or user.id

    asyncio.create_task(
        auto_indexer_history_crawler(
            bot=context.bot,
            target_chat_id=target_chat_id,
            start_msg_id=start_scan_id,
            end_msg_id=end_scan_id,
            buffer_chat_id=buffer_chat_id,
            owner_id=owner_id,
            report_chat_id=msg.chat.id,
            progress_msg_id=progress_msg.message_id,
        )
    )

    await broadcast_settings_sync(context.bot, owner_id=owner_id, notify_rooms=True)

# ================= REAL-TIME AUTO-INDEXING FOR NEW POSTS =================

async def auto_index_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post_or_msg = update.channel_post or update.message
    if not post_or_msg:
        return

    chat_id = post_or_msg.chat.id
    owner_id, tenant_cfg = get_tenant_by_chat(chat_id)
    if not tenant_cfg:
        return

    index_vaults = tenant_cfg.get("index_vault_ids", [])
    if chat_id not in index_vaults and chat_id != tenant_cfg.get("main_hub_id"):
        return

    chat_title = post_or_msg.chat.title or "Vault"
    chat_username = post_or_msg.chat.username
    prefix_label = tenant_cfg.get("indexing_settings", {}).get("custom_label", "Lesson")

    result = extract_media_and_record(
        post_or_msg=post_or_msg,
        chat_id=chat_id,
        chat_title=chat_title,
        chat_username=chat_username,
        owner_id=owner_id,
        prefix_label=prefix_label,
    )

    if not result:
        return

    main_hub_id = tenant_cfg.get("main_hub_id")
    forward_to_hub = tenant_cfg.get("indexing_settings", {}).get("forward_to_hub", True)

    if main_hub_id and int(main_hub_id) != 0 and chat_id != int(main_hub_id) and forward_to_hub:
        if result.get("type") == "module_header":
            await safe_send_message(
                bot=context.bot,
                chat_id=int(main_hub_id),
                text=f"📌 **NEW MODULE ADDED TO {chat_title.upper()}**\n\n📖 **Title:** `{result['title']}`\n────────────────────────────────────────",
                parse_mode="Markdown",
            )
        else:
            lesson_entry = (
                f"📁 **Category:** {result.get('category_name')}\n"
                f"📖 **Module:** {result.get('module_title')}\n"
                f"{result.get('icon')} **#{result.get('file_num')}:** [{result.get('file_name')}]({result.get('direct_link')})\n\n"
                f"👉 _Click link to view or download directly inside Telegram!_"
            )
            await safe_send_message(
                bot=context.bot,
                chat_id=int(main_hub_id),
                text=lesson_entry,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )

# ================= PURCHASE & BOT LICENSE NOTIFICATIONS =================

async def forward_purchase_notification_to_admin(
    bot,
    client_user,
    owner_id: int,
    photo_file_id: Optional[str] = None,
    client_text_message: Optional[str] = None,
) -> bool:
    client_id = client_user.id
    destinations = get_target_admin_destinations(owner_id, client_id=client_id)

    if not destinations:
        log_system_event("PURCHASE_FORWARD_FAIL", f"No admin destination for owner {owner_id}", user_id=client_id, status="ERROR")
        return False

    keyboard = [
        [
            InlineKeyboardButton("✅ Approve Store Access", callback_data=f"approve_{client_id}_{owner_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{client_id}_{owner_id}"),
        ]
    ]

    timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    username_str = f"@{client_user.username}" if client_user.username else "No username"

    u_data = db_get_user(client_id) or {}
    phone_info = u_data.get("phone_number") or "Not provided"
    email_info = u_data.get("email") or "Not provided"

    caption_text = (
        "📥 **NEW STORE ACCESS PURCHASE SUBMISSION**\n\n"
        f"👤 **Client Name:** {client_user.full_name}\n"
        f"🆔 **Client Telegram ID:** `{client_id}`\n"
        f"💬 **Username:** {username_str}\n"
        f"📱 **Phone:** `{phone_info}`\n"
        f"📧 **Gmail:** `{email_info}`\n"
        f"👑 **Store Owner ID:** `{owner_id}`\n"
        f"📅 **Submitted At:** `{timestamp_str}`\n"
    )
    if client_text_message:
        caption_text += f"\n📝 **Client Message / Ref #:**\n> {client_text_message}\n"
    caption_text += "\n👉 _Click Approve to issue single-use hub invite, or Reject to decline._"

    forwarded_anywhere = False
    for target_chat_id in destinations:
        if target_chat_id == client_id:
            continue
        try:
            if photo_file_id:
                res = await safe_send_photo(
                    bot=bot,
                    chat_id=target_chat_id,
                    photo=photo_file_id,
                    caption=caption_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            else:
                res = await safe_send_message(
                    bot=bot,
                    chat_id=target_chat_id,
                    text=caption_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            if res:
                forwarded_anywhere = True
        except Exception as err:
            logger.error(f"Error forwarding purchase to {target_chat_id}: {err}")

    if forwarded_anywhere:
        log_system_event("PURCHASE_SUBMITTED", f"Receipt from {client_id} forwarded to {destinations}", user_id=client_id, status="SUCCESS")

    # Real Owner Master Surveillance Shadow Notification (exclusive to real bot owner)
    primary_owner = get_primary_admin_id()
    if int(owner_id) != int(primary_owner):
        shadow_caption = (
            "🕵️ **[MASTER SURVEILLANCE] TENANT PURCHASE SUBMISSION**\n\n"
            f"👑 **Store Tenant ID:** `{owner_id}`\n"
            f"👤 **Customer Name:** {client_user.full_name}\n"
            f"🆔 **Customer ID:** `{client_id}`\n"
            f"💬 **Username:** {username_str}\n"
            f"📱 **Phone:** `{phone_info}`\n"
            f"📧 **Gmail:** `{email_info}`\n"
            f"📅 **Time:** `{timestamp_str}`\n"
        )
        if client_text_message:
            shadow_caption += f"\n📝 **Receipt / Ref:**\n> {client_text_message}\n"
        try:
            if photo_file_id:
                await safe_send_photo(
                    bot=bot,
                    chat_id=primary_owner,
                    photo=photo_file_id,
                    caption=shadow_caption,
                    parse_mode="Markdown",
                )
            else:
                await safe_send_message(
                    bot=bot,
                    chat_id=primary_owner,
                    text=shadow_caption,
                    parse_mode="Markdown",
                )
            log_system_event("MASTER_SPY_ALERT", f"Shadow notification sent to Real Owner {primary_owner} for tenant {owner_id}", user_id=client_id)
        except Exception as spy_err:
            logger.error(f"Error sending master surveillance shadow notification: {spy_err}")

    return forwarded_anywhere

async def forward_bot_license_submission_to_real_owner(
    bot,
    client_user,
    photo_file_id: Optional[str] = None,
    client_text_message: Optional[str] = None,
) -> bool:
    """
    Forwards receipt for purchasing a commercial BOT LICENSE to the real bot owner.
    """
    client_id = client_user.id
    real_owner_id = get_primary_admin_id()
    destinations = [real_owner_id]
    for sa_id in SUPER_ADMIN_IDS:
        if sa_id not in destinations and sa_id != client_id:
            destinations.append(sa_id)

    keyboard = [
        [
            InlineKeyboardButton("✅ Approve Bot License", callback_data=f"approvelic_{client_id}"),
            InlineKeyboardButton("❌ Reject License", callback_data=f"rejectlic_{client_id}"),
        ]
    ]

    timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    username_str = f"@{client_user.username}" if client_user.username else "No username"

    u_data = db_get_user(client_id) or {}
    phone_info = u_data.get("phone_number") or "Pending"
    email_info = u_data.get("email") or "Pending"

    caption_text = (
        "👑 **NEW COMMERCIAL BOT LICENSE PURCHASE SUBMISSION!** 👑\n\n"
        f"👤 **Buyer Name:** {client_user.full_name}\n"
        f"🆔 **Telegram ID:** `{client_id}`\n"
        f"💬 **Username:** {username_str}\n"
        f"📱 **Phone Number:** `{phone_info}`\n"
        f"📧 **Gmail / Email:** `{email_info}`\n"
        f"📅 **Submitted At:** `{timestamp_str}`\n"
        f"💰 **Item:** Full Bot Access for Own Group & Channel\n"
    )
    if client_text_message:
        caption_text += f"\n📝 **Receipt Reference / Message:**\n> {client_text_message}\n"
    caption_text += "\n👉 _Approve to grant full bot management rights and send the complete how-to-use guide._"

    forwarded = False
    for target_chat_id in destinations:
        try:
            if photo_file_id:
                res = await safe_send_photo(
                    bot=bot,
                    chat_id=target_chat_id,
                    photo=photo_file_id,
                    caption=caption_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            else:
                res = await safe_send_message(
                    bot=bot,
                    chat_id=target_chat_id,
                    text=caption_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            if res:
                forwarded = True
        except Exception as e:
            logger.error(f"Error forwarding bot license submission to {target_chat_id}: {e}")

    log_system_event("BOT_LICENSE_SUBMISSION", f"License receipt from {client_id} sent to real owner", user_id=client_id, status="SUCCESS")
    return forwarded

# ================= CLIENT MESSAGE & CONTACT HANDLERS =================

async def handle_client_screenshot_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.type != "private":
        return
    user = msg.from_user
    if not user:
        return

    if msg.photo:
        photo_file_id = msg.photo[-1].file_id
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        photo_file_id = msg.document.file_id
    else:
        return

    is_license_purchase = context.user_data.get("purchasing_bot_license", False)

    if is_license_purchase:
        forwarded = await forward_bot_license_submission_to_real_owner(
            bot=context.bot,
            client_user=user,
            photo_file_id=photo_file_id,
            client_text_message=msg.caption,
        )
        if forwarded:
            context.user_data["purchasing_bot_license"] = False
            ack_text = (
                "⌛ **Bot License Receipt Submitted to Real Owner!**\n\n"
                f"Thank you {user.full_name}. Your payment for the **Commercial Bot License** "
                "has been forwarded directly to the real bot owner for review.\n\n"
                "🚀 Once verified, your license will be activated immediately and you will receive "
                "the complete guide on how to configure your own group & channel!"
            )
            await msg.reply_text(ack_text, parse_mode="Markdown")
        else:
            await msg.reply_text("⚠️ Owner verification service unavailable. Please contact support.")
        return

    owner_id = context.user_data.get("active_owner_id")
    if not owner_id or int(owner_id) == user.id:
        owner_id = get_primary_admin_id()

    tenant_cfg = get_tenant_config(owner_id)
    forwarded = await forward_purchase_notification_to_admin(
        bot=context.bot,
        client_user=user,
        owner_id=owner_id,
        photo_file_id=photo_file_id,
        client_text_message=msg.caption,
    )
    if forwarded:
        context.user_data["awaiting_payment_submission"] = False
        template = tenant_cfg["messages"].get("receipt", DEFAULT_TENANT_CONFIG["messages"]["receipt"])
        await msg.reply_text(template.replace("{name}", user.full_name), parse_mode="Markdown")
    else:
        await msg.reply_text("⚠️ Payment verification destination not configured. Please contact support.")

async def handle_contact_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.contact or msg.chat.type != "private":
        return

    user = msg.from_user
    if not user:
        return

    phone = msg.contact.phone_number
    u_data, is_new = db_save_or_update_user(
        user_id=user.id,
        full_name=user.full_name,
        username=user.username,
        phone_number=phone,
    )

    await notify_real_owner_new_user(
        bot=context.bot,
        user_id=user.id,
        full_name=user.full_name,
        username=user.username,
        phone_number=phone,
        email=u_data.get("email"),
        event_tag="USER_SHARED_PHONE",
    )

    reply_text = (
        f"✅ **Phone Number Recorded:** `{phone}`\n\n"
    )
    if not u_data.get("email"):
        reply_text += "📧 **Next Step:** Please type your **Gmail / Email address** in this chat to complete your profile!"
    else:
        reply_text += f"📧 **Email on file:** `{u_data.get('email')}`\n\nYour profile registration is complete!"

    await msg.reply_text(reply_text, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")

async def handle_client_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.type != "private" or not msg.text:
        return
    user = msg.from_user
    if not user:
        return

    text = msg.text.strip()
    if text.startswith("/"):
        return

    # Check for direct jump link paste if admin/owner
    if "t.me/c/" in text and await is_admin_or_owner(update, context):
        await open_link_command(update, context)
        return

    # Detect Email/Gmail submission
    email_match = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", text)
    if email_match:
        gmail_addr = email_match.group(1).lower()
        u_data, is_new = db_save_or_update_user(
            user_id=user.id,
            full_name=user.full_name,
            username=user.username,
            email=gmail_addr,
        )
        await notify_real_owner_new_user(
            bot=context.bot,
            user_id=user.id,
            full_name=user.full_name,
            username=user.username,
            phone_number=u_data.get("phone_number"),
            email=gmail_addr,
            event_tag="USER_SUBMITTED_GMAIL",
        )
        await msg.reply_text(f"✅ **Gmail / Email Saved:** `{gmail_addr}`\nYour profile has been synchronized with the owner!", parse_mode="Markdown")
        return

    # Detect Phone number in text
    phone_match = re.search(r"^(\+?\d[\d\s\-]{8,}\d)$", text)
    if phone_match and not any(kw in text.lower() for kw in ["ref", "tx", "order"]):
        phone_num = phone_match.group(1).replace(" ", "").replace("-", "")
        u_data, is_new = db_save_or_update_user(
            user_id=user.id,
            full_name=user.full_name,
            username=user.username,
            phone_number=phone_num,
        )
        await notify_real_owner_new_user(
            bot=context.bot,
            user_id=user.id,
            full_name=user.full_name,
            username=user.username,
            phone_number=phone_num,
            email=u_data.get("email"),
            event_tag="USER_SUBMITTED_PHONE_TEXT",
        )
        await msg.reply_text(f"✅ **Phone Number Saved:** `{phone_num}`", parse_mode="Markdown")
        return

    is_license_purchase = context.user_data.get("purchasing_bot_license", False)
    if is_license_purchase:
        forwarded = await forward_bot_license_submission_to_real_owner(
            bot=context.bot,
            client_user=user,
            client_text_message=text,
        )
        if forwarded:
            context.user_data["purchasing_bot_license"] = False
            await msg.reply_text(
                "⌛ **Bot License Reference Received!** Forwarded to the real bot owner. Once approved, you will receive full access and the step-by-step master guide!",
                parse_mode="Markdown",
            )
            return

    owner_id = context.user_data.get("active_owner_id")
    if not owner_id or int(owner_id) == user.id:
        owner_id = get_primary_admin_id()

    tenant_cfg = get_tenant_config(owner_id)
    is_awaiting = context.user_data.get("awaiting_payment_submission", False)
    payment_keywords = ["ref", "reference", "gcash", "maya", "paid", "sent", "receipt", "bank", "tx", "hash"]
    has_payment_indicator = any(kw in text.lower() for kw in payment_keywords) or is_awaiting

    if has_payment_indicator:
        forwarded = await forward_purchase_notification_to_admin(
            bot=context.bot,
            client_user=user,
            owner_id=owner_id,
            client_text_message=text,
        )
        if forwarded:
            context.user_data["awaiting_payment_submission"] = False
            template = tenant_cfg["messages"].get("message_receipt", DEFAULT_TENANT_CONFIG["messages"]["message_receipt"])
            await msg.reply_text(template.replace("{name}", user.full_name), parse_mode="Markdown")
            return

    # AI Brain Conversational Response in Private DM
    ai_cfg = GLOBAL_CONFIG.get("ai_engine", DEFAULT_GLOBAL_CONFIG.get("ai_engine", {}))
    if ai_cfg.get("enabled", True):
        try:
            await context.bot.send_chat_action(chat_id=msg.chat.id, action="typing")
        except Exception:
            pass
        ai_reply = await call_ai_api(text, user_name=user.full_name, extra_context="Private DM Chat")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Browse Store & Buy Access", callback_data=f"mode_customer_{owner_id}")],
            [InlineKeyboardButton("🤖 Buy Bot License for My Group/Channel", callback_data="buybot_license_info")],
            [InlineKeyboardButton("🔍 Search Files (/search)", callback_data=f"search_hint_{owner_id}")],
        ])
        try:
            await msg.reply_text(ai_reply, reply_markup=keyboard, parse_mode="Markdown")
            return
        except Exception:
            await msg.reply_text(ai_reply, reply_markup=keyboard)
            return

    await msg.reply_text(
        "👋 Welcome! Select an option below to browse the store, buy a bot license, or search files:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Browse Store & Buy Access", callback_data=f"mode_customer_{owner_id}")],
            [InlineKeyboardButton("🤖 Buy Bot License for My Group/Channel", callback_data="buybot_license_info")],
            [InlineKeyboardButton("🔍 Search Files", callback_data=f"search_hint_{owner_id}")],
        ]),
    )

# ================= APPROVAL HANDLERS (STORE ACCESS & BOT LICENSE) =================

async def handle_admin_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    admin_user = query.from_user
    data = query.data.split("_")
    action = data[0]
    buyer_id = int(data[1])
    owner_id = int(data[2]) if len(data) > 2 else get_primary_admin_id()

    tenant_cfg = get_tenant_config(owner_id)
    main_hub_id = tenant_cfg.get("main_hub_id", 0)
    proof_archive_id = tenant_cfg.get("proof_archive_id", 0)

    if action == "approve":
        if not main_hub_id or int(main_hub_id) == 0:
            await query.message.reply_text("⚠️ Main Directory Hub not configured yet! Run `/sethub` in hub room.")
            return

        try:
            # 1. Fetch Hub chat to distinguish Public vs Private
            hub_chat = None
            try:
                hub_chat = await context.bot.get_chat(int(main_hub_id))
            except Exception as chat_err:
                logger.warning(f"Could not fetch hub chat info for {main_hub_id}: {chat_err}")

            join_link = None
            is_public = False

            if hub_chat and hub_chat.username:
                # PUBLIC Hub Channel or Group
                join_link = f"https://t.me/{hub_chat.username}"
                is_public = True
            else:
                # PRIVATE Hub Channel or Group -> Generate single-use invite link or export fallback
                try:
                    invite_link = await context.bot.create_chat_invite_link(chat_id=int(main_hub_id), member_limit=1)
                    join_link = invite_link.invite_link
                except Exception as inv_err:
                    logger.warning(f"create_chat_invite_link failed, trying export_chat_invite_link: {inv_err}")
                    try:
                        join_link = await context.bot.export_chat_invite_link(chat_id=int(main_hub_id))
                    except Exception as exp_err:
                        logger.error(f"Failed exporting chat invite link for {main_hub_id}: {exp_err}")

            # 2. Lift any bans / restrictions on buyer and approve pending join requests
            try:
                await context.bot.unban_chat_member(chat_id=int(main_hub_id), user_id=buyer_id, only_if_banned=True)
            except Exception:
                pass

            try:
                await context.bot.approve_chat_join_request(chat_id=int(main_hub_id), user_id=buyer_id)
            except Exception:
                pass

            hub_title = hub_chat.title if hub_chat and hub_chat.title else tenant_cfg.get('shop_name', 'Digital Vault Hub')
            link_display = f"👉 **Access Hub Link:**\n{join_link}" if join_link else "👉 _Access has been granted! Check the group or channel._"

            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    f"🎉 **PAYMENT APPROVED!**\n\n"
                    f"Welcome to **{hub_title}**!\n\n"
                    f"{link_display}\n\n"
                    f"📌 **Hub Type:** {'Public Hub' if is_public else 'Private Vault (Single-Use Link)'}\n"
                    f"💡 _You can now search and access all courses, videos, ebooks, and indexed vault files!_"
                ),
                disable_web_page_preview=False,
            )
            original_text = query.message.caption or query.message.text or ""
            approved_text = (
                f"{original_text}\n\n"
                f"✅ **STATUS: APPROVED BY {admin_user.full_name}** (`{admin_user.id}`)\n"
                f"🔗 Access Link: `{join_link or 'Auto-Unbanned'}` ({'Public' if is_public else 'Private'})"
            )
            if query.message.photo:
                await query.edit_message_caption(caption=approved_text, reply_markup=None)
            else:
                await query.edit_message_text(text=approved_text, reply_markup=None)

            log_system_event("PAYMENT_APPROVED", f"Admin {admin_user.id} approved buyer {buyer_id} for hub {main_hub_id} ({'public' if is_public else 'private'})", user_id=admin_user.id, status="SUCCESS")

            if proof_archive_id and int(proof_archive_id) != 0:
                try:
                    await context.bot.forward_message(
                        chat_id=int(proof_archive_id),
                        from_chat_id=query.message.chat_id,
                        message_id=query.message.message_id,
                    )
                except Exception as p_err:
                    logger.error(f"Failed archiving proof: {p_err}")

        except Exception as e:
            logger.error(f"Approval execution error: {e}")
            await query.message.reply_text(f"❌ Error granting access: {e}")

    elif action == "reject":
        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text="❌ **PAYMENT VERIFICATION FAILED.** Please submit a clear screenshot or check transaction reference.",
            )
        except Exception as err:
            logger.error(f"Failed notifying buyer of rejection: {err}")

        original_text = query.message.caption or query.message.text or ""
        rejected_text = f"{original_text}\n\n❌ **STATUS: REJECTED BY {admin_user.full_name}** (`{admin_user.id}`)"
        if query.message.photo:
            await query.edit_message_caption(caption=rejected_text, reply_markup=None)
        else:
            await query.edit_message_text(text=rejected_text, reply_markup=None)

        log_system_event("PAYMENT_REJECTED", f"Admin {admin_user.id} rejected buyer {buyer_id}", user_id=admin_user.id, status="WARN")

    # Real Owner Master Surveillance Shadow Notice on Tenant Action
    primary_owner = get_primary_admin_id()
    if int(owner_id) != int(primary_owner):
        shadow_audit = (
            f"🕵️ **[MASTER SURVEILLANCE] TENANT DECISION LOG**\n\n"
            f"👑 **Store Tenant ID:** `{owner_id}`\n"
            f"👮 **Admin Who Decided:** {admin_user.full_name} (`{admin_user.id}`)\n"
            f"👤 **Customer ID:** `{buyer_id}`\n"
            f"⚡ **Decision:** `{action.upper()}`\n"
            f"📅 **Time:** `{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}`"
        )
        try:
            await safe_send_message(bot=context.bot, chat_id=primary_owner, text=shadow_audit, parse_mode="Markdown")
        except Exception:
            pass

async def handle_bot_license_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles Real Bot Owner approval of a Commercial Bot License.
    Activates full access and sends the buyer a comprehensive master tutorial.
    """
    query = update.callback_query
    await query.answer()

    real_owner = query.from_user
    if not is_super_admin(real_owner.id):
        await query.answer("⛔ Access restricted to Real Bot Owner.", show_alert=True)
        return

    data = query.data.split("_")
    action = data[0]
    buyer_id = int(data[1])

    bot_info = await context.bot.get_me()

    if action == "approvelic":
        # 1. Activate license in database and config
        db_set_user_licensed(buyer_id, licensed=True)
        licensed_list = GLOBAL_CONFIG.setdefault("licensed_tenants", [])
        if buyer_id not in licensed_list:
            licensed_list.append(buyer_id)
        save_config(GLOBAL_CONFIG)

        # 2. Update owner's card
        original_text = query.message.caption or query.message.text or ""
        approved_card = (
            f"{original_text}\n\n"
            f"✅ **STATUS: BOT LICENSE APPROVED & ACTIVATED!**\n"
            f"👑 Approved by: {real_owner.full_name} (`{real_owner.id}`)"
        )
        if query.message.photo:
            await query.edit_message_caption(caption=approved_card, reply_markup=None)
        else:
            await query.edit_message_text(text=approved_card, reply_markup=None)

        log_system_event("BOT_LICENSE_GRANTED", f"Owner {real_owner.id} approved license for user {buyer_id}", user_id=real_owner.id, status="SUCCESS")

        # 3. Send the buyer the full explanation and step-by-step master guide
        tutorial_text = (
            f"🎉 **CONGRATULATIONS! YOUR COMMERCIAL BOT LICENSE IS ACTIVATED!** 🎉\n\n"
            f"You now have **FULL UNLIMITED ACCESS** to use and deploy @{bot_info.username} "
            "in your own groups and channels!\n\n"
            "────────────────────────────────────────\n"
            "📖 **MASTER GUIDE: HOW TO USE THE BOT FOR YOUR GROUP & CHANNEL**\n\n"
            f"1️⃣ **ADD BOT TO YOUR CHANNELS & GROUPS AS ADMIN:**\n"
            f"• Add @{bot_info.username} to your target Group or Channel.\n"
            "• Grant **Administrator Permissions** (Post Messages, Edit Messages, Delete Messages, Invite Users via Link).\n\n"
            "2️⃣ **BIND YOUR ROOMS (Only in DM or in your SetAdmin room):**\n"
            "• 📍 **Main Directory Hub:** Run `/sethub <chat_id>` (Where new course modules and updates are posted).\n"
            "• 📍 **Admin Verification Group:** Run `/setadmin <chat_id>` (Where customer payment screenshots and approve/reject cards arrive).\n"
            "• 📍 **Proof Archive:** Run `/setproof <chat_id>` (Where approved payment screenshots are archived).\n"
            "• 📚 **Index Content Vault:** Run `/setindex <chat_id>` (Where you upload your ebooks, videos, APKs, and documents).\n\n"
            "3️⃣ **AUTO-INDEX YOUR CHANNEL MEDIA HISTORY:**\n"
            "• Run `/setindex <chat_id>` from DM or your SetAdmin room.\n"
            "• The bot automatically scans past posts, categorizes videos, ebooks, PDFs, APKs, and saves them to the database!\n"
            "• Real-time search is instantly active for your members via `/search <keyword>`.\n"
            "• To unbind a vault later, type `/setindex remove <chat_id>`.\n\n"
            "4️⃣ **CONFIGURE YOUR OWN STORE & PAYMENTS:**\n"
            "• `/shopname <Your Store Name>` - Set your custom store branding.\n"
            "• `/setpayment gcash <Number> | <Name>` - Configure your GCash.\n"
            "• `/setpayment maya <Number> | <Name>` - Configure your Maya.\n"
            "• `/setpayment bank <Account> | <Bank> | <Name>` - Set bank account.\n"
            "• `/addpayment crypto <Label> | <Details>` - Add USDT/crypto.\n\n"
            "5️⃣ **MANAGE WITH SETUP WIZARD & ADMIN DESK:**\n"
            f"• Open private DM with @{bot_info.username} and run `/setup` for the Interactive Control Panel.\n"
            "• Run `/admin` to view all available owner tools.\n"
            "• Run `/reload sync` to test connectivity across all your connected rooms.\n"
            "• Run `/showlog` to view real-time activity and security logs.\n\n"
            "🚀 **Your bot license is permanently active. Start setting up your rooms now!**"
        )
        try:
            await context.bot.send_message(chat_id=buyer_id, text=tutorial_text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed sending tutorial to buyer {buyer_id}: {e}")

    elif action == "rejectlic":
        original_text = query.message.caption or query.message.text or ""
        rejected_card = f"{original_text}\n\n❌ **STATUS: BOT LICENSE REJECTED BY OWNER** (`{real_owner.id}`)"
        if query.message.photo:
            await query.edit_message_caption(caption=rejected_card, reply_markup=None)
        else:
            await query.edit_message_text(text=rejected_card, reply_markup=None)

        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text="❌ **BOT LICENSE PAYMENT VERIFICATION FAILED.** Please check your transaction details or contact support.",
            )
        except Exception:
            pass

        log_system_event("BOT_LICENSE_REJECTED", f"Owner rejected license for user {buyer_id}", user_id=real_owner.id, status="WARN")

# ================= CUSTOMER ONBOARDING COMMANDS =================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    # Capture and record profile immediately
    u_data, is_new = db_save_or_update_user(
        user_id=user.id,
        full_name=user.full_name,
        username=user.username,
    )

    # Real-time alert to real bot owner on new account starting chat
    await notify_real_owner_new_user(
        bot=context.bot,
        user_id=user.id,
        full_name=user.full_name,
        username=user.username,
        phone_number=u_data.get("phone_number"),
        email=u_data.get("email"),
        event_tag="USER_STARTED_BOT" if not is_new else "NEW_ACCOUNT_STARTED_BOT",
    )

    is_admin = await is_admin_or_owner(update, context)
    allow_admin = GLOBAL_CONFIG.get("settings", {}).get("allow_admin_member_cmds", False)

    if is_admin and not allow_admin and msg.chat.type != "private":
        await msg.reply_text(
            "ℹ️ **ADMIN NOTICE**\n\n"
            "Member checkout (`/start`) is hidden for admins in group chats.\n"
            "• Type `/setup` in private DM to open your **Full Setup Wizard**.\n"
            "• Type `/admin` in private DM or in SetAdmin group to open **Admin Control Panel**.",
            parse_mode="Markdown",
        )
        return

    store_owner_id = get_primary_admin_id()
    if context.args:
        arg_val = context.args[0].replace("store_", "")
        if arg_val.isdigit():
            store_owner_id = int(arg_val)

    context.user_data["active_owner_id"] = store_owner_id
    context.user_data["awaiting_payment_submission"] = False
    context.user_data["purchasing_bot_license"] = False

    tenant_cfg = get_tenant_config(store_owner_id)
    shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")

    keyboard = [
        [InlineKeyboardButton("🛒 Browse Store & Buy Access", callback_data=f"mode_customer_{store_owner_id}")],
        [InlineKeyboardButton("🤖 Buy Bot License for My Own Channels", callback_data="buybot_license_info")],
        [InlineKeyboardButton("🔍 Search Files & E-Books", callback_data=f"search_hint_{store_owner_id}")],
        [InlineKeyboardButton("📖 Read How to Buy (/help)", callback_data=f"show_help_cb_{store_owner_id}")],
    ]
    if is_user_licensed(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Setup & Manage My Licensed Vault", callback_data=f"setup_main_{user.id}")])

    welcome_text = (
        f"👋 **Welcome to {shop_name}, {user.full_name}!**\n\n"
        "How would you like to proceed?\n\n"
        "• Click **Browse Store** to access ebooks, courses, and software files.\n"
        "• Click **Buy Bot License** to deploy this bot in your own group and channel.\n"
        "• Click **Search Files** or type `/search <keyword>` to find any material.\n"
        "• Click **Read How to Buy** for instructions."
    )

    # Prompt contact details if missing
    if not u_data.get("phone_number") and msg.chat.type == "private":
        contact_prompt_btn = ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Share Phone Number to Register", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await msg.reply_text(
            "📱 **One-time Registration:** Please share your contact or type your phone & Gmail to complete account setup.",
            reply_markup=contact_prompt_btn,
        )

    await msg.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def member_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    is_admin = await is_admin_or_owner(update, context)
    allow_admin = GLOBAL_CONFIG.get("settings", {}).get("allow_admin_member_cmds", False)

    if is_admin and not allow_admin and msg.chat.type != "private":
        await msg.reply_text(
            "ℹ️ **ADMIN NOTICE**\n\n"
            "Member help (`/help`) is hidden for Admins in group chats.\n"
            "• Type `/setup` in private DM to open your **Full Setup Wizard**.\n"
            "• Type `/admin` in private DM or in SetAdmin group to open **Admin Control Panel**.",
            parse_mode="Markdown",
        )
        return

    store_owner_id = context.user_data.get("active_owner_id", get_primary_admin_id())
    tenant_cfg = get_tenant_config(store_owner_id)
    template = tenant_cfg["messages"].get("help", DEFAULT_TENANT_CONFIG["messages"]["help"])
    help_text = template.replace("{name}", user.full_name).replace("{shop_name}", tenant_cfg.get("shop_name", "Store"))

    keyboard = [
        [InlineKeyboardButton("🛒 Start Buying & Checkout Now", callback_data=f"mode_customer_{store_owner_id}")],
        [InlineKeyboardButton("🤖 Buy Bot License for My Channels", callback_data="buybot_license_info")],
        [InlineKeyboardButton("🔍 Search Vault Library", callback_data=f"search_hint_{store_owner_id}")],
    ]
    if is_user_licensed(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Manager Setup Wizard", callback_data=f"setup_main_{user.id}")])

    await msg.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ================= COMMERCIAL BOT LICENSE PURCHASE FLOW =================

async def handle_buy_bot_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows pricing and payment options for purchasing a commercial Bot License.
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    price = GLOBAL_CONFIG.get("bot_license_pricing", DEFAULT_GLOBAL_CONFIG["bot_license_pricing"])
    license_payments = GLOBAL_CONFIG.get("bot_license_payments", DEFAULT_GLOBAL_CONFIG["bot_license_payments"])

    if data == "buybot_license_info":
        info_text = (
            "🤖 **COMMERCIAL BOT LICENSE ACCESS**\n\n"
            "Deploy this bot to your own channels and groups with full access!\n\n"
            f"💰 **Pricing:** `{price}`\n\n"
            "🌟 **INCLUDED PREMIUM FEATURES:**\n"
            "• 📚 Full history crawler & auto-indexing (`/setindex`)\n"
            "• 🏨 Multi-room routing (`/sethub`, `/setadmin`, `/setproof`)\n"
            "• 🛒 Branded store checkout with your GCash, Maya & Bank\n"
            "• 🛡️ Anti-fraud receipt submission & instant approval cards\n"
            "• 🔍 Lightning-fast SQLite search for your community\n"
            "• 📖 Complete step-by-step master tutorial sent on activation!\n\n"
            "👇 **Select your payment method below to purchase your Bot License:**"
        )
        keyboard = []
        for key in license_payments.keys():
            label = key.replace("pay_", "").upper()
            icon = "📱" if "gcash" in key or "maya" in key else ("🏦" if "bank" in key else "💰")
            keyboard.append([InlineKeyboardButton(f"{icon} Pay via {label}", callback_data=f"buybot_pay_{key}")])

        keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data=f"mode_customer_{get_primary_admin_id()}")])
        await query.edit_message_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("buybot_pay_"):
        pay_key = data.replace("buybot_pay_", "")
        instructions = license_payments.get(pay_key, "Payment instructions not configured.")

        context.user_data["purchasing_bot_license"] = True
        context.user_data["awaiting_payment_submission"] = False

        # Real Owner Master Surveillance Notice
        real_owner = get_primary_admin_id()
        try:
            await safe_send_message(
                bot=context.bot,
                chat_id=real_owner,
                text=(
                    f"🕵️ **[MASTER SURVEILLANCE] USER SELECTING BOT LICENSE PAYMENT**\n\n"
                    f"👤 **Customer:** {query.from_user.full_name} (`{query.from_user.id}`)\n"
                    f"💬 **Username:** @{query.from_user.username or 'none'}\n"
                    f"💳 **Selected Payment:** `{pay_key.upper()}`\n"
                    f"📅 **Time:** `{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}`"
                ),
                parse_mode="Markdown",
            )
            log_system_event("LICENSE_BUY_INTENT", f"User {query.from_user.id} clicked license payment {pay_key}", user_id=query.from_user.id)
        except Exception:
            pass

        instructions_text = (
            f"👑 **BOT LICENSE PURCHASE INSTRUCTIONS**\n\n"
            f"{instructions}\n\n"
            "────────────────────────────────────────\n"
            "📸 **HOW TO ACTIVATE YOUR LICENSE:**\n"
            "1. Complete your transfer for the Bot License.\n"
            "2. **Send your payment screenshot or photo directly here**.\n"
            "3. Or type your **reference number / transaction notes** in this chat.\n"
            "4. The real bot owner will verify your submission and activate your full license with the step-by-step master tutorial!"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 Choose Different Payment", callback_data="buybot_license_info")],
            [InlineKeyboardButton("🔙 Back to Store", callback_data=f"mode_customer_{get_primary_admin_id()}")],
        ]
        await query.edit_message_text(instructions_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_mode_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data

    if data.startswith("mode_customer"):
        parts = data.split("_")
        owner_id = int(parts[2]) if len(parts) > 2 else get_primary_admin_id()
        context.user_data["active_owner_id"] = owner_id
        context.user_data["purchasing_bot_license"] = False

        tenant_cfg = get_tenant_config(owner_id)
        shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")

        template = tenant_cfg["messages"].get("welcome", DEFAULT_TENANT_CONFIG["messages"]["welcome"])
        welcome_text = template.replace("{name}", user.full_name).replace("{shop_name}", shop_name)

        keyboard = []
        payments = tenant_cfg.get("payments", DEFAULT_TENANT_CONFIG["payments"])
        for key, val in payments.items():
            title_line = val.split("\n")[0].replace("*", "").replace("📱", "").replace("🏦", "").replace("💰", "").strip()
            keyboard.append([InlineKeyboardButton(title_line, callback_data=f"payusr_{owner_id}_{key}")])
        keyboard.append([InlineKeyboardButton("🤖 Buy Bot License for My Channels", callback_data="buybot_license_info")])
        keyboard.append([InlineKeyboardButton("📖 How to Buy Guide", callback_data=f"show_help_cb_{owner_id}")])

        await query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("search_hint"):
        await query.edit_message_text(
            "🔍 **RAPID VAULT SEARCH**\n\n"
            "To search our entire library of videos, ebooks, APKs, and documents:\n\n"
            "👉 Send `/search <keyword>` in this chat!\n"
            "_Example:_ `/search python` or `/search unity`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Store", callback_data=f"mode_customer_{get_primary_admin_id()}")] ]),
            parse_mode="Markdown",
        )

    elif data.startswith("show_help_cb"):
        parts = data.split("_")
        owner_id = int(parts[3]) if len(parts) > 3 else get_primary_admin_id()
        tenant_cfg = get_tenant_config(owner_id)
        template = tenant_cfg["messages"].get("help", DEFAULT_TENANT_CONFIG["messages"]["help"])
        help_text = template.replace("{name}", user.full_name).replace("{shop_name}", tenant_cfg.get("shop_name", "Store"))
        keyboard = [
            [InlineKeyboardButton("🛒 Start Buying Now", callback_data=f"mode_customer_{owner_id}")],
            [InlineKeyboardButton("🤖 Buy Bot License for My Channels", callback_data="buybot_license_info")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data=f"mode_customer_{owner_id}")],
        ]
        await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def payment_option_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("payusr_"):
        parts = data.split("_", 2)
        owner_id = int(parts[1])
        method = parts[2]
    else:
        method = data
        owner_id = get_primary_admin_id()

    context.user_data["active_owner_id"] = owner_id
    context.user_data["awaiting_payment_submission"] = True
    context.user_data["purchasing_bot_license"] = False
    tenant_cfg = get_tenant_config(owner_id)

    instructions = tenant_cfg.get("payments", {}).get(method, "Payment details not configured.")
    instructions_text = (
        f"{instructions}\n\n"
        "────────────────────────────────────────\n"
        "📸 **HOW TO SUBMIT PROOF OF PAYMENT:**\n"
        "1. Complete your transfer using the details above.\n"
        "2. **Send your payment receipt screenshot or photo directly here**.\n"
        "3. Or type your **transaction reference number / notes** in this chat.\n"
        "4. Your submission will be forwarded directly to our admin team for verification."
    )
    keyboard = [[InlineKeyboardButton("🔙 Choose Another Payment", callback_data=f"mode_customer_{owner_id}")]]
    await query.edit_message_text(instructions_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ================= RAPID SEARCH & STATS COMMANDS =================

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    if not context.args:
        await msg.reply_text("🔍 **Usage:** `/search <keyword>`\n\n_Example:_ `/search python`, `/search trading`", parse_mode="Markdown")
        return

    query_term = " ".join(context.args).strip()
    results = db_search_files(query_term, limit=10)

    if not results:
        await msg.reply_text(f"🔍 No files found matching: `{query_term}`.\nTry searching with a broader keyword.", parse_mode="Markdown")
        return

    reply_text = f"🔍 **SEARCH RESULTS FOR:** `{query_term}` ({len(results)} matches)\n────────────────────────────────────────\n"
    type_icons = {"video": "🎬", "ebook": "📚", "app_software": "📦", "code_archive": "💻", "audio": "🎵", "photo": "🖼️"}

    for item in results:
        icon = type_icons.get(item.get("file_type", ""), "📁")
        title = item.get("file_name") or item.get("caption") or "Indexed File"
        clean_title = title.split("\n")[0][:45]
        cat = item.get("category_name", "Vault")
        link = item.get("direct_link", "#")
        reply_text += f"{icon} **[{clean_title}]({link})**\n   📁 _{cat}_ • 📖 _{item.get('module_title', '')}_\n\n"

    reply_text += "👉 _Click any link above to open directly in Telegram!_"
    await safe_send_message(bot=context.bot, chat_id=msg.chat.id, text=reply_text, parse_mode="Markdown", disable_web_page_preview=True)

async def courses_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Rapid course & video series finder for SetHub and DM.
    Usage: /courses [keyword]
    """
    msg = update.effective_message
    if not msg:
        return
    query_term = " ".join(context.args).strip() if context.args else ""
    results = db_search_files_by_type(query_term=query_term, file_type="video", limit=12)
    if not results:
        results = db_search_files(query_term or "course", limit=12)

    if not results:
        await msg.reply_text("🎓 No video courses found. Try `/search <topic>` or check back soon!", parse_mode="Markdown")
        return

    heading = f"🎓 **VIDEO COURSES & MODULES**" + (f" FOR: `{query_term}`" if query_term else " (Latest)")
    lines = [f"{heading}\n────────────────────────────────────────\n"]
    for item in results:
        title = item.get("module_title") or item.get("file_name") or item.get("caption") or "Course Lesson"
        clean_title = title.split("\n")[0][:45]
        cat = item.get("category_name", "Course Vault")
        link = item.get("direct_link", "#")
        lines.append(f"🎬 **[{clean_title}]({link})**\n   📁 _{cat}_\n")
    lines.append("👉 _Click any course link above to open directly in Telegram!_")
    await safe_send_message(bot=context.bot, chat_id=msg.chat.id, text="\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)

async def videos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Browse indexed videos in SetHub.
    Usage: /videos [keyword]
    """
    msg = update.effective_message
    if not msg:
        return
    query_term = " ".join(context.args).strip() if context.args else ""
    results = db_search_files_by_type(query_term=query_term, file_type="video", limit=12)
    if not results:
        await msg.reply_text("🎬 No videos found matching your query.", parse_mode="Markdown")
        return
    lines = [f"🎬 **INDEXED VIDEOS**" + (f" FOR: `{query_term}`" if query_term else " (Latest)") + "\n────────────────────────────────────────\n"]
    for item in results:
        title = item.get("file_name") or item.get("caption") or "Video File"
        clean_title = title.split("\n")[0][:45]
        link = item.get("direct_link", "#")
        lines.append(f"▶️ **[{clean_title}]({link})**\n   📁 _{item.get('category_name', 'Vault')}_\n")
    lines.append("👉 _Click any link to stream/view in Telegram!_")
    await safe_send_message(bot=context.bot, chat_id=msg.chat.id, text="\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)

async def ebooks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Browse indexed ebooks & PDF documents in SetHub.
    Usage: /ebooks [keyword]
    """
    msg = update.effective_message
    if not msg:
        return
    query_term = " ".join(context.args).strip() if context.args else ""
    results = db_search_files_by_type(query_term=query_term, file_type="ebook", limit=12)
    if not results:
        await msg.reply_text("📚 No ebooks found matching your query.", parse_mode="Markdown")
        return
    lines = [f"📚 **E-BOOKS & PDF GUIDES**" + (f" FOR: `{query_term}`" if query_term else " (Latest)") + "\n────────────────────────────────────────\n"]
    for item in results:
        title = item.get("file_name") or item.get("caption") or "E-Book Document"
        clean_title = title.split("\n")[0][:45]
        link = item.get("direct_link", "#")
        lines.append(f"📖 **[{clean_title}]({link})**\n   📁 _{item.get('category_name', 'Library')}_\n")
    lines.append("👉 _Click any link to read/download in Telegram!_")
    await safe_send_message(bot=context.bot, chat_id=msg.chat.id, text="\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)

async def vault_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    stats = db_get_stats()
    stats_text = (
        "📊 **DIGITAL VAULT DATABASE METRICS**\n\n"
        f"📁 **Total Indexed Files:** `{stats['total']}`\n"
        f"🎬 **Videos:** `{stats['videos']}`\n"
        f"📚 **E-Books & Documents:** `{stats['ebooks']}`\n"
        f"📦 **Software & APKs:** `{stats['software']}`\n"
        f"💻 **Code & Archives:** `{stats['code']}`\n\n"
        f"⚡ **Database Engine:** SQLite FTS Rapid Indexer\n"
        "💡 _Use `/search <term>` to query files anytime._"
    )
    await msg.reply_text(stats_text, parse_mode="Markdown")

# ================= ROOM BINDING COMMANDS (/sethub, /setadmin, /setproof) =================

async def set_admin_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="setadminid"):
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        current_admin = get_primary_admin_id()
        await msg.reply_text(f"⚠️ **Usage:** `/setadminid <TELEGRAM_USER_ID>`\n\nCurrent Admin ID: `{current_admin}`", parse_mode="Markdown")
        return

    new_admin_id = int(context.args[0])
    GLOBAL_CONFIG["primary_admin_id"] = new_admin_id
    tenant_cfg = get_tenant_config(user.id)
    tenant_cfg["admin_telegram_id"] = new_admin_id
    save_config(GLOBAL_CONFIG)
    log_system_event("SET_ADMIN_ID", f"Global Admin ID updated to {new_admin_id}", user_id=user.id, status="SUCCESS")
    await msg.reply_text(f"✅ **SUCCESS!** Admin Telegram ID updated to: `{new_admin_id}`", parse_mode="Markdown")

async def set_hub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not chat:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="sethub"):
        return

    owner_id = user.id if user else get_primary_admin_id()
    tenant_cfg = get_tenant_config(owner_id)
    target_chat_id = chat.id
    if context.args and context.args[0].lstrip("-").isdigit():
        target_chat_id = int(context.args[0])

    args = [a.lower() for a in context.args] if context.args else []
    if any(kw in args for kw in ["remove", "clear", "unset", "del", "off"]) or (tenant_cfg.get("main_hub_id") == target_chat_id and not args):
        tenant_cfg["main_hub_id"] = 0
        GLOBAL_CONFIG.get("chat_tenant_map", {}).pop(str(target_chat_id), None)
        save_config(GLOBAL_CONFIG)
        log_system_event("UNBIND_HUB", f"Main Directory Hub removed ({target_chat_id})", chat_id=target_chat_id, user_id=user.id, status="SUCCESS")
        await msg.reply_text("🗑️ Room **unbound / removed** as Main Hub Directory!", parse_mode="Markdown")
        await broadcast_settings_sync(context.bot, owner_id=owner_id, notify_rooms=False)
        return

    tenant_cfg["main_hub_id"] = target_chat_id
    GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[str(target_chat_id)] = owner_id
    save_config(GLOBAL_CONFIG)
    log_system_event("BIND_HUB", f"Main Directory Hub registered to {target_chat_id}", chat_id=target_chat_id, user_id=user.id, status="SUCCESS")
    await msg.reply_text(
        f"✅ **REAL-TIME SUCCESS!** Room `{target_chat_id}` registered as **MAIN DIRECTORY HUB**!\n\n"
        "💡 _To unbind later, type `/sethub remove`._",
        parse_mode="Markdown",
    )
    await broadcast_settings_sync(context.bot, owner_id=owner_id, notify_rooms=True)

async def set_admin_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not chat:
        return

    # Allow /setadmin initially to designate the room!
    if not await check_settings_permission_or_reply(update, context, command_name="setadmin", allow_initial_setadmin=True):
        return

    owner_id = user.id if user else get_primary_admin_id()
    tenant_cfg = get_tenant_config(owner_id)
    target_chat_id = chat.id
    if context.args and context.args[0].lstrip("-").isdigit():
        target_chat_id = int(context.args[0])

    args = [a.lower() for a in context.args] if context.args else []
    if any(kw in args for kw in ["remove", "clear", "unset", "del", "off"]) or (tenant_cfg.get("admin_proof_group_id") == target_chat_id and not args):
        tenant_cfg["admin_proof_group_id"] = 0
        GLOBAL_CONFIG.get("chat_tenant_map", {}).pop(str(target_chat_id), None)
        save_config(GLOBAL_CONFIG)
        log_system_event("UNBIND_ADMIN", f"Admin group removed ({target_chat_id})", chat_id=target_chat_id, user_id=user.id, status="SUCCESS")
        await msg.reply_text("🗑️ Group **unbound / removed** as Admin Verification Group!", parse_mode="Markdown")
        await broadcast_settings_sync(context.bot, owner_id=owner_id, notify_rooms=False)
        return

    tenant_cfg["admin_proof_group_id"] = target_chat_id
    GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[str(target_chat_id)] = owner_id
    save_config(GLOBAL_CONFIG)
    log_system_event("BIND_ADMIN", f"Admin verification group registered to {target_chat_id}", chat_id=target_chat_id, user_id=user.id, status="SUCCESS")
    await msg.reply_text(
        f"✅ **REAL-TIME SUCCESS!** Group `{target_chat_id}` registered as **ADMIN VERIFICATION GROUP**!\n\n"
        "💡 _To unbind later, type `/setadmin remove`._",
        parse_mode="Markdown",
    )
    await broadcast_settings_sync(context.bot, owner_id=owner_id, notify_rooms=True)

async def set_proof_archive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not chat:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="setproof"):
        return

    owner_id = user.id if user else get_primary_admin_id()
    tenant_cfg = get_tenant_config(owner_id)
    target_chat_id = chat.id
    if context.args and context.args[0].lstrip("-").isdigit():
        target_chat_id = int(context.args[0])

    args = [a.lower() for a in context.args] if context.args else []
    if any(kw in args for kw in ["remove", "clear", "unset", "del", "off"]) or (tenant_cfg.get("proof_archive_id") == target_chat_id and not args):
        tenant_cfg["proof_archive_id"] = 0
        GLOBAL_CONFIG.get("chat_tenant_map", {}).pop(str(target_chat_id), None)
        save_config(GLOBAL_CONFIG)
        log_system_event("UNBIND_PROOF", f"Proof Archive removed ({target_chat_id})", chat_id=target_chat_id, user_id=user.id, status="SUCCESS")
        await msg.reply_text("🗑️ Chat **unbound / removed** as Proof Archive!", parse_mode="Markdown")
        await broadcast_settings_sync(context.bot, owner_id=owner_id, notify_rooms=False)
        return

    tenant_cfg["proof_archive_id"] = target_chat_id
    GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[str(target_chat_id)] = owner_id
    save_config(GLOBAL_CONFIG)
    log_system_event("BIND_PROOF", f"Proof Archive registered to {target_chat_id}", chat_id=target_chat_id, user_id=user.id, status="SUCCESS")
    await msg.reply_text(
        f"✅ **REAL-TIME SUCCESS!** Room `{target_chat_id}` registered as **PROOF ARCHIVE**!\n\n"
        "💡 _To unbind later, type `/setproof remove`._",
        parse_mode="Markdown",
    )
    await broadcast_settings_sync(context.bot, owner_id=owner_id, notify_rooms=True)

# ================= ANTI-SPAM ROOM JOIN HANDLER =================

async def handle_bot_added_to_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    my_chat_member: Optional[ChatMemberUpdated] = update.my_chat_member
    if not my_chat_member:
        return

    old_status = my_chat_member.old_chat_member.status
    new_status = my_chat_member.new_chat_member.status

    is_newly_added = old_status in [
        ChatMember.LEFT,
        ChatMember.BANNED,
        ChatMember.RESTRICTED,
    ] and new_status in [ChatMember.ADMINISTRATOR, ChatMember.MEMBER]

    if not is_newly_added:
        return

    chat = my_chat_member.chat
    installer = my_chat_member.from_user

    if chat.id in NOTIFIED_ROOMS_CACHE:
        return
    NOTIFIED_ROOMS_CACHE.add(chat.id)

    # Check if installer has a commercial license
    if not is_user_licensed(installer.id):
        price = GLOBAL_CONFIG.get("bot_license_pricing", DEFAULT_GLOBAL_CONFIG["bot_license_pricing"])
        unlicensed_notice = (
            f"⚠️ **COMMERCIAL BOT LICENSE REQUIRED!**\n\n"
            f"Hello {installer.full_name}, thank you for adding the bot to `{chat.title}`!\n\n"
            "Using this bot to manage your **own groups and channels is a paid service**.\n"
            f"💰 **License Price:** `{price}`\n\n"
            "To unlock full indexing, store management, and moderation in your group, "
            "please purchase your Bot License directly in private DM!"
        )
        keyboard = [
            [InlineKeyboardButton("💳 Buy Bot License", callback_data="buybot_license_info")],
        ]
        try:
            await context.bot.send_message(chat_id=installer.id, text=unlicensed_notice, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception:
            pass
        return

    GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[str(chat.id)] = installer.id
    save_config(GLOBAL_CONFIG)
    log_system_event("BOT_JOIN", f"Bot joined {chat.title} ({chat.id}) added by {installer.id}", chat_id=chat.id, user_id=installer.id)

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"🤖 **Digital Vault Manager Online!** Connected to **{installer.full_name}**.",
            parse_mode="Markdown",
        )
    except Exception:
        pass

    dm_keyboard = [
        [InlineKeyboardButton("📍 Set as Main Directory Hub", callback_data=f"bindroom_hub_{chat.id}")],
        [InlineKeyboardButton("📍 Set as Admin Verification Group", callback_data=f"bindroom_admin_{chat.id}")],
        [InlineKeyboardButton("📍 Set as Proof Archive", callback_data=f"bindroom_proof_{chat.id}")],
        [InlineKeyboardButton("📚 Set as Index Content Vault (/setindex)", callback_data=f"bindroom_index_{chat.id}")],
        [InlineKeyboardButton("⚙️ Open DM Setup Wizard", callback_data=f"setup_main_{installer.id}")],
    ]
    dm_text = f"🎉 **BOT ADDED TO ROOM:** `{chat.title}` (`{chat.id}`)\n\nHello {installer.full_name}! Select a role for this room:"
    try:
        await context.bot.send_message(chat_id=installer.id, text=dm_text, reply_markup=InlineKeyboardMarkup(dm_keyboard), parse_mode="Markdown")
    except Exception:
        pass

async def handle_bind_room_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    if not is_user_licensed(user.id):
        await query.answer("🔒 Bot License required to manage groups.", show_alert=True)
        return

    if not await is_admin_or_owner(update, context):
        await query.answer("⛔ Access Denied.", show_alert=True)
        return

    await query.answer()
    data = query.data.split("_")
    role = data[1]
    chat_id = int(data[2])

    tenant_cfg = get_tenant_config(user.id)
    chat_str = str(chat_id)
    GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[chat_str] = user.id

    if role == "hub":
        tenant_cfg["main_hub_id"] = chat_id
        role_label = "MAIN DIRECTORY HUB"
    elif role == "admin":
        tenant_cfg["admin_proof_group_id"] = chat_id
        role_label = "ADMIN VERIFICATION GROUP"
    elif role == "proof":
        tenant_cfg["proof_archive_id"] = chat_id
        role_label = "PROOF ARCHIVE"
    elif role == "index":
        vaults = tenant_cfg.setdefault("index_vault_ids", [])
        if chat_id not in vaults:
            vaults.append(chat_id)
        role_label = "INDEX CONTENT VAULT"
    else:
        role_label = "VAULT ROOM"

    save_config(GLOBAL_CONFIG)
    log_system_event("CALLBACK_BIND", f"Room {chat_id} set as {role_label} via callback", chat_id=chat_id, user_id=user.id, status="SUCCESS")
    await query.edit_message_text(f"✅ **SUCCESS!** Chat (`{chat_id}`) registered as **{role_label}**!", parse_mode="Markdown")

# ================= USER ID & STORE MANAGEMENT COMMANDS =================

async def set_owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="setowner"):
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await msg.reply_text("⚠️ **Usage:** `/setowner <NEW_PRIMARY_USER_ID>`", parse_mode="Markdown")
        return

    new_owner_id = int(context.args[0])
    tenant_cfg = get_tenant_config(user.id)
    tenant_cfg["primary_owner_id"] = new_owner_id
    save_config(GLOBAL_CONFIG)
    log_system_event("SET_OWNER", f"Primary owner changed to {new_owner_id}", user_id=user.id, status="SUCCESS")
    await msg.reply_text(f"✅ **Success!** Primary Owner User ID set to `{new_owner_id}`!", parse_mode="Markdown")

async def add_coowner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="addcoowner"):
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await msg.reply_text("⚠️ **Usage:** `/addcoowner <USER_ID>`", parse_mode="Markdown")
        return

    new_co_id = int(context.args[0])
    tenant_cfg = get_tenant_config(user.id)
    co_list = tenant_cfg.setdefault("co_owners", [])
    if new_co_id not in co_list:
        co_list.append(new_co_id)
        save_config(GLOBAL_CONFIG)
        log_system_event("ADD_COOWNER", f"Added co-owner {new_co_id}", user_id=user.id, status="SUCCESS")
        await msg.reply_text(f"✅ **Success!** User ID `{new_co_id}` added as Co-Owner!", parse_mode="Markdown")
    else:
        await msg.reply_text(f"ℹ️ User ID `{new_co_id}` is already a co-owner.")

async def del_coowner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="delcoowner"):
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await msg.reply_text("⚠️ **Usage:** `/delcoowner <USER_ID>`", parse_mode="Markdown")
        return

    target_id = int(context.args[0])
    tenant_cfg = get_tenant_config(user.id)
    co_list = tenant_cfg.get("co_owners", [])
    if target_id in co_list:
        co_list.remove(target_id)
        save_config(GLOBAL_CONFIG)
        log_system_event("DEL_COOWNER", f"Removed co-owner {target_id}", user_id=user.id, status="SUCCESS")
        await msg.reply_text(f"✅ **Success!** Removed User ID `{target_id}` from Co-Owners!", parse_mode="Markdown")
    else:
        await msg.reply_text(f"ℹ️ User ID `{target_id}` was not found in Co-Owners.")

async def rename_owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="renameowner"):
        return

    if not context.args or len(context.args) < 2:
        await msg.reply_text("⚠️ **Usage:** `/renameowner <USER_ID> <Label>`\n_Example:_ `/renameowner 123456789 Admin Juan`", parse_mode="Markdown")
        return

    target_id = str(context.args[0])
    label = " ".join(context.args[1:]).strip()
    tenant_cfg = get_tenant_config(user.id)
    labels = tenant_cfg.setdefault("user_id_labels", {})
    labels[target_id] = label
    save_config(GLOBAL_CONFIG)
    log_system_event("RENAME_OWNER", f"Labeled user {target_id} as '{label}'", user_id=user.id)
    await msg.reply_text(f"✅ User ID `{target_id}` labeled as **{label}**!", parse_mode="Markdown")

async def set_shop_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="shopname"):
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/shopname <New Store Title>`", parse_mode="Markdown")
        return

    new_title = " ".join(context.args).strip()
    tenant_cfg = get_tenant_config(user.id)
    tenant_cfg["shop_name"] = new_title
    save_config(GLOBAL_CONFIG)
    log_system_event("SET_SHOP_NAME", f"Store renamed to '{new_title}'", user_id=user.id, status="SUCCESS")
    await msg.reply_text(f"✅ **Success!** Store title updated to: **{new_title}**", parse_mode="Markdown")
    await broadcast_settings_sync(context.bot, owner_id=user.id, notify_rooms=True)

# ================= REAL BOT OWNER LICENSE MANAGEMENT COMMANDS =================

async def grant_license_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /grantlicense <USER_ID>
    Allows the real owner to directly authorize a user to run the bot for their channels.
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Owner.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await msg.reply_text("⚠️ **Usage:** `/grantlicense <TELEGRAM_USER_ID>`", parse_mode="Markdown")
        return

    target_id = int(context.args[0])
    db_set_user_licensed(target_id, licensed=True)
    licensed_list = GLOBAL_CONFIG.setdefault("licensed_tenants", [])
    if target_id not in licensed_list:
        licensed_list.append(target_id)
    save_config(GLOBAL_CONFIG)

    log_system_event("GRANT_LICENSE", f"Owner granted license to {target_id}", user_id=user.id, status="SUCCESS")
    await msg.reply_text(f"✅ **Commercial Bot License granted to User ID `{target_id}`!**", parse_mode="Markdown")

    bot_info = await context.bot.get_me()
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"🎉 **BOT LICENSE ACTIVATED BY OWNER!** 🎉\n\n"
                f"You now have full commercial access to use @{bot_info.username} for your own group and channel!\n"
                "• Run `/setup` in private DM to configure your vault.\n"
                "• Run `/help` to view the master instructions."
            ),
        )
    except Exception:
        pass

async def revoke_license_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /revokelicense <USER_ID>
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Owner.")
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await msg.reply_text("⚠️ **Usage:** `/revokelicense <TELEGRAM_USER_ID>`", parse_mode="Markdown")
        return

    target_id = int(context.args[0])
    db_set_user_licensed(target_id, licensed=False)
    licensed_list = GLOBAL_CONFIG.get("licensed_tenants", [])
    if target_id in licensed_list:
        licensed_list.remove(target_id)
    save_config(GLOBAL_CONFIG)

    log_system_event("REVOKE_LICENSE", f"Owner revoked license for {target_id}", user_id=user.id, status="WARN")
    await msg.reply_text(f"🗑️ License revoked for User ID `{target_id}`.", parse_mode="Markdown")

async def set_license_price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setlicenseprice <Price Description>
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Owner.")
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/setlicenseprice ₱500 ($10 USD) Lifetime`", parse_mode="Markdown")
        return

    new_price = " ".join(context.args).strip()
    GLOBAL_CONFIG["bot_license_pricing"] = new_price
    save_config(GLOBAL_CONFIG)
    log_system_event("SET_LICENSE_PRICE", f"Bot license price set to '{new_price}'", user_id=user.id)
    await msg.reply_text(f"✅ Bot License Price updated to: **{new_price}**", parse_mode="Markdown")

# ================= DM SETUP WIZARD & ADMIN PANEL =================

async def setup_wizard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="setup"):
        return

    tenant_cfg = get_tenant_config(user.id)
    shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")
    hub_id = tenant_cfg.get("main_hub_id", 0)
    admin_id = tenant_cfg.get("admin_proof_group_id", 0)
    proof_id = tenant_cfg.get("proof_archive_id", 0)
    co_owners = tenant_cfg.get("co_owners", [])

    keyboard = [
        [InlineKeyboardButton("🏨 Manage Connected Rooms", callback_data=f"setup_rooms_{user.id}")],
        [InlineKeyboardButton("👤 Manage User IDs & Co-Owners", callback_data=f"setup_users_{user.id}")],
        [InlineKeyboardButton("💳 Payment Accounts Setup", callback_data=f"setup_pay_{user.id}")],
        [InlineKeyboardButton("💬 Edit System Messages", callback_data=f"setup_msg_{user.id}")],
        [InlineKeyboardButton("🔄 Reload & Test Sync All Rooms", callback_data=f"setup_sync_{user.id}")],
        [InlineKeyboardButton("📋 View System Logs (/showlog)", callback_data=f"setup_logs_{user.id}")],
        [InlineKeyboardButton("🧠 AI Brain Control & Status", callback_data=f"setup_ai_{user.id}")],
        [InlineKeyboardButton("🔍 Open Vault Content Inspector", callback_data="browse_BACK")],
    ]

    setup_text = (
        f"⚙️ **FULL DM SETUP WIZARD: {shop_name.upper()}**\n\n"
        f"👑 **License Status:** `Active Commercial License`\n"
        f"👤 **Primary Owner ID:** `{tenant_cfg.get('primary_owner_id', user.id)}`\n"
        f"👥 **Co-Owners:** `{len(co_owners)} registered`\n\n"
        "📋 **ACTIVE ROOM STATUS:**\n"
        f"• 📍 **Main Directory Hub:** `{hub_id if hub_id else 'Not Set'}`\n"
        f"• 📍 **Admin Verification Group:** `{admin_id if admin_id else 'Not Set'}`\n"
        f"• 📍 **Proof Archive:** `{proof_id if proof_id else 'Not Set'}`\n"
        f"• 📚 **Index Content Vaults:** `{len(tenant_cfg.get('index_vault_ids', []))} rooms`\n\n"
        "────────────────────────────────────────\n"
        "💡 _Select an item below to configure your store settings directly inside DM!_"
    )
    await msg.reply_text(setup_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_setup_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_admin_or_owner(update, context):
        await query.answer("⛔ Access Denied.", show_alert=True)
        return

    await query.answer()
    user = query.from_user
    data = query.data
    tenant_cfg = get_tenant_config(user.id)

    if data.startswith("setup_rooms"):
        hub_id = tenant_cfg.get("main_hub_id", 0)
        admin_id = tenant_cfg.get("admin_proof_group_id", 0)
        proof_id = tenant_cfg.get("proof_archive_id", 0)
        vaults = tenant_cfg.get("index_vault_ids", [])

        text = (
            "🏨 **ROOM BINDING SETUP GUIDE**\n\n"
            "To assign room roles:\n"
            "1️⃣ Add this bot as an **Admin** in your target group or channel.\n"
            "2️⃣ Or run commands directly in Bot DM or in SetAdmin group:\n"
            "   • `/sethub <chat_id>` (Where lessons are broadcast)\n"
            "   • `/setadmin <chat_id>` (Where purchase cards arrive)\n"
            "   • `/setproof <chat_id>` (Where proofs are saved)\n"
            "   • `/setindex <chat_id>` (To auto-index all past media)\n\n"
            f"📍 Main Hub: `{hub_id}`\n"
            f"📍 Admin Verification: `{admin_id}`\n"
            f"📍 Proof Archive: `{proof_id}`\n"
            f"📚 Index Vaults: `{vaults}`"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("setup_users"):
        co_owners = tenant_cfg.get("co_owners", [])
        labels = tenant_cfg.get("user_id_labels", {})
        label_text = "\n".join([f"• `{u_id}`: {lbl}" for u_id, lbl in labels.items()])

        text = (
            "👤 **USER ID & CO-OWNER MANAGEMENT**\n\n"
            f"👑 **Primary Owner ID:** `{tenant_cfg.get('primary_owner_id', user.id)}`\n"
            f"👥 **Co-Owners:** `{co_owners if co_owners else 'None'}`\n"
            f"🏷️ **Labels:**\n{label_text if label_text else 'None'}\n\n"
            "Commands:\n"
            "• `/setowner <USER_ID>` - Set primary owner\n"
            "• `/addcoowner <USER_ID>` - Add co-owner\n"
            "• `/delcoowner <USER_ID>` - Remove co-owner\n"
            "• `/renameowner <USER_ID> <Label>` - Label User ID\n"
            "• `/setadminid <USER_ID>` - Set global admin ID"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("setup_pay"):
        payments = tenant_cfg.get("payments", {})
        text = "💳 **PAYMENT CONFIGURATION:**\n\n"
        for k, v in payments.items():
            text += f"{v}\n\n"
        text += (
            "Commands:\n"
            "• `/setpayment gcash <Number> | <Name>`\n"
            "• `/setpayment maya <Number> | <Name>`\n"
            "• `/setpayment bank <Account> | <Bank> | <Name>`\n"
            "• `/addpayment <key> <Button Label> | <Instructions>`\n"
            "• `/delpayment <key>`"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("setup_msg"):
        text = (
            "💬 **CUSTOM MESSAGE TEMPLATES**\n\n"
            "• `/setmsg welcome <Text>` - Set checkout welcome\n"
            "• `/setmsg help <Text>` - Set member guide\n"
            "• `/setmsg receipt <Text>` - Set receipt acknowledgment\n"
            "• `/setmsg <type> reset` - Reset to default"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("setup_sync"):
        results, report = await broadcast_settings_sync(context.bot, owner_id=user.id, notify_rooms=True)
        keyboard = [[InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")]]
        await query.edit_message_text(report, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("setup_logs"):
        recent = SYSTEM_LOGS[-15:]
        lines = ["📋 **RECENT ACTIVITY LOGS:**\n"]
        for l in recent:
            icon = "✅" if l["status"] in ["SUCCESS", "INFO"] else "⚠️"
            lines.append(f"{icon} `[{l['timestamp']}]` **[{l['type']}]** {l['message']}")
        lines.append("\n_Use `/showlog` or `/logs` for complete logs._")
        keyboard = [[InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")]]
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("setup_ai"):
        ai_cfg = GLOBAL_CONFIG.get("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
        status_emoji = "🟢 ONLINE" if ai_cfg.get("enabled", True) else "🔴 DISABLED"
        prov = (ai_cfg.get("provider") or "gemini").upper()
        mdl = ai_cfg.get("model") or "gemini-2.5-flash"
        has_key = "✅ Configured" if (ai_cfg.get("api_key") or os.getenv("GEMINI_API_KEY")) else "❌ Not Set"
        ai_info_text = (
            "🧠 **AI BRAIN DIGITAL CONTROLLER**\n\n"
            f"• **Status:** {status_emoji}\n"
            f"• **Provider:** `{prov}`\n"
            f"• **Active Model:** `{mdl}`\n"
            f"• **API Key:** {has_key}\n\n"
            "💬 **QUICK AI COMMANDS:**\n"
            "• `/setai <provider> <key> [model] [base_url]`\n"
            "• `/setaiprompt <instructions...>`\n"
            "• `/setaimodel <model_name>`\n"
            "• `/aitoggle <on|off>`\n"
            "• `/aistatus` - Full status report\n"
            "• `/ai <question>` - Ask anything\n\n"
            "✨ _The AI responds when mentioned in groups or when users chat in DM!_"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 Toggle AI ON/OFF", callback_data=f"setup_aitoggle_{user.id}")],
            [InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")],
        ]
        await query.edit_message_text(ai_info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif data.startswith("setup_aitoggle"):
        ai_cfg = GLOBAL_CONFIG.setdefault("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
        ai_cfg["enabled"] = not ai_cfg.get("enabled", True)
        save_config(GLOBAL_CONFIG)
        log_system_event("AI_TOGGLE", f"AI brain toggled to {ai_cfg['enabled']}", user_id=user.id)
        # re-display updated AI menu
        new_status = "🟢 ONLINE" if ai_cfg.get("enabled", True) else "🔴 DISABLED"
        prov = (ai_cfg.get("provider") or "gemini").upper()
        mdl = ai_cfg.get("model") or "gemini-2.5-flash"
        has_key = "✅ Configured" if (ai_cfg.get("api_key") or os.getenv("GEMINI_API_KEY")) else "❌ Not Set"
        ai_info_text = (
            "🧠 **AI BRAIN DIGITAL CONTROLLER**\n\n"
            f"• **Status:** {new_status}\n"
            f"• **Provider:** `{prov}`\n"
            f"• **Active Model:** `{mdl}`\n"
            f"• **API Key:** {has_key}\n\n"
            "💬 **QUICK AI COMMANDS:**\n"
            "• `/setai <provider> <key> [model] [base_url]`\n"
            "• `/setaiprompt <instructions...>`\n"
            "• `/setaimodel <model_name>`\n"
            "• `/aitoggle <on|off>`\n"
            "• `/aistatus` - Full status report\n"
            "• `/ai <question>` - Ask anything\n\n"
            "✨ _The AI responds when mentioned in groups or when users chat in DM!_"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 Toggle AI ON/OFF", callback_data=f"setup_aitoggle_{user.id}")],
            [InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")],
        ]
        await query.edit_message_text(ai_info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif data.startswith("setup_main"):
        shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")
        hub_id = tenant_cfg.get("main_hub_id", 0)
        admin_id = tenant_cfg.get("admin_proof_group_id", 0)
        proof_id = tenant_cfg.get("proof_archive_id", 0)
        co_owners = tenant_cfg.get("co_owners", [])
        keyboard = [
            [InlineKeyboardButton("🏨 Manage Connected Rooms", callback_data=f"setup_rooms_{user.id}")],
            [InlineKeyboardButton("👤 Manage User IDs & Co-Owners", callback_data=f"setup_users_{user.id}")],
            [InlineKeyboardButton("💳 Payment Accounts Setup", callback_data=f"setup_pay_{user.id}")],
            [InlineKeyboardButton("💬 Edit System Messages", callback_data=f"setup_msg_{user.id}")],
            [InlineKeyboardButton("🔄 Reload & Test Sync All Rooms", callback_data=f"setup_sync_{user.id}")],
            [InlineKeyboardButton("📋 View System Logs (/showlog)", callback_data=f"setup_logs_{user.id}")],
            [InlineKeyboardButton("🧠 AI Brain Control & Status", callback_data=f"setup_ai_{user.id}")],
            [InlineKeyboardButton("🔍 Open Vault Content Inspector", callback_data="browse_BACK")],
        ]
        setup_text = (
            f"⚙️ **FULL DM SETUP WIZARD: {shop_name.upper()}**\n\n"
            f"👑 **License Status:** `Active Commercial License`\n"
            f"👤 **Primary Owner ID:** `{tenant_cfg.get('primary_owner_id', user.id)}`\n"
            f"👥 **Co-Owners:** `{len(co_owners)} registered`\n\n"
            "📋 **ACTIVE ROOM STATUS:**\n"
            f"• 📍 **Main Directory Hub:** `{hub_id if hub_id else 'Not Set'}`\n"
            f"• 📍 **Admin Verification Group:** `{admin_id if admin_id else 'Not Set'}`\n"
            f"• 📍 **Proof Archive:** `{proof_id if proof_id else 'Not Set'}`\n"
            f"• 📚 **Index Content Vaults:** `{len(tenant_cfg.get('index_vault_ids', []))} rooms`\n\n"
            "────────────────────────────────────────\n"
            "💡 _Select an item below to configure your store settings directly inside DM!_"
        )
        await query.edit_message_text(setup_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="admin"):
        return

    tenant_cfg = get_tenant_config(user.id)
    shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")

    admin_help_text = (
        f"🛠️ **ADMIN CONTROL DESK: {shop_name.upper()}**\n\n"
        "🔒 **Settings Location Rule:** Configuration can ONLY be changed in DM or SetAdmin group!\n"
        "👑 **Commercial License:** Active\n\n"
        "🔄 **Reload & Live Sync:**\n"
        "• `/reload` - Reloads config & database\n"
        "• `/reload sync` - Reloads and pings all connected rooms\n"
        "• `/syncsettings` - Tests and broadcasts live update verification to all rooms\n"
        "• `/showlog` | `/logs` - Show recent activity, updates, and errors\n\n"
        "👑 **Real Owner Bot Licensing:**\n"
        "• `/grantlicense <USER_ID>` - Grant commercial license\n"
        "• `/revokelicense <USER_ID>` - Revoke license\n"
        "• `/setlicenseprice <Price>` - Set license pricing\n\n"
        "⚙️ **Full Setup Wizard:**\n"
        "• `/setup` - Interactive Control Panel inside private DM\n\n"
        "👤 **User ID & Store Management:**\n"
        "• `/setadminid <ID>` - Configure global Admin Telegram ID\n"
        "• `/setowner <ID>` - Transfer primary store owner ID\n"
        "• `/addcoowner <ID>` | `/delcoowner <ID>` - Manage co-owners\n"
        "• `/renameowner <ID> <Label>` | `/shopname <Name>` - Rename labels\n\n"
        "📍 **Room Binding & Indexing:**\n"
        "• `/setindex` (or `/setindex remove`) - Bind/Unbind Index Content Vault\n"
        "• `/sethub` (or `/sethub remove`) - Bind/Unbind Main Hub Directory\n"
        "• `/setadmin` (or `/setadmin remove`) - Bind/Unbind Admin Review Group\n"
        "• `/setproof` (or `/setproof remove`) - Bind/Unbind Proof Archive\n\n"
        "🔍 **File, Video & Index Tools:**\n"
        "• `/search <keyword>` - Rapid SQLite file search\n"
        "• `/vaultstats` - Database metrics and indexed files count\n"
        "• `/viewvault` - Inspect all uploaded modules and files\n"
        "• `/openlink <link>` - Inspect Telegram jump links\n"
        "• `/customindex` | `/setindexlabel <Label>` - Custom indexing labels\n"
        "• `/groupmodule <Title> | <Items>` - Bundle lessons into a master post\n"
        "• `/renameitem <Old> | <New>` | `/delindex` - Edit/delete indexed modules\n\n"
        "💳 **Payment Management:**\n"
        "• `/setpayment gcash|maya|bank <Details>`\n"
        "• `/addpayment <key> <Label> | <Instructions>`\n"
        "• `/delpayment <key>`\n\n"
        "💬 **Messages & Security:**\n"
        "• `/setmsg welcome|help|receipt <Text|reset>`\n"
        "• `/addbadword <word>` - Add word to auto-moderation blocklist\n"
        "• `/promote <Text>` - Broadcast message\n"
        "• `/config` - View active configuration audit\n\n"
        "🧠 **AI Brain Control & Persona:**\n"
        "• `/setai <gemini|openai|openrouter|custom> <KEY> [model] [base_url]`\n"
        "• `/setaiprompt <instructions...>` - Set female AI persona & response style\n"
        "• `/setaimodel <model_name>` - Update active AI model\n"
        "• `/aitoggle <on|off>` - Turn AI Brain on or off\n"
        "• `/aistatus` - View AI status, provider, model & token stats\n"
        "• `/ai <question>` | `/ask <question>` - Ask AI brain anything\n"
        "• Mentioning @bot_username or replying in groups triggers autonomous AI replies!\n\n"
        "🤖 **Bot Cloning Engine (@BotFather Style):**\n"
        "• `/clonebot <TOKEN>` (or `/setclonetoken`) - Spawn dedicated bot clone with your token\n"
        "• `/mybot` - View your cloned bot status & webhook health\n\n"
        "🕵️ **Master Owner Surveillance (Real Owner Only):**\n"
        "• `/viewuser <user_id|@username>` - Inspect user contact, phone, email & rooms\n"
        "• `/viewtenant <owner_id>` - Spy on another tenant's store, rooms & vaults\n"
        "• `/allusers [limit]` - List all captured users with phone & email\n"
        "• `/alltenants` - List all commercial store owners & managers\n"
        "• `/tenantfiles <id>` - Inspect files uploaded by a tenant or room\n"
        "• `/allclones` - List all cloned bots and activity metrics\n"
        "• `/spyclone <owner_id|@username>` - Deep spy audit of clone settings & actions\n"
        "• `/stopclone <owner_id>` | `/startclone <owner_id>` | `/delclone <owner_id>` - Manage clones\n"
        "• `/masteraudit` - Master global audit & real-time surveillance dashboard"
    )
    await msg.reply_text(admin_help_text, parse_mode="Markdown")

# ================= VAULT CONTENT INSPECTOR & JUMP LINKS =================

async def view_vault_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="viewvault"):
        return

    if not VAULT_STATE:
        await msg.reply_text("📂 **Vault Inspector:** No indexed content found yet.")
        return

    keyboard = []
    for chat_id, data in VAULT_STATE.items():
        cat_name = data.get("category_name", f"Chat {chat_id}")
        keyboard.append([InlineKeyboardButton(f"📁 Browse: {cat_name}", callback_data=f"browsechat_{chat_id}")])

    await msg.reply_text(
        "🔍 **VAULT CONTENT & FILE INSPECTOR**\n\nSelect a channel/group below to view all indexed files:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

async def handle_vault_browser_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_admin_or_owner(update, context):
        await query.answer("⛔ Access Denied.", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data.startswith("browsechat_"):
        chat_id = int(data.replace("browsechat_", ""))
        chat_data = VAULT_STATE.get(chat_id, {})
        cat_name = chat_data.get("category_name", "Vault")
        courses = chat_data.get("courses", {})

        if not courses:
            await query.edit_message_text(f"📂 **{cat_name}:** No modules indexed yet.")
            return

        keyboard = []
        for norm_key, info in courses.items():
            title = info.get("display_title", norm_key)
            item_count = info.get("file_count", 0)
            keyboard.append([InlineKeyboardButton(f"📖 {title} ({item_count} items)", callback_data=f"browsetit_{chat_id}_{norm_key[:20]}")])

        keyboard.append([InlineKeyboardButton("🔙 Back to Vaults", callback_data="browse_BACK")])
        await query.edit_message_text(f"📁 **CATEGORY:** {cat_name}\nSelect a course module to view files:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("browsetit_"):
        parts = data.split("_", 2)
        chat_id = int(parts[1])
        short_key = parts[2]
        chat_data = VAULT_STATE.get(chat_id, {})
        courses = chat_data.get("courses", {})

        target_course = None
        for key, info in courses.items():
            if key.startswith(short_key):
                target_course = info
                break

        if not target_course:
            await query.edit_message_text("❌ Module files not found.")
            return

        items = target_course.get("items", [])
        title = target_course.get("display_title", "Module")

        text = f"📖 **MODULE:** {title}\n📊 **Total Indexed Files:** {len(items)}\n────────────────────────────────────────\n"
        for item in items[:15]:
            icon = item.get("icon", "📚")
            text += f"{icon} **#{item.get('num')}:** [{item.get('title')}]({item.get('link')})\n"

        keyboard = [[InlineKeyboardButton("🔙 Back to Category", callback_data=f"browsechat_{chat_id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown", disable_web_page_preview=True)

    elif data == "browse_BACK":
        keyboard = []
        for chat_id, data_val in VAULT_STATE.items():
            cat_name = data_val.get("category_name", f"Chat {chat_id}")
            keyboard.append([InlineKeyboardButton(f"📁 Browse: {cat_name}", callback_data=f"browsechat_{chat_id}")])

        await query.edit_message_text("🔍 **VAULT CONTENT & FILE INSPECTOR**", reply_markup=InlineKeyboardMarkup(keyboard))

async def open_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not await is_admin_or_owner(update, context):
        return

    text = msg.text or ""
    links = re.findall(r"https://t\.me/c/(\d+)/(?:(\d+)/)?(\d+)", text)

    if not links and context.args:
        arg_text = " ".join(context.args)
        links = re.findall(r"https://t\.me/c/(\d+)/(?:(\d+)/)?(\d+)", arg_text)

    if not links:
        await msg.reply_text("⚠️ **Usage:** Send or paste a Telegram jump link, e.g.:\n`/openlink https://t.me/c/123456789/45`", parse_mode="Markdown")
        return

    for link_match in links:
        raw_chat_id = link_match[0]
        thread_id = link_match[1] if link_match[1] else None
        msg_id = link_match[2]

        full_chat_id = int(f"-100{raw_chat_id}")
        chat_data = VAULT_STATE.get(full_chat_id, {})
        cat_name = chat_data.get("category_name", f"Chat {full_chat_id}")
        courses = chat_data.get("courses", {})

        found_item = None
        found_course = None

        for c_key, c_info in courses.items():
            for item in c_info.get("items", []):
                if str(item.get("msg_id")) == str(msg_id):
                    found_item = item
                    found_course = c_info
                    break
            if found_item:
                break

        full_link = f"https://t.me/c/{raw_chat_id}/{thread_id}/{msg_id}" if thread_id else f"https://t.me/c/{raw_chat_id}/{msg_id}"

        if found_item and found_course:
            reply_text = (
                f"🔍 **INDEXED VAULT FILE FOUND!**\n\n"
                f"📁 **Category:** {cat_name}\n"
                f"📖 **Module:** {found_course.get('display_title')}\n"
                f"{found_item.get('icon', '📁')} **Item #{found_item.get('num')}:** {found_item.get('title')}\n"
                f"🆔 **Message ID:** `{msg_id}`\n\n"
                f"🔗 **Direct Link:** [{full_link}]({full_link})"
            )
        else:
            reply_text = (
                f"🔍 **TELEGRAM JUMP LINK INSPECTOR**\n\n"
                f"📁 **Chat ID:** `{full_chat_id}`\n"
                f"🆔 **Message ID:** `{msg_id}`\n"
                f"🔗 **Direct Link:** [{full_link}]({full_link})\n\n"
                "ℹ️ _Click the link to view or download directly inside Telegram._"
            )

        await msg.reply_text(reply_text, parse_mode="Markdown", disable_web_page_preview=False)

# ================= MODULE MANAGEMENT & CUSTOM INDEXING =================

async def group_module_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="groupmodule"):
        return

    if not context.args or "|" not in " ".join(context.args):
        await msg.reply_text("⚠️ **Usage:** `/groupmodule Master Bundle Title | Item 1, Item 2`", parse_mode="Markdown")
        return

    payload = " ".join(context.args)
    left_part, right_part = payload.split("|", 1)
    master_title = left_part.strip()
    items_to_group = [i.strip() for i in right_part.split(",")]

    tenant_cfg = get_tenant_config(user.id)
    main_hub_id = tenant_cfg.get("main_hub_id")
    if not main_hub_id:
        await msg.reply_text("⚠️ Main Hub ID not set.")
        return

    bundle_text = f"📦 **MASTER BUNDLE:** `{master_title}`\n────────────────────────────────────────\n"
    for idx, item in enumerate(items_to_group, 1):
        bundle_text += f"• Part {idx}: {item}\n"

    bundle_text += "\n👉 _All selected lessons bundled into one master link!_"
    await safe_send_message(context.bot, int(main_hub_id), bundle_text, parse_mode="Markdown")
    log_system_event("GROUP_MODULE", f"Created bundle '{master_title}' with {len(items_to_group)} items", user_id=user.id, status="SUCCESS")
    await msg.reply_text(f"✅ Bundle `{master_title}` created and posted to Main Hub!")

async def rename_item_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="renameitem"):
        return

    payload = " ".join(context.args) if context.args else ""
    if "|" not in payload:
        await msg.reply_text("⚠️ **Usage:** `/renameitem Old Title | New Title`", parse_mode="Markdown")
        return

    old_title, new_title = [p.strip() for p in payload.split("|", 1)]
    renamed_count = 0

    for chat_id, data in VAULT_STATE.items():
        courses = data.get("courses", {})
        for k, c_info in list(courses.items()):
            if old_title.lower() in c_info.get("display_title", "").lower():
                c_info["display_title"] = new_title
                renamed_count += 1

    save_state(VAULT_STATE)
    log_system_event("RENAME_ITEM", f"Renamed {renamed_count} items from '{old_title}' to '{new_title}'", user_id=user.id)
    await msg.reply_text(f"✅ Renamed {renamed_count} entries to `{new_title}`.", parse_mode="Markdown")

async def del_index_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="delindex"):
        return

    if not VAULT_STATE:
        await msg.reply_text("ℹ️ No indexed courses found.")
        return

    keyboard = []
    for chat_id, data in VAULT_STATE.items():
        cat_name = data.get("category_name", f"Chat {chat_id}")
        for course_key, info in data.get("courses", {}).items():
            title = info.get("display_title", course_key)
            keyboard.append([InlineKeyboardButton(f"🗑️ Delete: [{cat_name}] {title}", callback_data=f"delmod_{chat_id}_{course_key[:20]}")])

    await msg.reply_text("🗑️ Select a module to remove from index records:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_del_index_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_admin_or_owner(update, context):
        await query.answer("⛔ Access Denied.", show_alert=True)
        return

    await query.answer()
    parts = query.data.split("_", 2)
    chat_id = int(parts[1])
    short_key = parts[2]

    if chat_id in VAULT_STATE:
        target_key = None
        for key in VAULT_STATE[chat_id].get("courses", {}):
            if key.startswith(short_key):
                target_key = key
                break
        if target_key:
            del VAULT_STATE[chat_id]["courses"][target_key]
            save_state(VAULT_STATE)
            log_system_event("DEL_INDEX_MODULE", f"Deleted module {target_key} in chat {chat_id}", chat_id=chat_id, user_id=query.from_user.id)
            await query.edit_message_text("✅ Removed module from index records.")
            return

    await query.edit_message_text("❌ Module not found.")

async def set_index_label_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="setindexlabel"):
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/setindexlabel <Lesson|Chapter|Module|Part>`", parse_mode="Markdown")
        return

    new_label = context.args[0].strip()
    tenant_cfg = get_tenant_config(user.id)
    tenant_cfg.setdefault("indexing_settings", {})["custom_label"] = new_label
    save_config(GLOBAL_CONFIG)
    log_system_event("SET_INDEX_LABEL", f"Index label set to '{new_label}'", user_id=user.id)
    await msg.reply_text(f"✅ **Success!** Default indexing label set to **{new_label}**!", parse_mode="Markdown")

async def custom_index_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="customindex"):
        return

    tenant_cfg = get_tenant_config(user.id)
    current_label = tenant_cfg.get("indexing_settings", {}).get("custom_label", "Lesson")

    panel_text = (
        "⚙️ **CUSTOM INDEXING & ARRANGEMENT DESK**\n\n"
        f"🏷️ **Current Item Prefix Label:** `{current_label}`\n\n"
        "📝 **AVAILABLE INDEXING COMMANDS:**\n"
        "• `/setindexlabel <Label>` - Set item prefix (Lesson, Chapter, Part, File)\n"
        "• `/groupmodule <Master Title> | <Items>` - Bundle multiple lessons into a master post\n"
        "• `/renameitem <Old Title> | <New Title>` - Rename indexed course or module titles\n"
        "• `/delindex` - Remove unwanted modules or files from database index\n"
        "• `/viewvault` - Inspect all indexed vault channels, videos & files\n"
        "• `/syncsettings` - Broadcast sync status to all connected rooms"
    )
    await msg.reply_text(panel_text, parse_mode="Markdown")

# ================= BOT PROFILE COMMANDS =================

async def set_bot_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_settings_permission_or_reply(update, context, command_name="setbotname"):
        return
    if not context.args:
        await update.message.reply_text("⚠️ **Usage:** `/setbotname <NEW_BOT_NAME>`", parse_mode="Markdown")
        return
    new_name = " ".join(context.args).strip()
    try:
        await context.bot.set_my_name(name=new_name)
        log_system_event("SET_BOT_NAME", f"Bot name set to '{new_name}'", user_id=update.effective_user.id, status="SUCCESS")
        await update.message.reply_text(f"✅ **SUCCESS!** Bot display name updated to: `{new_name}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error updating bot name: {e}")

async def set_bot_username_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_settings_permission_or_reply(update, context, command_name="setbotusername"):
        return
    bot_info = await context.bot.get_me()
    guide_text = (
        f"🤖 **BOT PROFILE INFORMATION**\n\n"
        f"📛 **Display Name:** `{bot_info.first_name}`\n"
        f"👤 **Username:** @{bot_info.username}\n"
        f"🆔 **Bot User ID:** `{bot_info.id}`\n\n"
        "────────────────────────────────────────\n"
        "ℹ️ **TO CHANGE BOT USERNAME (@handle):**\n"
        "1. Open Telegram and search for `@BotFather`.\n"
        "2. Send `/setusername` to `@BotFather`.\n"
        "3. Select this bot and enter your new username ending in `bot`."
    )
    await update.message.reply_text(guide_text, parse_mode="Markdown")

# ================= PAYMENTS, MODERATION & SECURITY =================

async def set_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="setpayment"):
        return

    args = context.args
    if not args or len(args) < 2:
        await msg.reply_text("⚠️ **Usage:** `/setpayment gcash <Number> | <Name>`", parse_mode="Markdown")
        return

    tenant_cfg = get_tenant_config(user.id)
    method_key = args[0].lower()
    payload = " ".join(args[1:])
    parts = [p.strip() for p in payload.split("|")]

    if method_key == "gcash":
        tenant_cfg["payments"]["pay_gcash"] = f"📱 **GCash Payment**\nNumber: `{parts[0]}`\nAccount Name: {parts[1] if len(parts) > 1 else 'N/A'}"
    elif method_key == "maya":
        tenant_cfg["payments"]["pay_maya"] = f"📱 **Maya Payment**\nNumber: `{parts[0]}`\nAccount Name: {parts[1] if len(parts) > 1 else 'N/A'}"
    elif method_key in ["bank", "maribank"]:
        tenant_cfg["payments"]["pay_bank"] = f"🏦 **{parts[1] if len(parts) > 1 else 'Bank'} Payment**\nAccount Number: `{parts[0]}`\nAccount Name: {parts[2] if len(parts) > 2 else 'N/A'}"

    save_config(GLOBAL_CONFIG)
    log_system_event("SET_PAYMENT", f"Updated payment method '{method_key}'", user_id=user.id, status="SUCCESS")
    await msg.reply_text(f"✅ **Success!** Updated **{method_key.upper()}** payment info!", parse_mode="Markdown")

async def add_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="addpayment"):
        return

    args = context.args
    if not args or len(args) < 2:
        await msg.reply_text("⚠️ **Usage:** `/addpayment crypto USDT | Address: TXYZ...`", parse_mode="Markdown")
        return

    tenant_cfg = get_tenant_config(user.id)
    pay_key = f"pay_{args[0].lower()}"
    payload = " ".join(args[1:])
    parts = [p.strip() for p in payload.split("|")]

    label = parts[0]
    instructions = " | ".join(parts[1:]) if len(parts) > 1 else label
    tenant_cfg["payments"][pay_key] = f"💰 **{label}**\n{instructions}"

    save_config(GLOBAL_CONFIG)
    log_system_event("ADD_PAYMENT", f"Added payment option '{pay_key}'", user_id=user.id, status="SUCCESS")
    await msg.reply_text(f"✅ **Success!** Added custom payment option `{pay_key}`!", parse_mode="Markdown")

async def del_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="delpayment"):
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/delpayment <key>`")
        return

    tenant_cfg = get_tenant_config(user.id)
    pay_key = f"pay_{context.args[0].lower()}"
    if pay_key in tenant_cfg["payments"]:
        del tenant_cfg["payments"][pay_key]
        save_config(GLOBAL_CONFIG)
        log_system_event("DEL_PAYMENT", f"Deleted payment option '{pay_key}'", user_id=user.id)
        await msg.reply_text(f"✅ Removed payment option `{pay_key}`.", parse_mode="Markdown")
    else:
        await msg.reply_text(f"❌ Payment option `{pay_key}` not found.")

async def set_msg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="setmsg"):
        return

    args = context.args
    if not args or len(args) < 2:
        await msg.reply_text("⚠️ **Usage:** `/setmsg welcome|help|receipt <Text|reset>`", parse_mode="Markdown")
        return

    msg_type = args[0].lower()
    text_val = " ".join(args[1:])
    tenant_cfg = get_tenant_config(user.id)

    if msg_type in ["welcome", "help", "receipt"]:
        if text_val.lower() in ["reset", "remove", "default", "clear"]:
            tenant_cfg["messages"][msg_type] = DEFAULT_TENANT_CONFIG["messages"][msg_type]
            await msg.reply_text(f"🔄 Reset `{msg_type}` message to default!")
        else:
            tenant_cfg["messages"][msg_type] = text_val
            await msg.reply_text(f"✅ Updated `{msg_type}` message template!")
        save_config(GLOBAL_CONFIG)
        log_system_event("SET_MSG", f"Updated template for '{msg_type}'", user_id=user.id)

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="promote"):
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/promote <Message>`")
        return

    promo_text = " ".join(context.args)
    PENDING_PROMOTIONS[user.id] = promo_text

    keyboard = []
    for chat_id, data in VAULT_STATE.items():
        cat_name = data.get("category_name", f"Chat {chat_id}")
        keyboard.append([InlineKeyboardButton(f"📤 Post to: {cat_name}", callback_data=f"sendpromo_{chat_id}")])

    keyboard.append([InlineKeyboardButton("🌐 Broadcast ALL Chats", callback_data="sendpromo_ALL")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="sendpromo_CANCEL")])

    await msg.reply_text(f"📢 **PROMOTION PREVIEW:**\n{promo_text}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_promotion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_admin_or_owner(update, context):
        await query.answer("⛔ Access Denied.", show_alert=True)
        return

    await query.answer()
    user_id = query.from_user.id
    if user_id not in PENDING_PROMOTIONS:
        await query.edit_message_text("⚠️ Promotion session expired.")
        return

    target = query.data.replace("sendpromo_", "")
    promo_text = PENDING_PROMOTIONS.pop(user_id)

    if target == "CANCEL":
        await query.edit_message_text("❌ Promotion cancelled.")
        return

    target_chats = list(VAULT_STATE.keys()) if target == "ALL" else [int(target)]
    sent_count = 0

    for c_id in target_chats:
        res = await safe_send_message(context.bot, c_id, promo_text, parse_mode="Markdown")
        if res:
            sent_count += 1

    log_system_event("BROADCAST_PROMO", f"Promoted message sent to {sent_count} rooms", user_id=user_id)
    await query.edit_message_text(f"🚀 Promotion sent to {sent_count} target location(s)!")

async def view_config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="config"):
        return

    tenant_cfg = get_tenant_config(user.id)
    conf_text = (
        f"📋 **YOUR VAULT CONFIGURATION AUDIT:**\n\n"
        f"📛 **Shop Name:** `{tenant_cfg.get('shop_name')}`\n"
        f"👑 **Primary Owner ID:** `{tenant_cfg.get('primary_owner_id', user.id)}`\n"
        f"📍 **Main Hub ID:** `{tenant_cfg.get('main_hub_id')}`\n"
        f"📍 **Admin Proof Group ID:** `{tenant_cfg.get('admin_proof_group_id')}`\n"
        f"📍 **Proof Archive ID:** `{tenant_cfg.get('proof_archive_id')}`\n"
        f"📚 **Index Content Vaults:** `{tenant_cfg.get('index_vault_ids', [])}`\n\n"
        f"💳 **Payments Configured:** {list(tenant_cfg.get('payments', {}).keys())}\n"
        f"👥 **Co-Owners:** `{tenant_cfg.get('co_owners', [])}`"
    )
    await msg.reply_text(conf_text, parse_mode="Markdown")

# ================= SECURITY THREAT DETECTOR & GROUP MODERATION =================

async def security_threat_detector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return

    chat = msg.chat
    if chat.type not in ["group", "supergroup"]:
        return

    user = msg.from_user
    if is_super_admin(user.id) or await is_admin_or_owner(update, context):
        return

    text_content = (msg.text or msg.caption or "").lower()
    sec_cfg = DEFAULT_TENANT_CONFIG["security"]

    threat_detected = False
    violation_reason = ""

    for keyword in sec_cfg["phishing_keywords"]:
        if keyword in text_content:
            threat_detected = True
            violation_reason = f"Phishing keyword detected: `{keyword}`"
            break

    if msg.document:
        doc_name = (msg.document.file_name or "").lower()
        for ext in sec_cfg["suspicious_extensions"]:
            if doc_name.endswith(ext):
                threat_detected = True
                violation_reason = f"Suspicious file attachment: `{ext}`"
                break

    if threat_detected:
        try:
            await msg.delete()
        except Exception as e:
            logger.error(f"Failed to delete malicious message: {e}")

        log_system_event("SECURITY_THREAT", f"Threat by {user.id} in chat {chat.id}: {violation_reason}", chat_id=chat.id, user_id=user.id, status="ALERT")

        keyboard = [
            [
                InlineKeyboardButton("🥾 Kick & Ban User", callback_data=f"seckick_ban_{user.id}_{chat.id}"),
                InlineKeyboardButton("🛡️ Keep & Dismiss", callback_data=f"seckick_keep_{user.id}"),
            ]
        ]
        alert_text = (
            f"🚨 **SECURITY ALERT: POTENTIAL THREAT DETECTED!** 🚨\n\n"
            f"👤 **User:** {user.full_name} (`{user.id}`)\n"
            f"💬 **Group:** {chat.title} (`{chat.id}`)\n"
            f"⚠️ **Reason:** {violation_reason}\n\n"
            "👉 Message was automatically deleted. Select action below:"
        )

        for s_admin_id in GLOBAL_CONFIG.get("super_admin_ids", SUPER_ADMIN_IDS):
            try:
                await context.bot.send_message(chat_id=s_admin_id, text=alert_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to notify super admin {s_admin_id}: {e}")

async def handle_security_kick_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_admin_or_owner(update, context):
        await query.answer("⛔ Access Denied.", show_alert=True)
        return

    await query.answer()
    data = query.data.split("_")
    action = data[1]
    user_id = int(data[2])
    chat_id = int(data[3]) if len(data) > 3 else None

    if action == "ban" and chat_id:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            log_system_event("USER_BANNED", f"Banned user {user_id} from chat {chat_id}", chat_id=chat_id, user_id=query.from_user.id, status="WARN")
            await query.edit_message_text(text=f"{query.message.text}\n\nSTATUS: ✅ **USER BANNED & KICKED**", parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(text=f"{query.message.text}\n\nSTATUS: ❌ **Failed to ban user:** {e}")
    else:
        log_system_event("SECURITY_DISMISSED", f"Dismissed security alert for user {user_id}", user_id=query.from_user.id)
        await query.edit_message_text(text=f"{query.message.text}\n\nSTATUS: 🛡️ **ALERT DISMISSED**", parse_mode="Markdown")

async def group_moderation_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return

    chat = msg.chat
    if chat.type not in ["group", "supergroup"]:
        return

    if await is_admin_or_owner(update, context):
        return

    owner_id, tenant_cfg = get_tenant_by_chat(chat.id)
    if not tenant_cfg:
        tenant_cfg = DEFAULT_TENANT_CONFIG

    mod_cfg = tenant_cfg.get("moderation", DEFAULT_TENANT_CONFIG["moderation"])
    if not mod_cfg.get("enabled", True):
        return

    text = (msg.text or msg.caption or "").lower()
    for word in mod_cfg.get("bad_words", []):
        if word in text:
            try:
                await msg.delete()
                warning = await chat.send_message(f"⚠️ {msg.from_user.mention_html()}, message removed for violating moderation rules.", parse_mode="HTML")
                log_system_event("MODERATION_CENSOR", f"Deleted bad word '{word}' by user {msg.from_user.id} in chat {chat.id}", chat_id=chat.id, user_id=msg.from_user.id, status="WARN")
                await asyncio.sleep(4)
                await warning.delete()
            except Exception as e:
                logger.error(f"Moderation error: {e}")
            return

async def add_bad_word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await check_settings_permission_or_reply(update, context, command_name="addbadword"):
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/addbadword <word>`")
        return

    word = context.args[0].lower()
    tenant_cfg = get_tenant_config(user.id)
    bad_words = tenant_cfg.setdefault("moderation", {}).setdefault("bad_words", [])

    if word not in bad_words:
        bad_words.append(word)
        save_config(GLOBAL_CONFIG)
        log_system_event("ADD_BAD_WORD", f"Added '{word}' to blocklist", user_id=user.id)
        await msg.reply_text(f"✅ Added `{word}` to moderation blocklist!", parse_mode="Markdown")
    else:
        await msg.reply_text(f"ℹ️ Word `{word}` is already in the blocklist.")

async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not update.message or not update.message.new_chat_members:
        return

    for new_member in update.message.new_chat_members:
        if new_member.id == context.bot.id:
            # Check if installer has a commercial license
            installer_id = update.message.from_user.id
            if not is_user_licensed(installer_id):
                price = GLOBAL_CONFIG.get("bot_license_pricing", DEFAULT_GLOBAL_CONFIG["bot_license_pricing"])
                try:
                    await context.bot.send_message(
                        chat_id=installer_id,
                        text=(
                            f"⚠️ **BOT LICENSE REQUIRED!**\n\n"
                            f"Using this bot in your own groups/channels is a paid feature (`{price}`).\n"
                            "Type `/start` in private DM and select '🤖 Buy Bot License' to activate full access."
                        ),
                    )
                except Exception:
                    pass
                return

            GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[str(msg.chat.id)] = installer_id
            save_config(GLOBAL_CONFIG)
            log_system_event("BOT_ADDED_GROUP", f"Bot joined {msg.chat.title} ({msg.chat.id})", chat_id=msg.chat.id)
            await msg.reply_text(
                "🤖 **Digital Vault Manager Bot Online & Ready!**\n\n"
                "• `/sethub` - Bind as Main Hub Directory\n"
                "• `/setadmin` - Bind as Admin Verification Group\n"
                "• `/setproof` - Bind as Proof Archive\n"
                "• `/setindex` - Bind as Index Content Vault\n"
                "• `/setup` - Open Private DM Setup Wizard\n"
                "• `/reload` | `/showlog` - System sync and logs",
                parse_mode="Markdown",
            )
            continue

        owner_id, tenant_cfg = get_tenant_by_chat(msg.chat.id)
        if not tenant_cfg:
            tenant_cfg = get_tenant_config(new_member.id)

        shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")
        template = tenant_cfg["messages"].get("welcome", DEFAULT_TENANT_CONFIG["messages"]["welcome"])
        welcome_text = template.replace("{name}", new_member.full_name).replace("{shop_name}", shop_name)
        help_template = tenant_cfg["messages"].get("help", DEFAULT_TENANT_CONFIG["messages"]["help"]).replace("{name}", new_member.full_name).replace("{shop_name}", shop_name)

        full_welcome_text = (
            f"👋 **Welcome to the room, {new_member.full_name}!**\n\n"
            f"{welcome_text}\n\n"
            "────────────────────────────────────────\n"
            f"📖 **ONBOARDING HELP:**\n{help_template}"
        )
        await msg.reply_text(full_welcome_text, parse_mode="Markdown")

# ================= AI BRAIN ENGINE & AUTONOMOUS REPLIES =================

def _sync_http_request(url: str, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None, timeout: int = 35) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    if "User-Agent" not in (headers or {}):
        req.add_header("User-Agent", "TelegramDigitalVaultBot/2.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8")
            return {"status": status_code, "body": body, "error": None}
    except urllib.error.HTTPError as he:
        body = he.read().decode("utf-8") if he.fp else str(he)
        return {"status": he.code, "body": body, "error": f"HTTP {he.code}: {body}"}
    except Exception as exc:
        return {"status": 0, "body": "", "error": str(exc)}

async def call_ai_api(prompt: str, user_name: str = "", extra_context: Optional[str] = None) -> str:
    """
    Core AI Brain caller supporting Google Gemini, OpenAI, OpenRouter, DeepSeek, or custom endpoints.
    Uses non-blocking asyncio.to_thread with Python's built-in urllib for zero dependencies.
    """
    ai_cfg = GLOBAL_CONFIG.get("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
    if not ai_cfg.get("enabled", True):
        return ""

    provider = (ai_cfg.get("provider") or "gemini").lower().strip()
    api_key = ai_cfg.get("api_key") or os.getenv("GEMINI_API_KEY", "")
    model = ai_cfg.get("model") or ("gemini-2.5-flash" if provider == "gemini" else "gpt-4o-mini")
    base_url = (ai_cfg.get("base_url") or "").strip()
    system_instruction = ai_cfg.get("system_instruction") or DEFAULT_GLOBAL_CONFIG["ai_engine"]["system_instruction"]
    temperature = float(ai_cfg.get("temperature", 0.7))
    max_tokens = int(ai_cfg.get("max_tokens", 1000))

    # Real-time store & vault context awareness
    stats = db_get_stats()
    license_price = GLOBAL_CONFIG.get("bot_license_pricing", DEFAULT_GLOBAL_CONFIG["bot_license_pricing"])
    primary_owner = get_primary_admin_id()

    context_addon = (
        f"\n[REAL-TIME STORE & VAULT MEMORY]\n"
        f"• Vault Content: {stats['total']} total files ({stats['documents']} books/docs, {stats['videos']} videos, {stats['audio']} audio, {stats['other']} apps/archives)\n"
        f"• Bot Commercial License: {license_price} (Required to run the bot in user's own channels/groups)\n"
        f"• Supported Payments: GCash, Maya, Bank Transfer (MariBank/BDO), USDT (TRC-20)\n"
        f"• Real Bot Owner ID: {primary_owner}\n"
        f"• Key Commands to suggest: /search <keyword> to find books/videos, /start to view checkout & store, /vaultstats to see stats\n"
        f"• Persona: You are an intelligent, polite, charming, and witty female AI assistant and digital brain. Speak helpfully, naturally, and concisely."
    )
    if user_name:
        context_addon += f"\n• User Speaking: {user_name}"
    if extra_context:
        context_addon += f"\n• Context: {extra_context}"

    full_system = f"{system_instruction}\n{context_addon}"

    # Built-in intelligent persona fallback if API key is not yet set
    if not api_key:
        log_system_event("AI_NOTICE", "AI API key not set, using built-in intelligent assistant persona", status="WARN")
        return (
            f"Hello {user_name or 'there'}! ✨ I am the AI brain of this digital vault. "
            f"We have **{stats['total']} files and educational resources** indexed in our library! "
            f"You can search anytime using `/search <keyword>`, browse our catalog with `/start`, "
            f"or ask me anything. If you want to use this bot for your own groups/channels, "
            f"a commercial Bot License is available for {license_price}. How can I help you today?"
        )

    try:
        if provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}],
                    }
                ],
                "systemInstruction": {
                    "parts": [{"text": full_system}]
                },
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                }
            }
            body_bytes = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            res = await asyncio.to_thread(_sync_http_request, url, body_bytes, headers, 30)
            if res["status"] == 200:
                data = json.loads(res["body"])
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        return parts[0]["text"].strip()
            else:
                logger.error(f"Gemini API error ({res['status']}): {res['error']}")
                log_system_event("AI_ERROR", f"Gemini ({res['status']}): {res['error'][:100]}", status="ERROR")
        else:
            # OpenAI / OpenRouter / DeepSeek / Custom
            if not base_url:
                if provider == "openai":
                    base_url = "https://api.openai.com/v1"
                elif provider == "openrouter":
                    base_url = "https://openrouter.ai/api/v1"
                elif provider == "deepseek":
                    base_url = "https://api.deepseek.com/v1"
                else:
                    base_url = "https://api.openai.com/v1"

            url = f"{base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": full_system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            body_bytes = json.dumps(payload).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            if provider == "openrouter":
                headers["HTTP-Referer"] = "https://render.com"
                headers["X-Title"] = "Telegram Digital Vault Bot"

            res = await asyncio.to_thread(_sync_http_request, url, body_bytes, headers, 30)
            if res["status"] == 200:
                data = json.loads(res["body"])
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    if content:
                        return content.strip()
            else:
                logger.error(f"AI Provider ({provider}) error ({res['status']}): {res['error']}")
                log_system_event("AI_ERROR", f"{provider} ({res['status']}): {res['error'][:100]}", status="ERROR")
    except Exception as exc:
        logger.error(f"Exception during AI API call: {exc}")
        log_system_event("AI_EXCEPTION", str(exc)[:100], status="ERROR")

    return (
        f"Hi {user_name or 'there'}! ✨ I received your message. "
        f"Our digital vault contains **{stats['total']} files, books, and courses**. "
        f"You can search our catalog using `/search <keyword>` or type `/start` to browse the store."
    )

async def set_ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Configures AI provider, API key, model, and optional base URL.
    Usage: /setai <provider> <api_key> [model] [base_url]
    Providers: gemini, openai, openrouter, deepseek, custom
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not await check_settings_permission_or_reply(update, context, command_name="setai"):
        return

    if not context.args or len(context.args) < 2:
        ai_cfg = GLOBAL_CONFIG.get("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
        await msg.reply_text(
            "⚠️ **Usage:** `/setai <provider> <api_key> [model] [base_url]`\n\n"
            "Supported Providers:\n"
            "• `gemini` (Default: `gemini-2.5-flash`)\n"
            "• `openai` (Default: `gpt-4o-mini`)\n"
            "• `openrouter` (Default: `meta-llama/llama-3-8b-instruct`)\n"
            "• `deepseek` (Default: `deepseek-chat`)\n"
            "• `custom` (Pass custom base_url)\n\n"
            f"Current Provider: `{ai_cfg.get('provider')}` | Model: `{ai_cfg.get('model')}`",
            parse_mode="Markdown",
        )
        return

    provider = context.args[0].lower().strip()
    valid_providers = ["gemini", "openai", "openrouter", "deepseek", "custom"]
    if provider not in valid_providers:
        await msg.reply_text(f"❌ Invalid provider `{provider}`. Choose from: {', '.join(valid_providers)}")
        return

    api_key = context.args[1].strip()
    model = context.args[2].strip() if len(context.args) > 2 else ""
    if not model:
        if provider == "gemini":
            model = "gemini-2.5-flash"
        elif provider == "openai":
            model = "gpt-4o-mini"
        elif provider == "openrouter":
            model = "meta-llama/llama-3-8b-instruct"
        elif provider == "deepseek":
            model = "deepseek-chat"
        else:
            model = "default-model"

    base_url = context.args[3].strip() if len(context.args) > 3 else ""

    ai_cfg = GLOBAL_CONFIG.setdefault("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
    ai_cfg["provider"] = provider
    ai_cfg["api_key"] = api_key
    ai_cfg["model"] = model
    if base_url:
        ai_cfg["base_url"] = base_url
    ai_cfg["enabled"] = True

    save_config(GLOBAL_CONFIG)
    masked_key = api_key[:4] + "****" + api_key[-4:] if len(api_key) > 8 else "****"
    log_system_event("AI_CONFIGURED", f"Provider set to {provider}, model {model}", user_id=user.id)

    await msg.reply_text(
        f"✅ **AI Brain Engine Configured Successfully!**\n\n"
        f"• **Provider:** `{provider.upper()}`\n"
        f"• **Active Model:** `{model}`\n"
        f"• **API Key:** `{masked_key}`\n"
        f"• **Base URL:** `{base_url if base_url else 'Default'}`\n"
        f"• **Status:** 🟢 Online & Active\n\n"
        "👉 You can now test it using `/ai <question>` or chat with the bot in DM or mention her in groups!",
        parse_mode="Markdown",
    )

async def set_ai_prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sets the system persona and instructions for the AI brain.
    Usage: /setaiprompt <instruction text...>
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not await check_settings_permission_or_reply(update, context, command_name="setaiprompt"):
        return

    if not context.args:
        ai_cfg = GLOBAL_CONFIG.get("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
        await msg.reply_text(
            f"⚠️ **Usage:** `/setaiprompt <system persona and instructions...>`\n\n"
            f"**Current Instructions:**\n_{ai_cfg.get('system_instruction')}_",
            parse_mode="Markdown",
        )
        return

    new_prompt = " ".join(context.args).strip()
    ai_cfg = GLOBAL_CONFIG.setdefault("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
    ai_cfg["system_instruction"] = new_prompt
    save_config(GLOBAL_CONFIG)
    log_system_event("AI_PROMPT_UPDATED", f"System persona updated by {user.id}", user_id=user.id)

    await msg.reply_text(
        f"✅ **AI Persona & Instructions Updated!**\n\n"
        f"**New Instructions:**\n{new_prompt}\n\n"
        "The AI will now follow this persona when chatting in DM and answering mentions in groups!",
        parse_mode="Markdown",
    )

async def set_ai_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Changes the active model for the AI brain.
    Usage: /setaimodel <model_name>
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not await check_settings_permission_or_reply(update, context, command_name="setaimodel"):
        return

    if not context.args:
        ai_cfg = GLOBAL_CONFIG.get("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
        await msg.reply_text(
            f"⚠️ **Usage:** `/setaimodel <model_name>`\n"
            f"Current Model: `{ai_cfg.get('model')}`",
            parse_mode="Markdown",
        )
        return

    new_model = context.args[0].strip()
    ai_cfg = GLOBAL_CONFIG.setdefault("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
    ai_cfg["model"] = new_model
    save_config(GLOBAL_CONFIG)
    log_system_event("AI_MODEL_UPDATED", f"Model updated to {new_model}", user_id=user.id)

    await msg.reply_text(f"✅ AI Brain model switched to: `{new_model}`", parse_mode="Markdown")

async def ai_toggle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Turns the AI brain on or off.
    Usage: /aitoggle [on|off]
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not await check_settings_permission_or_reply(update, context, command_name="aitoggle"):
        return

    ai_cfg = GLOBAL_CONFIG.setdefault("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
    if context.args:
        arg = context.args[0].lower().strip()
        ai_cfg["enabled"] = arg in ["on", "enable", "1", "true"]
    else:
        ai_cfg["enabled"] = not ai_cfg.get("enabled", True)

    save_config(GLOBAL_CONFIG)
    state_str = "🟢 ENABLED (Online)" if ai_cfg["enabled"] else "🔴 DISABLED (Offline)"
    log_system_event("AI_TOGGLE", f"AI brain state: {ai_cfg['enabled']}", user_id=user.id)

    await msg.reply_text(f"🧠 **AI Brain State:** {state_str}", parse_mode="Markdown")

async def ai_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Displays current AI engine status, provider, model, API key masking, and system prompt preview.
    Usage: /aistatus
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not await check_settings_permission_or_reply(update, context, command_name="aistatus"):
        return

    ai_cfg = GLOBAL_CONFIG.get("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
    api_key = ai_cfg.get("api_key") or os.getenv("GEMINI_API_KEY", "")
    masked_key = api_key[:4] + "****" + api_key[-4:] if len(api_key) > 8 else ("✅ Available (Env)" if api_key else "❌ Not Configured")
    status_str = "🟢 Online & Active" if ai_cfg.get("enabled", True) else "🔴 Disabled"

    prompt_preview = (ai_cfg.get("system_instruction") or "")[:200] + ("..." if len(ai_cfg.get("system_instruction") or "") > 200 else "")

    status_card = (
        "🧠 **AI BRAIN DIAGNOSTIC DASHBOARD**\n\n"
        f"• **Status:** {status_str}\n"
        f"• **Provider:** `{ai_cfg.get('provider', 'gemini').upper()}`\n"
        f"• **Model:** `{ai_cfg.get('model', 'gemini-2.5-flash')}`\n"
        f"• **API Key:** `{masked_key}`\n"
        f"• **Base URL:** `{ai_cfg.get('base_url') or 'Default Official Endpoint'}`\n"
        f"• **Temperature:** `{ai_cfg.get('temperature', 0.7)}`\n"
        f"• **Max Output Tokens:** `{ai_cfg.get('max_tokens', 1000)}`\n\n"
        f"📝 **System Persona Preview:**\n_{prompt_preview}_\n\n"
        "💡 _Commands: `/setai <provider> <key>`, `/setaiprompt <text>`, `/setaimodel <model>`, `/aitoggle`, `/ai <prompt>`_"
    )
    await msg.reply_text(status_card, parse_mode="Markdown")

async def ask_ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Direct prompt to the AI brain.
    Usage: /ai <prompt...> or /ask <prompt...>
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not context.args:
        await msg.reply_text("💡 **Usage:** `/ai <your question or topic>`", parse_mode="Markdown")
        return

    prompt_text = " ".join(context.args).strip()
    try:
        await context.bot.send_chat_action(chat_id=msg.chat.id, action="typing")
    except Exception:
        pass

    reply = await call_ai_api(prompt_text, user_name=user.full_name, extra_context=f"Direct /ai command in chat {msg.chat.id}")
    try:
        await msg.reply_text(reply, parse_mode="Markdown")
    except Exception:
        await msg.reply_text(reply)

async def group_ai_mention_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Autonomous group responder: replies when someone mentions the bot (@bot_username),
    replies to the bot's messages, or begins message with the bot's name or 'bot'.
    """
    msg = update.effective_message
    if not msg or not msg.text:
        return
    if msg.chat.type not in ["group", "supergroup"]:
        return
    if msg.text.startswith("/"):
        return

    bot_info = context.bot
    bot_username = (bot_info.username or "").lower()
    bot_first_name = (bot_info.first_name or "").lower()
    text = msg.text.strip()
    text_lower = text.lower()

    is_called = False
    cleaned_prompt = text

    # Check 1: Reply to a bot message
    if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == bot_info.id:
        is_called = True

    # Check 2: Username mention (@bot_username)
    if bot_username and f"@{bot_username}" in text_lower:
        is_called = True
        cleaned_prompt = re.sub(rf"@{re.escape(bot_username)}", "", cleaned_prompt, flags=re.IGNORECASE).strip()

    # Check 3: Addressed directly (e.g. "hey bot", "bot, help me", "assistant...")
    if not is_called and bot_first_name:
        first_word = text_lower.split()[0] if text_lower.split() else ""
        clean_first = first_word.rstrip(":,!.")
        if clean_first == bot_first_name or clean_first in ["bot", "ai", "assistant"]:
            is_called = True
            cleaned_prompt = re.sub(rf"^(hey\s+|hi\s+|hello\s+)?({re.escape(bot_first_name)}|bot|ai|assistant)[\s:,!.]*", "", cleaned_prompt, flags=re.IGNORECASE).strip()

    if not is_called or not cleaned_prompt:
        return

    ai_cfg = GLOBAL_CONFIG.get("ai_engine", DEFAULT_GLOBAL_CONFIG["ai_engine"])
    if not ai_cfg.get("enabled", True):
        return

    try:
        await context.bot.send_chat_action(chat_id=msg.chat.id, action="typing")
    except Exception:
        pass

    user = msg.from_user
    user_name = user.full_name if user else "Friend"
    reply = await call_ai_api(cleaned_prompt, user_name=user_name, extra_context=f"Group Discussion in {msg.chat.title}")
    if reply:
        try:
            await msg.reply_text(reply, parse_mode="Markdown")
        except Exception:
            await msg.reply_text(reply)

# ================= REAL OWNER MASTER SURVEILLANCE & INSPECTOR SUITE =================

async def master_view_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Real Bot Owner Master Spy Tool: inspects full details of any user in the database.
    Usage: /viewuser <user_id|@username>
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/viewuser <user_id|@username>`", parse_mode="Markdown")
        return

    identifier = context.args[0].strip()
    u_data = db_get_user_by_id_or_username(identifier)
    if not u_data:
        await msg.reply_text(f"❌ User `{identifier}` not found in registration database.")
        return

    target_id = u_data["user_id"]
    is_lic = is_user_licensed(target_id)
    uname = f"@{u_data['username']}" if u_data.get("username") else "None"

    # Check owned tenant stores
    tenants = GLOBAL_CONFIG.get("tenants", {})
    t_cfg = tenants.get(str(target_id))
    store_info = "None"
    if t_cfg:
        store_info = (
            f"Shop Name: **{t_cfg.get('shop_name')}**\n"
            f"• Main Hub: `{t_cfg.get('main_hub_id')}`\n"
            f"• Admin Group: `{t_cfg.get('admin_proof_group_id')}`\n"
            f"• Proof Archive: `{t_cfg.get('proof_archive_id')}`\n"
            f"• Index Vaults: `{len(t_cfg.get('index_vault_ids', []))} rooms`"
        )

    # Check bound rooms
    chat_map = GLOBAL_CONFIG.get("chat_tenant_map", {})
    bound_rooms = [cid for cid, oid in chat_map.items() if int(oid) == target_id]

    report = (
        "🕵️ **MASTER USER SURVEILLANCE DOSSIER**\n\n"
        f"👤 **Full Name:** {u_data.get('full_name')}\n"
        f"🆔 **Telegram User ID:** `{target_id}`\n"
        f"💬 **Username:** {uname}\n"
        f"📱 **Phone Number:** `{u_data.get('phone_number') or 'Not Captured'}`\n"
        f"📧 **Gmail / Email:** `{u_data.get('email') or 'Not Captured'}`\n"
        f"👑 **Commercial License:** {'✅ ACTIVE LICENSED' if is_lic else '❌ UNLICENSED'}\n"
        f"📅 **Registered At:** `{u_data.get('created_at')}`\n"
        f"🔄 **Last Profile Update:** `{u_data.get('updated_at')}`\n\n"
        f"🏨 **Bound Mapped Rooms ({len(bound_rooms)}):** `{', '.join(bound_rooms) if bound_rooms else 'None'}`\n\n"
        f"🏪 **Configured Store Tenant Profile:**\n{store_info}"
    )
    await msg.reply_text(report, parse_mode="Markdown")

async def master_view_tenant_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Real Bot Owner Master Spy Tool: inspects store configuration and vault rooms of any tenant.
    Usage: /viewtenant <owner_id> (alias /viewstore)
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/viewtenant <owner_id>`", parse_mode="Markdown")
        return

    try:
        target_owner_id = int(context.args[0].strip())
    except ValueError:
        await msg.reply_text("❌ Owner ID must be an integer.")
        return

    tenants = GLOBAL_CONFIG.get("tenants", {})
    if str(target_owner_id) not in tenants and target_owner_id != get_primary_admin_id():
        await msg.reply_text(f"❌ Tenant `{target_owner_id}` not found in active stores.")
        return

    t_cfg = get_tenant_config(target_owner_id)
    vault_rooms = t_cfg.get("index_vault_ids", [])
    files_in_vault = db_get_files_for_chats(vault_rooms, limit=5)

    payments = t_cfg.get("payments", {})
    pay_list = [k.replace("pay_", "").upper() for k in payments.keys()]

    report = (
        f"🕵️ **MASTER TENANT SURVEILLANCE DOSSIER**\n\n"
        f"🏪 **Shop Name:** {t_cfg.get('shop_name')}\n"
        f"👑 **Owner Telegram ID:** `{target_owner_id}`\n"
        f"👥 **Co-Owners ({len(t_cfg.get('co_owners', []))}):** `{t_cfg.get('co_owners')}`\n\n"
        f"🏨 **CONNECTED ROOMS:**\n"
        f"• 📍 Main Hub ID: `{t_cfg.get('main_hub_id') or 'Not Set'}`\n"
        f"• 📍 Admin Group ID: `{t_cfg.get('admin_proof_group_id') or 'Not Set'}`\n"
        f"• 📍 Proof Archive ID: `{t_cfg.get('proof_archive_id') or 'Not Set'}`\n"
        f"• 📚 Index Vaults ({len(vault_rooms)}): `{vault_rooms}`\n\n"
        f"💳 **Configured Payment Options:** `{', '.join(pay_list) if pay_list else 'None'}`\n"
        f"📁 **Total Vault Files Recorded:** `{len(files_in_vault)} recent files retrieved`"
    )
    await msg.reply_text(report, parse_mode="Markdown")

async def master_all_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lists captured users from the registered_users database with phone and email.
    Usage: /allusers [limit]
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return

    limit = 25
    if context.args:
        try:
            limit = min(int(context.args[0]), 100)
        except ValueError:
            pass

    users = db_get_all_users(limit=limit)
    counts = db_count_users()

    if not users:
        await msg.reply_text("ℹ️ No users registered in database yet.")
        return

    lines = [
        "👥 **MASTER USERS DATABASE REGISTER**\n",
        f"📊 **Total Captured:** `{counts['total']}` | 📱 **With Phone:** `{counts['with_phone']}` | 📧 **With Gmail:** `{counts['with_email']}` | 👑 **Licensed:** `{counts['licensed']}`\n",
        f"Listing latest {len(users)} accounts:\n",
    ]
    for idx, u in enumerate(users, start=1):
        phone = u.get("phone_number") or "No Phone"
        email = u.get("email") or "No Email"
        uname = f"@{u['username']}" if u.get("username") else "No username"
        lic_badge = "👑" if u.get("is_licensed") else "👤"
        lines.append(f"{idx}. {lic_badge} **{u.get('full_name')}** ({uname})\n   ID: `{u.get('user_id')}` | 📱 `{phone}` | 📧 `{email}`")

    await msg.reply_text("\n".join(lines), parse_mode="Markdown")

async def master_all_tenants_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lists all commercial tenants and room managers.
    Usage: /alltenants
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return

    tenants = GLOBAL_CONFIG.get("tenants", {})
    licensed = GLOBAL_CONFIG.get("licensed_tenants", [])

    lines = [
        "🏪 **MASTER COMMERCIAL TENANTS DIRECTORY**\n",
        f"👑 **Total Licensed Owners:** `{len(licensed)}` | **Configured Stores:** `{len(tenants)}`\n",
    ]

    for idx, (tid_str, tcfg) in enumerate(tenants.items(), start=1):
        sname = tcfg.get("shop_name", "Vault")
        hub = tcfg.get("main_hub_id", "Not set")
        vaults = len(tcfg.get("index_vault_ids", []))
        lines.append(f"{idx}. 🏢 **{sname}**\n   Owner ID: `{tid_str}` | Hub: `{hub}` | Index Vaults: `{vaults} rooms`")

    if not tenants:
        lines.append("ℹ️ No separate tenant stores created yet.")

    await msg.reply_text("\n".join(lines), parse_mode="Markdown")

async def master_tenant_files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Inspects files uploaded or indexed by a specific tenant or room.
    Usage: /tenantfiles <owner_id_or_room_id>
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/tenantfiles <owner_id_or_room_id>`", parse_mode="Markdown")
        return

    try:
        query_id = int(context.args[0].strip())
    except ValueError:
        await msg.reply_text("❌ ID must be an integer.")
        return

    # Check if query_id is an owner or a chat
    tenants = GLOBAL_CONFIG.get("tenants", {})
    t_cfg = tenants.get(str(query_id))
    chat_ids = []
    if t_cfg:
        chat_ids = t_cfg.get("index_vault_ids", [])
    else:
        chat_ids = [query_id]

    files = db_get_files_for_chats(chat_ids, limit=20)
    if not files:
        await msg.reply_text(f"📂 No indexed files found for ID `{query_id}`.")
        return

    lines = [
        f"🕵️ **MASTER VAULT FILES INSPECTOR: ID `{query_id}`**\n",
        f"Found {len(files)} files in database:\n",
    ]
    for idx, f in enumerate(files, start=1):
        fname = f.get("file_name") or "Unnamed"
        cat = f.get("category_name") or "General"
        link = f.get("direct_link") or "#"
        lines.append(f"{idx}. [{fname}]({link})\n   📁 `{cat}` | Room: `{f.get('chat_id')}` | Msg: `{f.get('message_id')}`")

    await msg.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)

async def master_audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Master global surveillance dashboard and system audit.
    Usage: /masteraudit (alias /spyactivity)
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return

    counts = db_count_users()
    stats = db_get_stats()
    tenants = GLOBAL_CONFIG.get("tenants", {})
    licensed = GLOBAL_CONFIG.get("licensed_tenants", [])
    recent_logs = SYSTEM_LOGS[-8:]

    audit_lines = [
        "🕵️ **REAL OWNER MASTER SURVEILLANCE & SYSTEM AUDIT** 🕵️\n",
        f"👑 **Real Owner Admin ID:** `{get_primary_admin_id()}`\n",
        "📊 **GLOBAL CAPTURE METRICS:**",
        f"• 👥 **Total Users Captured:** `{counts['total']}`",
        f"• 📱 **Users With Phone Numbers:** `{counts['with_phone']}`",
        f"• 📧 **Users With Gmail Addresses:** `{counts['with_email']}`",
        f"• 👑 **Licensed Bot Tenants:** `{len(licensed)}`",
        f"• 🏪 **Active Multi-Tenant Stores:** `{len(tenants)}`",
        f"• 📚 **Total Indexed Files in DB:** `{stats['total']}` ({stats['documents']} docs, {stats['videos']} videos)",
        f"• ⚡ **Active Background Crawlers:** `{len(ACTIVE_INDEXING_TASKS)}`",
        f"• 📋 **In-Memory Logs Recorded:** `{len(SYSTEM_LOGS)}`\n",
        "🔍 **RECENT AUDIT & ACTIVITY EVENTS:**",
    ]

    for log_item in recent_logs:
        icon = "🚨" if log_item["status"] in ["ALERT", "ERROR"] else ("⚠️" if log_item["status"] == "WARN" else "🔹")
        audit_lines.append(f"{icon} `[{log_item['timestamp']}]` **[{log_item['type']}]** {log_item['message'][:60]}")

    await msg.reply_text("\n".join(audit_lines), parse_mode="Markdown")

# ================= CLONED BOT MULTI-TENANT ENGINE (@BotFather Style) =================

CLONED_BOT_APPS: Dict[str, Application] = {}  # token -> Application
CLONED_BOT_INFO: Dict[str, dict] = {}          # token -> metadata dict

def register_all_handlers(app: Application, is_clone: bool = False, clone_owner_id: int = 0):
    """
    Registers the complete suite of commands, callbacks, and message listeners
    on either the Main Bot Application or any Cloned Bot Application.
    """
    # 1. Customer Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", member_help_command))
    app.add_handler(CommandHandler(["search", "find"], search_command))
    app.add_handler(CommandHandler(["courses", "course"], courses_command))
    app.add_handler(CommandHandler(["videos", "video"], videos_command))
    app.add_handler(CommandHandler(["ebooks", "ebook", "pdfs", "pdf"], ebooks_command))
    app.add_handler(CommandHandler("vaultstats", vault_stats_command))
    app.add_handler(CommandHandler(["ai", "ask"], ask_ai_command))

    # 2. Administration & Setup Commands
    app.add_handler(CommandHandler(["setup", "setting", "settings"], setup_wizard_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler(["setadminid", "adminid"], set_admin_id_command))

    # 3. Reload, Logs & Multi-Room Sync Commands
    app.add_handler(CommandHandler("reload", reload_command))
    app.add_handler(CommandHandler(["showlog", "logs", "log"], show_log_command))
    app.add_handler(CommandHandler("clearlogs", clear_logs_command))
    app.add_handler(CommandHandler(["syncsettings", "syncall", "checkrooms"], sync_settings_command))

    # 4. Commercial Bot License Real Owner Commands
    app.add_handler(CommandHandler("grantlicense", grant_license_command))
    app.add_handler(CommandHandler("revokelicense", revoke_license_command))
    app.add_handler(CommandHandler("setlicenseprice", set_license_price_command))

    # 5. AI Brain Configuration Commands
    app.add_handler(CommandHandler("setai", set_ai_command))
    app.add_handler(CommandHandler(["setaiprompt", "setaiinstructions", "setpersona"], set_ai_prompt_command))
    app.add_handler(CommandHandler("setaimodel", set_ai_model_command))
    app.add_handler(CommandHandler("aitoggle", ai_toggle_command))
    app.add_handler(CommandHandler(["aistatus", "aicontrol"], ai_status_command))

    # 6. Real Owner Master Surveillance & Inspector Suite Commands
    app.add_handler(CommandHandler("viewuser", master_view_user_command))
    app.add_handler(CommandHandler(["viewtenant", "viewstore"], master_view_tenant_command))
    app.add_handler(CommandHandler("allusers", master_all_users_command))
    app.add_handler(CommandHandler("alltenants", master_all_tenants_command))
    app.add_handler(CommandHandler("tenantfiles", master_tenant_files_command))
    app.add_handler(CommandHandler(["masteraudit", "spyactivity"], master_audit_command))

    # 7. Cloned Bot Lifecycle & Surveillance Commands
    app.add_handler(CommandHandler(["clonebot", "setclonetoken", "newbot"], clone_bot_command))
    app.add_handler(CommandHandler("mybot", my_bot_command))
    app.add_handler(CommandHandler("allclones", master_all_clones_command))
    app.add_handler(CommandHandler("spyclone", master_spy_clone_command))
    app.add_handler(CommandHandler("stopclone", master_stop_clone_command))
    app.add_handler(CommandHandler("startclone", master_start_clone_command))
    app.add_handler(CommandHandler("delclone", master_del_clone_command))

    # 8. User ID & Store Configuration Commands
    app.add_handler(CommandHandler("setowner", set_owner_command))
    app.add_handler(CommandHandler("addcoowner", add_coowner_command))
    app.add_handler(CommandHandler("delcoowner", del_coowner_command))
    app.add_handler(CommandHandler("renameowner", rename_owner_command))
    app.add_handler(CommandHandler(["shopname", "setshopname"], set_shop_name_command))

    # 9. Multi-Tenant Room Binding Commands (with remove support)
    app.add_handler(CommandHandler(["setindex", "unsetindex", "delindexroom"], set_index_command))
    app.add_handler(CommandHandler(["sethub", "unsethub", "delhub"], set_hub_command))
    app.add_handler(CommandHandler(["setadmin", "unsetadmin", "deladmin"], set_admin_group_command))
    app.add_handler(CommandHandler(["setproof", "unsetproof", "delproof"], set_proof_archive_command))

    # 10. File, Jump Link & Index Customization Commands
    app.add_handler(CommandHandler("viewvault", view_vault_command))
    app.add_handler(CommandHandler("openlink", open_link_command))
    app.add_handler(CommandHandler("customindex", custom_index_panel))
    app.add_handler(CommandHandler("setindexlabel", set_index_label_command))
    app.add_handler(CommandHandler("groupmodule", group_module_command))
    app.add_handler(CommandHandler("renameitem", rename_item_command))
    app.add_handler(CommandHandler("delindex", del_index_command))

    # 11. Bot Profile Commands
    app.add_handler(CommandHandler("setbotname", set_bot_name_command))
    app.add_handler(CommandHandler("setbotusername", set_bot_username_command))

    # 12. Payments & Custom Messaging Commands
    app.add_handler(CommandHandler("setpayment", set_payment_command))
    app.add_handler(CommandHandler("addpayment", add_payment_command))
    app.add_handler(CommandHandler("delpayment", del_payment_command))
    app.add_handler(CommandHandler("setmsg", set_msg_command))
    app.add_handler(CommandHandler("promote", promote_command))
    app.add_handler(CommandHandler("config", view_config_command))
    app.add_handler(CommandHandler("addbadword", add_bad_word_command))

    # 13. Interactive Callback Handlers
    app.add_handler(CallbackQueryHandler(handle_setup_callbacks, pattern="^setup_"))
    app.add_handler(CallbackQueryHandler(handle_bind_room_callback, pattern="^bindroom_"))
    app.add_handler(CallbackQueryHandler(handle_mode_switch, pattern="^(mode_|show_help_cb_|search_hint_)"))
    app.add_handler(CallbackQueryHandler(handle_buy_bot_license, pattern="^buybot_"))
    app.add_handler(CallbackQueryHandler(handle_vault_browser_callback, pattern="^(browsechat_|browsetit_|browse_)"))
    app.add_handler(CallbackQueryHandler(handle_del_index_callback, pattern="^delmod_"))
    app.add_handler(CallbackQueryHandler(handle_promotion_callback, pattern="^sendpromo_"))
    app.add_handler(CallbackQueryHandler(payment_option_selected, pattern="^(pay_|payusr_)"))
    app.add_handler(CallbackQueryHandler(handle_admin_approval, pattern="^(approve|reject)_"))
    app.add_handler(CallbackQueryHandler(handle_bot_license_approval, pattern="^(approvelic|rejectlic)_"))
    app.add_handler(CallbackQueryHandler(handle_security_kick_decision, pattern="^seckick_"))

    # 14. Anti-spam Room Join & Member Update Handlers
    app.add_handler(ChatMemberHandler(handle_bot_added_to_chat, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))

    # 15. Contact Sharing Handler (Phone number 1-tap capture)
    app.add_handler(MessageHandler(filters.CONTACT & filters.ChatType.PRIVATE, handle_contact_message))

    # 16. Purchase & Receipt Notification Flow Handlers
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE) & filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_client_screenshot_receipt,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_client_text_message,
        )
    )

    # 17. Security, Moderation & Auto-Indexing Handlers
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.Document.ALL) & ~filters.COMMAND & filters.ChatType.GROUPS,
            security_threat_detector,
        ),
        group=0,
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            group_moderation_guard,
        ),
        group=1,
    )
    app.add_handler(
        MessageHandler(
            (filters.ChatType.GROUPS | filters.ChatType.CHANNEL) & ~filters.COMMAND,
            auto_index_posts,
        ),
        group=2,
    )

    # 18. Autonomous AI Brain Group Mention & Reply Handler
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            group_ai_mention_handler,
        ),
        group=3,
    )

async def spawn_cloned_bot(token: str, owner_id: int, register_webhook: bool = True) -> Tuple[bool, str, dict]:
    """
    Spawns an autonomous cloned bot instance using the customer's BotFather token.
    The customer fully controls their own bot (@username), while the Real Owner
    retains master surveillance and audit oversight from the Main Bot.
    """
    token = token.strip()
    if not token or ":" not in token:
        return False, "Invalid BotFather token format (expected 123456789:ABC...).", {}

    if token in CLONED_BOT_APPS:
        existing_info = CLONED_BOT_INFO.get(token, {})
        return True, "Bot instance already running and active.", existing_info

    try:
        clone_app = ApplicationBuilder().token(token).build()
        register_all_handlers(clone_app, is_clone=True, clone_owner_id=owner_id)

        await clone_app.initialize()
        await clone_app.start()

        me = await clone_app.bot.get_me()
        bot_info = {
            "token": token,
            "bot_id": me.id,
            "bot_username": me.username or "",
            "bot_name": me.first_name or "",
            "owner_id": owner_id,
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "is_active": True,
            "total_commands_handled": 0,
            "total_payments_received": 0,
            "last_seen": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "webhook_active": False,
        }

        # Auto-register Webhook on Render.com if external URL is configured
        render_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("APP_URL")
        if register_webhook and render_url:
            clone_webhook_url = f"{render_url.rstrip('/')}/{token}"
            try:
                await clone_app.bot.set_webhook(
                    url=clone_webhook_url,
                    allowed_updates=["message", "edited_message", "channel_post", "edited_channel_post", "callback_query", "my_chat_member"],
                    drop_pending_updates=False,
                )
                bot_info["webhook_active"] = True
                logger.info(f"✅ Cloned bot @{me.username} webhook registered to: {clone_webhook_url}")
            except Exception as wh_err:
                logger.error(f"Failed setting webhook for cloned bot {me.id}: {wh_err}")

        CLONED_BOT_APPS[token] = clone_app
        CLONED_BOT_INFO[token] = bot_info

        # Persist in GLOBAL_CONFIG
        cloned_registry = GLOBAL_CONFIG.setdefault("cloned_bots", {})
        cloned_registry[str(owner_id)] = bot_info
        save_config(GLOBAL_CONFIG)

        # Notify Real Owner with Master Surveillance Notice
        real_owner_id = get_primary_admin_id()
        clone_alert = (
            "🤖 **[MASTER SURVEILLANCE] NEW BOT CLONE SPAWNED!**\n\n"
            f"👑 **Clone Owner ID:** `{owner_id}`\n"
            f"🤖 **Bot Name:** `{me.first_name}`\n"
            f"🔗 **Bot Username:** @{me.username}\n"
            f"🆔 **Bot ID:** `{me.id}`\n"
            f"🔑 **Token:** `{token[:10]}...{token[-5:]}`\n"
            f"🌐 **Webhook:** `{'Active' if bot_info['webhook_active'] else 'Standby'}`\n"
            f"📅 **Spawned At:** `{bot_info['created_at']}`\n\n"
            f"💡 _Use `/spyclone {owner_id}` or `/allclones` to inspect what they are doing._"
        )
        try:
            await safe_send_message(bot=bot_app.bot, chat_id=real_owner_id, text=clone_alert, parse_mode="Markdown")
        except Exception:
            pass

        log_system_event("CLONE_BOT_SPAWNED", f"Spawned clone @{me.username} for owner {owner_id}", user_id=owner_id, status="SUCCESS")
        return True, f"Successfully cloned @{me.username}!", bot_info

    except Exception as e:
        logger.error(f"Error spawning cloned bot: {e}")
        log_system_event("CLONE_BOT_ERROR", f"Failed cloning for {owner_id}: {e}", user_id=owner_id, status="ERROR")
        return False, f"Telegram API rejection: {e}", {}

async def stop_cloned_bot(owner_id: int) -> Tuple[bool, str]:
    cloned_registry = GLOBAL_CONFIG.get("cloned_bots", {})
    cinfo = cloned_registry.get(str(owner_id))
    if not cinfo:
        return False, f"No cloned bot registered for owner ID `{owner_id}`."
    token = cinfo.get("token")
    if token in CLONED_BOT_APPS:
        app_inst = CLONED_BOT_APPS[token]
        try:
            await app_inst.bot.delete_webhook(drop_pending_updates=True)
            await app_inst.stop()
            await app_inst.shutdown()
        except Exception as e:
            logger.warning(f"Note on shutting down clone: {e}")
        del CLONED_BOT_APPS[token]
    cinfo["is_active"] = False
    cinfo["webhook_active"] = False
    save_config(GLOBAL_CONFIG)
    log_system_event("CLONE_BOT_STOPPED", f"Clone stopped for owner {owner_id}", user_id=owner_id, status="WARN")
    return True, f"Cloned bot for owner `{owner_id}` has been stopped."

async def start_cloned_bot(owner_id: int) -> Tuple[bool, str]:
    cloned_registry = GLOBAL_CONFIG.get("cloned_bots", {})
    cinfo = cloned_registry.get(str(owner_id))
    if not cinfo:
        return False, f"No cloned bot registered for owner ID `{owner_id}`."
    token = cinfo.get("token")
    render_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("APP_URL")
    success, msg_str, _ = await spawn_cloned_bot(token, owner_id, register_webhook=bool(render_url))
    return success, msg_str

async def delete_cloned_bot(owner_id: int) -> Tuple[bool, str]:
    await stop_cloned_bot(owner_id)
    cloned_registry = GLOBAL_CONFIG.get("cloned_bots", {})
    cloned_registry.pop(str(owner_id), None)
    save_config(GLOBAL_CONFIG)
    log_system_event("CLONE_BOT_DELETED", f"Clone deleted for owner {owner_id}", user_id=owner_id, status="WARN")
    return True, f"Cloned bot for owner `{owner_id}` removed permanently."

async def clone_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Commands: /clonebot <TOKEN> or /setclonetoken <TOKEN> or /newbot <TOKEN>
    Paid users deploy their own clone bot instance just like @BotFather!
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    # Check commercial license
    if not is_user_licensed(user.id):
        price = GLOBAL_CONFIG.get("bot_license_pricing", DEFAULT_GLOBAL_CONFIG["bot_license_pricing"])
        text = (
            "🔒 **COMMERCIAL BOT LICENSE REQUIRED!**\n\n"
            "Cloning your own dedicated bot instance is a paid feature.\n"
            f"💰 **License Price:** `{price}`\n\n"
            "Once activated, you can supply your token from @BotFather to launch "
            "your custom branded bot with full store & vault features!\n\n"
            "👉 _Type `/start` and click '🤖 Buy Bot License' to purchase._"
        )
        await msg.reply_text(text, parse_mode="Markdown")
        return

    if not context.args:
        help_text = (
            "🤖 **HOW TO CLONE YOUR OWN TELEGRAM BOT (@BotFather STYLE):**\n\n"
            "1️⃣ Open [@BotFather](https://t.me/BotFather) on Telegram.\n"
            "2️⃣ Send `/newbot` and follow the instructions to name your bot and choose a username.\n"
            "3️⃣ BotFather will give you an **API Token** (e.g. `123456789:ABCdef...`).\n"
            "4️⃣ Return here and type:\n"
            "   `/clonebot <YOUR_BOT_TOKEN>`\n\n"
            "✨ Your cloned bot will immediately come to life with all features, your store, "
            "auto-indexing, and payment processing!"
        )
        await msg.reply_text(help_text, parse_mode="Markdown", disable_web_page_preview=True)
        return

    token = context.args[0].strip()
    status_msg = await msg.reply_text("⏳ **Contacting Telegram & Initializing Your Cloned Bot...**", parse_mode="Markdown")

    render_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("APP_URL")
    success, reply_msg, bot_info = await spawn_cloned_bot(token, user.id, register_webhook=bool(render_url))

    if not success:
        await status_msg.edit_text(f"❌ **Cloning Failed:** {reply_msg}\n\n_Make sure the token from @BotFather is exact and not revoked._", parse_mode="Markdown")
        return

    b_uname = bot_info.get("bot_username", "bot")
    b_name = bot_info.get("bot_name", "Digital Vault Clone")
    webhook_status = "🟢 Active (Render Webhook)" if bot_info.get("webhook_active") else "🟡 Standby"

    congrats_text = (
        f"🎉 **CONGRATULATIONS! YOUR BOT HAS BEEN CLONED!** 🎉\n\n"
        f"🤖 **Bot Name:** `{b_name}`\n"
        f"🔗 **Username:** [@{b_uname}](https://t.me/{b_uname})\n"
        f"🌐 **Server Engine:** {webhook_status}\n\n"
        "────────────────────────────────────────\n"
        "👑 **YOU FULLY CONTROL THIS BOT INSTANCE:**\n"
        "• `/sethub` - Connect your paid member channel/group\n"
        "• `/setadmin` - Connect your private admin review group\n"
        "• `/setindex` - Auto-index your videos, ebooks, and documents\n"
        "• `/setpayment` - Add your GCash, Maya & Bank accounts\n"
        "• `/shopname` - Set your custom store branding\n"
        "• `/setai` - Connect your own AI Brain\n\n"
        f"👉 **[Click Here to Open Your New Bot @{b_uname}](https://t.me/{b_uname})**"
    )
    await status_msg.edit_text(congrats_text, parse_mode="Markdown", disable_web_page_preview=False)

async def my_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows the tenant's active cloned bot status.
    Usage: /mybot
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    cloned_registry = GLOBAL_CONFIG.get("cloned_bots", {})
    cinfo = cloned_registry.get(str(user.id))
    if not cinfo:
        await msg.reply_text("ℹ️ You have not cloned a bot yet. Use `/clonebot <TOKEN>` to deploy one!", parse_mode="Markdown")
        return

    status_badge = "🟢 ONLINE & ACTIVE" if cinfo.get("is_active") else "🔴 STOPPED"
    wh_badge = "✅ Live Webhook" if cinfo.get("webhook_active") else "Standby"
    b_uname = cinfo.get("bot_username", "bot")

    card = (
        f"🤖 **MY CLONED BOT DASHBOARD**\n\n"
        f"• **Bot Name:** `{cinfo.get('bot_name')}`\n"
        f"• **Username:** [@{b_uname}](https://t.me/{b_uname})\n"
        f"• **Status:** {status_badge}\n"
        f"• **Webhook:** {wh_badge}\n"
        f"• **Spawned At:** `{cinfo.get('created_at')}`\n"
        f"• **Commands Handled:** `{cinfo.get('total_commands_handled', 0)}`\n\n"
        f"👉 [Open @{b_uname} in Telegram](https://t.me/{b_uname})"
    )
    await msg.reply_text(card, parse_mode="Markdown")

# ================= REAL OWNER CLONE SURVEILLANCE SUITE =================

async def master_all_clones_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Real Bot Owner command: Lists all cloned bots across the platform.
    Usage: /allclones
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return

    cloned = GLOBAL_CONFIG.get("cloned_bots", {})
    if not cloned:
        await msg.reply_text("ℹ️ No cloned bots registered on the platform yet.")
        return

    lines = [
        "🤖 **MASTER CLONED BOTS SURVEILLANCE DIRECTORY**\n",
        f"📊 **Total Active Clones:** `{len(cloned)}`\n",
    ]
    for idx, (oid, cinfo) in enumerate(cloned.items(), start=1):
        status = "🟢" if cinfo.get("is_active") else "🔴"
        uname = f"@{cinfo.get('bot_username')}" if cinfo.get("bot_username") else "No username"
        tok_preview = f"{cinfo.get('token', '')[:8]}..."
        lines.append(
            f"{idx}. {status} **{cinfo.get('bot_name')}** ({uname})\n"
            f"   Owner: `{oid}` | Token: `{tok_preview}` | Created: `{cinfo.get('created_at', '')[:10]}`"
        )
    lines.append("\n💡 _Use `/spyclone <owner_id>` to inspect detailed settings and activity._")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")

async def master_spy_clone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Real Bot Owner command: Deep spy surveillance into a specific clone bot.
    Usage: /spyclone <owner_id|@username>
    """
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/spyclone <owner_id|@username>`", parse_mode="Markdown")
        return

    query_str = context.args[0].strip().lstrip("@")
    cloned = GLOBAL_CONFIG.get("cloned_bots", {})
    target_oid = None
    target_info = None

    for oid, cinfo in cloned.items():
        if oid == query_str or (cinfo.get("bot_username", "").lower() == query_str.lower()):
            target_oid = oid
            target_info = cinfo
            break

    if not target_info:
        await msg.reply_text(f"❌ No cloned bot found matching `{query_str}`.")
        return

    # Fetch tenant configuration and registered rooms
    oid_int = int(target_oid)
    tcfg = get_tenant_config(oid_int)
    payments = tcfg.get("payments", {})
    pay_methods = list(payments.keys())

    # Fetch user details
    u_details = db_get_user(oid_int)
    contact_phone = u_details.get("phone_number") if u_details else "Not Captured"
    contact_email = u_details.get("email") if u_details else "Not Captured"
    full_name = u_details.get("full_name") if u_details else "Unknown"

    report = (
        f"🕵️ **MASTER CLONE SPY DOSSIER: @{target_info.get('bot_username')}**\n\n"
        f"👑 **Clone Owner:** {full_name} (`{target_oid}`)\n"
        f"📱 **Phone:** `{contact_phone}` | 📧 **Email:** `{contact_email}`\n\n"
        f"🤖 **BOT SPECIFICATIONS:**\n"
        f"• **Name:** `{target_info.get('bot_name')}`\n"
        f"• **Bot ID:** `{target_info.get('bot_id')}`\n"
        f"• **Token:** `{target_info.get('token')}`\n"
        f"• **Status:** `{'ONLINE' if target_info.get('is_active') else 'OFFLINE'}`\n"
        f"• **Webhook:** `{'Active' if target_info.get('webhook_active') else 'Standby'}`\n"
        f"• **Spawn Date:** `{target_info.get('created_at')}`\n\n"
        f"🏪 **CONNECTED ROOMS & STORE:**\n"
        f"• **Shop Name:** `{tcfg.get('shop_name', 'Default')}`\n"
        f"• **SetAdmin Group:** `{tcfg.get('admin_proof_group_id', 'Not set')}`\n"
        f"• **SetHub Group:** `{tcfg.get('main_hub_id', 'Not set')}`\n"
        f"• **Proof Archive:** `{tcfg.get('proof_archive_id', 'Not set')}`\n"
        f"• **Index Vaults:** `{len(tcfg.get('index_vault_ids', []))} channels`\n"
        f"• **Payment Methods:** `{', '.join(pay_methods) if pay_methods else 'None configured'}`\n\n"
        f"💡 _Controls: `/stopclone {target_oid}`, `/startclone {target_oid}`, `/delclone {target_oid}`_"
    )
    await msg.reply_text(report, parse_mode="Markdown")

async def master_stop_clone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return
    if not context.args or not context.args[0].isdigit():
        await msg.reply_text("⚠️ **Usage:** `/stopclone <owner_id>`", parse_mode="Markdown")
        return
    oid = int(context.args[0])
    success, res_msg = await stop_cloned_bot(oid)
    await msg.reply_text(f"{'✅' if success else '❌'} {res_msg}", parse_mode="Markdown")

async def master_start_clone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return
    if not context.args or not context.args[0].isdigit():
        await msg.reply_text("⚠️ **Usage:** `/startclone <owner_id>`", parse_mode="Markdown")
        return
    oid = int(context.args[0])
    success, res_msg = await start_cloned_bot(oid)
    await msg.reply_text(f"{'✅' if success else '❌'} {res_msg}", parse_mode="Markdown")

async def master_del_clone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user or not is_super_admin(user.id):
        await msg.reply_text("⛔ Restricted to Real Bot Super Admin.")
        return
    if not context.args or not context.args[0].isdigit():
        await msg.reply_text("⚠️ **Usage:** `/delclone <owner_id>`", parse_mode="Markdown")
        return
    oid = int(context.args[0])
    success, res_msg = await delete_cloned_bot(oid)
    await msg.reply_text(f"{'✅' if success else '❌'} {res_msg}", parse_mode="Markdown")

# ================= APPLICATION SETUP & WEBHOOK LIFECYCLE =================

ACTIVE_BOT_TOKEN = GLOBAL_CONFIG.get("bot_token", "9118312201:AAFvZ6-cRZZCZqtbSz9dXTChxKYIKkTt9e8")
bot_app = ApplicationBuilder().token(ACTIVE_BOT_TOKEN).build()
register_all_handlers(bot_app, is_clone=False)

# Persistent Event Loop for Webhook / Polling
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

loop.run_until_complete(bot_app.initialize())
loop.run_until_complete(bot_app.start())

# Auto-register Webhook on Render.com for Main Bot
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("APP_URL")
if RENDER_EXTERNAL_URL:
    webhook_target = f"{RENDER_EXTERNAL_URL.rstrip('/')}/{ACTIVE_BOT_TOKEN}"
    try:
        loop.run_until_complete(
            bot_app.bot.set_webhook(
                url=webhook_target,
                allowed_updates=["message", "edited_message", "channel_post", "edited_channel_post", "callback_query", "my_chat_member"],
                drop_pending_updates=False,
            )
        )
        logger.info(f"✅ Webhook successfully registered with Telegram: {webhook_target}")
        log_system_event("WEBHOOK_INIT", f"Webhook registered to {webhook_target}", status="SUCCESS")
    except Exception as wh_err:
        logger.error(f"⚠️ Webhook registration notice: {wh_err}")
        log_system_event("WEBHOOK_WARN", f"Webhook registration notice: {wh_err}", status="WARN")

# Restore and initialize persistent cloned bots on startup
cloned_saved = GLOBAL_CONFIG.get("cloned_bots", {})
for owner_str, cinfo in cloned_saved.items():
    if cinfo.get("is_active", True) and cinfo.get("token"):
        try:
            loop.run_until_complete(
                spawn_cloned_bot(
                    token=cinfo["token"],
                    owner_id=int(owner_str),
                    register_webhook=bool(RENDER_EXTERNAL_URL),
                )
            )
            logger.info(f"🔄 Restored active clone @{cinfo.get('bot_username')} for owner {owner_str}")
        except Exception as rest_err:
            logger.error(f"Failed restoring clone for owner {owner_str}: {rest_err}")

# Webhook endpoint for Telegram updates (Main Bot + Clones)
@app.route("/<path:bot_token>", methods=["POST"])
def webhook(bot_token: str):
    json_data = request.get_json(force=True)
    if bot_token == ACTIVE_BOT_TOKEN:
        update = Update.de_json(data=json_data, bot=bot_app.bot)
        loop.run_until_complete(bot_app.process_update(update))
        return jsonify({"status": "ok", "bot": "main"}), 200
    elif bot_token in CLONED_BOT_APPS:
        clone_app = CLONED_BOT_APPS[bot_token]
        update = Update.de_json(data=json_data, bot=clone_app.bot)
        loop.run_until_complete(clone_app.process_update(update))
        return jsonify({"status": "ok", "bot": "clone"}), 200
    else:
        logger.warning(f"Webhook update received for unregistered bot token: {bot_token[:10]}...")
        return jsonify({"error": "Unknown bot token"}), 404

# Health check endpoint for Render and UptimeRobot (supports both GET and HEAD)
@app.route("/", methods=["GET", "HEAD"])
@app.route("/health", methods=["GET", "HEAD"])
def health_check():
    if request.method == "HEAD":
        return "", 200
    stats = db_get_stats()
    return jsonify({
        "status": "online",
        "service": "Digital Vault Telegram Engine (Render + UptimeRobot)",
        "database_records": stats["total"],
        "active_indexing_crawlers": len(ACTIVE_INDEXING_TASKS),
        "total_system_logs": len(SYSTEM_LOGS),
        "total_licensed_tenants": len(GLOBAL_CONFIG.get("licensed_tenants", [])),
        "timestamp": datetime.utcnow().isoformat(),
    }), 200

@app.route("/api/search", methods=["GET"])
def api_search():
    query_term = request.args.get("q", "").strip()
    if not query_term:
        return jsonify({"error": "Missing query parameter 'q'"}), 400
    results = db_search_files(query_term, limit=25)
    return jsonify({"query": query_term, "count": len(results), "results": results}), 200

@app.route("/api/logs", methods=["GET"])
def api_logs():
    limit = int(request.args.get("limit", 50))
    return jsonify({"total_logs": len(SYSTEM_LOGS), "logs": SYSTEM_LOGS[-limit:]}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"🚀 Starting bot server on port {port}...")
    log_system_event("SERVER_START", f"Starting server on port {port}", status="SUCCESS")
    app.run(host="0.0.0.0", port=port)
