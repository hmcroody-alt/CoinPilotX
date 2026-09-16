import AsyncStorage from "@react-native-async-storage/async-storage";
import { useCallback, useEffect, useState } from "react";
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
 *   2. the PulseSoc Cosmic default
 *
 * A choice is therefore never overwritten — the default only fills the gap
 * where there is no choice, or where we do not know it yet.
 */

const CACHE_PREFIX = "pulsesoc.native.messenger.wallpaper.v1";

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
 * Resolve the wallpaper for a conversation.
 *
 * Returns the Cosmic default synchronously on the very first render, which is
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

  useEffect(() => {
    if (!userId || !conversationId) return;
    let live = true;
    const key = cacheKey(userId, conversationId);

    (async () => {
      const cached = await readCachedConversationWallpaper(userId, conversationId);
      if (live && cached) setWallpaper(cached);
      if (!enabled || refreshed.has(key)) return;
      refreshed.add(key);
      try {
        const data = await getConversationControlCenter(conversationId);
        const value = data.settings?.appearance?.wallpaper;
        // An unset or unrecognised value is the "no choice made" case, and the
        // default already on screen is the right answer for it. Only a value we
        // recognise displaces it.
        if (!isChatWallpaperId(value)) return;
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
    setWallpaper(value);
    void rememberConversationWallpaper(userId, conversationId, value);
  }, [conversationId, userId]);

  return { applyWallpaper, wallpaper };
}
