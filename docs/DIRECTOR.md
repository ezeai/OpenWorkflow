# Director and shot planner

Milestone 4. Everything here runs without a language model. The planner is a set of visible rules.

## Script to shots

1. **Script.** Plain text. Lines starting `INT.`, `EXT.`, `INT/EXT.`, `SCENE:` or `#` start a scene. A line like `NAME: words` is dialogue. Any other line is action. Text with no heading becomes one untitled scene. Saves as you type.
2. **Plan from script** (a proposal only, nothing is written):
   - Each scene opens with an **establishing** shot.
   - Each action line becomes a shot. Its framing is medium when a known character is named, otherwise wide.
   - Each dialogue line becomes a **close-up** of the speaker, when the speaker is a known character.
   - Each dialogue line is followed by a **reaction** shot of another character, when one exists.
   - Movement words suggest a camera move: running or walking suggests tracking, looking suggests a slow push-in, weather suggests handheld. Matching is whole-word, so "window" does not match "wind".
   - Each proposal shows the rule behind it.
3. **Apply.** You choose which proposals to add. Existing shots are never replaced. The plan is computed again on the server when applied, so a stale plan cannot write the wrong shot.

Scene ids come from a hash of the heading and its position. Editing a heading gives the scene a new id, so shots that pointed at the old one are flagged `scene_missing`. They are not deleted.

## Shot fields

Framing, camera angle, camera movement, lens, depth of field, composition, motion intensity, duration (0.5 to 30 s), workflow, enabled, characters, location, props, and text (beat, action, purpose, lighting, colour, tone, continuity notes). Every enumerated value is checked by the server.

Presets (Dialogue close-up, Hero entrance, and six more) set **only the camera fields**. Your text is kept.

## Capability map: what the workflow actually does

Each camera control shows one of:

| Status | Meaning |
|---|---|
| `honoured` | The workflow executes it. **No workflow has this yet.** |
| `prompt_only` | Written into the prompt as guidance. Nothing checks the result. |
| `unsupported` | The workflow's declaration says it is not executed. The value is kept as shot metadata. |
| `not_mapped` | No workflow is chosen, so the intent is stored only. |

A camera move is never presented as executed for an image workflow. For example, `img-z-image-turbo` declares `camera_move` unsupported, and the inspector says so.

## Takes and regeneration

- **Request take** records a snapshot of the shot's settings, with its capability map, as a take in state `requested`.
- Requesting another take **keeps every earlier take**. Regenerating one shot never touches any other shot.
- Nothing is generated in this milestone. A take shows "Requested. Not generated yet." until the job engine exists (Milestone 5).
- A shot with takes cannot be removed. It can be disabled, which keeps its history.

## Coverage suggestions

Deterministic rules, each with a stable key so it can be dismissed (and the dismissal is kept):

- A scene with no establishing or wide shot.
- A dialogue scene with no reaction shot.
- Four shots in a row with the same framing in the same scene.
- A character named in the script with no shot.
- A location change between scenes with no establishing shot.

## Concurrency and recovery

Every write sends the project revision it last saw. A stale write is refused with HTTP 409, and the page reloads. Writes from the page go through one queue, so quick successive clicks cannot conflict with each other. Shots live in the project file, so they follow the same atomic-save and backup rules as the rest of the project.

## Project format

Schema version 2 adds `script_text`, `shots` and `dismissed_suggestions`. A version 1 project is migrated on load with those fields empty. Nothing else changes.

## Not done

- No generation, so no output images or video. Takes are requests only.
- No drag-and-drop reordering. Reordering uses the up and down buttons.
- No language model planning. The rules above are the only planner.
- The keyboard and focus audit is still a manual pass, and there are no frontend unit tests.
