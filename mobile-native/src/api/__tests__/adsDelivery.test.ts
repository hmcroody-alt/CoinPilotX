/**
 * What these tests are actually pinning.
 *
 * 1. That "active" is never allowed to mean "delivering". The selector requires
 *    eight conditions and the client used to read one, so the tests below walk
 *    each gate and assert the campaign reports `blocked` with the right reason
 *    rather than a green pill.
 * 2. That the account gate is reported as something the advertiser cannot
 *    clear. No route sets an ad account to `active`, so "wait for verification"
 *    would be a dead end with a progress bar on it.
 * 3. That `delivering` is only claimed when money moved. Everything else the
 *    client can see is a forecast, and the two are labelled differently.
 * 4. That a non-owner never sees a wallet figure. `wallet_summary` is
 *    owner-only and `portal_summary` substitutes a fabricated `"$0.00"`, so the
 *    role is the only thing separating a real zero from an invented one.
 * 5. That Resume is not offered where the server answers "Campaign not found."
 *    — a 404 about a campaign the reader is looking at is the worst available
 *    outcome, and it is the one the current button produces.
 */

import { AdCampaign } from "../businessOs";
import { AdCreative, AdsPortal, normalizeAdsPortal } from "../adsPortal";
import {
  attributionNote,
  campaignWindow,
  creativeIsDeliverable,
  creativeMediaSatisfiesSelector,
  deliveryBlocker,
  deliveryState,
  deliveryStateDetail,
  deliveryStateLabel,
  deliveryStateTone,
  resumeCheck,
  walletAuthority,
  walletRollupAuthority
} from "../adsDelivery";

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

/** A campaign with every visible gate already clear, so each test can break one. */
function campaign(over: Partial<AdCampaign> = {}): AdCampaign {
  return {
    id: 5,
    ad_account_id: 7,
    campaign_name: "Spring push",
    status: "active",
    budget_type: "daily",
    daily_budget_cents: 5_000,
    lifetime_budget_cents: 0,
    spent_cents: 0,
    start_at: "",
    end_at: "",
    placements: ["feed"],
    ...over
  } as AdCampaign;
}

/** An approved text creative — the selector's easiest case. */
function approved(over: Partial<AdCreative> = {}): AdCreative {
  return {
    id: 11,
    ad_account_id: 7,
    campaign_id: 5,
    title: "Summer banner",
    creative_type: "text",
    status: "approved",
    moderation_status: "approved",
    ...over
  } as AdCreative;
}

function portalWith(
  over: {
    creatives?: AdCreative[];
    accounts?: unknown[];
    wallets?: unknown[];
    metrics?: unknown;
    degraded?: boolean;
  } = {}
): AdsPortal {
  return normalizeAdsPortal({
    creatives: over.creatives ?? [approved()],
    accounts: over.accounts ?? [{ id: 7, role: "owner", status: "active" }],
    wallets: over.wallets ?? [],
    metrics: over.metrics,
    degraded: over.degraded
  } as never);
}

/* ------------------------------------------------------------------ *
 * The gates
 * ------------------------------------------------------------------ */

describe("deliveryBlocker — the gates select_ads actually applies", () => {
  it("clears when every visible condition is met", () => {
    expect(deliveryBlocker(portalWith(), campaign())).toBeNull();
  });

  it("names the ad account first, because nothing downstream matters while it is closed", () => {
    const portal = portalWith({
      accounts: [{ id: 7, role: "owner", status: "pending_verification" }],
      creatives: []
    });
    // The campaign also has no creative. The account still wins.
    expect(deliveryBlocker(portal, campaign())?.code).toBe("account_not_verified");
  });

  /*
   * The four account states are four different instructions, and the whole
   * reason they are separate codes is that collapsing them produces advice that
   * is wrong for three readers out of four. Telling someone whose review is
   * already in progress to request verification sends them to do a thing they
   * have done; telling someone who has never asked to "wait" leaves them waiting
   * on a queue they are not in.
   */
  it("distinguishes never-asked from waiting from declined", () => {
    const state = (verification: string) =>
      deliveryBlocker(
        portalWith({
          accounts: [
            { id: 7, role: "owner", status: "pending_verification", verification_status: verification }
          ]
        }),
        campaign()
      )?.code;

    expect(state("unverified")).toBe("account_not_verified");
    expect(state("pending")).toBe("account_verification_pending");
    expect(state("rejected")).toBe("account_verification_rejected");
    expect(state("changes_requested")).toBe("account_verification_changes_requested");
  });

  it("reads a changes-requested account as an open, clearable ask — not a dead-end rejection", () => {
    const gate = deliveryBlocker(
      portalWith({
        accounts: [
          { id: 7, role: "owner", status: "pending_verification", verification_status: "changes_requested" }
        ]
      }),
      campaign()
    );
    expect(gate?.code).toBe("account_verification_changes_requested");
    // A change request is something the advertiser answers and resubmits, so the
    // banner offers the action rather than stating a wall.
    expect(gate?.advertiserCanClear).toBe(true);
  });

  it("says a review in progress is not the reader's move, and an unverified account is", () => {
    const gate = (verification: string) =>
      deliveryBlocker(
        portalWith({
          accounts: [
            { id: 7, role: "owner", status: "pending_verification", verification_status: verification }
          ]
        }),
        campaign()
      );

    // Nothing to do. Saying otherwise invents work.
    expect(gate("pending")?.advertiserCanClear).toBe(false);
    // Something to do — and it exists now, which it did not when this module was
    // written. `POST /api/pulse/ads/accounts/:id/verification`.
    expect(gate("unverified")?.advertiserCanClear).toBe(true);
    expect(gate("rejected")?.advertiserCanClear).toBe(true);
  });

  it("reads a verified-but-inactive account as the contradiction it is", () => {
    // Approval writes both columns together, so this pairing should be
    // unreachable. If it happens anyway the selector reads `status`, so the
    // honest answer is the pessimistic one and it is not the advertiser's to fix.
    const portal = portalWith({
      accounts: [{ id: 7, role: "owner", status: "pending_verification", verification_status: "verified" }]
    });
    const gate = deliveryBlocker(portal, campaign());
    expect(gate?.code).toBe("account_not_active");
    expect(gate?.advertiserCanClear).toBe(false);
    expect(gate?.detail).toMatch(/contact support/i);
  });

  it("separates suspension from never-approved", () => {
    const portal = portalWith({ accounts: [{ id: 7, role: "owner", status: "suspended" }] });
    expect(deliveryBlocker(portal, campaign())?.code).toBe("account_suspended");
  });

  it("does not invent an account problem for an account it cannot see", () => {
    const portal = portalWith({ accounts: [{ id: 999, role: "owner", status: "active" }] });
    // Account 7 is absent from the payload. The next real gate is reported
    // instead of a guess about a row we never read.
    expect(deliveryBlocker(portal, campaign())?.code).not.toBe("account_not_active");
  });

  it("distinguishes no creative from a rejected one from one still in review", () => {
    expect(deliveryBlocker(portalWith({ creatives: [] }), campaign())?.code).toBe("no_creative");

    const allRejected = portalWith({
      creatives: [approved({ status: "pending_review", moderation_status: "rejected" })]
    });
    expect(deliveryBlocker(allRejected, campaign())?.code).toBe("creative_rejected");

    const mixed = portalWith({
      creatives: [
        approved({ id: 11, status: "pending_review", moderation_status: "rejected" }),
        approved({ id: 12, status: "pending_review", moderation_status: "pending" })
      ]
    });
    expect(deliveryBlocker(mixed, campaign())?.code).toBe("creative_in_review");
  });

  it("requires both creative columns, not either", () => {
    const halfApproved = portalWith({
      creatives: [approved({ status: "pending_review", moderation_status: "approved" })]
    });
    expect(deliveryBlocker(halfApproved, campaign())?.code).toBe("creative_in_review");
  });

  it("catches an approved image creative with no uploaded asset", () => {
    const portal = portalWith({
      creatives: [approved({ creative_type: "image", media_asset_id: 0, media_url: "https://cdn/x.png" })]
    });
    // The portal reports media_ready for this row. The selector does not accept it.
    expect(deliveryBlocker(portal, campaign())?.code).toBe("creative_media_missing");
  });

  it("does not demand an asset id from a text creative", () => {
    expect(deliveryBlocker(portalWith(), campaign())).toBeNull();
  });

  it("catches a campaign with no placement", () => {
    expect(deliveryBlocker(portalWith(), campaign({ placements: [] }))?.code).toBe("no_placement");
  });

  it("does not read a missing placements field as a campaign with no placement", () => {
    // An absent field is a campaign row that didn't carry placements; an empty
    // array is a campaign that has none. Only the second is a finding, and
    // conflating them sends the reader to fix a placement that already exists.
    const row = campaign();
    delete (row as { placements?: unknown }).placements;
    expect(deliveryBlocker(portalWith(), row)).toBeNull();
  });

  it("catches a zero budget and a spent-out lifetime budget", () => {
    expect(
      deliveryBlocker(portalWith(), campaign({ daily_budget_cents: 0, lifetime_budget_cents: 0 }))?.code
    ).toBe("no_budget");
    expect(
      deliveryBlocker(
        portalWith(),
        campaign({ daily_budget_cents: 0, lifetime_budget_cents: 1_000, spent_cents: 1_000 })
      )?.code
    ).toBe("budget_exhausted");
  });

  it("asserts no creative fault when the creatives were never requested", () => {
    // `portal: null` is the fan-out path — creatives were not fetched at all.
    // Reporting "This campaign has no ad in it" there is the mirror of the
    // fabricated zero: a finding manufactured out of an unmade request.
    expect(deliveryBlocker(null, campaign())).toBeNull();
    expect(deliveryBlocker(undefined, campaign())).toBeNull();
  });

  it("treats a degraded portal the same as no portal for creative gates", () => {
    // `degraded` is the portal admitting part of it failed. An empty creatives
    // array behind that flag means "didn't arrive", not "none exist".
    expect(deliveryBlocker(portalWith({ creatives: [], degraded: true }), campaign())).toBeNull();
  });

  it("still applies the campaign row's own gates without a portal", () => {
    // Budget lives on the campaign, which did arrive. Absence of the portal is
    // not a licence to stop checking what we can actually see.
    expect(
      deliveryBlocker(null, campaign({ daily_budget_cents: 0, lifetime_budget_cents: 0 }))?.code
    ).toBe("no_budget");
    expect(
      deliveryBlocker(null, campaign({ daily_budget_cents: 0, lifetime_budget_cents: 900, spent_cents: 900 }))
        ?.code
    ).toBe("budget_exhausted");
  });

  it("gives every gate a non-empty next step", () => {
    const cases: AdsPortal[] = [
      portalWith({ accounts: [{ id: 7, role: "owner", status: "suspended" }] }),
      portalWith({ accounts: [{ id: 7, role: "owner", status: "pending_verification" }] }),
      portalWith({ creatives: [] }),
      portalWith({ creatives: [approved({ status: "pending_review", moderation_status: "rejected" })] }),
      portalWith({ creatives: [approved({ status: "draft", moderation_status: "pending" })] }),
      portalWith({ creatives: [approved({ creative_type: "video", media_asset_id: 0 })] })
    ];
    for (const portal of cases) {
      const gate = deliveryBlocker(portal, campaign());
      expect(gate).not.toBeNull();
      expect(gate!.title.length).toBeGreaterThan(0);
      expect(gate!.detail.length).toBeGreaterThan(0);
    }
  });
});

/* ------------------------------------------------------------------ *
 * State
 * ------------------------------------------------------------------ */

describe("deliveryState — active is not delivering", () => {
  it("reports blocked for an active campaign the server would not serve", () => {
    const portal = portalWith({ accounts: [{ id: 7, role: "owner", status: "pending_verification" }] });
    expect(deliveryState(portal, campaign({ status: "active" }))).toBe("blocked");
  });

  it("claims delivering only once the ledger proves it", () => {
    expect(deliveryState(portalWith(), campaign({ spent_cents: 0 }))).toBe("eligible");
    expect(deliveryState(portalWith(), campaign({ spent_cents: 1 }))).toBe("delivering");
  });

  it("labels the two differently, because one is a forecast", () => {
    expect(deliveryStateLabel("eligible")).toBe("Ready to deliver");
    expect(deliveryStateLabel("delivering")).toBe("Delivering");
    expect(deliveryStateLabel("eligible")).not.toBe(deliveryStateLabel("delivering"));
  });

  it("will not forecast eligibility from gates it never loaded", () => {
    // Without the portal the visible gates are unread, so "Ready to deliver"
    // would be an all-clear issued on no evidence. The state says only what the
    // campaign row says, in the plainest available word.
    expect(deliveryState(null, campaign({ spent_cents: 0 }))).toBe("active_unconfirmed");
    expect(deliveryState(portalWith({ degraded: true }), campaign({ spent_cents: 0 }))).toBe(
      "active_unconfirmed"
    );
    expect(deliveryStateLabel("active_unconfirmed")).toBe("Active");
    // Neutral, not success: a green pill is a claim about delivery and this
    // state is the absence of one.
    expect(deliveryStateTone("active_unconfirmed")).toBe("neutral");
    expect(deliveryStateDetail(null, campaign({ spent_cents: 0 }))).toMatch(/Open the campaign/i);
  });

  it("still calls it delivering without a portal, because spend is a receipt", () => {
    // `spent_cents` is incremented by the server's own ledger. It proves
    // delivery whether or not the gates were loaded.
    expect(deliveryState(null, campaign({ spent_cents: 42 }))).toBe("delivering");
  });

  it("reads the lifecycle for every non-active status", () => {
    const portal = portalWith();
    expect(deliveryState(portal, campaign({ status: "paused" }))).toBe("paused");
    expect(deliveryState(portal, campaign({ status: "archived" }))).toBe("archived");
    expect(deliveryState(portal, campaign({ status: "suspended" }))).toBe("suspended");
    expect(deliveryState(portal, campaign({ status: "pending_review" }))).toBe("in_review");
    expect(deliveryState(portal, campaign({ status: "completed" }))).toBe("ended");
    expect(deliveryState(portal, campaign({ status: "draft" }))).toBe("draft");
    expect(deliveryState(portal, campaign({ status: "something_new" }))).toBe("draft");
  });

  it("does not call a paused or archived campaign blocked, whatever else is wrong", () => {
    const broken = portalWith({ creatives: [], accounts: [{ id: 7, role: "owner", status: "suspended" }] });
    expect(deliveryState(broken, campaign({ status: "paused" }))).toBe("paused");
    expect(deliveryState(broken, campaign({ status: "archived" }))).toBe("archived");
  });

  it("reads the schedule window", () => {
    const portal = portalWith();
    const future = new Date(Date.now() + 86_400_000).toISOString();
    const past = new Date(Date.now() - 86_400_000).toISOString();
    expect(deliveryState(portal, campaign({ start_at: future }))).toBe("scheduled");
    expect(deliveryState(portal, campaign({ end_at: past }))).toBe("ended");
    expect(campaignWindow(campaign({}))).toBe("open");
  });

  it("puts an ended window ahead of a blocker, because ended is the simpler truth", () => {
    const past = new Date(Date.now() - 86_400_000).toISOString();
    const portal = portalWith({ creatives: [] });
    expect(deliveryState(portal, campaign({ end_at: past }))).toBe("ended");
  });

  it("gives every state a tone and a non-empty explanation", () => {
    const portal = portalWith();
    for (const status of ["active", "paused", "archived", "suspended", "pending_review", "completed", "draft"]) {
      const state = deliveryState(portal, campaign({ status }));
      expect(["neutral", "info", "success", "warning", "error"]).toContain(deliveryStateTone(state));
      expect(deliveryStateDetail(portal, campaign({ status })).length).toBeGreaterThan(0);
    }
  });

  it("explains a blocked campaign with the blocker, not a generic line", () => {
    const portal = portalWith({ creatives: [] });
    expect(deliveryStateDetail(portal, campaign())).toBe("This campaign has no ad in it");
  });

  it("does not promise delivery in the eligible explanation", () => {
    const detail = deliveryStateDetail(portalWith(), campaign({ spent_cents: 0 }));
    expect(detail).toMatch(/isn't confirmed/i);
  });
});

/* ------------------------------------------------------------------ *
 * Wallet authority
 * ------------------------------------------------------------------ */

describe("walletAuthority — the fabricated $0.00", () => {
  const wallet = {
    account_id: 7,
    available_balance_cents: 2_500,
    available_balance: "$25.00",
    reserved_budget_cents: 500,
    reserved_budget: "$5.00",
    spendable_balance_cents: 2_000
  };

  it("believes the figure for an owner", () => {
    const auth = walletAuthority(portalWith({ wallets: [wallet] }), 7);
    expect(auth.state).toBe("confirmed");
    expect(auth.display).toBe("$25.00");
    expect(auth.reservedDisplay).toBe("$5.00");
    expect(auth.spendableCents).toBe(2_000);
    expect(auth.note).toBeNull();
  });

  it("refuses the figure for every non-owner, because the server never computed it", () => {
    for (const role of ["campaign_manager", "marketing_manager", "analyst", "viewer"]) {
      const portal = portalWith({
        accounts: [{ id: 7, role, status: "active" }],
        // The server substituted this row after wallet_summary raised.
        wallets: [{ account_id: 7, available_balance_cents: 0, available_balance: "$0.00" }]
      });
      const auth = walletAuthority(portal, 7);
      expect(auth.state).toBe("restricted");
      expect(auth.display).toBe("Restricted");
      expect(auth.spendableCents).toBeNull();
    }
  });

  it("never renders the substituted zero as a balance", () => {
    const portal = portalWith({
      accounts: [{ id: 7, role: "campaign_manager", status: "active" }],
      wallets: [{ account_id: 7, available_balance_cents: 0, available_balance: "$0.00" }]
    });
    expect(walletAuthority(portal, 7).display).not.toBe("$0.00");
  });

  it("tells a restricted reader they can still run campaigns", () => {
    const portal = portalWith({ accounts: [{ id: 7, role: "campaign_manager", status: "active" }] });
    expect(walletAuthority(portal, 7).note).toMatch(/owner/i);
  });

  it("separates a failed load from a real zero", () => {
    expect(walletAuthority(portalWith({ degraded: true, wallets: [wallet] }), 7).state).toBe("unavailable");
    expect(walletAuthority(null, 7).display).toBe("Unavailable");
    expect(walletAuthority(portalWith({ wallets: [] }), 7).state).toBe("unavailable");
  });

  it("does state a real zero for an owner whose wallet is genuinely empty", () => {
    const portal = portalWith({
      wallets: [{ account_id: 7, available_balance_cents: 0, available_balance: "$0.00" }]
    });
    expect(walletAuthority(portal, 7).display).toBe("$0.00");
  });

  /*
   * The server now says outright when a wallet could not be read, instead of
   * substituting a row of zeroes. The client has to honour that rather than
   * re-manufacturing the zero on the way in.
   */
  it("honours the server's own 'I could not read this wallet'", () => {
    const portal = portalWith({
      wallets: [
        {
          account_id: 7,
          unavailable: true,
          unavailable_reason: "Wallet balance could not be loaded. This is a temporary error, not a zero balance.",
          available_balance_cents: null,
          available_balance: "",
          spendable_balance_cents: null
        }
      ]
    });
    const auth = walletAuthority(portal, 7);
    expect(auth.state).toBe("unavailable");
    expect(auth.display).toBe("Unavailable");
    expect(auth.display).not.toBe("$0.00");
    expect(auth.spendableCents).toBeNull();
    expect(auth.note).toMatch(/not a zero balance/i);
  });

  it("does not let normalization turn an unreadable wallet's nulls into zeroes", () => {
    const portal = portalWith({
      wallets: [{ account_id: 7, unavailable: true, available_balance_cents: null }]
    });
    const row = portal.wallets.find((each) => Number(each.account_id) === 7);
    expect(row?.unavailable).toBe(true);
    expect(row?.available_balance_cents).toBeUndefined();
    expect(row?.spendable_balance_cents).toBeUndefined();
  });

  /*
   * Overdrawn is not empty. A reversed top-up debits the wallet after the money
   * is gone, so `spendable_balance_cents` floors at 0 and the account reads
   * exactly like one that never funded — while actually owing money and having
   * had its campaigns paused underneath it.
   */
  it("names the debt when a reversed top-up left the account owing money", () => {
    const portal = portalWith({
      wallets: [
        {
          account_id: 7,
          available_balance_cents: -50_000,
          available_balance: "-$500.00",
          spendable_balance_cents: 0,
          amount_owed_cents: 50_000,
          amount_owed: "$500.00"
        }
      ]
    });
    const auth = walletAuthority(portal, 7);
    expect(auth.state).toBe("confirmed");
    expect(auth.owedCents).toBe(50_000);
    expect(auth.owedDisplay).toBe("$500.00");
    // The spendable figure is still an honest zero; the debt is what the zero
    // fails to explain on its own.
    expect(auth.spendableCents).toBe(0);
  });

  it("reports no debt rather than a decorative zero when nothing is owed", () => {
    const auth = walletAuthority(portalWith({ wallets: [wallet] }), 7);
    expect(auth.owedCents).toBe(0);
    expect(auth.owedDisplay).toBeNull();
  });

  it("formats the debt itself if the server sent only the cents", () => {
    const portal = portalWith({
      wallets: [{ account_id: 7, available_balance_cents: -1_250, amount_owed_cents: 1_250 }]
    });
    expect(walletAuthority(portal, 7).owedDisplay).toBe("$12.50");
  });

  it("never reports a debt for a wallet it is not allowed to believe", () => {
    const restricted = portalWith({
      accounts: [{ id: 7, role: "analyst", status: "active" }],
      wallets: [{ account_id: 7, amount_owed_cents: 50_000, amount_owed: "$500.00" }]
    });
    expect(walletAuthority(restricted, 7).owedCents).toBeNull();
    expect(walletAuthority(null, 7).owedDisplay).toBeNull();
  });
});

describe("walletRollupAuthority — a total that omits an account is wrong, not small", () => {
  const metrics = {
    wallet_balance_cents: 2_500,
    wallet_balance: "$25.00",
    reserved_budget_cents: 0,
    reserved_budget: "$0.00",
    spendable_balance_cents: 2_500
  };

  it("believes the total when every account is owned", () => {
    const portal = portalWith({
      accounts: [
        { id: 7, role: "owner", status: "active" },
        { id: 8, role: "owner", status: "active" }
      ],
      metrics
    });
    const auth = walletRollupAuthority(portal);
    expect(auth.state).toBe("confirmed");
    expect(auth.display).toBe("$25.00");
  });

  it("refuses the total when even one account contributes a fabricated row", () => {
    const portal = portalWith({
      accounts: [
        { id: 7, role: "owner", status: "active" },
        { id: 8, role: "analyst", status: "active" }
      ],
      metrics
    });
    const auth = walletRollupAuthority(portal);
    expect(auth.state).toBe("restricted");
    expect(auth.note).toMatch(/1 account/);
  });

  it("says something different when the reader owns none of them", () => {
    const portal = portalWith({
      accounts: [{ id: 7, role: "viewer", status: "active" }],
      metrics
    });
    expect(walletRollupAuthority(portal).note).toMatch(/Only an account owner/);
  });

  it("does not report a zero total for no accounts", () => {
    const portal = portalWith({ accounts: [], metrics });
    expect(walletRollupAuthority(portal).display).toBe("Unavailable");
  });

  it("reports a degraded portal as unavailable", () => {
    expect(walletRollupAuthority(portalWith({ degraded: true, metrics })).state).toBe("unavailable");
  });

  /*
   * The server sums these totals over the wallets it could actually read and
   * reports how many it could not. A total short by an unknown amount, shown as
   * though it were whole, is the same lie as a fake zero and harder to catch.
   */
  it("refuses the total when a wallet behind it could not be loaded", () => {
    const portal = portalWith({
      metrics: { ...metrics, wallets_unavailable: 1 }
    });
    const auth = walletRollupAuthority(portal);
    expect(auth.state).toBe("unavailable");
    expect(auth.display).toBe("Unavailable");
    expect(auth.note).toMatch(/1 wallet/);
    expect(auth.spendableCents).toBeNull();
  });

  it("pluralises the missing-wallet count", () => {
    const portal = portalWith({ metrics: { ...metrics, wallets_unavailable: 3 } });
    expect(walletRollupAuthority(portal).note).toMatch(/3 wallets/);
  });

  it("carries the summed debt when every wallet was readable", () => {
    const portal = portalWith({
      metrics: {
        ...metrics,
        wallet_balance_cents: -50_000,
        wallet_balance: "-$500.00",
        spendable_balance_cents: 0,
        amount_owed_cents: 50_000,
        amount_owed: "$500.00"
      }
    });
    const auth = walletRollupAuthority(portal);
    expect(auth.state).toBe("confirmed");
    expect(auth.owedDisplay).toBe("$500.00");
  });
});

/* ------------------------------------------------------------------ *
 * Resume
 * ------------------------------------------------------------------ */

describe("resumeCheck — not offering a button that 404s", () => {
  const wallet = {
    account_id: 7,
    available_balance_cents: 100_000,
    available_balance: "$1,000.00",
    spendable_balance_cents: 100_000
  };

  it("allows an owner with budget and funds on an otherwise clear campaign", () => {
    const check = resumeCheck(portalWith({ wallets: [wallet] }), campaign({ status: "paused" }));
    expect(check.allowed).toBe(true);
    expect(check.reason).toBeNull();
  });

  it("blocks every non-owner, because reserve_campaign_budget is owner-only", () => {
    for (const role of ["campaign_manager", "marketing_manager", "analyst", "viewer"]) {
      const portal = portalWith({ accounts: [{ id: 7, role, status: "active" }], wallets: [wallet] });
      const check = resumeCheck(portal, campaign({ status: "paused" }));
      expect(check.allowed).toBe(false);
      expect(check.reason).toMatch(/owner/i);
    }
  });

  it("does not tell a reader they aren't the owner when no role was fetched", () => {
    // `accountRole` answers "viewer" for an account it cannot find. On the
    // fan-out path no roles are fetched at all, so an unguarded check would
    // disable every Resume button in the app and give each reader a false
    // reason for it. Let the attempt reach the server instead.
    expect(resumeCheck(null, campaign({ status: "paused" })).allowed).toBe(true);
    expect(resumeCheck(portalWith({ accounts: [] }), campaign({ status: "paused" })).allowed).toBe(true);
  });

  it("blocks a zero-budget campaign the server would reject", () => {
    const portal = portalWith({ wallets: [wallet] });
    const check = resumeCheck(portal, campaign({ daily_budget_cents: 0, lifetime_budget_cents: 0 }));
    expect(check.allowed).toBe(false);
    expect(check.reason).toMatch(/budget/i);
  });

  it("blocks on a confirmed balance below the reserve", () => {
    const portal = portalWith({
      wallets: [{ account_id: 7, available_balance_cents: 100, spendable_balance_cents: 100 }]
    });
    const check = resumeCheck(portal, campaign({ daily_budget_cents: 5_000 }));
    expect(check.allowed).toBe(false);
    expect(check.reason).toMatch(/too low/i);
  });

  it("does not block on an unconfirmed balance — that would be the fake zero again", () => {
    // Owner, but the wallet row is missing from the payload. Unknown is not zero.
    const portal = portalWith({ wallets: [] });
    expect(resumeCheck(portal, campaign()).allowed).toBe(true);
  });

  it("blocks when the account has not been verified", () => {
    const portal = portalWith({
      accounts: [{ id: 7, role: "owner", status: "pending_verification" }],
      wallets: [wallet]
    });
    const check = resumeCheck(portal, campaign({ status: "paused" }));
    expect(check.allowed).toBe(false);
    expect(check.reason).toMatch(/verification/i);
  });

  /*
   * This test used to assert the opposite, and the change is the point of the
   * phase rather than a relaxation of it.
   *
   * The old rule was `!gate.advertiserCanClear`: offer Resume for anything the
   * advertiser could fix themselves, because the server would accept it anyway
   * and the campaign would simply sit in `blocked`. That was an accurate
   * description of a server that checked only the wallet. `activation_blocker`
   * now enforces policy approval before a single cent is reserved, so the same
   * button would produce a refusal the reader had no warning was coming — and a
   * control that fails on press is the dead end §37 forbids, just at a smaller
   * scale than a page.
   */
  it("no longer offers resume on a campaign whose ad is still in review", () => {
    const portal = portalWith({ creatives: [], wallets: [wallet] });
    const check = resumeCheck(portal, campaign({ status: "paused" }));
    expect(check.allowed).toBe(false);
    expect(check.reason).toMatch(/creative|review/i);
  });

  it("still offers resume on an exhausted lifetime budget", () => {
    // The server's activation gate checks that a budget exists, not that it has
    // room left; the per-impression check does that. Refusing here would be the
    // client inventing a rule the server does not have.
    const portal = portalWith({ wallets: [wallet] });
    const check = resumeCheck(
      portal,
      campaign({ status: "paused", lifetime_budget_cents: 10_000, spent_cents: 10_000 })
    );
    expect(check.allowed).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * Selector helpers
 * ------------------------------------------------------------------ */

describe("selector helpers", () => {
  it("creativeIsDeliverable needs both columns", () => {
    expect(creativeIsDeliverable(approved())).toBe(true);
    expect(creativeIsDeliverable(approved({ status: "pending_review" }))).toBe(false);
    expect(creativeIsDeliverable(approved({ moderation_status: "pending" }))).toBe(false);
  });

  it("creativeMediaSatisfiesSelector ignores media_url, which the selector does too", () => {
    expect(creativeMediaSatisfiesSelector(approved({ creative_type: "text" }))).toBe(true);
    expect(
      creativeMediaSatisfiesSelector(approved({ creative_type: "image", media_url: "https://cdn/x.png" }))
    ).toBe(false);
    expect(creativeMediaSatisfiesSelector(approved({ creative_type: "image", media_asset_id: 3 }))).toBe(true);
    expect(creativeMediaSatisfiesSelector(approved({ creative_type: "audio", media_asset_id: 3 }))).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * Attribution
 * ------------------------------------------------------------------ */

describe("attribution", () => {
  it("tells an outcome campaign that nothing after the tap is measured", () => {
    for (const objective of [
      "marketplace",
      "marketplace_sales",
      "app_promotion",
      "event_promotion",
      "music_promotion",
      "video_promotion",
      "creator_growth",
      "creator_promotion"
    ]) {
      const note = attributionNote({ objective });
      expect(note).toBeTruthy();
      expect(note).toMatch(/aren’t tracked/);
      // The whole point is that it carries no figure. A digit here would be a
      // conversion count, and there is no conversion instrumentation to source
      // one from — `SponsoredAdCard` used to write `conversion` on viewability
      // and that write is gone.
      expect(note).not.toMatch(/\d/);
    }
  });

  it("says nothing to a campaign whose objective the platform does measure", () => {
    // Impressions and clicks are the whole of what an awareness or traffic
    // campaign asked for, so the note would be a disclaimer about a gap the
    // reader does not have.
    for (const objective of ["awareness", "brand_awareness", "traffic", "website_traffic", "engagement"]) {
      expect(attributionNote({ objective })).toBeNull();
    }
    expect(attributionNote({})).toBeNull();
    expect(attributionNote({ objective: "" })).toBeNull();
  });

  it("matches on the objective regardless of case", () => {
    expect(attributionNote({ objective: "Marketplace_Sales" })).toBeTruthy();
  });
});
