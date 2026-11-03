# LAX LIVE BOT — YouTube livestream chat assistant

A lightweight Flask dashboard for **LAX ALFA EFX** and **LAX ESPORTS OFFICIAL**. Connect each YouTube channel with its own Google account. The bot can greet a viewer when it first sees that viewer chat during the current bot session, answer `!help` and custom commands, and post optional scheduled reminders.

> This is a community chat assistant, not an official YouTube product. It uses the YouTube Data API v3 and is subject to Google's API quota, OAuth verification/testing rules, and YouTube policies.

## Features

- Connect multiple YouTube channels, including channels owned by separate Google accounts.
- Separate settings for each connected channel.
- Welcome reply on a viewer's first observed chat message during a bot session.
- Built-in `!help` and custom commands such as `!discord` and `!yt`.
- Optional recurring reminder messages (5–180 minutes).
- Mobile-friendly dark LAX dashboard.
- OAuth tokens encrypted in SQLite using Fernet.
- No API keys, client secrets, or OAuth tokens are included in this repository.

## Important limitations

1. YouTube's live chat API does **not** provide a general event that reliably tells this bot that a person merely opened/joined the stream. The welcome feature therefore greets a viewer when the bot first observes a message from them in live chat.
2. The bot must remain running on a server while the stream is live. Your Android phone is used to open the dashboard; closing the browser does not stop a properly hosted service.
3. A free hosting plan may sleep, restart, or change its limits. If the service sleeps, the bot cannot reply until it wakes. Persistent storage may require a paid disk depending on the host's current plan.
4. The current bot checks the first active broadcast returned for each connected channel. Start the bot only when that channel's live chat is enabled.
5. The bot uses a single web process and one worker per connected channel. Keep a modest number of channel connections and monitor YouTube API quota.
6. Do not use this bot to spam chat. Keep reminders infrequent and follow YouTube's policies.

## 1. Create the Google API project

Do this for the Google account that will own/manage the bot OAuth consent setup:

1. Open Google Cloud Console: https://console.cloud.google.com/
2. Create a project, for example `LAX LIVE BOT`.
3. Open **APIs & Services → Library**, find **YouTube Data API v3**, and enable it.
4. Open **APIs & Services → OAuth consent screen**. Configure the app name and contact details. If the app is in Testing mode, add the Google accounts that will connect the YouTube channels as test users.
5. Open **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
6. Choose **Web application**.
7. Add this Authorized redirect URI for local testing:
   `http://127.0.0.1:8080/oauth/callback`
8. Download the OAuth client JSON file and rename it to `client_secret.json`. Keep it private.

Google documentation:
- YouTube Data API: https://developers.google.com/youtube/v3
- Live chat messages: https://developers.google.com/youtube/v3/live/docs/liveChatMessages
- OAuth 2.0 web server flow: https://developers.google.com/identity/protocols/oauth2/web-server

The OAuth scope requested is `youtube.force-ssl`, which allows the app to read and send live chat messages for the channel that authorizes it. Review the consent screen and Google's current requirements before publishing to other users.

## 2. Generate secure secrets

Install Python 3.11+ and dependencies. Generate a Fernet encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Generate a Flask secret (any long random value is suitable):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy `.env.example` to `.env` and fill in:
- `FLASK_SECRET_KEY`
- `ADMIN_PASSWORD`: a unique, long password for the private dashboard
- `TOKEN_ENCRYPTION_KEY`
- `GOOGLE_CLIENT_SECRETS_FILE=client_secret.json`
- `OAUTH_REDIRECT_URI=http://127.0.0.1:8080/oauth/callback`
- `COOKIE_SECURE=false` for local HTTP only

Never put `.env`, `client_secret.json`, OAuth tokens, or real secrets into GitHub. The `.gitignore` already excludes them.

## 3. Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:8080` in a browser. For Android-only use, local hosting is not ideal because Android may stop background processes. Deploy the app to a server and open its HTTPS dashboard from your phone.

## 4. Deploy with Render (phone-friendly)

1. Push this project to your GitHub repository `laxalfa/LAX-LIVE-BOT`.
2. In Render, create a **New → Blueprint** and select that repository. Render reads `render.yaml`.
3. In the Render service's Environment settings, add:
   - `ADMIN_PASSWORD`: a unique, long password for the private dashboard
- `TOKEN_ENCRYPTION_KEY`: your generated Fernet key
   - `GOOGLE_CLIENT_SECRETS_JSON`: paste the **entire contents** of your downloaded OAuth client JSON file as one JSON value
   - `OAUTH_REDIRECT_URI`: `https://YOUR-RENDER-SERVICE.onrender.com/oauth/callback`
4. In Google Cloud Console, edit the OAuth client and add the same HTTPS callback URL to **Authorized redirect URIs**.
5. Deploy. Open the Render HTTPS service URL on your Android phone.

If you use a different host, set the same environment variables. Set `COOKIE_SECURE=true` when using HTTPS. Ensure the database path points to persistent storage if you need channel connections to survive host restarts/redeploys. Back up the database securely.

**Free-tier warning:** host pricing, sleep behaviour, persistent disk availability, and limits change. Check the host's current plan before relying on it for a live event.

## 5. Connect both channels

1. Open the dashboard.
2. Enter `LAX ALFA EFX` as the label and tap **Connect Google account**.
3. Sign in with the Google account that owns/manages that YouTube channel. Review and approve the requested permissions.
4. Return to the dashboard.
5. Repeat using label `LAX ESPORTS OFFICIAL`, signing in with the second Google account.
6. Open **Settings** on each channel to configure messages and commands separately.

If both channels belong to one Google account, the channel selected by the authorization response may be the account's primary channel. For Brand Accounts or multiple channels under one Google login, confirm which channel the OAuth connection selected before going live.

## 6. Configure commands

In a channel's Settings page, enter valid JSON. Example:

```json
{
  "!discord": "Join our Discord community: YOUR_INVITE_LINK",
  "!yt": "Subscribe to LAX ESPORTS OFFICIAL!",
  "!rules": "Be respectful. No spam, abuse, or advertising."
}
```

Keys must start with `!`. The command is matched when it is the first word in the chat message. `!help` is built in.

## 7. Start a livestream bot

1. Start a YouTube livestream with Live Chat enabled.
2. Open the LAX LIVE BOT dashboard.
3. Tap **Start bot** on the matching channel.
4. Check the status text. It should say `Connected to live chat`.
5. Test `!help` from another viewer account. Do not test by sending commands from the channel owner account.

## Troubleshooting

- **OAuth redirect_uri_mismatch:** The redirect URI in Google Cloud must exactly match `OAUTH_REDIRECT_URI`, including HTTPS and path.
- **Access blocked / app not verified:** During development, add the connecting Google account as an OAuth test user. Google may require verification for public distribution or broader use.
- **No active livestream:** Confirm the correct channel is connected, the broadcast is actually live, and Live Chat is enabled.
- **403 or quota errors:** Check that YouTube Data API v3 is enabled, the account granted access, and the project has quota available. Avoid very frequent polling.
- **Bot stops after deploy/restart:** The worker is in-process and must be started again after a restart. Use a host that keeps the web service alive. For production use, move the worker to a dedicated background worker service and configure persistent storage.
- **Channel connected but wrong channel title:** Remove the connection and reconnect with the correct Google account/channel selection.

## Security checklist

- Set a strong, unique `ADMIN_PASSWORD` before deploying. The dashboard login protects channel management routes.
- Keep Google OAuth client JSON and all generated secrets private.
- Never commit `.env`, `client_secret.json`, database files, or access tokens.
- Use HTTPS in deployment and set `COOKIE_SECURE=true`.
- Do not share the dashboard URL if it is publicly accessible. Add authentication/access protection before inviting other people to use it.
- Rotate secrets if they are exposed. Removing a channel connection deletes its saved encrypted token from the local database.

## Project structure

```text
LAX-LIVE-BOT/
├── app.py
├── requirements.txt
├── Procfile
├── render.yaml
├── Dockerfile
├── .env.example
├── .gitignore
├── README.md
├── templates/
│   ├── login.html
│   ├── index.html
│   └── settings.html
└── static/
    ├── app.js
    └── style.css
```
