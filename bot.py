import os
import sqlite3
import logging
from datetime import datetime
from contextlib import contextmanager

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "noktel_lama.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("noktel_lama")

TERMS = """📜 SYARAT & KETENTUAN

Sebelum menggunakan NOKTEL LAMA, harap membaca dan menyetujui ketentuan berikut:

• Gunakan layanan secara bertanggung jawab.
• Pastikan detail pesanan sudah benar sebelum pembayaran.
• Pembayaran diverifikasi oleh Admin.
• Dilarang melakukan penipuan atau penyalahgunaan layanan.
• Produk yang dijual harus merupakan aset yang sah untuk diperjualbelikan.
• Dengan menekan "Saya Setuju", Anda menyatakan telah membaca dan menyetujui ketentuan ini.

Apakah Anda setuju?"""

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            accepted_terms INTEGER DEFAULT 0,
            balance INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS countries(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            emoji TEXT DEFAULT '🌍',
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT NOT NULL UNIQUE,
            country_id INTEGER NOT NULL,
            digits INTEGER NOT NULL,
            price INTEGER NOT NULL,
            status TEXT DEFAULT 'available',
            created_at TEXT NOT NULL,
            FOREIGN KEY(country_id) REFERENCES countries(id)
        );

        CREATE TABLE IF NOT EXISTS topups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT DEFAULT 'waiting',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT DEFAULT 'completed',
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        );

        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS support_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            created_at TEXT NOT NULL
        );
        """)

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def rupiah(n):
    return "Rp{:,.0f}".format(n).replace(",", ".")

def get_setting(key, default=None):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

def set_setting(key, value):
    with db() as c:
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )

def get_user(uid, username=None):
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if not r:
            c.execute(
                "INSERT INTO users(user_id,username,created_at) VALUES(?,?,?)",
                (uid, username, now())
            )
            return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if username != r["username"]:
            c.execute("UPDATE users SET username=? WHERE user_id=?", (username, uid))
        return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

def is_owner(uid):
    return OWNER_ID and uid == OWNER_ID

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Belanja ID", callback_data="shop"),
         InlineKeyboardButton("💰 Top Up Saldo", callback_data="topup")],
        [InlineKeyboardButton("📜 Riwayat", callback_data="history"),
         InlineKeyboardButton("🆘 Bantuan", callback_data="help")],
    ])

def owner_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Kelola ID", callback_data="o_products"),
         InlineKeyboardButton("🌍 Kelola Negara", callback_data="o_countries")],
        [InlineKeyboardButton("📱 Kelola QRIS", callback_data="o_qris"),
         InlineKeyboardButton("💰 Nominal Top Up", callback_data="o_topups")],
        [InlineKeyboardButton("🧾 Pesanan", callback_data="o_orders"),
         InlineKeyboardButton("👥 Pengguna", callback_data="o_users")],
        [InlineKeyboardButton("📊 Statistik", callback_data="o_stats"),
         InlineKeyboardButton("📢 Pengumuman", callback_data="o_broadcast")],
        [InlineKeyboardButton("🆘 Pesan Bantuan", callback_data="o_support")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="home")],
    ])

async def send_terms(update, context):
    await update.effective_message.reply_text(
        TERMS,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Saya Setuju", callback_data="terms_yes"),
            InlineKeyboardButton("❌ Saya Tidak Setuju", callback_data="terms_no")
        ]])
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    row = get_user(u.id, u.username)
    if not row["accepted_terms"]:
        await send_terms(update, context)
        return
    await show_home(update, context)

async def show_home(update, context):
    u = update.effective_user
    row = get_user(u.id, u.username)
    text = f"🕰️ NOKTEL LAMA\n\nSelamat datang di marketplace ID lama.\n\n💰 Saldo: {rupiah(row['balance'])}"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_keyboard())
    else:
        await update.effective_message.reply_text(text, reply_markup=main_keyboard())

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    row = get_user(uid, q.from_user.username)

    if q.data == "terms_yes":
        with db() as c:
            c.execute("UPDATE users SET accepted_terms=1 WHERE user_id=?", (uid,))
        await q.edit_message_text("✅ Persetujuan berhasil disimpan.\n\nSelamat datang di 🕰️ NOKTEL LAMA!")
        await q.message.reply_text(
            f"🕰️ NOKTEL LAMA\n\n💰 Saldo: {rupiah(get_user(uid)['balance'])}",
            reply_markup=main_keyboard()
        )
        return

    if q.data == "terms_no":
        await q.edit_message_text("❌ AKSES DIBATALKAN\n\nAnda belum menyetujui Syarat & Ketentuan. Gunakan /start kembali jika ingin melanjutkan.")
        return

    if not row["accepted_terms"]:
        await q.edit_message_text(TERMS, reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Saya Setuju", callback_data="terms_yes"),
            InlineKeyboardButton("❌ Saya Tidak Setuju", callback_data="terms_no")
        ]]))
        return

    if q.data in ("home", "back_home"):
        await show_home(update, context); return

    if q.data == "shop":
        with db() as c:
            countries = c.execute(
                "SELECT * FROM countries WHERE active=1 ORDER BY name"
            ).fetchall()
        if not countries:
            await q.edit_message_text(
                "⚠️ BELUM TERSEDIA\n\nSaat ini belum ada negara atau ID yang tersedia.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Kembali", callback_data="home")]])
            )
            return
        kb = [[InlineKeyboardButton(f"{r['emoji']} {r['name']}", callback_data=f"country:{r['id']}")] for r in countries]
        kb.append([InlineKeyboardButton("🏠 Kembali", callback_data="home")])
        await q.edit_message_text("🌍 PILIH NEGARA", reply_markup=InlineKeyboardMarkup(kb))
        return

    if q.data.startswith("country:"):
        cid = int(q.data.split(":")[1])
        with db() as c:
            country = c.execute("SELECT * FROM countries WHERE id=? AND active=1", (cid,)).fetchone()
            digits = c.execute(
                "SELECT DISTINCT digits FROM products WHERE country_id=? AND status='available' ORDER BY digits",
                (cid,)
            ).fetchall()
        if not country or not digits:
            await q.edit_message_text(
                "⚠️ BELUM TERSEDIA\n\nBelum ada ID yang tersedia untuk negara ini.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="shop")]])
            )
            return
        kb = [[InlineKeyboardButton(f"🔢 {d['digits']} Digit", callback_data=f"digits:{cid}:{d['digits']}")] for d in digits]
        kb.append([InlineKeyboardButton("🔙 Kembali", callback_data="shop")])
        await q.edit_message_text(f"{country['emoji']} {country['name']}\n\nPilih jumlah digit ID:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if q.data.startswith("digits:"):
        _, cid, digits = q.data.split(":")
        cid, digits = int(cid), int(digits)
        with db() as c:
            country = c.execute("SELECT * FROM countries WHERE id=?", (cid,)).fetchone()
            products = c.execute(
                "SELECT * FROM products WHERE country_id=? AND digits=? AND status='available' ORDER BY price, id LIMIT 50",
                (cid, digits)
            ).fetchall()
        if not products:
            await q.edit_message_text(
                f"⚠️ BELUM TERSEDIA\n\nSaat ini belum ada ID {digits} digit untuk negara tersebut.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data=f"country:{cid}")]])
            )
            return
        kb = [[InlineKeyboardButton(f"🔢 {p['number']} — {rupiah(p['price'])}", callback_data=f"product:{p['id']}")] for p in products]
        kb.append([InlineKeyboardButton("🔙 Kembali", callback_data=f"country:{cid}")])
        await q.edit_message_text(f"{country['emoji']} {country['name']}\n🔢 {digits} DIGIT\n\nPilih ID:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if q.data.startswith("product:"):
        pid = int(q.data.split(":")[1])
        with db() as c:
            p = c.execute("""SELECT p.*, c.name country_name, c.emoji
                             FROM products p JOIN countries c ON c.id=p.country_id
                             WHERE p.id=?""", (pid,)).fetchone()
        if not p or p["status"] != "available":
            await q.edit_message_text("⚠️ ID tersebut sudah terjual atau tidak tersedia.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="home")]]))
            return
        await q.edit_message_text(
            f"📦 DETAIL ID\n\n🌍 Negara: {p['emoji']} {p['country_name']}\n🔢 ID: {p['number']}\n🔢 Digit: {p['digits']}\n💰 Harga: {rupiah(p['price'])}\n\nStatus: 🟢 Tersedia",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Beli Sekarang", callback_data=f"buy:{pid}")],
                [InlineKeyboardButton("🔙 Kembali", callback_data=f"country:{p['country_id']}")]
            ])
        )
        return

    if q.data.startswith("buy:"):
        pid = int(q.data.split(":")[1])
        with db() as c:
            p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            user = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            if not p or p["status"] != "available":
                await q.edit_message_text("⚠️ ID sudah tidak tersedia.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="home")]]))
                return
            if user["balance"] < p["price"]:
                await q.edit_message_text(
                    f"❌ SALDO TIDAK CUKUP\n\nHarga: {rupiah(p['price'])}\nSaldo: {rupiah(user['balance'])}\n\nSilakan Top Up terlebih dahulu.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💰 Top Up", callback_data="topup")],[InlineKeyboardButton("🔙 Kembali", callback_data=f"product:{pid}")]])
                )
                return
        await q.edit_message_text(
            f"🛒 KONFIRMASI PEMBELIAN\n\nID: {p['number']}\nHarga: {rupiah(p['price'])}\nSaldo: {rupiah(user['balance'])}\nSaldo setelah pembelian: {rupiah(user['balance']-p['price'])}\n\nYakin membeli?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Konfirmasi", callback_data=f"confirmbuy:{pid}")],
                [InlineKeyboardButton("❌ Batal", callback_data=f"product:{pid}")]
            ])
        )
        return

    if q.data.startswith("confirmbuy:"):
        pid = int(q.data.split(":")[1])
        with db() as c:
            p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            u = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            if not p or p["status"] != "available":
                await q.edit_message_text("⚠️ ID sudah tidak tersedia.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="home")]])); return
            if u["balance"] < p["price"]:
                await q.edit_message_text("❌ Saldo tidak cukup.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💰 Top Up", callback_data="topup")]])); return
            code = f"ORD{int(datetime.utcnow().timestamp())}{uid % 1000}"
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (p["price"], uid))
            c.execute("UPDATE products SET status='sold' WHERE id=? AND status='available'", (pid,))
            c.execute("INSERT INTO orders(code,user_id,product_id,amount,status,created_at) VALUES(?,?,?,?,?,?)",
                      (code, uid, pid, p["price"], "completed", now()))
        await q.edit_message_text(f"✅ PEMBELIAN BERHASIL\n\nID: {p['number']}\nHarga: {rupiah(p['price'])}\nOrder: #{code}\n\n💰 Saldo tersisa: {rupiah(get_user(uid)['balance'])}\n\nTerima kasih telah berbelanja di 🕰️ NOKTEL LAMA.", reply_markup=main_keyboard())
        await owner_notify(context, f"🛒 PENJUALAN BARU\n\nOrder: #{code}\nID: {p['number']}\nHarga: {rupiah(p['price'])}\nPembeli: @{u['username'] or '-'}\nTelegram ID: {uid}")
        return

    if q.data == "topup":
        amounts = get_setting("topup_amounts", "25000,50000,100000,250000,500000")
        vals = [int(x) for x in amounts.split(",") if x.strip().isdigit() and int(x) > 0]
        kb = []
        for i in range(0, len(vals), 2):
            kb.append([InlineKeyboardButton(rupiah(x), callback_data=f"topupamt:{x}") for x in vals[i:i+2]])
        kb.append([InlineKeyboardButton("✏️ Nominal Lain", callback_data="topupcustom")])
        kb.append([InlineKeyboardButton("🏠 Kembali", callback_data="home")])
        await q.edit_message_text("💰 TOP UP SALDO\n\nPilih nominal:", reply_markup=InlineKeyboardMarkup(kb)); return

    if q.data == "topupcustom":
        context.user_data["awaiting"] = "topup_custom"
        await q.edit_message_text("✏️ Masukkan nominal top up dalam angka.\nContoh: 75000\n\n/Kembali untuk batal"); return

    if q.data.startswith("topupamt:"):
        amount = int(q.data.split(":")[1])
        await create_topup(q, context, amount); return

    if q.data.startswith("confirmtopup:") or q.data.startswith("rejecttopup:"):
        if not is_owner(uid):
            return
        action, tid = q.data.split(":")
        tid = int(tid)
        with db() as c:
            t = c.execute("SELECT * FROM topups WHERE id=?", (tid,)).fetchone()
            if not t or t["status"] != "waiting":
                await q.edit_message_text("⚠️ Transaksi sudah diproses."); return
            if action == "confirmtopup":
                c.execute("UPDATE topups SET status='approved' WHERE id=?", (tid,))
                c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (t["amount"], t["user_id"]))
                msg = f"✅ TOP UP BERHASIL\n\nOrder: #{t['code']}\n+{rupiah(t['amount'])} saldo telah ditambahkan."
            else:
                c.execute("UPDATE topups SET status='rejected' WHERE id=?", (tid,))
                msg = f"❌ PEMBAYARAN DITOLAK\n\nOrder: #{t['code']}"
        await q.edit_message_text("✅ Diproses.")
        try: await context.bot.send_message(t["user_id"], msg, reply_markup=main_keyboard())
        except Exception: pass
        return

    if q.data == "topup_paid":
        tid = context.user_data.get("pending_topup_id")
        if not tid:
            await q.edit_message_text("⚠️ Tidak ada top up yang menunggu."); return
        with db() as c:
            t = c.execute("SELECT * FROM topups WHERE id=? AND user_id=?", (tid, uid)).fetchone()
        if not t or t["status"] != "waiting":
            await q.edit_message_text("⚠️ Transaksi sudah diproses."); return
        context.user_data.pop("pending_topup_id", None)
        await q.edit_message_text("✅ Laporan pembayaran diterima.\n\nAdmin akan memeriksa pembayaran Anda.")
        await owner_notify(context, f"🔔 PEMBAYARAN TOP UP\n\nOrder: #{t['code']}\n👤 Pembeli: @{q.from_user.username or '-'}\n🆔 Telegram ID: {uid}\n💰 Nominal: {rupiah(t['amount'])}", InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Konfirmasi", callback_data=f"confirmtopup:{tid}"),
             InlineKeyboardButton("❌ Tolak", callback_data=f"rejecttopup:{tid}")]
        ]))
        return

    if q.data == "history":
        with db() as c:
            tops = c.execute("SELECT * FROM topups WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
            orders = c.execute("""SELECT o.*, p.number FROM orders o JOIN products p ON p.id=o.product_id
                                  WHERE o.user_id=? ORDER BY o.id DESC LIMIT 10""", (uid,)).fetchall()
        lines = ["📜 RIWAYAT TRANSAKSI", ""]
        for t in tops:
            st = {"approved":"✅ Berhasil","rejected":"❌ Ditolak","waiting":"⏳ Menunggu"}.get(t["status"], t["status"])
            lines.append(f"#{t['code']}\n💰 Top Up {rupiah(t['amount'])}\n{st}\n")
        for o in orders:
            lines.append(f"#{o['code']}\n🛒 Pembelian ID {o['number']}\n💰 -{rupiah(o['amount'])}\n✅ Berhasil\n")
        if len(lines) == 2: lines.append("Belum ada transaksi.")
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Kembali", callback_data="home")]])); return

    if q.data == "help":
        context.user_data["awaiting"] = "support"
        await q.edit_message_text("🆘 BANTUAN\n\nKirim pertanyaan atau masalah Anda. Pesan akan diteruskan kepada Admin.\n\n/Kembali untuk batal."); return

    if q.data == "o_menu":
        if is_owner(uid):
            await q.edit_message_text("🔐 PANEL OWNER", reply_markup=owner_keyboard())
        return

    if q.data.startswith("o_") and not is_owner(uid):
        return

    if q.data == "o_qris":
        await q.edit_message_text("📱 QRIS ADMIN\n\nKirim foto QRIS baru ke chat ini. Bot akan menyimpannya.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel Owner", callback_data="o_menu")]]))
        context.user_data["awaiting"] = "qris"; return

    if q.data == "o_countries":
        with db() as c:
            rows = c.execute("SELECT * FROM countries ORDER BY name").fetchall()
        text = "🌍 KELOLA NEGARA\n\n" + ("\n".join(f"{r['emoji']} {r['name']} — {'AKTIF' if r['active'] else 'NONAKTIF'}" for r in rows) if rows else "Belum ada negara.")
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Tambah Negara", callback_data="o_add_country")],
            [InlineKeyboardButton("🗑️ Hapus Negara", callback_data="o_del_country")],
            [InlineKeyboardButton("🔙 Panel Owner", callback_data="o_menu")]
        ])); return

    if q.data == "o_add_country":
        context.user_data["awaiting"] = "country_add"
        await q.edit_message_text("➕ Kirim negara dengan format:\n`Indonesia|🇮🇩`\n\n/Kembali untuk batal"); return

    if q.data == "o_del_country":
        with db() as c: rows = c.execute("SELECT * FROM countries ORDER BY name").fetchall()
        if not rows:
            await q.edit_message_text("Belum ada negara.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel", callback_data="o_menu")]])); return
        kb = [[InlineKeyboardButton(f"🗑️ {r['emoji']} {r['name']}", callback_data=f"delcountry:{r['id']}")] for r in rows]
        kb.append([InlineKeyboardButton("🔙 Panel", callback_data="o_menu")])
        await q.edit_message_text("Pilih negara yang akan dihapus:", reply_markup=InlineKeyboardMarkup(kb)); return

    if q.data.startswith("delcountry:"):
        cid = int(q.data.split(":")[1])
        with db() as c:
            count = c.execute("SELECT COUNT(*) n FROM products WHERE country_id=?", (cid,)).fetchone()["n"]
            if count:
                await q.answer("Hapus ID produk negara ini terlebih dahulu.", show_alert=True); return
            c.execute("DELETE FROM countries WHERE id=?", (cid,))
        await q.edit_message_text("✅ Negara dihapus.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel Owner", callback_data="o_menu")]])); return

    if q.data == "o_products":
        await q.edit_message_text("📦 KELOLA ID", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Tambah ID", callback_data="o_add_product")],
            [InlineKeyboardButton("📋 Daftar ID", callback_data="o_list_products")],
            [InlineKeyboardButton("🗑️ Hapus ID", callback_data="o_del_product")],
            [InlineKeyboardButton("🔙 Panel Owner", callback_data="o_menu")]
        ])); return

    if q.data == "o_add_product":
        context.user_data["awaiting"] = "product_add"
        await q.edit_message_text("➕ Kirim ID dengan format:\n`123456|Indonesia|50000`\n\nID harus berupa angka. Jumlah digit dihitung otomatis."); return

    if q.data == "o_list_products":
        with db() as c:
            rows = c.execute("""SELECT p.*, c.name country_name, c.emoji FROM products p
                                JOIN countries c ON c.id=p.country_id ORDER BY c.name,p.digits,p.price LIMIT 100""").fetchall()
        if not rows: text = "📦 Belum ada ID."
        else:
            text = "📦 DAFTAR ID\n\n" + "\n".join(f"{r['emoji']} {r['number']} ({r['digits']} digit) — {rupiah(r['price'])} — {r['status']}" for r in rows)
        await q.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel", callback_data="o_products")]])); return

    if q.data == "o_del_product":
        context.user_data["awaiting"] = "product_delete"
        await q.edit_message_text("🗑️ Kirim ID angka yang ingin dihapus.\n\n/Kembali untuk batal"); return

    if q.data == "o_topups":
        await q.edit_message_text(f"💰 NOMINAL TOP UP\n\nSaat ini: {get_setting('topup_amounts','25000,50000,100000,250000,500000')}\n\nKirim nominal dipisahkan koma.\nContoh: 25000,50000,100000", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel", callback_data="o_menu")]]))
        context.user_data["awaiting"] = "topup_settings"; return

    if q.data == "o_orders":
        with db() as c:
            rows = c.execute("""SELECT o.*, p.number, u.username FROM orders o
                                JOIN products p ON p.id=o.product_id JOIN users u ON u.user_id=o.user_id
                                ORDER BY o.id DESC LIMIT 30""").fetchall()
        text = "🧾 PESANAN\n\n" + ("\n".join(f"#{r['code']} | {r['number']} | {rupiah(r['amount'])} | @{r['username'] or '-'}" for r in rows) if rows else "Belum ada pesanan.")
        await q.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel", callback_data="o_menu")]])); return

    if q.data == "o_users":
        with db() as c:
            n = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        await q.edit_message_text(f"👥 PENGGUNA\n\nTotal pengguna: {n}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel", callback_data="o_menu")]])); return

    if q.data == "o_stats":
        with db() as c:
            total = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
            available = c.execute("SELECT COUNT(*) n FROM products WHERE status='available'").fetchone()["n"]
            sold = c.execute("SELECT COUNT(*) n FROM products WHERE status='sold'").fetchone()["n"]
            users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            sales = c.execute("SELECT COALESCE(SUM(amount),0) n FROM orders WHERE status='completed'").fetchone()["n"]
            topups = c.execute("SELECT COALESCE(SUM(amount),0) n FROM topups WHERE status='approved'").fetchone()["n"]
        await q.edit_message_text(f"📊 STATISTIK\n\n📦 Total ID: {total}\n🟢 Tersedia: {available}\n🔴 Terjual: {sold}\n👥 Pengguna: {users}\n💰 Penjualan: {rupiah(sales)}\n💳 Top Up: {rupiah(topups)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel", callback_data="o_menu")]])); return

    if q.data == "o_broadcast":
        context.user_data["awaiting"] = "broadcast"
        await q.edit_message_text("📢 Kirim teks pengumuman yang ingin dikirim ke semua pengguna.\n\n/Kembali untuk batal."); return

    if q.data == "o_support":
        with db() as c:
            rows = c.execute("SELECT * FROM support_messages WHERE status='open' ORDER BY id DESC LIMIT 20").fetchall()
        text = "🆘 PESAN BANTUAN\n\n" + ("\n".join(f"#{r['id']} | User {r['user_id']}\n{r['message'][:150]}\n" for r in rows) if rows else "Tidak ada pesan terbuka.")
        await q.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel", callback_data="o_menu")]])); return

async def create_topup(q, context, amount):
    uid = q.from_user.id
    with db() as c:
        code = f"TOPUP{int(datetime.utcnow().timestamp())}{uid % 1000}"
        cur = c.execute("INSERT INTO topups(code,user_id,amount,status,created_at) VALUES(?,?,?,?,?)",
                        (code, uid, amount, "waiting", now()))
        tid = cur.lastrowid
    context.user_data["pending_topup_id"] = tid
    qris = get_setting("qris_file_id")
    caption = f"💳 PEMBAYARAN TOP UP\n\nOrder: #{code}\nNominal: {rupiah(amount)}\n\nSilakan bayar melalui QRIS Admin. Setelah selesai, tekan tombol di bawah."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Saya Sudah Bayar", callback_data="topup_paid")],
        [InlineKeyboardButton("❌ Batal", callback_data="home")]
    ])
    if qris:
        try:
            await q.message.reply_photo(qris, caption=caption, reply_markup=kb)
            await q.message.delete()
            return
        except Exception:
            pass
    await q.edit_message_text(caption + "\n\n⚠️ QRIS belum disetel Admin.", reply_markup=kb)

async def owner_notify(context, text, reply_markup=None):
    if OWNER_ID:
        await context.bot.send_message(OWNER_ID, text, reply_markup=reply_markup)

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    row = get_user(u.id, u.username)

    if context.user_data.get("awaiting") == "qris":
        if not is_owner(u.id) or not update.message.photo:
            return
        set_setting("qris_file_id", update.message.photo[-1].file_id)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ QRIS berhasil disimpan.", reply_markup=owner_keyboard()); return

    awaiting = context.user_data.get("awaiting")

    if awaiting == "topup_custom":
        try:
            amount = int(update.message.text.replace(".", "").replace(",", ""))
            if amount < 1000: raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Nominal tidak valid. Masukkan angka, contoh 75000."); return
        context.user_data.pop("awaiting", None)
        # reuse a message-like path
        class Q:
            from_user = u
            message = update.message
        await create_topup(Q(), context, amount); return

    if awaiting == "support":
        if not row["accepted_terms"]: return
        msg = update.message.text.strip()
        if not msg: return
        with db() as c:
            cur = c.execute("INSERT INTO support_messages(user_id,message,created_at) VALUES(?,?,?)", (u.id,msg,now()))
            sid = cur.lastrowid
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Pesan Anda telah dikirim kepada Admin.", reply_markup=main_keyboard())
        await owner_notify(context, f"📩 PESAN BANTUAN #{sid}\n\n👤 @{u.username or '-'}\n🆔 {u.id}\n\n💬 {msg}")
        return

    if not is_owner(u.id):
        return

    if awaiting == "country_add":
        try:
            name, emoji = [x.strip() for x in update.message.text.split("|",1)]
            if not name: raise ValueError
            with db() as c: c.execute("INSERT INTO countries(name,emoji) VALUES(?,?)", (name,emoji or "🌍"))
            await update.message.reply_text("✅ Negara ditambahkan.", reply_markup=owner_keyboard())
        except Exception:
            await update.message.reply_text("❌ Format salah. Contoh: Indonesia|🇮🇩")
        context.user_data.pop("awaiting", None); return

    if awaiting == "product_add":
        try:
            number, country_name, price = [x.strip() for x in update.message.text.split("|",2)]
            if not number.isdigit() or int(price) <= 0: raise ValueError
            with db() as c:
                country = c.execute("SELECT * FROM countries WHERE name=?", (country_name,)).fetchone()
                if not country: raise ValueError("Negara belum ada")
                c.execute("""INSERT INTO products(number,country_id,digits,price,status,created_at)
                             VALUES(?,?,?,?,?,?)""",
                          (number,country["id"],len(number),int(price),"available",now()))
            await update.message.reply_text(f"✅ ID berhasil ditambahkan.\n\nID: {number}\nDigit: {len(number)}\nNegara: {country['emoji']} {country['name']}\nHarga: {rupiah(int(price))}", reply_markup=owner_keyboard())
        except ValueError as e:
            await update.message.reply_text(f"❌ Gagal: {e}")
        except Exception:
            await update.message.reply_text("❌ ID mungkin sudah ada atau format salah.\nContoh: 123456|Indonesia|50000")
        context.user_data.pop("awaiting", None); return

    if awaiting == "product_delete":
        number = update.message.text.strip()
        with db() as c:
            cur = c.execute("DELETE FROM products WHERE number=? AND status!='sold'", (number,))
        await update.message.reply_text("✅ ID dihapus." if cur.rowcount else "⚠️ ID tidak ditemukan atau sudah terjual.", reply_markup=owner_keyboard())
        context.user_data.pop("awaiting", None); return

    if awaiting == "topup_settings":
        try:
            vals = [int(x.strip()) for x in update.message.text.split(",")]
            if not vals or any(v <= 0 for v in vals): raise ValueError
            set_setting("topup_amounts", ",".join(map(str,vals)))
            await update.message.reply_text("✅ Nominal Top Up diperbarui.", reply_markup=owner_keyboard())
            context.user_data.pop("awaiting", None)
        except Exception:
            await update.message.reply_text("❌ Format salah. Contoh: 25000,50000,100000")
        return

    if awaiting == "broadcast":
        text = update.message.text.strip()
        with db() as c: users = c.execute("SELECT user_id FROM users WHERE accepted_terms=1").fetchall()
        sent = 0
        for x in users:
            try:
                await context.bot.send_message(x["user_id"], f"📢 PENGUMUMAN NOKTEL LAMA\n\n{text}")
                sent += 1
            except Exception:
                pass
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(f"✅ Pengumuman dikirim ke {sent} pengguna.", reply_markup=owner_keyboard())
        return

async def owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text("🔐 PANEL OWNER\n\nHanya Anda yang dapat mengakses panel ini.", reply_markup=owner_keyboard())

async def cancel(update, context):
    context.user_data.clear()
    if update.callback_query:
        await show_home(update, context)
    else:
        await update.message.reply_text("Dibatalkan.", reply_markup=main_keyboard())

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN belum diatur.")
    if not OWNER_ID:
        raise RuntimeError("OWNER_ID belum diatur.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("owner", owner_command))
    app.add_handler(CommandHandler("kembali", cancel))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.add_handler(MessageHandler(filters.PHOTO, text_message))
    logger.info("NOKTEL LAMA bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
