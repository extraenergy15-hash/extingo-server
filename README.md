# Extingo Server

Backend server for the Extingo fire-detection system (`http.server`-based,
no Flask). Handles telemetry ingestion, fire alerts, remote commands, and
Telegram/CallMeBot notifications.

## 1. Install dependencies

```bash
pip install -r requirements.txt
```

## 2. Configure environment variables

Secrets and deployment-specific values are no longer hardcoded in
`server.py` — they're read from environment variables at startup.

1. Copy the example file:

   ```bash
   cp .env.example .env
   ```

2. Fill in your real values in `.env`:
   - `TELEGRAM_BOT_TOKEN` — your bot's token from @BotFather
   - `TELEGRAM_CHAT_ID` — the chat ID alerts should be sent to
   - `NGROK_URL` — the public URL from step 4 below (leave a placeholder for now)
   - `CALLMEBOT_TELEGRAM_USERNAME` — your Telegram username registered with CallMeBot

3. Load `.env` into your shell before running the server:

   ```bash
   export $(grep -v '^#' .env | xargs)
   ```

   (Any variable left unset falls back to a safe placeholder and the
   corresponding feature — Telegram, webhook registration, or CallMeBot —
   is skipped with a console warning, so the server still runs without
   full configuration.)

`.env` is your local secrets file and should **not** be committed to
version control — only `.env.example` should be.

## 3. Run the server

```bash
python3 server.py
```

By default it listens on `0.0.0.0:5000`.

## 4. Expose it with ngrok

In a **separate terminal**, start ngrok pointing at the same port:

```bash
ngrok http 5000
```

Copy the `https://...ngrok-free.dev` (or `.app`) URL that ngrok prints.

- Set this as `NGROK_URL` in your `.env` (used to register the Telegram
  webhook at startup) and re-export it / restart the server so the
  webhook picks it up.
- Paste this same URL into the `SERVER_URL` constant in **both** Site A's
  and Site B's `api.js`, so the frontends know where to send telemetry,
  alerts, and commands, and where to poll from.

## Notes

- If `opencv-python` fails to import, webcam capture is disabled and the
  server falls back to text-only Telegram alerts.
- If `requests` fails to import, all Telegram/CallMeBot integrations are
  disabled.
- Restart the server whenever you change `.env` — values are read once
  at startup.
