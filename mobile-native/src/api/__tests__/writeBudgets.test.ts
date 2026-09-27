/**
 * Every long-running write must outlast the server it is talking to.
 *
 * `pulseApi` has two default budgets and both are guesses made from the *shape*
 * of a request: `PULSE_API_READ_TIMEOUT_MS` (15s), named for reads but silently
 * bounding every JSON write too, and a larger one picked by `body instanceof
 * FormData`. The server's ceiling is gunicorn's `--timeout`, which is 120s.
 *
 * **A client budget shorter than the server's ceiling cannot report anything but
 * failure.** The client is the one deciding, and it decides before the server has
 * had its allotted time — so a write that legitimately takes 20 or 150 seconds
 * and *completes* is reported to the user as a failure, and no amount of careful
 * copy downstream can recover the truth. Measured in production on 2026-09-27:
 * `POST …/dropshipping/connections/<id>/import` answered `200` after
 * `duration_ms=153968` with all 25 products committed, while the app had aborted
 * at 15,046ms and told the merchant "Nothing was imported — your cart is
 * unchanged."
 *
 * So this file is not about either feature. It is the one place that holds the
 * *relationship* between the two numbers, and it derives both ends rather than
 * pinning either:
 *
 * - The ceiling is read out of the `Procfile`, because raising gunicorn's
 *   `--timeout` to 300 would silently make today's budgets short again. A test
 *   pinning `120` would stay green through exactly that change.
 * - The budgets are read off the `timeoutMs` each function actually puts on the
 *   wire, so deleting the option — the whole of the original bug — fails here
 *   even though every screen test still passes.
 *
 * A write belongs in this file when the server is *allowed* to spend real time on
 * it: it walks a collection, calls a provider, or transcodes something. A write
 * that touches one row does not need a raised budget, and adding one here would
 * make the list meaningless.
 */

import { readFileSync } from "fs";
import { join } from "path";

const mockPulseApi = jest.fn();
jest.mock("../pulseApi", () => ({
  ...jest.requireActual("../pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import { importSelected } from "../dropshipping";
import { batchMarketplaceSellerListings } from "../marketplace";

/**
 * How long the server is permitted to spend on one request, in ms.
 *
 * Read from the `Procfile`'s `web:` line — the process that answers every one of
 * these calls. Its absence is a failure rather than a skip: a guard that quietly
 * stops checking is worse than no guard, because the summary still says it ran.
 */
function serverCeilingMs(): number {
  const procfile = readFileSync(join(__dirname, "..", "..", "..", "..", "Procfile"), "utf8");
  const web = procfile.split("\n").find((line) => line.startsWith("web:"));
  expect(web).toBeTruthy();
  const timeout = /--timeout\s+(\d+)/.exec(String(web));
  expect(timeout).toBeTruthy();
  return Number(timeout![1]) * 1000;
}

/** The `timeoutMs` the last `pulseApi` call carried, or `undefined` if none. */
function budgetOfLastCall(): number | undefined {
  expect(mockPulseApi).toHaveBeenCalled();
  const [, init] = mockPulseApi.mock.calls[mockPulseApi.mock.calls.length - 1] as [
    string,
    { timeoutMs?: number }
  ];
  return init?.timeoutMs;
}

beforeEach(() => {
  mockPulseApi.mockReset();
  // The transport is never reached and no caller's parsing is under test here —
  // every assertion is about the request. Failures are swallowed at each call
  // site so a parser that rejects a thin stub cannot look like a missing budget.
  mockPulseApi.mockResolvedValue({ ok: true, results: [], items: [] });
});

describe("a write the server is allowed to take its time over", () => {
  it("gives the dropshipping import longer than the server's own ceiling", async () => {
    await importSelected({ businessId: "biz-1", storeId: "store-1" }, "conn-1", {
      itemIds: ["item-1"]
    }).catch(() => undefined);

    const budget = budgetOfLastCall();
    expect(budget).toBeDefined();
    expect(budget!).toBeGreaterThan(serverCeilingMs());
  });

  it("gives a 200-listing marketplace batch longer than the server's own ceiling", async () => {
    await batchMarketplaceSellerListings({
      action: "publish",
      listingIds: Array.from({ length: 200 }, (_, at) => at + 1),
      idempotencyKey: "k-1"
    }).catch(() => undefined);

    const budget = budgetOfLastCall();
    expect(budget).toBeDefined();
    expect(budget!).toBeGreaterThan(serverCeilingMs());
  });

  /**
   * The ceiling this file compares against has to be the real one. If the
   * `Procfile` stops naming a gunicorn timeout — a switch to a different server,
   * or the flag simply dropped — then `serverCeilingMs` would be comparing
   * against nothing, and both tests above would pass for the wrong reason. This
   * is the assertion that the derivation still has something to derive.
   */
  it("reads a real ceiling out of the Procfile rather than assuming one", () => {
    const ceiling = serverCeilingMs();
    expect(Number.isFinite(ceiling)).toBe(true);
    expect(ceiling).toBeGreaterThanOrEqual(30_000);
  });
});
