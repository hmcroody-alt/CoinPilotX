/**
 * The seller verdict, fetched once and refreshed when it can have changed.
 *
 * Seller status changes off-device — a reviewer approves an application, an
 * admin suspends a store — so a value read at login and cached forever is a
 * value that is wrong for as long as the app stays open. It refreshes on focus,
 * which covers the three moments that actually matter: cold launch, coming back
 * from the application screen after submitting, and returning to the app after
 * an approval notification.
 *
 * `loading` starts true and access starts denied. A screen that renders seller
 * tools while `loading` is true has reintroduced the bug this fixes, just
 * narrowed to the first few hundred milliseconds.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useFocusEffect } from "@react-navigation/native";
import {
  DENIED_SELLER_ACCESS,
  fetchSellerAccessState,
  isSellerAccessUnsupported,
  loadCachedSellerAccessState,
  type SellerAccessState
} from "../api/sellerAccess";

export type SellerAccessResult = {
  state: SellerAccessState;
  loading: boolean;
  /** True when the network read failed and `state` is cached or denied. */
  failed: boolean;
  /** True while showing a cached answer that the network has not yet confirmed. */
  stale: boolean;
  /**
   * True when the server has no access-state route, so there is no verdict to
   * honour. Callers must not gate on this: see `isSellerAccessUnsupported`.
   */
  unsupported: boolean;
  refresh: () => Promise<void>;
};

export function useSellerAccess(): SellerAccessResult {
  const [state, setState] = useState<SellerAccessState>(DENIED_SELLER_ACCESS);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [stale, setStale] = useState(false);
  const [unsupported, setUnsupported] = useState(false);
  const mounted = useRef(true);
  // Set once the server has answered. Guards the cache paint below from
  // overwriting a canonical answer with an older one if the two race.
  const canonical = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchSellerAccessState();
      if (!mounted.current) return;
      canonical.current = true;
      setState(next);
      setFailed(false);
      setStale(false);
      setUnsupported(false);
    } catch (error) {
      if (!mounted.current) return;
      if (isSellerAccessUnsupported(error)) {
        // Not a failed check — there is no check on this deployment. Reported
        // separately so callers can fall through to the pre-gate behaviour
        // instead of showing a retry that will never succeed.
        setUnsupported(true);
        setFailed(false);
        setStale(false);
        return;
      }
      // Keep whatever we last knew rather than flipping to denied: a network
      // blip must not eject an approved seller from their own store. The server
      // re-checks every mutation anyway, so a stale "approved" here costs a
      // refused request, not an escape.
      //
      // `unsupported` is cleared here as well as on success. It is a claim
      // about the server, and a 404 followed by a 503 means the last thing we
      // know is "could not check", not "there is no check" — leaving it latched
      // would keep the gate stood down on evidence the next attempt withdrew.
      setUnsupported(false);
      setFailed(true);
      setStale(true);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const cached = await loadCachedSellerAccessState();
      if (cancelled || !mounted.current || canonical.current || !cached) return;
      setState(cached);
      setStale(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useFocusEffect(
    useCallback(() => {
      void refresh();
    }, [refresh])
  );

  return { state, loading, failed, stale, unsupported, refresh };
}
