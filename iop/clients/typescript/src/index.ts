export type Step = { id: string; device?: string; operation: string; arguments?: Record<string, unknown>; after?: string[]; resources?: string[]; requirements?: Record<string, number>; bindings?: Record<string, Source>; sweep?: Sweep; repeat?: number; until?: Until; retake?: true; retry?: { times: number; after_s?: number }; seek?: Seek };
/** A value an earlier step returned: path selects inside its result or, with view, inside a view of an array it returned. */
export type Source = { step: string; path?: (string | number)[]; view?: ArrayViewOptions };
/** An end of a range: a number, or one read from an earlier step when the sweep runs. */
export type End = number | Source;
/** Parameters a step runs over, one point after another: a list of values, or numbers from start to stop or around a center, evenly spaced or, with adapt (the path to the number each point reads), placed closer together where that reading changes most. */
export type Sweep = Record<string, (number | string)[] | ({ start: End; stop: End } | { center: End; span: End }) & { points: number; adapt?: (string | number)[] }>;
/** Stop a sweep after a point whose reading at path is above or below a limit, or is not a number, or with view once path in that view of what the sweep gathered is, read after `points` points; the later points are skipped and the steps after the sweep run. */
export type Until = { path: (string | number)[]; above?: number; below?: number; view?: ArrayViewOptions; points?: number };
/** Where a reading is largest or smallest, found by a compass search over the parameters in over; a number may be read from an earlier step as a binding is. */
export type Seek = { maximize?: (string | number)[]; minimize?: (string | number)[]; over: Record<string, { minimum: number | Source; maximum: number | Source; step: number | Source; start?: number | Source; resolution?: number | Source }>; points?: number; gain?: number };
export type Endpoint = { device: string; port: string };
export type Procedure = { steps: Step[]; resources?: string[] };
export type ConditionStep = { device?: string; operation: string; arguments?: Record<string, unknown> };
export type Tolerance = { value?: (string | number)[]; minimum?: number; maximum?: number; equals?: unknown };
export type TunedSetting = { device: string; operation: string; argument: string; arguments?: Record<string, unknown>; read: ConditionStep & { device: string; value?: (string | number)[] }; step: number; resolution?: number; minimum: number; maximum: number };
/** A correction the Runtime performs itself: adjust settings against the verification value until it is best. */
export type Tuning = { tune: TunedSetting | TunedSetting[]; goal?: 'maximize' | 'minimize' | 'center'; evaluations?: number; until?: 'optimum' | 'accept'; gain?: number };
/** What must hold on a platform for its experiments: how it is verified, its tolerance, what restores it and how long it stays valid. */
export type Condition = {
  id: string; description: string; kind?: 'environment' | 'equilibrium' | 'reference' | 'calibration' | 'alignment' | 'lock' | 'operating_point' | 'signal';
  after?: string[]; verify: ConditionStep; accept: Tolerance; usable?: Tolerance; reference?: Record<string, (string | number)[]>;
  correct?: ConditionStep | Tuning | (ConditionStep | Tuning)[]; settle?: number; valid_for?: number; note?: string;
};
export type ConditionState = { id: string; platform?: string; device?: string; description: string; kind?: string; after: string[]; state: 'holds' | 'due' | 'lost' | 'unknown'; reason?: string; value?: unknown; verified?: string; valid_until?: string; [key: string]: unknown };
/** Declared on a platform: the Runtime maintains these conditions when the platform is idle. */
export type Upkeep = { conditions?: string[]; ahead_s?: number; retry_s?: number; correct?: boolean };
export type UpkeepStatus = { platform?: string; run?: string; upkeep: Partial<Upkeep>; next: string | null; pending: Record<string, string>; waiting?: string; last?: { id: string; state: string; started: string; ended?: string; holds?: boolean; run_id: string } };
export type Maintenance = { id: string; platform: string | null; device?: string; state: string; complete: boolean; holds?: boolean; run_id: string; report: { condition: string; result: 'held' | 'verified' | 'corrected' | 'lost' | 'blocked'; value?: unknown; by?: string; found?: string }[]; error?: string };
export type PlatformDefinition = {
  schema_version: 1; id: string; name?: string; description?: string; devices: string[];
  resources?: { id: string; devices: string[]; description?: string; operations?: Record<string, string[]> }[];
  connections?: { from: Endpoint; to: Endpoint; kind: string; description?: string }[];
  procedures?: Record<string, Procedure>;
  conditions?: Condition[];
  upkeep?: Upkeep;
};
export type Platform = { id: string; revision: number; updated: string; definition: PlatformDefinition; active_runs?: string[]; missing_devices?: string[] };
/** Data that may have been taken while a condition did not hold: since its last accepted verification, until it was found lost or disturbed. */
export type Doubt = { since: string | null; found: string; outcome: string; reason?: string };
export type RunRecord = { id: string; state: string; result?: unknown; error?: string; platform?: Platform; operation_states?: Record<string, number>; in_doubt?: Record<string, Doubt>; estimated_end?: string; can_resume?: boolean; attempt?: number; [key: string]: unknown };
export type OperationRecord = RunRecord & { run_id: string; device: string; operation: string; device_revision?: number; maintenance?: boolean; retakes?: string; retaken_by?: string; attempts?: { state: string; error?: string; started: string; ended: string }[]; retry_at?: string; conditions?: Record<string, { accepted: string; value?: unknown; reference?: Record<string, unknown> }> };
export type BatchRecord = { request_id: string; run_id: string; complete: boolean; operations: OperationRecord[] };
export type ArtifactRecord = { id: string; run_id: string; name: string; size: number; media_type: string; created: string; operation_ids?: string[] };
export type Event = { sequence: number; kind: string; run_id?: string; [key: string]: unknown };
export type ManualTask = { operation_id: string; run_id: string | null; device: string; operation: string; instruction: string; arguments: Record<string, unknown>; readings: Record<string, Record<string, unknown>>; created: string };
export type RunUpdates = { run: RunRecord; changes: Event[]; operations: OperationRecord[]; cursor: number; has_more: boolean };
/** An axis a view follows: the positions selected and, when known, their coordinates. */
export type ArrayAxis = { axis: string | number; start: number; stop: number; low?: number; high?: number; step?: number; coordinates?: number[]; unit?: string };
/** What an array says: see Client.readArray. Along two axes, along is a pair and values and envelope are maps. */
export type ArrayView = {
  field: string; dtype: string; shape: number[]; unit?: string;
  along: ArrayAxis | ArrayAxis[];
  at?: Record<string, string | number>; summarized?: (string | number)[];
  summary: { count: number; nonfinite?: number; min: number | null; max: number | null; mean: number | null; std: number | null; error?: number | null;
    min_index?: number[]; max_index?: number[]; min_at?: number | (number | null)[]; max_at?: number | (number | null)[] };
  values?: (number | null)[] | (number | null)[][];
  envelope?: { position: number[] | number[][]; coordinate?: number[] | (number[] | null)[]; min: (number | null)[] | (number | null)[][];
    max: (number | null)[] | (number | null)[][]; mean: (number | null)[] | (number | null)[][] };
  /** With peak: the line's center between samples, its width where it is halfway to the base, the coordinates there, its value and base. */
  peak?: { center: number; width?: number; low?: number; high?: number; value: number; base: number; index: number } | null;
  /** With peak and prominence: every line that stands out by that much, in order of position, its width at half its prominence. */
  peaks?: { center: number; width?: number; low?: number; high?: number; value: number; base: number; prominence: number; index: number }[];
  /** With level: each place the trace crosses it, between samples, in order; index is the sample before. */
  crossings?: { at: number; rising: boolean; index: number }[];
  /** With fit "line": the straight line through the selection, its value at coordinate zero, where it reaches zero, and the root mean square it leaves; with "lorentzian" or "gaussian", a line of that shape, its full width at half height. */
  fit?: { slope: number; intercept: number; zero: number | null; residual: number; count: number; errors?: { slope: number | null; zero: number | null } }
    | { center: number; width: number; height: number; base: number; residual: number; count: number;
        errors?: { center: number | null; width: number | null; height: number | null; base: number | null } | null } | null;
  /** With one other axis left free: its coordinates or labels, and at each of its positions the peak, crossings and fit of the trace there. */
  rows?: { axis: string | number; unit?: string; coordinates?: number[]; labels?: string[]; peak?: (ArrayView['peak'])[];
    crossings?: NonNullable<ArrayView['crossings']>[]; fit?: (ArrayView['fit'])[] };
};
/** The URL query of an array view; at, and a list with one value per axis along, are sent as JSON. */
function viewQuery(options: ArrayViewOptions) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(options)) if (value !== undefined) query.set(key, key === 'at' || Array.isArray(value) ? JSON.stringify(value) : String(value));
  return query.size ? '?' + query : '';
}
type PerAxis<T> = T | (T | null)[];
export type ArrayViewOptions = { field?: string; along?: string | number | (string | number)[]; at?: Record<string, string | number>;
  start?: PerAxis<number>; stop?: PerAxis<number>; low?: PerAxis<number>; high?: PerAxis<number>; points?: number | number[]; peak?: 'max' | 'min'; prominence?: number; level?: number; fit?: 'line' | 'lorentzian' | 'gaussian' };
export type Submission = { name?: string; project?: string; experiment?: string; platform?: string; resources?: string[]; requirements?: Record<string, number>; parameters?: Record<string, unknown>; request_id?: string; requires?: string[]; upkeep?: boolean | Omit<Upkeep, 'conditions'> };
export class IOPError extends Error {
  constructor(message: string, readonly status: number, readonly details: Record<string, unknown> = {}) { super(message); this.name = 'IOPError'; }
  get code() { return this.details.code as string | undefined; }
}
const terminal = new Set(['completed', 'skipped', 'failed', 'cancelled', 'interrupted']);
const delay = (ms: number, signal?: AbortSignal): Promise<void> => new Promise((resolve, reject) => {
  if (signal?.aborted) { reject(signal.reason); return; }
  const abort = () => { clearTimeout(timer); reject(signal?.reason); };
  const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, ms);
  signal?.addEventListener('abort', abort, { once: true });
});
export class IOP {
  readonly endpoint: string;
  constructor(endpoint = 'http://127.0.0.1:8765', readonly token = '', readonly timeout = 30000) {
    this.endpoint = endpoint.replace(/\/$/, '');
  }
  async request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(this.endpoint + path, { method: body === undefined ? 'GET' : 'POST',
      headers: { 'Authorization': 'Bearer ' + this.token, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: signal ?? AbortSignal.timeout(this.timeout) });
    const value = await response.json();
    if (!response.ok) throw new IOPError(value.error ?? response.statusText, response.status, value);
    return value as T;
  }
  devices() { return this.request<Record<string, unknown>[]>('/v1/devices'); }
  context(refresh = false, detail: 'summary' | 'full' = 'summary') { return this.request<Record<string, unknown>>('/v1/context?' + new URLSearchParams({ refresh: String(refresh), detail })); }
  changes(after = 0, limit = 100, wait = 0) { return this.request<{changes: Event[]; cursor: number}>('/v1/context/changes?' + new URLSearchParams({ after: String(after), limit: String(limit), wait: String(wait) })); }
  devicePage(offset = 0, limit = 100) { return this.request('/v1/context/devices?' + new URLSearchParams({offset:String(offset),limit:String(limit)})); }
  deviceHistory(device: string) { return this.request<{id: string; revision: number; definition: Record<string, unknown>}[]>(`/v1/devices/${encodeURIComponent(device)}/history`); }
  configureDevice(device: string, definition: Record<string, unknown>, expectedRevision: number) { return this.request(`/v1/devices/${encodeURIComponent(device)}/configuration`, { definition, expected_revision: expectedRevision }); }
  schema(name: 'device' | 'platform' | 'workflow' | 'instrument-profile' | 'operation') { return this.request<Record<string, unknown>>('/v1/schemas/' + name); }
  discover() { return this.request<Record<string, unknown>>('/v1/discovery'); }
  startDiscovery() { return this.request<Record<string, unknown>>('/v1/discovery', {}); }
  inventory() { return this.request<Record<string, unknown>>('/v1/discovery?cached=true'); }
  register(record: Record<string, unknown>) { return this.request('/v1/devices', record); }
  instrumentProfiles() { return this.request('/v1/instrument-profiles'); }
  saveInstrumentProfile(profile: Record<string, unknown>) { return this.request('/v1/instrument-profiles', profile); }
  retryDiscovery(key?: string) { return this.request('/v1/discovery/retry', { key }); }
  async identify(device: string, requestId = crypto.randomUUID()): Promise<Run> {
    const record = await this.request<RunRecord>(`/v1/devices/${encodeURIComponent(device)}/identify`, { request_id: requestId });
    return this.run(record.id);
  }
  registerMany(devices: Record<string, unknown>[]) { return this.request('/v1/devices/batch', { devices }); }
  adapters() { return this.request<Record<string, unknown>[]>('/v1/adapters'); }
  adapterReference(name: string) { return this.request<Record<string, unknown>>('/v1/adapters?' + new URLSearchParams({ name })); }
  instrumentCatalog(options: { manufacturer?: string; model?: string; serial_number?: string; firmware?: string; id?: string; offset?: number; limit?: number; details?: boolean } = {}) {
    const query = new URLSearchParams(Object.entries(options).map(([key, value]) => [key, String(value)]));
    return this.request<{profiles: Record<string, unknown>[]; total: number; offset: number; next_offset: number | null; errors: Record<string, unknown>[]}>(`/v1/instrument-catalog?${query}`);
  }
  resources() { return this.request('/v1/resources'); }
  configureResources(capacities: Record<string, number>) { return this.request('/v1/resources', { capacities }); }
  reconnect(device: string) { return this.request('/v1/devices/' + encodeURIComponent(device) + '/reconnect', {}); }
  release(device: string) { return this.request('/v1/devices/' + encodeURIComponent(device) + '/release', {}); }
  resolveResources(resources: string[], observation: string) { return this.request<{released: string[]}>('/v1/resources/resolve', { resources, observation }); }
  manualTasks() { return this.request<ManualTask[]>('/v1/manual-tasks'); }
  reportManual(operationId: string, status: 'done' | 'failed' | 'unsure', readings: Record<string, unknown> = {}, note = '', by = '') {
    return this.request<{operation_id: string; status: string}>('/v1/manual-tasks/' + encodeURIComponent(operationId), { status, readings, note, by });
  }
  savedOperations() { return this.request<Record<string, unknown>[]>('/v1/library'); }
  savedOperation(id: string) { return this.request<Record<string, unknown>>('/v1/library/' + encodeURIComponent(id)); }
  /** Save a Standard or Workflow Operation; `program` is the ZIP of a Workflow Operation that decides from results. */
  saveOperation(definition: Record<string, unknown>, expectedRevision?: number, program?: Uint8Array) {
    let archive: string | undefined;
    if (program) {
      let text = '';
      for (let offset = 0; offset < program.length; offset += 8192) text += String.fromCharCode(...program.subarray(offset, offset + 8192));
      archive = btoa(text);
    }
    return this.request<Record<string, unknown>>('/v1/library', { definition, expected_revision: expectedRevision ?? null, archive });
  }
  programFile(id: string, name: string) {
    return this.request<{ text: string | null }>(`/v1/library/${encodeURIComponent(id)}/files/${name.split('/').map(encodeURIComponent).join('/')}`);
  }
  /** Summary, envelope or exact window of an array an operation returned, without moving all its values. */
  readArray(operationId: string, options: ArrayViewOptions = {}) {
    return this.request<ArrayView & {operation_id: string}>(`/v1/operations/${encodeURIComponent(operationId)}/array` + viewQuery(options));
  }
  /** A file the service makes on request, such as an array as .npy. */
  async file(path: string, signal?: AbortSignal): Promise<Response> {
    const response = await fetch(this.endpoint + path, { headers: { Authorization: 'Bearer ' + this.token }, signal: signal ?? AbortSignal.timeout(this.timeout) });
    if (!response.ok) {
      const text = await response.text();
      let message = response.statusText;
      try { message = JSON.parse(text).error ?? message; } catch { /* not JSON */ }
      throw new IOPError(message, response.status);
    }
    return response;
  }
  /** The whole array an operation returned as a NumPy .npy file; a last axis of re/im pairs becomes complex values. */
  arrayFile(operationId: string, field?: string, signal?: AbortSignal) {
    return this.file(`/v1/operations/${encodeURIComponent(operationId)}/array.npy` + viewQuery({ field }), signal);
  }
  /** Conditions with their state; `platform` adds its instruments' own conditions, `device` gives one instrument's. */
  conditions(platform?: string, device?: string) {
    const query = new URLSearchParams(Object.entries({ platform, device }).filter((entry): entry is [string, string] => Boolean(entry[1])));
    return this.request<ConditionState[]>('/v1/conditions' + (query.size ? '?' + query : ''));
  }
  condition(id: string) { return this.request<Condition & ConditionState & { statistics: Record<string, unknown>; records: Record<string, unknown>[] }>('/v1/conditions/' + encodeURIComponent(id)); }
  /** Verify the conditions that are due and correct those that drifted, in dependency order; resolves when the pass has ended. */
  async maintain(options: { platform?: string; device?: string; conditions?: string[]; runId?: string; requestId?: string; force?: boolean; signal?: AbortSignal } = {}): Promise<Maintenance> {
    let job = await this.request<Maintenance>('/v1/conditions/maintain?wait=25', {
      platform: options.platform, device: options.device, conditions: options.conditions, run_id: options.runId,
      request_id: options.requestId ?? crypto.randomUUID(), force: options.force ?? false }, options.signal);
    while (!job.complete) job = await this.request<Maintenance>(`/v1/maintenance/${encodeURIComponent(job.id)}?wait=25`, undefined, options.signal);
    return job;
  }
  /** Platforms whose conditions the Runtime keeps: settings, pending reasons, the next foreseen pass and the last one. */
  upkeep(platform?: string) { return this.request<UpkeepStatus[]>('/v1/upkeep' + (platform ? '?' + new URLSearchParams({ platform }) : '')); }
  invalidate(conditions: string[], reason: string) { return this.request<ConditionState[]>('/v1/conditions/invalidate', { conditions, reason }); }
  platforms() { return this.request<Platform[]>('/v1/platforms'); }
  platform(id: string) { return this.request<Platform>('/v1/platforms/' + encodeURIComponent(id)); }
  configurePlatform(definition: PlatformDefinition, expectedRevision = 0) {
    return this.request<Platform>('/v1/platforms', { definition, expected_revision: expectedRevision });
  }
  async procedure(platform: string, procedure = 'initialize', requestId = crypto.randomUUID()): Promise<Run> {
    const record = await this.request<RunRecord>(`/v1/platforms/${encodeURIComponent(platform)}/procedures/${encodeURIComponent(procedure)}`, { request_id: requestId });
    return this.run(record.id);
  }
  async submit(steps: Step[], options: Submission = {}): Promise<Run> {
    const record = await this.request<RunRecord>('/v1/runs', { ...options, steps, request_id: options.request_id ?? crypto.randomUUID() });
    return this.run(record.id);
  }
  /** What a step graph would do without running it: operations by instrument, the conditions it relies on and their state, and estimated_s at the median each kind took recently. */
  preview(steps: Step[], options: Omit<Submission, 'request_id'> = {}) {
    return this.request<{ preview: true; operations: number; by_operation: Record<string, { operations: number; median_s?: number; from?: number }>; estimated_s: number | null; requires: Record<string, string> }>('/v1/runs', { ...options, steps, preview: true });
  }
  async startExperiment(options: Submission = {}): Promise<Run> {
    const record = await this.request<RunRecord>('/v1/runs', { ...options, kind: 'external', request_id: options.request_id ?? crypto.randomUUID() });
    return this.run(record.id);
  }
  async submitPackage(archive: Uint8Array, options: Submission = {}): Promise<Run> {
    let text = '';
    for (let offset = 0; offset < archive.length; offset += 8192) text += String.fromCharCode(...archive.subarray(offset, offset + 8192));
    const record = await this.request<RunRecord>('/v1/packages', { ...options, archive: btoa(text), request_id: options.request_id ?? crypto.randomUUID() });
    return this.run(record.id);
  }
  run(id: string) { return new Run(this, id); }
  events(after = 0, runId?: string, wait = 0, signal?: AbortSignal) {
    const params = new URLSearchParams({ after: String(after), wait: String(wait) });
    if (runId) params.set('run_id', runId);
    return this.request<{ events: Event[]; cursor: number }>('/v1/events?' + params, undefined, signal);
  }
  async invoke(device: string, operation: string, args: Record<string, unknown> = {}, platform?: string) {
    const run = await this.submit([{ id: 'call', device, operation, arguments: args }], { platform });
    return ((await run.result()) as Record<string, unknown>).call;
  }
}
export class Run {
  constructor(readonly client: IOP, readonly id: string) {}
  info(details = true) { return this.client.request<RunRecord>('/v1/runs/' + encodeURIComponent(this.id) + (details ? '' : '?details=false')); }
  cancel() { return this.client.request<RunRecord>(`/v1/runs/${encodeURIComponent(this.id)}/cancel`, {}); }
  finish() { return this.client.request<RunRecord>(`/v1/runs/${encodeURIComponent(this.id)}/finish`, {}); }
  checkpoints() { return this.client.request<Record<string, {name: string; revision: number; updated: string; state: unknown}>>(`/v1/runs/${encodeURIComponent(this.id)}/checkpoints`); }
  checkpoint(state: unknown, name = 'main') { return this.client.request(`/v1/runs/${encodeURIComponent(this.id)}/checkpoints`, { name, state }); }
  resume(requestId = crypto.randomUUID()) { return this.client.request<RunRecord>(`/v1/runs/${encodeURIComponent(this.id)}/resume`, { request_id: requestId }); }
  /** A whole array this experiment's result holds, such as a sweep's map, as a NumPy .npy file; with requestId, one in that batch's results. */
  arrayFile(options: { field?: string; requestId?: string } = {}, signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (options.field !== undefined) query.set('field', options.field);
    if (options.requestId !== undefined) query.set('request_id', options.requestId);
    return this.client.file(`/v1/runs/${encodeURIComponent(this.id)}/array.npy` + (query.size ? '?' + query : ''), signal);
  }
  /** What an array this experiment's result holds says, such as what a sweep gathered; with requestId, one in that batch's results. */
  readArray(options: ArrayViewOptions & { requestId?: string } = {}) {
    const { requestId, ...view } = options;
    const query = viewQuery(view);
    return this.client.request<ArrayView & {run_id: string; request_id?: string}>(`/v1/runs/${encodeURIComponent(this.id)}/array` + query + (requestId === undefined ? '' : (query ? '&' : '?') + new URLSearchParams({ request_id: requestId })));
  }
  streams() { return this.client.request<{name: string; definition: Record<string, unknown>; chunks: number; bytes: number; next_sequence: number}[]>(`/v1/runs/${encodeURIComponent(this.id)}/streams`); }
  async stream(name: string, definition?: Record<string, unknown>) {
    const stream = new DataStream(this, name);
    if (definition) await this.client.request(stream.path, { definition });
    return stream;
  }
  async wait(options: { signal?: AbortSignal; interval?: number; details?: boolean } = {}): Promise<RunRecord> {
    let record = await this.info(false);
    while (!terminal.has(record.state)) {
      options.signal?.throwIfAborted();
      // The Runtime answers when the experiment's state changes, however many operations it runs meanwhile.
      const seconds = Math.min(25, Math.max(0, this.client.timeout / 1000 - 1));
      record = await this.client.request<RunRecord>(`/v1/runs/${encodeURIComponent(this.id)}?` + new URLSearchParams({ details: 'false', wait: String(seconds), state: record.state }), undefined, options.signal);
      if (!seconds) await new Promise((resolve) => setTimeout(resolve, options.interval ?? 100));
    }
    return options.details === false ? record : this.info();
  }
  async result(options: { signal?: AbortSignal; interval?: number } = {}): Promise<unknown> {
    const record = await this.wait({ ...options, details: false });
    if (record.state !== 'completed') throw new IOPError(`${record.state}: ${record.error ?? this.id}`, 0);
    return this.client.request(`/v1/runs/${encodeURIComponent(this.id)}/result`);
  }
  files() { return this.client.request<ArtifactRecord[]>(`/v1/runs/${encodeURIComponent(this.id)}/artifacts`); }
  filePage(after = 0, limit = 100) { return this.client.request<{files: ArtifactRecord[]; cursor: number; has_more: boolean}>(`/v1/runs/${encodeURIComponent(this.id)}/file-page?` + new URLSearchParams({after:String(after),limit:String(limit)})); }
  updates(after = 0, limit = 100, wait = 0, signal?: AbortSignal) { return this.client.request<RunUpdates>(`/v1/runs/${encodeURIComponent(this.id)}/updates?` + new URLSearchParams({after:String(after),limit:String(limit),wait:String(wait)}), undefined, signal); }
  /** A page of this experiment's operations; with inDoubt, only those a condition may have spoiled. */
  operations(after = 0, limit = 200, inDoubt = false) { return this.client.request<{ operations: OperationRecord[]; cursor: number }>(`/v1/runs/${encodeURIComponent(this.id)}/operations?` + new URLSearchParams({after:String(after),limit:String(limit),...(inDoubt ? {in_doubt:'true'} : {})})); }
  saveResult(result: unknown) { return this.client.request(`/v1/runs/${encodeURIComponent(this.id)}/result`, { result }); }
  async beginEpisode(id: string, definition: EpisodeDefinition) {
    await this.client.request(`/v1/runs/${encodeURIComponent(this.id)}/episodes`, {id, ...definition});
    return new Episode(this, id);
  }
  episode(id: string) { return new Episode(this, id); }
  episodes(offset = 0, limit = 100) {
    return this.client.request(`/v1/runs/${encodeURIComponent(this.id)}/episodes?` + new URLSearchParams({offset:String(offset),limit:String(limit)}));
  }
  saveAcquisition(name: string, operationIds: string[], conditions: Record<string, unknown> = {}, analysis: Record<string, unknown> = {}) {
    return this.client.request<ArtifactRecord>(`/v1/runs/${encodeURIComponent(this.id)}/acquisitions`, { name, operation_ids: operationIds, conditions, analysis });
  }
  async batch(steps: Step[], options: { requestId?: string; signal?: AbortSignal } = {}): Promise<OperationRecord[]> {
    const requestId = options.requestId ?? crypto.randomUUID();
    const wait = Math.min(25, Math.max(0, this.client.timeout / 1000 - 1));
    const path = `/v1/runs/${encodeURIComponent(this.id)}/batches`;
    let batch = await this.client.request<BatchRecord>(path + '?wait=' + wait, { steps, request_id: requestId }, options.signal);
    while (!batch.complete) {
      batch = await this.client.request<BatchRecord>(path + '?' + new URLSearchParams({request_id: requestId, wait: String(wait)}), undefined, options.signal);
    }
    if (batch.operations.some(op => op.state !== 'completed' && op.state !== 'skipped')) throw new IOPError('Operation batch did not complete', 0, batch);
    return batch.operations;
  }
  /** Use a saved Standard or Workflow Operation in this experiment and return its named result. */
  /** Inside this experiment, verify what it relies on and correct what drifted. */
  maintain(conditions?: string[], options: { force?: boolean; signal?: AbortSignal } = {}) {
    return this.client.maintain({ conditions, runId: this.id, ...options });
  }
  /** Use a saved operation and return its named result; with sweep, at each point, its named results gathered into arrays, stopping early with until. */
  /** Use a saved operation in this experiment and return its named result; the options of a step apply: sweep, repeat, until, seek and retry. */
  async call(operation: string, args: Record<string, unknown> = {}, options: { requestId?: string; signal?: AbortSignal } & Pick<Step, 'sweep' | 'repeat' | 'until' | 'seek' | 'retry'> = {}) {
    const requestId = options.requestId ?? crypto.randomUUID();
    const { sweep, repeat, until, seek, retry } = options;
    const given = Object.fromEntries(Object.entries({ sweep, repeat, until, seek, retry }).filter(([, value]) => value !== undefined));
    await this.batch([{ id: 'call', operation, arguments: args, ...given }], { requestId, signal: options.signal });
    const batch = await this.client.request<{ results: Record<string, Record<string, unknown>> }>(
      `/v1/runs/${encodeURIComponent(this.id)}/batches?` + new URLSearchParams({ request_id: requestId }), undefined, options.signal);
    return batch.results.call;
  }
  async invoke(device: string, operation: string, args: Record<string, unknown> = {}, options: { requestId?: string; signal?: AbortSignal } = {}) {
    return (await this.invokeRecord(device, operation, args, options)).result;
  }
  async invokeRecord(device: string, operation: string, args: Record<string, unknown> = {}, options: { requestId?: string; signal?: AbortSignal } = {}): Promise<OperationRecord> {
    const wait = Math.min(25, Math.max(0, this.client.timeout / 1000 - 1));
    let record = await this.client.request<OperationRecord>(`/v1/runs/${encodeURIComponent(this.id)}/operations?wait=${wait}`, {
      device, operation, arguments: args, request_id: options.requestId ?? crypto.randomUUID()
    }, options.signal);
    while (!terminal.has(record.state)) {
      record = await this.client.request<OperationRecord>('/v1/operations/' + encodeURIComponent(record.id) + '?wait=' + wait, undefined, options.signal);
    }
    if (record.state !== 'completed') throw new IOPError(record.error ?? record.state, 0);
    return record;
  }
  async upload(name: string, file: Blob, signal?: AbortSignal, operationIds: string[] = []) {
    const response = await fetch(this.client.endpoint + `/v1/runs/${encodeURIComponent(this.id)}/files?` + new URLSearchParams({ name, operation_ids: JSON.stringify(operationIds) }), {
      method: 'POST', headers: { Authorization: 'Bearer ' + this.client.token, 'Content-Type': 'application/octet-stream' },
      body: file, signal: signal ?? AbortSignal.timeout(this.client.timeout)
    });
    const value = await response.json();
    if (!response.ok) throw new IOPError(value.error ?? response.statusText, response.status);
    return value as ArtifactRecord;
  }
  /** The whole experiment as one ZIP: its records, its files and an RO-Crate description; read the response as a stream. */
  async archive(signal?: AbortSignal): Promise<Response> {
    const response = await fetch(this.client.endpoint + `/v1/runs/${encodeURIComponent(this.id)}/archive`, {
      headers: { Authorization: 'Bearer ' + this.client.token }, signal
    });
    if (!response.ok) throw new IOPError(response.statusText, response.status);
    return response;
  }
  async download(artifactId: string, signal?: AbortSignal): Promise<Response> {
    const response = await fetch(this.client.endpoint + '/v1/artifacts/' + encodeURIComponent(artifactId), {
      headers: { Authorization: 'Bearer ' + this.client.token }, signal: signal ?? AbortSignal.timeout(this.client.timeout)
    });
    if (!response.ok) throw new IOPError(response.statusText, response.status);
    return response;
  }
  async *watch(after = 0, signal?: AbortSignal): AsyncGenerator<Event> {
    let ended = false;
    for (;;) {
      signal?.throwIfAborted();
      const page = await this.client.events(after, this.id, Math.min(25, Math.max(0, this.client.timeout / 1000 - 1)), signal);
      for (const event of page.events) yield event;
      after = page.cursor;
      if (!page.events.length && terminal.has((await this.info(false)).state)) {
        if (ended) return;
        ended = true;
        continue;
      }
      if (!page.events.length) await delay(200, signal);
    }
  }
}
export class DataStream {
  readonly path: string;
  constructor(readonly run: Run, name: string) { this.path = `/v1/runs/${encodeURIComponent(run.id)}/streams/${encodeURIComponent(name)}`; }
  info() { return this.run.client.request<{name: string; definition: Record<string, unknown>; chunks: number; bytes: number; next_sequence: number}>(this.path); }
  chunks(after = -1, limit = 100) { return this.run.client.request<{chunks: Record<string, unknown>[]; cursor: number}>(this.path + '/chunks?' + new URLSearchParams({ after: String(after), limit: String(limit) })); }
  /** What a numeric stream (format such as float32-le) says, its chunks read as one array of samples by channels. */
  readArray(options: Omit<ArrayViewOptions, 'field'> = {}) { return this.run.client.request<ArrayView & {run_id: string; stream: string}>(this.path + '/array' + viewQuery(options)); }
  /** The whole samples of a numeric stream, by channels, as a NumPy .npy file. */
  arrayFile(signal?: AbortSignal) { return this.run.client.file(this.path + '/array.npy', signal); }
  async append(data: Blob, sequence: number, signal?: AbortSignal) {
    const response = await fetch(this.run.client.endpoint + this.path + '/chunks?sequence=' + sequence, {
      method: 'POST', headers: { Authorization: 'Bearer ' + this.run.client.token, 'Content-Type': 'application/octet-stream' },
      body: data, signal: signal ?? AbortSignal.timeout(this.run.client.timeout)
    });
    const value = await response.json();
    if (!response.ok) throw new IOPError(value.error ?? response.statusText, response.status, value);
    return value as {id: string; sequence: number; size: number};
  }
}


export type EpisodeDefinition = {
  task: {id: string; version: string}; observation: unknown;
  policy?: {id: string; version: string} | null; seed?: number | null; info?: Record<string, unknown>;
};
export type Transition = {
  sequence: number; action: unknown; observation: unknown; reward: number | null;
  terminated: boolean; truncated: boolean; operation_ids: string[]; info?: Record<string, unknown>;
};
export class Episode {
  readonly path: string;
  constructor(readonly run: Run, readonly id: string) {
    this.path = `/v1/runs/${encodeURIComponent(run.id)}/episodes/${encodeURIComponent(id)}`;
  }
  info() { return this.run.client.request(this.path); }
  record(transition: Transition) { return this.run.client.request(this.path + '/transitions', transition); }
  transitions(after = -1, limit = 100) {
    return this.run.client.request(this.path + '/transitions?' + new URLSearchParams({after:String(after),limit:String(limit)}));
  }
}
