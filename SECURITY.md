# Security policy

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

This file provides reporting guidance and does not promise a particular response time or
support period.
