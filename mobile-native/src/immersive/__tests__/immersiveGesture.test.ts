/**
 * The gesture arbiter, pinned at the boundaries.
 *
 * Almost every assertion here is about a gesture that is *refused*: a swipe past
 * the end of a carousel, a swipe up with nothing below, a swipe of any kind from
 * inside a zoom. Those are the cases where being wrong is silent — the user
 * simply ends up somewhere they did not ask to be, and no log records it.
 */
import {
  CAROUSEL_COMMIT_DISTANCE,
  ENTRY_COMMIT_DISTANCE,
  FLICK_VELOCITY,
  ImmersiveGestureInput,
  resolveImmersiveGesture
} from "../immersiveGesture";

/** A post in the middle of the feed holding a three-frame carousel, at frame 1. */
function drag(over: Partial<ImmersiveGestureInput> = {}): ImmersiveGestureInput {
  return {
    translationX: 0,
    translationY: 0,
    velocityX: 0,
    velocityY: 0,
    zoom: 1,
    carouselLength: 3,
    carouselIndex: 1,
    canGoPrevious: true,
    canGoNext: true,
    ...over
  };
}

describe("the thresholds themselves", () => {
  /**
   * Pinned to literals rather than spent as symbols. A test that asserts
   * `ENTRY_COMMIT_DISTANCE > CAROUSEL_COMMIT_DISTANCE` in terms of the constants
   * moves its own goalposts the moment either is changed, and a widened
   * threshold is exactly the drift this file exists to catch.
   */
  it("makes leaving a post cost more travel than moving inside one", () => {
    expect(CAROUSEL_COMMIT_DISTANCE).toBe(60);
    expect(ENTRY_COMMIT_DISTANCE).toBe(90);
    expect(FLICK_VELOCITY).toBe(600);
  });
});

describe("the vertical axis moves between posts", () => {
  it("goes to the next post on a swipe up", () => {
    expect(resolveImmersiveGesture(drag({ translationY: -120 }))).toBe("entry-next");
  });

  it("goes to the previous post on a swipe down", () => {
    expect(resolveImmersiveGesture(drag({ translationY: 120 }))).toBe("entry-previous");
  });

  it("does nothing for a drag that never committed", () => {
    expect(resolveImmersiveGesture(drag({ translationY: -ENTRY_COMMIT_DISTANCE }))).toBe("none");
    expect(resolveImmersiveGesture(drag({ translationY: 40 }))).toBe("none");
  });

  /** A short fast flick is intent. Without this the feed can only be dragged. */
  it("commits a fast flick that barely travelled", () => {
    expect(resolveImmersiveGesture(drag({ translationY: -12, velocityY: -1400 }))).toBe("entry-next");
    expect(resolveImmersiveGesture(drag({ translationY: 12, velocityY: 1400 }))).toBe("entry-previous");
  });

  it("does not commit a slow flick that barely travelled", () => {
    expect(resolveImmersiveGesture(drag({ translationY: -12, velocityY: -200 }))).toBe("none");
  });
});

describe("the horizontal axis stays inside the post", () => {
  it("moves to the next frame of the carousel", () => {
    expect(resolveImmersiveGesture(drag({ translationX: -90 }))).toBe("carousel-next");
  });

  it("moves to the previous frame of the carousel", () => {
    expect(resolveImmersiveGesture(drag({ translationX: 90 }))).toBe("carousel-previous");
  });

  /**
   * §6, stated as a refusal. A fall-through here would make the boundary between
   * "inside this post" and "on to the next" invisible: the same swipe would
   * sometimes advance a frame and sometimes eject you into somebody else's media,
   * depending on a count the user cannot see.
   */
  it("refuses to leave the post at the last frame", () => {
    expect(resolveImmersiveGesture(drag({ translationX: -90, carouselIndex: 2 }))).toBe("none");
  });

  it("refuses to leave the post at the first frame", () => {
    expect(resolveImmersiveGesture(drag({ translationX: 90, carouselIndex: 0 }))).toBe("none");
  });

  /** A single-medium post has no horizontal behaviour at all -- not even a dismiss. */
  it.each([
    ["one medium", 1],
    ["none declared", 0],
    ["a nonsense count", NaN]
  ])("has nothing to do horizontally in a post with %s", (_label, carouselLength) => {
    expect(resolveImmersiveGesture(drag({ translationX: -300, carouselLength: carouselLength as number }))).toBe(
      "none"
    );
    expect(resolveImmersiveGesture(drag({ translationX: 300, carouselLength: carouselLength as number }))).toBe(
      "none"
    );
  });

  it("commits a horizontal flick that barely travelled", () => {
    expect(resolveImmersiveGesture(drag({ translationX: -10, velocityX: -900 }))).toBe("carousel-next");
  });

  it("does nothing for a horizontal drag that never committed", () => {
    expect(resolveImmersiveGesture(drag({ translationX: -CAROUSEL_COMMIT_DISTANCE }))).toBe("none");
  });
});

describe("which axis won", () => {
  it("gives a mostly-sideways drag to the carousel", () => {
    expect(resolveImmersiveGesture(drag({ translationX: -200, translationY: -150 }))).toBe("carousel-next");
  });

  it("gives a mostly-upward drag to the feed", () => {
    expect(resolveImmersiveGesture(drag({ translationX: -150, translationY: -200 }))).toBe("entry-next");
  });

  /**
   * A perfectly diagonal drag has no axis by distance, so velocity breaks the
   * tie. Picking an axis arbitrarily would make the same gesture do two
   * different things on two different builds.
   */
  it("breaks a perfect diagonal tie on velocity", () => {
    expect(resolveImmersiveGesture(drag({ translationX: -120, translationY: -120, velocityY: -900 }))).toBe(
      "entry-next"
    );
    expect(resolveImmersiveGesture(drag({ translationX: -120, translationY: -120, velocityX: -900 }))).toBe(
      "carousel-next"
    );
  });

  it("has no axis for a gesture that did not move at all", () => {
    expect(resolveImmersiveGesture(drag())).toBe("none");
  });
});

describe("a zoomed photo owns every direction", () => {
  it.each([
    ["sideways", { translationX: -300 }],
    ["upward", { translationY: -300 }],
    ["downward", { translationY: 300 }]
  ])("pans rather than paging on a %s drag", (_label, over) => {
    expect(resolveImmersiveGesture(drag({ ...over, zoom: 2.5 }))).toBe("pan");
  });

  it("pages again once the zoom is released", () => {
    expect(resolveImmersiveGesture(drag({ translationY: -300, zoom: 1 }))).toBe("entry-next");
  });
});

describe("the ends of the feed", () => {
  /**
   * The end of the queue is usually the moment a continuation is in flight, so
   * "there is nothing below" describes this instant and not the feed. Dismissing
   * on it would throw the user out of the session because the network was slow.
   */
  it("refuses to dismiss at the bottom of the feed", () => {
    expect(resolveImmersiveGesture(drag({ translationY: -300, canGoNext: false }))).toBe("none");
    expect(resolveImmersiveGesture(drag({ translationY: -10, velocityY: -2000, canGoNext: false }))).toBe("none");
  });

  /**
   * The top is the one place a drag can close, because the first entry is the
   * thing the user tapped -- pulling down off it returns them to exactly where
   * they tapped it (§25) rather than discarding anything.
   */
  it("returns to the origin when pulled down from the first post", () => {
    expect(resolveImmersiveGesture(drag({ translationY: 300, canGoPrevious: false }))).toBe("close");
  });

  it("does not close on a pull-down that never committed", () => {
    expect(resolveImmersiveGesture(drag({ translationY: 30, canGoPrevious: false }))).toBe("none");
  });

  /** Horizontal containment still holds at the top: the axis cannot dismiss. */
  it("does not close sideways from the first post", () => {
    expect(
      resolveImmersiveGesture(drag({ translationX: -300, carouselLength: 1, canGoPrevious: false }))
    ).toBe("none");
  });
});

describe("garbage in", () => {
  it("does nothing with no gesture at all", () => {
    expect(resolveImmersiveGesture(null as unknown as ImmersiveGestureInput)).toBe("none");
  });

  it("treats unreadable travel as no travel", () => {
    expect(
      resolveImmersiveGesture(drag({ translationX: NaN, translationY: NaN, velocityX: NaN, velocityY: NaN }))
    ).toBe("none");
  });

  it("treats an unreadable zoom as not zoomed", () => {
    expect(resolveImmersiveGesture(drag({ translationY: -300, zoom: NaN }))).toBe("entry-next");
  });
});
