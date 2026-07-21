import { useEffect, useRef } from "react";
import { InteractionManager } from "react-native";
import { perfNow, recordDuration, type PerfAttributes } from "./perfTrace";

/**
 * Instruments a screen with two client-observed timings, both measured from the
 * screen's first render:
 *
 * - `screen.firstRender`  — first render -> first commit (skeleton/shell visible).
 * - `screen.interactive`  — first render -> interactions settled (post-mount work done).
 *
 * These are honest render-side metrics; they do not include navigation dispatch
 * time before the screen begins rendering. Safe to call unconditionally — when
 * tracing is disabled the recorded durations are dropped at the sink boundary and
 * the InteractionManager callback does effectively nothing.
 */
export function useScreenPerf(route: string, attributes?: PerfAttributes): void {
  // Captured once, on the first render, via the ref initializer.
  const startRef = useRef<number>(perfNow());
  const firstRenderRecorded = useRef(false);
  const interactiveRecorded = useRef(false);

  useEffect(() => {
    if (!firstRenderRecorded.current) {
      firstRenderRecorded.current = true;
      recordDuration("screen.firstRender", perfNow() - startRef.current, { route, ...attributes });
    }

    const handle = InteractionManager.runAfterInteractions(() => {
      if (interactiveRecorded.current) return;
      interactiveRecorded.current = true;
      recordDuration("screen.interactive", perfNow() - startRef.current, { route, ...attributes });
    });

    return () => handle.cancel();
    // route is intentionally the only dependency; attributes are captured on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route]);
}
