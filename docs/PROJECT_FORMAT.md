# Project format (schema v1)

A project is a folder. Everything inside it is relative, so the folder can be moved or copied as a unit.

```
<projects folder>/<project id>/
  project.json        metadata, entities, version history  (schema_version 1)
  project.json.bak    previous saved copy, used if project.json is damaged
  assets.json         asset registry (paths, sizes, sha256, labels)
  assets/images/      imported images, named by asset id (never overwritten)
```

The projects folder is set in Settings. The default is `<Open Workflow data>/projects`.

## Guarantees

- **Atomic writes.** Each save writes a temp file, flushes it to disk, and replaces the old file. An interrupted save leaves the previous state.
- **Backup on save.** Before a save replaces `project.json`, the previous copy is kept as `.bak`. A damaged main file is never copied over the backup.
- **Recovery.** If `project.json` cannot be read, the backup is used and the project shows a message saying so. If both are damaged, the project is reported as unreadable and the rest of the app keeps working.
- **Newer formats refused.** A manifest with a higher `schema_version` than this build understands is not opened, so older builds never silently rewrite it.
- **Optimistic concurrency.** Every write sends the revision it last saw. A stale write gets HTTP 409 and nothing changes. The UI shows "Reload latest".
- **Versioning.** An entity gets a new version only when its fields or references actually change. Old versions are never edited. Restoring a version creates a new version.
- **Images.** The format is decided from the bytes (PNG, JPEG or WebP, decoded with Pillow), not the filename. Uploads are capped at 25 MB and 60 megapixels. SVG is refused. The original filename is kept as a label only.
- **Integrity.** Each asset's size is checked on every listing. The sha256 is checked on request ("deep verify"). Missing or changed files are shown, not hidden.
- **Deletion.** Deleting a project needs the exact title and removes the project folder. Assets are never deleted by themselves in this milestone.

## Entity fields

Each kind has an explicit allow-list (`backend/open_workflow/projects/schema.py`). Unknown fields are rejected, and each field is limited to 4,000 characters.

- Character: identity fields, plus `identity_lock` (off, balanced or strong). The lock is stored only. No workflow applies it yet.
- World, style and prop: description and kind-specific fields.

Reference roles depend on the kind. For example, a character can have face, three-quarter, profile, full-body, hair, clothing or expression roles.

## Not yet

- Repairing or relinking a missing asset. It is reported only.
- Moving a project between machines with a dedicated export. Copying the folder works, but no export tool exists yet (Milestone 8).
