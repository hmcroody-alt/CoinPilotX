/**
 * The Payments gap ledger, held to its own count.
 *
 * `PAYMENTS_MOCK_DATA_GAPS` names every field the money screen would draw if a
 * source existed for it, and for each one the behaviour the client chose
 * instead — which is almost always "render the module absent". Eight of the nine
 * MOCK-DATA tables in this app are pinned by a test. This one was not pinned by
 * anything, so a row could be added, edited or quietly deleted with the suite
 * still green, and the count a completion report quotes was a number nobody had
 * checked.
 *
 * That mattered more here than anywhere else for two reasons. These are the
 * money rows: closing one by faking it changes a figure a seller reads as their
 * own balance. And one of the nine is not a data gap at all — step-up
 * authentication is a declared *security* gap, and it is a hard precondition on
 * all six Payments flags. The least protected line in the ledger was the one
 * that gates the other six.
 *
 * So this file locks the length against a literal, the same way Store, Insights,
 * Ads, Orders and Marketplace lock theirs. The number moving is not a failure in
 * itself; the point is that it cannot move without somebody editing this line
 * and saying which it was — a gap closed by a real source, or a gap papered over.
 */
import {
  PAYMENTS_MOCK_DATA_GAPS,
  PAYMENTS_MOCK_DATA_GAP_COUNT,
  adTopUpIsLive,
  escrowCardIsLive,
  instantPayoutIsLive,
  payoutInitiationIsLive,
  statementsAreLive,
  taxDocumentsAreLive
} from "../paymentsHub";

describe("PAYMENTS_MOCK_DATA_GAPS", () => {
  it("names every money field with no source, and the count is pinned", () => {
    expect(PAYMENTS_MOCK_DATA_GAPS).toHaveLength(9);
    expect(PAYMENTS_MOCK_DATA_GAP_COUNT).toBe(9);
    expect(PAYMENTS_MOCK_DATA_GAPS.map((gap) => gap.field)).toEqual([
      "next payout date",
      "available balance becoming non-zero",
      "masked bank destination",
      "instant payout fee and net",
      "held in escrow",
      "statements and tax documents",
      "ad wallet top-up and auto top-up",
      "refund response deadline",
      "step-up authentication"
    ]);
  });

  it("says, for every gap, why it exists and what the screen does instead", () => {
    for (const gap of PAYMENTS_MOCK_DATA_GAPS) {
      expect(gap.field.length).toBeGreaterThan(0);
      // A gap with no stated reason is a shrug, and one with no stated client
      // behaviour leaves the next reader to guess whether the module is absent,
      // disabled or showing a placeholder — the distinction this screen is built on.
      expect(gap.why.length).toBeGreaterThan(20);
      expect(gap.clientBehaviour.length).toBeGreaterThan(20);
    }
  });

  it("keeps step-up authentication in the ledger as a security gap", () => {
    // Pinned by itself because it is the precondition on all six flags below.
    // If this row is ever deleted, that has to be because a re-authentication
    // primitive was built, and this assertion is where somebody says so.
    const stepUp = PAYMENTS_MOCK_DATA_GAPS.find((gap) => gap.field === "step-up authentication");
    expect(stepUp).toBeDefined();
    expect(stepUp?.why).toMatch(/SECURITY GAP/);
  });
});

describe("payments feature gates", () => {
  /**
   * Every one of these defaults off, and the screen's rule is that an off gate
   * means the module is absent rather than disabled. This restates the default
   * so that turning one on cannot happen by accident — it takes a build variable
   * plus a failing line here.
   */
  it("defaults every money module to off", () => {
    expect(payoutInitiationIsLive()).toBe(false);
    expect(instantPayoutIsLive()).toBe(false);
    expect(statementsAreLive()).toBe(false);
    expect(taxDocumentsAreLive()).toBe(false);
    expect(escrowCardIsLive()).toBe(false);
  });

  it("keeps ad top-up off when no billing summary was fetched", () => {
    // The billing argument is required on purpose: a caller who forgot to fetch
    // it gets a compile error rather than a plausible-looking false.
    expect(adTopUpIsLive(null)).toBe(false);
    expect(adTopUpIsLive(undefined)).toBe(false);
  });
});
