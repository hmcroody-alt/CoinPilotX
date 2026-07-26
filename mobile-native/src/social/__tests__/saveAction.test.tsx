/**
 * The behaviours this suite exists to pin down are the ones the user actually
 * reported: a Save button that did not reliably save. Each of the failures
 * found during the repair gets a test here, so a regression shows up as a red
 * test rather than as a button that quietly disagrees with the server.
 */

import { act, render } from "@testing-library/react-native";
import { createElement } from "react";
import { Text } from "react-native";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn()
}));

jest.mock("../../api/pulseApi", () => {
  const actual = jest.requireActual("../../api/pulseApi");
  return { ...actual, pulseApi: jest.fn() };
});

import { adoptFeedSavedStates, PulsePost, savablePostId } from "../../api/feed";
import { PulseApiError, pulseApi } from "../../api/pulseApi";
import { SavableContentType, saveKey, saveTargetFromUrl, setSavedOnServer } from "../saveContract";
import {
  observeSavedState,
  peekSaveState,
  resetSavedStoreForTests,
  subscribeToSaveChanges,
  useSavedState
} from "../savedStore";
import { resetSaveActionsForTests, setSaved, toggleSaved } from "../useSaveAction";

const mockedApi = pulseApi as jest.Mock;

/** A request whose resolution this test controls, so races can be staged. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolveFn, rejectFn) => {
    resolve = resolveFn;
    reject = rejectFn;
  });
  return { promise, resolve, reject };
}

/** Renders the store's opinion of one item as text, the way a card would. */
function SavedProbe({ type, id, serverSaved, onRender }: { type: SavableContentType; id: number; serverSaved?: boolean; onRender?: () => void }) {
  const state = useSavedState(type, id, serverSaved);
  onRender?.();
  return createElement(Text, null, `${state.saved ? "saved" : "unsaved"}:${state.pending ? "pending" : "idle"}`);
}

beforeEach(() => {
  jest.clearAllMocks();
  resetSavedStoreForTests();
  resetSaveActionsForTests();
});

describe("saveKey", () => {
  it("separates identical ids belonging to different content types", () => {
    expect(saveKey("post", 12)).not.toEqual(saveKey("reel", 12));
  });

  it("treats a numeric and a string id as the same content", () => {
    expect(saveKey("post", 12)).toEqual(saveKey("post", "12"));
  });
});

describe("setSavedOnServer", () => {
  it("states the wanted state rather than asking for a toggle, so a retry confirms", async () => {
    mockedApi.mockResolvedValue({ ok: true, saved: true, changed: true });
    await setSavedOnServer({ type: "post", id: 5 }, true);
    await setSavedOnServer({ type: "post", id: 5 }, true);
    const bodies = mockedApi.mock.calls.map((call) => JSON.parse(call[1].body));
    expect(bodies).toEqual([{ post_id: 5, saved: true }, { post_id: 5, saved: true }]);
  });

  it.each<[SavableContentType, string]>([
    ["post", "/api/pulse/posts/9/save"],
    ["reel", "/api/pulse/reels/9/save"],
    ["marketplace", "/api/pulse/marketplace/listings/save"],
    ["status", "/api/pulse/saved"]
  ])("routes %s content to its own endpoint", async (type, path) => {
    mockedApi.mockResolvedValue({ ok: true, saved: true });
    await setSavedOnServer({ type, id: 9 }, true);
    expect(mockedApi).toHaveBeenCalledWith(path, expect.objectContaining({ method: "POST" }));
  });

  it("sends the snapshot fields a Status needs to outlive its own expiry", async () => {
    mockedApi.mockResolvedValue({ ok: true, saved: true });
    await setSavedOnServer({ type: "status", id: 3, title: "Launch day", previewText: "we shipped" }, true);
    const body = JSON.parse(mockedApi.mock.calls[0][1].body);
    expect(body).toMatchObject({ content_type: "status", content_id: "3", saved: true, title: "Launch day", preview_text: "we shipped" });
  });

  it("falls back to is_saved when a route reports state under the older name", async () => {
    mockedApi.mockResolvedValue({ ok: true, is_saved: false });
    await expect(setSavedOnServer({ type: "post", id: 1 }, false)).resolves.toMatchObject({ saved: false });
  });

  it("reports changed:false when the server was already in the requested state", async () => {
    mockedApi.mockResolvedValue({ ok: true, saved: true, changed: false });
    await expect(setSavedOnServer({ type: "post", id: 1 }, true)).resolves.toMatchObject({ saved: true, changed: false });
  });
});

describe("saveTargetFromUrl", () => {
  it.each([
    ["/pulse/post/41", { type: "post", id: 41 }],
    ["/pulse/reels/7", { type: "reel", id: 7 }],
    ["/pulse/status?status_id=88", { type: "status", id: 88 }],
    ["/pulse/marketplace?listing=15", { type: "marketplace", id: 15 }]
  ])("recovers a target from %s", (url, expected) => {
    expect(saveTargetFromUrl(url)).toEqual(expected);
  });

  it("resolves a comment deep link to the post it hangs from, which is the savable thing", () => {
    expect(saveTargetFromUrl("/pulse/post/41#comment-9")).toEqual({ type: "post", id: 41 });
  });

  it("returns null for destinations with nothing savable behind them", () => {
    expect(saveTargetFromUrl("/pulse/@ada")).toBeNull();
    expect(saveTargetFromUrl("/pulse/messages?room=builders")).toBeNull();
    expect(saveTargetFromUrl("")).toBeNull();
  });
});

describe("savablePostId", () => {
  it("resolves a repost to the post it wraps, so both cards save the same thing", () => {
    expect(savablePostId({ id: 99, repost: { original_post_id: 41 } } as PulsePost)).toBe(41);
    expect(savablePostId({ id: 99, original_post: { id: 41 } } as PulsePost)).toBe(41);
  });

  it("uses the post's own id when it is not a repost", () => {
    expect(savablePostId({ id: 41 } as PulsePost)).toBe(41);
  });
});

describe("setSaved", () => {
  it("flips the store before the server answers, so the card responds to the tap", async () => {
    const pending = deferred<{ ok: boolean; saved: boolean }>();
    mockedApi.mockReturnValue(pending.promise);

    let outcome: Promise<unknown>;
    act(() => {
      outcome = setSaved({ type: "post", id: 4 }, true);
    });
    expect(peekSaveState("post", 4)).toEqual({ saved: true, pending: true });

    await act(async () => {
      pending.resolve({ ok: true, saved: true });
      await outcome;
    });
    expect(peekSaveState("post", 4)).toEqual({ saved: true, pending: false });
  });

  it("rolls back to the previous state when the server refuses", async () => {
    mockedApi.mockResolvedValueOnce({ ok: true, saved: true });
    await setSaved({ type: "post", id: 4 }, true);

    mockedApi.mockRejectedValueOnce(new PulseApiError("Server error", 500));
    const outcome = await setSaved({ type: "post", id: 4 }, false);

    expect(outcome.ok).toBe(false);
    expect(outcome.saved).toBe(true);
    expect(outcome.message).toBeTruthy();
    expect(peekSaveState("post", 4)).toEqual({ saved: true, pending: false });
  });

  it("drops a second tap while the first request is still in flight", async () => {
    const pending = deferred<{ ok: boolean; saved: boolean }>();
    mockedApi.mockReturnValue(pending.promise);

    const first = setSaved({ type: "post", id: 4 }, true);
    const second = await setSaved({ type: "post", id: 4 }, true);

    expect(second.ok).toBe(false);
    expect(mockedApi).toHaveBeenCalledTimes(1);

    pending.resolve({ ok: true, saved: true });
    await first;
  });

  it("holds one lock per item, so a save on one post does not block another", async () => {
    const first = deferred<{ ok: boolean; saved: boolean }>();
    const second = deferred<{ ok: boolean; saved: boolean }>();
    mockedApi.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    const a = setSaved({ type: "post", id: 1 }, true);
    const b = setSaved({ type: "post", id: 2 }, true);
    expect(mockedApi).toHaveBeenCalledTimes(2);

    first.resolve({ ok: true, saved: true });
    second.resolve({ ok: true, saved: true });
    await Promise.all([a, b]);
    expect(peekSaveState("post", 1)?.saved).toBe(true);
    expect(peekSaveState("post", 2)?.saved).toBe(true);
  });

  it("never has two requests in flight for one item, so responses cannot land out of order", async () => {
    // The ordering hazard the original bug produced — an older response
    // overwriting a newer one — is prevented upstream, by never letting a
    // second request start. This pins that property rather than the recovery
    // behaviour, because with the lock in place the recovery is unreachable.
    const slowSave = deferred<{ ok: boolean; saved: boolean }>();
    mockedApi.mockReturnValueOnce(slowSave.promise);

    const saving = setSaved({ type: "post", id: 4 }, true);
    const rejected = await setSaved({ type: "post", id: 4 }, false);

    expect(rejected.ok).toBe(false);
    expect(mockedApi).toHaveBeenCalledTimes(1);
    expect(peekSaveState("post", 4)).toEqual({ saved: true, pending: true });

    slowSave.resolve({ ok: true, saved: true });
    await saving;
    expect(peekSaveState("post", 4)).toEqual({ saved: true, pending: false });

    // And the lock is released, so the intent the user has to repeat succeeds.
    mockedApi.mockResolvedValueOnce({ ok: true, saved: false });
    await setSaved({ type: "post", id: 4 }, false);
    expect(peekSaveState("post", 4)?.saved).toBe(false);
  });

  it("toggleSaved asks for the opposite of what the caller is showing", async () => {
    mockedApi.mockResolvedValue({ ok: true, saved: false });
    await toggleSaved({ type: "reel", id: 8 }, true);
    expect(JSON.parse(mockedApi.mock.calls[0][1].body)).toEqual({ reel_id: 8, saved: false });
  });
});

describe("cross-screen synchronisation", () => {
  it("updates every mounted card for the same content from a single save", async () => {
    mockedApi.mockResolvedValue({ ok: true, saved: true });
    const feedCard = render(createElement(SavedProbe, { type: "post", id: 6 }));
    const detailCard = render(createElement(SavedProbe, { type: "post", id: 6 }));

    await act(async () => { await setSaved({ type: "post", id: 6 }, true); });

    expect(feedCard.getByText("saved:idle")).toBeTruthy();
    expect(detailCard.getByText("saved:idle")).toBeTruthy();
  });

  it("does not disturb a card for different content", async () => {
    mockedApi.mockResolvedValue({ ok: true, saved: true });
    const other = render(createElement(SavedProbe, { type: "post", id: 7 }));

    await act(async () => { await setSaved({ type: "post", id: 6 }, true); });

    expect(other.getByText("unsaved:idle")).toBeTruthy();
  });

  it("publishes unsaves globally, which is how the Saved list drops a row", async () => {
    const seen: Array<[string, boolean]> = [];
    const unsubscribe = subscribeToSaveChanges((key, state) => {
      if (!state.pending) seen.push([key, state.saved]);
    });
    mockedApi.mockResolvedValue({ ok: true, saved: false });

    await setSaved({ type: "reel", id: 3 }, false);
    unsubscribe();

    expect(seen).toContainEqual(["reel:3", false]);
  });

  it("keeps a card mounted from a stale payload showing the state the user chose", async () => {
    mockedApi.mockResolvedValue({ ok: true, saved: true });
    await act(async () => { await setSaved({ type: "post", id: 9 }, true); });

    // A list fetched before the save finally renders, still claiming unsaved.
    const staleCard = render(createElement(SavedProbe, { type: "post", id: 9, serverSaved: false }));
    expect(staleCard.getByText("saved:idle")).toBeTruthy();
  });

  it("accepts server truth for an item the store has no opinion about", () => {
    observeSavedState("post", 11, true);
    expect(peekSaveState("post", 11)).toEqual({ saved: true, pending: false });
  });

  it("lets a fresh feed load correct a save the user performed on another device", async () => {
    mockedApi.mockResolvedValue({ ok: true, saved: true });
    await act(async () => { await setSaved({ type: "post", id: 20 }, true); });
    const card = render(createElement(SavedProbe, { type: "post", id: 20 }));
    expect(card.getByText("saved:idle")).toBeTruthy();

    // The user unsaved it on the web, then pulled to refresh here. Seeding
    // alone would leave this card stuck on Saved forever, so the load path
    // adopts explicitly.
    act(() => { adoptFeedSavedStates([{ id: 20, saved: false } as PulsePost]); });

    expect(card.getByText("unsaved:idle")).toBeTruthy();
  });

  it("refuses to let a late list response undo a mutation still in flight", async () => {
    const pending = deferred<{ ok: boolean; saved: boolean }>();
    mockedApi.mockReturnValue(pending.promise);
    const saving = setSaved({ type: "post", id: 12 }, true);

    observeSavedState("post", 12, false);
    expect(peekSaveState("post", 12)).toEqual({ saved: true, pending: true });

    pending.resolve({ ok: true, saved: true });
    await saving;
    expect(peekSaveState("post", 12)?.saved).toBe(true);
  });
});

describe("store cost", () => {
  // A shared store is only usable in a feed if subscribing to it is cheap. These
  // are the two properties that make it so, and both are easy to lose in a
  // refactor: a context provider or a single global subscription would satisfy
  // every correctness test in this file and re-render the whole list on every tap.

  it("does not re-render cards for other content when one item is saved", async () => {
    mockedApi.mockResolvedValue({ ok: true, saved: true });
    const neighbourRenders = jest.fn();
    render(createElement(SavedProbe, { type: "post", id: 30 }));
    render(createElement(SavedProbe, { type: "post", id: 31, onRender: neighbourRenders }));
    neighbourRenders.mockClear();

    await act(async () => { await setSaved({ type: "post", id: 30 }, true); });

    expect(neighbourRenders).not.toHaveBeenCalled();
  });

  it("renders a subscribed card once per visible state change, not once per store write", async () => {
    // Two states are visible across a save — pending, then settled — so two
    // renders is the floor, and a store that emitted on every write regardless
    // of whether anything changed would exceed it.
    const pending = deferred<{ ok: boolean; saved: boolean }>();
    mockedApi.mockReturnValue(pending.promise);
    const renders = jest.fn();
    const card = render(createElement(SavedProbe, { type: "post", id: 32, onRender: renders }));
    renders.mockClear();

    let saving!: Promise<unknown>;
    await act(async () => { saving = setSaved({ type: "post", id: 32 }, true); });
    expect(card.getByText("saved:pending")).toBeTruthy();
    expect(renders).toHaveBeenCalledTimes(1);

    await act(async () => {
      pending.resolve({ ok: true, saved: true });
      await saving;
    });
    expect(card.getByText("saved:idle")).toBeTruthy();
    expect(renders).toHaveBeenCalledTimes(2);
  });

  it("does not render at all when a settle repeats the state already shown", async () => {
    // The Saved screen calls `observeSavedState` for every row on every refresh.
    // Without the equality check in `write`, that is a full re-render of the list
    // on a pull-to-refresh that changed nothing.
    mockedApi.mockResolvedValue({ ok: true, saved: true });
    const renders = jest.fn();
    render(createElement(SavedProbe, { type: "post", id: 34, onRender: renders }));
    await act(async () => { await setSaved({ type: "post", id: 34 }, true); });
    renders.mockClear();

    act(() => { observeSavedState("post", 34, true); });

    expect(renders).not.toHaveBeenCalled();
  });

  it("drops its listener bookkeeping when the last card for an item unmounts", () => {
    // Otherwise a long scroll session accumulates one dead Set per post seen.
    const card = render(createElement(SavedProbe, { type: "post", id: 33 }));
    const seen: string[] = [];
    const stop = subscribeToSaveChanges((key) => seen.push(key));

    card.unmount();
    act(() => { observeSavedState("post", 33, true); });
    stop();

    // The global listener still hears it — nothing is silently dropped — but the
    // unmounted card's own subscription is gone rather than firing into a
    // detached component.
    expect(seen).toEqual(["post:33"]);
  });
});
