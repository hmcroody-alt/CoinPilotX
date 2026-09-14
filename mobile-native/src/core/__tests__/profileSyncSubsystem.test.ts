import { invalidateNativeSync, NATIVE_SYNC_SUBSYSTEMS, registerSyncInvalidation } from "../eventSync";

/**
 * A changed avatar reaches the rest of the app through this channel and nothing
 * else. The union type and the runtime gate used to be two hand-written lists,
 * so a subsystem could be spelled correctly, typecheck, and still be dropped
 * silently by `dedupeSubsystems` — which is exactly what an avatar that updates
 * on Profile but nowhere else looks like.
 */
describe("profile invalidation channel", () => {
  it("is a subsystem the runtime gate accepts, not only one the types allow", async () => {
    expect(NATIVE_SYNC_SUBSYSTEMS).toContain("profile");
    const handler = jest.fn();
    const stop = registerSyncInvalidation("profile", handler);
    await invalidateNativeSync(["profile"], "profile_avatar_updated");
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({
      reason: "profile_avatar_updated",
      subsystems: ["profile"]
    }));
    stop();
  });

  it("stops calling a handler once its subscription is released", async () => {
    const handler = jest.fn();
    registerSyncInvalidation("profile", handler)();
    await invalidateNativeSync(["profile"], "profile_cover_updated");
    expect(handler).not.toHaveBeenCalled();
  });

  it("does not wake subscribers of other subsystems", async () => {
    const profileHandler = jest.fn();
    const reelsHandler = jest.fn();
    const stopProfile = registerSyncInvalidation("profile", profileHandler);
    const stopReels = registerSyncInvalidation("reels", reelsHandler);
    await invalidateNativeSync(["profile"], "profile_avatar_updated");
    expect(profileHandler).toHaveBeenCalledTimes(1);
    expect(reelsHandler).not.toHaveBeenCalled();
    stopProfile();
    stopReels();
  });
});
