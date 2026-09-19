/**
 * Which axis owns a drag, and what it means when it is let go.
 *
 * Pure, and separate from the viewer, for the same reason the session model is:
 * every rule here is a *judgement call about a number*, and judgement calls about
 * numbers are the things that quietly drift. Extracted, they can be pinned; left
 * inline in a gesture handler they can only be checked by swiping on a phone.
 *
 * ## The axes are swapped, relative to the existing viewer
 *
 * `NativeMediaViewer` spends the horizontal axis on the collection and the
 * vertical on dismiss. Immersive media is the other way round: vertical moves
 * through other people's media, and horizontal moves *inside* the post you are
 * already in. That is §6 — "the carousel stays contained" — and it is the whole
 * reason this module exists rather than a flag on the old arbiter. The two
 * meanings of "swipe left" are not a parameter of one behaviour; they are two
 * behaviours.
 *
 * ## Containment, stated as a refusal
 *
 * At the last frame of a carousel, a further horizontal swipe resolves to
 * nothing. It does *not* fall through to the next post. A fall-through would
 * make the boundary between "inside this post" and "on to the next" invisible:
 * the same gesture would sometimes advance a frame and sometimes eject you into
 * somebody else's media, depending on a count you cannot see. Containment means
 * the horizontal axis can never change which post you are looking at.
 *
 * An entry holding a single medium therefore has no horizontal behaviour at all.
 * Not a dismiss, not a page — nothing.
 *
 * ## Leaving costs more than moving
 *
 * `ENTRY_COMMIT_DISTANCE` is larger than `CAROUSEL_COMMIT_DISTANCE` on purpose.
 * Moving a frame within a post is trivially reversible — swipe back. Leaving a
 * post loses your place in its carousel and starts somebody else's video. The
 * more expensive outcome is given the longer runway, which is the same ordering
 * the existing viewer already uses (dismiss 90 > swipe 60).
 *
 * ## The bottom of the feed does not dismiss
 *
 * Swiping up with no next entry resolves to nothing, never to a close. At the
 * end of the queue a continuation is usually in flight, so "there is nothing
 * below" is a statement about *this instant*, not about the feed. Dismissing on
 * it would throw the user out of the session because the network was slow.
 *
 * The top is different, and is the one place a drag can close. At the first
 * entry there is nothing above, and the first entry is the thing the user
 * tapped — so pulling down from it puts them back exactly where they tapped it
 * (§25). That is a return, not a discard, which is why it is allowed to be a
 * gesture at all.
 */

/** Horizontal travel that commits a move within the post's own carousel. */
export const CAROUSEL_COMMIT_DISTANCE = 60;
/** Vertical travel that commits a move to another post. Longer: leaving costs more. */
export const ENTRY_COMMIT_DISTANCE = 90;
/**
 * Speed, in points per second, at which a flick commits regardless of distance.
 *
 * Without it the only way to page is a long deliberate drag, which is what makes
 * a feed feel heavy. With it a short fast flick reads as intent, which is what
 * every native vertical feed does.
 */
export const FLICK_VELOCITY = 600;

export type ImmersiveGestureInput = {
  translationX: number;
  translationY: number;
  velocityX?: number;
  velocityY?: number;
  /** Live pinch scale. Above 1 the whole gesture belongs to the photo. */
  zoom?: number;
  /** How many media this post holds. One or fewer means there is no carousel. */
  carouselLength?: number;
  /** Which of them is showing. */
  carouselIndex?: number;
  /** Whether a previous entry exists above this one. */
  canGoPrevious?: boolean;
  /** Whether a next entry exists below this one *right now*. */
  canGoNext?: boolean;
};

export type ImmersiveGestureDecision =
  /** Zoomed in: the drag moves the photo, not the feed. */
  | "pan"
  /** Within this post's own media. */
  | "carousel-next"
  | "carousel-previous"
  /** To another post. */
  | "entry-next"
  | "entry-previous"
  /** Back to the origin (§25). Only from the first entry, pulling down. */
  | "close"
  /** Below threshold, or contained. Snap back. */
  | "none";

function num(value: unknown, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/**
 * Which axis the user meant.
 *
 * Distance first, because a drag is what the hand is doing; velocity only
 * breaks a tie. A gesture with no travel and no speed has no axis, and saying
 * so is better than picking one.
 */
function dominantAxis(input: ImmersiveGestureInput): "horizontal" | "vertical" | null {
  const x = Math.abs(num(input.translationX));
  const y = Math.abs(num(input.translationY));
  if (x > y) return "horizontal";
  if (y > x) return "vertical";
  const vx = Math.abs(num(input.velocityX));
  const vy = Math.abs(num(input.velocityY));
  if (vx > vy) return "horizontal";
  if (vy > vx) return "vertical";
  return null;
}

function committed(distance: number, velocity: number, threshold: number) {
  return Math.abs(distance) > threshold || Math.abs(velocity) >= FLICK_VELOCITY;
}

export function resolveImmersiveGesture(input: ImmersiveGestureInput): ImmersiveGestureDecision {
  if (!input) return "none";

  // Zoomed in, every direction belongs to the photo. Paging out of a zoom would
  // make it impossible to look at the right-hand side of anything.
  if (num(input.zoom, 1) > 1) return "pan";

  const axis = dominantAxis(input);
  if (!axis) return "none";

  if (axis === "horizontal") {
    const length = Math.trunc(num(input.carouselLength, 1));
    if (length <= 1) return "none"; // No carousel: the axis means nothing here.
    if (!committed(num(input.translationX), num(input.velocityX), CAROUSEL_COMMIT_DISTANCE)) return "none";
    const at = Math.trunc(num(input.carouselIndex));
    const forward = num(input.translationX) < 0 || (num(input.translationX) === 0 && num(input.velocityX) < 0);
    if (forward) return at >= length - 1 ? "none" : "carousel-next";
    return at <= 0 ? "none" : "carousel-previous";
  }

  if (!committed(num(input.translationY), num(input.velocityY), ENTRY_COMMIT_DISTANCE)) return "none";
  const forward = num(input.translationY) < 0 || (num(input.translationY) === 0 && num(input.velocityY) < 0);
  if (forward) {
    // Nothing below is a statement about this instant, not about the feed.
    return input.canGoNext ? "entry-next" : "none";
  }
  if (input.canGoPrevious) return "entry-previous";
  // Nothing above: the first entry is the thing that was tapped, so pulling down
  // off it is a return to where it was tapped from.
  return "close";
}
