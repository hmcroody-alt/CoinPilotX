import { act, render, waitFor } from "@testing-library/react-native";
import React from "react";

const mockAuthState = { user: { user_id: 7 } };
const mockGetMyProfile = jest.fn();
const mockGetPublicProfile = jest.fn();
const mockListFeed = jest.fn();
const mockPickProfileImage = jest.fn();
const mockSaveProfileImage = jest.fn();

// The header is the surface under test only for the props it is handed; its own
// rendering is covered in components/__tests__/ProfileHeader.test.tsx. Capturing
// the props here is what lets this file drive the two entry points directly.
let headerProps: Record<string, unknown> = {};
jest.mock("../../components/ProfileHeader", () => ({
  ProfileHeader: (props: Record<string, unknown>) => {
    headerProps = props;
    return null;
  }
}));

jest.mock("../../api/profile", () => ({
  getMyProfile: () => mockGetMyProfile(),
  getPublicProfile: (...args: unknown[]) => mockGetPublicProfile(...args),
  listPublicProfilePosts: (...args: unknown[]) => mockListFeed(...args),
  loadCachedProfileEntry: jest.fn(async () => null),
  profileErrorState: jest.fn(() => ({ title: "Error", body: "Error", retryable: true, offline: false })),
  toggleProfileFollow: jest.fn()
}));
jest.mock("../../profile/profileMediaEdit", () => ({
  ...jest.requireActual("../../profile/profileMediaEdit"),
  pickProfileImage: (...args: unknown[]) => mockPickProfileImage(...args),
  saveProfileImage: (...args: unknown[]) => mockSaveProfileImage(...args)
}));
jest.mock("../../native/haptics", () => ({ haptic: jest.fn() }));
jest.mock("expo-image-picker", () => ({
  requestMediaLibraryPermissionsAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
  MediaTypeOptions: { Images: "Images" }
}));
jest.mock("../../api/feed", () => ({
  listFeed: (...args: unknown[]) => mockListFeed(...args),
  pulsePostUrl: jest.fn(), reactToPost: jest.fn(), repostPost: jest.fn(), deletePost: jest.fn(),
  savablePostId: (post: { id: number }) => post.id
}));
jest.mock("../../components/GalacticAtmosphere", () => ({ GalacticAtmosphere: () => null }));
jest.mock("../../components/Screen", () => ({
  LogiNexusScreenShell: ({ children }: { children: React.ReactNode }) => children,
  LogiNexusStatePanel: ({ title }: { title: string }) => title
}));
jest.mock("../../navigation/refreshCoordinator", () => ({ registerRefreshDestination: jest.fn(() => jest.fn()) }));
jest.mock("../../social/actionGuard", () => ({ actionKey: jest.fn(), useSocialActionGuard: () => ({ run: jest.fn(), isItemBusy: () => false }) }));
jest.mock("../../social/savedStore", () => ({ peekSaveState: jest.fn() }));
jest.mock("../../social/useSaveAction", () => ({ setSaved: jest.fn() }));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn() }));
jest.mock("../../core/eventSync", () => ({ invalidateNativeSync: jest.fn(async () => undefined) }));
jest.mock("../../api/messenger", () => ({ openDirectConversation: jest.fn() }));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavSurface: () => ({ contentPadding: {}, handlers: { onScroll: jest.fn(), onScrollBeginDrag: jest.fn(), scrollEventThrottle: 16 } })
}));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: mockAuthState }) }));
jest.mock("../../api/premiumCenter", () => ({
  ...jest.requireActual("../../api/premiumCenter"),
  getPremiumCenter: jest.fn(() => Promise.reject(new Error("premium status not loaded in this test")))
}));

import { ProfileScreen } from "../ProfileScreen";

const mine = {
  user_id: 7, display_name: "Roody Cherie", username: "roodycherie", public_player_id: "roodycherie",
  post_count: 0, follower_count: 12_400, avatar_url: "https://cdn/old-a.jpg", cover_url: "https://cdn/old-c.jpg"
};
// A public account as the server describes one. Without `viewer_permissions`
// the client denies by default, so omitting them would hide every visitor tile
// and make the ownership assertions below pass for the wrong reason.
const theirs = {
  user_id: 8, display_name: "Maria Cherie", username: "mariacherie", public_player_id: "Pilot-8008",
  post_count: 0, avatar_url: "https://cdn/m-a.jpg", cover_url: "https://cdn/m-c.jpg",
  viewer_permissions: {
    can_view_public_profile: true, can_view_public_media: true, can_view_public_activity: true,
    can_message: true, can_report: true, can_block: true
  }
};

function renderProfile(params?: Record<string, unknown>) {
  return render(<ProfileScreen navigation={{ navigate: jest.fn() } as never} route={{ params } as never} />);
}

beforeEach(() => {
  jest.clearAllMocks();
  headerProps = {};
  mockAuthState.user = { user_id: 7 };
  mockGetMyProfile.mockResolvedValue(mine);
  mockGetPublicProfile.mockResolvedValue(theirs);
  mockListFeed.mockResolvedValue({ posts: [], next_offset: 0, has_more: false });
  mockPickProfileImage.mockResolvedValue({
    status: "picked", uri: "file:///new.jpg", name: "cover.jpg", mimeType: "image/jpeg"
  });
});

describe("who may change profile media", () => {
  it("offers both entry points on your own profile", async () => {
    renderProfile();
    await waitFor(() => expect(headerProps.canEditMedia).toBe(true));
    expect(typeof headerProps.onEditAvatar).toBe("function");
    expect(typeof headerProps.onEditCover).toBe("function");
  });

  it("offers neither on somebody else's profile", async () => {
    renderProfile({ userId: 8 });
    await waitFor(() => expect(headerProps.profile).toMatchObject({ user_id: 8 }));
    expect(headerProps.canEditMedia).toBe(false);
  });

  // Client visibility is not authorization: the handler itself must refuse.
  it("does not reach the picker when a visitor invokes the handler anyway", async () => {
    renderProfile({ userId: 8 });
    await waitFor(() => expect(headerProps.canEditMedia).toBe(false));
    await act(async () => { (headerProps.onEditCover as () => void)(); });
    expect(mockPickProfileImage).not.toHaveBeenCalled();
    expect(mockSaveProfileImage).not.toHaveBeenCalled();
  });
});

describe("cover edit", () => {
  it("previews the crop, then adopts the URL the server returns", async () => {
    let release: (value: unknown) => void = () => undefined;
    mockSaveProfileImage.mockReturnValue(new Promise((resolve) => { release = resolve; }));

    const view = renderProfile();
    await waitFor(() => expect(headerProps.canEditMedia).toBe(true));
    await act(async () => { (headerProps.onEditCover as () => void)(); });

    expect(headerProps.profile).toMatchObject({ cover_url: "file:///new.jpg" });
    expect(headerProps.coverBusy).toBe(true);

    await act(async () => {
      release({ ...mine, cover_url: "https://cdn/new-c.jpg?v=2", banner_url: "https://cdn/new-c.jpg?v=2" });
    });

    await waitFor(() => expect(headerProps.profile).toMatchObject({ cover_url: "https://cdn/new-c.jpg?v=2" }));
    expect(headerProps.coverBusy).toBe(false);
    expect(view.getByText("Cover photo updated.")).toBeTruthy();
  });

  // §36: an optimistic preview must never survive a failed upload.
  it("restores the previous cover and explains the failure", async () => {
    mockSaveProfileImage.mockRejectedValue(new Error("Cover upload rejected."));
    const view = renderProfile();
    await waitFor(() => expect(headerProps.canEditMedia).toBe(true));

    await act(async () => { (headerProps.onEditCover as () => void)(); });

    await waitFor(() => expect(view.getByText("Cover upload rejected.")).toBeTruthy());
    expect(headerProps.profile).toMatchObject({ cover_url: "https://cdn/old-c.jpg" });
    expect(headerProps.coverBusy).toBe(false);
  });
});

describe("profile photo edit", () => {
  it("previews and adopts the avatar without disturbing the cover", async () => {
    mockSaveProfileImage.mockResolvedValue({
      ...mine, avatar_url: "https://cdn/new-a.jpg?v=2", avatar_thumbnail_url: "https://cdn/new-a-t.jpg?v=2"
    });
    const view = renderProfile();
    await waitFor(() => expect(headerProps.canEditMedia).toBe(true));

    await act(async () => { (headerProps.onEditAvatar as () => void)(); });

    await waitFor(() => expect(headerProps.profile).toMatchObject({ avatar_url: "https://cdn/new-a.jpg?v=2" }));
    expect(headerProps.profile).toMatchObject({ cover_url: "https://cdn/old-c.jpg" });
    expect(view.getByText("Profile photo updated.")).toBeTruthy();
  });

  // The upload response merges onto a cache that may predate what is on screen,
  // so only the media fields may be taken from it.
  it("keeps live counts when the upload response carries a stale copy of them", async () => {
    mockSaveProfileImage.mockResolvedValue({
      ...mine, follower_count: 3, avatar_url: "https://cdn/new-a.jpg?v=2"
    });
    renderProfile();
    await waitFor(() => expect(headerProps.canEditMedia).toBe(true));

    await act(async () => { (headerProps.onEditAvatar as () => void)(); });

    await waitFor(() => expect(headerProps.profile).toMatchObject({ avatar_url: "https://cdn/new-a.jpg?v=2" }));
    expect(headerProps.profile).toMatchObject({ follower_count: 12_400 });
  });

  it("reports a denied permission instead of failing silently", async () => {
    mockPickProfileImage.mockResolvedValue({ status: "denied" });
    const view = renderProfile();
    await waitFor(() => expect(headerProps.canEditMedia).toBe(true));

    await act(async () => { (headerProps.onEditAvatar as () => void)(); });

    expect(mockSaveProfileImage).not.toHaveBeenCalled();
    await waitFor(() => expect(view.getByText(/photo access/)).toBeTruthy());
  });

  it("says nothing and changes nothing when the picker is cancelled", async () => {
    mockPickProfileImage.mockResolvedValue({ status: "cancelled" });
    renderProfile();
    await waitFor(() => expect(headerProps.canEditMedia).toBe(true));

    await act(async () => { (headerProps.onEditAvatar as () => void)(); });

    expect(mockSaveProfileImage).not.toHaveBeenCalled();
    expect(headerProps.profile).toMatchObject({ avatar_url: "https://cdn/old-a.jpg" });
  });

  it("ignores a second tap while an upload is in flight", async () => {
    let release: (value: unknown) => void = () => undefined;
    mockSaveProfileImage.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    renderProfile();
    await waitFor(() => expect(headerProps.canEditMedia).toBe(true));

    await act(async () => { (headerProps.onEditAvatar as () => void)(); });
    await act(async () => { (headerProps.onEditCover as () => void)(); });

    expect(mockPickProfileImage).toHaveBeenCalledTimes(1);
    await act(async () => { release(mine); });
  });
});

/**
 * §17/§52: the tiles belong to the profile on screen.
 *
 * profileOsTiles.test.ts pins what `visibleProfileOsTiles` and
 * `profileOsDestination` return for a given context, and profileOsOwnerGate
 * pins that each destination screen refuses a visitor. Neither sees the call
 * site — a ProfileScreen that built the context from the signed-in account and
 * handed it to a correct registry would satisfy both and still open Roody's
 * library under Maria's name, which is the original defect.
 */
describe("Profile OS tiles follow the viewed profile", () => {
  it("routes a tile on someone else's profile to that person, not to the viewer", async () => {
    const navigate = jest.fn();
    render(<ProfileScreen navigation={{ navigate } as never} route={{ params: { userId: 8 } } as never} />);
    await waitFor(() => expect(headerProps.profile).toMatchObject({ user_id: 8 }));

    const tiles = headerProps.moduleKeys as string[];
    expect(tiles.length).toBeGreaterThan(0);
    await act(async () => { (headerProps.onModulePress as (key: string) => void)("identity"); });

    expect(navigate).toHaveBeenCalledTimes(1);
    const params = JSON.stringify(navigate.mock.calls[0][1] ?? {});
    expect(params).toContain("8");
    expect(params).not.toContain("\"7\"");
  });

  it("names the viewed profile as the tiles' owner and offers fewer of them than to the owner", async () => {
    render(<ProfileScreen navigation={{ navigate: jest.fn() } as never} route={{ params: { userId: 8 } } as never} />);
    await waitFor(() => expect(headerProps.profile).toMatchObject({ user_id: 8 }));
    const visitorTiles = headerProps.moduleKeys as string[];
    expect(headerProps.moduleOwnerName).toBe("Maria");

    render(<ProfileScreen navigation={{ navigate: jest.fn() } as never} route={{ params: undefined } as never} />);
    await waitFor(() => expect(headerProps.profile).toMatchObject({ user_id: 7 }));
    expect(headerProps.moduleOwnerName).toBe("");
    expect((headerProps.moduleKeys as string[]).length).toBeGreaterThan(visitorTiles.length);
  });
});
