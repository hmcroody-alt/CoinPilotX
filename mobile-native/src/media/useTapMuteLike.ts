import { useCallback, useEffect, useRef } from "react";

const DOUBLE_TAP_WINDOW_MS = 280;

export type TapMuteLikeOptions = {
  /** Called once per confirmed single tap — product rule: toggles mute. */
  onToggleMuted: () => void;
  /** Called once per confirmed double tap — product rule: likes the content. */
  onLike: () => void;
  /** Optional: fired alongside onToggleMuted, for feedback (glyph pulse, haptics). */
  onSingleTapFeedback?: () => void;
  /** Optional: fired alongside onLike, for feedback (heart burst, haptics), receives tap position. */
  onLikeFeedback?: (x: number, y: number) => void;
  disabled?: boolean;
  doubleTapWindowMs?: number;
};

export type TapMuteLikeHandlers = {
  onPress: (event?: { nativeEvent?: { locationX?: number; locationY?: number } }) => void;
};

/**
 * Shared tap-gesture primitive for every PulseSoc media surface (Reels, feed
 * video, statuses, mixed-media posts).
 *
 * Product rule: a single tap toggles mute, a double tap likes the content.
 * This hook centralizes the timing-based single/double tap disambiguation so
 * every surface behaves identically instead of each screen reimplementing its
 * own setTimeout/lastTap bookkeeping.
 */
export function useTapMuteLike({
  onToggleMuted,
  onLike,
  onSingleTapFeedback,
  onLikeFeedback,
  disabled = false,
  doubleTapWindowMs = DOUBLE_TAP_WINDOW_MS
}: TapMuteLikeOptions): TapMuteLikeHandlers {
  const lastTap = useRef(0);
  const lastTapPos = useRef({ x: 0, y: 0 });
  const pendingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const callbacksRef = useRef({ onToggleMuted, onLike, onSingleTapFeedback, onLikeFeedback });

  useEffect(() => {
    callbacksRef.current = { onToggleMuted, onLike, onSingleTapFeedback, onLikeFeedback };
  }, [onLike, onLikeFeedback, onSingleTapFeedback, onToggleMuted]);

  useEffect(() => () => {
    if (pendingTimer.current) clearTimeout(pendingTimer.current);
  }, []);

  const onPress = useCallback((event?: { nativeEvent?: { locationX?: number; locationY?: number } }) => {
    if (disabled) return;
    const x = event?.nativeEvent?.locationX ?? lastTapPos.current.x;
    const y = event?.nativeEvent?.locationY ?? lastTapPos.current.y;
    lastTapPos.current = { x, y };
    const now = Date.now();
    const elapsed = now - lastTap.current;
    if (elapsed > 0 && elapsed < doubleTapWindowMs) {
      if (pendingTimer.current) {
        clearTimeout(pendingTimer.current);
        pendingTimer.current = null;
      }
      lastTap.current = 0;
      callbacksRef.current.onLike();
      callbacksRef.current.onLikeFeedback?.(x, y);
      return;
    }
    lastTap.current = now;
    pendingTimer.current = setTimeout(() => {
      pendingTimer.current = null;
      callbacksRef.current.onToggleMuted();
      callbacksRef.current.onSingleTapFeedback?.();
    }, doubleTapWindowMs);
  }, [disabled, doubleTapWindowMs]);

  return { onPress };
}
