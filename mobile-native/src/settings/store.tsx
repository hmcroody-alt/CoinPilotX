/**
 * Preference store: local-first, optimistic, self-healing.
 *
 * Lifecycle of a toggle:
 *   1. State updates immediately  → the switch never lags the finger.
 *   2. Snapshot written to AsyncStorage → survives an app kill mid-flight.
 *   3. Patch coalesced for 400ms  → dragging a slider sends one request, not 40.
 *   4. PATCH sent with a revision → server rejects stale writes.
 *   5. On failure: transient errors retry with backoff; permanent errors roll
 *      the value back to the last server-confirmed state and raise an error.
 *
 * The store never blocks first paint. `hydrated` flips as soon as the local
 * snapshot is read; the network reconcile happens behind it.
 */

import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";
import { AppState, AppStateStatus } from "react-native";
import { readJsonCache, writeJsonCache } from "../core/cache";
import {
  fetchRemotePreferences,
  PreferenceSyncError,
  pushPreferencePatch
} from "./api";
import {
  DEFAULT_PREFERENCES,
  normalizePreferences,
  PreferenceGroup,
  Preferences,
  preferencesEqual
} from "./schema";

const CACHE_KEY = "pulsesoc.native.settings.v1";
const COALESCE_MS = 400;
const MAX_RETRY_DELAY_MS = 30_000;

export type SyncStatus = "idle" | "saving" | "saved" | "error" | "offline";

type PersistedSnapshot = {
  preferences: Preferences;
  revision: number;
  /** Groups written locally but not yet confirmed by the server. */
  pendingGroups: PreferenceGroup[];
};

export type PreferencesContextValue = {
  preferences: Preferences;
  /** True once the local snapshot has been read — safe to render Settings. */
  hydrated: boolean;
  /** True while the initial server reconcile is in flight. */
  refreshing: boolean;
  status: SyncStatus;
  /** Human-readable reason for the most recent failure, else null. */
  error: string | null;
  /** Groups with unsaved or in-flight changes — drives per-section spinners. */
  pendingGroups: PreferenceGroup[];
  /**
   * Update one group. Merges into the existing group, so callers pass only the
   * keys they changed. Resolves once the change is durable locally; sync
   * failures surface through `status`/`error` rather than by rejecting.
   */
  update: <K extends PreferenceGroup>(group: K, patch: Partial<Preferences[K]>) => Promise<void>;
  /** Re-read from the server, discarding nothing that is still pending. */
  refresh: () => Promise<void>;
  /** Restore every preference to its shipped default and sync. */
  resetAll: () => Promise<void>;
  /** Dismiss a surfaced error without changing any value. */
  clearError: () => void;
};

const PreferencesContext = createContext<PreferencesContextValue | null>(null);

function normalizeSnapshot(value: PersistedSnapshot): PersistedSnapshot {
  return {
    preferences: normalizePreferences(value?.preferences),
    revision: Number.isFinite(value?.revision) ? Number(value.revision) : 0,
    pendingGroups: Array.isArray(value?.pendingGroups)
      ? value.pendingGroups.filter((group): group is PreferenceGroup => group in DEFAULT_PREFERENCES)
      : []
  };
}

export function PreferencesProvider({ children, enabled = true }: { children: ReactNode; enabled?: boolean }) {
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES);
  const [hydrated, setHydrated] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [status, setStatus] = useState<SyncStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [pendingGroups, setPendingGroups] = useState<PreferenceGroup[]>([]);

  /** Last state the server confirmed — the rollback target. */
  const confirmed = useRef<Preferences>(DEFAULT_PREFERENCES);
  const revision = useRef(0);
  /** Groups changed locally and not yet flushed. */
  const dirty = useRef(new Set<PreferenceGroup>());
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryDelay = useRef(1000);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  /** Latest preferences, readable from callbacks without stale closures. */
  const latest = useRef<Preferences>(DEFAULT_PREFERENCES);

  latest.current = preferences;

  const persist = useCallback(async (next: Preferences, nextRevision: number, pending: PreferenceGroup[]) => {
    await writeJsonCache<PersistedSnapshot>(CACHE_KEY, {
      preferences: next,
      revision: nextRevision,
      pendingGroups: pending
    }).catch(() => undefined);
  }, []);

  const syncPendingState = useCallback(() => {
    if (!mounted.current) return;
    setPendingGroups(Array.from(dirty.current));
  }, []);

  /* ----------------------------- Flush pipeline ---------------------------- */

  const flush = useCallback(async () => {
    if (!enabled || inFlight.current) return;
    const groups = Array.from(dirty.current);
    if (!groups.length) return;

    inFlight.current = true;
    if (mounted.current) setStatus("saving");

    const source = latest.current;
    const patch = groups.reduce<Partial<Preferences>>((acc, group) => {
      (acc as Record<string, unknown>)[group] = source[group];
      return acc;
    }, {});

    try {
      const envelope = await pushPreferencePatch(patch, revision.current);
      // Clear only the groups this request carried; a toggle flipped while the
      // request was in flight stays dirty and flushes on the next pass.
      groups.forEach((group) => dirty.current.delete(group));
      revision.current = envelope.revision || revision.current;

      // Server is authoritative for groups it just accepted, but must not clobber
      // groups the user changed mid-flight.
      const merged = { ...envelope.preferences } as Preferences;
      dirty.current.forEach((group) => {
        (merged as Record<string, unknown>)[group] = latest.current[group];
      });

      confirmed.current = merged;
      latest.current = merged;
      retryDelay.current = 1000;

      if (mounted.current) {
        setPreferences((current) => (preferencesEqual(current, merged) ? current : merged));
        setError(null);
        setStatus(dirty.current.size ? "saving" : "saved");
        syncPendingState();
      }
      await persist(merged, revision.current, Array.from(dirty.current));
    } catch (caught) {
      const syncError = caught instanceof PreferenceSyncError ? caught : new PreferenceSyncError("Could not save.", 0, false);

      if (syncError.permanent) {
        // The server refused this payload — keeping it queued would fail forever.
        // Roll the affected groups back to the last confirmed values.
        groups.forEach((group) => dirty.current.delete(group));
        const rolledBack = { ...latest.current } as Preferences;
        groups.forEach((group) => {
          (rolledBack as Record<string, unknown>)[group] = confirmed.current[group];
        });
        latest.current = rolledBack;
        if (mounted.current) {
          setPreferences(rolledBack);
          setStatus("error");
          setError(syncError.message);
          syncPendingState();
        }
        await persist(rolledBack, revision.current, Array.from(dirty.current));
      } else if (mounted.current) {
        // Transient: keep the optimistic value, keep the group dirty, back off.
        setStatus("offline");
        setError(syncError.message);
        if (retryTimer.current) clearTimeout(retryTimer.current);
        retryTimer.current = setTimeout(() => {
          retryTimer.current = null;
          void flush();
        }, retryDelay.current);
        retryDelay.current = Math.min(MAX_RETRY_DELAY_MS, retryDelay.current * 2);
      }
    } finally {
      inFlight.current = false;
      // A change landed while we were syncing — go again.
      if (mounted.current && dirty.current.size && !retryTimer.current) {
        if (flushTimer.current) clearTimeout(flushTimer.current);
        flushTimer.current = setTimeout(() => {
          flushTimer.current = null;
          void flush();
        }, COALESCE_MS);
      }
    }
  }, [enabled, persist, syncPendingState]);

  const scheduleFlush = useCallback(() => {
    if (flushTimer.current) clearTimeout(flushTimer.current);
    flushTimer.current = setTimeout(() => {
      flushTimer.current = null;
      void flush();
    }, COALESCE_MS);
  }, [flush]);

  /* -------------------------------- Hydration ------------------------------ */

  useEffect(() => {
    mounted.current = true;
    let cancelled = false;

    (async () => {
      const snapshot = await readJsonCache<PersistedSnapshot>(CACHE_KEY, normalizeSnapshot);
      if (cancelled) return;

      if (snapshot) {
        confirmed.current = snapshot.preferences;
        revision.current = snapshot.revision;
        snapshot.pendingGroups.forEach((group) => dirty.current.add(group));
        // Assigned here as well as in the render body: the reconcile below reads
        // `latest.current` to protect unflushed groups, and it can run before
        // React has re-rendered with this snapshot — a cached or immediately
        // resolved response is enough. Waiting for the render would merge the
        // server's values over edits the user has not sent yet.
        latest.current = snapshot.preferences;
        setPreferences(snapshot.preferences);
        syncPendingState();
      }
      setHydrated(true);

      if (!enabled) return;

      setRefreshing(true);
      const remote = await fetchRemotePreferences();
      if (cancelled || !mounted.current) return;
      setRefreshing(false);

      if (!remote) {
        // Offline start: keep local values, retry any queued writes.
        if (dirty.current.size) scheduleFlush();
        return;
      }

      revision.current = remote.revision;
      // Server wins except where the user has unflushed local edits.
      const merged = { ...remote.preferences } as Preferences;
      dirty.current.forEach((group) => {
        (merged as Record<string, unknown>)[group] = latest.current[group];
      });
      confirmed.current = remote.preferences;
      latest.current = merged;
      setPreferences((current) => (preferencesEqual(current, merged) ? current : merged));
      await persist(merged, revision.current, Array.from(dirty.current));
      if (dirty.current.size) scheduleFlush();
    })().catch(() => {
      if (!cancelled && mounted.current) {
        setHydrated(true);
        setRefreshing(false);
      }
    });

    return () => {
      cancelled = true;
      mounted.current = false;
      if (flushTimer.current) clearTimeout(flushTimer.current);
      if (retryTimer.current) clearTimeout(retryTimer.current);
    };
  }, [enabled, persist, scheduleFlush, syncPendingState]);

  /* --------------------- Retry when the app returns to foreground ---------- */

  useEffect(() => {
    if (!enabled) return;
    const handler = (state: AppStateStatus) => {
      if (state !== "active") return;
      if (!dirty.current.size) return;
      // Connectivity often changes while backgrounded — retry immediately
      // instead of waiting out the backoff.
      retryDelay.current = 1000;
      if (retryTimer.current) {
        clearTimeout(retryTimer.current);
        retryTimer.current = null;
      }
      void flush();
    };
    const subscription = AppState.addEventListener("change", handler);
    return () => subscription.remove();
  }, [enabled, flush]);

  /* --------------------------------- Actions ------------------------------- */

  const update = useCallback(
    async <K extends PreferenceGroup>(group: K, patch: Partial<Preferences[K]>) => {
      const current = latest.current;
      const nextGroup = { ...current[group], ...patch };
      const next = normalizePreferences({ ...current, [group]: nextGroup });

      if (preferencesEqual(current, next)) return;

      latest.current = next;
      setPreferences(next);
      dirty.current.add(group);
      syncPendingState();
      setError(null);
      setStatus("saving");

      await persist(next, revision.current, Array.from(dirty.current));
      if (enabled) scheduleFlush();
      else setStatus("saved");
    },
    [enabled, persist, scheduleFlush, syncPendingState]
  );

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setRefreshing(true);
    const remote = await fetchRemotePreferences();
    if (!mounted.current) return;
    setRefreshing(false);
    if (!remote) {
      setStatus("offline");
      setError("Couldn't reach PulseSoc. Showing your saved settings.");
      return;
    }
    revision.current = remote.revision;
    const merged = { ...remote.preferences } as Preferences;
    dirty.current.forEach((group) => {
      (merged as Record<string, unknown>)[group] = latest.current[group];
    });
    confirmed.current = remote.preferences;
    latest.current = merged;
    setPreferences(merged);
    setError(null);
    setStatus(dirty.current.size ? "saving" : "idle");
    await persist(merged, revision.current, Array.from(dirty.current));
  }, [enabled, persist]);

  const resetAll = useCallback(async () => {
    latest.current = DEFAULT_PREFERENCES;
    setPreferences(DEFAULT_PREFERENCES);
    (Object.keys(DEFAULT_PREFERENCES) as PreferenceGroup[]).forEach((group) => dirty.current.add(group));
    syncPendingState();
    setStatus("saving");
    setError(null);
    await persist(DEFAULT_PREFERENCES, revision.current, Array.from(dirty.current));
    if (enabled) scheduleFlush();
  }, [enabled, persist, scheduleFlush, syncPendingState]);

  const clearError = useCallback(() => {
    setError(null);
    setStatus((current) => (current === "error" || current === "offline" ? "idle" : current));
  }, []);

  const value = useMemo<PreferencesContextValue>(
    () => ({
      preferences,
      hydrated,
      refreshing,
      status,
      error,
      pendingGroups,
      update,
      refresh,
      resetAll,
      clearError
    }),
    [preferences, hydrated, refreshing, status, error, pendingGroups, update, refresh, resetAll, clearError]
  );

  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function usePreferences(): PreferencesContextValue {
  const context = useContext(PreferencesContext);
  if (!context) {
    throw new Error("usePreferences must be used inside a <PreferencesProvider>.");
  }
  return context;
}

/**
 * Subscribe to a single group. Components using this only re-render when their
 * own group changes, which keeps a switch in Notifications from re-rendering
 * the Appearance screen.
 */
export function usePreferenceGroup<K extends PreferenceGroup>(group: K) {
  const { preferences, update, pendingGroups, status, error } = usePreferences();
  const value = preferences[group];
  const setGroup = useCallback((patch: Partial<Preferences[K]>) => update(group, patch), [group, update]);
  const pending = pendingGroups.includes(group);
  return useMemo(
    () => ({ value, setGroup, pending, status, error }),
    [value, setGroup, pending, status, error]
  );
}

export const __testing = { CACHE_KEY, COALESCE_MS, normalizeSnapshot };
