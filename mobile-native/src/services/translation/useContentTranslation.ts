/**
 * The per-item half of Stage 3: one content item's translation lifecycle.
 *
 * Named `useContentTranslation` rather than the brief's `useTranslation`
 * because `useTranslation` in this app is the i18n hook for interface strings,
 * and `ContentTranslation` needs both in the same file. Shadowing the
 * localization hook with a content-translation hook would be a name that reads
 * correctly and does the wrong thing.
 *
 * Two invariants carry the weight here.
 *
 * Identity: every call gets a fresh request id derived from the content id, and
 * a result is applied only if its id is still the one this hook is waiting for.
 * A recycled feed cell therefore cannot display the previous item's
 * translation, which is the failure Stage 7 calls out — and which no amount of
 * correctness in the router can prevent, because the router cannot know the
 * cell moved.
 *
 * Cost: nothing here ever starts work on its own. `translate` is called from a
 * press handler or from an explicit "always translate" preference, and
 * `userInitiated` is passed through rather than assumed, because that flag is
 * what decides whether the request may escalate to a billable provider at all.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { TranslatableContentType } from "../../api/translation";
import { digestText } from "./cache";
import { cancelTranslationRequests, translateText } from "./router";
import type { TranslationFailure, TranslationProviderId } from "./types";

export type ContentTranslationStatus = "idle" | "translating" | "translated" | "failed";

export type UseContentTranslationOptions = {
  contentType: TranslatableContentType;
  /** Stable per item, e.g. `post:1234`. Not the array index. */
  contentId: string;
  /** Bumped when the item is edited, so an edit cannot serve a stale answer. */
  contentVersion?: string | null;
  text: string;
  sourceLanguage?: string | null;
  targetLanguage: string;
};

export type TranslateOptions = {
  /** False for the automatic "always translate" path. Defaults to true. */
  userInitiated?: boolean;
  /** True only from an explicit press on the download affordance. */
  allowDownload?: boolean;
};

export function useContentTranslation({
  contentType,
  contentId,
  contentVersion,
  text,
  sourceLanguage,
  targetLanguage
}: UseContentTranslationOptions) {
  const [status, setStatus] = useState<ContentTranslationStatus>("idle");
  const [translatedText, setTranslatedText] = useState("");
  const [provider, setProvider] = useState<TranslationProviderId | null>(null);
  const [detectedSourceLanguage, setDetectedSourceLanguage] = useState<string | null>(null);
  const [failure, setFailure] = useState<TranslationFailure | null>(null);

  /**
   * The request this hook is currently prepared to accept an answer for.
   * A ref rather than state because it is read inside an async continuation:
   * state there would be the value captured at call time, which is exactly the
   * stale value the check exists to detect.
   */
  const pendingRequestId = useRef<string | null>(null);
  const sequence = useRef(0);

  // The text digest is here as well as the version, not instead of it. A caller
  // that tracks versions gets an invalidation the moment the version moves, and
  // a caller that does not still cannot show a translation of words that are no
  // longer on screen — which is the case for every call site today, since the
  // feed has no edit counter.
  const identity = `${contentId}|${contentVersion ?? "-"}|${digestText(text)}|${targetLanguage}`;

  // Abandon anything in flight when the item, its version or the target
  // language changes, and clear the visible translation with it. Cancelling is
  // what stops a scrolled-away cell from finishing work nobody will see; the
  // reset is what stops the previous item's text from being briefly shown under
  // the new item's content.
  useEffect(() => {
    const abandoned = pendingRequestId.current;
    pendingRequestId.current = null;
    setStatus("idle");
    setTranslatedText("");
    setProvider(null);
    setDetectedSourceLanguage(null);
    setFailure(null);
    if (abandoned) void cancelTranslationRequests([abandoned], "content_changed");
  }, [identity]);

  useEffect(
    () => () => {
      const abandoned = pendingRequestId.current;
      pendingRequestId.current = null;
      if (abandoned) void cancelTranslationRequests([abandoned], "unmounted");
    },
    []
  );

  const translate = useCallback(
    async (options: TranslateOptions = {}) => {
      if (!text.trim()) return;
      sequence.current += 1;
      const requestId = `${contentId}#${sequence.current}`;
      pendingRequestId.current = requestId;
      setStatus("translating");
      setFailure(null);

      const outcome = await translateText({
        requestId,
        contentId,
        contentType,
        contentVersion: contentVersion ?? null,
        text,
        sourceLanguage: sourceLanguage ?? null,
        targetLanguage,
        allowDownload: options.allowDownload === true,
        userInitiated: options.userInitiated !== false
      });

      // One check covers both recycling and unmount: every cleanup path above
      // nulls `pendingRequestId` before it cancels, so an answer that outlives
      // the question it was asked for can never match. A separate `mounted`
      // flag would read as defensive and be unreachable.
      if (pendingRequestId.current !== requestId) return;
      pendingRequestId.current = null;

      if (outcome.ok) {
        setTranslatedText(outcome.translatedText);
        setProvider(outcome.provider);
        setDetectedSourceLanguage(outcome.sourceLanguage);
        setStatus("translated");
        return;
      }

      setFailure(outcome);
      setStatus("failed");
    },
    [contentId, contentType, contentVersion, sourceLanguage, targetLanguage, text]
  );

  const showOriginal = useCallback(() => {
    // Keeps the translation so that switching back does not re-translate, which
    // on the cloud path would be a second billable request for text the user
    // has already seen translated.
    setStatus(previous => (previous === "translated" ? "idle" : previous));
  }, []);

  const showTranslation = useCallback(() => {
    setStatus(previous => (previous === "idle" && translatedText ? "translated" : previous));
  }, [translatedText]);

  return {
    status,
    translatedText,
    provider,
    detectedSourceLanguage,
    failure,
    hasTranslation: translatedText.length > 0,
    translate,
    showOriginal,
    showTranslation
  };
}
