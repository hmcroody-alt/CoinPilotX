/**
 * One save/unsave operation for every kind of savable content.
 *
 * Before this module each surface owned its own idea of what saving meant.
 * Posts POSTed `/posts/{id}/save` and read `saved`; Reels POSTed
 * `/reels/{id}/save` and read `saved` from a payload the server derived from a
 * different table than the one that route wrote, so a saved Reel reverted on
 * the next fetch; Marketplace POSTed `/marketplace/listings/save`, got no state
 * back at all, and answered the "is it saved" question by disabling its own
 * button forever; Status had no client path whatsoever. Four spellings of one
 * verb, three of them wrong in a different way.
 *
 * The contract here is deliberately narrow: state the id, state the state you
 * want, get back the state the server now holds. It is an assertion, not a
 * toggle, so a retry after a dropped response confirms rather than reverses —
 * the property a Save button needs in order to survive a double tap or a flaky
 * connection.
 */

import { pulseApi } from "../api/pulseApi";

export type SavableContentType = "post" | "reel" | "status" | "marketplace";

/**
 * Everything the server needs to save a piece of content, and nothing it does
 * not. `title`/`previewText`/`sourceUrl` are only consulted for content types
 * that go through the generic library route, which stores a snapshot so the
 * Saved screen has something to render for content that later disappears.
 */
export type SaveTarget = {
  type: SavableContentType;
  id: number | string;
  title?: string;
  previewText?: string;
  sourceUrl?: string;
  thumbnailUrl?: string;
};

export type SaveResult = {
  saved: boolean;
  /** False when the server was already in the requested state. */
  changed: boolean;
  message?: string;
};

type SaveResponse = {
  ok?: boolean;
  saved?: boolean;
  is_saved?: boolean;
  changed?: boolean;
  message?: string;
};

/**
 * The key every cache, store, and test uses to identify one saved thing.
 *
 * Content ids are only unique within a type — post 12 and reel 12 are different
 * content — so the type is part of the identity rather than something the
 * caller is trusted to keep track of separately.
 */
export function saveKey(type: SavableContentType, id: number | string): string {
  return `${type}:${id}`;
}

/**
 * Recover a save target from a PulseSoc content URL.
 *
 * Some surfaces — activity rows, notifications, deep links — carry a
 * destination and nothing else: no content payload, no id field, no saved flag.
 * The URL shapes below are the ones the backend actually emits (see the search
 * and notification routes), so parsing them is reading a contract rather than
 * guessing. Anything unrecognised returns null, and the caller renders no Save
 * button at all, which is the correct outcome for a row that points at a
 * profile or a conversation.
 */
export function saveTargetFromUrl(url?: string | null): { type: SavableContentType; id: number } | null {
  const path = String(url || "").trim();
  if (!path) return null;
  const patterns: Array<[SavableContentType, RegExp]> = [
    ["reel", /\/pulse\/reels?\/(\d+)/i],
    ["post", /\/pulse\/post\/(\d+)/i],
    ["status", /[?&]status(?:_id)?=(\d+)/i],
    ["marketplace", /[?&]listing(?:_id)?=(\d+)/i]
  ];
  for (const [type, pattern] of patterns) {
    const match = path.match(pattern);
    const id = match ? Number(match[1]) : 0;
    if (id > 0) return { type, id };
  }
  return null;
}

function readResult(response: SaveResponse, requested: boolean): SaveResult {
  // `saved` is the server's word for the state it now holds. When a route is
  // older than this contract and says nothing, the request succeeded, so the
  // requested state is the state — but we never invent a `changed` of true.
  const saved = typeof response.saved === "boolean"
    ? response.saved
    : typeof response.is_saved === "boolean"
      ? response.is_saved
      : requested;
  return {
    saved,
    changed: typeof response.changed === "boolean" ? response.changed : saved === requested,
    message: response.message
  };
}

/**
 * Assert a saved state on the server. Safe to repeat: asking for a state the
 * server already holds succeeds and reports `changed: false`.
 */
export async function setSavedOnServer(target: SaveTarget, saved: boolean): Promise<SaveResult> {
  const id = target.id;
  switch (target.type) {
    case "post": {
      const response = await pulseApi<SaveResponse>(`/api/pulse/posts/${id}/save`, {
        method: "POST",
        body: JSON.stringify({ post_id: id, saved })
      });
      return readResult(response, saved);
    }
    case "reel": {
      const response = await pulseApi<SaveResponse>(`/api/pulse/reels/${id}/save`, {
        method: "POST",
        body: JSON.stringify({ reel_id: id, saved })
      });
      return readResult(response, saved);
    }
    case "marketplace": {
      const response = await pulseApi<SaveResponse>("/api/pulse/marketplace/listings/save", {
        method: "POST",
        body: JSON.stringify({ listing_id: id, saved })
      });
      return readResult(response, saved);
    }
    case "status": {
      // Status has no route of its own; it lives in the generic library, which
      // is also where the web client puts it. Sending the snapshot fields means
      // the Saved screen can render the Status after it expires, which is the
      // usual reason someone saves one.
      const response = await pulseApi<SaveResponse>("/api/pulse/saved", {
        method: "POST",
        body: JSON.stringify({
          content_type: "status",
          content_id: String(id),
          saved,
          title: target.title || "PulseSoc Status",
          preview_text: target.previewText || "",
          thumbnail_url: target.thumbnailUrl || "",
          source_url: target.sourceUrl || `/pulse/status?status=${id}`
        })
      });
      return readResult(response, saved);
    }
    default: {
      // Exhaustiveness: adding a content type without adding its route becomes a
      // compile error rather than a button that silently does nothing.
      const unreachable: never = target.type;
      throw new Error(`No save route for content type ${String(unreachable)}`);
    }
  }
}
