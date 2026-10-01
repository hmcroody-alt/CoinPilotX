/**
 * Persistence behaviour of the listing draft store: debounced autosave,
 * hydration with the Resume/Start-over contract, and the publish-time clear.
 * AsyncStorage is the package's in-memory jest mock (see jest.setup.js).
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import { ListingDraft } from "../listingDraft";
import {
  __testing,
  clearListingDraft,
  getListingDraftSnapshot,
  hydrateListingDraft,
  persistListingDraft,
  subscribeListingDraft,
  updateListingDraft
} from "../listingDraftStore";

const { DRAFT_CACHE_KEY, AUTOSAVE_DEBOUNCE_MS } = __testing;

async function storedDraft(): Promise<ListingDraft | null> {
  const raw = await AsyncStorage.getItem(DRAFT_CACHE_KEY);
  return raw ? (JSON.parse(raw) as ListingDraft) : null;
}

beforeEach(async () => {
  jest.useFakeTimers();
  __testing.reset();
  await AsyncStorage.clear();
  jest.clearAllMocks(); // call counts on the storage mock reset per test
});

afterEach(() => {
  jest.runOnlyPendingTimers();
  jest.useRealTimers();
});

describe("listingDraftStore", () => {
  it("applies patches synchronously and notifies subscribers", () => {
    const listener = jest.fn();
    const unsubscribe = subscribeListingDraft(listener);

    updateListingDraft({ title: "Bike" });
    expect(getListingDraftSnapshot().title).toBe("Bike");
    expect(listener).toHaveBeenCalledTimes(1);

    updateListingDraft((draft) => ({ ...draft, title: `${draft.title}!` }));
    expect(getListingDraftSnapshot().title).toBe("Bike!");

    unsubscribe();
    updateListingDraft({ title: "quiet" });
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("debounces the autosave so a burst of keystrokes writes once", async () => {
    updateListingDraft({ title: "B" });
    updateListingDraft({ title: "Bi" });
    updateListingDraft({ title: "Bik" });
    expect(await storedDraft()).toBeNull(); // nothing durable yet

    jest.advanceTimersByTime(AUTOSAVE_DEBOUNCE_MS + 1);
    await Promise.resolve(); // let the queued write settle
    await Promise.resolve();

    expect((await storedDraft())?.title).toBe("Bik");
    expect(AsyncStorage.setItem).toHaveBeenCalledTimes(1);
  });

  it("persists immediately on demand, cancelling the pending debounce", async () => {
    updateListingDraft({ title: "Save me now" });
    await persistListingDraft();

    expect((await storedDraft())?.title).toBe("Save me now");
    jest.advanceTimersByTime(AUTOSAVE_DEBOUNCE_MS * 2);
    await Promise.resolve();
    expect(AsyncStorage.setItem).toHaveBeenCalledTimes(1); // no second write
  });

  it("hydrates a stored draft with content and reports it for the resume prompt", async () => {
    updateListingDraft({ title: "Half-finished amp", listingType: "physical" });
    await persistListingDraft();
    __testing.reset();

    const stored = await hydrateListingDraft();
    expect(stored?.title).toBe("Half-finished amp");
    expect(getListingDraftSnapshot().title).toBe("Half-finished amp");
    expect(getListingDraftSnapshot().listingType).toBe("physical");
  });

  it("returns null from hydration when the stored draft is empty", async () => {
    await persistListingDraft(); // empty draft on disk
    __testing.reset();

    expect(await hydrateListingDraft()).toBeNull();
    expect(getListingDraftSnapshot().listingType).toBeNull();
  });

  it("clears the draft and removes the stored copy on publish", async () => {
    updateListingDraft({ title: "Sold thing", listingType: "digital" });
    await persistListingDraft();

    await clearListingDraft();

    expect(getListingDraftSnapshot().title).toBe("");
    expect(getListingDraftSnapshot().listingType).toBeNull();
    expect(await storedDraft()).toBeNull();
  });
});
