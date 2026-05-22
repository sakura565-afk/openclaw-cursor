# Tools and credentials (local reference)

**Do not commit real secrets.** Keep tokens in your environment or a private copy of this file.

## Yandex Metrika daily report (`scripts/metrika_daily.py`)

Set before running:

| Variable | Purpose |
|----------|---------|
| `YANDEX_METRIKA_OAUTH_TOKEN` | OAuth token for `api-metrika.yandex.net` (Reporting API). |
| `TELEGRAM_BOT_TOKEN` | Bot token for Telegram (when sending). |
| `TELEGRAM_CHAT_ID` | Destination chat id (when using built-in sender). |

Optional:

| Variable | Purpose |
|----------|---------|
| `METRIKA_HTTP_PROXY` | HTTP(S) proxy for Metrika and Telegram (default `http://127.0.0.1:3067`). |
| `TELEGRAM_MEDIA_SEND_SCRIPT` | Path to `telegram_media_send_v2.py` if you use the OpenClaw skill instead of `scripts/telegram_sender.py`. |

Counters are fixed in the script: `94834593` (amadey.ru), `63403` (divaninfo.ru).
