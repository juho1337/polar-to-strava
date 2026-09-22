# Privacy and security

## Sensitive data

A Polar user-data export and the generated migration workspace can contain or reveal:

- GPS routes and frequently visited places
- exact workout timestamps
- heart rate and other sensor measurements
- training history and sport habits
- device information
- profile or account-related information elsewhere in the Polar export

Treat the following as private:

- the original Polar archive and extracted export
- generated FIT files
- migration audit reports and manifests
- migration configuration files
- `.strava-tokens.json`
- `migration-state.sqlite3`
- Strava Client Secret, authorization codes, access tokens, and refresh tokens

## Where processing occurs

Discovery, parsing, validation, FIT generation, decoding, audit generation, dry-run
selection, and status reporting occur locally. A real network upload begins only when you
run `strava upload` without `--dry-run`. OAuth commands communicate with Strava to obtain
and refresh tokens.

## Storage guidance

- Put real exports and workspaces outside the repository checkout.
- Restrict access using your operating system's file permissions and disk encryption.
- Do not sync the data to a public or shared cloud folder unintentionally.
- Do not attach exports, FIT files, tokens, manifests, or state databases to public issues.
- Do not delete `migration-state.sqlite3` during a migration.
- Remove local data only after you have verified the migration and retained whatever
  private backup you need.

The repository `.gitignore` covers common workspace reports, manifests, FIT files,
SQLite databases, token files, local environment files, and common export directory
names. Git ignore rules are a last line of defense; they do not protect files stored
elsewhere or files already committed.

For a suspected vulnerability or accidental secret exposure, follow
[SECURITY.md](../SECURITY.md). Revoke exposed Strava credentials or tokens promptly from
Strava rather than posting them for diagnosis.
