# Security and data handling

## What is checked
- **Network:** the server listens on `127.0.0.1` only. ComfyUI settings reject any address that is not loopback.
- **Files from outside:** imported zips are checked for path traversal, absolute paths, size and file count, and each asset's checksum. Images are identified by their bytes and decoded before they are accepted. SVG is refused.
- **Paths from the browser:** never accepted for files. Asset files are served only from inside the project folder, and ids are checked against a strict pattern.
- **Writes:** project and job files are written atomically, with a backup of the previous copy. Model files are never overwritten, moved or renamed by Open Workflow.
- **Deletion:** removing a project needs its exact title. Removing managed model files needs the model id typed. External model files are never removed.
- **Secrets:** the log filter redacts values next to words such as token, password, secret and api key. The diagnostics bundle applies the same filter.

## What leaves the machine
- **Model downloads:** only after you confirm a plan, and only from verified sources. No optional model is downloaded automatically.
- **Generation:** prompts and images go to the ComfyUI server you configured, on loopback. Open Workflow sends nothing else.
- **Nothing else:** there is no telemetry and no update check. Diagnostics are a file you save and send yourself.

## Known limits
- No authentication. Anyone who can reach the port can use the app. Keep it on loopback.
- The GPU job engine runs only on the same machine. There is no remote worker.
- Image parsing uses Pillow. Keep it updated.
- Custom-node packs run inside ComfyUI with its permissions. Open Workflow cannot vet them.

## Reporting a problem
Use Settings → Diagnostics, review the zip, and attach it to your report. Remove anything you do not want to share first.
