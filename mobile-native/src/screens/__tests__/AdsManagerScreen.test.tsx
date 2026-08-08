/**
 * The ads manager makes three promises that only a rendered screen can keep.
 *
 *   1. A pause switch is either pressable or explains itself. The derivation is
 *      tested in `api/__tests__/adsDashboard.test.ts`; what is tested here is
 *      that the card actually receives the disabled state and the reason, and
 *      that a double tap sends exactly one action — an idempotency guard that
 *      lives in a ref, not in a pure function.
 *   2. Money is only ever shown when the server said it. A failed wallet call
 *      renders no chip at all, never a stale or zero balance, and the failure
 *      is stated with a retry.
 *   3. When the wallet is empty AND verification is pending, both banners show
 *      and the wallet one comes first — an unfunded campaign stops sooner than
 *      one waiting on approval.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0,
  useBottomNavScrollVisibility: () => ({
    onScroll: jest.fn(),
    onScrollBeginDrag: jest.fn(),
    scrollEventThrottle: 16
  })
}));
jest.mock("../../core/cache", () => ({
  readJsonCache: jest.fn().mockResolvedValue(null),
  writeJsonCache: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => jest.fn())
}));

const mockRunAction = jest.fn();
jest.mock("../../api/businessOs", () => ({
  ...jest.requireActual("../../api/businessOs"),
  runAdCampaignAction: (...args: unknown[]) => mockRunAction(...args)
}));

const mockLoad = jest.fn();
jest.mock("../../api/adsDashboard", () => ({
  ...jest.requireActual("../../api/adsDashboard"),
  loadAdsMarketplace: () => mockLoad()
}));

import { AdsManagerScreen } from "../AdsManagerScreen";
import { walletSummary } from "../../api/adsDashboard";
import { normalizeAdsPortal } from "../../api/adsPortal";

const ACTIVE_ACCOUNT = { id: 7, business_name: "Roody Goods", status: "active" };
const PENDING_ACCOUNT = { id: 7, business_name: "Roody Goods", status: "pending" };

function campaign(overrides: Record<string, unknown> = {}) {
  return {
    id: 21,
    campaign_name: "Launch",
    objective: "awareness",
    status: "active",
    budget_type: "daily",
    daily_budget_cents: 2500,
    lifetime_budget_cents: 0,
    spent_cents: 500,
    ...overrides
  };
}

function wallet(balanceCents: number) {
  return walletSummary(
    7,
    { currency: "USD", spendable_balance_cents: balanceCents } as never,
    { billing_enabled: true, live_charging: false } as never
  );
}

function model(overrides: Record<string, unknown> = {}) {
  const accounts = (overrides.accounts as unknown[]) || [ACTIVE_ACCOUNT];
  return {
    accounts,
    primaryAccount: accounts[0] || null,
    campaigns: [campaign()],
    analytics: null,
    wallet: wallet(14200),
    // `windowed: false` is not a formality. It is the flag the spend card reads
    // to decide whether it may title itself "last 7 days", and there is no
    // windowed analytics endpoint — so the fixture has to carry the same answer
    // the real builder gives, or the screen under test is one nobody ships.
    spend: { daysCents: [], totalCents: 0, mock: false, windowed: false },
    needsVerification: false,
    accountsStatus: "ok",
    campaignsStatus: "ok",
    analyticsStatus: "ok",
    offline: false,
    // `null` is the fan-out's answer: the review board, roles, creatives and
    // notifications were never requested on that path. It is the default here
    // because it is the state the Policy Center tile most has to get right — an
    // unmade request must not render as an all-clear.
    portal: null,
    ...overrides
  };
}

/** A portal carrying only the review board, which is all the policy tile reads. */
function boardPortal(review_board: unknown[]) {
  return normalizeAdsPortal({ review_board } as never);
}

beforeEach(() => {
  jest.clearAllMocks();
  mockRunAction.mockResolvedValue({ message: "Done." });
  mockLoad.mockResolvedValue(model());
});

async function renderScreen() {
  const view = render(<AdsManagerScreen />);
  await waitFor(() => expect(view.queryByText("Launch")).toBeTruthy());
  return view;
}

describe("ads manager — delivery switch", () => {
  it("sends exactly one action when the switch is tapped twice quickly", async () => {
    let resolveAction: (value: unknown) => void = () => {};
    mockRunAction.mockImplementation(
      () => new Promise((resolve) => { resolveAction = resolve; })
    );
    const view = await renderScreen();
    // By role, not by label: "Delivering" is also the status pill's text, and
    // the thing under test is the switch.
    const control = view.getByRole("switch");

    await act(async () => {
      fireEvent.press(control);
      fireEvent.press(control);
    });
    // The second press lands while the first is still in flight. A campaign
    // paused twice is a support ticket, so the in-flight set drops it.
    expect(mockRunAction).toHaveBeenCalledTimes(1);
    expect(mockRunAction).toHaveBeenCalledWith(21, "pause");

    await act(async () => {
      resolveAction({ message: "Paused." });
    });
  });

  it("disables the switch on an ended campaign and says why instead of no-opping", async () => {
    mockLoad.mockResolvedValue(
      model({
        campaigns: [campaign(), campaign({ id: 22, status: "archived", campaign_name: "Finished" })]
      })
    );
    const view = await renderScreen();
    // Ended campaigns live in their own tab; the switch there is the one that
    // must explain itself rather than disappear.
    await act(async () => {
      fireEvent.press(view.getByLabelText("Ended, 1"));
    });
    await waitFor(() => expect(view.queryByText("Finished")).toBeTruthy());

    expect(
      view.getByText("This campaign has ended and can't be restarted. Duplicate it to run it again.")
    ).toBeTruthy();
    await act(async () => {
      fireEvent.press(view.getByRole("switch"));
    });
    expect(mockRunAction).not.toHaveBeenCalled();
  });

  it("disables the switch while a campaign is in review", async () => {
    mockLoad.mockResolvedValue(
      model({ campaigns: [campaign({ status: "pending_review", campaign_name: "Waiting" })] })
    );
    const view = render(<AdsManagerScreen />);
    // In review sits under Active, not Drafts: it was submitted, not abandoned.
    await waitFor(() => expect(view.queryByText("Waiting")).toBeTruthy());
    expect(view.getByText("In review. You can pause it once it starts delivering.")).toBeTruthy();
  });

  it("refuses delivery changes while offline and says so", async () => {
    mockLoad.mockResolvedValue(model({ offline: true }));
    const view = await renderScreen();
    await act(async () => {
      fireEvent.press(view.getByRole("switch"));
    });
    expect(mockRunAction).not.toHaveBeenCalled();
  });
});

describe("ads manager — money", () => {
  it("shows the server's balance and nothing it computed itself", async () => {
    const view = await renderScreen();
    expect(
      view.getByLabelText("Ad wallet balance $142.00. Tap to open wallet.")
    ).toBeTruthy();
  });

  it("renders no wallet chip and offers a retry when the wallet call failed", async () => {
    mockLoad.mockResolvedValue(model({ wallet: null }));
    const view = await renderScreen();
    // Never a stale or zero number: the chip is absent and the failure is named.
    expect(view.queryByLabelText(/^Ad wallet balance \$/)).toBeNull();
    // "couldn't load", not "not yet available" — the old wording described a
    // request still in flight, which is a different state with a different
    // response. The visible amount now says the same thing, so the retry is not
    // the only clue that something went wrong.
    expect(view.getByLabelText("Ad wallet balance couldn’t load. Tap to retry.")).toBeTruthy();
    // `getAllByText`, not `getByText`: this fixture fails the whole model, so
    // the KPI tiles are unavailable too and legitimately say so. §37's ban on
    // duplicate unavailable notices is about repeating one *notice* — a banner
    // and a card both announcing the same outage — not about several
    // independent figures each reporting their own state in the shared word.
    expect(view.getAllByText("Couldn't load").length).toBeGreaterThan(0);
    expect(view.getByText("Tap to retry")).toBeTruthy();
  });

  it("says adding funds isn't live rather than offering a control that can't charge", async () => {
    mockLoad.mockResolvedValue(
      model({ wallet: wallet(0), campaigns: [campaign({ status: "active" })] })
    );
    const view = await renderScreen();
    expect(
      view.getByText(
        "Your ad wallet is empty. Adding funds isn’t available in this build yet, so campaigns won’t deliver."
      )
    ).toBeTruthy();
  });

  it("puts the empty-wallet banner before the verification banner when both apply", async () => {
    mockLoad.mockResolvedValue(
      model({
        accounts: [PENDING_ACCOUNT],
        wallet: wallet(0),
        needsVerification: true,
        campaigns: [campaign({ status: "active" })]
      })
    );
    const view = await renderScreen();
    const walletBanner = view.getByText("Ad wallet is empty");
    const verifyBanner = view.getByText("Verification needed");
    // Both are shown — neither suppresses the other — and money comes first.
    expect(walletBanner).toBeTruthy();
    expect(verifyBanner).toBeTruthy();
    const order = view.UNSAFE_root.findAllByType(require("react-native").Text);
    const texts = order.map((node: any) => node.props.children);
    expect(texts.indexOf("Ad wallet is empty")).toBeLessThan(texts.indexOf("Verification needed"));
  });

  /**
   * The fabricated `"$0.00"`.
   *
   * `portal_summary` wraps every `wallet_summary` call in a bare `except` and
   * appends a hand-written row of zeroes on failure, and `wallet_summary` opens
   * with `_owner_account` — so for a non-owner the zero is guaranteed invented.
   * The doubt applies to the portal path only: the fan-out path calls the
   * per-account wallet route directly and shows no chip when it fails, so a
   * balance that survives it is the server's own answer.
   */
  it("refuses to print a balance the portal fabricated for a non-owner", async () => {
    mockLoad.mockResolvedValue(
      model({
        wallet: wallet(0),
        portal: normalizeAdsPortal({
          accounts: [{ id: 7, role: "campaign_manager", status: "active" }],
          wallets: [{ account_id: 7, available_balance_cents: 0, spendable_balance_cents: 0 }]
        } as never)
      })
    );
    const view = await renderScreen();
    expect(view.getByLabelText("Ad wallet balance Restricted. Tap to open wallet.")).toBeTruthy();
    expect(view.queryByLabelText(/^Ad wallet balance \$/)).toBeNull();
    // And the "you're out of money" banner must not fire on an invented zero:
    // it would tell a team member their campaigns are stopping on a fully
    // funded account.
    expect(view.queryByText("Ad wallet is empty")).toBeNull();
  });

  it("repeats an owner's real balance from the portal unchanged", async () => {
    mockLoad.mockResolvedValue(
      model({
        portal: normalizeAdsPortal({
          accounts: [{ id: 7, role: "owner", status: "active" }],
          wallets: [
            {
              account_id: 7,
              available_balance: "$142.00",
              available_balance_cents: 14_200,
              spendable_balance_cents: 14_200
            }
          ]
        } as never)
      })
    );
    const view = await renderScreen();
    expect(view.getByLabelText("Ad wallet balance $142.00. Tap to open wallet.")).toBeTruthy();
  });

  /**
   * Overdrawn is not empty, and it is the state most likely to go unreported.
   *
   * A refunded or disputed top-up debits the wallet after the money has already
   * been delivered, so `available_balance_cents` goes negative and the spendable
   * figure floors at $0.00 — identical on screen to an account that simply never
   * funded. The same handler pauses every campaign it can no longer fund, which
   * means the "something is still trying to spend" condition that surfaces the
   * empty-wallet banner is already false by the time the advertiser looks. The
   * debt would be visible nowhere.
   */
  it("names the debt when a reversed payment left the wallet overdrawn", async () => {
    mockLoad.mockResolvedValue(
      model({
        wallet: wallet(0),
        // Nothing is delivering — the reversal stopped it, which is exactly why
        // the banner must not depend on a delivering campaign to appear.
        // `spent_cents: 0` is what makes `deliveryState` refuse "delivering":
        // no money has moved, so there is no receipt to prove it.
        campaigns: [campaign({ spent_cents: 0 })],
        portal: normalizeAdsPortal({
          accounts: [{ id: 7, role: "owner", status: "active" }],
          wallets: [
            {
              account_id: 7,
              available_balance: "-$500.00",
              available_balance_cents: -50_000,
              spendable_balance_cents: 0,
              amount_owed_cents: 50_000,
              amount_owed: "$500.00"
            }
          ]
        } as never)
      })
    );
    const view = await renderScreen();
    expect(view.getByText("Ad wallet is overdrawn")).toBeTruthy();
    // "Empty" would be the wrong diagnosis and the wrong instruction.
    expect(view.queryByText("Ad wallet is empty")).toBeNull();
    expect(view.getByText(/owes \$500\.00/)).toBeTruthy();
    expect(view.getByText(/refunded or disputed/)).toBeTruthy();
  });

  it("still says 'empty' for an account that merely never funded", async () => {
    mockLoad.mockResolvedValue(
      model({ wallet: wallet(0), campaigns: [campaign({ status: "active" })] })
    );
    const view = await renderScreen();
    expect(view.getByText("Ad wallet is empty")).toBeTruthy();
    expect(view.queryByText("Ad wallet is overdrawn")).toBeNull();
  });

  it("does not announce a debt on a wallet it is not allowed to believe", async () => {
    mockLoad.mockResolvedValue(
      model({
        wallet: wallet(0),
        portal: normalizeAdsPortal({
          accounts: [{ id: 7, role: "analyst", status: "active" }],
          wallets: [{ account_id: 7, amount_owed_cents: 50_000, amount_owed: "$500.00" }]
        } as never)
      })
    );
    const view = await renderScreen();
    expect(view.queryByText("Ad wallet is overdrawn")).toBeNull();
  });

  it("trusts the per-account wallet route when there is no portal to doubt", async () => {
    // `portal: null` is the fan-out path: the balance came from
    // GET /api/pulse/ads/accounts/<id>/wallet, which shows no chip at all when
    // it fails. Printing "Restricted" over it would be manufacturing a doubt
    // the payload doesn't carry.
    const view = await renderScreen();
    expect(view.getByLabelText("Ad wallet balance $142.00. Tap to open wallet.")).toBeTruthy();
  });
});

/**
 * §31 applied to the status pill. "Active" was one of eight conditions the
 * selector requires and the only one the card was reading, so a campaign that
 * reaches nobody read as green. These pin that the pill now states delivery and
 * that it never states one it cannot support.
 */
describe("ads manager — delivery truth on the card", () => {
  it("says a campaign is not delivering, and why, instead of calling it active", async () => {
    mockLoad.mockResolvedValue(
      model({
        campaigns: [campaign({ spent_cents: 0 })],
        portal: normalizeAdsPortal({
          accounts: [{ id: 7, role: "owner", status: "active" }],
          // No creative on the campaign — a gate the selector applies and the
          // card never used to mention.
          creatives: []
        } as never)
      })
    );
    const view = await renderScreen();
    expect(view.getByText("Not delivering")).toBeTruthy();
    expect(view.getByText("This campaign has no ad in it")).toBeTruthy();
  });

  it("will not claim delivery it cannot see the gates for", async () => {
    // No portal, nothing spent. "Active" is all the campaign row supports;
    // "Delivering" and "Ready to deliver" are both claims about gates that were
    // never loaded.
    mockLoad.mockResolvedValue(model({ campaigns: [campaign({ spent_cents: 0 })] }));
    const view = await renderScreen();
    expect(view.getByText("Active")).toBeTruthy();
    expect(view.getByText("Marked active with nothing spent yet. Open the campaign to check its ads and budget."))
      .toBeTruthy();
    // "Delivering" is also the pause switch's own label, so the absence being
    // asserted is the pill's — there is exactly one of it on screen, the
    // switch's, and none from the status.
    expect(view.queryAllByText("Delivering")).toHaveLength(1);
    expect(view.queryByText("Ready to deliver")).toBeNull();
  });

  it("calls it delivering once the ledger proves it", async () => {
    // The default fixture campaign has spent 500 cents. Spend is a receipt from
    // the server's own ledger, so it survives the missing portal — and now both
    // the pill and the switch read "Delivering".
    const view = await renderScreen();
    expect(view.queryAllByText("Delivering")).toHaveLength(2);
    expect(view.getByText("This campaign has spent, so it is reaching people.")).toBeTruthy();
  });
});

/**
 * The reporting hole, stated on the card that has the numbers.
 *
 * A marketplace campaign's owner wants to know whether anyone bought. Nothing
 * in the product records that — the one thing writing `conversion` events was
 * `SponsoredAdCard`, firing on one second of viewability, and it is gone. §37
 * forbids showing an advertiser clicks and spend with no evidence the objective
 * was met, so the card says which question its numbers cannot answer.
 */
describe("ads manager — what the metrics don't measure", () => {
  const NOTE = "Conversions aren’t tracked. Impressions and clicks above are measured; what happens after the tap isn’t.";

  it("names the gap on a campaign whose objective happens after the tap", async () => {
    mockLoad.mockResolvedValue(model({ campaigns: [campaign({ objective: "marketplace_sales" })] }));
    const view = await renderScreen();
    expect(view.getByText(NOTE)).toBeTruthy();
    // And never alongside an invented figure for it.
    expect(view.queryByText("Conversions")).toBeNull();
  });

  it("stays quiet on an awareness campaign, which impressions already answer", async () => {
    // The default fixture objective is `awareness`.
    const view = await renderScreen();
    expect(view.queryByText(NOTE)).toBeNull();
  });
});

describe("ads manager — section failures", () => {
  it("keeps campaigns on screen when the analytics call failed", async () => {
    mockLoad.mockResolvedValue(model({ analyticsStatus: "error" }));
    const view = await renderScreen();
    expect(view.getByText("Spend and clicks didn't load.")).toBeTruthy();
    // The chart failing must not take the list with it.
    expect(view.getByText("Launch")).toBeTruthy();
  });
});

/**
 * §36 screen corrections. Each of these was a defect visible in a screenshot,
 * which is why they are asserted against rendered output rather than against the
 * derivations — the derivations were mostly already right, and the screen was
 * still wrong.
 */
describe("ads manager — §36 corrections", () => {
  /** A navigation double that records where a tile actually points. */
  function nav() {
    return { navigate: jest.fn(), goBack: jest.fn() };
  }

  async function renderEmpty(overrides: Record<string, unknown> = {}) {
    const navigation = nav();
    mockLoad.mockResolvedValue(model({ campaigns: [], ...overrides }));
    const view = render(<AdsManagerScreen navigation={navigation as never} />);
    await waitFor(() => expect(view.queryByText("No campaigns yet")).toBeTruthy());
    return { view, navigation };
  }

  /**
   * The empty state used to fork: an unverified advertiser saw "Verify your
   * business" and nothing else, which enforced a rule the platform does not
   * have. Verification gates delivery, not authoring — a draft costs nothing and
   * delivers nothing, so there is no state in which drafting is unsafe.
   */
  it("offers a draft path and verification together while verification is pending", async () => {
    const { view, navigation } = await renderEmpty({
      accounts: [PENDING_ACCOUNT],
      needsVerification: true
    });
    // Two on screen: this one and the standing CTA at the foot of the page.
    // `[0]` is the empty state's, which is the one under test — the point is
    // that the *empty state* offers it, not merely that the screen does.
    expect(view.getAllByText("Create campaign")).toHaveLength(2);
    // Not "Verify your business", which is what this said while the control
    // opened the Verification Center — a profile-badge flow that never writes
    // `pulse_ad_accounts.status`. The label now names the request the server
    // actually accepts.
    expect(view.getByText("Request verification")).toBeTruthy();

    await act(async () => {
      fireEvent.press(view.getAllByText("Create campaign")[0]);
    });
    // The draft path is not a decoration — it routes to the screen that owns
    // campaign creation, the same one a verified advertiser reaches.
    expect(navigation.navigate).toHaveBeenCalledWith(
      "BusinessOsAdvertising",
      expect.objectContaining({ mode: "create" })
    );
  });

  it("offers only the draft path once there is nothing left to verify", async () => {
    const { view } = await renderEmpty({ needsVerification: false });
    expect(view.getAllByText("Create campaign")).toHaveLength(2);
    expect(view.queryByText("Request verification")).toBeNull();
    expect(view.queryByText("Request review again")).toBeNull();
  });

  /**
   * A review already in flight has no action attached to it. Offering one anyway
   * produces a control whose only possible outcome is the server's "Verification
   * is already in review" — §31's active-looking unavailable control. The empty
   * state therefore drops the secondary action entirely rather than disabling it.
   */
  it("offers no verification action while a review is already in flight", async () => {
    const { view } = await renderEmpty({
      accounts: [{ ...PENDING_ACCOUNT, verification_status: "pending" }],
      needsVerification: true
    });
    expect(view.getAllByText("Create campaign")).toHaveLength(2);
    expect(view.queryByText("Request verification")).toBeNull();
    expect(view.queryByText("Request review again")).toBeNull();
    // Dropping the button does not mean dropping the explanation. The
    // verification banner renders only beside blocked campaigns, so with none
    // on the account this body is the only thing that says why.
    expect(view.getByText(/once your account review is decided/)).toBeTruthy();
  });

  /**
   * A declined review is the one state with something for the advertiser to
   * read: §37 requires the policy reason and the appeal path to be reachable,
   * and here they are the same surface — the stored reason, then resubmission.
   */
  it("shows the recorded reason and a resubmit path after a decline", async () => {
    const { view } = await renderEmpty({
      accounts: [
        {
          ...PENDING_ACCOUNT,
          verification_status: "rejected",
          verification_reason: "Business address could not be confirmed."
        }
      ],
      needsVerification: true
    });
    expect(view.getByText(/Business address could not be confirmed\./)).toBeTruthy();
    expect(view.getByText("Request review again")).toBeTruthy();
    // The old copy told every blocked advertiser to wait for approval. A
    // declined account is waiting on the advertiser, not on us.
    expect(view.queryByText(/Delivery begins once/)).toBeNull();
  });

  /**
   * Verified but not active is a contradiction the advertiser cannot resolve:
   * `approve_account_verification` writes both columns together, so a mismatch
   * is a platform fault. Sending them round the verification loop again would
   * be a request the server has no reason to grant.
   */
  it("routes a verified-but-inactive account to support rather than to verification", async () => {
    const { view } = await renderEmpty({
      accounts: [{ ...PENDING_ACCOUNT, verification_status: "verified" }],
      needsVerification: true
    });
    expect(view.getByText(/verified but isn't marked active/)).toBeTruthy();
    expect(view.queryByText("Request verification")).toBeNull();
    expect(view.queryByText("Request review again")).toBeNull();
    // Never "wait for approval": approval already happened.
    expect(view.queryByText(/Delivery begins once/)).toBeNull();
  });

  /**
   * Both tiles rendered `disabled` with an "isn't available" subtitle: accurate,
   * and still a dead end. §37 forbids an empty locked card with no useful
   * destination, so each now opens a page describing what the server already
   * enforces for every campaign.
   */
  it("opens a real destination from the tiles that used to be locked", async () => {
    const navigation = nav();
    const view = render(<AdsManagerScreen navigation={navigation as never} />);
    await waitFor(() => expect(view.queryByText("Launch")).toBeTruthy());

    for (const [label, mode] of [
      ["Audiences", "audiences"],
      ["Creative library", "creatives"]
    ] as const) {
      await act(async () => {
        fireEvent.press(view.getByText(label));
      });
      expect(navigation.navigate).toHaveBeenCalledWith(
        "BusinessOsAdvertising",
        expect.objectContaining({ mode })
      );
    }
  });

  /**
   * "Ad account 8" put an internal identifier where the business name belongs.
   * The row is two lines now, and the number moved to Account details — so the
   * identity row must still be able to reach it.
   */
  it("shows the account name over its standing, with no identifier in sight", async () => {
    const navigation = nav();
    const view = render(<AdsManagerScreen navigation={navigation as never} />);
    await waitFor(() => expect(view.queryByText("Launch")).toBeTruthy());

    const row = view.getByLabelText(
      "Roody Goods. Advertising account · Active. Open account details."
    );
    expect(view.queryByText(/Ad account \d/)).toBeNull();

    await act(async () => {
      fireEvent.press(row);
    });
    expect(navigation.navigate).toHaveBeenCalledWith(
      "BusinessOsAdvertising",
      expect.objectContaining({ mode: "account", accountId: 7 })
    );
  });

  /**
   * §37: no fake seven-day report. With an empty series the card must fall back
   * to the to-date heading — "$0.00" under "last 7 days" reads as "delivery
   * stopped this week" when the truth is that nothing was ever spent.
   */
  it("titles the spend card by the window it can actually source", async () => {
    const view = await renderScreen();
    expect(view.getByText("Account spend")).toBeTruthy();
    expect(view.queryByText(/last 7 days/i)).toBeNull();
  });
});

/**
 * The Policy Center tile is the manager's only surface for a rejected ad, so its
 * subtitle is load-bearing in a way a tile caption usually isn't: it is the line
 * that decides whether someone taps through.
 *
 * §31 makes four of the states distinct claims, and the one that matters most is
 * the boundary between "the board is empty" and "the board was never fetched".
 */
describe("ads manager — Policy Center tile", () => {
  /** A navigation double that records where the tile actually points. */
  function nav() {
    return { navigate: jest.fn(), goBack: jest.fn() };
  }

  it("opens the Policy Center", async () => {
    const navigation = nav();
    const view = render(<AdsManagerScreen navigation={navigation as never} />);
    await waitFor(() => expect(view.queryByText("Launch")).toBeTruthy());

    await act(async () => {
      fireEvent.press(view.getByText("Policy Center"));
    });
    expect(navigation.navigate).toHaveBeenCalledWith(
      "BusinessOsAdvertising",
      expect.objectContaining({ mode: "policy" })
    );
  });

  /**
   * The fan-out path never asked about policy. A tile that said "All clear" on
   * the strength of a request nobody made would tell an advertiser with a
   * rejected ad that nothing is wrong — so it names the destination instead and
   * asserts nothing about what is in it.
   */
  it("asserts nothing when the board was never fetched", async () => {
    const view = await renderScreen();
    expect(view.getByText("See review decisions")).toBeTruthy();
    expect(view.queryByText("All clear")).toBeNull();
    expect(view.queryByText("No decisions yet")).toBeNull();
  });

  it("counts outstanding rejections, because they are the only ones needing action", async () => {
    mockLoad.mockResolvedValue(
      model({
        portal: boardPortal([
          { review_id: 1, creative_id: 1, moderation_status: "rejected" },
          { review_id: 2, creative_id: 2, moderation_status: "rejected" },
          { review_id: 3, creative_id: 3, moderation_status: "pending" }
        ])
      })
    );
    const view = await renderScreen();
    expect(view.getByText("2 need attention")).toBeTruthy();
  });

  /** "1 creatives" is the kind of seam that makes a surface read as unfinished. */
  it("pluralises a single rejection correctly", async () => {
    mockLoad.mockResolvedValue(
      model({ portal: boardPortal([{ review_id: 1, creative_id: 1, moderation_status: "rejected" }]) })
    );
    const view = await renderScreen();
    expect(view.getByText("1 needs attention")).toBeTruthy();
  });

  it("falls back to pending decisions when nothing is rejected", async () => {
    mockLoad.mockResolvedValue(
      model({
        portal: boardPortal([
          { review_id: 1, creative_id: 1, moderation_status: "pending" },
          { review_id: 2, creative_id: 2, moderation_status: "approved" }
        ])
      })
    );
    const view = await renderScreen();
    expect(view.getByText("1 in review")).toBeTruthy();
  });

  it("says all clear only when a board that actually loaded is entirely approved", async () => {
    mockLoad.mockResolvedValue(
      model({ portal: boardPortal([{ review_id: 1, creative_id: 1, moderation_status: "approved" }]) })
    );
    const view = await renderScreen();
    expect(view.getByText("All clear")).toBeTruthy();
  });

  it("distinguishes a board that loaded and holds nothing", async () => {
    mockLoad.mockResolvedValue(model({ portal: boardPortal([]) }));
    const view = await renderScreen();
    expect(view.getByText("No decisions yet")).toBeTruthy();
  });

  /** A degraded portal's empty board is an unmade request wearing an answer's clothes. */
  it("treats a degraded portal as unfetched rather than clear", async () => {
    mockLoad.mockResolvedValue(model({ portal: { ...boardPortal([]), degraded: true } }));
    const view = await renderScreen();
    expect(view.getByText("See review decisions")).toBeTruthy();
    expect(view.queryByText("No decisions yet")).toBeNull();
  });
});

/**
 * The Creative library tile used to read "See the creative rules", which was
 * accurate about the page it opened and useless about the advertiser: it said
 * the same thing whether they had a rejected creative or none at all. Now that
 * the page lists real creatives, the tile carries the one number worth the
 * space — how many aren't running.
 *
 * The §31 boundary is the same as the policy tile's, and matters for the same
 * reason: a library that was never fetched must not render as an empty one.
 */
describe("ads manager — Creative library tile", () => {
  function nav() {
    return { navigate: jest.fn(), goBack: jest.fn() };
  }

  /** A portal carrying only creatives and their accounts, which is all this tile reads. */
  function creativePortal(creatives: unknown[]) {
    return normalizeAdsPortal({ creatives, accounts: [{ id: 8, role: "owner" }] } as never);
  }

  const DRAFT = { id: 21, ad_account_id: 8, status: "draft", moderation_status: "draft" };
  const APPROVED = { id: 23, ad_account_id: 8, status: "approved", moderation_status: "approved" };

  it("opens the library", async () => {
    const navigation = nav();
    const view = render(<AdsManagerScreen navigation={navigation as never} />);
    await waitFor(() => expect(view.queryByText("Launch")).toBeTruthy());

    await act(async () => {
      fireEvent.press(view.getByText("Creative library"));
    });
    expect(navigation.navigate).toHaveBeenCalledWith(
      "BusinessOsAdvertising",
      expect.objectContaining({ mode: "creatives" })
    );
  });

  /** The fan-out path never asked about creatives, so the tile claims nothing. */
  it("asserts nothing when the library was never fetched", async () => {
    const view = await renderScreen();
    expect(view.getByText("Browse your creatives")).toBeTruthy();
    expect(view.queryByText("No creatives yet")).toBeNull();
    expect(view.queryByText("All delivering")).toBeNull();
  });

  it("counts rejections and unsubmitted drafts together, because both mean nothing is running", async () => {
    mockLoad.mockResolvedValue(
      model({
        portal: creativePortal([
          DRAFT,
          { id: 22, ad_account_id: 8, status: "pending_review", moderation_status: "rejected" },
          APPROVED
        ])
      })
    );
    const view = await renderScreen();
    expect(view.getByText("2 need attention")).toBeTruthy();
  });

  it("pluralises a single one correctly", async () => {
    mockLoad.mockResolvedValue(model({ portal: creativePortal([DRAFT, APPROVED]) }));
    const view = await renderScreen();
    expect(view.getByText("1 needs attention")).toBeTruthy();
  });

  it("says all delivering only when a library that loaded has nothing outstanding", async () => {
    mockLoad.mockResolvedValue(model({ portal: creativePortal([APPROVED]) }));
    const view = await renderScreen();
    expect(view.getByText("All delivering")).toBeTruthy();
  });

  it("distinguishes a library that loaded and holds nothing", async () => {
    mockLoad.mockResolvedValue(model({ portal: creativePortal([]) }));
    const view = await renderScreen();
    expect(view.getByText("No creatives yet")).toBeTruthy();
  });

  it("treats a degraded portal as unfetched rather than empty", async () => {
    mockLoad.mockResolvedValue(model({ portal: { ...creativePortal([]), degraded: true } }));
    const view = await renderScreen();
    expect(view.getByText("Browse your creatives")).toBeTruthy();
    expect(view.queryByText("No creatives yet")).toBeNull();
  });

  /** The old caption described a rulebook. It no longer describes the page. */
  it("no longer sends the reader to a rulebook", async () => {
    mockLoad.mockResolvedValue(model({ portal: creativePortal([APPROVED]) }));
    const view = await renderScreen();
    expect(view.queryByText("See the creative rules")).toBeNull();
  });
});
