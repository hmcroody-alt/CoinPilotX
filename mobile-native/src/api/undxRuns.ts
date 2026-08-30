/**
 * Durable agent runs, from the phone's side.
 *
 * When UNDX defers an action to the worker, the turn answers `accepted_queued` and hands
 * back a `run_id`. Everything that happens after that happens somewhere else — in a
 * container the phone has no connection to, over a span of time that outlives the screen
 * that started it. This module is the whole of the app's relationship with that: it can
 * list those runs, read one, and cancel one that has not started.
 *
 * Three properties are load-bearing, and each of them is a thing the obvious client would
 * get wrong.
 *
 * **The phone does not keep the job alive.** Nothing here starts, resumes, extends or
 * heartbeats a run. {@link watchUndxRun} is a *reader* — it polls a status endpoint and
 * calls back — and stopping it stops the polling and nothing else. That is the whole point
 * of the worker: the person can background the app, lose signal, or force-quit, and the
 * action still happens. A client that treated its own polling loop as the run's lifeline
 * would have quietly reintroduced the problem the queue was built to remove.
 *
 * **No status is rounded.** The server declares twelve states and refuses to compress them
 * to "processing" (see `services/undx_run_status.py`); this module refuses to compress them
 * either. In particular {@link normalizeUndxRun} maps an unrecognised status to
 * `"unknown"` rather than to `"queued"` or `"failed"`, because both of those are claims
 * about the person's account that nothing supports, and `"unknown"` is deliberately *not*
 * terminal, so a client watching such a run keeps watching it.
 *
 * **Two booleans, not one, decide what may be said.** `may_claim_completed` is "may a
 * change be reported as done"; `requires_disclosure` is "must the sentence carry a hedge".
 * They are not the same question and a successful read separates them — it completes
 * nothing, so the first is false, and it needs no hedge, so the second is false too. Both
 * default to the cautious reading when the field is missing, which for `requires_disclosure`
 * means `true`. See {@link UndxRun}.
 *
 * **No user-visible English lives here.** The server sends `status_detail`, which is an
 * English sentence for logs and support, and this module carries it under a name that says
 * so. What a screen should render is {@link undxRunStatusKey}, an i18n key per status. The
 * keys are not in the catalogs yet — no screen consumes them — and adding them is the first
 * step of building that screen, in all twelve locales at once, rather than a debt this file
 * quietly takes on by hardcoding English.
 */

import { PulseApiError, pulseApi } from "./pulseApi";

/**
 * The complete status vocabulary, in the server's declaration order.
 *
 * Mirrors `RunStatus` in `services/undx_run_status.py`. A test in `__tests__` parses that
 * Python file and asserts equality, following `src/undx/__tests__/contractParity.test.ts`:
 * two enums describing one thing in two languages do not stay in sync by intention, and
 * the divergence that matters is silent — the server starts emitting a state, the app does
 * not know it, and the app renders something anyway.
 */
export const UNDX_RUN_STATUSES = [
  "queued",
  "claimed",
  "running",
  "waiting_confirmation",
  "verifying",
  "retry_wait",
  "partial",
  "completed",
  "failed",
  "cancelled",
  "expired",
  "unknown"
] as const;

export type UndxRunStatus = (typeof UNDX_RUN_STATUSES)[number];

/**
 * Statuses after which nothing further will happen without a new request.
 *
 * `"unknown"` is absent on purpose, exactly as it is absent from the server's
 * `TERMINAL_STATUSES`: a row that could not be read has not been shown to be finished, and
 * a client that stopped polling it would stop watching a run that is still live.
 */
export const UNDX_TERMINAL_STATUSES: ReadonlySet<UndxRunStatus> = new Set<UndxRunStatus>([
  "partial",
  "completed",
  "failed",
  "cancelled",
  "expired"
]);

/** What `POST /api/undx/runs/{id}/cancel` concluded. Mirrors the `CANCEL_*` codes. */
export const UNDX_CANCEL_RESULTS = [
  "cancelled",
  "not_found",
  "already_settled",
  "in_flight"
] as const;

export type UndxCancelResult = (typeof UNDX_CANCEL_RESULTS)[number];

/** One durable run, normalized. Field for field, what `_present()` sends. */
export type UndxRun = {
  run_id: string;
  capability_id: string;
  /** The thing acted on — which alert, which post. Already the person's own. */
  target_id: string;
  status: UndxRunStatus;
  /**
   * The server's English sentence for this status. For logs, support tickets and
   * debugging — not for rendering. Use {@link undxRunStatusKey} with `t()` instead.
   */
  status_detail_en: string;
  /** Recomputed locally from {@link UNDX_TERMINAL_STATUSES}, not taken from the wire. */
  terminal: boolean;
  /**
   * Whether a *change* may be reported as done. False for a healthy read, because a lookup
   * completes nothing. Defaults to `false` when absent.
   */
  may_claim_completed: boolean;
  /**
   * Whether the sentence must be hedged. Defaults to `true` when absent: a run whose
   * disclosure requirement could not be read is one to hedge about.
   */
  requires_disclosure: boolean;
  /** The raw gateway outcome, alongside the projection rather than instead of it. */
  outcome: string;
  confirmation_state: string;
  dispatch_reason: string;
  attempt: number;
  max_attempts: number;
  /** Identifies the body an approval was bound to. Not the arguments themselves. */
  arguments_hash: string;
  created_at: string;
  updated_at: string;
  completed_at: string;
  expires_at: string;
  /** A stable error code the worker recorded. Never an exception message. */
  error: string;
};

export type UndxRunListResult = {
  runs: UndxRun[];
  limit: number;
  /**
   * The vocabulary the server sent with the list. Compared against
   * {@link UNDX_RUN_STATUSES} by {@link undxUnknownServerStatuses} so that a server ahead
   * of the app is a condition the app can detect rather than one it renders through.
   */
  statuses: string[];
};

export type UndxCancelOutcome = {
  result: UndxCancelResult;
  /** True only for `"cancelled"`. The other three are answers, not successes. */
  stopped: boolean;
  /** The server's English sentence. Same caveat as `status_detail_en`. */
  message_en: string;
  /**
   * The run's state after the attempt, when the server could read it back. Present on all
   * four results except a `"not_found"`, which by definition has no row to describe.
   */
  run?: UndxRun;
};

const RUNS_PATH = "/api/undx/runs";
const STATUS_SET: ReadonlySet<string> = new Set<string>(UNDX_RUN_STATUSES);
const CANCEL_SET: ReadonlySet<string> = new Set<string>(UNDX_CANCEL_RESULTS);

function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : String(value);
}

function count(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0;
}

/**
 * A wire status as one of the twelve, or `"unknown"`.
 *
 * The fallback is the entire reason this function exists. An unrecognised status means the
 * server knows something this build does not, and the two tempting ways to absorb that —
 * treat it as still queued, treat it as failed — are both statements about whether the
 * person's action happened. `"unknown"` asserts neither and keeps the run under watch.
 */
export function normalizeUndxRunStatus(value: unknown): UndxRunStatus {
  const raw = text(value).trim().toLowerCase();
  return STATUS_SET.has(raw) ? (raw as UndxRunStatus) : "unknown";
}

/**
 * The i18n key for a status. Never a rendered string.
 *
 * Total by construction: an unrecognised status resolves through
 * {@link normalizeUndxRunStatus} to `"unknown"`, which has its own key, so there is no
 * input for which a screen is left with nothing to draw.
 */
export function undxRunStatusKey(status: unknown): string {
  return `undx.run.status.${normalizeUndxRunStatus(status)}`;
}

/** One wire object as an {@link UndxRun}. Never throws; never returns a partial object. */
export function normalizeUndxRun(raw: unknown): UndxRun {
  const row = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const status = normalizeUndxRunStatus(row.status);
  return {
    run_id: text(row.run_id),
    capability_id: text(row.capability_id),
    target_id: text(row.target_id),
    status,
    status_detail_en: text(row.status_detail),
    // Recomputed rather than trusted. The server sends `terminal`, and it will agree — but
    // the app stops polling on this field, and "stop watching a live run" is the failure
    // that costs a person their answer. Deriving it from the status the app itself
    // recognised keeps the two decisions, "which state is this" and "is it over", from
    // being able to disagree.
    terminal: UNDX_TERMINAL_STATUSES.has(status),
    may_claim_completed: row.may_claim_completed === true,
    // Absent means hedge. The inverse default would turn a truncated response into an
    // unhedged claim about a write.
    requires_disclosure: row.requires_disclosure !== false,
    outcome: text(row.outcome),
    confirmation_state: text(row.confirmation_state),
    dispatch_reason: text(row.dispatch_reason),
    attempt: count(row.attempt),
    max_attempts: count(row.max_attempts),
    arguments_hash: text(row.arguments_hash),
    created_at: text(row.created_at),
    updated_at: text(row.updated_at),
    completed_at: text(row.completed_at),
    expires_at: text(row.expires_at),
    error: text(row.error)
  };
}

/**
 * Statuses the server listed that this build cannot render.
 *
 * Empty in a matched deployment. Non-empty means the web service is ahead of the app, which
 * is a supportable state — those runs arrive as `"unknown"` and stay under watch — and one
 * worth logging rather than discovering from a screenshot.
 */
export function undxUnknownServerStatuses(statuses: readonly string[]): string[] {
  return statuses.filter((name) => !STATUS_SET.has(text(name).trim().toLowerCase()));
}

/**
 * This account's runs, newest first.
 *
 * There is no `user_id` parameter, here or on the server. The owner comes from the session,
 * so there is nothing a caller could pass that would read somebody else's runs.
 */
export async function fetchUndxRuns(
  options: { limit?: number; signal?: AbortSignal } = {}
): Promise<UndxRunListResult> {
  const query = options.limit ? `?limit=${encodeURIComponent(String(options.limit))}` : "";
  const data = await pulseApi<Record<string, unknown>>(`${RUNS_PATH}${query}`, {
    signal: options.signal
  });
  const rows = Array.isArray(data.runs) ? data.runs : [];
  const statuses = Array.isArray(data.statuses) ? data.statuses.map(text) : [];
  return {
    runs: rows.map(normalizeUndxRun),
    limit: count(data.limit),
    statuses
  };
}

/**
 * One run of this account's.
 *
 * A run that does not exist and a run belonging to somebody else both answer 404, which
 * arrives as a {@link PulseApiError}. That is the server's design and not a shortfall here:
 * separating them would confirm whether an arbitrary run id is real.
 */
export async function fetchUndxRun(
  runId: string,
  options: { signal?: AbortSignal } = {}
): Promise<UndxRun> {
  const data = await pulseApi<Record<string, unknown>>(
    `${RUNS_PATH}/${encodeURIComponent(runId)}`,
    { signal: options.signal }
  );
  return normalizeUndxRun(data.run);
}

/**
 * Ask for a queued run to be stopped.
 *
 * **Three of the four answers arrive as HTTP errors, and none of them is a fault.** A run
 * that already finished answers 409, one already executing answers 409, and an unknown id
 * answers 404. `pulseApi` turns every non-2xx into a thrown {@link PulseApiError}, so a
 * caller left to `try/catch` would be deciding, at each call site, which thrown errors are
 * really outcomes — and the cost of getting that wrong is showing somebody a network error
 * when what actually happened is that their request had already completed. So this resolves
 * for all four and throws only for the rest.
 *
 * **"Cancelled" is not a promise that nothing happened.** It is only reachable for a run no
 * worker has claimed. A run already in flight cannot be recalled, and this returns
 * `"in_flight"` with the run's current state attached so the caller can go on watching it
 * rather than telling the person something untrue about their own account.
 */
export async function cancelUndxRun(runId: string): Promise<UndxCancelOutcome> {
  const path = `${RUNS_PATH}/${encodeURIComponent(runId)}/cancel`;
  try {
    const data = await pulseApi<Record<string, unknown>>(path, { method: "POST" });
    return cancelOutcome(data);
  } catch (error) {
    if (error instanceof PulseApiError && error.details && isCancelBody(error.details)) {
      return cancelOutcome(error.details);
    }
    throw error;
  }
}

/** Whether a body carries one of the four codes, i.e. is an answer rather than a fault. */
function isCancelBody(body: Record<string, unknown>): boolean {
  return CANCEL_SET.has(text(body.result).trim().toLowerCase());
}

function cancelOutcome(body: Record<string, unknown>): UndxCancelOutcome {
  const raw = text(body.result).trim().toLowerCase();
  // A 200 whose `result` this build does not recognise is the one case with no honest
  // reading. `"in_flight"` is the fail-closed choice: it claims nothing was stopped and
  // leaves the caller watching the run, which is what an unrecognised answer warrants.
  const result: UndxCancelResult = CANCEL_SET.has(raw) ? (raw as UndxCancelResult) : "in_flight";
  const outcome: UndxCancelOutcome = {
    result,
    stopped: result === "cancelled",
    message_en: text(body.message)
  };
  if (text(body.run_id)) outcome.run = normalizeUndxRun(body);
  return outcome;
}

/** How often {@link watchUndxRun} re-reads, and how long it will keep doing so. */
export const UNDX_RUN_POLL_INTERVAL_MS = 3_000;
export const UNDX_RUN_POLL_MAX_MS = 10 * 60 * 1_000;

export type UndxRunWatchOptions = {
  intervalMs?: number;
  maxDurationMs?: number;
  /** Called on every successful read, including the one that resolves the watch. */
  onUpdate?: (run: UndxRun) => void;
  /**
   * Called when a read fails. The watch continues: a dropped poll says nothing about the
   * run, and a watch that gave up on the first network blip would abandon work that is
   * still going. Omit it and read failures are silent.
   */
  onError?: (error: unknown) => void;
};

/**
 * Watch a run until it settles. Returns a stop function.
 *
 * **This does not drive the run.** It reads. Calling the returned function stops the
 * polling and has no effect whatsoever on the work — for that, see {@link cancelUndxRun},
 * and note that it only works before a worker has started. Unmounting a screen, closing the
 * app or losing the network all stop the watching and none of them stop the run. That
 * asymmetry is the feature.
 *
 * The deadline in {@link UNDX_RUN_POLL_MAX_MS} bounds the *watching*, not the run. It exists
 * because `"unknown"` is deliberately non-terminal, so a row this build cannot read would
 * otherwise be polled forever; when it trips, the last known state is returned unchanged and
 * still non-terminal, which is the honest report — the run was not observed to finish.
 */
export function watchUndxRun(
  runId: string,
  options: UndxRunWatchOptions = {}
): { stop: () => void; done: Promise<UndxRun | null> } {
  const intervalMs = Math.max(1_000, options.intervalMs ?? UNDX_RUN_POLL_INTERVAL_MS);
  const deadline = Date.now() + Math.max(intervalMs, options.maxDurationMs ?? UNDX_RUN_POLL_MAX_MS);
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let settle: (run: UndxRun | null) => void = () => undefined;

  const done = new Promise<UndxRun | null>((resolve) => {
    settle = resolve;
  });

  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (timer) clearTimeout(timer);
    timer = null;
    // Resolves `null` rather than rejecting. A caller that stopped watching on purpose has
    // not encountered an error, and making teardown throw would put a `catch` on every
    // screen that ever watches a run.
    settle(null);
  };

  const tick = async () => {
    if (stopped) return;
    let latest: UndxRun | null = null;
    try {
      latest = await fetchUndxRun(runId);
    } catch (error) {
      options.onError?.(error);
    }
    if (stopped) return;
    if (latest) {
      options.onUpdate?.(latest);
      if (latest.terminal) {
        stopped = true;
        settle(latest);
        return;
      }
    }
    if (Date.now() >= deadline) {
      stopped = true;
      settle(latest);
      return;
    }
    timer = setTimeout(tick, intervalMs);
  };

  void tick();
  return { stop, done };
}
