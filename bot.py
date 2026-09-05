import os
import re
import json
import sqlite3
import logging
import asyncio
from html import escape

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "").strip()
REQUIRED_CHANNEL_URL = os.getenv("REQUIRED_CHANNEL_URL", "").strip()
ADMIN_CONTACT_URL = os.getenv("ADMIN_CONTACT_URL", "").strip()
DEFAULT_PRICE = int(os.getenv("LINK_PRICE", "1000"))
DB_PATH = os.getenv("DB_PATH", "pangeran_bot.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN belum diisi.")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID belum diisi.")

TERMS = """📜 <b>TERMS & CONDITIONS</b>
<b>PANGERAN BOT</b>

1. Pengguna wajib memasukkan link yang valid.
2. Dilarang menambahkan link yang melanggar hukum, penipuan, spam, atau konten berbahaya.
3. Biaya penambahan link adalah <b>Rp1.000 per link</b>.
4. Pembayaran harus disertai bukti pembayaran.
5. Link hanya akan dipublikasikan setelah proses pembayaran disetujui admin.
6. Admin berhak menolak atau menghapus link yang dianggap melanggar ketentuan.
7. Pembayaran yang sudah dikonfirmasi tidak dapat dibatalkan untuk link yang telah diproses.

Dengan menggunakan PANGERAN BOT, pengguna dianggap telah menyetujui ketentuan yang berlaku.
"""

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            accepted INTEGER DEFAULT 0, banned INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS states (
            user_id INTEGER PRIMARY KEY, state TEXT, data TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            kind TEXT NOT NULL, amount INTEGER NOT NULL, status TEXT DEFAULT 'pending',
            proof_type TEXT, proof_file_id TEXT, used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, decided_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            kind TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        defaults = {
            "maintenance": "0",
            "poster_file_id": "",
            "qris_file_id": "",
            "price": str(DEFAULT_PRICE),
            "terms": TERMS,
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        c.commit()

def get_setting(key, default=""):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key, value):
    with db() as c:
        c.execute("""INSERT INTO settings(key,value) VALUES(?,?)
                     ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, str(value)))
        c.commit()

def price():
    try:
        return int(get_setting("price", str(DEFAULT_PRICE)))
    except ValueError:
        return DEFAULT_PRICE

def fmt_rp(number):
    return f"Rp{int(number):,}".replace(",", ".")

def upsert_user(user):
    if user:
        with db() as c:
            c.execute("""INSERT INTO users(user_id,username,first_name) VALUES(?,?,?)
                         ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                         first_name=excluded.first_name""",
                      (user.id, user.username or "", user.first_name or ""))
            c.commit()

def user_row(user_id):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def is_banned(user_id):
    row = user_row(user_id)
    return bool(row and row["banned"])

def set_state(user_id, state="", data=None):
    with db() as c:
        if not state:
            c.execute("DELETE FROM states WHERE user_id=?", (user_id,))
        else:
            c.execute("""INSERT INTO states(user_id,state,data) VALUES(?,?,?)
                         ON CONFLICT(user_id) DO UPDATE SET state=excluded.state,
                         data=excluded.data""",
                      (user_id, state, json.dumps(data or {})))
        c.commit()

def get_state(user_id):
    with db() as c:
        row = c.execute("SELECT state,data FROM states WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return "", {}
        try:
            return row["state"], json.loads(row["data"] or "{}")
        except json.JSONDecodeError:
            return row["state"], {}

def is_maintenance():
    return get_setting("maintenance", "0") == "1"

def button(text, callback_data=None, url=None, style=None):
    kw = {"text": text}
    if callback_data is not None:
        kw["callback_data"] = callback_data
    if url is not None:
        kw["url"] = url
    if style:
        kw["style"] = style
    return InlineKeyboardButton(**kw)

def main_kb(user_id):
    rows = [
        [button("👥 GRUP", "menu:group", style="primary"), button("📢 CHANNEL", "menu:channel", style="primary")],
        [button("🌐 SEMUA GRUP", "catalog:group:1", style="success"), button("🌐 SEMUA CHANNEL", "catalog:channel:1", style="success")],
        [button("📜 TERMS & CONDITIONS", "terms", style="primary")],
        [button("📢 CHANNEL WAJIB JOIN", "joininfo", style="primary")],
        [button("👨‍💻 KONTAK ADMIN", "contact", style="danger")],
    ]
    if user_id == ADMIN_ID:
        rows.append([button("👑 OWNER PANEL", "admin:panel", style="danger")])
    return InlineKeyboardMarkup(rows)

def group_kb():
    return InlineKeyboardMarkup([
        [button("➕ Tambah Link Grup", "add:group", style="success")],
        [button("📋 Link Grup Saya", "my:group:1", style="primary")],
        [button("🌐 Semua Link Grup", "catalog:group:1", style="success")],
        [button("🔙 Kembali", "back:main", style="danger")]
    ])

def channel_kb():
    return InlineKeyboardMarkup([
        [button("➕ Tambah Link Channel", "add:channel", style="success")],
        [button("📋 Link Channel Saya", "my:channel:1", style="primary")],
        [button("🌐 Semua Link Channel", "catalog:channel:1", style="success")],
        [button("🔙 Kembali", "back:main", style="danger")]
    ])

def back_kb(target):
    return InlineKeyboardMarkup([[button("🔙 Kembali", target, style="danger")]])

async def send_page(update, context, text, markup=None, edit=False):
    chat_id = update.effective_chat.id
    if edit and update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass
    poster = get_setting("poster_file_id", "")
    if poster:
        try:
            return await context.bot.send_photo(chat_id, poster, caption=text,
                                                parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception as e:
            logger.warning("Poster gagal dikirim: %s", e)
    return await context.bot.send_message(chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=markup)

async def send_page_for_user(context, user_id, text, markup=None):
    poster = get_setting("poster_file_id", "")
    if poster:
        try:
            return await context.bot.send_photo(user_id, poster, caption=text,
                                                parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass
    return await context.bot.send_message(user_id, text=text, parse_mode=ParseMode.HTML, reply_markup=markup)

async def joined_required_channel(context, user_id):
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = await context.bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator") or (
            member.status == "restricted" and getattr(member, "is_member", False))
    except Exception as e:
        logger.warning("Gagal mengecek channel: %s", e)
        return False

async def access_ok(update, context):
    user = update.effective_user
    upsert_user(user)
    if is_banned(user.id):
        await send_page(update, context, "🚫 <b>AKSES DITOLAK</b>\n\nAkun Anda dibatasi oleh admin.",
                        back_kb("back:main"))
        return False
    if is_maintenance() and user.id != ADMIN_ID:
        await send_page(update, context, "🛠 <b>BOT SEDANG MAINTENANCE</b>\n\nSilakan coba lagi nanti.",
                        back_kb("back:main"))
        return False
    row = user_row(user.id)
    if not row or not row["accepted"]:
        return False
    if not await joined_required_channel(context, user.id):
        await show_join(update, context, edit=False)
        return False
    return True

async def start(update, context):
    user = update.effective_user
    upsert_user(user)
    if is_banned(user.id):
        await send_page(update, context, "🚫 <b>AKSES DITOLAK</b>\n\nAkun Anda dibatasi oleh admin.")
        return
    row = user_row(user.id)
    if row and row["accepted"]:
        if is_maintenance() and user.id != ADMIN_ID:
            await send_page(update, context, "🛠 <b>BOT SEDANG MAINTENANCE</b>")
            return
        if not await joined_required_channel(context, user.id):
            await show_join(update, context)
            return
        await show_main(update, context, edit=False)
        return
    text = """👑 <b>SELAMAT DATANG DI PANGERAN BOT</b>

Bot untuk menyimpan dan menemukan link grup & channel Telegram.

Sebelum menggunakan layanan, silakan baca ketentuan dan pastikan Anda telah bergabung ke channel wajib."""
    kb = InlineKeyboardMarkup([
        [button("📜 Terms & Conditions", "terms", style="primary")],
        [button("📢 Wajib Join Channel", "joininfo", style="primary")],
        [button("👨‍💻 Kontak Admin", "contact", style="danger")],
        [button("✅ Saya Setuju & Lanjut", "accept", style="success")]
    ])
    await send_page(update, context, text, kb)

async def show_main(update, context, edit=True):
    await send_page(update, context, "👑 <b>PANGERAN BOT</b>\n\nSilakan pilih layanan yang ingin digunakan.",
                    main_kb(update.effective_user.id), edit=edit)

async def show_terms(update, context, edit=True):
    row = user_row(update.effective_user.id)
    target = "back:main" if row and row["accepted"] else "back:onboarding"
    await send_page(update, context, get_setting("terms", TERMS), back_kb(target), edit=edit)

async def show_join(update, context, edit=True):
    buttons = []
    if REQUIRED_CHANNEL_URL:
        buttons.append([button("📢 JOIN CHANNEL", url=REQUIRED_CHANNEL_URL, style="primary")])
    buttons.append([button("✅ CEK KEANGGOTAAN", "checkjoin", style="success")])
    buttons.append([button("🔙 Kembali", "back:onboarding", style="danger")])
    text = """📢 <b>CHANNEL WAJIB JOIN</b>

Silakan bergabung terlebih dahulu ke channel wajib.

Setelah bergabung, tekan tombol <b>CEK KEANGGOTAAN</b>."""
    if not REQUIRED_CHANNEL:
        text = "📢 <b>CHANNEL WAJIB JOIN</b>\n\nSaat ini tidak ada channel wajib yang dikonfigurasi."
    await send_page(update, context, text, InlineKeyboardMarkup(buttons), edit=edit)

async def show_contact(update, context, edit=True):
    row = user_row(update.effective_user.id)
    target = "back:main" if row and row["accepted"] else "back:onboarding"
    rows = []
    if ADMIN_CONTACT_URL:
        rows.append([button("👨‍💻 HUBUNGI ADMIN", url=ADMIN_CONTACT_URL, style="primary")])
    rows.append([button("🔙 Kembali", target, style="danger")])
    await send_page(update, context, "👨‍💻 <b>KONTAK ADMIN</b>\n\nJika membutuhkan bantuan, silakan hubungi admin melalui tombol di bawah.",
                    InlineKeyboardMarkup(rows), edit=edit)

async def accept(update, context):
    user = update.effective_user
    if not await joined_required_channel(context, user.id):
        await show_join(update, context, edit=True)
        return
    with db() as c:
        c.execute("UPDATE users SET accepted=1 WHERE user_id=?", (user.id,))
        c.commit()
    await show_main(update, context, edit=True)

async def show_menu_kind(update, context, kind, edit=True):
    if not await access_ok(update, context):
        return
    if kind == "group":
        await send_page(update, context, "👥 <b>MENU GRUP</b>\n\nKelola link grup Telegram Anda.",
                        group_kb(), edit=edit)
    else:
        await send_page(update, context, "📢 <b>MENU CHANNEL</b>\n\nKelola link channel Telegram Anda.",
                        channel_kb(), edit=edit)

async def show_add_payment(update, context, kind, edit=True):
    if not await access_ok(update, context):
        return
    set_state(update.effective_user.id, "choose_payment", {"kind": kind})
    p = price()
    label = "GRUP" if kind == "group" else "CHANNEL"
    text = f"""💳 <b>TAMBAH LINK {label}</b>

Biaya penambahan:
<b>{fmt_rp(p)}</b> / 1 link

Silakan lakukan pembayaran melalui QRIS admin, lalu kirim bukti pembayaran.

<b>1 pembayaran hanya berlaku untuk 1 link.</b>"""
    kb = InlineKeyboardMarkup([
        [button(f"💰 Bayar {fmt_rp(p)}", f"pay:{kind}", style="success")],
        [button("📤 Kirim Bukti Pembayaran", "proof", style="primary")],
        [button("❌ Batal", "cancel", style="danger")],
        [button("🔙 Kembali", f"back:{kind}", style="danger")]
    ])
    await send_page(update, context, text, kb, edit=edit)

async def show_payment(update, context, kind, edit=True):
    if not await access_ok(update, context):
        return
    set_state(update.effective_user.id, "waiting_proof", {"kind": kind})
    p = price()
    text = f"""💰 <b>PEMBAYARAN {fmt_rp(p)}</b>

Silakan scan QRIS di bawah dan lakukan pembayaran.

Setelah selesai, tekan <b>📤 Kirim Bukti Pembayaran</b> lalu kirim screenshot/foto bukti pembayaran."""
    kb = InlineKeyboardMarkup([
        [button("📤 Kirim Bukti Pembayaran", "proof", style="primary")],
        [button("❌ Batal", "cancel", style="danger")],
        [button("🔙 Kembali", f"back:{kind}", style="danger")]
    ])
    await send_page(update, context, text, kb, edit=edit)
    qris = get_setting("qris_file_id", "")
    if qris:
        try:
            await context.bot.send_photo(update.effective_chat.id, qris,
                                         caption="🧾 <b>QRIS PEMBAYARAN</b>", parse_mode=ParseMode.HTML)
        except Exception:
            pass
    else:
        await context.bot.send_message(update.effective_chat.id, "⚠️ QRIS belum dipasang admin.")

async def request_proof(update, context, edit=True):
    _, data = get_state(update.effective_user.id)
    kind = data.get("kind", "group")
    set_state(update.effective_user.id, "waiting_proof", {"kind": kind})
    await send_page(update, context, "📤 <b>KIRIM BUKTI PEMBAYARAN</b>\n\nSilakan kirim foto atau dokumen bukti pembayaran di chat ini.",
                    InlineKeyboardMarkup([
                        [button("❌ Batal", "cancel", style="danger")],
                        [button("🔙 Kembali", f"back:add:{kind}", style="danger")]
                    ]), edit=edit)

async def handle_proof(update, context):
    user = update.effective_user
    state, data = get_state(user.id)
    if state != "waiting_proof":
        return False
    kind = data.get("kind", "group")
    if update.message.photo:
        file_id, proof_type = update.message.photo[-1].file_id, "photo"
    elif update.message.document:
        file_id, proof_type = update.message.document.file_id, "document"
    else:
        await send_page(update, context, "⚠️ Kirim <b>foto atau dokumen</b> sebagai bukti pembayaran.",
                        back_kb(f"back:add:{kind}"))
        return True
    with db() as c:
        cur = c.execute("""INSERT INTO payments(user_id,kind,amount,status,proof_type,proof_file_id)
                           VALUES(?,?,?,?,?,?)""",
                        (user.id, kind, price(), "pending", proof_type, file_id))
        payment_id = cur.lastrowid
        c.commit()
    set_state(user.id, "waiting_payment_review", {"kind": kind, "payment_id": payment_id})
    username = f"@{user.username}" if user.username else "-"
    caption = f"""💳 <b>PEMBAYARAN BARU</b>

ID: <code>#{payment_id}</code>

User:
<a href="tg://user?id={user.id}">{escape(user.first_name or 'User')}</a>

Username: {escape(username)}
User ID: <code>{user.id}</code>
Jenis: <b>{kind.upper()}</b>
Nominal: <b>{fmt_rp(price())}</b>

Silakan verifikasi pembayaran."""
    kb = InlineKeyboardMarkup([[
        button("✅ TERIMA", f"payok:{payment_id}", style="success"),
        button("❌ TOLAK", f"payno:{payment_id}", style="danger")
    ]])
    try:
        if proof_type == "photo":
            await context.bot.send_photo(ADMIN_ID, file_id, caption=caption,
                                         parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await context.bot.send_document(ADMIN_ID, file_id, caption=caption,
                                            parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception as e:
        logger.error("Gagal kirim bukti: %s", e)
    await send_page(update, context, "⏳ <b>BUKTI TERKIRIM</b>\n\nBukti pembayaran sudah diteruskan ke admin.\n\nSilakan tunggu konfirmasi admin.",
                    back_kb(f"back:{kind}"))
    return True

async def admin_decide_payment(update, context, payment_id, approved):
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.answer("Akses ditolak.", show_alert=True)
        return
    with db() as c:
        pay = c.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not pay:
            await update.callback_query.answer("Pembayaran tidak ditemukan.", show_alert=True)
            return
        if pay["status"] != "pending":
            await update.callback_query.answer("Pembayaran sudah diproses.", show_alert=True)
            return
        c.execute("UPDATE payments SET status=?, decided_at=CURRENT_TIMESTAMP WHERE id=?",
                  ("approved" if approved else "rejected", payment_id))
        c.commit()
    if approved:
        set_state(pay["user_id"], "waiting_link", {"kind": pay["kind"], "payment_id": payment_id})
        label = "grup" if pay["kind"] == "group" else "channel"
        await send_page_for_user(context, pay["user_id"],
            f"✅ <b>PEMBAYARAN DISETUJUI</b>\n\nPembayaran <code>#{payment_id}</code> telah diterima.\n\nSekarang kirim <b>1 link {label}</b> yang ingin ditambahkan.",
            back_kb(f"back:{pay['kind']}"))
        await update.callback_query.answer("Pembayaran diterima.")
    else:
        set_state(pay["user_id"], "")
        await send_page_for_user(context, pay["user_id"],
            "❌ <b>PEMBAYARAN DITOLAK</b>\n\nBukti pembayaran tidak dapat diverifikasi.\n\nSilakan coba lakukan pembayaran kembali.",
            InlineKeyboardMarkup([
                [button("🔄 Coba Lagi", f"add:{pay['kind']}", style="success")],
                [button("🔙 Kembali", "back:main", style="danger")]
            ]))
        await update.callback_query.answer("Pembayaran ditolak.")
    try:
        await update.callback_query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

def normalize_link(text):
    text = text.strip()
    if text.startswith("@") and re.fullmatch(r"@[A-Za-z0-9_]{3,32}", text):
        return "https://t.me/" + text[1:]
    if re.match(r"^(https?://)?(t\.me|telegram\.me)/", text, re.I):
        return text if text.lower().startswith("http") else "https://" + text
    return None

async def handle_link(update, context):
    user = update.effective_user
    state, data = get_state(user.id)
    if state != "waiting_link":
        return False
    kind = data.get("kind")
    payment_id = int(data.get("payment_id", 0))
    link = normalize_link(update.message.text or "")
    if not link:
        await send_page(update, context, "⚠️ <b>LINK TIDAK VALID</b>\n\nKirim link Telegram seperti:\n\n<code>https://t.me/namagrup</code>",
                        back_kb(f"back:{kind}"))
        return True
    with db() as c:
        payment = c.execute("""SELECT * FROM payments
                               WHERE id=? AND user_id=? AND status='approved' AND used=0""",
                            (payment_id, user.id)).fetchone()
        if not payment:
            set_state(user.id, "")
            await send_page(update, context, "⚠️ Pembayaran tidak ditemukan atau sudah digunakan.",
                            back_kb("back:main"))
            return True
        exists = c.execute("SELECT id FROM links WHERE url=?", (link,)).fetchone()
        if exists:
            await send_page(update, context, "⚠️ Link tersebut sudah terdaftar di katalog.",
                            back_kb(f"back:{kind}"))
            return True
        c.execute("INSERT INTO links(user_id,kind,url) VALUES(?,?,?)", (user.id, kind, link))
        c.execute("UPDATE payments SET used=1 WHERE id=?", (payment_id,))
        c.commit()
    set_state(user.id, "")
    label = "Grup" if kind == "group" else "Channel"
    await send_page(update, context,
        f"✅ <b>LINK {label.upper()} BERHASIL DISIMPAN</b>\n\n🔗 {escape(link)}\n\nLink sudah masuk ke katalog publik.",
        InlineKeyboardMarkup([
            [button(f"🔗 Buka {label}", url=link, style="success")],
            [button("🔙 Kembali", f"back:{kind}", style="danger")]
        ]))
    return True

async def catalog(update, context, kind, page=1, mine=False, edit=True):
    if not await access_ok(update, context):
        return
    page = max(1, int(page))
    per_page = 5
    offset = (page - 1) * per_page
    with db() as c:
        where = "WHERE kind=?"
        params = [kind]
        if mine:
            where += " AND user_id=?"
            params.append(update.effective_user.id)
        total = c.execute(f"SELECT COUNT(*) AS n FROM links {where}", params).fetchone()["n"]
        rows = c.execute(f"SELECT * FROM links {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                         params + [per_page, offset]).fetchall()
    label = "GRUP" if kind == "group" else "CHANNEL"
    lines = [f"📋 <b>LINK {label} {'SAYA' if mine else 'PUBLIK'}</b>", ""]
    if not rows:
        lines.append("Belum ada link.")
    else:
        for i, row in enumerate(rows, start=offset + 1):
            lines.append(f"<b>{i}.</b> <code>{escape(row['url'])}</code>")
    buttons = []
    for row in rows:
        buttons.append([button("📢 Buka Channel" if kind == "channel" else "🔗 Buka Grup",
                               url=row["url"], style="success")])
    nav = []
    prefix = "my" if mine else "catalog"
    if page > 1:
        nav.append(button("⬅️", f"{prefix}:{kind}:{page-1}", style="primary"))
    if offset + len(rows) < total:
        nav.append(button("➡️", f"{prefix}:{kind}:{page+1}", style="primary"))
    if nav:
        buttons.append(nav)
    buttons.append([button("🔙 Kembali", "back:channel" if kind == "channel" else "back:group", style="danger")])
    await send_page(update, context, "\n".join(lines), InlineKeyboardMarkup(buttons), edit=edit)

def admin_kb():
    return InlineKeyboardMarkup([
        [button("📊 Statistik Bot", "admin:stats", style="primary"), button("💳 Kelola Pembayaran", "admin:payments", style="success")],
        [button("🔗 Kelola Link", "admin:links", style="primary"), button("👤 Kelola Pengguna", "admin:users", style="primary")],
        [button("🖼️ Set Poster Branding", "admin:poster", style="success"), button("🧾 Set QRIS", "admin:qris", style="success")],
        [button("⚙️ Pengaturan Bot", "admin:settings", style="primary")],
        [button("📣 Broadcast", "admin:broadcast", style="primary")],
        [button("🛠 Maintenance", "admin:maintenance", style="danger")],
        [button("🔙 Kembali", "back:main", style="danger")]
    ])

async def admin_panel(update, context, edit=True):
    if update.effective_user.id == ADMIN_ID:
        await send_page(update, context, "👑 <b>PANGERAN BOT — OWNER PANEL</b>\n\nPusat kontrol dan pengelolaan bot.",
                        admin_kb(), edit=edit)

async def admin_stats(update, context, edit=True):
    if update.effective_user.id != ADMIN_ID: return
    with db() as c:
        users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        groups = c.execute("SELECT COUNT(*) n FROM links WHERE kind='group'").fetchone()["n"]
        channels = c.execute("SELECT COUNT(*) n FROM links WHERE kind='channel'").fetchone()["n"]
        payments = c.execute("SELECT COUNT(*) n FROM payments").fetchone()["n"]
        pending = c.execute("SELECT COUNT(*) n FROM payments WHERE status='pending'").fetchone()["n"]
        revenue = c.execute("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE status='approved'").fetchone()["n"]
        today = c.execute("SELECT COUNT(*) n FROM links WHERE date(created_at)=date('now')").fetchone()["n"]
    text = f"""📊 <b>STATISTIK BOT</b>

👤 Pengguna: <b>{users}</b>
👥 Grup: <b>{groups}</b>
📢 Channel: <b>{channels}</b>
💳 Total pembayaran: <b>{payments}</b>
⏳ Pending: <b>{pending}</b>
💰 Pendapatan: <b>{fmt_rp(revenue)}</b>
📅 Link hari ini: <b>{today}</b>"""
    await send_page(update, context, text, back_kb("admin:panel"), edit=edit)

async def admin_payments(update, context, edit=True):
    if update.effective_user.id != ADMIN_ID: return
    with db() as c:
        rows = c.execute("SELECT * FROM payments ORDER BY id DESC LIMIT 15").fetchall()
    lines = ["💳 <b>KELOLA PEMBAYARAN</b>", ""]
    for r in rows:
        lines.append(f"#{r['id']} • {r['kind']} • {fmt_rp(r['amount'])} • <b>{r['status']}</b>")
    if not rows: lines.append("Belum ada pembayaran.")
    await send_page(update, context, "\n".join(lines), InlineKeyboardMarkup([
        [button("🖼️ Upload / Ubah QRIS", "admin:qris", style="success")],
        [button("🔙 Kembali", "admin:panel", style="danger")]
    ]), edit=edit)

async def admin_links(update, context, edit=True):
    if update.effective_user.id != ADMIN_ID: return
    with db() as c:
        rows = c.execute("SELECT * FROM links ORDER BY id DESC LIMIT 20").fetchall()
    lines, buttons = ["🔗 <b>KELOLA LINK</b>", ""], []
    for r in rows:
        lines.append(f"#{r['id']} • {r['kind']} • {escape(r['url'])} • <code>{r['user_id']}</code>")
        buttons.append([button(f"🗑️ Hapus #{r['id']}", f"del:{r['id']}", style="danger")])
    if not rows: lines.append("Belum ada link.")
    buttons.append([button("🔙 Kembali", "admin:panel", style="danger")])
    await send_page(update, context, "\n".join(lines), InlineKeyboardMarkup(buttons), edit=edit)

async def admin_users(update, context, edit=True):
    if update.effective_user.id != ADMIN_ID: return
    with db() as c:
        rows = c.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 20").fetchall()
    lines, buttons = ["👤 <b>KELOLA PENGGUNA</b>", ""], []
    for r in rows:
        name = "@" + r["username"] if r["username"] else (r["first_name"] or str(r["user_id"]))
        status = "🚫 BANNED" if r["banned"] else "✅ Aktif"
        lines.append(f"<code>{r['user_id']}</code> • {escape(name)} • {status}")
        buttons.append([button(("♻️ Unban " if r["banned"] else "🚫 Ban ") + name, f"ban:{r['user_id']}",
                               style="success" if r["banned"] else "danger")])
    buttons.append([button("🔙 Kembali", "admin:panel", style="danger")])
    await send_page(update, context, "\n".join(lines), InlineKeyboardMarkup(buttons), edit=edit)

async def admin_settings(update, context, edit=True):
    if update.effective_user.id != ADMIN_ID: return
    text = f"""⚙️ <b>PENGATURAN BOT</b>

💰 Harga per link: <b>{fmt_rp(price())}</b>
📢 Required channel: <code>{escape(REQUIRED_CHANNEL or '-')}</code>
👨‍💻 Kontak admin: <code>{escape(ADMIN_CONTACT_URL or '-')}</code>
🛠 Maintenance: <b>{'ON' if is_maintenance() else 'OFF'}</b>"""
    await send_page(update, context, text, InlineKeyboardMarkup([
        [button("💰 Ubah Harga", "admin:price", style="success")],
        [button("🔙 Kembali", "admin:panel", style="danger")]
    ]), edit=edit)

async def admin_price_prompt(update, context):
    if update.effective_user.id != ADMIN_ID: return
    set_state(ADMIN_ID, "admin_price")
    await send_page(update, context, "💰 <b>UBAH HARGA</b>\n\nKirim nominal angka saja.\n\nContoh:\n<code>1000</code>",
                    back_kb("admin:settings"))

async def admin_upload_prompt(update, context, what):
    if update.effective_user.id != ADMIN_ID: return
    if what == "poster":
        state, text = "admin_poster", "🖼️ <b>SET POSTER BRANDING</b>\n\nKirim 1 foto poster branding PANGERAN BOT.\n\nPoster ini akan digunakan di seluruh menu dan pesan bot."
    else:
        state, text = "admin_qris", "🧾 <b>SET QRIS</b>\n\nKirim 1 foto QRIS pembayaran.\n\nQRIS terbaru akan digunakan untuk pembayaran berikutnya."
    set_state(ADMIN_ID, state)
    await send_page(update, context, text, back_kb("admin:panel"))

async def admin_broadcast_prompt(update, context):
    if update.effective_user.id != ADMIN_ID: return
    set_state(ADMIN_ID, "admin_broadcast")
    await send_page(update, context, "📣 <b>BROADCAST</b>\n\nKirim teks atau foto dengan caption yang ingin dikirim ke seluruh pengguna bot.",
                    back_kb("admin:panel"))

async def admin_maintenance(update, context):
    if update.effective_user.id != ADMIN_ID: return
    set_setting("maintenance", "0" if is_maintenance() else "1")
    await admin_panel(update, context, edit=True)

async def handle_admin_input(update, context):
    if update.effective_user.id != ADMIN_ID: return False
    state, _ = get_state(ADMIN_ID)

    if state == "admin_poster":
        if not update.message.photo:
            await send_page(update, context, "⚠️ Kirim foto poster branding.", back_kb("admin:panel"))
            return True
        set_setting("poster_file_id", update.message.photo[-1].file_id)
        set_state(ADMIN_ID, "")
        await send_page(update, context, "✅ <b>POSTER BRANDING BERHASIL DISIMPAN</b>\n\nPoster ini sekarang digunakan di seluruh tampilan menu bot.",
                        back_kb("admin:panel"))
        return True

    if state == "admin_qris":
        if not update.message.photo:
            await send_page(update, context, "⚠️ Kirim foto QRIS.", back_kb("admin:panel"))
            return True
        set_setting("qris_file_id", update.message.photo[-1].file_id)
        set_state(ADMIN_ID, "")
        await send_page(update, context, "✅ <b>QRIS BERHASIL DIPERBARUI</b>\n\nQRIS baru akan digunakan untuk pembayaran berikutnya.",
                        back_kb("admin:panel"))
        return True

    if state == "admin_price":
        try:
            value = int(re.sub(r"[^\d]", "", update.message.text or ""))
            if value <= 0: raise ValueError
        except ValueError:
            await send_page(update, context, "⚠️ Nominal tidak valid.\n\nContoh:\n<code>1000</code>",
                            back_kb("admin:settings"))
            return True
        set_setting("price", value)
        set_state(ADMIN_ID, "")
        await send_page(update, context, f"✅ <b>HARGA BERHASIL DIUBAH</b>\n\nHarga baru:\n<b>{fmt_rp(value)}</b> / link",
                        back_kb("admin:settings"))
        return True

    if state == "admin_broadcast":
        text = update.message.text or update.message.caption or ""
        if not text:
            await send_page(update, context, "⚠️ Kirim teks broadcast atau foto dengan caption.", back_kb("admin:panel"))
            return True
        with db() as c:
            rows = c.execute("SELECT user_id FROM users WHERE banned=0").fetchall()
        success = failed = 0
        for r in rows:
            try:
                if update.message.photo:
                    await context.bot.send_photo(r["user_id"], update.message.photo[-1].file_id,
                                                 caption=text, parse_mode=ParseMode.HTML)
                else:
                    await context.bot.send_message(r["user_id"], text=text, parse_mode=ParseMode.HTML)
                success += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        set_state(ADMIN_ID, "")
        await send_page(update, context, f"📣 <b>BROADCAST SELESAI</b>\n\n✅ Berhasil: <b>{success}</b>\n❌ Gagal: <b>{failed}</b>",
                        back_kb("admin:panel"))
        return True
    return False

async def on_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = update.effective_user.id
    upsert_user(update.effective_user)

    if data == "terms": await show_terms(update, context); return
    if data == "joininfo": await show_join(update, context); return
    if data == "contact": await show_contact(update, context); return
    if data == "accept": await accept(update, context); return
    if data == "checkjoin":
        if await joined_required_channel(context, user_id):
            with db() as c:
                c.execute("UPDATE users SET accepted=1 WHERE user_id=?", (user_id,))
                c.commit()
            await show_main(update, context)
        else:
            await send_page(update, context, "❌ Anda belum terdeteksi sebagai anggota channel wajib.",
                            back_kb("joininfo"), edit=True)
        return

    if data == "menu:group": await show_menu_kind(update, context, "group"); return
    if data == "menu:channel": await show_menu_kind(update, context, "channel"); return
    if data.startswith("add:"): await show_add_payment(update, context, data.split(":")[1]); return
    if data.startswith("pay:"): await show_payment(update, context, data.split(":")[1]); return
    if data == "proof": await request_proof(update, context); return
    if data == "cancel":
        set_state(user_id, ""); await show_main(update, context); return
    if data.startswith("catalog:"):
        _, kind, page = data.split(":"); await catalog(update, context, kind, int(page)); return
    if data.startswith("my:"):
        _, kind, page = data.split(":"); await catalog(update, context, kind, int(page), mine=True); return

    if data.startswith("back:"):
        target = data[5:]
        if target == "main": await show_main(update, context); return
        if target == "onboarding": await start(update, context); return
        if target == "group": await show_menu_kind(update, context, "group"); return
        if target == "channel": await show_menu_kind(update, context, "channel"); return
        if target.startswith("add:"): await show_add_payment(update, context, target.split(":")[1]); return
        if target.startswith("admin"): await admin_panel(update, context); return

    if data.startswith("payok:"): await admin_decide_payment(update, context, int(data.split(":")[1]), True); return
    if data.startswith("payno:"): await admin_decide_payment(update, context, int(data.split(":")[1]), False); return

    if data == "admin:panel":
        if user_id != ADMIN_ID: return
        await admin_panel(update, context); return
    if data == "admin:stats":
        if user_id != ADMIN_ID: return
        await admin_stats(update, context); return
    if data == "admin:payments":
        if user_id != ADMIN_ID: return
        await admin_payments(update, context); return
    if data == "admin:links":
        if user_id != ADMIN_ID: return
        await admin_links(update, context); return
    if data == "admin:users":
        if user_id != ADMIN_ID: return
        await admin_users(update, context); return
    if data == "admin:poster":
        if user_id != ADMIN_ID: return
        await admin_upload_prompt(update, context, "poster"); return
    if data == "admin:qris":
        if user_id != ADMIN_ID: return
        await admin_upload_prompt(update, context, "qris"); return
    if data == "admin:settings":
        if user_id != ADMIN_ID: return
        await admin_settings(update, context); return
    if data == "admin:price":
        if user_id != ADMIN_ID: return
        await admin_price_prompt(update, context); return
    if data == "admin:broadcast":
        if user_id != ADMIN_ID: return
        await admin_broadcast_prompt(update, context); return
    if data == "admin:maintenance":
        if user_id != ADMIN_ID: return
        await admin_maintenance(update, context); return

    if data.startswith("del:"):
        if user_id != ADMIN_ID: return
        with db() as c:
            c.execute("DELETE FROM links WHERE id=?", (int(data.split(":")[1]),))
            c.commit()
        await admin_links(update, context); return

    if data.startswith("ban:"):
        if user_id != ADMIN_ID: return
        target = int(data.split(":")[1])
        if target == ADMIN_ID: return
        with db() as c:
            row = c.execute("SELECT banned FROM users WHERE user_id=?", (target,)).fetchone()
            if row:
                c.execute("UPDATE users SET banned=? WHERE user_id=?", (0 if row["banned"] else 1, target))
                c.commit()
        await admin_users(update, context); return

async def message_router(update, context):
    if not update.effective_user or not update.message: return
    upsert_user(update.effective_user)
    if await handle_admin_input(update, context): return
    if await handle_proof(update, context): return
    if update.message.text and await handle_link(update, context): return
    row = user_row(update.effective_user.id)
    if row and row["accepted"] and not is_banned(update.effective_user.id):
        await show_main(update, context, edit=False)

async def admin_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Akses ditolak.")
        return
    await admin_panel(update, context, edit=False)

async def error_handler(update, context):
    logger.exception("Unhandled error:", exc_info=context.error)

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | (filters.TEXT & ~filters.COMMAND), message_router))
    app.add_error_handler(error_handler)
    logger.info("👑 PANGERAN BOT berjalan...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
