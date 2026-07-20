import { ComposerDraftInput } from "./draftToContentModel";

/**
 * Result of publishing from the preview screen.
 * - `ok`: publish succeeded; the screen should dismiss.
 * - failure: the screen stays open, surfaces `message`, and preserves the
 *   draft so the user can retry or return to edit.
 */
export type PreviewPublishResult = { ok: true; message?: string } | { ok: false; message: string };

export type PreviewHandoff = {
  /** Canonical draft input used to render the preview. */
  draft: ComposerDraftInput;
  /**
   * Executes the real publish through the composer's existing, duplicate-safe
   * publish path. The preview screen never talks to the API directly — it
   * delegates here so guardrails (dup guard, draft preservation, no false
   * success) live in one place.
   */
  publish: () => Promise<PreviewPublishResult>;
};

/**
 * In-memory handoff store. Unlike the AsyncStorage camera handoff, this must
 * carry a live `publish` closure across the navigation boundary, so it cannot
 * be serialized. Both the composer and the full-screen preview screen run in
 * the same JS runtime, so a module-level slot keyed by a nonce is safe and
 * avoids leaking the callback anywhere persistent.
 */
const store = new Map<string, PreviewHandoff>();

export function stashPreviewHandoff(handoff: PreviewHandoff): string {
  const token = `preview-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  store.set(token, handoff);
  return token;
}

export function peekPreviewHandoff(token: string): PreviewHandoff | null {
  return store.get(token) || null;
}

export function clearPreviewHandoff(token: string) {
  store.delete(token);
}
