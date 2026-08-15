import { useSyncExternalStore } from "react";

/**
 * Tiny external store for the Spatial Create Console's open state.
 *
 * Lives outside React because two separated trees need it: the tab bar's
 * Create button (which morphs + → ×) and the console overlay mounted above
 * the tab scenes. No persistence — the console always starts closed.
 */
let open = false;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function openCreateConsole() {
  if (open) return;
  open = true;
  emit();
}

export function closeCreateConsole() {
  if (!open) return;
  open = false;
  emit();
}

export function toggleCreateConsole() {
  open = !open;
  emit();
}

export function isCreateConsoleOpen() {
  return open;
}

export function useCreateConsoleOpen(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => open,
    () => open
  );
}
