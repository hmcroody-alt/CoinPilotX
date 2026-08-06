/**
 * Wrong-subject guard for Profile OS destination screens.
 *
 * The tile registry hides viewer-scoped tiles on other people's profiles, but a
 * deep link can still land on the destination screen with another profile's
 * route params. Without a guard the screen would call its me-scoped APIs and
 * render the signed-in viewer's private data under that person's name.
 *
 * Each case renders a screen with visitor params (subject id "999", viewer id
 * "1") and asserts two things: the me-scoped fetch was never dispatched (skip,
 * not fetch-then-hide) and the private-content notice is what renders.
 */
import React from "react";
import { act, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0,
  useBottomNavScrollVisibility: () => ({
    onScroll: jest.fn(),
    onScrollBeginDrag: jest.fn(),
    scrollEventThrottle: 16
  }),
  useBottomNavSurface: () => ({ handlers: {}, contentPadding: null })
}));
jest.mock("../../navigation/notificationRouting", () => ({
  routeNotificationTarget: jest.fn()
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
// The music screen imports the radio singleton and expo-av at module scope;
// neither should spin up real audio machinery inside a gating test.
jest.mock("expo-av", () => ({
  Audio: { setAudioModeAsync: jest.fn(), Sound: { createAsync: jest.fn() } }
}));
jest.mock("expo-document-picker", () => ({ getDocumentAsync: jest.fn() }));
jest.mock("../../core/pulseRadio", () => ({
  cyclePulseRadioRepeatMode: jest.fn(),
  getPulseRadioState: () => ({
    status: "idle",
    track: null,
    queue: [],
    shuffle: false,
    repeat: "off",
    positionMillis: 0,
    durationMillis: 0
  }),
  playNextTrack: jest.fn(),
  playPreviousTrack: jest.fn(),
  seekPulseRadioBy: jest.fn(),
  subscribePulseRadio: jest.fn(() => () => undefined),
  togglePulseRadio: jest.fn(),
  togglePulseRadioShuffle: jest.fn()
}));
jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn(),
  releaseMediaPlayback: jest.fn()
}));
jest.mock("../../social/savedStore", () => ({
  observeSavedState: jest.fn(),
  subscribeToSaveChanges: jest.fn(() => () => undefined),
  useSavedState: () => ({ saved: false, pending: false })
}));
jest.mock("../../social/useSaveAction", () => ({ setSaved: jest.fn() }));

// The viewer is signed in as user "1"; the route params say the subject is
// profile "999". `resolveRouteProfileContext` must resolve this as a visitor.
const mockVisitorParams = {
  profileOwnerId: "999",
  sourceProfileId: "999",
  isOwnProfile: false,
  entryPoint: "PROFILE_OS",
  displayName: "Maria"
};

jest.mock("../../session/auth", () => ({
  useAuth: () => ({ authState: { user: { user_id: "1" } } })
}));

// ActivityInboxScreen takes no props — its params arrive through useRoute.
jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ navigate: jest.fn(), setOptions: jest.fn(), goBack: jest.fn() }),
  useRoute: () => ({ key: "ActivityInbox-test", name: "ActivityInbox", params: mockVisitorParams })
}));

const mockGetMyProfile = jest.fn();
jest.mock("../../api/profile", () => ({
  ...jest.requireActual("../../api/profile"),
  getMyProfile: (...args: unknown[]) => mockGetMyProfile(...args)
}));

const mockSearchPulseMusic = jest.fn();
jest.mock("../../api/music", () => ({
  ...jest.requireActual("../../api/music"),
  searchPulseMusic: (...args: unknown[]) => mockSearchPulseMusic(...args),
  loadCachedPulseMusicSnapshot: jest.fn(async () => [])
}));

const mockListSupportTickets = jest.fn();
jest.mock("../../api/support", () => ({
  ...jest.requireActual("../../api/support"),
  listSupportTickets: (...args: unknown[]) => mockListSupportTickets(...args),
  loadCachedSupportState: jest.fn(async () => null)
}));

const mockLoadSafetyState = jest.fn();
jest.mock("../../api/safety", () => ({
  ...jest.requireActual("../../api/safety"),
  loadSafetyState: (...args: unknown[]) => mockLoadSafetyState(...args),
  loadCachedSafetyState: jest.fn(async () => null)
}));

const mockGetIntelligenceState = jest.fn();
const mockListCryptoAlerts = jest.fn();
jest.mock("../../api/intelligence", () => ({
  ...jest.requireActual("../../api/intelligence"),
  getIntelligenceState: (...args: unknown[]) => mockGetIntelligenceState(...args),
  listCryptoAlerts: (...args: unknown[]) => mockListCryptoAlerts(...args),
  loadCachedIntelligenceState: jest.fn(async () => null),
  loadCachedAlertList: jest.fn(async () => [])
}));

const mockGetNotificationBadgeCounts = jest.fn();
jest.mock("../../api/notifications", () => ({
  ...jest.requireActual("../../api/notifications"),
  getNotificationBadgeCounts: (...args: unknown[]) => mockGetNotificationBadgeCounts(...args)
}));

const mockGetPremiumStatus = jest.fn();
jest.mock("../../api/premium", () => ({
  ...jest.requireActual("../../api/premium"),
  getPremiumStatus: (...args: unknown[]) => mockGetPremiumStatus(...args)
}));

const mockGetGrowthState = jest.fn();
jest.mock("../../api/growth", () => ({
  ...jest.requireActual("../../api/growth"),
  getGrowthState: (...args: unknown[]) => mockGetGrowthState(...args),
  loadCachedGrowthState: jest.fn(async () => null)
}));

const mockLoadActivityInboxState = jest.fn();
jest.mock("../../api/activity", () => ({
  ...jest.requireActual("../../api/activity"),
  loadActivityInboxState: (...args: unknown[]) => mockLoadActivityInboxState(...args)
}));

const mockListSavedContent = jest.fn();
jest.mock("../../api/saved", () => ({
  ...jest.requireActual("../../api/saved"),
  listSavedContent: (...args: unknown[]) => mockListSavedContent(...args),
  loadCachedSavedLibrary: jest.fn(async () => null)
}));

const mockListAdAccounts = jest.fn();
const mockGetAdAnalytics = jest.fn();
jest.mock("../../api/businessOs", () => ({
  ...jest.requireActual("../../api/businessOs"),
  listAdAccounts: (...args: unknown[]) => mockListAdAccounts(...args),
  getAdAnalytics: (...args: unknown[]) => mockGetAdAnalytics(...args),
  loadCachedAdAccounts: jest.fn(async () => []),
  loadCachedAdAnalytics: jest.fn(async () => null)
}));

const mockSellerSnapshot = jest.fn();
jest.mock("../../api/marketplace", () => ({
  loadSellerStoreSnapshot: (...args: unknown[]) => mockSellerSnapshot(...args),
  loadCachedSellerStore: jest.fn(async () => null)
}));

import { PRIVATE_CONTENT_MESSAGE } from "../../profile/profileContext";
import { ActivityInboxScreen } from "../ActivityInboxScreen";
import { BusinessOsScreen } from "../BusinessOsScreen";
import { GrowthCenterScreen } from "../GrowthCenterScreen";
import { IntelligenceCenterScreen } from "../IntelligenceCenterScreen";
import { MusicScreen } from "../MusicScreen";
import { SafetyHubScreen } from "../SafetyHubScreen";
import { SavedScreen } from "../SavedScreen";
import { TrustSafetyScreen } from "../TrustSafetyScreen";

const visitorRoute = { params: mockVisitorParams } as never;

function navigationSpy() {
  return {
    navigate: jest.fn(),
    goBack: jest.fn(),
    setOptions: jest.fn(),
    addListener: jest.fn(() => () => undefined)
  } as never;
}

async function renderAsVisitor(element: React.ReactElement) {
  const view = render(element);
  await act(async () => {
    await Promise.resolve();
  });
  return view;
}

beforeEach(() => {
  jest.clearAllMocks();
  // The music library search is public catalogue data, so it stays live even
  // for a visitor; everything me-scoped must never be dispatched at all.
  mockSearchPulseMusic.mockResolvedValue({ tracks: [] });
});

describe("Profile OS owner gate", () => {
  it("MusicScreen refuses a visitor and never fetches the viewer's profile", async () => {
    const view = await renderAsVisitor(<MusicScreen route={visitorRoute} navigation={navigationSpy()} />);
    expect(mockGetMyProfile).not.toHaveBeenCalled();
    expect(view.getByText(PRIVATE_CONTENT_MESSAGE)).toBeTruthy();
  });

  it("TrustSafetyScreen refuses a visitor and never lists the viewer's tickets", async () => {
    const view = await renderAsVisitor(<TrustSafetyScreen route={visitorRoute} navigation={navigationSpy()} />);
    expect(mockListSupportTickets).not.toHaveBeenCalled();
    expect(view.getByText(PRIVATE_CONTENT_MESSAGE)).toBeTruthy();
  });

  it("SafetyHubScreen refuses a visitor and never loads the viewer's safety state", async () => {
    const view = await renderAsVisitor(<SafetyHubScreen route={visitorRoute} navigation={navigationSpy()} />);
    expect(mockLoadSafetyState).not.toHaveBeenCalled();
    expect(view.getByText(PRIVATE_CONTENT_MESSAGE)).toBeTruthy();
  });

  it("IntelligenceCenterScreen refuses a visitor and never loads the viewer's alerts", async () => {
    const view = await renderAsVisitor(
      <IntelligenceCenterScreen route={visitorRoute} navigation={navigationSpy()} />
    );
    expect(mockGetIntelligenceState).not.toHaveBeenCalled();
    expect(mockListCryptoAlerts).not.toHaveBeenCalled();
    expect(mockGetNotificationBadgeCounts).not.toHaveBeenCalled();
    expect(view.getByText(PRIVATE_CONTENT_MESSAGE)).toBeTruthy();
  });

  it("GrowthCenterScreen refuses a visitor and never loads the viewer's growth state", async () => {
    const view = await renderAsVisitor(<GrowthCenterScreen route={visitorRoute} navigation={navigationSpy()} />);
    expect(mockGetGrowthState).not.toHaveBeenCalled();
    expect(mockGetPremiumStatus).not.toHaveBeenCalled();
    expect(view.getByText(PRIVATE_CONTENT_MESSAGE)).toBeTruthy();
  });

  it("ActivityInboxScreen refuses a visitor and never loads the viewer's inbox", async () => {
    const view = await renderAsVisitor(<ActivityInboxScreen />);
    expect(mockLoadActivityInboxState).not.toHaveBeenCalled();
    expect(view.getByText(PRIVATE_CONTENT_MESSAGE)).toBeTruthy();
  });

  it("SavedScreen refuses a visitor and never lists the viewer's saved content", async () => {
    const view = await renderAsVisitor(<SavedScreen route={visitorRoute} />);
    expect(mockListSavedContent).not.toHaveBeenCalled();
    expect(view.getByText(PRIVATE_CONTENT_MESSAGE)).toBeTruthy();
  });

  it("BusinessOsScreen refuses a visitor and never fetches the viewer's business", async () => {
    const view = await renderAsVisitor(<BusinessOsScreen route={visitorRoute} navigation={navigationSpy()} />);
    expect(mockListAdAccounts).not.toHaveBeenCalled();
    expect(mockGetAdAnalytics).not.toHaveBeenCalled();
    expect(mockSellerSnapshot).not.toHaveBeenCalled();
    expect(view.getByText(PRIVATE_CONTENT_MESSAGE)).toBeTruthy();
  });
});
