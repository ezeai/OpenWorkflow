# Forge: custom-node dependencies

Status: **milestone 0 (audit) and milestone 1 (deterministic dependency analysis) are done.** Package discovery beyond ComfyUI Manager's mapping, installation, approvals, restart checks and model dependency reporting are **not built**. This page is the record of what exists and what does not.

## 1. Audit (2026-10-27)

| Area | Found | Notes |
|---|---|---|
| Workflow parsing | `backend/open_workflow/forge/workflow.py`, `convert.py` | Reused. Gives node classes, modes, subgraphs and links. |
| ComfyUI node inventory | `forge/object_info.py`, `api_forge._info` | Reused. Live `/object_info` with a 60 s in-memory cache, a saved copy, and a bundled core snapshot. The live source is the only one that can show a class as registered. |
| Custom-node folders | `D:\…\ComfyUI\custom_nodes` on this machine: about 60 folders | Read only, counted. Not matched to classes by name. Example: the `ComfyUI-GGUF` folder registers `UnetLoaderGGUF`, but a workflow asking for `LoaderGGUF` is not satisfied by it. |
| ComfyUI Manager | Installed: version 3.39.3, `security_level = normal`, `network_mode = public`, `db_mode = cache` | Read-only use: its `extension-node-map.json` (about 4,100 repositories, 31,000 class entries) maps classes to repositories. Its install routes (`/customnode/install/git_url`, `/manager/queue/install`) are **not called**. |
| Existing installer | None in Forge. The repo's own `custom-nodes/ComfyUI-Open Workflow` is our pack, not an installer. | Nothing to reuse for installation. |
| Frontend | Apps analysis tab | Extended with a Custom nodes section. |
| Tests | 15 new, plus the existing Forge suites | Mocks only for network; no install is tested. |

### Security observations
- Installing a custom node runs its Python code. Nothing in Forge does that yet. Any future installer needs explicit approval, an argument-list subprocess (no shell), a timeout, and redacted logs.
- Manager's `security_level = normal` is set on this machine. Whether it permits Git-URL installs was **not tested**, so the app makes no claim about it.
- The Manager mapping is a third-party list. A candidate is a lead, not evidence the repository is safe.

### Gaps against the brief
- Official Comfy registry: not used. I did not find a documented, stable endpoint to call here, and the brief says not to invent one. Manager's mapping is the only package source used.
- Git URL intake, release or commit selection, dependency plan review, install lifecycle, restart handling, post-install verification, model dependency reporting, and the dependency approval screen: **not built**.

## 2. What the slice does

`backend/open_workflow/forge/deps.py`:

- **Required classes:** every node class a run would use. Muted and bypassed nodes are skipped. Subgraph wrappers are expanded and their inner nodes are reported with their scope.
- **Availability, with its source:**
  - `registered`: in the live ComfyUI node list. This is the only thing that counts as available for a custom node.
  - `core`: in the bundled or saved core list.
  - `unchecked`: ComfyUI is not running and the class is not core. It is **not** reported as missing.
  - `missing`: the live list does not have it, and no pack is known.
- **Candidate packs:** from Manager's mapping, by exact class name. One candidate is `likely`; several are `ambiguous` and all are listed (status `choose`). None means no candidate.
- **Never claimed:** `installed` is always `null`. Verification reads "registered in the running ComfyUI" only for live registration.
- **Caching:** the mapping is parsed once per file version. The live node list is kept for 60 s.

## 3. Evidence

- **Real workflow, live ComfyUI (custom nodes enabled, 2026-10-27):** a saved workflow needing 16 classes showed 15 `registered` and 1 `choose`. The one was `LoaderGGUF`, with two candidate packs (`ComfyUI-WarpedToolset`, `gguf`). The installed `ComfyUI-GGUF` pack does not provide that class.
- **Real workflow, ComfyUI not running:** a saved workflow with about 50 non-core classes showed them all `unchecked`, with candidates for most and none for a few (for example `IF_PromptMkr`). Headline: "ComfyUI is not running, so custom nodes cannot be checked."
- **Response time:** the analysis took about 6–9 s on a cold request before the caches. It is now about 0.01 s on repeat requests.

## 4. Limits

- Manager's mapping is a snapshot of Manager's data at its last update. It can be out of date.
- A class can be on Manager's list and still fail to load (for example, missing Python dependencies). Only a live check shows registration, and even that does not prove the node works.
- "Registered" means ComfyUI lists the class. It does not mean a run with it has succeeded.
- No network calls are made by this code. The only input is the local mapping file and the ComfyUI you configured.

## 5. Next slices, in order

1. Manual Git URL intake: validation (HTTPS only, no local paths, no shell), and a reviewed plan that shows the target Python, repository, ref and dependencies. No install yet.
2. Installation lifecycle: approval, argument-list subprocess with a timeout, a pre-install snapshot, and a state machine ending in "verified only after a live registration check".
3. Restart handling and post-install re-analysis.
4. Model dependency reporting (separate approval, never automatic download).
