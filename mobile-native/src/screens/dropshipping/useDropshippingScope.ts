/**
 * Resolving the store every dropshipping route is scoped to.
 *
 * ## Why this is cached at module scope
 *
 * Nine screens need the scope. Resolving it per screen would put nine requests
 * behind a merchant walking from the hub to a product draft, and — worse — a
 * screen that resolved *later* could pick a different store than the one the hub
 * used if the merchant owns more than one. Caching the promise makes the whole
 * navigation stack agree by construction.
 *
 * The cache holds the in-flight promise, not just the value, so two screens
 * mounting in the same frame share one pair of requests rather than racing.
 *
 * A failure is never cached. A merchant who resolves scope while offline must
 * get a real attempt on their next screen, not a permanent "no business" state
 * that only a full app restart clears.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  resolveDropshippingScope,
  stateForError,
  type DropshippingScope,
  type DropshippingState,
  type ScopeGap,
  type ScopeSource,
  type ScopeResolution
} from "../../api/dropshipping";

let cached: Promise<ScopeResolution> | null = null;

function loadScope(): Promise<ScopeResolution> {
  if (!cached) {
    cached = resolveDropshippingScope().catch((error) => {
      cached = null;
      throw error;
    });
  }
  return cached;
}

/**
 * Drop the cached scope.
 *
 * Called on explicit pull-to-refresh, and exported so a test can start from a
 * clean slate — a module-level cache that no test can clear is a module-level
 * cache that makes the second test in a file depend on the first.
 */
export function resetDropshippingScopeCache(): void {
  cached = null;
}

export type ScopeStatus =
  | { phase: "loading" }
  | { phase: "ready"; scope: DropshippingScope; storeName: string; source: ScopeSource }
  | { phase: "missing"; gap: ScopeGap }
  | { phase: "failed"; state: DropshippingState };

export function useDropshippingScope(): { status: ScopeStatus; reload: () => void } {
  const [status, setStatus] = useState<ScopeStatus>({ phase: "loading" });
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const run = useCallback(() => {
    setStatus({ phase: "loading" });
    loadScope().then(
      (resolution) => {
        if (!mounted.current) return;
        setStatus(
          resolution.status === "ok"
            ? {
                phase: "ready",
                scope: resolution.scope,
                storeName: resolution.storeName,
                source: resolution.source
              }
            : { phase: "missing", gap: resolution.gap }
        );
      },
      (error) => {
        if (!mounted.current) return;
        setStatus({ phase: "failed", state: stateForError(error) });
      }
    );
  }, []);

  useEffect(run, [run]);

  const reload = useCallback(() => {
    resetDropshippingScopeCache();
    run();
  }, [run]);

  return { status, reload };
}
