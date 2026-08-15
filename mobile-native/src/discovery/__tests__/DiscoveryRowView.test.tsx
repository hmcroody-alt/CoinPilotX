/**
 * §5–§8, §11, §14 — the card the user actually touches.
 *
 * The single most consequential requirement across the whole discovery mission
 * is that a tap opens **the exact thing in the thumbnail**: the reel in that
 * poster, that person's profile, that status, that group. Everything else in the
 * feature is recoverable; a card that opens something else is the failure the
 * user reports as "it showed me the wrong video".
 *
 * So these assert the *arguments handed to the handlers*, not that a handler
 * fired. `toHaveBeenCalled()` would pass just as happily for a row that opens
 * the first item every time.
 *
 * The other thing under test is §11's arbitration: the Add Friend and Join
 * buttons live inside the pressable card, so a tap on the button must not also
 * open the profile behind it.
 */
import { fireEvent, render } from "@testing-library/react-native";
import { DiscoveryRowView, DiscoveryRowActions } from "../DiscoveryRowView";
import { resetDiscoveryImpressions, setDiscoveryAnalyticsSink } from "../analytics";
import type { DiscoveryModule } from "../discoveryRows";

// The shell renders translated headings and button labels; the catalog itself is
// covered by the i18n validator, so here the key is echoed back to keep the
// assertions about behavior.
jest.mock("../../i18n/I18nContext", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

function actions(): jest.Mocked<DiscoveryRowActions> {
  return {
    onOpenReel: jest.fn(),
    onOpenStatus: jest.fn(),
    onOpenGroup: jest.fn(),
    onOpenPerson: jest.fn(),
    onAddFriend: jest.fn(),
    onJoinGroup: jest.fn(),
    onSeeAll: jest.fn(),
    onDismiss: jest.fn()
  };
}

const reelsModule: DiscoveryModule = {
  kind: "reels",
  titleKey: "social:feed.discovery.reelsTitle",
  items: [
    { reelId: 11, title: "First" },
    { reelId: 22, title: "Second" },
    { reelId: 33, title: "Third" }
  ]
};

const peopleModule: DiscoveryModule = {
  kind: "people",
  titleKey: "social:feed.discovery.peopleTitle",
  items: [
    { profileKey: "nova", username: "nova", displayName: "Nova" },
    { profileKey: "atlas", username: "atlas", displayName: "Atlas" },
    { profileKey: "vega", username: "vega", displayName: "Vega" }
  ]
};

const statusesModule: DiscoveryModule = {
  kind: "statuses",
  titleKey: "social:feed.discovery.statusesTitle",
  items: [
    { statusId: 101, title: "One", authorName: "Nova" },
    { statusId: 202, title: "Two", authorName: "Atlas" },
    { statusId: 303, title: "Three", authorName: "Vega" }
  ]
};

const groupsModule: DiscoveryModule = {
  kind: "groups",
  titleKey: "social:feed.discovery.groupsTitle",
  items: [
    { slug: "astro", name: "Astro" },
    { slug: "makers", name: "Makers" },
    { slug: "runners", name: "Runners" }
  ]
};

function renderRow(module: DiscoveryModule, extra: Partial<React.ComponentProps<typeof DiscoveryRowView>> = {}) {
  const handlers = actions();
  const utils = render(<DiscoveryRowView module={module} slot={0} {...handlers} {...extra} />);
  return { ...utils, handlers };
}

beforeEach(() => {
  resetDiscoveryImpressions();
  setDiscoveryAnalyticsSink(() => undefined);
});

afterEach(() => {
  setDiscoveryAnalyticsSink(null);
});

describe("exact destinations (§4–§7)", () => {
  it("opens the reel that was tapped, not the first in the row", () => {
    const { getByTestId, handlers } = renderRow(reelsModule);

    fireEvent.press(getByTestId("discovery-card-reels-22"));

    expect(handlers.onOpenReel).toHaveBeenCalledTimes(1);
    expect(handlers.onOpenReel.mock.calls[0][0]).toMatchObject({ reelId: 22 });
  });

  it("opens the person that was tapped", () => {
    const { getByTestId, handlers } = renderRow(peopleModule);

    fireEvent.press(getByTestId("discovery-card-people-atlas"));

    expect(handlers.onOpenPerson.mock.calls[0][0]).toMatchObject({ profileKey: "atlas" });
  });

  it("opens the status that was tapped", () => {
    const { getByTestId, handlers } = renderRow(statusesModule);

    fireEvent.press(getByTestId("discovery-card-statuses-202"));

    expect(handlers.onOpenStatus.mock.calls[0][0]).toMatchObject({ statusId: 202 });
  });

  it("opens the group that was tapped", () => {
    const { getByTestId, handlers } = renderRow(groupsModule);

    fireEvent.press(getByTestId("discovery-card-groups-makers"));

    expect(handlers.onOpenGroup.mock.calls[0][0]).toMatchObject({ slug: "makers" });
  });
});

describe("in-card actions (§5, §7, §11)", () => {
  it("sends a friend request without also opening the profile", () => {
    // The button is nested inside the card's Pressable. If the press propagates,
    // the user gets a friend request *and* a navigation they did not ask for.
    const { getByTestId, handlers } = renderRow(peopleModule);

    fireEvent.press(getByTestId("discovery-add-friend-nova"));

    expect(handlers.onAddFriend.mock.calls[0][0]).toMatchObject({ profileKey: "nova" });
    expect(handlers.onOpenPerson).not.toHaveBeenCalled();
  });

  it("joins a group without also opening it", () => {
    const { getByTestId, handlers } = renderRow(groupsModule);

    fireEvent.press(getByTestId("discovery-join-group-astro"));

    expect(handlers.onJoinGroup.mock.calls[0][0]).toMatchObject({ slug: "astro" });
    expect(handlers.onOpenGroup).not.toHaveBeenCalled();
  });

  it("shows the requested state for a pending friend request", () => {
    const { getByTestId } = renderRow(peopleModule, { pendingFriendKeys: new Set(["nova"]) });

    expect(getByTestId("discovery-add-friend-nova").props.accessibilityLabel).toContain(
      "social:feed.discovery.requestSent"
    );
  });

  it("shows the joined state for a group already joined this session", () => {
    const { getByTestId } = renderRow(groupsModule, { joinedGroupSlugs: new Set(["astro"]) });

    expect(getByTestId("discovery-join-group-astro").props.accessibilityLabel).toContain(
      "social:feed.discovery.joined"
    );
  });
});

describe("the module shell (§2)", () => {
  it("dismisses the module by kind", () => {
    const { getByTestId, handlers } = renderRow(groupsModule);

    fireEvent.press(getByTestId("discovery-dismiss-groups"));

    expect(handlers.onDismiss).toHaveBeenCalledWith("groups");
  });

  it("renders See all when a destination was supplied", () => {
    const { getByTestId, handlers } = renderRow(reelsModule);

    fireEvent.press(getByTestId("discovery-see-all-reels"));

    expect(handlers.onSeeAll).toHaveBeenCalledWith("reels");
  });

  it("omits See all entirely when there is no destination", () => {
    // §9's rule applied generally: an inert control is worse than no control.
    // People has no friends screen in the mobile app, so the row has no See all.
    const { queryByTestId } = renderRow(peopleModule, { onSeeAll: undefined });

    expect(queryByTestId("discovery-see-all-people")).toBeNull();
  });

  it("labels the row with its translated heading", () => {
    const { getByTestId } = renderRow(reelsModule);

    expect(getByTestId("discovery-row-reels").props.accessibilityLabel).toBe(
      "social:feed.discovery.reelsTitle"
    );
  });

  it("renders nothing for a kind with no approved card (§9, §10)", () => {
    const topics: DiscoveryModule = {
      kind: "topics",
      titleKey: "social:feed.discovery.reelsTitle",
      items: [{ topic: "astro", label: "Astro" }]
    };

    const { queryByTestId } = renderRow(topics);

    expect(queryByTestId("discovery-card-topics-astro")).toBeNull();
  });
});

describe("analytics (§14)", () => {
  it("reports the exact destination id on a tap", () => {
    const events: { name: string; target?: string; slot: number }[] = [];
    setDiscoveryAnalyticsSink((event) => events.push(event));
    const { getByTestId } = renderRow(reelsModule, { slot: 2 });

    fireEvent.press(getByTestId("discovery-card-reels-33"));

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: "reel_opened", target: "33", slot: 2 })
      ])
    );
  });

  it("reports a friend request against the person it was sent to", () => {
    const events: { name: string; target?: string }[] = [];
    setDiscoveryAnalyticsSink((event) => events.push(event));
    const { getByTestId } = renderRow(peopleModule);

    fireEvent.press(getByTestId("discovery-add-friend-vega"));

    expect(events).toEqual(
      expect.arrayContaining([expect.objectContaining({ name: "friend_request_sent", target: "vega" })])
    );
  });
});
