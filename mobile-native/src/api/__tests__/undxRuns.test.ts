/**
 * The native run client, against the server it actually talks to.
 *
 * Two kinds of assertion live here and they are doing different jobs.
 *
 * The parity block parses `services/undx_run_status.py` and
 * `services/undx_agent_runs.py` and compares them against the constants in `undxRuns.ts`.
 * That follows `src/undx/__tests__/contractParity.test.ts`, for the reason given there: two
 * enums describing one thing in two languages do not stay in sync by intention, and the
 * divergence is silent in the direction that matters — the server starts emitting a state,
 * the app does not know it, and the app renders something anyway.
 *
 * The rest are behavioural, and every one of them is about a rounding this client must not
 * do: an unrecognised status must not become "queued", a missing `requires_disclosure` must
 * not become "no hedge needed", a 409 from cancel must not become a network error, and
 * stopping a watch must not read as the run having stopped.
 */

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

const mockPulseApi = jest.fn();

/**
 * The error class is defined *inside* the factory and read back out below.
 *
 * `cancelUndxRun` narrows with `instanceof PulseApiError`, so the class the test throws and
 * the class the module imports have to be the same object. Declaring it in the test body and
 * closing over it does not achieve that — `jest.mock` is hoisted above every import, the
 * module under test binds `PulseApiError` at import time, and the declaration has not been
 * evaluated yet, so the module binds `undefined` and `instanceof` throws. Owning it here and
 * pulling it back with `requireMock` makes the identity exact by construction.
 */
jest.mock("../pulseApi", () => {
  class PulseApiError extends Error {
    status: number;
    code?: string;
    details?: Record<string, unknown>;

    constructor(message: string, status: number, code?: string, details?: Record<string, unknown>) {
      super(message);
      this.name = "PulseApiError";
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }
  return { pulseApi: (...args: unknown[]) => mockPulseApi(...args), PulseApiError };
});

import { readFileSync } from "fs";
import { join } from "path";

const { PulseApiError: ApiError } = jest.requireMock("../pulseApi") as {
  PulseApiError: new (
    message: string,
    status: number,
    code?: string,
    details?: Record<string, unknown>
  ) => Error;
};

import {
  UNDX_CANCEL_RESULTS,
  UNDX_RUN_STATUSES,
  UNDX_TERMINAL_STATUSES,
  cancelUndxRun,
  fetchUndxRun,
  fetchUndxRuns,
  normalizeUndxRun,
  normalizeUndxRunStatus,
  undxRunStatusKey,
  undxUnknownServerStatuses,
  watchUndxRun
} from "../undxRuns";

const SERVICES = join(__dirname, "..", "..", "..", "..", "services");
const RUN_STATUS_PY = join(SERVICES, "undx_run_status.py");
const AGENT_RUNS_PY = join(SERVICES, "undx_agent_runs.py");

/** Every string literal assigned inside one `class Name:` block in a Python file. */
function pythonClassValues(source: string, className: string): string[] {
  const start = source.indexOf(`class ${className}:`);
  if (start === -1) throw new Error(`${className} not found`);
  const rest = source.slice(start + `class ${className}:`.length);
  const end = rest.search(/\nclass\s/);
  const body = end === -1 ? rest : rest.slice(0, end);
  const values = new Set<string>();
  const assignment = /^\s{4}[A-Z][A-Z0-9_]*\s*=\s*"([a-z0-9_]+)"/gm;
  let match: RegExpExecArray | null;
  while ((match = assignment.exec(body)) !== null) values.add(match[1]);
  return [...values].sort();
}

/** The members of a module-level `frozenset({RunStatus.X, ...})` binding. */
function pythonFrozenset(source: string, name: string): string[] {
  const start = source.indexOf(`${name} = frozenset({`);
  if (start === -1) throw new Error(`${name} not found`);
  const body = source.slice(start, source.indexOf("})", start));
  return [...new Set([...body.matchAll(/RunStatus\.([A-Z_]+)/g)].map((m) => m[1]))].sort();
}

/** A module-level `NAME = "value"` constant. */
function pythonConstant(source: string, name: string): string {
  const match = new RegExp(`^${name} = "([a-z0-9_]+)"`, "m").exec(source);
  if (!match) throw new Error(`${name} not found`);
  return match[1];
}

describe("UNDX run status parity with the server", () => {
  const runStatus = readFileSync(RUN_STATUS_PY, "utf8");
  const agentRuns = readFileSync(AGENT_RUNS_PY, "utf8");

  it("declares exactly the server's status vocabulary", () => {
    expect([...UNDX_RUN_STATUSES].sort()).toEqual(pythonClassValues(runStatus, "RunStatus"));
  });

  it("declares exactly the server's terminal set", () => {
    // Compared by constant *name* rather than by value, because that is how the Python
    // frozenset is written; the previous assertion already pins names to values.
    const names = pythonFrozenset(runStatus, "TERMINAL_STATUSES");
    const mine = [...UNDX_TERMINAL_STATUSES].map((s) => s.toUpperCase()).sort();
    expect(mine).toEqual(names);
  });

  it("does not treat unknown as terminal", () => {
    // The single most consequential entry in that set is the one that is absent. A client
    // that stopped polling an unreadable row would stop watching a live run.
    expect(UNDX_TERMINAL_STATUSES.has("unknown" as never)).toBe(false);
    expect(UNDX_RUN_STATUSES).toContain("unknown");
  });

  it("declares exactly the server's four cancel results", () => {
    const server = ["CANCEL_DONE", "CANCEL_NOT_FOUND", "CANCEL_ALREADY_SETTLED", "CANCEL_IN_FLIGHT"]
      .map((name) => pythonConstant(agentRuns, name))
      .sort();
    expect([...UNDX_CANCEL_RESULTS].sort()).toEqual(server);
  });

  it("has an i18n key for every status and never an English sentence", () => {
    UNDX_RUN_STATUSES.forEach((status) => {
      expect(undxRunStatusKey(status)).toBe(`undx.run.status.${status}`);
    });
    // The module must not carry user-visible English. `status_detail_en` and `message_en`
    // are named for what they are; anything else that looks like a sentence is a leak.
    const source = readFileSync(join(__dirname, "..", "undxRuns.ts"), "utf8");
    const code = source
      .replace(/\/\*\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    // Newline-excluded on purpose: a greedy `[^"]*` spans from one literal to the next
    // across lines and reports the code in between as prose.
    const sentences = [...code.matchAll(/"([^"\n]*[a-z]{2,} [a-z]{2,}[^"\n]*)"/g)].map((m) => m[1]);
    expect(sentences).toEqual([]);
  });
});

describe("nothing is rounded", () => {
  it("maps an unrecognised status to unknown rather than to queued or failed", () => {
    ["processing", "", "SUCCEEDED", "in_progress", "done"].forEach((raw) => {
      expect(normalizeUndxRunStatus(raw)).toBe("unknown");
    });
    expect(normalizeUndxRunStatus(undefined)).toBe("unknown");
    expect(normalizeUndxRunStatus(null)).toBe("unknown");
  });

  it("accepts every declared status, case- and whitespace-insensitively", () => {
    UNDX_RUN_STATUSES.forEach((status) => {
      expect(normalizeUndxRunStatus(` ${status.toUpperCase()} `)).toBe(status);
    });
  });

  it("hedges when the disclosure flag is missing", () => {
    // The inverse default would turn a truncated response into an unhedged claim about a
    // write that may never have landed.
    expect(normalizeUndxRun({ run_id: "r" }).requires_disclosure).toBe(true);
    expect(normalizeUndxRun({ requires_disclosure: false }).requires_disclosure).toBe(false);
  });

  it("claims no completion unless the server said so exactly", () => {
    expect(normalizeUndxRun({}).may_claim_completed).toBe(false);
    expect(normalizeUndxRun({ may_claim_completed: "true" }).may_claim_completed).toBe(false);
    expect(normalizeUndxRun({ may_claim_completed: 1 }).may_claim_completed).toBe(false);
    expect(normalizeUndxRun({ may_claim_completed: true }).may_claim_completed).toBe(true);
  });

  it("derives terminal from the status it recognised, not from the wire", () => {
    // A server that said "terminal" about a status this build cannot read must not be able
    // to make the app stop watching.
    const run = normalizeUndxRun({ status: "something_new", terminal: true });
    expect(run.status).toBe("unknown");
    expect(run.terminal).toBe(false);
  });

  it("keeps a completed run terminal even if the wire omits the flag", () => {
    expect(normalizeUndxRun({ status: "completed" }).terminal).toBe(true);
    expect(normalizeUndxRun({ status: "partial" }).terminal).toBe(true);
    expect(normalizeUndxRun({ status: "running" }).terminal).toBe(false);
  });

  it("never throws on a malformed row", () => {
    [null, undefined, 7, "run", [], { attempt: "x", max_attempts: -3 }].forEach((raw) => {
      const run = normalizeUndxRun(raw);
      expect(run.status).toBe("unknown");
      expect(run.attempt).toBe(0);
      expect(run.max_attempts).toBe(0);
    });
  });
});

describe("reading runs", () => {
  beforeEach(() => mockPulseApi.mockReset());

  it("sends no identity of any kind", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true, runs: [], limit: 20, statuses: [] });
    await fetchUndxRuns({ limit: 5 });
    const [path, options] = mockPulseApi.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/api/undx/runs?limit=5");
    expect(path).not.toContain("user");
    expect(options.method ?? "GET").toBe("GET");
    expect(options.body).toBeUndefined();
  });

  it("normalizes every row in a list", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      limit: 20,
      statuses: [...UNDX_RUN_STATUSES],
      runs: [
        { run_id: "run_a", status: "completed", may_claim_completed: true, requires_disclosure: false },
        { run_id: "run_b", status: "made_up" }
      ]
    });
    const result = await fetchUndxRuns();
    expect(result.runs.map((r) => r.status)).toEqual(["completed", "unknown"]);
    expect(result.runs[0].terminal).toBe(true);
    expect(result.runs[1].terminal).toBe(false);
    expect(undxUnknownServerStatuses(result.statuses)).toEqual([]);
  });

  it("reports a server vocabulary this build cannot render", () => {
    expect(undxUnknownServerStatuses([...UNDX_RUN_STATUSES, "escalated"])).toEqual(["escalated"]);
  });

  it("reads one run out of its envelope", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true, run: { run_id: "run_c", status: "running" } });
    const run = await fetchUndxRun("run_c");
    expect(run.run_id).toBe("run_c");
    expect(run.status).toBe("running");
    expect(mockPulseApi.mock.calls[0][0]).toBe("/api/undx/runs/run_c");
  });

  it("escapes a run id rather than interpolating it into a path", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true, run: {} });
    await fetchUndxRun("run_a/../../admin");
    expect(mockPulseApi.mock.calls[0][0]).toBe("/api/undx/runs/run_a%2F..%2F..%2Fadmin");
  });
});

describe("cancelling", () => {
  beforeEach(() => mockPulseApi.mockReset());

  it("posts and reports a stop", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      result: "cancelled",
      message: "Cancelled. That request will not run.",
      run_id: "run_d",
      status: "cancelled"
    });
    const outcome = await cancelUndxRun("run_d");
    expect(mockPulseApi.mock.calls[0][1]).toMatchObject({ method: "POST" });
    expect(outcome.result).toBe("cancelled");
    expect(outcome.stopped).toBe(true);
    expect(outcome.run?.status).toBe("cancelled");
  });

  it("treats a 409 as an answer, not a fault", async () => {
    // The specific bug this prevents: showing somebody a network error when what actually
    // happened is that their request had already completed.
    mockPulseApi.mockRejectedValueOnce(
      new ApiError("already", 409, undefined, {
        ok: false,
        result: "already_settled",
        message: "That request already finished.",
        run_id: "run_e",
        status: "completed"
      })
    );
    const outcome = await cancelUndxRun("run_e");
    expect(outcome.result).toBe("already_settled");
    expect(outcome.stopped).toBe(false);
    expect(outcome.run?.status).toBe("completed");
  });

  it("reports in-flight with the run attached so the caller keeps watching", async () => {
    mockPulseApi.mockRejectedValueOnce(
      new ApiError("running", 409, undefined, {
        ok: false,
        result: "in_flight",
        message: "That request is already running.",
        run_id: "run_f",
        status: "running"
      })
    );
    const outcome = await cancelUndxRun("run_f");
    expect(outcome.result).toBe("in_flight");
    expect(outcome.stopped).toBe(false);
    expect(outcome.run?.terminal).toBe(false);
  });

  it("passes a 404 through as not_found with no run", async () => {
    mockPulseApi.mockRejectedValueOnce(
      new ApiError("no such", 404, undefined, { ok: false, result: "not_found" })
    );
    const outcome = await cancelUndxRun("run_g");
    expect(outcome.result).toBe("not_found");
    expect(outcome.run).toBeUndefined();
  });

  it("rethrows a genuine fault rather than inventing an outcome", async () => {
    await expect(async () => {
      mockPulseApi.mockRejectedValueOnce(
        new ApiError("offline", 503, "request_unreachable")
      );
      await cancelUndxRun("run_h");
    }).rejects.toThrow("offline");

    await expect(async () => {
      mockPulseApi.mockRejectedValueOnce(
        new ApiError("boom", 500, undefined, { ok: false, message: "Could not cancel." })
      );
      await cancelUndxRun("run_i");
    }).rejects.toThrow("boom");
  });

  it("fails closed on a result code it does not recognise", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true, result: "deferred", run_id: "run_j", status: "queued" });
    const outcome = await cancelUndxRun("run_j");
    expect(outcome.stopped).toBe(false);
    expect(outcome.result).toBe("in_flight");
  });
});

describe("watching does not drive the run", () => {
  beforeEach(() => {
    mockPulseApi.mockReset();
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  const flush = async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  };

  it("polls until the run is terminal and then stops", async () => {
    mockPulseApi
      .mockResolvedValueOnce({ ok: true, run: { run_id: "run_k", status: "queued" } })
      .mockResolvedValueOnce({ ok: true, run: { run_id: "run_k", status: "running" } })
      .mockResolvedValueOnce({ ok: true, run: { run_id: "run_k", status: "completed" } });

    const seen: string[] = [];
    const watch = watchUndxRun("run_k", { intervalMs: 1_000, onUpdate: (r) => seen.push(r.status) });

    await flush();
    jest.advanceTimersByTime(1_000);
    await flush();
    jest.advanceTimersByTime(1_000);
    await flush();

    const final = await watch.done;
    expect(seen).toEqual(["queued", "running", "completed"]);
    expect(final?.status).toBe("completed");

    // Nothing further is requested once it settled.
    jest.advanceTimersByTime(10_000);
    await flush();
    expect(mockPulseApi).toHaveBeenCalledTimes(3);
  });

  it("stops polling on stop() and never claims the run stopped", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, run: { run_id: "run_l", status: "running" } });
    const watch = watchUndxRun("run_l", { intervalMs: 1_000 });
    await flush();
    watch.stop();

    // `null`, not a cancelled run. Tearing down a screen says nothing about the work, and
    // resolving anything status-shaped here would let a caller render "stopped".
    await expect(watch.done).resolves.toBeNull();

    const calls = mockPulseApi.mock.calls.length;
    jest.advanceTimersByTime(30_000);
    await flush();
    expect(mockPulseApi).toHaveBeenCalledTimes(calls);
  });

  it("does not issue any write while watching", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, run: { run_id: "run_m", status: "queued" } });
    const watch = watchUndxRun("run_m", { intervalMs: 1_000 });
    await flush();
    jest.advanceTimersByTime(2_000);
    await flush();
    watch.stop();
    await watch.done;

    // The phone must not keep the job alive: no heartbeat, no lease extension, no POST.
    mockPulseApi.mock.calls.forEach(([, options]) => {
      const method = String((options as Record<string, unknown> | undefined)?.method || "GET");
      expect(method.toUpperCase()).toBe("GET");
    });
  });

  it("keeps watching through a failed read", async () => {
    mockPulseApi
      .mockRejectedValueOnce(new ApiError("offline", 503, "request_unreachable"))
      .mockResolvedValueOnce({ ok: true, run: { run_id: "run_n", status: "failed" } });

    const errors: unknown[] = [];
    const watch = watchUndxRun("run_n", { intervalMs: 1_000, onError: (e) => errors.push(e) });
    await flush();
    jest.advanceTimersByTime(1_000);
    await flush();

    const final = await watch.done;
    expect(errors).toHaveLength(1);
    expect(final?.status).toBe("failed");
  });

  it("gives up watching an unreadable run without calling it finished", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, run: { run_id: "run_o", status: "who_knows" } });
    const watch = watchUndxRun("run_o", { intervalMs: 1_000, maxDurationMs: 2_000 });
    await flush();
    jest.advanceTimersByTime(1_000);
    await flush();
    jest.advanceTimersByTime(1_000);
    await flush();

    const final = await watch.done;
    // The deadline bounds the watching, not the run. The last state is reported as it was:
    // unknown and not terminal, which is the honest "it was not observed to finish".
    expect(final?.status).toBe("unknown");
    expect(final?.terminal).toBe(false);
  });
});
