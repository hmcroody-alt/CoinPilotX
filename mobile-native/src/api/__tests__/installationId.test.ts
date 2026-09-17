/**
 * The push installation id must be one value per device, even under concurrency.
 *
 * `getPushInstallationId` is a read-then-write: read SecureStore, and if nothing is
 * stored, mint an id and store it. Two callers that reach the read before either
 * reaches the write both see nothing and both mint — and they are not hypothetical
 * callers. `api/push.ts` registers the alert token and `api/calls.ts` registers the
 * VoIP token, both during start-up, neither awaiting the other.
 *
 * Nothing errors when that happens. Both registrations succeed, under different device
 * ids. The backend suppresses the incoming-call alert push for device ids that hold an
 * active VoIP token, so a split means the alert is filed under an id with no VoIP token
 * and is never suppressed: CallKit rings *and* a banner arrives. The duplicate ring is
 * the only visible symptom of a race two layers down.
 *
 * These tests drive the interleaving deterministically through a deferred SecureStore
 * rather than hoping real timing reproduces it.
 */

const mockGetItemAsync = jest.fn();
const mockSetItemAsync = jest.fn();

jest.mock("../../native/secureStore", () => ({
  getItemAsync: (...args: unknown[]) => mockGetItemAsync(...args),
  setItemAsync: (...args: unknown[]) => mockSetItemAsync(...args),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: "afterFirstUnlockThisDeviceOnly",
}));

const KEY = "pulsesoc.native.push.installation_id";
/** What the module must write so a *locked* phone can still read the id back. */
const LOCKED_READABLE = { keychainAccessible: "afterFirstUnlockThisDeviceOnly" };

/**
 * Re-imported per test on purpose.
 *
 * The module carries process state — the shared in-flight promise, and the flag that
 * makes the accessibility upgrade happen once per launch. Sharing one instance across
 * tests makes each one depend on the order of the ones before it: the upgrade assertions
 * would pass or fail according to whether an earlier test had already consumed the single
 * upgrade. A fresh module per test is what a fresh app launch actually looks like.
 */
let getPushInstallationId: typeof import("../installationId").getPushInstallationId;

/** A promise plus the handles to settle it from the test body. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  mockGetItemAsync.mockReset();
  mockSetItemAsync.mockReset();
  mockSetItemAsync.mockResolvedValue(undefined);
  jest.resetModules();
  getPushInstallationId = require("../installationId").getPushInstallationId;
});

describe("push installation id", () => {
  it("gives concurrent callers the same id when the store starts empty", async () => {
    // Hold the read open so both callers are past the entry point and inside the
    // read before either can proceed to the write. This is the exact window that
    // produced three ids in ~2ms on device.
    const read = deferred<string>();
    mockGetItemAsync.mockReturnValue(read.promise);

    const alertRegistration = getPushInstallationId();
    const voipRegistration = getPushInstallationId();

    read.resolve("");
    const [alertId, voipId] = await Promise.all([alertRegistration, voipRegistration]);

    expect(alertId).toBe(voipId);
    expect(alertId).toBeTruthy();
  });

  it("mints and stores exactly one id for a burst of callers", async () => {
    // Distinct from the test above: that one pins what the callers *receive*, this one
    // pins what the device *stores*. Returning one id while writing another would pass
    // the first test and still leave the persisted id disagreeing with both
    // registrations on the next launch.
    const read = deferred<string>();
    mockGetItemAsync.mockReturnValue(read.promise);

    const callers = [getPushInstallationId(), getPushInstallationId(), getPushInstallationId()];
    read.resolve("");
    const ids = await Promise.all(callers);

    expect(new Set(ids).size).toBe(1);
    expect(mockSetItemAsync).toHaveBeenCalledTimes(1);
    expect(mockSetItemAsync).toHaveBeenCalledWith(KEY, ids[0], LOCKED_READABLE);
  });

  it("returns the stored id unchanged, rewriting it only to widen when it can be read", async () => {
    // The rewrite is a migration, not an overwrite: same value, different accessibility.
    // Installs made before this carry the id under the `whenUnlocked` default, and the
    // keychain offers no way to ask which accessibility an item has, so writing it again
    // is the only way to move it. What must never change is the value itself — a new id
    // here would silently split this device's alert and VoIP registrations.
    mockGetItemAsync.mockResolvedValue("native-ios-existing-0123456789");

    const id = await getPushInstallationId();

    expect(id).toBe("native-ios-existing-0123456789");
    expect(mockSetItemAsync).toHaveBeenCalledWith(KEY, "native-ios-existing-0123456789", LOCKED_READABLE);
  });

  it("upgrades the stored id once per launch, not on every read", async () => {
    // Every authenticated request on the call path reads this id. Rewriting a keychain
    // item on each one is pointless work on the latency-sensitive answer path.
    mockGetItemAsync.mockResolvedValue("native-ios-existing-0123456789");

    await getPushInstallationId();
    await getPushInstallationId();
    await getPushInstallationId();

    expect(mockSetItemAsync).toHaveBeenCalledTimes(1);
  });

  it("retries the upgrade after a failed write rather than giving up for the process", async () => {
    // The first attempt can land while the device is locked *and* the item is still
    // stored under the old accessibility — precisely the state the upgrade exists to
    // leave. Marking it done anyway would strand the device there until the app is
    // killed and relaunched.
    mockGetItemAsync.mockResolvedValue("native-ios-existing-0123456789");
    mockSetItemAsync.mockRejectedValueOnce(new Error("keychain locked"));

    await getPushInstallationId();
    await getPushInstallationId();

    expect(mockSetItemAsync).toHaveBeenCalledTimes(2);
  });

  it("does not mint an id when the keychain refuses the read", async () => {
    // The load-bearing distinction. `searchKeyChain` returns null only for
    // `errSecItemNotFound` and throws for every other status, so a throw means the keychain
    // refused — on iOS, overwhelmingly a locked device — not that the item is absent.
    // Collapsing the two (`.catch(() => "")` around the read, then minting) is what the
    // implementation used to do, and it is now actively dangerous: the item is written
    // `afterFirstUnlock`, so a mint during a locked read would *overwrite* the real
    // installation id. The device's alert registration would then be filed under an id its
    // VoIP token no longer matches, and the backend would stop suppressing the alert push.
    // Empty says "not known right now"; every caller handles that by omitting the id.
    mockGetItemAsync.mockRejectedValue(new Error("errSecInteractionNotAllowed"));

    const id = await getPushInstallationId();

    expect(id).toBe("");
    expect(mockSetItemAsync).not.toHaveBeenCalled();
  });

  it("re-reads on a later call instead of caching the id for the process", async () => {
    // A process-lifetime memo would close the race too, and would be wrong. SecureStore
    // reads can fail transiently while the device is locked — which is exactly when a
    // VoIP push arrives — so a memo would pin the id minted during that failure and
    // diverge from the stored one for the rest of the process. Re-reading lets the next
    // call recover the real id.
    mockGetItemAsync.mockRejectedValueOnce(new Error("keychain locked"));
    const whileLocked = await getPushInstallationId();

    mockGetItemAsync.mockResolvedValue("native-ios-real-9876543210");
    const afterUnlock = await getPushInstallationId();

    expect(whileLocked).not.toBe(afterUnlock);
    expect(afterUnlock).toBe("native-ios-real-9876543210");
  });

  it("does not wedge: a failed read still lets the next call through", async () => {
    // Positive control for the in-flight handle. If it were left set after a rejection,
    // every later caller would await a dead promise and both registrations would hang
    // forever — a far worse failure than the duplicate ring being fixed here. The test
    // above would not catch it, because it never sees a *rejected* shared promise.
    const read = deferred<string>();
    // The implementation is what normally attaches a handler to this rejection. If a
    // regression stops it from doing so, an unhandled rejection takes down the whole
    // jest worker and the file reports a crash instead of a failed assertion — which
    // hides *which* guarantee broke. This no-op keeps the failure legible.
    read.promise.catch(() => undefined);
    mockGetItemAsync.mockReturnValueOnce(read.promise);
    const first = getPushInstallationId();
    read.reject(new Error("keychain unavailable"));
    await first;

    mockGetItemAsync.mockResolvedValue("native-ios-recovered-5555555555");
    await expect(getPushInstallationId()).resolves.toBe("native-ios-recovered-5555555555");
  });
});
