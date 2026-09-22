# Strava API and OAuth setup

The uploader currently requires each user to register a Strava API application. Strava's
official [getting-started guide](https://developers.strava.com/docs/getting-started/)
explains the application dashboard, and its
[authentication guide](https://developers.strava.com/docs/authentication/) defines the
OAuth flow used here.

## Create the application

1. Sign in to Strava and open [My API Application](https://www.strava.com/settings/api).
2. Create an application using a truthful, non-official name and description. It does not
   have to be named after this project.
3. Set **Authorization Callback Domain** to `localhost` for this local flow.
4. Copy the displayed Client ID and Client Secret for the next step.

Strava currently states that creating an API application requires a Strava subscription.
Application fields and account requirements can change, so prefer Strava's current page
over screenshots or copied UI instructions.

## Set credentials

Set credentials only in the shell used to run the command.

Windows PowerShell:

```powershell
$env:STRAVA_CLIENT_ID="YOUR_CLIENT_ID"
$env:STRAVA_CLIENT_SECRET="YOUR_CLIENT_SECRET"
```

macOS or Linux:

```bash
export STRAVA_CLIENT_ID="YOUR_CLIENT_ID"
export STRAVA_CLIENT_SECRET="YOUR_CLIENT_SECRET"
```

Never commit the Client Secret. Never publish a client secret, authorization code, access
token, or refresh token in an issue, log, screenshot, or support message.

## Authorize a workspace

```powershell
python main.py strava auth "C:\path\to\migration-workspace"
```

The command:

1. Prints a Strava authorization URL requesting `activity:write`.
2. Waits while you open that URL and approve access.
3. Relies on Strava redirecting the browser to `http://localhost`.
4. Prompts separately for the returned authorization `code` and granted `scope`.

The tool does not run a callback web server. A browser message such as “connection
refused” or “site can't be reached” at localhost is therefore expected. Stay on that
page, inspect its address, copy only the value after `code=`, and enter it at the hidden
authorization-code prompt. Then copy the `scope` query value, normally
`activity:write`, into the scope prompt. Do not paste the full callback URL.

The code is short-lived and usable once. If authorization fails, start `strava auth`
again to obtain a new URL and code.

Successful authorization stores a minimal token set in `.strava-tokens.json` inside the
migration workspace. The uploader refreshes expiring access tokens and atomically stores
the newest rotating refresh token. Athlete profile data is not persisted.

Proceed with a local status check and dry run as described in the
[README](../README.md#dry-run-and-upload).
