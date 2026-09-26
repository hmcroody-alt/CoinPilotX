/**
 * Where a link tapped outside Messenger gets its navigation from.
 *
 * `openMessageLink` is already tested for *which* door a URL goes through, and
 * this file must not restate that — if it did, the two would have to be updated
 * together and the duplication would be the bug. There is exactly one thing here
 * that `openMessageLink.test.ts` cannot cover, because it is the thing this
 * module adds: the navigation object is not a parameter, it is `navigationRef`.
 *
 * That makes the readiness of the ref a real branch with a real consequence. A
 * tap during the first frames of a cold start, or inside a test that renders
 * `PostCard` bare, arrives before the navigation container exists — and
 * `navigationRef.navigate` on an unready ref is not a no-op in React Navigation,
 * it warns and drops the action. Returning `"blocked"` instead of attempting it
 * is what keeps that from being a warning in the console and nothing on screen.
 *
 * The second case is the one that would otherwise be discovered by a crash in an
 * unrelated suite: `PostCard.test.tsx` renders with no navigation container at
 * all, so if this reached for a context instead of a ref, adding tappable links
 * to the feed would have turned a rendering test into a failure for reasons that
 * have nothing to do with what it asserts.
 */

import { navigationRef } from "../../navigation/notificationRouting";
import { openContentLink } from "../openContentLink";

const POST_URL = "https://pulsesoc.com/pulse/post/2432";

describe("openContentLink — before navigation exists", () => {
  it("reports the tap as blocked rather than attempting to navigate", () => {
    // The real ref, unmounted, which is exactly the state during a cold start.
    expect(navigationRef.isReady()).toBe(false);
    expect(openContentLink(POST_URL)).toBe("blocked");
  });

  it("does not warn or throw, so a bare render stays a render", () => {
    // React Navigation warns when an action is dispatched at an unready ref.
    // Nothing should reach it, so nothing should warn. This is what lets
    // PostCard be rendered without a container.
    const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    const error = jest.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => openContentLink(POST_URL)).not.toThrow();
    expect(warn).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
    warn.mockRestore();
    error.mockRestore();
  });

  it("blocks every kind of URL equally, including an external one", () => {
    // "Blocked" here is about the navigator being absent, so it must not depend
    // on the URL -- an external link handed to the OS would not need navigation
    // at all, but answering "external" while the app cannot yet show anything
    // would be a claim that something happened.
    expect(openContentLink("https://apple.com")).toBe("blocked");
    expect(openContentLink("not a url")).toBe("blocked");
    expect(openContentLink("")).toBe("blocked");
  });
});

describe("openContentLink — once navigation is ready", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("delegates to the app's existing router rather than resolving anything itself", () => {
    // The point of the module is that it contributes *only* the navigation
    // object. So the assertion is that the same `navigate` call the notification
    // router and a Universal Link produce is the one a tapped post body
    // produces -- not that this file knows `PostDetail` is where posts live.
    jest.spyOn(navigationRef, "isReady").mockReturnValue(true);
    const navigate = jest.spyOn(navigationRef, "navigate").mockImplementation(() => undefined);

    expect(openContentLink(POST_URL)).toBe("internal");
    expect(navigate).toHaveBeenCalledWith("PostDetail", expect.objectContaining({ postId: 2432 }));
  });

  it("still keeps an external link out of the navigator", () => {
    jest.spyOn(navigationRef, "isReady").mockReturnValue(true);
    const navigate = jest.spyOn(navigationRef, "navigate").mockImplementation(() => undefined);

    expect(openContentLink("https://apple.com")).toBe("external");
    expect(navigate).not.toHaveBeenCalled();
  });
});
