/**
 * What the next account can see.
 *
 * The media cache has had account isolation since Stage 35 and is tested for it.
 * The JSON content cache never was: `writeJsonCache` takes a bare key with no
 * scope in it, so every account on a handset writes Home, Profiles, Statuses,
 * the activity inbox and the radio queue to the *same* AsyncStorage keys. The
 * only thing standing between user A's cached content and user B is the sweep
 * `clearUserScopedMediaState` performs at sign-out, which until `storageScope`
 * was an explicit six-prefix allowlist the rest of the app had long outgrown.
 *
 * These tests are written against the real `core/cache` rather than a mock,
 * because the defect they exist to catch lives in the interaction between the
 * cache's in-memory tier and a sweep that deletes straight from AsyncStorage.
 * A mocked cache cannot express it: it is precisely the real module's own
 * memoisation that keeps serving a value the sweep believes it removed.
 *
 * They pull in the real outbox for the same reason. Inverting the sweep put
 * every queued write in its blast radius, and whether a message the user
 * pressed Send on survives a sign-out is not a question a mocked queue can be
 * asked — the answer depends on the key the outbox actually chooses.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import { readJsonCache, resetJsonCacheMemory, writeJsonCache } from "../../core/cache";
import { enqueueMutation, outboxSize, pendingMutations, setOutboxScope } from "../../core/mutations/outbox";
import { clearUserScopedMediaState } from "../mediaSessionCleanup";

jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  resetMediaPlayback: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("../messengerMediaAccess", () => ({
  resetMessengerMediaAccess: jest.fn()
}));
jest.mock("../mediaCache", () => ({
  clearAllMediaCaches: jest.fn().mockResolvedValue(undefined),
  setMediaCacheScope: jest.fn()
}));

const identity = <T,>(value: T) => value;

beforeEach(async () => {
  jest.clearAllMocks();
  resetJsonCacheMemory();
  await AsyncStorage.clear();
  setOutboxScope(null);
});

describe("signing out", () => {
  it("does not let the memory tier serve the previous account's feed", async () => {
    // The sharp one. `clearUserScopedMediaState` removes the key from
    // AsyncStorage, but `readJsonCache` answers from a module-level Map first
    // and never reaches disk — so the delete is invisible to the only code path
    // that reads. User B opens Home and sees user A's feed.
    await writeJsonCache("pulsesoc.native.feed.home", { items: ["A's private post"] });

    await clearUserScopedMediaState();

    expect(await readJsonCache("pulsesoc.native.feed.home", identity)).toBeNull();
  });

  it("clears the account's cached content, not just the six prefixes someone remembered", async () => {
    // Every one of these is user content under a bare key. The sweep list has
    // not grown since it was written, so each new cached surface has been
    // silently opting out of sign-out isolation.
    const leaked = {
      "pulsesoc.native.profile.7": { display_name: "A" },
      "pulsesoc.native.activity.inbox": { unread: 4 },
      "pulsesoc.native.account.state": { balance: 1200 },
      "pulsesoc.native.radio.queue.v1": [{ id: "t1" }],
      "pulsesoc.native.saved.library": { items: ["A's saved post"] },
      "pulsesoc.native.search.recent": ["a private search"]
    };
    for (const [key, value] of Object.entries(leaked)) await writeJsonCache(key, value);

    await clearUserScopedMediaState();
    // Read through a cold memory tier as well, so this asserts the bytes are
    // gone rather than merely unreachable in this process.
    resetJsonCacheMemory();

    for (const key of Object.keys(leaked)) {
      expect({ key, value: await readJsonCache(key, identity) }).toEqual({ key, value: null });
    }
  });

  it("clears drafts the previous account never posted", async () => {
    // A draft is the previous user's unsent writing. It must never be evicted
    // for storage pressure, but sign-out is not storage pressure — leaving it
    // puts A's half-written post in B's composer.
    await writeJsonCache("pulsesoc.native.home.composer.draft.v1", { body: "A's unposted draft" });
    await writeJsonCache("pulsesoc.native.status.creator.draft", { caption: "A's status" });

    await clearUserScopedMediaState();
    resetJsonCacheMemory();

    expect(await readJsonCache("pulsesoc.native.home.composer.draft.v1", identity)).toBeNull();
    expect(await readJsonCache("pulsesoc.native.status.creator.draft", identity)).toBeNull();
  });

  it("keeps the things that belong to the device rather than the account", async () => {
    // The reason this is a classification and not a blanket wipe. Regenerating
    // the push installation id silently breaks notifications for the next
    // account, and clearing the remembered-accounts list defeats the login
    // screen that reads it.
    await AsyncStorage.setItem("pulsesoc.native.push.installation_id", "device-abc");
    await AsyncStorage.setItem("pulsesoc.native.session.rememberedAccounts.v1", '["a@example.com"]');
    await AsyncStorage.setItem("pulsesoc.native.settings.v1", '{"language":"fr"}');

    await clearUserScopedMediaState();

    expect(await AsyncStorage.getItem("pulsesoc.native.push.installation_id")).toBe("device-abc");
    expect(await AsyncStorage.getItem("pulsesoc.native.session.rememberedAccounts.v1")).toBe('["a@example.com"]');
    expect(await AsyncStorage.getItem("pulsesoc.native.settings.v1")).toBe('{"language":"fr"}');
  });

  it("does not destroy a message the user already pressed send on", async () => {
    // The sharpest edge of inverting the sweep. A queued message is not a
    // cache and not a draft: the user pressed Send, and the app has been
    // showing them a bubble ever since. Deleting it at sign-out means a
    // message they believe was sent silently never sends.
    //
    // It is safe to leave precisely because the outbox is scoped per account
    // in its own key — the leak that justifies sweeping drafts, where the next
    // account's composer reads a bare key, has no equivalent here. B's session
    // cannot see or drain A's queue; A signing back in resumes it.
    setOutboxScope(41);
    await enqueueMutation({
      type: "messenger.send",
      idempotencyKey: "client-msg-1",
      stream: "conversation:9",
      payload: { conversationId: 9, payload: { body: "A's unsent message" } }
    });
    expect(await outboxSize()).toBe(1);

    await clearUserScopedMediaState();

    expect(await outboxSize()).toBe(1);
    expect((await pendingMutations()).map((op) => op.idempotencyKey)).toEqual(["client-msg-1"]);
  });

  it("leaves the biometric credential for the auth layer to decide about", async () => {
    // `signOut({ clearBiometrics })` makes this call deliberately: an ordinary
    // sign-out keeps a Face-ID-gated refresh token so the user can return.
    // Sweeping it here would override that decision from the wrong layer.
    await AsyncStorage.setItem("pulsesoc.native.session.biometric.envelope.v2", "sealed");

    await clearUserScopedMediaState();

    expect(await AsyncStorage.getItem("pulsesoc.native.session.biometric.envelope.v2")).toBe("sealed");
  });
});
