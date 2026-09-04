# NOKTEL LAMA

Telegram marketplace bot untuk penjualan ID angka yang sah dimiliki/berhak dijual.

## Railway Variables
- BOT_TOKEN = token bot Telegram
- OWNER_ID = Telegram numeric user ID Owner
- DB_PATH = /data/noktel_lama.db (disarankan jika memakai Railway Volume)

## Deploy
1. Upload project ke GitHub.
2. Connect repository ke Railway.
3. Set variables di atas.
4. Jika memakai Railway Volume, mount ke `/data` dan set `DB_PATH=/data/noktel_lama.db`.
5. Deploy.

QRIS tidak disimpan di GitHub. Owner meng-upload QRIS langsung melalui panel bot.
