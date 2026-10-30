# Continuity and canvas (Milestone 6)

## Continuity

Checks run whenever a project is read. They never block work. Each warning has a stable key. You can accept a warning with a written reason (at least three characters). The acceptance stays listed with its reason, and the warning is never hidden.

### Built-in checks
| Warning | When |
|---|---|
| Character changed since a take | A shot's latest generated take was made from an older character version. Versions are recorded on each take when it is requested. |
| Location changed since a take | Same, for a location. |
| Missing reference image | A character's reference file is missing or changed on disk. Checked for every character, whether or not it is in a shot. |
| Identity lock not applied | A character asks for identity locking that the shot's workflow does not apply. No workflow in this build applies it. |

### User rules
| Rule | Effect |
|---|---|
| Same character version in every take | Each generated take of a shot featuring the character must use the newest version. |
| Needs an approved take first | A target shot warns until a source shot has an approved take. |
| Keep a location at a set time of day | Warns for each shot using the location when its time of day differs. |

Rules are added and removed in the Director's Continuity tab. Ids are pasted in from the Characters, Worlds and Shots views. A rule with missing parameters is refused.

## Canvas (typed node graph)

Five node types. Each one only exists because it does something real:

| Node | Inputs | Outputs | Notes |
|---|---|---|---|
| Shot | none | shot | Points at one project shot. |
| Character | none | character | Points at one character. |
| Location | none | location | Points at one location. |
| Generate: Z-Image Turbo | shot | asset | Starts the same job as the Director's Generate take. |
| Report | asset | none | Terminal node. |

Ports are typed (shot, character, location, asset, report). A connection between mismatched types is refused in the editor and rejected by the server. The server also rejects: unknown node types or ports, a second connection into one input, cycles, shot nodes pointing at missing or disabled shots, and character or location nodes pointing at the wrong kind of entity. A save with bad connections is accepted and reported; a malformed graph is refused.

**Run** is only offered when validation passes. It starts one job per generator node through the same function the Director uses (`start_shot_generation`). The test suite checks that a canvas run and a Director run produce jobs with the same shape, state and prompt.

### Templates
- **Shot to image (Z-Image Turbo):** shot → generator → report.
- **Character and location to shot image:** character and location nodes are shown for context. They are not yet connected to the prompt. Connect them to the shot node if you want to include them. This is a limit, noted here.

## Not done
- Continuity checks do not look at screen direction, wardrobe state, or props on screen (no data for them yet).
- The canvas has no undo, copy and paste, or grouping. Save is automatic on change.
- Characters and locations on the canvas do not feed the prompt. They are informational until a graph-level prompt input exists.
- Only one generator node type exists.
