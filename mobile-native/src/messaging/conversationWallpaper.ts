import AsyncStorage from "@react-native-async-storage/async-storage";
import { useCallback, useEffect, useRef, useState } from "react";
import { getConversationControlCenter } from "../api/messenger";
import { ChatWallpaperId, DEFAULT_CHAT_WALLPAPER, isChatWallpaperId } from "../theme/chatWallpaper";

/**
 * Which wallpaper a conversation draws, for *this* viewer.
 *
 * The preference itself is server-owned and already per-viewer:
 * `comm_v2_conversation_settings` is keyed `(conversation_id, user_id)`, so
 * `appearance.wallpaper` is one person's choice for one thread and is never
 * visible to the other participants. Nothing here writes that preference; the
 * control centre is the only writer. This module only decides what to *draw*,
 * and caches what it learns so the next open of the same thread paints the
 * right background in its first frame instead of a default that then changes.
 *
 * Precedence, which is the whole point:
 *
 *   1. the viewer's explicit choice, once known (cache, then server)
 *   2. the PulseSoc Graphite default
 *
 * A choice is therefore never overwritten — the default only fills the gap
 * where there is no choice, or where we do not know it yet.
 */

/**
 * The default's id is part of the key, which matters when the product default
 * changes again.
 *
 * The server sends a value for every conversation — `_merge_control_settings`
 * layers the stored row over its defaults, so "no choice" arrives as the
 * default rather than as nothing. The client therefore cannot tell a real
 * choice from a gap-filler, and caches both. That is harmless until the default
 * changes: entries written under the old default would then out-rank the new
 * one, and a conversation nobody customised would paint the new default, swap
 * to the stale cached one, then swap back when the server answered. Two visible
 * changes of background on open, for the majority of conversations.
 *
 * Keying on the default orphans those entries instead, so the new default
 * paints in the first frame and stays. Someone with a real choice takes one
 * re-confirmation from the server the first time they open a thread after such
 * a release, and is cached again after it.
 */
const CACHE_PREFIX = `pulsesoc.native.messenger.wallpaper.v1.${DEFAULT_CHAT_WALLPAPER}`;

/**
 * Per viewer, not just per conversation. Two accounts on one device must not
 * inherit each other's background.
 */
function cacheKey(userId: number, conversationId: number) {
  return `${CACHE_PREFIX}.${userId}.${conversationId}`;
}

/**
 * One server read per conversation per app run. Without this the background
 * would cost a request on every single chat open; with it, the first open of a
 * thread confirms the choice and every later open is free.
 */
const refreshed = new Set<string>();

/** Tests and sign-out need to forget what this process has already confirmed. */
export function resetConversationWallpaperRefreshes() {
  refreshed.clear();
}

export async function readCachedConversationWallpaper(userId: number, conversationId: number): Promise<ChatWallpaperId | null> {
  try {
    const raw = await AsyncStorage.getItem(cacheKey(userId, conversationId));
    return isChatWallpaperId(raw) ? raw : null;
  } catch {
    return null;
  }
}

export async function rememberConversationWallpaper(userId: number, conversationId: number, value: unknown): Promise<void> {
  if (!isChatWallpaperId(value)) return;
  try {
    await AsyncStorage.setItem(cacheKey(userId, conversationId), value);
  } catch {
    // A background that has to be re-confirmed next launch is not worth
    // surfacing to anyone.
  }
}

/**
 * Forget a cached choice, for when the server reports there is no longer one.
 *
 * Without this, a choice made here and then cleared somewhere else — the web
 * control centre, another device — would survive indefinitely: the cache paints
 * it, the server's "no choice" answer does not displace it, and nothing else
 * ever writes the key.
 */
export async function forgetConversationWallpaper(userId: number, conversationId: number): Promise<void> {
  try {
    await AsyncStorage.removeItem(cacheKey(userId, conversationId));
  } catch {
    // Same reasoning as above.
  }
}

/**
 * Resolve the wallpaper for a conversation.
 *
 * Returns the Graphite default synchronously on the very first render, which is
 * what guarantees there is no frame of black, white, or flat blue before a
 * background exists. It is then replaced — once — if this viewer turns out to
 * have chosen something else.
 *
 * `enabled` is false for the assistant thread and the local QA fixtures, which
 * have no server-side settings row to read.
 *
 * `applyWallpaper` is for the control centre: it already knows the new value
 * the moment the save returns, so the background can change under the open
 * sheet instead of waiting for the next visit to the thread.
 */
export function useConversationWallpaper(userId: number, conversationId: number, enabled = true) {
  const [wallpaper, setWallpaper] = useState<ChatWallpaperId>(DEFAULT_CHAT_WALLPAPER);
  /**
   * Set once the viewer picks a wallpaper in the control centre during this
   * visit. Both reads below started before that pick and so describe the state
   * it replaced; applying either afterwards would revert the background under
   * the person who just changed it.
   */
  const picked = useRef(false);

  useEffect(() => {
    if (!userId || !conversationId) return;
    let live = true;
    const key = cacheKey(userId, conversationId);
    picked.current = false;

    (async () => {
      const cached = await readCachedConversationWallpaper(userId, conversationId);
      if (live && !picked.current && cached) setWallpaper(cached);
      if (!enabled || refreshed.has(key)) return;
      refreshed.add(key);
      try {
        const data = await getConversationControlCenter(conversationId);
        const value = data.settings?.appearance?.wallpaper;
        if (picked.current) return;
        // The server has now answered, and its answer is authoritative for the
        // preference. Anything it sends that this build cannot draw — the
        // "default" sentinel, an empty value, an id from a later release — means
        // there is no choice to honour, so the default is what belongs on screen
        // and a cached choice has to be dropped rather than left to out-rank it.
        // Doing nothing here would strand a choice that was cleared elsewhere.
        if (!isChatWallpaperId(value)) {
          await forgetConversationWallpaper(userId, conversationId);
          if (live) setWallpaper(DEFAULT_CHAT_WALLPAPER);
          return;
        }
        await rememberConversationWallpaper(userId, conversationId, value);
        if (live) setWallpaper(value);
      } catch {
        // The background is not worth a retry, a spinner, or an error state.
        // Whatever is on screen stays on screen.
        refreshed.delete(key);
      }
    })();

    return () => {
      live = false;
    };
  }, [conversationId, enabled, userId]);

  const applyWallpaper = useCallback((value: unknown) => {
    if (!isChatWallpaperId(value)) return;
    picked.current = true;
    setWallpaper(value);
    void rememberConversationWallpaper(userId, conversationId, value);
  }, [conversationId, userId]);

  return { applyWallpaper, wallpaper };
}
