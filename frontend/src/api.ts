export type EngineHealth =
  | { state: "ok"; url: string; comfyui_version?: string; devices: { name: string; vram_total: number; vram_free: number }[] }
  | { state: "unreachable"; url: string; error?: string };

export interface Health {
  app: string;
  version: string;
  storage_home: string;
  engine: EngineHealth;
  open_weights_path: boolean;
  first_run_complete: boolean;
}

export interface Hardware {
  os: string;
  python: string;
  cpu_threads: number;
  ram_total_gb: number;
  ram_available_gb: number;
  gpus: { name: string; vram_total_mb: number; vram_used_mb: number; vram_free_mb: number; driver: string }[];
  disk_free_gb: Record<string, number | null>;
  ffmpeg: { available: boolean; path: string | null; version: string | null };
}

export type Category = "managed" | "installed" | "discovered" | "missing";

export interface FileView {
  path: string;
  name: string;
  size: number;
  ownership: "external" | "user_shared" | "managed";
  format: string;
  header_ok: boolean;
  header_error: string | null;
  is_symlink: boolean;
  link_target: string | null;
  root: string;
}

export interface ComponentView {
  role: string;
  state: "missing" | "found" | "identified";
  license_status: string;
  license_note: string;
  files: FileView[];
  confirmed_path: string | null;
  identity_stale: boolean;
}

export interface VariantView {
  id: string;
  name: string;
  publisher_files: boolean;
  discovered: boolean;
  complete: boolean;
  identified: boolean;
  header_ok: boolean;
  validated: boolean;
  managed: boolean;
  components: ComponentView[];
}

export interface ModelView {
  id: string;
  name: string;
  family: string;
  tasks: string[];
  publisher: string;
  source_url: string;
  category: Category;
  discovered: boolean;
  identified: boolean;
  validated: boolean;
  smoke_tested: boolean;
  download_available: boolean;
  license: { id: string; status: "verified" | "restricted" | "unverified"; evidence_url: string | null; verified_on: string | null; commercial_restriction: string | null; notes: string };
  open_weight_eligibility: "eligible" | "review" | "not_eligible";
  variants: VariantView[];
  limitations: string[];
  obtain_from: string;
  approx_size_gb: number | null;
  install_guide: string;
  workflows?: string[];
  download_sources?: { variant: string; available: boolean; reason: string | null }[];
}

export interface Problem { kind: string; path: string; detail: string }

export interface ModelsResponse {
  models: ModelView[];
  catalog_errors: string[];
  registry_errors: string[];
  scan: { roots: { path: string; ownership: string; label: string; exists: boolean }[]; files_found: number; problems: Problem[]; last_scan_at: string | null };
}

export interface WorkflowEval {
  id: string;
  version: string;
  name: string;
  task: string;
  status: "license_blocked" | "unknown_engine" | "missing_models" | "unconfirmed_files" | "missing_nodes" | "missing_packs" | "requirements_met_untested" | "validated";
  blockers: string[];
  missing_models: { model_id: string; variant?: string; role?: string; reason?: string }[];
  unconfirmed_files: { model_id: string; variant: string; role: string }[];
  missing_nodes: string[];
  missing_packs: string[];
  smoke_tested: boolean;
  supported_params: string[];
  unsupported_controls: string[];
  license_requirements: string;
  limitations: string[];
}

export interface Recommendation {
  workflow_id: string | null;
  status: string | null;
  reason: string;
  blockers?: string[];
  next_steps?: string[];
}

export interface WizardState {
  steps: string[];
  completed: string[];
  skipped: string[];
  starter_workflow: string | null;
  updated_at: string | null;
}

export interface Plan {
  plan_id: string;
  model_id: string;
  variant: string;
  source_repo: string;
  license: { id: string; status: string };
  files: { relpath: string; url: string; size_bytes: number; sha256: string | null; checksum: string }[];
  total_bytes: number;
  destination: string;
  free_bytes: number | null;
  required_bytes: number;
  checksum_authority: string;
  needed_for_workflows: string[];
  expires_at: string;
}

export interface Job { job_id: string; plan_id: string; state: string; bytes_done: number; bytes_total: number; current_file: string | null; error: string | null; results: unknown[] }

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await r.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    const detail = (data as { detail?: unknown })?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d: { message?: string }) => d.message).join("; ") : `${r.status}`;
    throw new ApiError(msg || `HTTP ${r.status}`, r.status);
  }
  return data as T;
}

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export const api = {
  health: () => request<Health>("GET", "/api/health"),
  hardware: () => request<Hardware>("GET", "/api/hardware"),
  models: () => request<ModelsResponse>("GET", "/api/models"),
  model: (id: string) => request<ModelView & { workflows: string[]; download_sources: { variant: string; available: boolean; reason: string | null }[] }>("GET", `/api/models/${id}`),
  scan: () => request<{ files_found: number; problems: Problem[] }>("POST", "/api/models/scan"),
  identify: (id: string, body: { variant: string; role: string; path: string }) =>
    request<{ identified: boolean }>("POST", `/api/models/${id}/identify`, { ...body, confirm: true }),
  verifyHash: (id: string, body: { variant: string; role: string; path: string }) =>
    request<{ sha256: string; from_cache: boolean; authority: string }>("POST", `/api/models/${id}/verify-hash`, body),
  removeManaged: (id: string, variant: string, typed: string) =>
    request<{ removed: string[] }>("POST", `/api/models/${id}/managed/remove`, { variant, confirm_model_id: typed }),
  plan: (id: string, variant: string) => request<Plan>("POST", `/api/models/${id}/plan`, { variant }),
  startDownload: (planId: string) => request<Job>("POST", `/api/downloads/${planId}/start`, { confirm: true }),
  download: (jobId: string) => request<Job>("GET", `/api/downloads/${jobId}`),
  cancelDownload: (jobId: string) => request<Job>("POST", `/api/downloads/${jobId}/cancel`),
  workflows: () => request<{ workflows: WorkflowEval[]; catalog_errors: string[] }>("GET", "/api/workflows"),
  recommendation: () => request<Recommendation>("GET", "/api/workflows/recommendation"),
  roots: () => request<{ roots: { path: string; ownership: string; label: string }[]; note: string }>("GET", "/api/model-roots"),
  addRoot: (path: string) => request<{ added: string }>("POST", "/api/model-roots", { path }),
  removeRoot: (path: string) => request<{ removed_from_list: string }>("POST", "/api/model-roots/remove", { path }),
  detectComfy: () => request<{ found: { path: string; models_dir: string }[]; note: string }>("GET", "/api/comfy/detect"),
  setComfyRoot: (path: string) => request<{ comfy_root: string }>("POST", "/api/comfy/root", { path }),
  wizard: () => request<WizardState>("GET", "/api/wizard"),
  wizardStep: (step: string, action: "complete" | "skip", workflowId?: string | null) =>
    request<{ completed: string[]; skipped: string[] }>("POST", "/api/wizard/step", { step, action, workflow_id: workflowId ?? null }),
  wizardReset: () => request<{ reset: boolean }>("POST", "/api/wizard/reset"),
};

// ---------------- Milestone 3: projects ----------------

export type EntityKindId = "character" | "world" | "style" | "prop";

export interface EntityVersion { version: number; created_at: string; fields: Record<string, string>; refs: { asset_id: string; role: string }[]; note: string }

export interface Entity {
  id: string;
  kind: EntityKindId;
  name: string;
  fields: Record<string, string>;
  refs: { asset_id: string; role: string }[];
  current_version: number;
  versions: EntityVersion[];
  archived: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectAsset {
  id: string;
  rel_path: string;
  mime: string;
  size: number;
  sha256: string;
  width: number | null;
  height: number | null;
  label: string;
  created_at: string;
  status: "ok" | "missing" | "size_mismatch" | "hash_mismatch" | "invalid_path";
  status_detail: string | null;
}

export interface ProjectView {
  id: string;
  title: string;
  description: string;
  created_at: string;
  updated_at: string;
  revision: number;
  recovery_note: string | null;
  entities: Entity[];
  assets: ProjectAsset[];
  field_labels: Record<EntityKindId, Record<string, string>>;
  ref_roles: Record<EntityKindId, string[]>;
}

export interface ProjectSummary { id: string; title: string; updated_at: string; readable: boolean; note: string | null }

export const projectsApi = {
  list: () => request<{ projects: ProjectSummary[] }>("GET", "/api/projects"),
  create: (title: string, description = "") => request<ProjectView>("POST", "/api/projects", { title, description }),
  get: (id: string) => request<ProjectView>("GET", `/api/projects/${id}`),
  patch: (id: string, body: { base_revision: number; title?: string; description?: string }) =>
    request<ProjectView>("PATCH", `/api/projects/${id}`, body),
  remove: (id: string, confirmTitle: string) => request<{ deleted: string }>("POST", `/api/projects/${id}/delete`, { confirm_title: confirmTitle }),
  createEntity: (id: string, baseRevision: number, kind: EntityKindId, name: string) =>
    request<{ entity_id: string; project: ProjectView }>("POST", `/api/projects/${id}/entities`, { base_revision: baseRevision, kind, name }),
  patchEntity: (id: string, eid: string, body: { base_revision: number; name?: string; fields?: Record<string, string>; refs?: { asset_id: string; role: string }[]; archived?: boolean }) =>
    request<ProjectView>("PATCH", `/api/projects/${id}/entities/${eid}`, body),
  restore: (id: string, eid: string, baseRevision: number, version: number) =>
    request<ProjectView>("POST", `/api/projects/${id}/entities/${eid}/restore`, { base_revision: baseRevision, version }),
  kinds: () => request<{ kinds: { id: EntityKindId; label: string; fields: Record<string, string>; ref_roles: string[] }[] }>("GET", "/api/entity-kinds"),
  verifyAsset: (id: string, aid: string, deep: boolean) =>
    request<{ status: string; detail: string | null }>("POST", `/api/projects/${id}/assets/${aid}/verify`, { deep }),
  uploadImage: async (id: string, file: File, label: string): Promise<{ asset: ProjectAsset }> => {
    const r = await fetch(`/api/projects/${id}/assets?label=${encodeURIComponent(label)}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const detail = (data as { detail?: unknown }).detail;
      throw new ApiError(typeof detail === "string" ? detail : `Upload failed (${r.status})`, r.status);
    }
    return data as { asset: ProjectAsset };
  },
  fileUrl: (id: string, aid: string) => `/api/projects/${id}/assets/${aid}/file`,
};

// ---------------- Milestone 4: director ----------------

export type Capability = "honoured" | "prompt_only" | "unsupported" | "not_mapped";

export interface ShotWarning { level: "info" | "warn" | "error"; code: string; message: string }

export interface TakeView { id: string; number: number; state: string; note: string; created_at: string; settings: Record<string, unknown>; asset_id: string | null }

export interface ShotView {
  id: string;
  number: number;
  scene_id: string;
  scene_title: string;
  character_ids: string[];
  location_id: string | null;
  prop_ids: string[];
  shot_size: string;
  camera_angle: string;
  camera_move: string;
  lens: string;
  depth: string;
  composition: string;
  move_intensity: string;
  duration_s: number;
  workflow_id: string | null;
  enabled: boolean;
  preset: string | null;
  texts: Record<string, string>;
  takes: TakeView[];
  revision: number;
  capabilities: Record<string, Capability | string>;
  warnings: ShotWarning[];
}

export interface DirectorView {
  revision: number;
  script_text: string;
  scenes: { id: string; title: string; beats: number }[];
  shots: ShotView[];
  suggestions: { key: string; message: string; why: string }[];
  presets: { id: string; label: string; fields: Record<string, string> }[];
  workflows: { id: string; name: string; task: string; unsupported_controls: string[] }[];
  characters: { id: string; name: string }[];
  locations: { id: string; name: string }[];
  props: { id: string; name: string }[];
  recovery_note?: string | null;
}

export interface Proposal {
  scene_id: string;
  scene_title: string;
  location_id: string | null;
  shot_size: string;
  camera_move: string;
  camera_angle: string;
  character_ids: string[];
  prop_ids: string[];
  texts: Record<string, string>;
  why: string;
}

export const CAMERA_OPTIONS: Record<string, string[]> = {
  shot_size: ["establishing", "extreme_wide", "wide", "medium", "medium_close_up", "close_up", "extreme_close_up", "over_the_shoulder", "two_shot", "profile", "insert", "reaction"],
  camera_angle: ["eye_level", "low", "high", "birds_eye", "ground_level", "dutch", "profile", "pov"],
  camera_move: ["static", "push_in", "pull_out", "pan", "tilt", "tracking", "dolly", "orbit", "crane", "handheld", "pov_movement", "aerial"],
  lens: ["wide_angle", "normal", "portrait", "telephoto", "macro", "fisheye"],
  depth: ["deep", "moderate", "shallow", "subject_isolation"],
  composition: ["centered", "rule_of_thirds", "symmetrical", "negative_space", "leading_lines", "foreground_framing", "layered_depth"],
  move_intensity: ["low", "medium", "high"],
};

export const directorApi = {
  get: (pid: string) => request<DirectorView>("GET", `/api/projects/${pid}/director`),
  setScript: (pid: string, base: number, text: string) => request<DirectorView>("PUT", `/api/projects/${pid}/script`, { base_revision: base, text }),
  plan: (pid: string) => request<{ scenes: { id: string; title: string }[]; proposals: Proposal[]; note: string; revision: number }>("POST", `/api/projects/${pid}/plan`),
  applyPlan: (pid: string, base: number, picks: number[]) => request<DirectorView & { created: string[] }>("POST", `/api/projects/${pid}/plan/apply`, { base_revision: base, picks }),
  addShot: (pid: string, base: number, sceneId?: string) => request<DirectorView & { shot_id: string }>("POST", `/api/projects/${pid}/shots`, { base_revision: base, scene_id: sceneId ?? null }),
  patchShot: (pid: string, sid: string, base: number, fields: Record<string, unknown>, preset?: string) =>
    request<DirectorView>("PATCH", `/api/projects/${pid}/shots/${sid}`, { base_revision: base, fields, preset: preset ?? null }),
  duplicate: (pid: string, sid: string, base: number) => request<DirectorView>("POST", `/api/projects/${pid}/shots/${sid}/duplicate`, { base_revision: base }),
  remove: (pid: string, sid: string, base: number) => request<DirectorView>("POST", `/api/projects/${pid}/shots/${sid}/remove`, { base_revision: base }),
  reorder: (pid: string, base: number, order: string[]) => request<DirectorView>("PUT", `/api/projects/${pid}/shots/order`, { base_revision: base, order }),
  requestTake: (pid: string, sid: string, base: number, note: string) => request<DirectorView>("POST", `/api/projects/${pid}/shots/${sid}/takes`, { base_revision: base, note }),
  approveTake: (pid: string, sid: string, tid: string, base: number) => request<DirectorView>("POST", `/api/projects/${pid}/shots/${sid}/takes/${tid}/approve`, { base_revision: base }),
  unapproveTake: (pid: string, sid: string, tid: string, base: number) => request<DirectorView>("POST", `/api/projects/${pid}/shots/${sid}/takes/${tid}/unapprove`, { base_revision: base }),
  dismiss: (pid: string, key: string, base: number) => request<DirectorView>("POST", `/api/projects/${pid}/suggestions/${encodeURIComponent(key)}/dismiss`, { base_revision: base }),
};

// ---------------- Milestone 5: generation and jobs ----------------

export type JobState = "QUEUED" | "VALIDATING" | "LOADING_MODEL" | "RUNNING" | "POSTPROCESSING" | "COMPLETED" | "FAILED" | "CANCELLING" | "CANCELLED" | "INTERRUPTED" | "RECOVERY_REQUIRED";

export interface JobProgress {
  mode: "determinate" | "indeterminate" | string;
  node?: string | null;
  step?: number | null;
  steps?: number | null;
  nodes_finished?: number;
  updated_at?: string;
}

export interface JobView {
  id: string;
  state: JobState;
  project_id: string;
  shot_id: string;
  take_id: string;
  workflow_id: string;
  workflow_version: string;
  params: Record<string, number | string>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
  models: { role: string; name?: string; file?: string; status?: string; identity_confirmed?: boolean; license_status?: string }[];
  error: string | null;
  output_asset_id: string | null;
  kind?: string;
  output_file?: { name: string; sha256: string; bytes: number; media: string } | null;
  progress: JobProgress;
  notes: string[];
  history: { state: JobState; at: string; note: string }[];
  prompt: { positive: string; negative: string } | null;
}

export const ACTIVE_JOB_STATES: JobState[] = ["QUEUED", "VALIDATING", "LOADING_MODEL", "RUNNING", "POSTPROCESSING", "CANCELLING"];

export interface GenerationPreflight {
  ready: boolean;
  problems: string[];
  params: Record<string, number | string>;
  prompt: { positive: string; negative: string };
  notes: string[];
  models: { role: string; name: string; path: string; identity_confirmed: boolean; license_status: string }[];
  nodes_checked: number;
}

export const jobsApi = {
  status: () => request<{ engine_reachable: boolean; worker_alive: boolean; generatable_workflows: string[]; note: string }>("GET", "/api/generation/status"),
  generate: (pid: string, sid: string, base: number, seed?: number | null) =>
    request<JobView>("POST", `/api/projects/${pid}/shots/${sid}/generate`, { base_revision: base, seed: seed ?? null, width: 512, height: 512, steps: 8 }),
  preflight: (pid: string, sid: string) =>
    request<GenerationPreflight>("POST", `/api/projects/${pid}/shots/${sid}/preflight`, { base_revision: 0, seed: null, width: 512, height: 512, steps: 8 }),
  list: () => request<{ jobs: JobView[] }>("GET", "/api/jobs"),
  get: (jid: string) => request<JobView>("GET", `/api/jobs/${jid}`),
  cancel: (jid: string) => request<JobView>("POST", `/api/jobs/${jid}/cancel`),
  retry: (jid: string) => request<JobView>("POST", `/api/jobs/${jid}/retry`),
  importOutput: (jid: string) => request<JobView>("POST", `/api/jobs/${jid}/import`),
};

// ---------------- Milestone 6: continuity and canvas graphs ----------------

export const api_canvas = {
  list: (pid: string) => request<{ revision: number; graphs: { id: string; name: string; nodes: number; edges: number; valid: boolean; updated_at: string }[] }>("GET", `/api/projects/${pid}/graphs`),
  get: (pid: string, gid: string) => request<{ id: string; name: string; nodes: { id: string; type: string; x: number; y: number; params: Record<string, string> }[]; edges: { id: string; src: string; src_port: string; dst: string; dst_port: string }[]; validation: { ok: boolean; errors: { node?: string; edge?: string; code: string; message: string }[]; warnings: { node?: string; message: string }[]; run_plan: { node: string; shot_id: string }[] }; updated_at: string; revision: number }>("GET", `/api/projects/${pid}/graphs/${gid}`),
  create: (pid: string, base: number, name: string, template: string | null) =>
    request<{ id: string; revision: number }>("POST", `/api/projects/${pid}/graphs`, { base_revision: base, name, template }),
  save: (pid: string, gid: string, body: { base_revision: number; nodes: unknown[]; edges: unknown[]; name?: string }) =>
    request<{ id: string; revision: number; validation: { ok: boolean; errors: { node?: string; edge?: string; code: string; message: string }[]; warnings: { node?: string; message: string }[]; run_plan: { node: string; shot_id: string }[] } }>("PUT", `/api/projects/${pid}/graphs/${gid}`, body),
  run: (pid: string, gid: string, base: number) => request<{ graph_id: string; jobs: string[] }>("POST", `/api/projects/${pid}/graphs/${gid}/run`, { base_revision: base }),
};

export const continuityApi = {
  get: (pid: string) => request<{ revision: number; rules: { id: string; kind: string; params: Record<string, string>; note: string }[]; rule_kinds: string[]; warnings: { key: string; shot_id: string | null; message: string; why: string; accepted: boolean; accepted_reason: string | null }[]; acks: unknown[] }>("GET", `/api/projects/${pid}/continuity`),
  addRule: (pid: string, base: number, kind: string, params: Record<string, string>, note: string) =>
    request<unknown>("POST", `/api/projects/${pid}/continuity/rules`, { base_revision: base, kind, params, note }),
  removeRule: (pid: string, rid: string, base: number) => request<unknown>("POST", `/api/projects/${pid}/continuity/rules/${rid}/remove`, { base_revision: base }),
  accept: (pid: string, base: number, key: string, reason: string) => request<unknown>("POST", `/api/projects/${pid}/continuity/accept`, { base_revision: base, key, reason }),
};

// ---------------- Forge: workflow import and analysis ----------------

export interface ForgeWorkflow { id: string; name: string; origin: string; format: "ui" | "api" | "api_wrapped"; sha256: string; size: number; imported_at: string; readiness?: ForgeAnalysis["readiness"] | null; reasons?: string[] }
export interface ForgeTemplate { name: string; title: string; category: string | null; group: string | null; description: string; models: string[]; media: string | null; open_source: boolean; uses_paid_api: boolean }
export interface ForgeProblem { severity: "error" | "warning" | "info"; message: string; node: string | null }
export interface ForgeControl {
  id: string; node: string; input: string; class_type: string; node_title: string | null; scope: string;
  fact: { type: string | null; value: unknown; constraints: Record<string, unknown>; origin: string; checked_against: string | null };
  inferred: { kind: string; rule: string };
  app_builder_selected: boolean;
  suggested: { show: boolean; advanced: boolean; hidden: boolean };
}
export interface ForgeModelFile { id: string; node: string; input: string; class_type: string; file: string; status: "available" | "found_elsewhere" | "ambiguous" | "missing" | "unknown"; comfy_name: string | null; alternatives: string[]; checked_against: string }
export interface ForgeAnalysis {
  task: { family: string; rule: string; inferred: boolean };
  readiness: "ready" | "needs_attention" | "blocked";
  reasons: string[];
  counts: Record<string, number>;
  controls: ForgeControl[];
  outputs: { node: string; class_type: string; title: string | null; media: string; app_builder_selected: boolean }[];
  model_files: ForgeModelFile[];
  unknown_node_types: string[];
  custom_node_packs: { id: string; version: string }[];
  problems: ForgeProblem[];
  conversion_status: string;
  node_definitions: { source: "live" | "cache" | "bundled" | "none"; captured_at: string | null; comfyui_version: string | null; node_classes: number; note: string };
  app_builder: boolean;
  dependencies: ForgeDeps;
  weights: { total_bytes: number; measured: number; unmeasured: number; files: { file: string; bytes: number | null }[] };
}

export type ComponentType = "text" | "prompt" | "number" | "slider" | "select" | "toggle" | "seed" | "image_upload";
export interface SpecComponent {
  id: string; type: ComponentType; label: string; help: string; group: "main" | "advanced"; hidden: boolean;
  role?: "identity" | "layout" | "garment" | "style" | "other" | null;
  reason?: string | null;   // why the control exists, from the rule that chose it
  binding: { node: string; input: string };
  props: { min?: number | null; max?: number | null; step?: number | null; default?: string | number | boolean | null; choices?: (string | number | boolean)[] | null; rows?: number | null; placeholder?: string | null };
}
export interface UISpec {
  schema_version: number; name: string; description: string; workflow_id: string; workflow_sha256: string;
  layout: "simple_generator" | "reference_guided" | "advanced_workspace";
  components: SpecComponent[]; outputs: { node: string; media: string; label: string }[]; generated_by: "deterministic" | "user";
}
export interface ComponentVerdict { component: string; status: "verified" | "unresolved" | "invalid"; reasons: string[]; evidence: unknown }
export interface SpecValidation { status: "valid" | "needs_attention" | "invalid"; counts: Record<string, number>; components: ComponentVerdict[]; problems: string[]; runnable: boolean }
export interface SpecView { spec: UISpec; revision: number; source: "generated" | "saved"; stale: boolean; validation: SpecValidation; readiness: string; reasons: string[]; catalog: string[]; layouts: string[] }

export interface ImportPreview {
  name: string; format: string; bytes: number; sha256: string; nodes: number; subgraphs: number;
  unknown_node_types: string[]; unknown_count: number; already_imported: { id: string; name: string }[];
}

export const forgeApi = {
  spec: (id: string) => request<SpecView>("GET", `/api/forge/workflows/${id}/spec`),
  regenerate: (id: string) => request<SpecView>("POST", `/api/forge/workflows/${id}/spec/regenerate`),
  validateSpec: (id: string, spec: UISpec) => request<{ validation: SpecValidation }>("POST", `/api/forge/workflows/${id}/spec/validate`, { spec }),
  saveSpec: (id: string, spec: UISpec, base_revision: number) => request<{ revision: number; validation: SpecValidation }>("PUT", `/api/forge/workflows/${id}/spec`, { spec, base_revision }),
  list: () => request<{ workflows: ForgeWorkflow[] }>("GET", "/api/forge/workflows"),
  /** Stores a workflow file. A file already imported is not stored twice unless `copy` is true; then `duplicate` is true. */
  upload: async (file: File, copy = false): Promise<{ workflow: ForgeWorkflow; duplicate: boolean }> => {
    const r = await fetch(`/api/forge/workflows?name=${encodeURIComponent(file.name)}&copy=${copy}`, { method: "POST", body: file });
    const data = await r.json().catch(() => null);
    if (!r.ok) throw new ApiError(typeof data?.detail === "string" ? data.detail : `HTTP ${r.status}`, r.status);
    return data;
  },
  templates: () => request<{ available: boolean; reason?: string; source?: string; templates: ForgeTemplate[] }>("GET", "/api/forge/templates"),
  importTemplate: (name: string) => request<{ workflow: ForgeWorkflow }>("POST", "/api/forge/templates/import", { name }),
  /** Adds a reason to saved controls that have none. Labels, groups and values are kept. */
  addReasons: (id: string) => request<{ changed: string[]; revision: number }>("POST", `/api/forge/workflows/${id}/spec/add-reasons`),
  /** What a file contains, before it is imported. Nothing is stored. */
  preview: async (file: File): Promise<ImportPreview> => {
    const r = await fetch(`/api/forge/workflows/preview?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    const data = await r.json().catch(() => null);
    if (!r.ok) throw new ApiError(typeof data?.detail === "string" ? data.detail : "This file could not be read.", r.status);
    return data;
  },
  analysis: (id: string) => request<{ workflow: ForgeWorkflow; analysis: ForgeAnalysis }>("GET", `/api/forge/workflows/${id}/analysis`),
  rename: (id: string, name: string) => request<{ workflow: ForgeWorkflow }>("PATCH", `/api/forge/workflows/${id}`, { name }),
  remove: (id: string, confirm_name: string) => request<{ deleted: string }>("POST", `/api/forge/workflows/${id}/delete`, { confirm_name }),
  run: (id: string, values: Record<string, string | number | boolean>, provider: "local" | "cloud" = "local", consent?: { acknowledged: boolean; digest: string }) =>
    request<{ job: { id: string; state: string; notes: string[]; provider: string } }>("POST", `/api/forge/workflows/${id}/run`, { values, provider, consent: consent ?? null }),
  cloudPreview: (id: string, values: Record<string, string | number | boolean>) =>
    request<{ preview: { host: string; sends: { item: string; detail: string }[]; does_not_send: string[]; digest: string; cost_note: string }; status: { state: string; message: string } }>(
      "POST", `/api/forge/workflows/${id}/cloud-preview`, { values }),
  providers: () => request<{ local: { state: string; message: string }; cloud: { state: string; message: string }; default: string }>("GET", "/api/forge/providers"),
  runStatus: (jid: string) => request<{ id: string; state: string; error: string | null; notes: string[]; output_file: { name: string } | null; provider: string }>("GET", `/api/jobs/${jid}`),
  runOutputUrl: (jid: string) => `/api/forge/runs/${jid}/output`,
  runReceiptUrl: (jid: string) => `/api/forge/runs/${jid}/receipt`,
};

export interface LlmStatus { state: "ok" | "unreachable" | "no_models"; url: string; models: string[]; reason: string | null; default_model: string | null; timeout_seconds: number }
export interface ProposalResult { model: string; spec: UISpec; validation: SpecValidation; warnings: string[]; changed: string[]; source: "model" }

export const forgeModelApi = {
  status: () => request<LlmStatus>("GET", "/api/forge/llm/status"),
  propose: (id: string, body: { request: string; model?: string | null; current_spec?: UISpec | null }) =>
    request<ProposalResult>("POST", `/api/forge/workflows/${id}/spec/propose`, body),
};

export const forgePackageApi = {
  importPackage: async (file: File): Promise<{ workflow: ForgeWorkflow; spec_revision: number; package_forge_version: string | null }> => {
    const r = await fetch(`/api/forge/packages/import?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    const data = await r.json().catch(() => null);
    if (!r.ok) throw new ApiError(typeof data?.detail === "string" ? data.detail : `HTTP ${r.status}`, r.status);
    return data;
  },
  duplicate: (id: string) => request<{ workflow: ForgeWorkflow }>("POST", `/api/forge/workflows/${id}/duplicate`),
  exportBlob: async (id: string): Promise<Blob> => {
    const r = await fetch(`/api/forge/workflows/${id}/export`);
    if (!r.ok) {
      const data = await r.json().catch(() => null);
      throw new ApiError(typeof data?.detail === "string" ? data.detail : `HTTP ${r.status}`, r.status);
    }
    return r.blob();
  },
};

export interface ForgeDepRow {
  class: string; nodes: string[]; titles: string[]; available: boolean | null; availability_source: string;
  status: "registered" | "core" | "unchecked" | "missing" | "candidate" | "choose";
  confidence: string; candidates: { repository: string; title: string | null; match: string; source: string }[];
  candidate_count: number; installed: null; verification: string;
}
export interface ForgeDeps {
  headline: string; counts: Record<string, number>; requirements: ForgeDepRow[]; node_classes_needed: number;
  node_instances_needed: number; pack_index: string; custom_nodes_folders: number | null; availability_source: string; notes: string[];
}

export const forgeUploadApi = {
  upload: async (file: File): Promise<{ name: string; sha256: string; width: number; height: number; format: string }> => {
    const r = await fetch(`/api/forge/uploads?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    const data = await r.json().catch(() => null);
    if (!r.ok) throw new ApiError(typeof data?.detail === "string" ? data.detail : `HTTP ${r.status}`, r.status);
    return data;
  },
};

export interface RunCompare {
  same_app: boolean; same_workflow_file: boolean; spec_revision: { a: number | null; b: number | null };
  values: { name: string; a: unknown; b: unknown }[]; loras_same: boolean;
  repeatable: { a: { repeatable: boolean; why: string[] }; b: { repeatable: boolean; why: string[] } };
}

export const forgeRunsApi = {
  list: (id: string) => request<{ runs: { id: string; state: string; provider: string; created_at: string | null; has_output: boolean; error: string | null; receipt_url: string | null }[] }>("GET", `/api/forge/workflows/${id}/runs`),
  /** Runs the app again with the exact values of this run's receipt. */
  replay: (id: string) => request<{ job: { id: string }; replayed: string; spec_changed: boolean }>("POST", `/api/forge/runs/${id}/replay`),
  /** What differs between two runs, and whether each can be repeated exactly. */
  compare: (a: string, b: string) => request<RunCompare>("GET", `/api/forge/runs/${a}/compare/${b}`),
};

// MOSS-Image product posters (see backend/open_workflow/api_moss.py). Local only.
export type PosterBox = { type: "obj" | "text"; x: number; y: number; w: number; h: number; desc?: string; text?: string; palette?: string[] };
export type Poster = {
  id: string; state: "running" | "completed" | "failed"; created_at: string; finished_at: string | null;
  params: { prompt: string; headline: string; width: number; height: number; seed: number };
  output: { name: string; sha256: string } | null; receipt: string | null; message: string | null;
};
export type MossStatus = { state: "ready" | "not_running"; url: string; message: string };
export const mossApi = {
  status: () => request<MossStatus>("GET", "/api/moss/status"),
  create: (body: { prompt: string; headline: string; aspect: string; base: number; seed: number; boxes?: PosterBox[] }) =>
    request<{ poster: Poster }>("POST", "/api/moss/posters", body),
  get: (id: string) => request<{ poster: Poster }>("GET", `/api/moss/posters/${id}`),
  list: () => request<{ posters: { id: string; state: string; created_at: string; headline: string; prompt: string }[] }>("GET", "/api/moss/posters"),
  outputUrl: (id: string) => `/api/moss/posters/${id}/output`,
  receiptUrl: (id: string) => `/api/moss/posters/${id}/receipt`,
  aspects: () => request<{ aspects: { id: string; name: string; use: string; sizes: Record<string, [number, number]> }[]; bases: number[] }>("GET", "/api/moss/aspects"),
  presets: () => request<{ presets: { id: string; name: string; description: string; boxes: PosterBox[] }[] }>("GET", "/api/moss/layout-presets"),
  layout: (body: { boxes: PosterBox[]; aspect: string; base: number; prompt: string }) =>
    request<{ boxes: PosterBox[]; layout: unknown; message: string | null; warnings: string[][] }>("POST", "/api/moss/layout", body),
  suggest: (idea: string, engine: "fast" | "encoder") => request<{ request: PromptRequest }>("POST", "/api/moss/enhance", { idea, engine }),
  suggestion: (id: string) => request<{ request: PromptRequest }>("GET", `/api/moss/enhance/${id}`),
};
export type PromptRequest = { id: string; state: "running" | "done"; prompt?: string; source?: string; note?: string | null };

export interface ChainPort { name: string; type: "text" | "number" | "integer" | "boolean" | "select" | "image"; node: string; field: string; label: string; default: string | number | boolean | null; required: boolean; options: string[] | null }
export interface PipelineRun { id: string; state: "running" | "completed" | "failed"; message: string | null; stages: { id: string; state: string; job_id: string | null; message: string | null }[] }

export interface NavTab { app_id: string; label: string }

export const navTabsApi = {
  list: () => request<{ tabs: NavTab[] }>("GET", "/api/forge/nav-tabs"),
  save: (tabs: NavTab[]) => request<{ tabs: NavTab[] }>("PUT", "/api/forge/nav-tabs", { tabs }),
};

export const forgeChainApi = {
  createModule: (body: { workflow_id: string; name: string; inputs: ChainPort[]; outputs: { name: string; node: string; label: string }[] }) =>
    request<{ module: { module_id: string; version: number } }>("POST", "/api/forge/modules", body),
  createPipeline: (body: { name: string; stages: { id: string; module_id: string; version: number; values: Record<string, unknown> }[] }) =>
    request<{ pipeline: { id: string } }>("POST", "/api/forge/pipelines", body),
  runPipeline: (id: string) => request<{ run: PipelineRun }>("POST", `/api/forge/pipelines/${id}/run`),
  pipelineRun: (id: string) => request<{ run: PipelineRun }>("GET", `/api/forge/pipeline-runs/${id}`),
};

export interface OperatorEndpoint { id: string; label: string; base: string; kind: "ollama" | "openai"; state: "ok" | "no_models" | "unreachable"; models: string[]; reason: string | null }
export interface OperatorCard { kind: "run_app"; app_id: string; app_name: string; values: Record<string, string | number | boolean>; rejected: string[] }
export interface OperatorTurn { answer: string; cards: OperatorCard[]; trace: { tool: string; args: Record<string, unknown>; result: Record<string, unknown> }[] }

export const forgeOperatorApi = {
  endpoints: () => request<{ endpoints: OperatorEndpoint[] }>("GET", "/api/forge/operator/providers"),
  chat: (body: { endpoint: string; model: string; message: string; history: { role: "user" | "assistant"; content: string }[] }) =>
    request<OperatorTurn>("POST", "/api/forge/operator/chat", body),
};

export interface CharacterAnchor { comfy_name: string; path: string; sha256: string; width: number; height: number; original_name: string }
export interface CharacterCheck { score: number | null; threshold: number; passed: boolean; face_found: boolean; job_id: string; at: string }
export interface Character { id: string; name: string; notes: string; created_at: string; anchor: CharacterAnchor | null; history: CharacterCheck[] }

export const forgeCharacterApi = {
  list: () => request<{ characters: { id: string; name: string; created_at: string; anchor: boolean; checks: number }[] }>("GET", "/api/forge/characters"),
  create: (name: string, notes: string) => request<{ character: Character }>("POST", "/api/forge/characters", { name, notes }),
  get: (id: string) => request<{ character: Character }>("GET", `/api/forge/characters/${id}`),
  setAnchor: async (id: string, file: File): Promise<{ character: Character }> => {
    const r = await fetch(`/api/forge/characters/${id}/anchor?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    const data = await r.json().catch(() => null);
    if (!r.ok) throw new ApiError(typeof data?.detail === "string" ? data.detail : `HTTP ${r.status}`, r.status);
    return data;
  },
  check: (id: string, jobId: string) => request<{ check: CharacterCheck; character: Character }>("POST", `/api/forge/characters/${id}/check`, { job_id: jobId }),
};

export const forgeCreatorApi = {
  prompt: (body: { idea: string; reference_b64?: string | null; endpoint: string; model: string; base?: string | null; kind?: "ollama" | "openai" | null }) =>
    request<{ prompt: string; model: string; reference_used: boolean }>("POST", "/api/forge/creator/prompt", body),
  runs: (body: { workflow_id: string; prompt: string; count: number; seed_base: number }) =>
    request<{ jobs: { id: string; seed: number; state: string }[] }>("POST", "/api/forge/creator/runs", body),
  anchorFromRun: (cid: string, jobId: string) =>
    request<{ character: Character }>("POST", `/api/forge/characters/${cid}/anchor-from-run`, { job_id: jobId }),
};

export interface ShotPreset { id: string; label: string; headless: boolean; prompt: string }
export interface DatasetItem { id: string; job_id: string; preset: string | null; file: string; caption: string; added_at: string }

export const forgeShotApi = {
  presets: () => request<{ presets: ShotPreset[] }>("GET", "/api/forge/shots/presets"),
  dataset: (cid: string) => request<{ dataset: DatasetItem[] }>("GET", `/api/forge/characters/${cid}/dataset`),
  add: (cid: string, jobId: string, presetId: string | null) =>
    request<{ item: DatasetItem; character: Character }>("POST", `/api/forge/characters/${cid}/dataset`, { job_id: jobId, preset_id: presetId }),
  imageUrl: (cid: string, itemId: string) => `/api/forge/characters/${cid}/dataset/${itemId}/image`,
};
