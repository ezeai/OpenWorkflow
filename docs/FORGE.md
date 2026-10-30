# Forge: workflows to apps

Forge is the "Apps" section of Open Workflow. It imports a ComfyUI workflow, works out what it does and what it needs, and (in later milestones) turns it into an app with its own controls. The Director and the rest of Open Workflow are unchanged.


## Milestone 1: import and analysis (done, 2026-10-22)

**Acceptance: met.** The Z-Image Turbo template from ComfyUI's installed template package was imported through the Forge routes. Forge converted it to an API prompt, using the installed model names it found. The local ComfyUI accepted the prompt (no node errors) and produced a real image. Evidence: [`docs/evidence/forge-m1/`](evidence/forge-m1/) (the converted prompt, `run.json`, and the output image).

### What it does
- **Import:** upload a workflow file (drag and drop or file picker), or pick one of ComfyUI's installed templates. The file is stored byte for byte with its SHA-256 and is never rewritten. Import runs nothing. The size limit is 20 MB. JSON that is not a workflow is refused.
- **Formats:** editor workflows (UI format 0.4, with subgraphs) and API prompts. Other JSON is refused with a reason.
- **Conversion to an API prompt** (`backend/open_workflow/forge/convert.py`), the step the ComfyUI editor normally does in JavaScript:
  - expands subgraphs; inner nodes get ids such as `57:27`, as in the editor
  - drops muted nodes and routes links through bypassed nodes and reroutes
  - takes values from primitive nodes, skips notes
  - maps saved widget values to input names using ComfyUI's node definitions, including the seed control value, upload buttons, and dynamic combos (`format`, `format.codec`)
- **Analysis** (`backend/open_workflow/forge/analysis.py`), with no language model involved:
  - **Controls:** every widget value, with its type, value and limits taken as facts from the definitions. Each also gets an inferred role (prompt, negative prompt, seed, width, image input…) that names the rule used, and a suggested placement (main panel, advanced, hidden).
  - **Outputs:** output nodes and their media.
  - **Model files:** each one is installed, installed in another folder (matched by file name, with the installed name given), ambiguous, missing, or unknown.
  - **Node types and packs:** node types ComfyUI does not have, and the custom-node packs the file names.
  - **App Builder:** selections from ComfyUI's App Builder (`extra.linearData`) are shown as hints.
  - **Readiness:** ready, needs attention, or blocked, with reasons.
- **Node definitions** come from the running ComfyUI. Failing that, they come from the last saved copy, or from a bundled core snapshot (ComfyUI 0.41.2, custom nodes disabled, with file lists and tooltips removed). The analysis always says which source it used.

### Measured coverage
With live definitions from this machine's ComfyUI 0.41.2 (custom nodes disabled), over the 564 installed editor-format templates:
- 399 convert with no problems
- 126 convert with notes or warnings
- 39 are blocked: their node types are missing (for example 3D and paid-API nodes), or a required value is missing

Across all 580 template files and the user's 258 saved workflows, no file made the parser or converter crash. The user's workflows show as blocked during this run only because ComfyUI was started with custom nodes disabled.

### Known limits
- V3 "dynamic" inputs other than dynamic combos are not mapped. Autogrow and match-type inputs work when they are connected by links.
- Editor-only widgets (3D viewers, painters, crop tools) are noted, not interpreted.
- Group nodes from old frontends are not supported. They show as unknown node types.
- The 576-second real run was slow because models loaded cold while system RAM was nearly full. This is not a measure of Forge's speed.

### Routes
`GET/POST /api/forge/workflows`, `GET /api/forge/workflows/{id}`, `GET /api/forge/workflows/{id}/analysis`, `GET /api/forge/workflows/{id}/api-prompt`, `PATCH /api/forge/workflows/{id}` (rename), `POST /api/forge/workflows/{id}/delete` (exact name to confirm), `GET /api/forge/templates`, `POST /api/forge/templates/import`.

### Tests
`backend/tests/test_forge_m1.py` has 24 tests. The fixtures are two unmodified MIT templates (`tests/fixtures/forge/`, with `NOTICE.txt`) and small hand-built graphs.

## Milestone 2: UI spec and renderer (done, 2026-10-22)

**Acceptance: met for preview.** A spec is generated from an analysed workflow with no language model, validated against the workflow, edited in the Apps > App tab, saved, and reopened. The preview is drawn by a fixed set of trusted components. **Running an app is not built yet**: the preview's Generate button is disabled and says so, and image upload is shown but disabled.

### Spec (`backend/open_workflow/forge/uispec.py`)
- **Versioned data, not code.** `schema_version` 1. A newer version is refused. Components come from a fixed catalogue: text, prompt, number, slider, select, toggle, seed, image upload. An unknown type is rejected when the spec is parsed. There is no component that runs a command or loads script.
- **Each component has a binding** (node id + input name). A binding is verified only if that exact input is an editable control in the workflow's analysis, the component type fits the input type (a toggle cannot set an INT), and its limits do not exceed the node's own. Model-file inputs are not controls, so a spec cannot expose or change them.
- **Verdicts:** `verified`, `unresolved` (cannot be checked yet, for example the list of allowed values is unknown while ComfyUI is not running), or `invalid`. Invalid components are not drawn. Saving a spec with an invalid binding is refused (422). Unresolved ones can be saved and stay flagged.
- **Generator:** the same workflow always gives the same spec. Prompts go first, then other main controls. Settings that are rarely changed (steps, sampler, dtype…) go under Advanced. Rarely useful inputs such as the output file prefix are left out. The layout is chosen from the analysis: reference-guided if there is an image input, simple generator for a small text-to-X workflow, otherwise advanced.
- **`apply_values`** (used from milestone 3): returns a copy of the API prompt with user values set. It changes only inputs bound by a not-invalid component, and checks type, range, allowed choices and text length. The original prompt is never changed.
- **Saving** keeps a revision number. A save based on an older revision is refused (409), so two windows cannot overwrite each other. The workflow's SHA-256 is stored; a spec made for a different copy is flagged as stale.

### UI
Apps now has Analysis and App tabs. The App tab shows a live preview (preview first, then the control list). Each control can be renamed, shown or hidden, moved to Main or Advanced, and reordered, and the layout can be changed. Every edit is re-validated on the server. Regenerate rebuilds the spec from the workflow without saving it.

### Found and fixed while checking it in the browser
- A browser cannot hold 2^64-1, the seed's upper limit. It sent back 2^64, and the server read that as a widened limit and refused every save. Limits are now compared as floats, and a test covers both the round trip and a real widening.
- Seeds the Randomize button creates stay below 2^53 so the value reaches the server exactly.

### Routes
`GET /api/forge/workflows/{id}/spec`, `PUT` the same (save), `POST .../spec/regenerate`, `POST .../spec/validate`.

### Tests
`backend/tests/test_forge_m2.py` (22): determinism, both templates, round trip, newer schema and bad shapes, bindings to missing nodes and model files, type and limit checks, duplicate bindings, stale workflow, value application and rejection cases, the seed round trip, and the routes (save, reload, stale save, refused invalid save).

### Not done
- Running an app, uploading an image, showing results (milestone 3).
- Components for video, audio, galleries and before/after.
- Adding a control that the generator did not offer, and layout templates with different structure. Only the ordering and Main/Advanced split differ between layouts so far.
- No frontend unit tests.

## Milestone 3: running an app (done, 2026-10-22)

**Acceptance: met for one app.** The saved Z-Image Turbo app was run from its App tab on the local ComfyUI. ComfyUI completed it, and the image was saved to the app's run folder with provenance. The image was checked and shown in the browser. Evidence: [`docs/evidence/forge-m3/`](evidence/forge-m3/) (the output image and `run.json`).

### How a run works
1. The run uses the **saved** app. Unsaved edits block the run, so every output matches a saved spec revision.
2. `forge/run.py` checks that the analysis is not blocked, that every control is verified, and that each model file is installed. Installed under another folder means ComfyUI's name for that file is used. Ambiguous, missing or unchecked files refuse the run. Nothing is guessed.
3. Only the bound inputs change. The API prompt is the converted workflow, with the values applied by `apply_values` (type, range and choice checks). `_meta` is removed.
4. The job enters the existing job engine as kind `app`. It skips shots and takes, and it never writes a smoke-test record. Its output goes to `<home>/forge/runs/<job>/output.png`, with `run.json` recording the workflow hash, spec revision, every value, the model files used, the ComfyUI prompt id and the output hash.
5. The App tab polls the job, shows the state, and offers Cancel. The finished image is served only from that run's folder.

### Refused before anything is queued
- App not saved (409), or the workflow file changed since the spec was saved (409)
- A workflow that is blocked, or a control that is not verified: every control must be verified to run
- Model file missing, ambiguous, or unchecked (ComfyUI not running)
- Out-of-range or wrong-type value, unknown control

### Real run
- ComfyUI 0.41.2 on this machine, custom nodes disabled, GPU RTX 3080 Laptop 16 GB.
- Settings: 512×512, 8 steps, defaults from the workflow.
- Result: `COMPLETED`, output 512×512, saved. The run took about nine minutes. Most of that was loading the models, with system RAM close to full, so it is not a measure of Forge's speed.
- The app shows Finished and the image in the App tab.

### Known limits
- Only apps with verified controls can run. The reference-image template's image input stays unverified: ComfyUI lists more than 200 uploadable files, which is over the limit at which Forge checks a choice list. Image upload is not built, so that app cannot run yet.
- No progress percentage (ComfyUI does not report one here), no retry for app runs (run it again from the app page), no history list of past runs in the UI yet.
- Seeds do not guarantee identical output across machines or software versions. The run record says so.

### Tests
`backend/tests/test_forge_m3.py` (11) uses a scripted ComfyUI. It covers the successful run, output and provenance, refusals (unsaved, missing and ambiguous models, unchecked models, out-of-range values, unknown controls, an invalid saved spec), a failed run that saves no output, and retry refusal for app runs. These tests prove Forge's own logic. The real run above is the evidence that ComfyUI accepts the prompt and produces the image.

## Milestone 4: local-model layout proposals (done, 2026-10-22)


### How it works
- **The model sees only the controls:** binding id, current label, type, limits, and the node name. It does not see model file names, prompt text, file paths, or output locations. A test checks this.
- **Reply format:** one JSON object (name, description, layout, and a list of control changes). Ollama 0.40 accepts JSON mode but not a JSON schema, so the schema is in the prompt and checked afterwards.
- **Checks:**
  - Unknown control ids are ignored, with a warning.
  - Component types outside the fixed catalogue are ignored, with a warning.
  - An unknown layout, or a reply that is not an object, is refused (422).
  - The result is applied to a copy of the spec and validated like any other spec. A proposal that breaks a binding cannot be applied.
- **Nothing is saved from a proposal.** Apply puts it in the draft; the user saves.
- **Refinement:** the request is made against the current draft, so unsaved edits are included.
- **Failure is clean:** Ollama not running, no chat model, a timeout, or invalid JSON each return a message, and the deterministic spec stays usable.
- **Loopback only:** the model URL is refused unless it is on this computer (also enforced in settings).

### Real result
- Model: `qwen3:4b-instruct-2507-q8_0` on Ollama 0.40.6. The first call took 32.5 seconds, mostly loading the model.
- Request: "Make this a simple portrait tool. Keep the prompt and seed in front; hide technical settings I never change."
- Result: 14 controls checked, all verified, no warnings. The model renamed the prompt and seed, hid width, height and the technical settings, and rewrote the help text.
- **Judgement call:** it also hid width and height, which a user may want to keep. The validator checks that the spec is valid, not that the layout is wise. Review is the reason for the proposal step.

### Known limits
- **One model and one workflow measured.** The plan asked for a benchmark across several models and ten real workflows. That has not been done, so no model is recommended beyond "it worked here".
- A workflow's node titles reach the model. A title could try to steer it. The checks still hold: it can change only layout fields, and only for controls that exist.
- Quality is not measured automatically. Labels and help text can read well and still be wrong for the user.
- Model loading makes the first request slow (about 20–30 seconds here). The panel says so.

### Tests
`backend/tests/test_forge_m4.py` (16) uses a fake model. It covers the valid path, unknown and invalid controls, unknown layouts, non-object replies, invalid JSON, the model being unavailable, a type change that breaks a binding (not savable), client specs checked against the workflow, what is sent to the model, request bounds, parse strictness, the status route, loopback-only settings, and the default-model choice.

## Milestone 5: library and .forgeapp packages (done, 2026-10-22)

**Acceptance: met.** The real Z-Image Turbo app was exported to a 6.7 KB `.forgeapp` file, then imported into an empty data folder. The imported app had its saved spec (all 14 controls verified). Example: [`docs/evidence/forge-m5/image_z_image_turbo.forgeapp`](evidence/forge-m5/image_z_image_turbo.forgeapp).

### The package
A zip with a fixed set of files:
- `manifest.json`: format, schema version, name, creation time, Forge version, the workflow's SHA-256, and a checksum for each other file
- `workflow/source.json`: the workflow exactly as imported
- `ui-spec.json`: the saved app
- `dependencies.json`: node types missing, custom node packs named, and model file names with their status at export. No paths, no weights.
- `README.md`: a short plain note

It never holds model weights, credentials, run outputs, or anything to execute. Import creates a new app. It never overwrites one: a repeated import becomes `name (2)`, `name (3)`, and so on.

### Import checks (all before anything is created)
- size limits: 30 MB for the package, 25 MB per file, and an expansion limit of 60×
- only the five fixed names; no folders, no paths with `..`, `/`, `\` or drive letters, no duplicates
- format name and schema version (a newer version is refused, with a message to update)
- every file's checksum, and the workflow's hash
- the app's controls must be valid, by the same rules as Milestone 2

A refused package creates nothing. Tests cover each case.

### Duplicate
A copy has its own spec and the same workflow file. Editing the copy does not change the original. Duplicating an unsaved app is refused.

### Routes
`GET /api/forge/workflows/{id}/export`, `POST /api/forge/packages/import?name=…` (raw body), `POST /api/forge/workflows/{id}/duplicate`.

### Limits
- A package can say which models it needs, but Forge does not install them. Run stays blocked until the models are in ComfyUI.
- Packages are not signed. The checksums catch damage and accidental edits, not a deliberate rewrite of the whole package.
- The library has no search, folders, or favourites yet. The app list is a strip in the Apps page.

### Tests
`backend/tests/test_forge_m5.py` (20): export rules, package contents (no paths or weights), round trip, numbered copies, duplicate independence, and refusals for traversal, extra files, folders, newer versions, changed parts, mismatched workflow hashes, invalid controls, missing parts, non-packages, expansion bombs, and size limits, plus the naming helpers.

## Milestone 6: providers, local and Comfy Cloud (partly done, 2026-10-27)

**Status:** hybrid as chosen. Local ComfyUI is the default. Comfy Cloud is available only when you configure a key, and every cloud run needs your consent to the exact preview. **Comfy Cloud has not been run live**: no key was available and the SDK is not installed.

### What works (tested with mocks)
- **Provider per job.** A job is bound to one provider when it is created. A failed local job never retries on the cloud, and a failed cloud job never retries locally (tested).
- **Consent.** `POST /api/forge/workflows/{id}/cloud-preview` returns what would be sent (host, items, what is not sent) and a digest of the exact converted prompt. A cloud run is refused (409) unless its consent matches that digest. The UI shows the preview first, with a checkbox, and sends nothing before it is ticked.
- **Status.** `GET /api/forge/providers` reports both providers. It never returns the key.
- **Key handling.** The key comes only from the environment variable `OPEN_WORKFLOW_COMFY_API_KEY`. It is never written to a file, a job record, a package, or a log. A test checks the data folder after a run.
- **Recovery.** A cloud job active when Open Workflow stopped is marked interrupted and is not polled, because its status is not checked.

### Built on the documented SDK calls (not verified live)
Comfy's [SDK page](https://docs.comfy.org/development/api-development/sdks) documents `Comfy(api_key=...)`, `workflows.from_file`, `client.run(wf)`, `job.get_outputs(node)`, and `output.to_file`. The adapter uses only those. Its base URL comes from `COMFY_BASE_URL` (default `https://cloud.comfy.org`).

### Not done or not verified
- **Live cloud run:** not run. Needs a key and `pip install comfy-sdk`, which needs your approval.
- **Cancel for cloud runs:** not supported. The SDK's `run` call blocks, and the cancel path says so.
- **Cloud model availability:** unverified, so apps with model files cannot run on the cloud. Z-Image is one of them.
- **SDK licence:** the SDK repos say nothing in the pages I read. Check before installing.
- **Comfy Developer Platform rule:** whether a local-only entry qualifies is still unresolved. This work adds a cloud route, which does not settle it.
- **Prices:** not shown, because the docs' price list is not verified for the API route.

### Tests
`backend/tests/test_forge_providers_m6.py` (17) uses a fake SDK and a fake ComfyUI. It covers status and key handling, the preview digest, the runner's calls, cancel refusal, error redaction, consent gates, a consented cloud run on the cloud runner only, cloud failure with no fallback, restart recovery, and no key on disk.

## Next
- **M5:** library and `.forgeapp` packages.
- **M6:** optional Comfy Cloud provider.
- **M7:** polish.
