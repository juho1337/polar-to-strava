# Prepare a Polar export

Follow Polar's official guide to
[download all your data from Polar Flow](https://support.polar.com/us-en/how-to-download-all-your-data-from-polar-flow).
Polar prepares the data asynchronously and provides a downloadable archive.

1. Download the archive to a private location.
2. Extract the archive. The current scanner accepts a directory, not a ZIP file.
3. Keep the extracted directory private and unchanged while auditing it.
4. Pass its root directory to `audit`.

```powershell
python main.py audit "C:\path\to\polar-export" --output "C:\path\to\migration-workspace"
```

The scanner searches recursively for JSON filenames beginning with
`training-session-`. It excludes daily `activity-*.json` records from workout discovery.
Other account-export files can stay in place; users do not need to select individual
training-session files.

The original export is never modified. Audit and conversion write only to the output path
you provide. Keep that output separate from the extracted source so it cannot be
rediscovered as input and is easier to protect or remove later.
