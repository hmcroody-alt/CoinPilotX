/**
 * The reconciler, exercised against a fake Notification Center.
 *
 * WHAT A GREEN RUN HERE IS AND IS NOT WORTH
 *
 * These tests drive `expo-notifications` through a mock, so they prove the
 * decision logic — which identifiers get dismissed, which get left alone, what
 * the badge is set to — and nothing about whether iOS honours the call. That
 * half is Stage 14's job and cannot be faked here. What CAN be proven here is
 * every way the logic could be wrong while still looking like it works on a
 * device: dismissing too much is invisible once the shade is empty, and the
 * badge being wrong by exactly the number dismissed looks plausible.
 *
 * So the suite leans on the cases where a bug produces a *believable* result:
 * other accounts' alerts, other conversations' alerts, non-message families, a
 * failed read-state lookup, a failed badge fetch, and an account switch landing
 * mid-pass.
 */

import * as Notifications from "expo-notifications";
import { fetchMessageReadState } from "../../api/messageNotifications";
import { refreshUnreadCounts } from "../../core/unreadCounts";
import {
  __resetNotificationReconciler,
  applyBadgeFromUnreadCounts,
  getNotificationScope,
  reconcileMessageNotifications,
  setNotificationScope
} from "../messageNotificationReconciler";

jest.mock("expo-notifications", () => ({
  getPresentedNotificationsAsync: jest.fn(),
  dismissNotificationAsync: jest.fn(),
  dismissAllNotificationsAsync: jest.fn(),
  setBadgeCountAsync: jest.fn()
}));

jest.mock("../../api/messageNotifications", () => ({
  fetchMessageReadState: jest.fn()
}));

jest.mock("../../core/unreadCounts", () => ({
  refreshUnreadCounts: jest.fn()
}));

const getPresented = Notifications.getPresentedNotificationsAsync as jest.Mock;
const dismissOne = Notifications.dismissNotificationAsync as jest.Mock;
const dismissAll = Notifications.dismissAllNotificationsAsync as jest.Mock;
const setBadge = Notifications.setBadgeCountAsync as jest.Mock;
const readState = fetchMessageReadState as jest.Mock;
const unreadCounts = refreshUnreadCounts as jest.Mock;

const ME = 4242;
const OTHER_ACCOUNT = 777;

/** A delivered notification, as `getPresentedNotificationsAsync` returns one. */
function delivered(identifier: string, data: Record<string, unknown>) {
  return { request: { identifier, content: { data, title: "Alex", body: "see you at 6" } } };
}

function messageAlert(
  identifier: string,
  opts: { messageId: number; conversationId?: number; recipientUserId?: number | null }
) {
  const data: Record<string, unknown> = {
    schemaVersion: 2,
    notificationType: "message",
    messageId: opts.messageId,
    conversationId: opts.conversationId ?? 12,
    senderId: 99
  };
  if (opts.recipientUserId !== null) data.recipientUserId = opts.recipientUserId ?? ME;
  return delivered(identifier, data);
}

/** A pre-contract alert: ids and a legacy type, no recipient. */
function legacyAlert(identifier: string, messageId: number, conversationId = 12) {
  return delivered(identifier, { type: "chat_message", message_id: messageId, conversation_id: conversationId });
}

function answer(over: Partial<{ read: number[]; unread: number[]; obsolete: number[]; unknown: number[]; degraded: boolean }> = {}) {
  return { read: [], unread: [], obsolete: [], unknown: [], ...over };
}

function counts(total: number, loadedAt = Date.now()) {
  return { bellCount: 0, messageCount: total, commerceCount: 0, totalCount: total, loadedAt, raw: {} };
}

/**
 * Sign in the way the app does — through the same setter `stateFor` calls,
 * rather than a back door onto the module variable. The flush and mock reset
 * are belt-and-braces: publishing a scope is a pure setter (there is a test for
 * that), so nothing should be pending, and a test that starts failing here is
 * telling you the setter grew a side effect again.
 */
async function signIn(userId = ME) {
  setNotificationScope(userId);
  await flush();
  jest.clearAllMocks();
  installDefaults();
}

async function flush() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

function installDefaults() {
  getPresented.mockResolvedValue([]);
  dismissOne.mockResolvedValue(undefined);
  setBadge.mockResolvedValue(undefined);
  readState.mockResolvedValue(answer());
  unreadCounts.mockResolvedValue(counts(0));
}

beforeEach(() => {
  jest.clearAllMocks();
  __resetNotificationReconciler();
  installDefaults();
});

describe("selective dismissal", () => {
  it("dismisses the read message's OS identifier and nothing else", async () => {
    await signIn();
    getPresented.mockResolvedValue([
      messageAlert("os-read", { messageId: 1 }),
      messageAlert("os-unread", { messageId: 2 })
    ]);
    readState.mockResolvedValue(answer({ read: [1], unread: [2] }));

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    expect(dismissOne).toHaveBeenCalledTimes(1);
    expect(dismissOne).toHaveBeenCalledWith("os-read");
    expect(result.dismissed).toBe(1);
    expect(result.preserved.still_unread).toBe(1);
  });

  it("never calls dismissAllNotificationsAsync, whatever the shape of the shade", async () => {
    await signIn();
    getPresented.mockResolvedValue([
      messageAlert("a", { messageId: 1 }),
      messageAlert("b", { messageId: 2 }),
      messageAlert("c", { messageId: 3 })
    ]);
    readState.mockResolvedValue(answer({ read: [1, 2, 3] }));

    await reconcileMessageNotifications({ trigger: "foreground" });

    // Every alert on the shade was read, so "dismiss everything" would produce
    // an identical end state here. It is still forbidden: the next time the
    // shade also holds a missed call, the same line would take that too.
    expect(dismissAll).not.toHaveBeenCalled();
    expect(dismissOne).toHaveBeenCalledTimes(3);
  });

  it("dismisses by the OS identifier, never by the logical notification key", async () => {
    await signIn();
    getPresented.mockResolvedValue([
      delivered("os-assigned-abc123", {
        notificationType: "message",
        messageId: 1,
        conversationId: 12,
        recipientUserId: ME,
        notificationKey: "message:12:1"
      })
    ]);
    readState.mockResolvedValue(answer({ read: [1] }));

    await reconcileMessageNotifications({ trigger: "foreground" });

    expect(dismissOne).toHaveBeenCalledWith("os-assigned-abc123");
    expect(dismissOne).not.toHaveBeenCalledWith("message:12:1");
  });

  it("preserves an alert the OS gave no identifier for", async () => {
    await signIn();
    getPresented.mockResolvedValue([delivered("", { notificationType: "message", messageId: 1, conversationId: 12 })]);

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    expect(dismissOne).not.toHaveBeenCalled();
    expect(result.preserved.no_os_identifier).toBe(1);
  });

  it("counts a refused dismissal without throwing or aborting the rest", async () => {
    await signIn();
    getPresented.mockResolvedValue([
      messageAlert("os-1", { messageId: 1 }),
      messageAlert("os-2", { messageId: 2 })
    ]);
    readState.mockResolvedValue(answer({ read: [1, 2] }));
    dismissOne.mockRejectedValueOnce(new Error("OS said no"));

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    expect(result.failed).toBe(1);
    expect(result.dismissed).toBe(1);
  });
});

describe("what must survive", () => {
  it("preserves every non-message family without asking the server about it", async () => {
    await signIn();
    getPresented.mockResolvedValue([
      delivered("call", { notificationType: "missed_call", conversationId: 12 }),
      delivered("live", { notificationType: "live" }),
      delivered("sec", { notificationType: "security" }),
      delivered("order", { notificationType: "order" }),
      delivered("crypto", { notificationType: "price_alert" })
    ]);

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    expect(dismissOne).not.toHaveBeenCalled();
    expect(readState).not.toHaveBeenCalled();
    expect(result.preserved.declared_other_type).toBe(5);
  });

  it("preserves another account's message without asking the server about it", async () => {
    await signIn();
    getPresented.mockResolvedValue([
      messageAlert("theirs", { messageId: 1, recipientUserId: OTHER_ACCOUNT }),
      messageAlert("mine", { messageId: 2 })
    ]);
    readState.mockResolvedValue(answer({ read: [2] }));

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    // Asking would leak that message 1 exists into this account's request.
    expect(readState).toHaveBeenCalledWith([2]);
    expect(dismissOne).toHaveBeenCalledTimes(1);
    expect(dismissOne).toHaveBeenCalledWith("mine");
    expect(result.preserved.other_account).toBe(1);
  });

  it("preserves everything when the read-state lookup is degraded", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 1 })]);
    readState.mockResolvedValue(answer({ unknown: [1], degraded: true }));

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    // A lookup that failed must not read as "not unread".
    expect(dismissOne).not.toHaveBeenCalled();
    expect(result.degraded).toBe(true);
    expect(result.preserved.read_state_unavailable).toBe(1);
  });

  it("does nothing at all when signed out", async () => {
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 1 })]);

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    expect(result.skipReason).toBe("signed_out");
    expect(getPresented).not.toHaveBeenCalled();
    expect(dismissOne).not.toHaveBeenCalled();
  });

  it("survives an unreadable delivered list without touching anything", async () => {
    await signIn();
    getPresented.mockRejectedValue(new Error("no permission"));

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    expect(result.skipReason).toBe("delivered_unreadable");
    expect(dismissOne).not.toHaveBeenCalled();
  });
});

describe("conversation scoping", () => {
  it("opening one thread leaves another thread's alerts alone", async () => {
    await signIn();
    getPresented.mockResolvedValue([
      messageAlert("this-thread", { messageId: 1, conversationId: 12 }),
      messageAlert("other-thread", { messageId: 2, conversationId: 34 })
    ]);
    readState.mockResolvedValue(answer({ read: [1] }));

    const result = await reconcileMessageNotifications({ trigger: "conversation_opened", conversationId: 12 });

    expect(readState).toHaveBeenCalledWith([1]);
    expect(dismissOne).toHaveBeenCalledTimes(1);
    expect(dismissOne).toHaveBeenCalledWith("this-thread");
    expect(result.preserved.other_conversation).toBe(1);
  });

  it("offline, treats opening the thread as proof and skips the network", async () => {
    await signIn();
    getPresented.mockResolvedValue([
      messageAlert("this-thread", { messageId: 1, conversationId: 12 }),
      messageAlert("other-thread", { messageId: 2, conversationId: 34 })
    ]);

    await reconcileMessageNotifications({ trigger: "conversation_opened", conversationId: 12, localOnly: true });

    expect(readState).not.toHaveBeenCalled();
    expect(dismissOne).toHaveBeenCalledTimes(1);
    expect(dismissOne).toHaveBeenCalledWith("this-thread");
  });

  it("online, does NOT treat opening the thread as blanket proof", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("arrived-after", { messageId: 9, conversationId: 12 })]);
    readState.mockResolvedValue(answer({ unread: [9] }));

    // The message whose push landed while the user was still reading the thread
    // is genuinely unread. Online we can ask, so we ask.
    const result = await reconcileMessageNotifications({ trigger: "conversation_opened", conversationId: 12 });

    expect(dismissOne).not.toHaveBeenCalled();
    expect(result.preserved.still_unread).toBe(1);
  });

  it("offline with no conversation named, dismisses nothing it cannot prove", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 1 })]);

    const result = await reconcileMessageNotifications({ trigger: "reconnected", localOnly: true });

    expect(readState).not.toHaveBeenCalled();
    expect(dismissOne).not.toHaveBeenCalled();
    expect(result.degraded).toBe(true);
  });

  it("trusts message ids the caller already knows are read", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 1 }), messageAlert("os-2", { messageId: 2 })]);
    readState.mockResolvedValue(answer({ unread: [2] }));

    await reconcileMessageNotifications({ trigger: "marked_read", knownReadMessageIds: [1] });

    expect(readState).toHaveBeenCalledWith([2]);
    expect(dismissOne).toHaveBeenCalledWith("os-1");
    expect(dismissOne).toHaveBeenCalledTimes(1);
  });
});

describe("legacy alerts and the obsolete bucket", () => {
  it("clears a legacy alert once the server confirms this account read it", async () => {
    await signIn();
    getPresented.mockResolvedValue([legacyAlert("upgrade-day", 1)]);
    readState.mockResolvedValue(answer({ read: [1] }));

    await reconcileMessageNotifications({ trigger: "cold_start" });

    // The payload could not say who it was for, but the server answers only for
    // the authenticated user — so a `read` verdict IS the ownership proof.
    expect(dismissOne).toHaveBeenCalledWith("upgrade-day");
  });

  it("clears an obsolete message when the payload proved the recipient", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("deleted", { messageId: 1 })]);
    readState.mockResolvedValue(answer({ obsolete: [1] }));

    const result = await reconcileMessageNotifications({ trigger: "message_removed" });

    expect(dismissOne).toHaveBeenCalledWith("deleted");
    expect(result.dismissed).toBe(1);
  });

  it("preserves an obsolete legacy alert, because obsolete also means not-your-conversation", async () => {
    await signIn();
    getPresented.mockResolvedValue([legacyAlert("ambiguous", 1)]);
    readState.mockResolvedValue(answer({ obsolete: [1] }));

    const result = await reconcileMessageNotifications({ trigger: "message_removed" });

    // "The message was deleted" and "you are not a participant" arrive in the
    // same bucket. The second is exactly what the server would say about the
    // other account's alert still sitting on this handset.
    expect(dismissOne).not.toHaveBeenCalled();
    expect(result.preserved.obsolete_unproven_owner).toBe(1);
  });
});

describe("badge", () => {
  it("sets the badge from the server, not from the number dismissed", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 1 })]);
    readState.mockResolvedValue(answer({ read: [1] }));
    unreadCounts.mockResolvedValue(counts(7));

    await reconcileMessageNotifications({ trigger: "foreground" });

    // One alert dismissed, badge still 7 — muted threads and hand-swiped alerts
    // mean the shade's length was never the unread count.
    expect(setBadge).toHaveBeenCalledWith(7);
  });

  it("refuses to write a badge from a snapshot that never loaded", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 1 })]);
    readState.mockResolvedValue(answer({ read: [1] }));
    // `refreshUnreadCounts` swallows its errors and hands back the module's
    // zero-initialised snapshot. Writing it is a blind zero in disguise.
    unreadCounts.mockResolvedValue(counts(0, 0));

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    expect(setBadge).not.toHaveBeenCalled();
    expect(result.degraded).toBe(true);
    // The dismissal still happened — the badge being unknowable is not a reason
    // to leave a read alert on the lock screen.
    expect(result.dismissed).toBe(1);
  });

  it("writes a genuine zero when the server really says zero", async () => {
    await signIn();
    unreadCounts.mockResolvedValue(counts(0, Date.now()));

    await applyBadgeFromUnreadCounts();

    expect(setBadge).toHaveBeenCalledWith(0);
  });

  it("never writes a negative or non-finite badge", async () => {
    await signIn();
    for (const bad of [-1, Number.NaN, Number.POSITIVE_INFINITY]) {
      setBadge.mockClear();
      unreadCounts.mockResolvedValue({ ...counts(0), totalCount: bad });
      await applyBadgeFromUnreadCounts();
      expect(setBadge).not.toHaveBeenCalled();
    }
  });

  it("passes a large count through rather than capping it — 99+ is the OS's job", async () => {
    await signIn();
    unreadCounts.mockResolvedValue(counts(1284));

    await applyBadgeFromUnreadCounts();

    expect(setBadge).toHaveBeenCalledWith(1284);
  });

  it("trues up the badge even when the shade is empty", async () => {
    await signIn();
    getPresented.mockResolvedValue([]);
    unreadCounts.mockResolvedValue(counts(3));

    await reconcileMessageNotifications({ trigger: "foreground" });

    // The user may have swiped the alerts away by hand while unread remains.
    expect(setBadge).toHaveBeenCalledWith(3);
  });

  it("reports degraded rather than throwing when the OS refuses the badge", async () => {
    await signIn();
    unreadCounts.mockResolvedValue(counts(3));
    setBadge.mockRejectedValue(new Error("no"));

    await expect(applyBadgeFromUnreadCounts()).resolves.toBe(false);
  });
});

describe("concurrency and identity", () => {
  it("coalesces lifecycle triggers that arrive in a cluster", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 1 })]);
    readState.mockResolvedValue(answer({ read: [1] }));

    const [first, second, third] = await Promise.all([
      reconcileMessageNotifications({ trigger: "cold_start" }),
      reconcileMessageNotifications({ trigger: "foreground" }),
      reconcileMessageNotifications({ trigger: "reconnected" })
    ]);

    // Three passes would each read the same shade and race to dismiss the same
    // identifier, producing two spurious failures and three badge writes.
    expect(getPresented).toHaveBeenCalledTimes(1);
    expect(dismissOne).toHaveBeenCalledTimes(1);
    expect(first.skipped).toBe(false);
    expect(second.skipReason).toBe("coalesced");
    expect(third.skipReason).toBe("coalesced");
    // A coalesced caller keeps its own trigger so its logs stay truthful.
    expect(second.trigger).toBe("foreground");
  });

  it("aborts without dismissing when the account changes mid-pass", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 1 })]);
    readState.mockImplementation(async () => {
      // The switch lands while we are waiting on the server — which is exactly
      // where a real one lands, because that is the longest await in the pass.
      setNotificationScope(OTHER_ACCOUNT);
      return answer({ read: [1] });
    });

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    expect(dismissOne).not.toHaveBeenCalled();
    expect(result.dismissed).toBe(0);
    expect(result.skipReason).toBe("account_changed_mid_pass");
  });

  it("gives the incoming account its own pass rather than the aborted one", async () => {
    await signIn();
    // Held in an object so TypeScript's control-flow analysis does not narrow
    // the callback-assigned handle to `never` at the call site below.
    const parked: { release: () => void } = { release: () => undefined };
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 1 })]);
    readState.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          parked.release = () => resolve(answer({ read: [1] }));
        })
    );

    const firstPass = reconcileMessageNotifications({ trigger: "foreground" });
    await flush();

    // Switch accounts while the first pass is parked on the server, then do what
    // `AppNavigator`'s identity effect does: ask for a pass for the new account
    // while the old one is still in flight.
    setNotificationScope(OTHER_ACCOUNT);
    const secondPass = reconcileMessageNotifications({ trigger: "account_changed" });
    parked.release();
    await Promise.all([firstPass, secondPass]);
    await flush();

    // Two reads of the shade: the aborted pass, and the incoming account's own.
    // Single-flight is scoped per account precisely so the second is NOT
    // coalesced into the first — it would have been handed the abort, and the
    // incoming account would never get looked at.
    expect(getPresented).toHaveBeenCalledTimes(2);
    expect((await secondPass).skipReason).not.toBe("coalesced");
  });

  it("publishing a scope is a pure setter with no I/O", async () => {
    /**
     * This is a guard against a fix that was tried and reverted.
     *
     * `setNotificationScope` is called from `stateFor`, the AuthState
     * constructor — which runs on every construction, not only on transitions.
     * Kicking a pass from there looked right (it is the one place that sees an
     * account switch) and cost the jest suite 11s → 30s plus an unrelated
     * screen-test failure, because every component reading `useAuth` began
     * dragging a reconciliation behind it. In production it meant OS and
     * network work inside an identity constructor.
     *
     * The identity trigger lives in `AppNavigator`'s effect keyed on the user
     * id instead. If this test starts failing, that move is being undone.
     */
    setNotificationScope(ME);
    await flush();

    expect(getNotificationScope()).toBe(ME);
    expect(getPresented).not.toHaveBeenCalled();
    expect(readState).not.toHaveBeenCalled();
    expect(setBadge).not.toHaveBeenCalled();
  });

  it("clearing the scope stops the reconciler dead, without touching the shade", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 1 })]);

    setNotificationScope(null);
    const result = await reconcileMessageNotifications({ trigger: "account_changed" });

    expect(getNotificationScope()).toBe(0);
    expect(result.skipReason).toBe("signed_out");
    expect(dismissOne).not.toHaveBeenCalled();
  });

  it("scopes a pass to the account signed in when it started", async () => {
    await signIn();
    getPresented.mockResolvedValue([
      messageAlert("mine", { messageId: 1 }),
      messageAlert("theirs", { messageId: 2, recipientUserId: OTHER_ACCOUNT })
    ]);
    readState.mockResolvedValue(answer({ read: [1] }));

    await reconcileMessageNotifications({ trigger: "account_changed" });

    expect(dismissOne).toHaveBeenCalledWith("mine");
    expect(dismissOne).not.toHaveBeenCalledWith("theirs");
  });

  it("is idempotent — a second pass over an already-clean shade dismisses nothing", async () => {
    await signIn();
    const shade = [messageAlert("os-1", { messageId: 1 })];
    getPresented.mockResolvedValue(shade);
    readState.mockResolvedValue(answer({ read: [1] }));

    await reconcileMessageNotifications({ trigger: "foreground" });
    expect(dismissOne).toHaveBeenCalledTimes(1);

    // The OS has now removed it, so the next pass sees an empty shade.
    getPresented.mockResolvedValue([]);
    dismissOne.mockClear();
    await reconcileMessageNotifications({ trigger: "foreground" });

    expect(dismissOne).not.toHaveBeenCalled();
  });
});

describe("privacy", () => {
  it("logs counts and a trigger, never an id, a name or a body", async () => {
    await signIn();
    const log = jest.spyOn(console, "log").mockImplementation(() => undefined);
    getPresented.mockResolvedValue([
      delivered("os-1", {
        notificationType: "message",
        messageId: 8675309,
        conversationId: 314159,
        recipientUserId: ME,
        sender_name: "Alex Rivera",
        message_preview: "see you at 6"
      })
    ]);
    readState.mockResolvedValue(answer({ read: [8675309] }));

    await reconcileMessageNotifications({ trigger: "foreground" });

    const emitted = log.mock.calls.map((call) => call.join(" ")).join("\n");
    for (const secret of ["8675309", "314159", "Alex", "see you at 6", String(ME)]) {
      expect(emitted).not.toContain(secret);
    }
    log.mockRestore();
  });

  it("returns counts only — the result object carries no ids either", async () => {
    await signIn();
    getPresented.mockResolvedValue([messageAlert("os-1", { messageId: 8675309, conversationId: 314159 })]);
    readState.mockResolvedValue(answer({ read: [8675309] }));

    const result = await reconcileMessageNotifications({ trigger: "foreground" });

    // The result is the thing a caller might log or report. It must be safe.
    const serialised = JSON.stringify(result);
    expect(serialised).not.toContain("8675309");
    expect(serialised).not.toContain("314159");
    expect(serialised).not.toContain("os-1");
  });
});
