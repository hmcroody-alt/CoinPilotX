/**
 * The Profile surface, as rendered: the canvas, the tab strip, the grid tiles and
 * the clearance the dock needs — checked against the resolved graphite tokens
 * rather than against hex literals, so retuning the ramp does not turn this file
 * red for the wrong reason.
 *
 * What this file is really guarding is the rejected look. The screen used to
 * paint itself with a full-bleed decorative atmosphere and then stack
 * translucent navy panels on it, and no test could see either decision: the old
 * suites mocked the atmosphere away and asserted on behaviour. So the assertions
 * here are deliberately about *composition* — what layers exist, whether they
 * are opaque, whether anything is drawn over the whole page — not just about
 * colour values.
 */

import { fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";
import { StyleSheet } from "react-native";
import { PulsePost } from "../../api/feed";
import { colors } from "../../theme/colors";
import { profileSurface } from "../../theme/profileGraphite";
import {
  BOTTOM_NAV_CONTENT_CLEARANCE,
  BOTTOM_NAV_UNDOCKED_PADDING
} from "../../navigation/bottomNavMetrics";
import { BottomNavVisibilityProvider } from "../../navigation/BottomNavVisibility";
import { ProfileScreen } from "../ProfileScreen";

const navigate = jest.fn();
const mockListFeed = jest.fn();
const mockGetMyProfile = jest.fn();
const mockProfileErrorState = jest.fn(() => ({ title: "Profile unavailable", body: "Try again.", retryable: true, offline: false }));
// A variable rather than an inline `async () => null`, because the offline state is
// only reachable when the cache *hits*: the screen falls back to a saved profile
// and reports the outage over it. With a permanently empty cache that branch is
// unreachable and the offline banner could never be asserted.
const mockLoadCachedProfileEntry = jest.fn<Promise<{ value: unknown; ageMs: number } | null>, [string]>(async () => null);

// Mirrors the dock's own arithmetic for the device the safe-area mock below
// describes. Written out rather than imported wholesale so that a change to the
// *formula* (not just to a constant) has to be acknowledged here too.
const SAFE_AREA_BOTTOM = 34;
const DOCKED_CLEARANCE = Math.max(SAFE_AREA_BOTTOM, 12) + BOTTOM_NAV_CONTENT_CLEARANCE;
const UNDOCKED_CLEARANCE = Math.max(SAFE_AREA_BOTTOM, 12) + BOTTOM_NAV_UNDOCKED_PADDING;

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(), setItem: jest.fn(), removeItem: jest.fn()
}));

jest.mock("@expo/vector-icons", () => ({ Ionicons: ({ name }: { name: string }) => name }));

// Forwarded onto a View rather than collapsed to children: the canvas IS a
// gradient, so a mock that discards `colors` would make every assertion in the
// first describe block unfalsifiable.
jest.mock("expo-linear-gradient", () => {
  const { View } = require("react-native");
  return { LinearGradient: View };
});

jest.mock("../../api/profile", () => ({
  getMyProfile: () => mockGetMyProfile(),
  getPublicProfile: jest.fn(),
  listPublicProfilePosts: (...args: unknown[]) => mockListFeed(...args),
  loadCachedProfileEntry: (key: string) => mockLoadCachedProfileEntry(key),
  profileErrorState: () => mockProfileErrorState(),
  toggleProfileFollow: jest.fn()
}));
jest.mock("../../api/feed", () => ({
  listFeed: (...args: unknown[]) => mockListFeed(...args),
  pulsePostUrl: jest.fn(), reactToPost: jest.fn(), repostPost: jest.fn(), deletePost: jest.fn(),
  savablePostId: (post: PulsePost) => post.id
}));
// The header has its own suite. Stubbed here so this file is about the screen's
// own surfaces and cannot pass or fail on the header's.
jest.mock("../../components/ProfileHeader", () => ({
  ProfileHeader: () => null,
  PROFILE_HERO_HEIGHT: 320
}));
jest.mock("../../navigation/refreshCoordinator", () => ({ registerRefreshDestination: jest.fn(() => jest.fn()) }));
jest.mock("../../social/actionGuard", () => ({ actionKey: jest.fn(), useSocialActionGuard: () => ({ run: jest.fn(), isItemBusy: () => false }) }));
jest.mock("../../social/savedStore", () => ({ peekSaveState: jest.fn() }));
jest.mock("../../social/useSaveAction", () => ({ setSaved: jest.fn() }));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn() }));
jest.mock("../../core/eventSync", () => ({ invalidateNativeSync: jest.fn() }));
jest.mock("../../api/messenger", () => ({ openDirectConversation: jest.fn() }));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7 } } }) }));
jest.mock("../../api/premiumCenter", () => ({
  ...jest.requireActual("../../api/premiumCenter"),
  getPremiumCenter: jest.fn(() => Promise.reject(new Error("premium status not loaded in this test")))
}));

// Real hook, real provider, mocked device. `useBottomNavSurface` is the thing
// under test for the clearance cases, so mocking it away — which every other
// Profile suite does — would leave the overlap fix asserted by nothing.
//
// `docked` is switched by mounting or omitting `BottomNavVisibilityProvider`
// rather than by stubbing `useBottomNavVisibility`, for two reasons. The first is
// that stubbing it does not work: `useBottomNavSurface` calls its neighbour by
// lexical reference inside the same module, so replacing the module's *export*
// leaves the internal call site untouched and every clearance assertion silently
// measures the undocked branch. The second is that presence-of-a-provider is
// exactly what `docked` means in production — true under the tab navigator, false
// in a pushed stack instance — so this is the real switch, not a simulation of it.
jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 59, bottom: SAFE_AREA_BOTTOM, left: 0, right: 0 })
}));
jest.mock("../../navigation/useIsFocusedIfNavigated", () => ({ useIsFocusedIfNavigated: () => true }));

const surface = profileSurface(colors);

/**
 * Mount Profile as the dock's tab instance (default) or as a pushed instance.
 *
 * @param docked whether a bottom-nav dock is present beneath the screen.
 */
function renderProfile({ docked = true }: { docked?: boolean } = {}) {
  const screen = <ProfileScreen navigation={{ navigate } as never} />;
  return render(docked ? <BottomNavVisibilityProvider>{screen}</BottomNavVisibilityProvider> : screen);
}

/**
 * The canvas is deliberately hidden from assistive technology, and RNTL excludes
 * hidden elements from `*ByTestId` by default — so every query for it has to opt
 * back in. Wrapped in a helper so a future query cannot forget and read the
 * miss as "the canvas is gone".
 */
function canvas(screen: ReturnType<typeof render>, testID = "profile-canvas") {
  return screen.getByTestId(testID, { includeHiddenElements: true });
}

/**
 * The style of the control that *wraps* a label, found by walking the rendered
 * tree rather than by `element.parent`.
 *
 * `parent` returns the nearest host ancestor, which for a `Pressable` wrapping a
 * `Text` is not reliably the pressable's own view — it read as `undefined` for
 * every style on it. Walking down and remembering the parent of the matching text
 * node is unambiguous about which node is being measured.
 */
function controlStyle(tree: { toJSON: () => unknown }, label: string) {
  let found: unknown;
  const visit = (node: any, parent: any) => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) return node.forEach((child) => visit(child, parent));
    if (typeof node.children?.[0] === "string" && node.children[0] === label && parent) found = parent.props?.style;
    (node.children ?? []).forEach((child: any) => visit(child, node));
  };
  visit(tree.toJSON(), null);
  return StyleSheet.flatten(found) as {
    borderWidth?: number; backgroundColor?: string; borderColor?: string;
    shadowRadius?: number; shadowOpacity?: number; minHeight?: number;
  };
}

const posts: PulsePost[] = [
  { id: 1, post_id: 1, body: "A text-only identity post with no media at all" },
  { id: 2, post_id: 2, body: "Photo", media: [{ media_type: "image", thumbnail_url: "https://cdn.example/a.jpg" }] }
];

/** Every `backgroundColor` in the tree, flattened. */
function fills(tree: { toJSON: () => unknown }): string[] {
  const found: string[] = [];
  const visit = (node: any) => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) return node.forEach(visit);
    const flat = StyleSheet.flatten(node.props?.style) as { backgroundColor?: string } | undefined;
    if (flat?.backgroundColor) found.push(flat.backgroundColor);
    (node.children ?? []).forEach(visit);
  };
  visit(tree.toJSON());
  return found;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockLoadCachedProfileEntry.mockResolvedValue(null);
  mockGetMyProfile.mockResolvedValue({
    user_id: 7, display_name: "Roody Cherie", username: "roodycherie",
    public_player_id: "roodycherie", post_count: 2, bio: "Building PulseSoc."
  });
  mockListFeed.mockResolvedValue({ posts, next_offset: 2, has_more: false });
});

describe("the Profile canvas", () => {
  it("paints the approved graphite run and nothing else", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    const layer = canvas(screen);
    expect(layer.props.colors).toEqual([surface.canvasTop, surface.canvasBottom]);
    // The rejected look was 23 stars, two nebulae, a planet and a scrim. A canvas
    // with children is a canvas that can grow them back.
    expect(layer.props.children ?? null).toBeNull();
  });

  it("no longer subscribes to the decorative atmosphere", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    expect(screen.queryByTestId("profile-galactic-atmosphere", { includeHiddenElements: true })).toBeNull();
  });

  // The specific failure mode the brief names: "do not simulate the graphite
  // appearance by placing a translucent layer over existing UI".
  it("draws the canvas opaque and underneath the content", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    const layer = canvas(screen);
    for (const stop of layer.props.colors) expect(stop).toMatch(/^#[0-9a-f]{6}$/i);
    expect(layer.props.pointerEvents).toBe("none");
    // First child of the screen root, so content composites over it, not it over
    // content. `importantForAccessibility` keeps it out of the VoiceOver order.
    const root = screen.toJSON() as any;
    expect(root.children[0].props.testID).toBe("profile-canvas");
    expect(layer.props.importantForAccessibility).toBe("no-hide-descendants");
  });

  it("shows the same canvas on the loading skeleton, so the first frame matches", async () => {
    mockGetMyProfile.mockReturnValue(new Promise(() => undefined));
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-skeleton"));
    expect(canvas(screen, "profile-skeleton-canvas").props.colors)
      .toEqual([surface.canvasTop, surface.canvasBottom]);
  });
});

describe("Profile surfaces sit on the ramp", () => {
  it("gives a grid cell the card step so a decoding image is not a hole in the grid", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    const all = fills(screen);
    expect(all).toContain(surface.raised);
    // The value `textTile` used to carry by hand, before that style was deleted
    // as unrendered. Kept as a guard against it coming back.
    expect(all).not.toContain("#0D2030");
  });

  it("never paints a Profile surface with the old near-black canvas", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    // `#050910` is the palette's `background` and `surface`; on graphite neither
    // is a Profile surface any more. An image placeholder painted with it was a
    // black hole in the middle of the grid until its picture decoded.
    expect(fills(screen)).not.toContain("#050910");
  });

  it("does not put a large soft shadow on any Profile surface", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    const radii: number[] = [];
    const visit = (node: any) => {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) return node.forEach(visit);
      const flat = StyleSheet.flatten(node.props?.style) as { shadowRadius?: number } | undefined;
      if (typeof flat?.shadowRadius === "number") radii.push(flat.shadowRadius);
      (node.children ?? []).forEach(visit);
    };
    visit(screen.toJSON());
    // The dock and the global header draw their own lift and are not in this
    // tree; anything here is a Profile decision.
    expect(radii.filter((r) => r > 8)).toEqual([]);
  });
});

describe("the content tab strip", () => {
  it("renders all three tabs and switches between them", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    expect(screen.getAllByRole("tab").map((tab) => tab.props.accessibilityState.selected)).toEqual([true, false, false]);
    fireEvent.press(screen.getByText("About"));
    await waitFor(() => expect(screen.getByText("Building PulseSoc.")).toBeTruthy());
    fireEvent.press(screen.getByText("Media"));
    await waitFor(() => expect(screen.queryByText("Building PulseSoc.")).toBeNull());
  });

  // The brief forbids communicating state by colour alone. Selection has to
  // survive greyscale and a colour deficiency, so it must differ in something
  // other than hue — here, border weight.
  it("states the selected tab by more than colour", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    const active = controlStyle(screen, "Posts");
    const inactive = controlStyle(screen, "About");
    expect(active.borderWidth).toBeGreaterThan(inactive.borderWidth as number);
    expect(active.backgroundColor).not.toBe(inactive.backgroundColor);
  });

  it("announces the selected tab to assistive technology", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    const [posts, , about] = screen.getAllByRole("tab");
    expect(posts.props.accessibilityState).toMatchObject({ selected: true });
    expect(about.props.accessibilityState).toMatchObject({ selected: false });
    fireEvent.press(screen.getByText("About"));
    await waitFor(() =>
      expect(screen.getAllByRole("tab").map((tab) => tab.props.accessibilityState.selected)).toEqual([false, false, true])
    );
  });

  it("gives an unselected tab the card step and the steel border", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    expect(controlStyle(screen, "About")).toMatchObject({
      backgroundColor: surface.raised,
      borderColor: surface.border
    });
  });

  // Every tab is a real target at every text size: the height is a floor, not a
  // consequence of the label's line box.
  it("keeps each tab at the minimum touch target", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    for (const label of ["Posts", "Media", "About"]) {
      expect(controlStyle(screen, label).minHeight).toBeGreaterThanOrEqual(44);
    }
  });
});

describe("dock clearance", () => {
  /** Every numeric `paddingBottom` in the tree. */
  function paddings(tree: { toJSON: () => unknown }): number[] {
    const found: number[] = [];
    const visit = (node: any) => {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) return node.forEach(visit);
      const flat = StyleSheet.flatten(node.props?.style) as { paddingBottom?: number } | undefined;
      if (typeof flat?.paddingBottom === "number") found.push(flat.paddingBottom);
      (node.children ?? []).forEach(visit);
    };
    visit(tree.toJSON());
    return found;
  }

  it("reserves the dock's derived height under the loaded grid", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    const list = screen.UNSAFE_getByType(require("react-native").FlatList);
    const padding = StyleSheet.flatten(list.props.contentContainerStyle) as { paddingBottom?: number };
    expect(padding.paddingBottom).toBe(DOCKED_CLEARANCE);
  });

  // The pushed instance covers the dock, so reserving dock clearance there would
  // leave a band of dead space above the home indicator.
  it("drops to undocked padding when no dock is beneath the screen", async () => {
    const screen = renderProfile({ docked: false });
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    const list = screen.UNSAFE_getByType(require("react-native").FlatList);
    const padding = StyleSheet.flatten(list.props.contentContainerStyle) as { paddingBottom?: number };
    expect(padding.paddingBottom).toBe(UNDOCKED_CLEARANCE);
  });

  // Derived, not hardcoded: the same screen on a device with no home indicator
  // has to reserve less, and the only way to be sure of that is to move the inset
  // and watch the total follow it.
  it("tracks the device's own safe-area inset rather than one phone's number", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByTestId("profile-grid-tile-1"));
    const list = screen.UNSAFE_getByType(require("react-native").FlatList);
    const padding = StyleSheet.flatten(list.props.contentContainerStyle) as { paddingBottom?: number };
    expect(padding.paddingBottom).toBe(SAFE_AREA_BOTTOM + BOTTOM_NAV_CONTENT_CLEARANCE);
    expect(padding.paddingBottom).toBeGreaterThan(BOTTOM_NAV_CONTENT_CLEARANCE);
  });

  // The regression this mission had to fix. `LogiNexusStatePanel` is `flex: 1`
  // inside a shell that reserved only the safe-area inset, so on a docked Profile
  // the error panel's lower edge and the "Try again" button ran under the pill.
  it("keeps the error state's retry control clear of the dock", async () => {
    mockGetMyProfile.mockRejectedValue(new Error("network unavailable"));
    mockListFeed.mockRejectedValue(new Error("network unavailable"));
    const screen = renderProfile();
    await waitFor(() => expect(screen.getByText("Try again")).toBeTruthy());
    // Exactly the figure the loaded grid uses, and reached the same way: the
    // shell's own safe-area-only padding is switched off so the two cannot be
    // added together into an over-reservation.
    const shellPaddings = paddings(screen);
    expect(shellPaddings).toContain(DOCKED_CLEARANCE);
    expect(shellPaddings).not.toContain(SAFE_AREA_BOTTOM);
  });

  it("de-glows the retry control instead of haloing the one button on the screen", async () => {
    mockGetMyProfile.mockRejectedValue(new Error("network unavailable"));
    mockListFeed.mockRejectedValue(new Error("network unavailable"));
    const screen = renderProfile();
    await waitFor(() => screen.getByText("Try again"));
    const style = controlStyle(screen, "Try again");
    expect(style.shadowRadius).toBeUndefined();
    expect(style.shadowOpacity).toBeUndefined();
    // Still a real target while we are here.
    expect(style.minHeight).toBeGreaterThanOrEqual(44);
  });
});

describe("content states keep their own shape", () => {
  it("shows an empty grid message rather than an error when the fetch succeeded", async () => {
    mockListFeed.mockResolvedValue({ posts: [], next_offset: 0, has_more: false });
    const screen = renderProfile();
    await waitFor(() => expect(screen.getByText(/No posts yet/i)).toBeTruthy());
    expect(screen.queryByText("Try again")).toBeNull();
    // An empty grid is still the graphite page, not a bare black rectangle.
    expect(canvas(screen).props.colors).toEqual([surface.canvasTop, surface.canvasBottom]);
  });

  // An error must never be dressed as an empty state — a failed fetch is not
  // "this member has posted nothing".
  it("does not claim an empty grid when the content request failed", async () => {
    mockListFeed.mockRejectedValue(new Error("network unavailable"));
    const screen = renderProfile();
    await waitFor(() => expect(screen.getByText(/temporarily unavailable/i)).toBeTruthy());
    expect(screen.queryByText(/No posts yet/i)).toBeNull();
  });
});

/*
 * Offline is a third state, not a variant of the error screen.
 *
 * The network failed but a saved profile is on disk, so the screen shows the real
 * profile and says so. The graphite requirement here is the same one the brief
 * makes of the loading skeleton: a degraded state is still the graphite page. A
 * fallback that reverted to a palette default would make "no signal" look like a
 * different app, and it is the one state a user is most likely to distrust.
 */
describe("the offline fallback", () => {
  const cached = {
    value: {
      user_id: 7, display_name: "Roody Cherie", username: "roodycherie",
      public_player_id: "roodycherie", post_count: 2, bio: "Building PulseSoc."
    },
    ageMs: 90_000
  };

  beforeEach(() => {
    mockGetMyProfile.mockRejectedValue(new Error("network unavailable"));
    mockLoadCachedProfileEntry.mockResolvedValue(cached);
  });

  it("says the profile is saved rather than pretending it is live", async () => {
    const screen = renderProfile();
    await waitFor(() => expect(screen.getByText(/Showing saved profile/i)).toBeTruthy());
    // Named as stale *and* dated. "Saved" alone leaves the user guessing whether
    // they are looking at a minute ago or a week ago.
    expect(screen.getByText(/Showing saved profile/i).props.children).toMatch(/\d/);
  });

  it("keeps the graphite canvas under a saved profile", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByText(/Showing saved profile/i));
    expect(canvas(screen).props.colors).toEqual([surface.canvasTop, surface.canvasBottom]);
  });

  // Offline is recoverable, so the way out has to be a real target — and it has to
  // clear the dock, because this banner pushes the content down and the retry
  // control is the lowest thing on the screen.
  it("offers a retry that is reachable and clear of the dock", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByText("Retry profile content"));
    expect(controlStyle(screen, "Retry profile content").minHeight).toBeGreaterThanOrEqual(44);
    const list = screen.UNSAFE_getByType(require("react-native").FlatList);
    const padding = StyleSheet.flatten(list.props.contentContainerStyle) as { paddingBottom?: number };
    expect(padding.paddingBottom).toBe(DOCKED_CLEARANCE);
  });

  // The offline banner is a statement about freshness; it must not also be read as
  // "this member has no posts", and the grid it sits above is the cached grid.
  it("does not turn a stale profile into an empty one", async () => {
    const screen = renderProfile();
    await waitFor(() => screen.getByText(/Showing saved profile/i));
    expect(screen.queryByText(/No posts yet/i)).toBeNull();
  });
});
