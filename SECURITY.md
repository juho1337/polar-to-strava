# Security policy

This document covers vulnerability reporting and the security rules contributors and
reviewers follow. It does not promise a particular response time or support period.

## Reporting a security issue

Do not publish secrets, tokens, private activity files, GPS routes, or other personal data
in a GitHub issue. Use a private GitHub security advisory for this repository when that
feature is available, or contact the repository owner through a private channel listed on
their GitHub profile.

Include the smallest sanitized reproduction possible. Describe the affected version,
impact, and steps without attaching a real Polar export or migration workspace.

## Exposed credentials or data

If a Strava Client Secret, authorization code, access token, or refresh token is exposed,
revoke or rotate it through Strava immediately. Removing a secret from the latest commit
does not remove it from Git history. Treat accidentally published Polar exports, FIT
files, manifests, reports, and SQLite state as personal-data exposure and remove access at
the hosting or storage provider.

## Credentials

- Never commit OAuth tokens, API secrets, passwords or refresh tokens.
- Keep credentials in the approved secure storage for the component using them, never in
  source, fixtures, reports or the migration workspace.
- Redact sensitive data from logs, reports and diagnostic exports.
- Do not print credentials in error messages or command lines.

## Exercise data

- Real Polar and Strava exports and activity histories are private user data. Do not
  commit them; use synthetic fixtures in automated tests.
- Avoid destructive changes to real training history. Work on copies or generated
  workspaces.

## Migration safety

- Never automatically delete Strava activities.
- Never silently upload unresolved possible duplicates.
- Do not blindly retry an upload whose outcome is uncertain. Persist upload state and
  reconcile uncertain results before retrying.
- Respect Strava API rate limits.

## Dependencies and build

- Avoid unnecessary dependencies, prefer maintained libraries and check licence
  compatibility (GPL-3.0-only).
- Review changes to dependency, build and packaging configuration.
- Keep build artifacts (`dist/`, packaged executables) and credentials out of source control.

## Areas needing extra review

Authentication and OAuth, credential storage, process execution and the JSON Lines
boundary to the desktop host (input validation), file and archive processing, path
traversal and malformed files, duplicate detection, retry safety, and data loss or
corruption risks. See the review guidelines in [AGENTS.md](AGENTS.md).
