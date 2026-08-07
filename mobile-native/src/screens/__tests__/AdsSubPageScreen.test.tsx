/**
 * These four pages exist because "Not available in the app yet" is an accurate
 * sentence and still a dead end. `NO_DEAD_ENDS` is not satisfied by a disabled
 * tile; it is satisfied by a destination that says what the feature is, what
 * already governs the reader's campaigns without it, and what to do meanwhile.
 *
 * So what is tested here is not layout. It is that:
 *
 *   1. Every page ends in somewhere to go. A preview page whose only outcome is
 *      "come back later" has reproduced the locked tile one screen deeper.
 *   2. The account page distinguishes loading, loaded and failed — and shows the
 *      ad account number in exactly one of those three. A stale or absent id
 *      quoted to support is worse than no id, so a failure must not print one.
 *   3. The audience page states the prohibitions. They are the reason the
 *      feature is gated at all; a page that lists what you *will* be able to
 *      target and omits what you never can is an advertisement, not a rulebook.
 *   4. The Policy Center — the one page that reports server data about this
 *      advertiser rather than describing rules — never converts a failed
 *      request into reassurance, always prints a reason under a rejection, and
 *      offers no appeal button, because the appeals route is on a surface this
 *      deployment does not serve.
 *   5. The Creative library, which stopped being a rulebook, holds the same
 *      line: a failed fetch is not an empty library, an action is offered only
 *      when the server would accept it, and a reader who can't act is told why
 *      rather than shown a shorter page.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0
}));

const mockList = jest.fn();
const mockCached = jest.fn();
jest.mock("../../api/businessOs", () => ({
  ...jest.requireActual("../../api/businessOs"),
  listAdAccounts: () => mockList(),
  loadCachedAdAccounts: () => mockCached()
}));

const mockPortal = jest.fn();
jest.mock("../../api/adsPortal", () => ({
  ...jest.requireActual("../../api/adsPortal"),
  getAdsPortal: () => mockPortal()
}));

// Only the write call is mocked. Grouping, ordering and which actions are
// offered are the module's own logic and are exercised through the screen —
// mocking them would test the mock.
const mockRunAction = jest.fn();
jest.mock("../../api/adsCreatives", () => ({
  ...jest.requireActual("../../api/adsCreatives"),
  runCreativeAction: (...args: unknown[]) => mockRunAction(...args)
}));

import { normalizeAdsPortal } from "../../api/adsPortal";
import { AdsSubPageScreen } from "../AdsSubPageScreen";

const ACCOUNT = { id: 8, business_name: "Roody Goods", status: "active" };

/** A portal carrying only the review board, which is all the Policy Center reads. */
function boardPortal(review_board: any[]) {
  return { ok: true, portal: normalizeAdsPortal({ review_board } as any) };
}

/** A portal carrying creatives and the accounts they hang off, which is what the library reads. */
function libraryPortal(creatives: any[], accounts: any[] = [{ id: 8, role: "owner" }]) {
  return { ok: true, portal: normalizeAdsPortal({ creatives, accounts } as any) };
}

function nav() {
  return { navigate: jest.fn(), goBack: jest.fn() };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockList.mockResolvedValue({ accounts: [ACCOUNT] });
  mockCached.mockResolvedValue([]);
  mockPortal.mockResolvedValue(boardPortal([]));
  mockRunAction.mockResolvedValue({ ok: true });
});

describe("ads sub-pages — no dead ends", () => {
  it.each([
    ["audiences", "Create campaign", "Account details"],
    ["creatives", "Create campaign", "Ad reports"],
    // Not "Verification Center": that opens the profile-badge track, which
    // never touches `pulse_ad_accounts.status` and so cannot make this account
    // deliver. The second destination is the surface that can.
    ["account", "Open ad wallet", "Account standing and verification"],
    ["policy", "Edit a campaign's creative", "Creative rules"]
  ] as const)("gives %s two places to go next", async (surface, primary, secondary) => {
    const navigation = nav();
    const view = render(
      <AdsSubPageScreen surface={surface} navigation={navigation as never} />
    );
    await waitFor(() => expect(view.queryByText(primary)).toBeTruthy());
    expect(view.getByText(secondary)).toBeTruthy();

    await act(async () => {
      fireEvent.press(view.getByText(primary));
    });
    expect(navigation.navigate).toHaveBeenCalled();
  });

  /**
   * The tap that got here came from a tile labelled "See what targeting
   * applies". If the page then only says the feature is unavailable, the label
   * was bait.
   *
   * What it must not do instead is describe a targeting system the reader's
   * campaigns are not in. The copy this replaces quoted the allowlists in
   * `services/business_os/advertising/targeting.py`, which are real and which
   * govern `business_os_ad_sets` — a table read by nothing outside its own
   * package. The stack these screens are on has no audience at all.
   */
  it("says plainly that no targeting is applied, rather than describing another stack's rules", async () => {
    const view = render(<AdsSubPageScreen surface="audiences" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("No audience narrowing is applied")).toBeTruthy());
    expect(view.getByText("What isn’t collected, so can’t be targeted")).toBeTruthy();
    expect(
      view.getByText(
        "Health, religion, politics, race or ethnicity, sexual orientation, gender identity"
      )
    ).toBeTruthy();
    // The claims that described a validator which does not exist, because on
    // this stack there is nothing for it to validate.
    expect(view.queryByText("What an audience will be able to narrow")).toBeNull();
    expect(view.queryByText(/refused by name/)).toBeNull();
    expect(view.queryByText("Age, from 18 upward")).toBeNull();
  });

  /**
   * The placement list used to be two hardcoded words, "Feed" and "Reels".
   * `seed_placements` writes twelve rows and none of them is Reels, so the
   * advertiser choosing where to spend could not see Marketplace, Search or
   * Pulse Radio — and could see one option that does not exist.
   */
  it("lists the placements the server actually serves", async () => {
    mockPortal.mockResolvedValue({
      ok: true,
      portal: normalizeAdsPortal({
        placements: {
          feed_inline: { display_name: "Feed inline signal", device_type: "all", max_frequency: 6 },
          marketplace_sponsor: {
            display_name: "Marketplace sponsor",
            device_type: "all",
            max_frequency: 5
          },
          status_interstitial: {
            display_name: "Status interstitial",
            device_type: "mobile",
            max_frequency: 3
          }
        }
      } as any)
    });
    const view = render(<AdsSubPageScreen surface="audiences" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Feed inline signal")).toBeTruthy());
    expect(view.getByText("Marketplace sponsor")).toBeTruthy();
    expect(view.getByText("Status interstitial")).toBeTruthy();
    // The device constraint is real — `select_ads` enforces `p.device_type` in
    // SQL — so it is worth stating next to the placement it constrains.
    expect(view.getByText(/Mobile only/)).toBeTruthy();
    // And Reels was never one of them.
    expect(view.queryByText("Reels")).toBeNull();
  });

  /**
   * §31: a failed request is `Unavailable`, not a shorter catalogue. The old
   * hardcoded list would have been a tempting fallback here, and it was already
   * wrong once, so there is no fallback.
   */
  it("calls a failed placement fetch unavailable rather than falling back to a guess", async () => {
    mockPortal.mockRejectedValue(new Error("offline"));
    const view = render(<AdsSubPageScreen surface="audiences" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText(/list of placements didn’t load/)).toBeTruthy());
    expect(view.queryByText("Feed")).toBeNull();
    expect(view.queryByText("Reels")).toBeNull();
    // The page still answers the question it was opened to answer.
    expect(view.getByText("No audience narrowing is applied")).toBeTruthy();
  });

  it("states the creative rules the server already enforces", async () => {
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Media has to be yours")).toBeTruthy());
    expect(view.getByText("Review is per creative, and edits are versioned")).toBeTruthy();
  });

  /**
   * `VALID_CREATIVE_TYPES` (pulse_ads_service.py:52) is
   * `{image, video, text, hologram, audio}`. The page listed "Image, Video,
   * Reels video" — one format that doesn't exist and three real ones missing,
   * so an advertiser could not discover that text, hologram or audio creatives
   * were available at all.
   */
  it("lists the five creative types the server accepts, and not Reels", async () => {
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("What counts as a creative")).toBeTruthy());
    expect(view.getByText("Image — needs an uploaded image")).toBeTruthy();
    expect(view.getByText("Audio — needs an uploaded audio file")).toBeTruthy();
    expect(view.getByText("Text — no media")).toBeTruthy();
    expect(view.getByText("Hologram — no media")).toBeTruthy();
    expect(view.queryByText("Reels video")).toBeNull();
  });

  /**
   * `validate_destination_url` (:253) does a prefix test, not a lookup, and
   * accepts plain http (:272). The page claimed destinations were "checked for
   * existence" and that external links "must be HTTPS" — both stricter than the
   * server, which is the direction that costs the advertiser money: they stop
   * checking their own links and pay for clicks into a 404.
   */
  it("does not claim destinations are checked for existence or forced to https", async () => {
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Where a creative can send people")).toBeTruthy());
    expect(view.getByText(/checked for shape rather than for whether it works/)).toBeTruthy();
    expect(
      view.getByText("An external http or https address — http is accepted, so use https yourself")
    ).toBeTruthy();
    expect(view.getByText("/pulse/admin and /pulse/api are refused")).toBeTruthy();
    expect(view.queryByText(/checked for existence/)).toBeNull();
    expect(view.queryByText("An external HTTPS address")).toBeNull();
    expect(view.queryByText("A post or a Reel")).toBeNull();
  });

  /**
   * Media rights are the one part of this page that was already true, so the
   * rewrite has to not lose it: ownership and ad-account scoping
   * (`_owned_ad_media_asset`), the creative/media type match
   * (`_asset_type_allowed`, called at :907) and the refusal of pasted URLs
   * (:894) are all real. Only "finished processing" was dropped, because the
   * sole readiness gate is a non-empty public URL.
   */
  it("keeps the media rights claims that are real, and drops the processing one", async () => {
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Media has to be yours")).toBeTruthy());
    expect(view.getByText(/belongs to you and to this ad account/)).toBeTruthy();
    expect(view.getByText(/a video creative will not accept an image/)).toBeTruthy();
    expect(view.getByText(/Pasting a media URL instead of uploading is refused/)).toBeTruthy();
    expect(view.queryByText(/finished processing/)).toBeNull();
  });
});

describe("ads sub-pages — account details", () => {
  /**
   * This is where the ad account number went when it was taken out of the
   * dashboard header, so it has to actually be here — labelled, and next to the
   * reason someone would need it.
   */
  it("shows the account number under a label that says what it's for", async () => {
    const view = render(<AdsSubPageScreen surface="account" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Ad account number")).toBeTruthy());
    expect(view.getByText("8")).toBeTruthy();
    expect(view.getByText("Roody Goods")).toBeTruthy();
    expect(view.getByText("Advertising account · Active")).toBeTruthy();
  });

  /** With several accounts, the one the caller asked for is the one shown. */
  it("honours the account the caller asked for", async () => {
    mockList.mockResolvedValue({
      accounts: [ACCOUNT, { id: 12, business_name: "Second shop", status: "pending" }]
    });
    const view = render(
      <AdsSubPageScreen
        surface="account"
        route={{ params: { accountId: 12 } }}
        navigation={nav() as never}
      />
    );
    await waitFor(() => expect(view.queryByText("Second shop")).toBeTruthy());
    expect(view.getByText("12")).toBeTruthy();
    expect(view.getByText("Advertising account · Verification pending")).toBeTruthy();
  });

  /**
   * §31 forbids a fake zero after a service failure, and an account number is
   * the worst field to guess at: someone quotes it to support. A failed load
   * says it failed and offers a retry — it does not print an id.
   */
  it("names the failure and prints no number when nothing loaded", async () => {
    mockList.mockRejectedValue(new Error("offline"));
    mockCached.mockResolvedValue([]);
    const view = render(<AdsSubPageScreen surface="account" navigation={nav() as never} />);
    await waitFor(() =>
      expect(view.queryByText("Your account details didn't load.")).toBeTruthy()
    );
    expect(view.queryByText("Ad account number")).toBeNull();
  });

  /**
   * Cached is not the same as failed. §31 also forbids blanking a screen that
   * has usable cached data, so a network failure with something in the cache
   * shows the account rather than the error.
   */
  it("falls back to the cache rather than blanking", async () => {
    mockList.mockRejectedValue(new Error("offline"));
    mockCached.mockResolvedValue([ACCOUNT]);
    const view = render(<AdsSubPageScreen surface="account" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Ad account number")).toBeTruthy());
    expect(view.queryByText("Your account details didn't load.")).toBeNull();
  });

  /** The retry is a real second attempt, not a re-render of the same failure. */
  it("retries the load when asked", async () => {
    mockList.mockRejectedValueOnce(new Error("offline"));
    const view = render(<AdsSubPageScreen surface="account" navigation={nav() as never} />);
    await waitFor(() =>
      expect(view.queryByText("Your account details didn't load.")).toBeTruthy()
    );

    mockList.mockResolvedValue({ accounts: [ACCOUNT] });
    await act(async () => {
      fireEvent.press(view.getByText("Try again"));
    });
    await waitFor(() => expect(view.queryByText("Ad account number")).toBeTruthy());
  });
});

/**
 * The Policy Center is the only one of the four pages that reports server data
 * about this advertiser's own creatives, so it is the only one where being
 * wrong has a cost beyond a wasted tap: an advertiser told "nothing to review"
 * while an ad sits rejected stops looking.
 */
describe("ads sub-pages — Policy Center", () => {
  const REJECTED = {
    review_id: 1,
    creative_id: 10,
    title: "Summer banner",
    campaign_name: "Spring push",
    moderation_status: "rejected",
    review_reason: "The image contains text covering more than 20% of the frame.",
    automated_review_status: "rejected",
    risk_score: 64
  };

  it("shows the verdict, the reason and what to do about it", async () => {
    mockPortal.mockResolvedValue(boardPortal([REJECTED]));
    const view = render(<AdsSubPageScreen surface="policy" navigation={nav() as never} />);

    await waitFor(() => expect(view.queryByText("Summer banner")).toBeTruthy());
    expect(view.getByText("Rejected")).toBeTruthy();
    expect(view.getByText("Needs your attention")).toBeTruthy();
    expect(
      view.getByText("The image contains text covering more than 20% of the frame.")
    ).toBeTruthy();
    expect(view.getByText("Risk score 64 of 100")).toBeTruthy();
    expect(view.getByText(/Edit the creative to address the reason above/)).toBeTruthy();
  });

  /**
   * §37 forbids an inaccessible policy reason. The backend can reject without
   * populating either reason column, and a card with a verdict and a blank space
   * under it is inaccessible in the way that matters.
   */
  it("admits when no reason was recorded rather than printing nothing", async () => {
    mockPortal.mockResolvedValue(
      boardPortal([{ ...REJECTED, review_reason: "", rejection_reason: "" }])
    );
    const view = render(<AdsSubPageScreen surface="policy" navigation={nav() as never} />);
    await waitFor(() =>
      expect(view.queryByText("No reason was recorded for this decision.")).toBeTruthy()
    );
  });

  /**
   * The appeals endpoint is `POST /api/business-os/advertising/appeals`, on the
   * canonical surface, which answers 404 unless `BUSINESS_OS_ADVERTISING` is
   * set. A button posting there converts "I don't know what to do" into "I did
   * the thing and nothing happened", so there is no button and no such promise.
   */
  it("offers no appeal, because the appeals route is dark in this deployment", async () => {
    mockPortal.mockResolvedValue(boardPortal([REJECTED]));
    const view = render(<AdsSubPageScreen surface="policy" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Summer banner")).toBeTruthy());
    expect(view.queryByText(/appeal/i)).toBeNull();
  });

  /** An automated flag nobody has looked at is still moving. Telling that reader
   *  to rewrite the ad would be wrong; the instruction is to wait. */
  it("tells a pending creative to wait and a human-upheld rejection to escalate", async () => {
    mockPortal.mockResolvedValue(
      boardPortal([
        { review_id: 2, creative_id: 11, title: "Pending banner", moderation_status: "pending" },
        {
          ...REJECTED,
          review_id: 3,
          title: "Upheld banner",
          human_review_status: "rejected"
        }
      ])
    );
    const view = render(<AdsSubPageScreen surface="policy" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Upheld banner")).toBeTruthy());
    expect(view.getByText(/No action is needed/)).toBeTruthy();
    expect(view.getByText(/A reviewer upheld this rejection/)).toBeTruthy();
    // Both groups render. "In review" appears twice on purpose — once as the
    // group heading and once as the pending row's pill — because the heading
    // and the badge naming the same state differently is the kind of seam that
    // makes a reader wonder whether they are the same thing.
    expect(view.getByText("Needs your attention")).toBeTruthy();
    expect(view.getAllByText("In review")).toHaveLength(2);
  });

  /**
   * §31, the load-bearing distinction on this page: a request that failed is
   * `Unavailable`, not an empty board. The message says so in as many words so
   * the reader does not conclude their ads are fine.
   */
  it("says a failed load is a failed load, not an empty board", async () => {
    mockPortal.mockRejectedValue(new Error("offline"));
    const view = render(<AdsSubPageScreen surface="policy" navigation={nav() as never} />);
    await waitFor(() =>
      expect(
        view.queryByText("Policy decisions didn't load. This doesn't mean there are none — try again.")
      ).toBeTruthy()
    );
    expect(view.queryByText("No activity yet")).toBeNull();
  });

  /** A degraded portal's empty board is an unmade request wearing an answer's clothes. */
  it("treats a degraded portal as unavailable rather than empty", async () => {
    const { portal } = boardPortal([]);
    mockPortal.mockResolvedValue({ ok: true, portal: { ...portal, degraded: true } });
    const view = render(<AdsSubPageScreen surface="policy" navigation={nav() as never} />);
    await waitFor(() =>
      expect(view.queryByText(/Policy decisions didn't load/)).toBeTruthy()
    );
  });

  /** A board that loaded and holds nothing is a real zero, and says so plainly. */
  it("distinguishes a genuinely empty board", async () => {
    mockPortal.mockResolvedValue(boardPortal([]));
    const view = render(<AdsSubPageScreen surface="policy" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("No activity yet")).toBeTruthy());
    expect(view.queryByText(/Policy decisions didn't load/)).toBeNull();
  });

  /** The retry is a real second request. */
  it("retries after a failure", async () => {
    mockPortal.mockRejectedValueOnce(new Error("offline"));
    const view = render(<AdsSubPageScreen surface="policy" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText(/Policy decisions didn't load/)).toBeTruthy());

    mockPortal.mockResolvedValue(boardPortal([REJECTED]));
    await act(async () => {
      fireEvent.press(view.getByText("Try again"));
    });
    await waitFor(() => expect(view.queryByText("Summer banner")).toBeTruthy());
  });

  /**
   * §37 forbids the duplicate notice. "How a decision is made" carries the
   * escalation route, and it is one fact about the page rather than one fact per
   * decision — three rejections must not produce three copies of it.
   */
  it("states how decisions work once, not once per card", async () => {
    mockPortal.mockResolvedValue(
      boardPortal([
        REJECTED,
        { ...REJECTED, review_id: 4, title: "Second banner" },
        { ...REJECTED, review_id: 5, title: "Third banner" }
      ])
    );
    const view = render(<AdsSubPageScreen surface="policy" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Third banner")).toBeTruthy());
    expect(view.getAllByText("How a decision is made")).toHaveLength(1);
  });
});

/**
 * The Creative library used to be four cards of prose about rules while
 * `portal.creatives` carried the advertiser's actual creatives. A tile called
 * "Creative library" that opened a rulebook is the empty locked card with no
 * useful destination §37 forbids, one screen deeper.
 *
 * What is tested here is the same discipline the Policy Center holds, plus one
 * thing only this page has: buttons that write. A control the server would
 * refuse is worse than an absent one, so every offered action has to match a
 * server rule.
 */
describe("ads sub-pages — Creative library", () => {
  const DRAFT = {
    id: 21,
    ad_account_id: 8,
    title: "Autumn teaser",
    campaign_name: "Autumn push",
    status: "draft",
    moderation_status: "draft",
    media_ready: true
  };

  const REJECTED_CREATIVE = {
    id: 22,
    ad_account_id: 8,
    title: "Summer banner",
    campaign_name: "Spring push",
    // The lifecycle column stays where submit left it; only the verdict moves.
    // This is exactly the row that must not be offered a Delete button.
    status: "pending_review",
    moderation_status: "rejected",
    rejection_reason: "The image contains text covering more than 20% of the frame.",
    media_ready: true
  };

  it("lists the advertiser's real creatives instead of describing the rules", async () => {
    mockPortal.mockResolvedValue(libraryPortal([DRAFT, REJECTED_CREATIVE]));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);

    await waitFor(() => expect(view.queryByText("Summer banner")).toBeTruthy());
    expect(view.getByText("Autumn teaser")).toBeTruthy();
    expect(view.getByText("Spring push")).toBeTruthy();
    expect(view.getByText("Needs your attention")).toBeTruthy();
    expect(view.getByText("Rejected")).toBeTruthy();
  });

  /**
   * §37 forbids an inaccessible policy reason, and the library is where a
   * rejected creative is most likely to be looked at.
   */
  it("carries the rejection reason on the row, and admits when none was recorded", async () => {
    mockPortal.mockResolvedValue(libraryPortal([REJECTED_CREATIVE]));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() =>
      expect(
        view.queryByText(/The image contains text covering more than 20% of the frame\./)
      ).toBeTruthy()
    );

    mockPortal.mockResolvedValue(
      libraryPortal([{ ...REJECTED_CREATIVE, rejection_reason: "" }])
    );
    const bare = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(bare.queryByText(/no reason was recorded/i)).toBeTruthy());
  });

  /**
   * `delete_draft` checks `status` *and* `moderation_status` and answers 409
   * otherwise. A rejected creative keeps `status='pending_review'`, so the
   * button must not be there — §31 forbids an active-looking control that
   * cannot complete.
   */
  it("offers no Delete on a rejected creative, and no Submit on one already submitted", async () => {
    mockPortal.mockResolvedValue(libraryPortal([REJECTED_CREATIVE]));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Summer banner")).toBeTruthy());

    expect(view.queryByText("Delete draft")).toBeNull();
    expect(view.queryByText("Submit for review")).toBeNull();
    // Duplicate is the one route out of a rejection this surface actually
    // serves, so it has to be present.
    expect(view.getByText("Duplicate")).toBeTruthy();
  });

  it("offers Submit and Delete on a draft, where the server accepts both", async () => {
    mockPortal.mockResolvedValue(libraryPortal([DRAFT]));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Autumn teaser")).toBeTruthy());
    expect(view.getByText("Submit for review")).toBeTruthy();
    expect(view.getByText("Delete draft")).toBeTruthy();
  });

  /** The action reaches the server, and the list is re-read rather than guessed at. */
  it("runs an action and reloads from the server afterwards", async () => {
    mockPortal.mockResolvedValue(libraryPortal([DRAFT]));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Submit for review")).toBeTruthy());

    const before = mockPortal.mock.calls.length;
    await act(async () => {
      fireEvent.press(view.getByText("Submit for review"));
    });

    expect(mockRunAction).toHaveBeenCalledWith(21, "submit");
    await waitFor(() => expect(mockPortal.mock.calls.length).toBeGreaterThan(before));
    expect(view.getByText(/Submitted for review/)).toBeTruthy();
  });

  /**
   * The server's own refusal is the only sentence that tells the reader what to
   * do instead. A generic "something went wrong" over a specific 409 throws it
   * away.
   */
  it("shows the server's refusal verbatim rather than a generic failure", async () => {
    mockPortal.mockResolvedValue(libraryPortal([DRAFT]));
    mockRunAction.mockResolvedValue({
      error: "Only draft creatives can be deleted. Archive this creative instead."
    });
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Delete draft")).toBeTruthy());

    await act(async () => {
      fireEvent.press(view.getByText("Delete draft"));
    });
    await waitFor(() =>
      expect(
        view.queryByText("Only draft creatives can be deleted. Archive this creative instead.")
      ).toBeTruthy()
    );
  });

  it("says nothing was changed when the request itself fell over", async () => {
    mockPortal.mockResolvedValue(libraryPortal([DRAFT]));
    mockRunAction.mockRejectedValue(new Error("offline"));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Duplicate")).toBeTruthy());

    await act(async () => {
      fireEvent.press(view.getByText("Duplicate"));
    });
    await waitFor(() => expect(view.queryByText(/Nothing was changed/)).toBeTruthy());
  });

  /**
   * A viewer gets the same list and no buttons. Silently omitting the controls
   * would leave them unable to tell "there is nothing to do" from "I'm not
   * allowed", so the reason is printed — once, above the list, because §37
   * forbids the duplicate notice.
   */
  it("explains a read-only role once instead of quietly dropping the buttons", async () => {
    mockPortal.mockResolvedValue(
      libraryPortal([DRAFT, REJECTED_CREATIVE], [{ id: 8, role: "viewer" }])
    );
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Summer banner")).toBeTruthy());

    expect(view.queryByText("Duplicate")).toBeNull();
    expect(view.getByText("You can see these but can't change them")).toBeTruthy();
    // Once. Two creatives, one notice — the reason is a fact about the reader's
    // role, not a fact about each row.
    expect(view.getAllByText(/read-only/)).toHaveLength(1);
  });

  /**
   * A library can span accounts, and authority is per account — the server
   * re-derives the role and answers 403, so `roles.current` reading `owner`
   * proves nothing about the second account. A blanket notice would be wrong
   * about half the list, so the reason moves to the rows it applies to.
   */
  it("offers actions on the account the reader owns and explains the one they don't", async () => {
    mockPortal.mockResolvedValue(
      libraryPortal(
        [DRAFT, { ...REJECTED_CREATIVE, ad_account_id: 9 }],
        [
          { id: 8, role: "owner" },
          { id: 9, role: "analyst" }
        ]
      )
    );
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Summer banner")).toBeTruthy());

    // The owned draft keeps its buttons.
    expect(view.getByText("Submit for review")).toBeTruthy();
    // The analyst-only row says why it has none, and the page-level card is
    // absent because it would be false for the other half of the list.
    expect(view.getByText(/Your analyst access can read reports/)).toBeTruthy();
    expect(view.queryByText("You can see these but can't change them")).toBeNull();
  });

  /** §31's load-bearing distinction, again: a failed fetch is not an empty library. */
  it("says a failed load is a failed load, not an empty library", async () => {
    mockPortal.mockRejectedValue(new Error("offline"));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() =>
      expect(
        view.queryByText("Your creatives didn't load. This doesn't mean you have none — try again.")
      ).toBeTruthy()
    );
    expect(view.queryByText("No activity yet")).toBeNull();
  });

  it("treats a degraded portal as unavailable rather than empty", async () => {
    const { portal } = libraryPortal([]);
    mockPortal.mockResolvedValue({ ok: true, portal: { ...portal, degraded: true } });
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText(/Your creatives didn't load/)).toBeTruthy());
  });

  it("distinguishes a genuinely empty library", async () => {
    mockPortal.mockResolvedValue(libraryPortal([]));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("No activity yet")).toBeTruthy());
    expect(view.queryByText(/Your creatives didn't load/)).toBeNull();
  });

  it("retries after a failure", async () => {
    mockPortal.mockRejectedValueOnce(new Error("offline"));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText(/Your creatives didn't load/)).toBeTruthy());

    mockPortal.mockResolvedValue(libraryPortal([DRAFT]));
    await act(async () => {
      fireEvent.press(view.getByText("Try again"));
    });
    await waitFor(() => expect(view.queryByText("Autumn teaser")).toBeTruthy());
  });

  /**
   * The rules did not go away. A rejection reason is only actionable against
   * the rule it cites, and the Policy Center's "Creative rules" button lands
   * here — it must still find them.
   */
  it("keeps the rules on the page, below the library rather than instead of it", async () => {
    mockPortal.mockResolvedValue(libraryPortal([REJECTED_CREATIVE]));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Summer banner")).toBeTruthy());
    expect(view.getByText("Media has to be yours")).toBeTruthy();
    expect(view.getByText("What every creative is checked against")).toBeTruthy();
  });

  /**
   * The page used to open with "A browsable creative library isn't in the app
   * yet." It is now, and a disclaimer contradicting the list under it is the
   * contradictory state the mission's governing rule forbids.
   */
  it("no longer claims the library doesn't exist", async () => {
    mockPortal.mockResolvedValue(libraryPortal([DRAFT]));
    const view = render(<AdsSubPageScreen surface="creatives" navigation={nav() as never} />);
    await waitFor(() => expect(view.queryByText("Autumn teaser")).toBeTruthy());
    expect(view.queryByText(/isn't in the app yet/)).toBeNull();
  });
});
