# Gmail, X, GitHub, and browser setup

Curie uses scoped tokens and OAuth. Do not put Gmail, X, or GitHub account
passwords in chat, source code, `ecosystem.config.js`, or Curie's memory.

## Secure the local environment

The `.env` file and encrypted vault must only be readable by the service user:

```bash
chmod 600 .env
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
openssl rand -hex 32
```

Put the first generated value in `CURIE_CREDENTIAL_KEY` and the second in
`CURIE_SETUP_TOKEN`. Keep both in `.env`. OAuth access and refresh tokens are
then stored encrypted in `.curie_credentials.enc`, which is gitignored.

## Gmail

1. In Google Cloud Console, create a project and enable the Gmail API.
2. Configure the OAuth consent screen and add `curieassistant@gmail.com` as a
   test user if the app is in testing mode.
3. Create a Web application OAuth client.
4. Register the exact callback URL
   `https://YOUR_CURIE_HOST/oauth/google/callback`.
5. Set:

```dotenv
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=https://YOUR_CURIE_HOST/oauth/google/callback
```

After restarting Curie, request the one-time consent URL from the server:

```bash
curl -H "X-Curie-Setup-Token: $CURIE_SETUP_TOKEN" \
  "https://YOUR_CURIE_HOST/oauth/google/start?owner_id=$MASTER_USER_ID&product=gmail&write=true"
```

Open the returned `authorization_url`, sign into Curie's Gmail account, and
approve the requested read and send scopes. Curie never receives the password.

For a server without a public HTTPS hostname, an SSH tunnel can expose Curie's
loopback API only to your workstation:

```bash
ssh -L 8000:127.0.0.1:8000 YOUR_SERVER_USER@YOUR_SERVER
```

Register the exact `http://127.0.0.1:8000/oauth/.../callback` URI in the provider
console, use that value in `.env`, and open the consent URL on the workstation
while the tunnel is active. Provider console rules determine whether a loopback
HTTP callback is accepted; otherwise use an HTTPS reverse proxy.

Chat commands:

```text
/gmail search is:unread newer_than:7d
/gmail read MESSAGE_ID
/gmail send person@example.com | Subject | Message body
```

Sending produces a preview and a single-use `/approve action TOKEN` command.

## X

Create an X developer Project and App, enable OAuth 2.0 Authorization Code with
PKCE, and register `https://YOUR_CURIE_HOST/oauth/x/callback`. The app requests
`tweet.read`, `tweet.write`, `users.read`, `dm.read`, `dm.write`, and
`offline.access`. Availability and billing still depend on the X developer tier.

```dotenv
X_OAUTH_CLIENT_ID=...
X_OAUTH_CLIENT_SECRET=...
X_OAUTH_REDIRECT_URI=https://YOUR_CURIE_HOST/oauth/x/callback
```

Request and open the consent URL:

```bash
curl -H "X-Curie-Setup-Token: $CURIE_SETUP_TOKEN" \
  "https://YOUR_CURIE_HOST/oauth/x/start?owner_id=$MASTER_USER_ID"
```

Chat commands:

```text
/x search QUERY
/x read POST_ID
/x post POST TEXT
/x reply POST_ID | REPLY TEXT
/x dm read
/x dm send PARTICIPANT_USER_ID | MESSAGE
```

Posts, replies, and DMs always require a fresh approval.

## GitHub

Create a fine-grained personal access token restricted to Curie's repositories.
Start with Contents read/write, Pull requests read/write, and Metadata read.
Do not grant repository administration unless a later feature genuinely needs it.

```dotenv
GITHUB_TOKEN=github_pat_...
MAIN_REPO=https://github.com/OWNER/REPOSITORY
MAIN_REVIEWER=YOUR_USERNAME
TARGET_BRANCH=main
```

## Headless browser on Ubuntu

Install the optional browser package and Chromium runtime:

```bash
.venv/bin/pip install -r requirements-browser.txt
.venv/bin/playwright install chromium
```

On a minimal Ubuntu image, an administrator may also need to install Playwright's
documented OS packages. Do not run Chromium with `--no-sandbox`.

Chat commands:

```text
/browser open https://example.com
/browser snapshot
/browser click Exact visible button text
/browser fill Exact field label | Value
/browser close
```

Clicks and fills require fresh approval. Password, passcode, token, OTP, and 2FA
fields are blocked. Owner browser cookies are kept in a private `0600` storage
file. Gmail and X should use their official OAuth/API connectors, not browser
automation.
