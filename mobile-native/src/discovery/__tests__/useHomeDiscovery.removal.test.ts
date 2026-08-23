/**
 * "Dismissed cards must not immediately reappear."
 *
 * The persistence layer is covered in `dismissals.test.ts`. What is left — and
 * what the user actually experiences — is whether the hook takes the removed
 * person out of the module it hands to Home *without waiting for a refetch*. The
 * server has no record of a client-side removal, so a refetch would return the
 * same person; if the filter lived anywhere but here, the card would sit on
 * screen until the network answered and then come straight back.
 *
 * The real `dismissals` module is used rather than a mock, so a removal that
 * persists but never reaches the render path still fails here.
 */
import { act, renderHook, waitFor } from "@testing-library/react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useHomeDiscovery } from "../useHomeDiscovery";
import { __resetDismissals } from "../dismissals";
import { resetDiscoveryImpressions, setDiscoveryAnalyticsSink } from "../analytics";
import { __clearDiscoveryFlagOverrides, __setDiscoveryFlagOverride } from "../flags";
import { loadDiscoveryModules } from "../sources";
import type { DiscoveryModule, PersonSuggestion } from "../discoveryRows";

jest.mock("../sources", () => ({ loadDiscoveryModules: jest.fn() }));
jest.mock("../../api/friends", () => ({ sendFriendRequest: jest.fn().mockResolvedValue({}) }));
jest.mock("../../api/groups", () => ({ joinGroup: jest.fn().mockResolvedValue({}) }));

const loadModules = loadDiscoveryModules as jest.MockedFunction<typeof loadDiscoveryModules>;

function person(key: string): PersonSuggestion {
  return { profileKey: key, username: key, displayName: key.toUpperCase() };
}

const peopleModule = (keys: string[]): DiscoveryModule => ({
  kind: "people",
  titleKey: "social:feed.discovery.peopleTitle",
  items: keys.map(person)
});

const reelsModule: DiscoveryModule = {
  kind: "reels",
  titleKey: "social:feed.discovery.reelsTitle",
  items: [{ reelId: 1, title: "Reel" }]
};

function renderWith(modules: DiscoveryModule[]) {
  loadModules.mockResolvedValue({ modules, failures: {} });
  return renderHook(() => useHomeDiscovery({ navigation: { navigate: jest.fn() } as never, enabled: true }));
}

beforeEach(async () => {
  __resetDismissals();
  await AsyncStorage.clear();
  loadModules.mockReset();
  resetDiscoveryImpressions();
  setDiscoveryAnalyticsSink(() => undefined);
  __setDiscoveryFlagOverride("homeDiscoveryEnabled", true);
});

afterEach(() => {
  setDiscoveryAnalyticsSink(null);
  __clearDiscoveryFlagOverrides();
});

function peopleKeys(modules: DiscoveryModule[]): string[] {
  const found = modules.find((module) => module.kind === "people");
  return found && found.kind === "people" ? found.items.map((item) => item.profileKey) : [];
}

describe("removing a suggestion", () => {
  it("drops the removed person and keeps the rest of the row", async () => {
    const { result } = renderWith([peopleModule(["atlas", "vega", "lyra"])]);
    await waitFor(() => expect(result.current.modules).toHaveLength(1));

    await act(async () => {
      result.current.actions.onRemovePerson(person("vega"));
    });

    expect(peopleKeys(result.current.modules)).toEqual(["atlas", "lyra"]);
  });

  it("does not refetch to make the removal take effect", async () => {
    const { result } = renderWith([peopleModule(["atlas", "vega"])]);
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
    const callsBefore = loadModules.mock.calls.length;

    await act(async () => {
      result.current.actions.onRemovePerson(person("atlas"));
    });

    expect(loadModules).toHaveBeenCalledTimes(callsBefore);
    expect(peopleKeys(result.current.modules)).toEqual(["vega"]);
  });

  it("drops the row entirely once its last suggestion is removed", async () => {
    // An empty carousel under a heading is worse than no row at all.
    const { result } = renderWith([reelsModule, peopleModule(["atlas"])]);
    await waitFor(() => expect(result.current.modules).toHaveLength(2));

    await act(async () => {
      result.current.actions.onRemovePerson(person("atlas"));
    });

    expect(result.current.modules.map((module) => module.kind)).toEqual(["reels"]);
  });

  it("leaves every other kind untouched", async () => {
    const { result } = renderWith([reelsModule, peopleModule(["atlas", "vega"])]);
    await waitFor(() => expect(result.current.modules).toHaveLength(2));

    await act(async () => {
      result.current.actions.onRemovePerson(person("atlas"));
    });

    expect(result.current.modules[0]).toBe(reelsModule);
  });

  it("does not rebuild the module array when nothing has been removed", async () => {
    // The row re-renders on every feed recomputation. Copying the array each
    // time would defeat the memo Home relies on to keep scrolling smooth.
    const built = [reelsModule, peopleModule(["atlas"])];
    const { result, rerender } = renderWith(built);
    await waitFor(() => expect(result.current.modules).toHaveLength(2));
    const first = result.current.modules;

    rerender(undefined);

    expect(result.current.modules).toBe(first);
  });

  it("keeps the removal after a reload, because the server will offer them again", async () => {
    const { result, unmount } = renderWith([peopleModule(["atlas", "vega"])]);
    await waitFor(() => expect(result.current.modules).toHaveLength(1));

    await act(async () => {
      result.current.actions.onRemovePerson(person("atlas"));
    });
    unmount();

    // A fresh mount with the graph returning the same two people.
    const second = renderWith([peopleModule(["atlas", "vega"])]);
    await waitFor(() => expect(peopleKeys(second.result.current.modules)).toEqual(["vega"]));
  });

  it("does not dismiss the whole row when one person is removed", async () => {
    const { result } = renderWith([peopleModule(["atlas", "vega"])]);
    await waitFor(() => expect(result.current.modules).toHaveLength(1));

    await act(async () => {
      result.current.actions.onRemovePerson(person("atlas"));
    });

    expect(result.current.dismissed.has("people")).toBe(false);
  });
});
