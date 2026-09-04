import os
import sqlite3
import asyncio
import smtplib
from email.message import EmailMessage
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "ISI_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "123456789"))

DB_FILE = "bot.db"

# SMTP default Gmail
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS senders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            app_password TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

    # Template default
    if get_setting("subject") is None:
        set_setting(
            "subject",
            "Laporan untuk {username}"
        )

    if get_setting("body") is None:
        set_setting(
            "body",
            """Halo,

Saya ingin menyampaikan laporan mengenai:

Username/Target: {username}

Mohon dilakukan pemeriksaan terhadap target tersebut.

Terima kasih."""
        )


def get_setting(key):
    conn = db()
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,)
    ).fetchone()
    conn.close()

    return row["value"] if row else None


def set_setting(key, value):
    conn = db()

    conn.execute("""
        INSERT INTO settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
    """, (key, value))

    conn.commit()
    conn.close()


# ============================================================
# OWNER CHECK
# ============================================================

def is_owner(update: Update):
    user = update.effective_user

    if not user:
        return False

    return user.id == OWNER_ID


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    # Orang lain benar-benar diabaikan
    if not is_owner(update):
        return

    await show_main_menu(update)


async def show_main_menu(update: Update):

    keyboard = [
        [
            InlineKeyboardButton(
                "📤 Buat Laporan",
                callback_data="report"
            )
        ],
        [
            InlineKeyboardButton(
                "📧 SMTP Sender",
                callback_data="senders"
            ),
            InlineKeyboardButton(
                "📨 Penerima",
                callback_data="recipients"
            )
        ],
        [
            InlineKeyboardButton(
                "📝 Template",
                callback_data="template"
            )
        ],
    ]

    text = (
        "🤖 *SMTP Report Bot*\n\n"
        "Bot khusus Owner.\n\n"
        "Pilih menu di bawah:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )


# ============================================================
# CALLBACK MENU
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_owner(update):
        return

    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "menu":
        await show_main_menu(update)

    elif data == "report":
        await report_start(update, context)

    elif data == "senders":
        await sender_menu(update)

    elif data == "recipients":
        await recipient_menu(update)

    elif data == "template":
        await template_menu(update)

    elif data == "add_sender":
        context.user_data["state"] = "add_sender"

        await query.edit_message_text(
            "📧 Kirim email sender:"
        )

    elif data == "list_senders":
        await list_senders(update)

    elif data.startswith("delete_sender:"):
        sender_id = int(data.split(":")[1])

        conn = db()
        conn.execute(
            "DELETE FROM senders WHERE id = ?",
            (sender_id,)
        )
        conn.commit()
        conn.close()

        await query.edit_message_text(
            "✅ Sender berhasil dihapus."
        )

        await asyncio.sleep(1)
        await sender_menu(update)

    elif data == "add_recipient":
        context.user_data["state"] = "add_recipient"

        await query.edit_message_text(
            "📨 Kirim email penerima.\n\n"
            "Contoh:\n"
            "admin@example.com"
        )

    elif data == "list_recipients":
        await list_recipients(update)

    elif data.startswith("delete_recipient:"):
        recipient_id = int(data.split(":")[1])

        conn = db()
        conn.execute(
            "DELETE FROM recipients WHERE id = ?",
            (recipient_id,)
        )
        conn.commit()
        conn.close()

        await query.edit_message_text(
            "✅ Penerima berhasil dihapus."
        )

        await asyncio.sleep(1)
        await recipient_menu(update)

    elif data == "set_subject":
        context.user_data["state"] = "set_subject"

        current = get_setting("subject")

        await query.edit_message_text(
            "📝 Kirim subject baru.\n\n"
            "Gunakan `{username}` untuk memasukkan username target.\n\n"
            f"Subject sekarang:\n{current}"
        )

    elif data == "set_body":
        context.user_data["state"] = "set_body"

        current = get_setting("body")

        await query.edit_message_text(
            "📄 Kirim isi email baru.\n\n"
            "Gunakan `{username}` untuk memasukkan username target.\n\n"
            f"Isi sekarang:\n\n{current}"
        )

    elif data == "cancel":
        context.user_data.clear()
        await show_main_menu(update)

    elif data.startswith("choose_sender:"):
        sender_id = int(data.split(":")[1])

        sender = get_sender(sender_id)

        if not sender:
            await query.edit_message_text(
                "❌ Sender tidak ditemukan."
            )
            return

        context.user_data["selected_sender"] = sender_id

        await query.edit_message_text(
            "📤 Sender dipilih:\n"
            f"`{sender['email']}`\n\n"
            "Sekarang kirim username atau link target.",
            parse_mode="Markdown"
        )

        context.user_data["state"] = "waiting_target"

    elif data == "confirm_send":
        await send_report(update, context)

    elif data == "cancel_report":
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Pengiriman dibatalkan."
        )

        await asyncio.sleep(1)
        await show_main_menu(update)


# ============================================================
# SENDER MENU
# ============================================================

async def sender_menu(update):

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Tambah Sender",
                callback_data="add_sender"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Daftar Sender",
                callback_data="list_senders"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Kembali",
                callback_data="menu"
            )
        ],
    ]

    text = "📧 *SMTP Sender*\n\nKelola akun SMTP yang dapat digunakan."

    await update.callback_query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


def get_sender(sender_id):
    conn = db()

    row = conn.execute(
        "SELECT * FROM senders WHERE id = ?",
        (sender_id,)
    ).fetchone()

    conn.close()

    return row


async def list_senders(update):

    conn = db()

    rows = conn.execute(
        "SELECT id, email FROM senders ORDER BY id"
    ).fetchall()

    conn.close()

    if not rows:
        text = "📧 Belum ada SMTP sender."
    else:
        text = "📧 *Daftar SMTP Sender:*\n\n"

        for row in rows:
            text += f"{row['id']}. `{row['email']}`\n"

    keyboard = [
        [
            InlineKeyboardButton(
                "⬅️ Kembali",
                callback_data="senders"
            )
        ]
    ]

    await update.callback_query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# ============================================================
# RECIPIENT MENU
# ============================================================

async def recipient_menu(update):

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Tambah Penerima",
                callback_data="add_recipient"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Daftar Penerima",
                callback_data="list_recipients"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Kembali",
                callback_data="menu"
            )
        ],
    ]

    await update.callback_query.edit_message_text(
        "📨 *Email Penerima*\n\n"
        "Kamu dapat menambahkan beberapa alamat penerima.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def list_recipients(update):

    conn = db()

    rows = conn.execute(
        "SELECT id, email FROM recipients ORDER BY id"
    ).fetchall()

    conn.close()

    if not rows:
        text = "📨 Belum ada penerima."
    else:
        text = "📨 *Daftar Penerima:*\n\n"

        for row in rows:
            text += f"{row['id']}. `{row['email']}`\n"

    keyboard = [
        [
            InlineKeyboardButton(
                "⬅️ Kembali",
                callback_data="recipients"
            )
        ]
    ]

    await update.callback_query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# ============================================================
# TEMPLATE MENU
# ============================================================

async def template_menu(update):

    keyboard = [
        [
            InlineKeyboardButton(
                "📝 Ubah Subject",
                callback_data="set_subject"
            )
        ],
        [
            InlineKeyboardButton(
                "📄 Ubah Isi Email",
                callback_data="set_body"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Kembali",
                callback_data="menu"
            )
        ],
    ]

    subject = get_setting("subject")
    body = get_setting("body")

    text = (
        "📝 *Template Email*\n\n"
        f"*Subject:*\n{subject}\n\n"
        f"*Body:*\n{body}"
    )

    await update.callback_query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# ============================================================
# REPORT START
# ============================================================

async def report_start(update, context):

    conn = db()

    senders = conn.execute(
        "SELECT id, email FROM senders ORDER BY id"
    ).fetchall()

    conn.close()

    if not senders:
        await update.callback_query.edit_message_text(
            "❌ Belum ada SMTP sender.\n\n"
            "Tambahkan sender terlebih dahulu."
        )
        return

    recipients = get_recipients()

    if not recipients:
        await update.callback_query.edit_message_text(
            "❌ Belum ada email penerima.\n\n"
            "Tambahkan penerima terlebih dahulu."
        )
        return

    keyboard = []

    for sender in senders:
        keyboard.append([
            InlineKeyboardButton(
                sender["email"],
                callback_data=f"choose_sender:{sender['id']}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "❌ Batal",
            callback_data="cancel"
        )
    ])

    await update.callback_query.edit_message_text(
        "📤 *Pilih SMTP Sender*\n\n"
        "Pilih satu sender yang akan digunakan:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


def get_recipients():

    conn = db()

    rows = conn.execute(
        "SELECT email FROM recipients ORDER BY id"
    ).fetchall()

    conn.close()

    return [row["email"] for row in rows]


# ============================================================
# TEXT HANDLER
# ============================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_owner(update):
        return

    state = context.user_data.get("state")

    if state == "add_sender":
        await process_add_sender(update, context)

    elif state == "add_recipient":
        await process_add_recipient(update, context)

    elif state == "set_subject":
        await process_subject(update, context)

    elif state == "set_body":
        await process_body(update, context)

    elif state == "waiting_target":
        await process_target(update, context)


# ============================================================
# ADD SENDER
# ============================================================

async def process_add_sender(update, context):

    email = update.message.text.strip()

    if "@" not in email:
        await update.message.reply_text(
            "❌ Format email tidak valid."
        )
        return

    context.user_data["new_sender_email"] = email
    context.user_data["state"] = "add_sender_password"

    await update.message.reply_text(
        "🔐 Sekarang kirim App Password SMTP untuk email tersebut."
    )


# ============================================================
# PASSWORD HANDLER
# ============================================================

async def password_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_owner(update):
        return

    if context.user_data.get("state") != "add_sender_password":
        return

    password = update.message.text.strip()

    email = context.user_data.get("new_sender_email")

    if not email:
        context.user_data.clear()
        return

    await update.message.delete()

    # Simpan sender
    conn = db()

    try:
        conn.execute(
            "INSERT INTO senders(email, app_password) VALUES (?, ?)",
            (email, password)
        )

        conn.commit()

    except sqlite3.IntegrityError:

        conn.close()

        context.user_data.clear()

        await update.effective_chat.send_message(
            "❌ Email sender tersebut sudah terdaftar."
        )

        return

    conn.close()

    context.user_data.clear()

    await update.effective_chat.send_message(
        "✅ SMTP sender berhasil ditambahkan."
    )


# ============================================================
# ADD RECIPIENT
# ============================================================

async def process_add_recipient(update, context):

    email = update.message.text.strip()

    if "@" not in email:
        await update.message.reply_text(
            "❌ Format email tidak valid."
        )
        return

    conn = db()

    try:
        conn.execute(
            "INSERT INTO recipients(email) VALUES (?)",
            (email,)
        )

        conn.commit()

    except sqlite3.IntegrityError:

        conn.close()

        context.user_data.clear()

        await update.message.reply_text(
            "❌ Email tersebut sudah ada."
        )

        return

    conn.close()

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Email penerima berhasil ditambahkan."
    )


# ============================================================
# SUBJECT
# ============================================================

async def process_subject(update, context):

    subject = update.message.text.strip()

    if not subject:
        return

    set_setting("subject", subject)

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Subject berhasil diperbarui."
    )


# ============================================================
# BODY
# ============================================================

async def process_body(update, context):

    body = update.message.text

    if not body.strip():
        return

    set_setting("body", body)

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Isi email berhasil diperbarui."
    )


# ============================================================
# TARGET
# ============================================================

async def process_target(update, context):

    target = update.message.text.strip()

    if not target:
        return

    sender_id = context.user_data.get("selected_sender")

    if not sender_id:
        context.user_data.clear()
        return

    sender = get_sender(sender_id)

    if not sender:
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Sender tidak ditemukan."
        )

        return

    subject = get_setting("subject")
    body = get_setting("body")

    subject = subject.replace("{username}", target)
    body = body.replace("{username}", target)

    recipients = get_recipients()

    context.user_data["target"] = target
    context.user_data["preview_subject"] = subject
    context.user_data["preview_body"] = body

    text = (
        "📋 *PREVIEW LAPORAN*\n\n"
        f"*Sender:*\n`{sender['email']}`\n\n"
        f"*Penerima:*\n"
        + "\n".join(f"• `{x}`" for x in recipients)
        + "\n\n"
        f"*Subject:*\n{subject}\n\n"
        f"*Isi:*\n{body}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Kirim",
                callback_data="confirm_send"
            ),
            InlineKeyboardButton(
                "❌ Batal",
                callback_data="cancel_report"
            )
        ]
    ]

    context.user_data["state"] = "preview"

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# ============================================================
# SEND EMAIL
# ============================================================

def smtp_send(
    sender_email,
    password,
    recipients,
    subject,
    body
):

    msg = EmailMessage()

    msg["From"] = sender_email
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    msg.set_content(body)

    with smtplib.SMTP_SSL(
        SMTP_HOST,
        SMTP_PORT,
        timeout=30
    ) as smtp:

        smtp.login(
            sender_email,
            password
        )

        smtp.send_message(msg)


async def send_report(update, context):

    query = update.callback_query

    sender_id = context.user_data.get("selected_sender")
    target = context.user_data.get("target")
    subject = context.user_data.get("preview_subject")
    body = context.user_data.get("preview_body")

    if not sender_id or not target:
        await query.edit_message_text(
            "❌ Data laporan tidak lengkap."
        )
        return

    sender = get_sender(sender_id)
    recipients = get_recipients()

    if not sender:
        await query.edit_message_text(
            "❌ SMTP sender tidak ditemukan."
        )
        return

    if not recipients:
        await query.edit_message_text(
            "❌ Tidak ada email penerima."
        )
        return

    await query.edit_message_text(
        "⏳ Sedang mengirim email..."
    )

    try:

        await asyncio.to_thread(
            smtp_send,
            sender["email"],
            sender["app_password"],
            recipients,
            subject,
            body
        )

        context.user_data.clear()

        await query.edit_message_text(
            "✅ *Email berhasil dikirim.*\n\n"
            f"Sender: `{sender['email']}`\n"
            f"Jumlah penerima: `{len(recipients)}`\n"
            f"Target: `{target}`",
            parse_mode="Markdown"
        )

    except Exception as e:

        context.user_data.clear()

        # Jangan tampilkan password SMTP
        error_text = str(e)[:500]

        await query.edit_message_text(
            "❌ *Gagal mengirim email.*\n\n"
            f"Error: `{error_text}`",
            parse_mode="Markdown"
        )


# ============================================================
# UNKNOWN COMMAND / TEXT
# ============================================================

async def unknown_command(update, context):

    if not is_owner(update):
        return

    await update.message.reply_text(
        "Gunakan /start untuk membuka menu."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if BOT_TOKEN == "ISI_BOT_TOKEN":
        raise ValueError(
            "BOT_TOKEN belum diatur."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    # Password diproses sebelum text handler
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            password_handler
        ),
        group=0
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        ),
        group=1
    )

    application.add_handler(
        MessageHandler(
            filters.COMMAND,
            unknown_command
        )
    )

    print("Bot sedang berjalan...")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
