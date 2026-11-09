import os
import json
import logging
import asyncio
import re
from flask import Flask, request, jsonify

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

# ================= CONFIGURATION & CONSTANTS =================

CONFIG_FILE = "bot_config.json"
STATE_FILE = "vault_state.json"

# Setup Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Master Bot Owner Telegram IDs (Super Admins with Global System Rights)
SUPER_ADMIN_IDS = [5331007152]

# Default Tenant Configuration Template for Multi-Tenant Support
DEFAULT_TENANT_CONFIG = {
    "main_hub_id": 0,
    "admin_proof_group_id": 0,
    "proof_archive_id": 0,
    "shop_name": "Digital Vault Store",
    "primary_owner_id": 0,
    "co_owners": [],
    "user_id_labels": {},  # { "123456789": "Manager John" }
    "indexing_settings": {
        "auto_index": True,
        "custom_label": "Lesson",
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
            "• **Members:** You are permitted to use `/start` (to browse and checkout) and `/help` (for instructions).\n"
            "• **Admins & Owners:** Advanced store & hub controls (`/admin`, `/setup`, `/sethub`, `/setadmin`, `/setproof`, etc.) are strictly locked to verified Administrators and Owners.\n\n"
            "─── 🛒 **STEP-BY-STEP PURCHASING GUIDE FOR MEMBERS** ───\n"
            "1️⃣ **Start Checkout:** Type `/start` in private DM (or click *Browse Store & Buy Access*).\n"
            "2️⃣ **Select Payment Method:** Choose your preferred payment option (GCash, Maya, MariBank, Bank Transfer, or Crypto).\n"
            "3️⃣ **Make Transfer:** Send the exact amount using the provided account details and reference number.\n"
            "4️⃣ **Submit Receipt:** Take a photo or screenshot of your successful transaction receipt and send it directly to this chat.\n"
            "5️⃣ **Receive Single-Use Link:** Our admin team will verify your receipt. Once approved, the bot will automatically send you a private single-use invite link to the Main Directory Hub!\n\n"
            "─── 📚 **NAVIGATING COURSES, EBOOKS & FILES** ───\n"
            "• Join the **Main Directory Hub** using your unique invite link.\n"
            "• Inside the hub, click any **[Lesson Link]**, **[Ebook Link]**, **[Software/APK Link]**, or **[Code Link]** to jump directly to your material inside our vault channels!\n\n"
            "─── ⚙️ **FOR STORE OWNERS & MANAGERS ONLY** ───\n"
            "• Type `/setup` in private DM to open the Setup Wizard.\n"
            "• Type `/admin` to open your **Admin Control Desk**.\n"
            "• Type `/customindex` to customize indexing labels and course arrangements."
        ),
        "receipt": "⌛ **Receipt Submitted!**\n\nThank you {name}. Our admin team is verifying your payment. Once approved, you will automatically receive your invite link here!"
    },
    "moderation": {
        "enabled": True,
        "bad_words": ["scam", "hack", "free money", "gambling", "crypto free"],
        "block_links": True,
        "whitelisted_domains": ["t.me", "telegram.org", "drive.google.com"]
    },
    "security": {
        "phishing_keywords": ["bot token", "session file", "otp code", "telegram login", "stealer", "token grabber"],
        "suspicious_extensions": [".exe", ".scr", ".bat", ".cmd", ".vbs", ".apk"]
    }
}

DEFAULT_GLOBAL_CONFIG = {
    "bot_token": os.getenv("TELEGRAM_TOKEN", "9118312201:AAFvZ6-cRZZCZqtbSz9dXTChxKYIKkTt9e8"),
    "super_admin_ids": SUPER_ADMIN_IDS,
    "settings": {
        "allow_admin_member_cmds": False,
    },
    "tenants": {},
    "chat_tenant_map": {}  # Maps { "chat_id": owner_id }
}

# ================= PERSISTENT STORAGE HELPERS =================

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                if "tenants" not in config:
                    config["tenants"] = {}
                if "chat_tenant_map" not in config:
                    config["chat_tenant_map"] = {}
                if "super_admin_ids" not in config:
                    config["super_admin_ids"] = SUPER_ADMIN_IDS
                if "bot_token" not in config:
                    config["bot_token"] = DEFAULT_GLOBAL_CONFIG["bot_token"]
                if "settings" not in config:
                    config["settings"] = DEFAULT_GLOBAL_CONFIG["settings"]
                return config
        except Exception as e:
            logger.error(f"Error loading config file: {e}")
            return DEFAULT_GLOBAL_CONFIG
    return DEFAULT_GLOBAL_CONFIG

def save_config(config):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving config file: {e}")

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                res = {}
                for k, v in data.items():
                    try:
                        res[int(k)] = v
                    except ValueError:
                        res[k] = v
                return res
        except Exception as e:
            logger.error(f"Error loading state file: {e}")
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving state file: {e}")

GLOBAL_CONFIG = load_config()
VAULT_STATE = load_state()
PENDING_PROMOTIONS = {}

app = Flask(__name__)

# Helper function to prevent Markdown parsing crashes on raw filenames/titles
async def safe_send_message(bot, chat_id, text, parse_mode="Markdown", reply_markup=None, disable_web_page_preview=True):
    try:
        return await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview
        )
    except Exception as e:
        logger.warning(f"Failed to send Markdown message to {chat_id}: {e}. Retrying with plain text...")
        clean_text = text.replace("**", "").replace("__", "").replace("`", "").replace("*", "").replace("_", "")
        try:
            return await bot.send_message(
                chat_id=int(chat_id),
                text=clean_text,
                parse_mode=None,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview
            )
        except Exception as err:
            logger.error(f"Critical error sending message to {chat_id}: {err}")
            return None

async def safe_send_photo(bot, chat_id, photo, caption, reply_markup=None, parse_mode="Markdown"):
    try:
        return await bot.send_photo(
            chat_id=int(chat_id),
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.warning(f"Failed sending formatted photo caption to {chat_id}: {e}. Retrying plain text...")
        clean_caption = caption.replace("**", "").replace("`", "").replace("*", "").replace("_", "")
        try:
            return await bot.send_photo(
                chat_id=int(chat_id),
                photo=photo,
                caption=clean_caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        except Exception as err:
            logger.error(f"Critical failure sending photo to {chat_id}: {err}")
            return None

def normalize_title(title: str) -> str:
    return title.strip().lower()

def get_tenant_config(owner_id: int):
    owner_str = str(owner_id)
    tenants = GLOBAL_CONFIG.setdefault("tenants", {})
    if owner_str not in tenants:
        new_tenant = json.loads(json.dumps(DEFAULT_TENANT_CONFIG))
        new_tenant["primary_owner_id"] = owner_id
        tenants[owner_str] = new_tenant
        save_config(GLOBAL_CONFIG)
    return tenants[owner_str]

def get_tenant_by_chat(chat_id: int):
    chat_str = str(chat_id)
    chat_map = GLOBAL_CONFIG.get("chat_tenant_map", {})
    if chat_str in chat_map:
        owner_id = chat_map[chat_str]
        return owner_id, get_tenant_config(owner_id)
    return None, None

def is_super_admin(user_id: int) -> bool:
    return user_id in GLOBAL_CONFIG.get("super_admin_ids", SUPER_ADMIN_IDS)

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
        if owner_id and (user_id == owner_id or user_id in tenant_cfg.get("co_owners", [])):
            return True

        if chat.type in ["group", "supergroup", "channel"]:
            try:
                member = await context.bot.get_chat_member(chat.id, user_id)
                if member.status in ["administrator", "creator"]:
                    return True
            except Exception as e:
                logger.error(f"Error checking chat permissions: {e}")

    owner_str = str(user_id)
    if owner_str in GLOBAL_CONFIG.get("tenants", {}):
        return True

    return False

# ================= ENHANCED MEMBER HELP COMMAND & USER ONBOARDING =================

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
            "Member help instructions (`/help`) are currently hidden for Admins in group chats to keep views focused on management.\n\n"
            "• Type `/setup` in private DM to open your **Full Setup Wizard**.\n"
            "• Type `/admin` to open your **Admin Control Panel**.",
            parse_mode="Markdown"
        )
        return

    owner_id, tenant_cfg = get_tenant_by_chat(msg.chat.id) if msg.chat else (None, None)
    if not tenant_cfg:
        tenant_cfg = get_tenant_config(user.id)

    shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")
    template = tenant_cfg["messages"].get("help", DEFAULT_TENANT_CONFIG["messages"]["help"])
    help_text = template.replace("{name}", user.full_name).replace("{shop_name}", shop_name)

    keyboard = [
        [InlineKeyboardButton("🛒 Start Buying & Checkout Now", callback_data=f"mode_customer_{tenant_cfg.get('primary_owner_id', user.id)}")],
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("⚙️ Manager Setup Wizard", callback_data=f"setup_main_{user.id}")])

    await msg.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    is_admin = await is_admin_or_owner(update, context)
    allow_admin = GLOBAL_CONFIG.get("settings", {}).get("allow_admin_member_cmds", False)

    if is_admin and not allow_admin and msg.chat.type != "private":
        await msg.reply_text(
            "ℹ️ **ADMIN NOTICE**\n\n"
            "Member checkout (`/start`) is currently hidden for admins in group chats.\n"
            "• Type `/setup` in private DM to open your **Full Setup Wizard**.\n"
            "• Type `/admin` to open your **Admin Control Panel**.",
            parse_mode="Markdown"
        )
        return

    tenant_cfg = get_tenant_config(user.id)
    shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")

    keyboard = [
        [InlineKeyboardButton("🛒 Browse Store & Buy Access", callback_data=f"mode_customer_{user.id}")],
        [InlineKeyboardButton("📖 Read How to Buy (/help)", callback_data=f"show_help_cb_{user.id}")],
    ]
    if is_admin or msg.chat.type == "private":
        keyboard.append([InlineKeyboardButton("⚙️ Setup & Manage My Own Vault / Groups", callback_data=f"setup_main_{user.id}")])

    welcome_text = (
        f"👋 **Welcome to {shop_name}, {user.full_name}!**\n\n"
        "How would you like to proceed?\n\n"
        "• Click **Browse Store** to view available payment methods and buy access.\n"
        "• Click **Read How to Buy** for step-by-step instructions.\n"
        "ℹ️ _Note: Regular members can only use `/start` and `/help`. Advanced management features (`/admin`, `/sethub`, etc.) are restricted to Administrators & Owners._"
    )
    await msg.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_mode_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data

    if data.startswith("mode_customer"):
        parts = data.split("_")
        owner_id = int(parts[2]) if len(parts) > 2 else user.id
        tenant_cfg = get_tenant_config(owner_id)
        shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")

        template = tenant_cfg["messages"].get("welcome", DEFAULT_TENANT_CONFIG["messages"]["welcome"])
        welcome_text = template.replace("{name}", user.full_name).replace("{shop_name}", shop_name)

        keyboard = []
        payments = tenant_cfg.get("payments", DEFAULT_TENANT_CONFIG["payments"])
        for key, val in payments.items():
            title_line = val.split("\n")[0].replace("*", "").replace("📱", "").replace("🏦", "").replace("💰", "").strip()
            keyboard.append([InlineKeyboardButton(title_line, callback_data=f"payusr_{owner_id}_{key}")])

        keyboard.append([InlineKeyboardButton("📖 How to Buy Instructions", callback_data=f"show_help_cb_{owner_id}")])
        await query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("show_help_cb"):
        parts = data.split("_")
        owner_id = int(parts[3]) if len(parts) > 3 else user.id
        tenant_cfg = get_tenant_config(owner_id)
        shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")

        template = tenant_cfg["messages"].get("help", DEFAULT_TENANT_CONFIG["messages"]["help"])
        help_text = template.replace("{name}", user.full_name).replace("{shop_name}", shop_name)

        keyboard = [
            [InlineKeyboardButton("🛒 Start Buying Now", callback_data=f"mode_customer_{owner_id}")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data=f"mode_customer_{owner_id}")],
        ]
        await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ================= DM SETUP WIZARD & USER ID MANAGEMENT =================

async def setup_wizard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text(
            "⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\n"
            "Advanced management features (like `/setup`, `/admin`, `/sethub`) are strictly restricted to Administrators and Vault Owners.",
            parse_mode="Markdown"
        )
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
        [InlineKeyboardButton("🔍 Open Vault Content Inspector", callback_data="browse_BACK")],
    ]

    setup_text = (
        f"⚙️ **FULL DM SETUP WIZARD: {shop_name.upper()}**\n\n"
        f"👤 **Primary Owner ID:** `{tenant_cfg.get('primary_owner_id', user.id)}`\n"
        f"👥 **Co-Owners:** `{len(co_owners)} registered`\n\n"
        "📋 **ACTIVE ROOM STATUS:**\n"
        f"• 📍 **Main Directory Hub:** `{hub_id if hub_id else 'Not Set'}`\n"
        f"• 📍 **Admin Verification Group:** `{admin_id if admin_id else 'Not Set'}`\n"
        f"• 📍 **Proof Archive:** `{proof_id if proof_id else 'Not Set'}`\n\n"
        "────────────────────────────────────────\n"
        "💡 _Select an item below to configure your store settings directly inside DM!_"
    )
    await msg.reply_text(setup_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_setup_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_admin_or_owner(update, context):
        await query.answer("⛔ Access Denied: Administrator or Owner permission required.", show_alert=True)
        return

    await query.answer()
    user = query.from_user
    data = query.data

    tenant_cfg = get_tenant_config(user.id)

    if data.startswith("setup_rooms"):
        hub_id = tenant_cfg.get("main_hub_id", 0)
        admin_id = tenant_cfg.get("admin_proof_group_id", 0)
        proof_id = tenant_cfg.get("proof_archive_id", 0)

        text = (
            "🏨 **ROOM BINDING SETUP GUIDE**\n\n"
            "To assign room roles:\n"
            "1️⃣ Add this bot as an **Admin** in your target group or channel.\n"
            "2️⃣ Once added, the bot will send you an **Instant DM Alert** with 1-click binding buttons!\n"
            "3️⃣ Or open the target chat and run:\n"
            "   • `/sethub` inside your Main Directory Hub (run again to remove)\n"
            "   • `/setadmin` inside your Admin Review Group (run again to remove)\n"
            "   • `/setproof` inside your Proof Archive Channel (run again to remove)\n\n"
            f"📍 Current Hub: `{hub_id}`\n"
            f"📍 Current Admin Group: `{admin_id}`\n"
            f"📍 Current Proof Archive: `{proof_id}`"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("setup_users"):
        co_owners = tenant_cfg.get("co_owners", [])
        labels = tenant_cfg.get("user_id_labels", {})
        
        label_text = ""
        for u_id, lbl in labels.items():
            label_text += f"\n• `{u_id}`: {lbl}"

        text = (
            "👤 **USER ID & CO-OWNER MANAGEMENT**\n\n"
            f"👑 **Primary Owner ID:** `{tenant_cfg.get('primary_owner_id', user.id)}`\n"
            f"👥 **Co-Owner User IDs:** `{co_owners if co_owners else 'None'}`\n"
            f"🏷️ **User ID Labels:** {label_text if label_text else 'None'}\n\n"
            "────────────────────────────────────────\n"
            "📝 **DM COMMANDS TO ADJUST USER IDs:**\n"
            "• `/setowner <USER_ID>` - Transfer primary owner ID\n"
            "• `/addcoowner <USER_ID>` - Add a co-administrator ID\n"
            "• `/delcoowner <USER_ID>` - Remove a co-administrator ID\n"
            "• `/renameowner <USER_ID> <Label>` - Set friendly name for User ID\n"
            "• `/shopname <New Store Title>` - Rename store title"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("setup_pay"):
        payments = tenant_cfg.get("payments", {})
        text = "💳 **CURRENT PAYMENT ACCOUNTS:**\n\n"
        for k, v in payments.items():
            text += f"{v}\n\n"
        text += (
            "────────────────────────────────────────\n"
            "📝 **DM COMMANDS TO ADJUST PAYMENTS:**\n"
            "• `/setpayment gcash <Number> | <Account Name>`\n"
            "• `/setpayment maya <Number> | <Account Name>`\n"
            "• `/setpayment bank <Account No> | <Bank Name> | <Account Name>`\n"
            "• `/addpayment <key> <Button Label> | <Instructions>`\n"
            "• `/delpayment <key>`"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("setup_msg"):
        text = (
            "💬 **CUSTOM MESSAGE TEMPLATES**\n\n"
            "Configure dynamic tags like `{name}` and `{shop_name}`:\n\n"
            "• `/setmsg welcome <Text>` - Update welcome checkout text\n"
            "• `/setmsg help <Text>` - Update member instructions text\n"
            "• `/setmsg receipt <Text>` - Update receipt acknowledgment text\n"
            "• `/setmsg welcome reset` - Reset to system defaults"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Setup Wizard", callback_data=f"setup_main_{user.id}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

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
            [InlineKeyboardButton("🔍 Open Vault Content Inspector", callback_data="browse_BACK")],
        ]
        setup_text = (
            f"⚙️ **FULL DM SETUP WIZARD: {shop_name.upper()}**\n\n"
            f"👤 **Primary Owner ID:** `{tenant_cfg.get('primary_owner_id', user.id)}`\n"
            f"👥 **Co-Owners:** `{len(co_owners)} registered`\n\n"
            "📋 **ACTIVE ROOM STATUS:**\n"
            f"• 📍 **Main Directory Hub:** `{hub_id if hub_id else 'Not Set'}`\n"
            f"• 📍 **Admin Verification Group:** `{admin_id if admin_id else 'Not Set'}`\n"
            f"• 📍 **Proof Archive:** `{proof_id if proof_id else 'Not Set'}`\n\n"
            "────────────────────────────────────────\n"
            "💡 _Select an item below to configure your store settings directly inside DM!_"
        )
        await query.edit_message_text(setup_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ================= USER ID & STORE MANAGEMENT COMMANDS =================

async def set_owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdvanced management features are strictly restricted to Administrators and Vault Owners.", parse_mode="Markdown")
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/setowner <NEW_PRIMARY_USER_ID>`", parse_mode="Markdown")
        return

    try:
        new_owner_id = int(context.args[0])
        tenant_cfg = get_tenant_config(user.id)
        tenant_cfg["primary_owner_id"] = new_owner_id
        save_config(GLOBAL_CONFIG)
        await msg.reply_text(f"✅ **Success!** Primary Owner User ID set to `{new_owner_id}`!", parse_mode="Markdown")
    except ValueError:
        await msg.reply_text("❌ Invalid User ID. Must be numerical.")

async def add_coowner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdvanced management features are strictly restricted to Administrators and Vault Owners.", parse_mode="Markdown")
        return

    args = context.args
    if not args:
        await msg.reply_text("⚠️ **Usage:** `/addcoowner <USER_ID>`", parse_mode="Markdown")
        return

    try:
        new_co_id = int(args[0])
        tenant_cfg = get_tenant_config(user.id)
        co_list = tenant_cfg.setdefault("co_owners", [])
        if new_co_id not in co_list:
            co_list.append(new_co_id)
            save_config(GLOBAL_CONFIG)
            await msg.reply_text(f"✅ **Success!** User ID `{new_co_id}` added as Co-Owner!", parse_mode="Markdown")
        else:
            await msg.reply_text(f"ℹ️ User ID `{new_co_id}` is already a co-owner.")
    except ValueError:
        await msg.reply_text("❌ Invalid User ID. Must be numerical.")

async def del_coowner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdvanced management features are strictly restricted to Administrators and Vault Owners.", parse_mode="Markdown")
        return

    args = context.args
    if not args:
        await msg.reply_text("⚠️ **Usage:** `/delcoowner <USER_ID>`", parse_mode="Markdown")
        return

    try:
        target_id = int(args[0])
        tenant_cfg = get_tenant_config(user.id)
        co_list = tenant_cfg.get("co_owners", [])
        if target_id in co_list:
            co_list.remove(target_id)
            save_config(GLOBAL_CONFIG)
            await msg.reply_text(f"✅ **Success!** Removed User ID `{target_id}` from Co-Owners!", parse_mode="Markdown")
        else:
            await msg.reply_text(f"ℹ️ User ID `{target_id}` was not found in Co-Owners.")
    except ValueError:
        await msg.reply_text("❌ Invalid User ID.")

async def rename_owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdvanced management features are strictly restricted to Administrators and Vault Owners.", parse_mode="Markdown")
        return

    if not context.args or len(context.args) < 2:
        await msg.reply_text("⚠️ **Usage:** `/renameowner <USER_ID> <Label>`\n_Example:_ `/renameowner 123456789 Admin Juan`", parse_mode="Markdown")
        return

    try:
        target_id = str(context.args[0])
        label = " ".join(context.args[1:]).strip()
        tenant_cfg = get_tenant_config(user.id)
        labels = tenant_cfg.setdefault("user_id_labels", {})
        labels[target_id] = label
        save_config(GLOBAL_CONFIG)
        await msg.reply_text(f"✅ User ID `{target_id}` labeled as **{label}**!", parse_mode="Markdown")
    except Exception as e:
        await msg.reply_text(f"❌ Error updating label: {e}")

async def set_shop_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdvanced management features are strictly restricted to Administrators and Vault Owners.", parse_mode="Markdown")
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/shopname <New Store Title>`", parse_mode="Markdown")
        return

    new_title = " ".join(context.args).strip()
    tenant_cfg = get_tenant_config(user.id)
    tenant_cfg["shop_name"] = new_title
    save_config(GLOBAL_CONFIG)
    await msg.reply_text(f"✅ **Success!** Store title updated to: **{new_title}**", parse_mode="Markdown")

# ================= AUTO-READY ROOM JOIN HANDLER =================

async def handle_bot_added_to_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    my_chat_member = update.my_chat_member
    if not my_chat_member:
        return

    new_status = my_chat_member.new_chat_member.status
    if new_status not in [ChatMember.ADMINISTRATOR, ChatMember.MEMBER]:
        return

    chat = my_chat_member.chat
    installer = my_chat_member.from_user

    GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[str(chat.id)] = installer.id
    save_config(GLOBAL_CONFIG)

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"🤖 **Digital Vault Manager Online & Ready!**\n\n"
                f"This room (`{chat.title}`) has been automatically linked to **{installer.full_name}** (`{installer.id}`).\n\n"
                "• Admins can type `/setup` in private DM with the bot or run setup commands here (`/sethub`, `/setadmin`, `/setproof`)."
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Could not post join welcome in chat {chat.id}: {e}")

    dm_keyboard = [
        [InlineKeyboardButton("📍 Set as Main Directory Hub", callback_data=f"bindroom_hub_{chat.id}")],
        [InlineKeyboardButton("📍 Set as Admin Verification Group", callback_data=f"bindroom_admin_{chat.id}")],
        [InlineKeyboardButton("📍 Set as Proof Archive", callback_data=f"bindroom_proof_{chat.id}")],
        [InlineKeyboardButton("⚙️ Open DM Setup Wizard", callback_data=f"setup_main_{installer.id}")],
    ]
    dm_text = (
        f"🎉 **BOT ADDED TO ROOM:** `{chat.title}` (`{chat.id}`)\n\n"
        f"Hello {installer.full_name}! What role would you like to assign to this room?"
    )
    try:
        await context.bot.send_message(
            chat_id=installer.id,
            text=dm_text,
            reply_markup=InlineKeyboardMarkup(dm_keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Could not send DM to installer {installer.id}: {e}")

async def handle_bind_room_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_admin_or_owner(update, context):
        await query.answer("⛔ Access Denied: Administrator or Owner permission required.", show_alert=True)
        return

    await query.answer()
    user = query.from_user
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
    else:
        role_label = "VAULT CONTENT"

    save_config(GLOBAL_CONFIG)
    await query.edit_message_text(
        f"✅ **SUCCESS!** Chat (`{chat_id}`) registered as your **{role_label}**!\n\n"
        f"The room is 100% configured and ready to perform its duties automatically.",
        parse_mode="Markdown"
    )

async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not update.message or not update.message.new_chat_members:
        return

    for new_member in update.message.new_chat_members:
        if new_member.id == context.bot.id:
            GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[str(msg.chat.id)] = update.message.from_user.id
            save_config(GLOBAL_CONFIG)
            await msg.reply_text(
                "🤖 **Digital Vault Manager Bot Online & Ready!**\n\n"
                "Thank you for adding me to this chat! Run setup commands here:\n"
                "• `/sethub` - Bind as Main Hub Directory (run again to remove)\n"
                "• `/setadmin` - Bind as Admin Verification Group (run again to remove)\n"
                "• `/setproof` - Bind as Proof Archive (run again to remove)\n"
                "• `/setup` - Open Private DM Setup Wizard",
                parse_mode="Markdown"
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
            f"👋 **Welcome to the group, {new_member.full_name}!**\n\n"
            f"{welcome_text}\n\n"
            f"────────────────────────────────────────\n"
            f"📖 **ONBOARDING HELP:**\n"
            f"{help_template}"
        )
        await msg.reply_text(full_welcome_text, parse_mode="Markdown")

# ================= TELEGRAM JUMP LINK INSPECTOR FOR REAL OWNER =================

async def open_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nOnly administrators or vault owners can inspect links.", parse_mode="Markdown")
        return

    text = msg.text or ""
    links = re.findall(r"https://t\.me/c/(\d+)/(?:(\d+)/)?(\d+)", text)

    if not links and context.args:
        arg_text = " ".join(context.args)
        links = re.findall(r"https://t\.me/c/(\d+)/(?:(\d+)/)?(\d+)", arg_text)

    if not links:
        await msg.reply_text(
            "⚠️ **Usage:** Send or paste a Telegram jump link, e.g.:\n"
            "`/openlink https://t.me/c/123456789/45`\n"
            "or paste the link directly in a private DM with the bot!",
            parse_mode="Markdown"
        )
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
            icon = "🎬" if found_item.get("type") == "video" else "📚"
            reply_text = (
                f"🔍 **INDEXED VAULT FILE FOUND!**\n\n"
                f"📁 **Category:** {cat_name}\n"
                f"📖 **Module:** {found_course.get('display_title')}\n"
                f"{icon} **Item #{found_item.get('num')}:** {found_item.get('title')}\n"
                f"🆔 **Message ID:** `{msg_id}`\n\n"
                f"🔗 **Direct Link:** [{full_link}]({full_link})"
            )
        else:
            reply_text = (
                f"🔍 **TELEGRAM JUMP LINK INSPECTOR**\n\n"
                f"📁 **Chat ID:** `{full_chat_id}`\n"
                f"🆔 **Message ID:** `{msg_id}`\n"
                f"📍 **Thread ID:** `{thread_id if thread_id else 'Main Chat'}`\n\n"
                f"🔗 **Direct Link:** [{full_link}]({full_link})\n\n"
                f"ℹ️ _Click the link above to view or download the file/video inside Telegram._"
            )

        await msg.reply_text(reply_text, parse_mode="Markdown", disable_web_page_preview=False)

async def handle_direct_link_paste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.type != "private":
        return

    text = msg.text or ""
    if "t.me/c/" in text:
        if await is_admin_or_owner(update, context):
            await open_link_command(update, context)

# ================= VAULT CONTENT INSPECTOR & FILE BROWSER =================

async def view_vault_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator or Vault Owner permission required.", parse_mode="Markdown")
        return

    if not VAULT_STATE:
        await msg.reply_text("📂 **Vault Inspector:** No indexed content or courses found yet.")
        return

    keyboard = []
    for chat_id, data in VAULT_STATE.items():
        cat_name = data.get("category_name", f"Chat {chat_id}")
        keyboard.append([InlineKeyboardButton(f"📁 Browse: {cat_name}", callback_data=f"browsechat_{chat_id}")])

    await msg.reply_text(
        "🔍 **VAULT CONTENT & FILE INSPECTOR**\n\nSelect a channel/group below to view all indexed videos, ebooks, apps, and modules:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_vault_browser_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_admin_or_owner(update, context):
        await query.answer("⛔ Access Denied: Administrator or Owner permission required.", show_alert=True)
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

# ================= DYNAMIC ROOM BINDING COMMANDS (REAL-TIME FIXES) =================

async def set_hub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not chat:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text(
            "⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\n"
            "Only administrators or room owners can set room roles.",
            parse_mode="Markdown"
        )
        return

    owner_id = user.id if user else (GLOBAL_CONFIG.get("chat_tenant_map", {}).get(str(chat.id)) or SUPER_ADMIN_IDS[0])
    tenant_cfg = get_tenant_config(owner_id)
    
    args = [a.lower() for a in context.args] if context.args else []
    
    target_chat_id = chat.id
    if context.args and context.args[0].lstrip('-').isdigit():
        target_chat_id = int(context.args[0])

    chat_str = str(target_chat_id)

    if any(kw in args for kw in ["remove", "clear", "unset", "off", "del"]) or (tenant_cfg.get("main_hub_id") == target_chat_id and not args):
        tenant_cfg["main_hub_id"] = 0
        GLOBAL_CONFIG.get("chat_tenant_map", {}).pop(chat_str, None)
        save_config(GLOBAL_CONFIG)
        await msg.reply_text("🗑️ Chat **unbound / removed** as your Main Hub Directory!", parse_mode="Markdown")
        return

    tenant_cfg["main_hub_id"] = target_chat_id
    GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[chat_str] = owner_id
    save_config(GLOBAL_CONFIG)

    owner_name = user.full_name if user else "Admin"
    clean_chat_id = str(target_chat_id).replace("-100", "").lstrip("-")
    room_link = f"https://t.me/c/{clean_chat_id}" if chat.type != "private" else "Private DM"

    await msg.reply_text(
        f"✅ **REAL-TIME SUCCESS!** Room registered as **YOUR MAIN HUB DIRECTORY**!\n\n"
        f"📍 **Room Title:** `{chat.title if chat.title else 'Main Hub'}`\n"
        f"🆔 **Room ID:** `{target_chat_id}`\n"
        f"🔗 **Room Link:** [{room_link}]({room_link})\n"
        f"👤 **Vault Owner ID:** `{owner_id}` ({owner_name})\n\n"
        "💡 _All auto-indexed jump links and master bundle posts will now post directly here!_\n"
        "💡 _To unbind later, type `/sethub remove` or `/sethub` again in this chat._",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

async def set_admin_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not chat:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text(
            "⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\n"
            "Administrator permission required.",
            parse_mode="Markdown"
        )
        return

    owner_id = user.id if user else (GLOBAL_CONFIG.get("chat_tenant_map", {}).get(str(chat.id)) or SUPER_ADMIN_IDS[0])
    tenant_cfg = get_tenant_config(owner_id)
    
    args = [a.lower() for a in context.args] if context.args else []

    target_chat_id = chat.id
    if context.args and context.args[0].lstrip('-').isdigit():
        target_chat_id = int(context.args[0])

    chat_str = str(target_chat_id)

    if any(kw in args for kw in ["remove", "clear", "unset", "off", "del"]) or (tenant_cfg.get("admin_proof_group_id") == target_chat_id and not args):
        tenant_cfg["admin_proof_group_id"] = 0
        GLOBAL_CONFIG.get("chat_tenant_map", {}).pop(chat_str, None)
        save_config(GLOBAL_CONFIG)
        await msg.reply_text("🗑️ Group **unbound / removed** as your Admin Verification Group!", parse_mode="Markdown")
        return

    tenant_cfg["admin_proof_group_id"] = target_chat_id
    GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[chat_str] = owner_id
    save_config(GLOBAL_CONFIG)

    owner_name = user.full_name if user else "Admin"
    clean_chat_id = str(target_chat_id).replace("-100", "").lstrip("-")
    room_link = f"https://t.me/c/{clean_chat_id}" if chat.type != "private" else "Private DM"

    await msg.reply_text(
        f"✅ **REAL-TIME SUCCESS!** Group registered as **YOUR ADMIN VERIFICATION GROUP**!\n\n"
        f"📍 **Group Title:** `{chat.title if chat.title else 'Admin Review Group'}`\n"
        f"🆔 **Group ID:** `{target_chat_id}`\n"
        f"🔗 **Group Link:** [{room_link}]({room_link})\n"
        f"👤 **Vault Owner ID:** `{owner_id}` ({owner_name})\n\n"
        "💡 _Customer receipt screenshots will be instantly forwarded here with Approve/Reject action buttons!_",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

async def set_proof_archive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not chat:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text(
            "⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\n"
            "Administrator permission required.",
            parse_mode="Markdown"
        )
        return

    owner_id = user.id if user else (GLOBAL_CONFIG.get("chat_tenant_map", {}).get(str(chat.id)) or SUPER_ADMIN_IDS[0])
    tenant_cfg = get_tenant_config(owner_id)
    
    args = [a.lower() for a in context.args] if context.args else []

    target_chat_id = chat.id
    if context.args and context.args[0].lstrip('-').isdigit():
        target_chat_id = int(context.args[0])

    chat_str = str(target_chat_id)

    if any(kw in args for kw in ["remove", "clear", "unset", "off", "del"]) or (tenant_cfg.get("proof_archive_id") == target_chat_id and not args):
        tenant_cfg["proof_archive_id"] = 0
        GLOBAL_CONFIG.get("chat_tenant_map", {}).pop(chat_str, None)
        save_config(GLOBAL_CONFIG)
        await msg.reply_text("🗑️ Chat **unbound / removed** as your Proof Archive!", parse_mode="Markdown")
        return

    tenant_cfg["proof_archive_id"] = target_chat_id
    GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[chat_str] = owner_id
    save_config(GLOBAL_CONFIG)

    owner_name = user.full_name if user else "Admin"
    clean_chat_id = str(target_chat_id).replace("-100", "").lstrip("-")
    room_link = f"https://t.me/c/{clean_chat_id}" if chat.type != "private" else "Private DM"

    await msg.reply_text(
        f"✅ **REAL-TIME SUCCESS!** Chat registered as **YOUR PROOF ARCHIVE**!\n\n"
        f"📍 **Archive Title:** `{chat.title if chat.title else 'Proof Archive'}`\n"
        f"🆔 **Archive ID:** `{target_chat_id}`\n"
        f"🔗 **Archive Link:** [{room_link}]({room_link})\n"
        f"👤 **Vault Owner ID:** `{owner_id}` ({owner_name})\n\n"
        "💡 _Once receipts are approved by admins, copies will be automatically archived here!_",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

# ================= ADMIN CONTROL DESK & PROFILE SETUP =================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nOnly administrators or vault owners can access this panel.", parse_mode="Markdown")
        return

    tenant_cfg = get_tenant_config(user.id)
    shop_name = tenant_cfg.get("shop_name", "Digital Vault Store")

    admin_help_text = (
        f"🛠️ **ADMIN CONTROL DESK: {shop_name.upper()}**\n\n"
        "⚙️ **Full Private DM Setup Wizard:**\n"
        "• `/setup` - Interactive Control Panel inside private DM\n\n"
        "👤 **User ID & Store Management:**\n"
        "• `/setowner <USER_ID>` - Transfer primary tenant owner ID\n"
        "• `/addcoowner <USER_ID>` - Add a co-administrator ID\n"
        "• `/delcoowner <USER_ID>` - Remove a co-administrator ID\n"
        "• `/renameowner <USER_ID> <Label>` - Label a User ID\n"
        "• `/shopname <Name>` - Change store title\n\n"
        "🤖 **Bot Profile Commands:**\n"
        "• `/setbotname <Name>` - Change bot display name live\n"
        "• `/setbotusername` - View current username & change guide\n\n"
        "📍 **Room Setup & Binding (Run in target chats):**\n"
        "• `/sethub` (or `/sethub remove`) - Bind/Unbind Main Hub Directory\n"
        "• `/setadmin` (or `/setadmin remove`) - Bind/Unbind Admin Review Group\n"
        "• `/setproof` (or `/setproof remove`) - Bind/Unbind Proof Archive\n\n"
        "🔍 **File, Video & Index Customization:**\n"
        "• `/viewvault` - Inspect and browse all uploaded content\n"
        "• `/openlink <link>` - Inspect any Telegram jump link directly\n"
        "• `/customindex` - Configure custom indexing & arrangements\n"
        "• `/setindexlabel <Label>` - Change file prefix label (Lesson, Part, File)\n"
        "• `/groupmodule <Master Title> | <Item 1, Item 2>` - Bundle lessons\n"
        "• `/renameitem <Old> | <New>` - Rename index entries\n"
        "• `/delindex` - Delete indexed modules\n\n"
        "💳 **Payment Management:**\n"
        "• `/setpayment gcash <Number> | <Name>`\n"
        "• `/addpayment <key> <Label> | <Instructions>`\n"
        "• `/delpayment <key>`\n\n"
        "💬 **Custom Messages & Settings:**\n"
        "• `/setmsg welcome|help|receipt <Text|reset>`\n"
        "• `/config` - View active configuration audit"
    )
    await msg.reply_text(admin_help_text, parse_mode="Markdown")

async def set_bot_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_owner(update, context):
        await update.message.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
        return

    if not context.args:
        await update.message.reply_text("⚠️ **Usage:** `/setbotname <NEW_BOT_NAME>`", parse_mode="Markdown")
        return

    new_name = " ".join(context.args).strip()
    try:
        await context.bot.set_my_name(name=new_name)
        await update.message.reply_text(f"✅ **SUCCESS!** Bot display name updated to: `{new_name}`", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to set bot name: {e}")
        await update.message.reply_text(f"❌ Error updating bot name: {e}")

async def set_bot_username_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_owner(update, context):
        await update.message.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
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

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
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
    await msg.reply_text(f"✅ **Success!** Updated **{method_key.upper()}** payment info!", parse_mode="Markdown")

async def add_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
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
    await msg.reply_text(f"✅ **Success!** Added custom payment option `{pay_key}`!", parse_mode="Markdown")

async def del_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
        return

    args = context.args
    if not args:
        await msg.reply_text("⚠️ **Usage:** `/delpayment <key>`")
        return

    tenant_cfg = get_tenant_config(user.id)
    pay_key = f"pay_{args[0].lower()}"
    if pay_key in tenant_cfg["payments"]:
        del tenant_cfg["payments"][pay_key]
        save_config(GLOBAL_CONFIG)
        await msg.reply_text(f"✅ Removed payment option `{pay_key}`.", parse_mode="Markdown")
    else:
        await msg.reply_text(f"❌ Payment option `{pay_key}` not found.")

async def payment_option_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("payusr_"):
        parts = data.split("_", 2)
        owner_id = int(parts[1])
        method = parts[2]
        tenant_cfg = get_tenant_config(owner_id)
    else:
        method = data
        owner_id = query.from_user.id
        tenant_cfg = get_tenant_config(owner_id)

    context.user_data["active_owner_id"] = owner_id

    instructions = tenant_cfg.get("payments", {}).get(method, "Payment details not configured.")
    instructions_text = f"{instructions}\n\n📸 **INSTRUCTIONS:** Send your receipt screenshot directly here."
    await query.edit_message_text(instructions_text, parse_mode="Markdown")

# ================= REAL-TIME RECEIPT FORWARDING & APPROVAL =================

async def handle_photo_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.type != "private":
        return

    user = msg.from_user
    
    # Extract photo ID from photo upload or image document
    if msg.photo:
        photo_file_id = msg.photo[-1].file_id
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        photo_file_id = msg.document.file_id
    else:
        return

    # Determine store owner ID
    owner_id = context.user_data.get("active_owner_id")
    if not owner_id:
        chat_owner_id, tenant_cfg = get_tenant_by_chat(msg.chat.id)
        owner_id = chat_owner_id if chat_owner_id else None

    if not owner_id:
        tenants = GLOBAL_CONFIG.get("tenants", {})
        if tenants:
            for t_id, t_data in tenants.items():
                if t_data.get("admin_proof_group_id") or t_data.get("main_hub_id"):
                    owner_id = int(t_id)
                    break
            if not owner_id:
                owner_id = int(list(tenants.keys())[0])
        else:
            owner_id = SUPER_ADMIN_IDS[0]

    tenant_cfg = get_tenant_config(owner_id)
    admin_group_id = tenant_cfg.get("admin_proof_group_id")

    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}_{owner_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user.id}_{owner_id}")
        ]
    ]
    
    caption_text = (
        f"📥 **NEW RECEIPT SUBMISSION**\n\n"
        f"👤 **Buyer:** {user.full_name}\n"
        f"🆔 **Buyer User ID:** `{user.id}`\n"
        f"💬 **Username:** @{user.username if user.username else 'N/A'}\n"
        f"👑 **Store Owner ID:** `{owner_id}`"
    )

    sent_successfully = False

    # 1. Primary Route: Forward to Admin Verification Group (/setadmin)
    if admin_group_id and admin_group_id != 0:
        res = await safe_send_photo(
            bot=context.bot,
            chat_id=int(admin_group_id),
            photo=photo_file_id,
            caption=caption_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        if res:
            sent_successfully = True

    # 2. Fallback: Forward directly to Store Owner / Admin Private Telegram DM
    if not sent_successfully:
        res = await safe_send_photo(
            bot=context.bot,
            chat_id=int(owner_id),
            photo=photo_file_id,
            caption=f"⚠️ **ADMIN GROUP NOT SET OR UNREACHABLE**\nForwarded directly to Store Owner DM:\n\n{caption_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        if res:
            sent_successfully = True
        else:
            for sa_id in SUPER_ADMIN_IDS:
                if sa_id != owner_id:
                    res_sa = await safe_send_photo(
                        bot=context.bot,
                        chat_id=int(sa_id),
                        photo=photo_file_id,
                        caption=f"⚠️ **SUPER ADMIN FALLBACK**\n\n{caption_text}",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="Markdown"
                    )
                    if res_sa:
                        sent_successfully = True
                        break

    if sent_successfully:
        template = tenant_cfg["messages"].get("receipt", DEFAULT_TENANT_CONFIG["messages"]["receipt"])
        await msg.reply_text(template.replace("{name}", user.full_name), parse_mode="Markdown")
    else:
        await msg.reply_text(
            "⚠️ **Notice:** Payment verification destination is not configured for this store yet.\n"
            "An administrator must run `/setadmin` in the review group."
        )

async def handle_admin_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("_")
    action = data[0]
    buyer_id = int(data[1])
    owner_id = int(data[2]) if len(data) > 2 else query.from_user.id

    tenant_cfg = get_tenant_config(owner_id)
    main_hub_id = tenant_cfg.get("main_hub_id")
    proof_archive_id = tenant_cfg.get("proof_archive_id")

    if action == "approve":
        if not main_hub_id or main_hub_id == 0:
            await query.message.reply_text(
                "⚠️ **Error:** Main Directory Hub is not set yet! Run `/sethub` inside your Main Hub group or channel."
            )
            return

        try:
            invite_link = await context.bot.create_chat_invite_link(chat_id=int(main_hub_id), member_limit=1)
            await context.bot.send_message(
                chat_id=buyer_id, 
                text=f"🎉 **Payment Approved!**\n\nWelcome! Click your single-use invite link to join the Main Directory Hub:\n{invite_link.invite_link}"
            )
            await query.edit_message_caption(caption=f"{query.message.caption}\n\nSTATUS: ✅ **APPROVED BY ADMIN**")

            # Forward approved proof screenshot to Proof Archive channel/group (/setproof) upon approval
            if proof_archive_id and proof_archive_id != 0:
                try:
                    await context.bot.forward_message(
                        chat_id=int(proof_archive_id), 
                        from_chat_id=query.message.chat_id, 
                        message_id=query.message.message_id
                    )
                except Exception as p_err:
                    logger.error(f"Failed to forward proof to archive {proof_archive_id}: {p_err}")
        except Exception as e:
            logger.error(f"Approval error: {e}")
            await query.message.reply_text(
                f"❌ Approval error: {e}\n(Ensure bot is an Admin in your Main Hub and has 'Invite Users via Link' permission!)"
            )
    elif action == "reject":
        try:
            await context.bot.send_message(
                chat_id=buyer_id, 
                text="❌ **Verification Failed.** Please double-check your payment reference number and submit a clear receipt screenshot."
            )
        except Exception as err:
            logger.error(f"Failed sending rejection to buyer {buyer_id}: {err}")
        await query.edit_message_caption(caption=f"{query.message.caption}\n\nSTATUS: ❌ **REJECTED BY ADMIN**")

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

        keyboard = [
            [
                InlineKeyboardButton("🥾 Kick & Ban User", callback_data=f"seckick_ban_{user.id}_{chat.id}"),
                InlineKeyboardButton("🛡️ Keep & Dismiss", callback_data=f"seckick_keep_{user.id}")
            ]
        ]
        alert_text = (
            f"🚨 **SECURITY ALERT: POTENTIAL THREAT DETECTED!** 🚨\n\n"
            f"👤 **User:** {user.full_name} (`{user.id}`)\n"
            f"💬 **Group:** {chat.title} (`{chat.id}`)\n"
            f"⚠️ **Reason:** {violation_reason}\n\n"
            f"👉 Message was automatically deleted. Select action below:"
        )

        for s_admin_id in GLOBAL_CONFIG.get("super_admin_ids", SUPER_ADMIN_IDS):
            try:
                await context.bot.send_message(chat_id=s_admin_id, text=alert_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to notify super admin {s_admin_id}: {e}")

async def handle_security_kick_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_admin_or_owner(update, context):
        await query.answer("⛔ Access Denied: Administrator or Owner permission required.", show_alert=True)
        return

    await query.answer()

    data = query.data.split("_")
    action = data[1]
    user_id = int(data[2])
    chat_id = int(data[3]) if len(data) > 3 else None

    if action == "ban" and chat_id:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await query.edit_message_text(text=f"{query.message.text}\n\nSTATUS: ✅ **USER BANNED & KICKED**", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to ban user: {e}")
            await query.edit_message_text(text=f"{query.message.text}\n\nSTATUS: ❌ **Failed to ban user:** {e}")
    else:
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

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
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
        await msg.reply_text(f"✅ Added `{word}` to moderation blocklist!", parse_mode="Markdown")
    else:
        await msg.reply_text(f"ℹ️ Word `{word}` is already in the blocklist.")

# ================= AUTOMATED CONTENT INDEXING (REAL-TIME) =================

async def auto_index_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post or update.message
    if not post:
        return

    # 1. Ignore posts sent directly by bots or by the bot itself
    if (post.from_user and post.from_user.is_bot) or getattr(post, "via_bot", None):
        return
    if post.sender_chat and post.sender_chat.id == context.bot.id:
        return

    extracted_text = post.text or post.caption or ""

    # 2. STRICT BOT MESSAGE / LOOP PREVENTION
    bot_index_markers = [
        "📌 **", "🔄 **", "👉 _", "INDEXED VAULT FILE", "📁 **Category:", "📖 **Module:",
        "🎬 **#", "📚 **#", "📦 **#", "💻 **#", "🖼️ **#", "🎵 **#", "🎞️ **#",
        "✅ **SUCCESS", "🤖 **Digital", "📥 **NEW RECEIPT", "STATUS:", "💳 **CURRENT",
        "⚙️ **FULL DM", "👤 **PRIMARY", "🛠️ **ADMIN CONTROL", "📋 **YOUR VAULT", "🗑️ Chat **unbound",
        "Click the link above to jump directly", "MASTER BUNDLE:", "TELEGRAM JUMP LINK INSPECTOR",
        "NEW MODULE ADDED", "RE-OPENED MODULE"
    ]
    if any(marker in extracted_text for marker in bot_index_markers):
        return

    chat_id = post.chat_id
    chat_id_str = str(chat_id)

    # 3. ROOM GUARD: Ignore posts made inside designated Hub, Admin Review Group, or Proof Archive
    for t_data in GLOBAL_CONFIG.get("tenants", {}).values():
        h_id = t_data.get("main_hub_id")
        a_id = t_data.get("admin_proof_group_id")
        p_id = t_data.get("proof_archive_id")

        if chat_id in [h_id, a_id, p_id] and chat_id != 0:
            return

    owner_id, tenant_cfg = get_tenant_by_chat(chat_id)
    if not tenant_cfg:
        chat_map = GLOBAL_CONFIG.get("chat_tenant_map", {})
        if chat_id_str in chat_map:
            owner_id = chat_map[chat_id_str]
            tenant_cfg = get_tenant_config(owner_id)
        else:
            tenants = GLOBAL_CONFIG.get("tenants", {})
            if tenants:
                owner_id = int(list(tenants.keys())[0])
                tenant_cfg = tenants[str(owner_id)]
                GLOBAL_CONFIG.setdefault("chat_tenant_map", {})[chat_id_str] = owner_id
                save_config(GLOBAL_CONFIG)
            else:
                return

    main_hub_id = tenant_cfg.get("main_hub_id")
    if not main_hub_id or chat_id == main_hub_id:
        return

    category_name = post.chat.title if post.chat.title else "Vault Content"
    clean_chat_id = chat_id_str.replace("-100", "").lstrip("-")
    thread_id = getattr(post, "message_thread_id", None)

    # Store channel record using integer chat_id
    chat_key = chat_id
    if chat_key not in VAULT_STATE:
        VAULT_STATE[chat_key] = {"category_name": category_name, "active_course": "General Library", "courses": {}}
    else:
        VAULT_STATE[chat_key]["category_name"] = category_name

    has_media = bool(post.video or post.document or post.audio or post.photo or post.animation)

    # CASE 1: Pure Text Post - Declared Module Header
    if (post.text and not has_media) or (getattr(post, "forward_origin", None) and post.text and not has_media):
        raw_title = post.text.strip()
        if raw_title.startswith("/"):
            return

        # In groups, only process text titles if posted by an admin/owner
        if post.chat.type in ["group", "supergroup"]:
            if not await is_admin_or_owner(update, context):
                return

        norm_key = normalize_title(raw_title)
        VAULT_STATE[chat_key]["active_course"] = norm_key

        if norm_key not in VAULT_STATE[chat_key]["courses"]:
            VAULT_STATE[chat_key]["courses"][norm_key] = {"display_title": raw_title, "file_count": 0, "items": []}
            status_msg = f"📌 **NEW MODULE ADDED TO {category_name.upper()}**\n\n📖 **Title:** `{raw_title}`"
        else:
            current_count = VAULT_STATE[chat_key]["courses"][norm_key]["file_count"]
            status_msg = f"🔄 **RE-OPENED MODULE IN {category_name.upper()}**\n\n📖 **Title:** `{raw_title}`\n📊 **Current Total Files:** {current_count}"

        save_state(VAULT_STATE)
        await safe_send_message(
            bot=context.bot,
            chat_id=int(main_hub_id),
            text=f"{status_msg}\n────────────────────────────────────────",
            parse_mode="Markdown"
        )
        return

    # CASE 2: Uploaded Content (Videos, Ebooks, APKs, EXEs, Source Code, Audio, Photos, Documents)
    if has_media:
        active_key = VAULT_STATE[chat_key].get("active_course", "General Library")
        if active_key not in VAULT_STATE[chat_key]["courses"]:
            VAULT_STATE[chat_key]["courses"][active_key] = {"display_title": active_key.title(), "file_count": 0, "items": []}

        VAULT_STATE[chat_key]["courses"][active_key]["file_count"] += 1
        file_num = VAULT_STATE[chat_key]["courses"][active_key]["file_count"]
        display_title = VAULT_STATE[chat_key]["courses"][active_key]["display_title"]

        prefix_label = tenant_cfg.get("indexing_settings", {}).get("custom_label", "Lesson")

        if post.video:
            content_type_icon = "🎬"
            item_type = "video"
            default_label = f"{prefix_label} {file_num}"
        elif post.audio:
            content_type_icon = "🎵"
            item_type = "audio"
            default_label = f"Audio Track {file_num}"
        elif post.photo:
            content_type_icon = "🖼️"
            item_type = "photo"
            default_label = f"Image {file_num}"
        elif post.animation:
            content_type_icon = "🎞️"
            item_type = "animation"
            default_label = f"Animation {file_num}"
        elif post.document:
            doc_name = (post.document.file_name or "").lower()
            if doc_name.endswith((".pdf", ".epub", ".mobi", ".doc", ".docx", ".txt")):
                content_type_icon = "📚"
                item_type = "ebook"
                default_label = f"Ebook/Doc Part {file_num}"
            elif doc_name.endswith((".apk", ".exe", ".msi", ".dmg", ".iso", ".app")):
                content_type_icon = "📦"
                item_type = "app_software"
                default_label = f"Software/App Part {file_num}"
            elif doc_name.endswith((".py", ".js", ".html", ".css", ".cpp", ".c", ".java", ".php", ".sh", ".json", ".zip", ".rar", ".7z")):
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

        caption_title = extracted_text.split("\n")[0] if extracted_text else default_label
        
        # Build direct jump link supporting public channel usernames and private channel IDs
        if post.chat.username:
            direct_link = f"https://t.me/{post.chat.username}/{thread_id}/{post.message_id}" if thread_id else f"https://t.me/{post.chat.username}/{post.message_id}"
        else:
            direct_link = f"https://t.me/c/{clean_chat_id}/{thread_id}/{post.message_id}" if thread_id else f"https://t.me/c/{clean_chat_id}/{post.message_id}"

        item_info = {
            "type": item_type,
            "icon": content_type_icon,
            "num": file_num,
            "title": caption_title,
            "link": direct_link,
            "msg_id": post.message_id
        }
        VAULT_STATE[chat_key]["courses"][active_key].setdefault("items", []).append(item_info)
        save_state(VAULT_STATE)

        lesson_entry = (
            f"📁 **Category:** {category_name}\n"
            f"📖 **Module:** {display_title}\n"
            f"{content_type_icon} **#{file_num}:** [{caption_title}]({direct_link})\n\n"
            f"👉 _Click the link above to jump directly to this material!_"
        )

        await safe_send_message(
            bot=context.bot,
            chat_id=int(main_hub_id),
            text=lesson_entry,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

# ================= ADVANCED MODULE GROUPING & RENAMING =================

async def group_module_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/groupmodule Game Dev Master Bundle | Lesson 1, Lesson 2`", parse_mode="Markdown")
        return

    payload = " ".join(context.args)
    if "|" not in payload:
        await msg.reply_text("❌ Please use `|` to separate the title from included items.")
        return

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
    await msg.reply_text(f"✅ Bundle `{master_title}` created and posted to Main Hub!")

async def rename_item_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
        return

    payload = " ".join(context.args)
    if "|" not in payload:
        await msg.reply_text("⚠️ **Usage:** `/renameitem Game Dev Intro | Advanced Unity 2026`", parse_mode="Markdown")
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
    await msg.reply_text(f"✅ Renamed {renamed_count} entries to `{new_title}`.", parse_mode="Markdown")

async def del_index_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
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
        await query.answer("⛔ Access Denied: Administrator or Owner permission required.", show_alert=True)
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
            await query.edit_message_text("✅ Removed module from index records.")
            return

    await query.edit_message_text("❌ Module not found.")

async def set_index_label_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
        return

    if not context.args:
        await msg.reply_text("⚠️ **Usage:** `/setindexlabel <Lesson|Chapter|Module|Part>`", parse_mode="Markdown")
        return

    new_label = context.args[0].strip()
    tenant_cfg = get_tenant_config(user.id)
    tenant_cfg.setdefault("indexing_settings", {})["custom_label"] = new_label
    save_config(GLOBAL_CONFIG)
    await msg.reply_text(f"✅ **Success!** Default indexing label set to **{new_label}**!", parse_mode="Markdown")

async def custom_index_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
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
        "• `/viewvault` - Inspect all indexed vault channels, videos & files"
    )
    await msg.reply_text(panel_text, parse_mode="Markdown")

# ================= CONFIG AUDIT & ADVANCED UTILITIES =================

async def set_msg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
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

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
        return

    args = context.args
    if not args:
        await msg.reply_text("⚠️ **Usage:** `/promote <Message>`")
        return

    promo_text = " ".join(args)
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
        await query.answer("⛔ Access Denied: Administrator or Owner permission required.", show_alert=True)
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

    await query.edit_message_text(f"🚀 Promotion sent to {sent_count} target location(s)!")

async def view_config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    if not await is_admin_or_owner(update, context):
        await msg.reply_text("⛔ **Access Denied:** Regular members can only use `/start` and `/help`.\nAdministrator permission required.", parse_mode="Markdown")
        return

    tenant_cfg = get_tenant_config(user.id)
    conf_text = (
        f"📋 **YOUR VAULT CONFIGURATION AUDIT:**\n\n"
        f"📛 **Shop Name:** `{tenant_cfg.get('shop_name')}`\n"
        f"👑 **Primary Owner ID:** `{tenant_cfg.get('primary_owner_id', user.id)}`\n"
        f"📍 **Main Hub ID:** `{tenant_cfg.get('main_hub_id')}`\n"
        f"📍 **Admin Proof Group ID:** `{tenant_cfg.get('admin_proof_group_id')}`\n"
        f"📍 **Proof Archive ID:** `{tenant_cfg.get('proof_archive_id')}`\n\n"
        f"💳 **Payments Configured:** {list(tenant_cfg.get('payments', {}).keys())}\n"
        f"👥 **Co-Owners:** `{tenant_cfg.get('co_owners', [])}`"
    )
    await msg.reply_text(conf_text, parse_mode="Markdown")

# ================= APPLICATION BUILDER & WEBHOOK LIFECYCLE =================

ACTIVE_BOT_TOKEN = GLOBAL_CONFIG.get("bot_token", "9118312201:AAFvZ6-cRZZCZqtbSz9dXTChxKYIKkTt9e8")
bot_app = ApplicationBuilder().token(ACTIVE_BOT_TOKEN).build()

# Core Onboarding & DM Setup Commands
bot_app.add_handler(CommandHandler("start", start_command))
bot_app.add_handler(CommandHandler("help", member_help_command))
bot_app.add_handler(CommandHandler(["setup", "setting", "settings"], setup_wizard_command))
bot_app.add_handler(CommandHandler("admin", admin_panel))

# User ID & Store Management Commands
bot_app.add_handler(CommandHandler("setowner", set_owner_command))
bot_app.add_handler(CommandHandler("addcoowner", add_coowner_command))
bot_app.add_handler(CommandHandler("delcoowner", del_coowner_command))
bot_app.add_handler(CommandHandler("renameowner", rename_owner_command))
bot_app.add_handler(CommandHandler(["shopname", "setshopname"], set_shop_name_command))

# File, Jump Link & Index Customization Commands
bot_app.add_handler(CommandHandler("viewvault", view_vault_command))
bot_app.add_handler(CommandHandler("openlink", open_link_command))
bot_app.add_handler(CommandHandler("customindex", custom_index_panel))
bot_app.add_handler(CommandHandler("setindexlabel", set_index_label_command))

# Bot Profile Setup Commands
bot_app.add_handler(CommandHandler("setbotname", set_bot_name_command))
bot_app.add_handler(CommandHandler("setbotusername", set_bot_username_command))

# Multi-Tenant Admin & Room Binding Commands
bot_app.add_handler(CommandHandler(["sethub", "unsethub", "delhub"], set_hub_command))
bot_app.add_handler(CommandHandler(["setadmin", "unsetadmin", "deladmin"], set_admin_group_command))
bot_app.add_handler(CommandHandler(["setproof", "unsetproof", "delproof"], set_proof_archive_command))

# Content & Moderation Management Commands
bot_app.add_handler(CommandHandler("promote", promote_command))
bot_app.add_handler(CommandHandler("groupmodule", group_module_command))
bot_app.add_handler(CommandHandler("renameitem", rename_item_command))
bot_app.add_handler(CommandHandler("delindex", del_index_command))
bot_app.add_handler(CommandHandler("addbadword", add_bad_word_command))

# Payments & Config Commands
bot_app.add_handler(CommandHandler("setmsg", set_msg_command))
bot_app.add_handler(CommandHandler("setpayment", set_payment_command))
bot_app.add_handler(CommandHandler("addpayment", add_payment_command))
bot_app.add_handler(CommandHandler("delpayment", del_payment_command))
bot_app.add_handler(CommandHandler("config", view_config_command))

# Auto-Ready Room Join Handlers & Callbacks
bot_app.add_handler(ChatMemberHandler(handle_bot_added_to_chat, ChatMemberHandler.MY_CHAT_MEMBER))
bot_app.add_handler(CallbackQueryHandler(handle_setup_callbacks, pattern="^setup_"))
bot_app.add_handler(CallbackQueryHandler(handle_bind_room_callback, pattern="^bindroom_"))
bot_app.add_handler(CallbackQueryHandler(handle_mode_switch, pattern="^(mode_|show_help_cb_)"))
bot_app.add_handler(CallbackQueryHandler(handle_vault_browser_callback, pattern="^(browsechat_|browsetit_|browse_)"))
bot_app.add_handler(CallbackQueryHandler(handle_del_index_callback, pattern="^delmod_"))
bot_app.add_handler(CallbackQueryHandler(handle_promotion_callback, pattern="^sendpromo_"))
bot_app.add_handler(CallbackQueryHandler(payment_option_selected, pattern="^(pay_|payusr_)"))
bot_app.add_handler(CallbackQueryHandler(handle_admin_approval, pattern="^(approve|reject)_"))
bot_app.add_handler(CallbackQueryHandler(handle_security_kick_decision, pattern="^seckick_"))

# Receipts & Group Auto-Indexing Handlers
bot_app.add_handler(MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, handle_photo_receipt))
bot_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))
bot_app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_direct_link_paste))

bot_app.add_handler(MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND & filters.ChatType.GROUPS, security_threat_detector), group=0)
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, group_moderation_guard), group=1)
bot_app.add_handler(MessageHandler((filters.ChatType.GROUPS | filters.ChatType.CHANNEL) & ~filters.COMMAND, auto_index_posts), group=2)

# Persistent Event Loop Manager for Webhook Processing
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

loop.run_until_complete(bot_app.initialize())
loop.run_until_complete(bot_app.start())

# AUTO-REGISTER WEBHOOK WITH TELEGRAM ON RENDER DEPLOY
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
if RENDER_EXTERNAL_URL:
    webhook_target = f"{RENDER_EXTERNAL_URL.rstrip('/')}/{ACTIVE_BOT_TOKEN}"
    try:
        loop.run_until_complete(bot_app.bot.set_webhook(url=webhook_target))
        logger.info(f"✅ Webhook successfully registered with Telegram: {webhook_target}")
    except Exception as wh_err:
        logger.error(f"⚠️ Webhook auto-registration warning: {wh_err}")

@app.route(f"/{ACTIVE_BOT_TOKEN}", methods=["POST"])
def webhook():
    json_data = request.get_json(force=True)
    update = Update.de_json(data=json_data, bot=bot_app.bot)
    loop.run_until_complete(bot_app.process_update(update))
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET"])
def health_check():
    return "Multi-Tenant Bot Server Active & Ready", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
