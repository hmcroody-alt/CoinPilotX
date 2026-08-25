/**
 * §13, §14.14 — tapping a suggested reel opens the real Reels player.
 *
 * This is the requirement most likely to regress into something that *looks*
 * right. A suggested reel that opens a post-detail screen, or opens Reels
 * without a reel id, or opens the root-stack copy of Reels instead of the tab
 * one, all render a plausible screen — the user just does not get the video they
 * tapped, or gets it under a bottom dock that has stopped responding.
 *
 * So these assert the whole navigation payload, not that navigation happened.
 * `toHaveBeenCalled()` would pass for every one of those failures.
 */
import { act, renderHook } from "@testing-library/react-native";
import { useHomeDiscovery } from "../useHomeDiscovery";
import { clearReelTransfer, peekReelTransfer } from "../reelTransfer";
import { resetDiscoveryImpressions, setDiscoveryAnalyticsSink } from "../analytics";
import { __clearDiscoveryFlagOverrides, __setDiscoveryFlagOverride } from "../flags";
import type { PulseReel } from "../../api/reels";
import type { ReelSuggestion } from "../discoveryRows";

jest.mock("../sources", () => ({
  loadDiscoveryModules: jest.fn().mockResolvedValue({ modules: [], failures: {} })
}));
jest.mock("../dismissals", () => ({
  loadDismissedModules: jest.fn().mockResolvedValue(new Set()),
  dismissModule: jest.fn((kind: string) => new Set([kind])),
  loadDismissedPeople: jest.fn().mockResolvedValue(new Set()),
  dismissPerson: jest.fn((key: string) => new Set([key]))
}));
jest.mock("../../api/friends", () => ({ sendFriendRequest: jest.fn().mockResolvedValue({}) }));
jest.mock("../../api/groups", () => ({ joinGroup: jest.fn().mockResolvedValue({}) }));

const navigate = jest.fn();

function renderDiscovery() {
  return renderHook(() =>
    useHomeDiscovery({ navigation: { navigate } as never, enabled: true })
  );
}

/**
 * A reel exactly as `listReels` returns it, which is what gets staged.
 *
 * The two ids differ deliberately. `normalizeReel` collapses them to a single
 * value, so a fixture with `id === reel_id` cannot tell whether the navigation
 * param and the transfer key were resolved by the same rule — it passes even if
 * one of them reads `id` and the other reads `reel_id`. With them split, these
 * assertions pin the whole handoff to `reel_id`, which is the one both sides and
 * `normalizeReel` agree on.
 */
const rawReel = { id: 900, reel_id: 42, title: "Exact" } as unknown as PulseReel;

const suggestion: ReelSuggestion = {
  reelId: 42,
  title: "Exact",
  previewVideoUrl: "https://cdn.example/42.m3u8",
  source: rawReel
};

beforeEach(() => {
  navigate.mockClear();
  clearReelTransfer();
  resetDiscoveryImpressions();
  setDiscoveryAnalyticsSink(() => undefined);
  __setDiscoveryFlagOverride("homeDiscoveryEnabled", true);
});

afterEach(() => {
  setDiscoveryAnalyticsSink(null);
  __clearDiscoveryFlagOverrides();
});

describe("§14.14 — a suggested reel opens Reels, on that reel", () => {
  it("navigates to the Reels screen and not to a post detail", async () => {
    const { result } = renderDiscovery();

    await act(async () => {
      result.current.actions.onOpenReel(suggestion);
    });

    expect(navigate).toHaveBeenCalledTimes(1);
    const [route, params] = navigate.mock.calls[0];
    expect(route).toBe("Tabs");
    expect((params as { screen: string }).screen).toBe("Reels");
  });

  it("carries the exact reel id that was in the thumbnail", async () => {
    // The whole point of the row. An id of undefined, or the first reel of the
    // lane, is the "it showed me the wrong video" bug.
    const { result } = renderDiscovery();

    await act(async () => {
      result.current.actions.onOpenReel(suggestion);
    });

    const params = navigate.mock.calls[0][1] as { params: { reelId: number } };
    expect(params.params.reelId).toBe(42);
  });

  it("opens the tab instance, not the root-stack copy", async () => {
    // Only the tab sits inside BottomNavVisibilityProvider. The stack copy plays
    // the right reel under a dock that has stopped listening to swipes.
    const { result } = renderDiscovery();

    await act(async () => {
      result.current.actions.onOpenReel(suggestion);
    });

    expect(navigate.mock.calls[0][0]).toBe("Tabs");
  });

  it("stages the reel payload so the player renders it on its first frame", async () => {
    // Without this the player shows whatever the lane returns while it looks for
    // the right reel — an unrelated video, for exactly as long as that takes.
    const { result } = renderDiscovery();

    await act(async () => {
      result.current.actions.onOpenReel(suggestion);
    });

    const params = navigate.mock.calls[0][1] as { params: { reelTransferNonce?: string } };

    // The nonce is what tells the player "this navigation has a payload waiting"
    // — without it a re-render would re-consume a stale slot.
    expect(params.params.reelTransferNonce).toEqual(expect.stringContaining("reel-transfer:42"));
    expect(peekReelTransfer(42)).toBe(rawReel);
  });

  it("still opens the right reel when there is no payload to stage", async () => {
    // A suggestion built from a response the adapter could not carry whole must
    // degrade to a correct-but-slower open, never to a wrong one.
    const { result } = renderDiscovery();

    await act(async () => {
      result.current.actions.onOpenReel({ reelId: 7, title: "No payload" });
    });

    const params = navigate.mock.calls[0][1] as {
      screen: string;
      params: { reelId: number; reelTransferNonce?: string };
    };
    expect(params.screen).toBe("Reels");
    expect(params.params.reelId).toBe(7);
    expect(params.params.reelTransferNonce).toBeUndefined();
  });
});

describe("§13 — every other kind reaches its own canonical destination", () => {
  it("opens a status on StatusDetail, keyed by its id", async () => {
    const { result } = renderDiscovery();

    await act(async () => {
      result.current.actions.onOpenStatus({ statusId: 101, title: "One" });
    });

    expect(navigate).toHaveBeenCalledWith("StatusDetail", expect.objectContaining({ statusId: 101 }));
  });

  it("opens a group on GroupDetail, keyed by its slug", async () => {
    const { result } = renderDiscovery();

    await act(async () => {
      result.current.actions.onOpenGroup({ slug: "astro", name: "Astro" });
    });

    expect(navigate).toHaveBeenCalledWith("GroupDetail", expect.objectContaining({ groupSlug: "astro" }));
  });
});
