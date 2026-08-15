# Architecture

PolarToStrava uses a dependency direction that keeps source formats and output
formats out of the business domain.

```mermaid
flowchart TD
    CLI --> Services
    Services --> Ports[Importer / Exporter / Uploader ports]
    Ports --> Integrations[Polar, TCX, Strava implementations]
    Services --> Domain
    Integrations --> Domain
```

## Layers

- **`domain/`** contains immutable `Activity` values and has no knowledge of Polar,
  TCX, Strava, filesystems, or HTTP.
- **Importers** translate a provider format into the domain. `PolarImporter` is the
  current adapter and returns `Activity` values only.
- **`services/`** orchestrates importer ports and validators. `ConversionService`
  scans folders, imports activities, and returns structured results; it never exports,
  writes files, or uploads.
- **Exporters** will build bytes in memory through `ActivityExporter`. TCX is split
  into a `TCXBuilder` and `TCXWriter`, so building is independently testable and a
  future writer can target a filesystem, stream, zip archive, or cloud store.
- **Uploaders** implement `ActivityUploader` for remote destinations such as Strava.

```mermaid
sequenceDiagram
    participant C as CLI
    participant S as ConversionService
    participant I as ActivityImporter
    participant V as Validator
    C->>S: import_folder()
    S->>I: scan() / import_activity()
    I-->>S: Activity
    S->>V: validate(Activity)
    V-->>S: ValidationResult
    S-->>C: ConversionResult
```

## Validation and errors

Validators never throw for malformed activities. They return `ValidationResult`,
containing structured `ValidationIssue` values at warning or error severity. Import,
export, configuration, and upload failures use the `PolarToStravaError` hierarchy,
which lets the CLI present a single user-friendly error boundary.

## Extension points

New sources implement `ActivityImporter`; new in-memory formats implement
`ActivityExporter`; and destinations implement `ActivityUploader`. This makes planned
`TCXExporter`, `FitExporter`, `GpxExporter`, and `StravaUploader` additions independent
of the CLI and domain packages.
