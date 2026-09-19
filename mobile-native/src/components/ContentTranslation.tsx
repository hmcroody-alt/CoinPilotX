/**
 * The Translate affordance, wherever text appears: feed posts, comments, chat
 * bubbles, marketplace listings, reel captions.
 *
 * This component used to call the billable cloud endpoint in `api/translation`
 * directly, which meant every screen that rendered text was its own
 * cost-control decision. It now states what it wants and lets
 * `services/translation` decide who answers: the device cache, Apple's
 * on-device engine, or the cloud, in that order and only when policy allows.
 * Nothing above this line knows which one did.
 *
 * Two behaviours are worth naming because they are easy to regress.
 *
 * The automatic path is not the manual path. "Always translate" calls
 * `translate({ userInitiated: false })`, and an automatic request is never
 * allowed to escalate to the billable provider. A user who turns on "Always"
 * for a language Apple cannot do gets no translation — deliberately — rather
 * than a bill that scales with how far they scroll.
 *
 * A download is a request, not a side effect. `allowDownload` is passed only
 * from a press on the download control, so no render path can cause Apple to
 * start pulling a language model onto the device.
 */

import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Modal, Pressable, StyleProp, Text, TextStyle, View } from "react-native";
import {
  peekTranslationPreference,
  subscribeTranslationPreference,
  TranslatableContentType,
  TranslationPolicy,
  updateTranslationPreference
} from "../api/translation";
import { useTimeZonePreference } from "../core/TimeZoneContext";
import { languageDisplayName, useTranslation } from "../i18n";
import { useContentTranslation } from "../services/translation";
import type { TranslationFailureCode } from "../services/translation";
import { chatGraphite } from "../theme/chatGraphite";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type ContentTranslationProps = {
  contentType: TranslatableContentType;
  contentRef: string | number;
  text: string;
  sourceLanguage?: string;
  textStyle?: StyleProp<TextStyle>;
  numberOfLines?: number;
  renderText?: (text: string, translated: boolean) => ReactNode;
  controlsMode?: "inline" | "compact";
  /**
   * Bumped by a surface outside this component -- the messenger's long-press
   * menu -- to ask for the same toggle the inline control performs.
   *
   * A number rather than a boolean because the request is an event, not a
   * state: someone may translate, show the original, then translate again,
   * and a boolean that is already `true` cannot express the second ask.
   *
   * It routes through `toggleTranslation`, the identical path the inline
   * button uses, so the menu cannot acquire its own translation behaviour --
   * including its own idea of what counts as user-initiated, which is what
   * decides whether a request lands on the billable path.
   */
  translateRequestId?: number;
};

const UNKNOWN_LANGUAGE = new Set(["", "auto", "unknown", "und", "undefined", "null"]);
const NON_ENGLISH_HINTS = /\b(mwen|ou|pa|pou|ak|nan|banm|bonjou|merci|hola|gracias|bonjour|salut|ça|oui|non|por|para|que|não|sim| danke| bitte|안녕|你好|مرحبا|नमस्ते)\b/i;
const NON_LATIN_OR_ACCENTED = /[^\u0000-\u007f]/;

/**
 * Failures the user asked for, or that describe the text rather than a fault.
 *
 * Rendering "Translation unavailable" after someone dismissed Apple's download
 * sheet would report their own decision back to them as an error, and
 * `same_language` means the text is already readable — an error row under text
 * that needs no translation is noise that teaches people to ignore the row.
 */
const SILENT_FAILURES = new Set<TranslationFailureCode>([
  "same_language",
  "request_canceled",
  "download_canceled"
]);

/**
 * The five router/native codes that have something specific and useful to say.
 * Everything else — a tripped breaker, an exhausted budget, a disabled flag, a
 * provider fault — collapses to the generic label on purpose: those are facts
 * about the deployment, and Stage 9 forbids putting operator diagnostics in
 * front of a user. The distinction stays visible in metrics, where it belongs.
 */
const FAILURE_MESSAGE_KEY: Partial<Record<TranslationFailureCode, string>> = {
  unsupported_language_pair: "translation:failure.unsupportedLanguage",
  unsupported_os_version: "translation:failure.unsupportedLanguage",
  invalid_language: "translation:failure.unsupportedLanguage",
  download_failed: "translation:failure.downloadFailed",
  offline_model_unavailable: "translation:failure.offline"
};

function normalizeLanguageTag(language: string) {
  return language.trim().replace("_", "-").toLowerCase();
}

function sameLanguage(left: string, right: string) {
  const a = normalizeLanguageTag(left);
  const b = normalizeLanguageTag(right);
  if (UNKNOWN_LANGUAGE.has(a) || UNKNOWN_LANGUAGE.has(b)) return false;
  return a === b || a.split("-")[0] === b.split("-")[0];
}

/**
 * Whether this text is worth offering to translate at all.
 *
 * Exported because the messenger's long-press menu has to answer the same
 * question one level up -- it decides whether to *list* Translate before this
 * component decides whether to *draw* its own control. Two answers to one
 * question is how a menu ends up offering Translate on a message whose bubble
 * shows no globe, so there is one function and both callers ask it.
 *
 * `compact` is part of the question rather than a rendering detail: chat
 * bubbles are short and unlabelled, so an unknown source language is only
 * treated as translatable there when the text itself looks foreign. Callers
 * asking on behalf of a chat bubble must pass `true`.
 */
export function offersTranslation(text: string, sourceLanguage: string, targetLanguage: string, compact: boolean) {
  return shouldOfferTranslation(text, sourceLanguage, targetLanguage, compact);
}

function shouldOfferTranslation(text: string, sourceLanguage: string, targetLanguage: string, compact: boolean) {
  if (!text.trim()) return false;
  const source = normalizeLanguageTag(sourceLanguage);
  if (!UNKNOWN_LANGUAGE.has(source)) return !sameLanguage(source, targetLanguage);
  if (!compact) return true;
  return NON_LATIN_OR_ACCENTED.test(text) || NON_ENGLISH_HINTS.test(text);
}

export function ContentTranslation({
  contentType,
  contentRef,
  text,
  sourceLanguage = "auto",
  textStyle,
  numberOfLines,
  renderText,
  controlsMode = "inline",
  translateRequestId
}: ContentTranslationProps) {
  const { t } = useTranslation();
  const { locale } = useTimeZonePreference();
  const targetLanguage = useMemo(() => locale.replace("_", "-").toLowerCase(), [locale]);
  const [policy, setPolicy] = useState<TranslationPolicy>("ask");
  const [showOptions, setShowOptions] = useState(false);
  const [preferenceFailed, setPreferenceFailed] = useState(false);

  const contentId = `${contentType}:${contentRef}`;
  const {
    status,
    translatedText,
    detectedSourceLanguage,
    failure,
    hasTranslation,
    translate,
    showOriginal,
    showTranslation
  } = useContentTranslation({
    contentType,
    contentId,
    text,
    sourceLanguage,
    targetLanguage
  });

  const busy = status === "translating";
  const showTranslated = status === "translated";

  useEffect(() => {
    setPreferenceFailed(false);
    const applyPreference = (preference: { policy: TranslationPolicy }) => {
      setPolicy(preference.policy);
      // The only automatic request in the app. `userInitiated: false` is what
      // keeps it off the billable path — see the header.
      if (preference.policy === "always") void translate({ userInitiated: false });
    };
    const cached = peekTranslationPreference(sourceLanguage, targetLanguage);
    if (cached) applyPreference(cached);
    else setPolicy("ask");
    return subscribeTranslationPreference(sourceLanguage, targetLanguage, applyPreference);
  }, [sourceLanguage, targetLanguage, translate]);

  const changePolicy = useCallback(
    async (nextPolicy: TranslationPolicy) => {
      const previous = policy;
      setPolicy(nextPolicy);
      setPreferenceFailed(false);
      if (nextPolicy === "never") showOriginal();
      try {
        const saved = await updateTranslationPreference(sourceLanguage, targetLanguage, nextPolicy);
        setPolicy(saved.policy);
        if (saved.policy === "always") await translate({ userInitiated: false });
      } catch {
        // The thrown value is a network or server error and may carry a URL or
        // a stack. Only the fact that the save failed reaches the screen.
        setPolicy(previous);
        setPreferenceFailed(true);
      }
    },
    [policy, showOriginal, sourceLanguage, targetLanguage, translate]
  );

  const toggleTranslation = useCallback(() => {
    if (showTranslated) showOriginal();
    else if (hasTranslation) showTranslation();
    else void translate();
  }, [hasTranslation, showOriginal, showTranslated, showTranslation, translate]);

  /**
   * An outside request to translate, replayed through the inline control's own
   * handler.
   *
   * The ref is the point. `toggleTranslation` is re-created whenever the
   * translation state changes -- which it does *as a result of* toggling --
   * so an effect that depended on the callback would translate, observe a new
   * callback, and translate again. Reading it out of a ref means the only
   * thing that can fire this is the id changing, which is the only thing that
   * means "the user asked".
   */
  const toggleRef = useRef(toggleTranslation);
  toggleRef.current = toggleTranslation;
  useEffect(() => {
    // Zero and undefined both mean "nobody has asked yet", so the mount of a
    // bubble that has never been long-pressed does not translate it.
    if (!translateRequestId) return;
    toggleRef.current();
  }, [translateRequestId]);

  const targetName = languageDisplayName(targetLanguage) || targetLanguage;
  const sourceName = detectedSourceLanguage ? languageDisplayName(detectedSourceLanguage) : "";
  const translatedLabel = sourceName
    ? t("translation:translatedFrom", { language: sourceName })
    : t("translation:translatedLabel");

  const visibleFailure = failure && !SILENT_FAILURES.has(failure.code) ? failure : null;
  const offerDownload = visibleFailure?.downloadAvailable === true;
  const offerRetry = visibleFailure?.recoverable === true && !offerDownload;
  const failureMessage = visibleFailure
    ? t(FAILURE_MESSAGE_KEY[visibleFailure.code] ?? "translation:failure.unavailable")
    : preferenceFailed
      ? t("translation:failure.preferenceNotSaved")
      : "";

  const visibleText = showTranslated && translatedText ? translatedText : text;
  const compact = controlsMode === "compact";
  const showTranslationAction = shouldOfferTranslation(text, sourceLanguage, targetLanguage, compact);
  const rendered = renderText ? (
    renderText(visibleText, showTranslated)
  ) : (
    <Text style={textStyle} numberOfLines={numberOfLines}>
      {visibleText}
    </Text>
  );

  return (
    <View style={styles.container}>
      {rendered}
      {showTranslationAction ? (
        compact ? (
          <View style={styles.compactRow} accessibilityLabel={t("translation:a11y.controls", { language: targetName })}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={
                showTranslated
                  ? t("translation:a11y.options")
                  : t("translation:a11y.translateTo", { language: targetName })
              }
              disabled={busy}
              onPress={(event) => {
                event?.stopPropagation?.();
                setShowOptions(true);
              }}
              style={({ pressed }) => [styles.compactControl, pressed && styles.pressed, busy && styles.disabled]}
            >
              {busy ? <ActivityIndicator size="small" color={colors.accent} /> : <Text style={styles.globe}>🌐</Text>}
              <Text style={styles.compactControlText}>
                {busy
                  ? t("translation:translating")
                  : showTranslated
                    ? t("translation:viewOriginal")
                    : t("translation:translate")}
              </Text>
            </Pressable>
            {showTranslated ? <Text style={styles.machineLabel}>{translatedLabel}</Text> : null}
          </View>
        ) : (
          <View style={styles.controls} accessibilityLabel={t("translation:a11y.controls", { language: targetName })}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={
                showTranslated
                  ? t("translation:a11y.showOriginal")
                  : t("translation:a11y.translateTo", { language: targetName })
              }
              disabled={busy}
              onPress={(event) => {
                event?.stopPropagation?.();
                toggleTranslation();
              }}
              style={({ pressed }) => [styles.control, pressed && styles.pressed, busy && styles.disabled]}
            >
              {busy ? <ActivityIndicator size="small" color={colors.accent} /> : null}
              <Text style={styles.controlText}>
                {busy
                  ? t("translation:translating")
                  : showTranslated
                    ? t("translation:viewOriginal")
                    : t("translation:translate")}
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={t("translation:a11y.alwaysTo", { language: targetName })}
              accessibilityState={{ selected: policy === "always" }}
              onPress={(event) => {
                event?.stopPropagation?.();
                void changePolicy(policy === "always" ? "ask" : "always");
              }}
              style={({ pressed }) => [styles.control, policy === "always" && styles.selected, pressed && styles.pressed]}
            >
              <Text style={[styles.controlText, policy === "always" && styles.selectedText]}>
                {t("translation:always")}
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={t("translation:a11y.neverTo", { language: targetName })}
              accessibilityState={{ selected: policy === "never" }}
              onPress={(event) => {
                event?.stopPropagation?.();
                void changePolicy(policy === "never" ? "ask" : "never");
              }}
              style={({ pressed }) => [styles.control, policy === "never" && styles.selected, pressed && styles.pressed]}
            >
              <Text style={[styles.controlText, policy === "never" && styles.selectedText]}>
                {t("translation:never")}
              </Text>
            </Pressable>
            {showTranslated ? <Text style={styles.machineLabel}>{translatedLabel}</Text> : null}
          </View>
        )
      ) : null}
      <Modal animationType="fade" transparent visible={showOptions} onRequestClose={() => setShowOptions(false)}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("translation:a11y.closeOptions")}
          style={styles.sheetScrim}
          onPress={() => setShowOptions(false)}
        >
          <Pressable accessibilityRole="menu" style={styles.sheet} onPress={(event) => event?.stopPropagation?.()}>
            <Text style={styles.sheetEyebrow}>{t("translation:eyebrow")}</Text>
            <Text style={styles.sheetTitle}>{t("translation:optionsTitle")}</Text>
            <Pressable
              accessibilityRole="menuitem"
              accessibilityLabel={
                showTranslated
                  ? t("translation:a11y.showOriginal")
                  : t("translation:a11y.translateTo", { language: targetName })
              }
              disabled={busy}
              onPress={() => {
                toggleTranslation();
                setShowOptions(false);
              }}
              style={({ pressed }) => [styles.sheetAction, pressed && styles.pressed]}
            >
              <Text style={styles.sheetActionTitle}>
                {showTranslated ? t("translation:viewOriginal") : t("translation:translateNow")}
              </Text>
              <Text style={styles.sheetActionSubtitle}>
                {showTranslated
                  ? t("translation:showOriginalHint")
                  : t("translation:translateNowHint", { language: targetName })}
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="menuitem"
              accessibilityLabel={t("translation:a11y.alwaysTo", { language: targetName })}
              accessibilityState={{ selected: policy === "always" }}
              onPress={() => {
                void changePolicy(policy === "always" ? "ask" : "always");
                setShowOptions(false);
              }}
              style={({ pressed }) => [styles.sheetAction, policy === "always" && styles.sheetActionSelected, pressed && styles.pressed]}
            >
              <Text style={styles.sheetActionTitle}>{t("translation:alwaysTranslate")}</Text>
              <Text style={styles.sheetActionSubtitle}>{t("translation:alwaysTranslateHint")}</Text>
            </Pressable>
            <Pressable
              accessibilityRole="menuitem"
              accessibilityLabel={t("translation:a11y.neverTo", { language: targetName })}
              accessibilityState={{ selected: policy === "never" }}
              onPress={() => {
                void changePolicy(policy === "never" ? "ask" : "never");
                setShowOptions(false);
              }}
              style={({ pressed }) => [styles.sheetAction, policy === "never" && styles.sheetActionSelected, pressed && styles.pressed]}
            >
              <Text style={styles.sheetActionTitle}>{t("translation:neverTranslate")}</Text>
              <Text style={styles.sheetActionSubtitle}>{t("translation:neverTranslateHint")}</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
      {offerDownload ? (
        <View style={styles.downloadRow} accessibilityLiveRegion="polite">
          {/* Named before it happens, because the alternative is Apple's own
              sheet appearing over the feed with no explanation of who asked. */}
          <Text style={styles.downloadExplainer}>{t("translation:downloadExplainer")}</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("translation:a11y.download", { language: targetName })}
            disabled={busy}
            onPress={(event) => {
              event?.stopPropagation?.();
              void translate({ allowDownload: true });
            }}
            style={({ pressed }) => [styles.retryControl, pressed && styles.pressed, busy && styles.disabled]}
          >
            <Text style={styles.retryText}>{t("translation:downloadLanguage")}</Text>
          </Pressable>
        </View>
      ) : failureMessage ? (
        <View style={styles.errorRow} accessibilityLiveRegion="polite">
          <Text style={styles.error}>{failureMessage}</Text>
          {offerRetry ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={t("translation:a11y.retry")}
              disabled={busy}
              onPress={(event) => {
                event?.stopPropagation?.();
                void translate();
              }}
              style={({ pressed }) => [styles.retryControl, pressed && styles.pressed, busy && styles.disabled]}
            >
              <Text style={styles.retryText}>{t("translation:tryAgain")}</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const styles = createThemedStyles(() => ({
  container: {
    minWidth: 0
  },
  controls: {
    marginTop: 5,
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 6
  },
  compactRow: {
    marginTop: 4,
    flexDirection: "row",
    alignItems: "center",
    gap: 6
  },
  // `controlsMode="compact"` has exactly one call site — the chat bubble in
  // ChatScreen — so these two rules are conversation-scoped even though the rest
  // of this component is shared with Reels, Marketplace and the feed.
  //
  // The fill moves to the graphite bubble-inset token because the old cyan wash
  // was tuned against a near-black bubble: on the new `#505761` incoming bubble
  // it composites to a field where `colors.muted` measures 4.04:1, and the label
  // is 10px, so 4.5:1 is the bar. Against the inset token `secondaryText`
  // measures 6.28:1.
  //
  // The cyan edge stays. It is the established brand language for a control, and
  // no value in the graphite palette gives this pill a 3:1 boundary; what
  // identifies it is its own visible "Translate" label, not its outline.
  compactControl: {
    minHeight: 28,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "rgba(110,223,246,0.24)",
    backgroundColor: chatGraphite.insetSurface,
    paddingHorizontal: 8,
    flexDirection: "row",
    alignItems: "center",
    gap: 4
  },
  compactControlText: {
    color: chatGraphite.secondaryText,
    fontSize: 10,
    fontWeight: "800"
  },
  globe: {
    fontSize: 12
  },
  control: {
    minHeight: 30,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "rgba(110,223,246,0.24)",
    backgroundColor: "rgba(110,223,246,0.06)",
    paddingHorizontal: 9,
    flexDirection: "row",
    alignItems: "center",
    gap: 5
  },
  selected: {
    borderColor: colors.accent,
    backgroundColor: "rgba(54,229,143,0.16)"
  },
  controlText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800"
  },
  selectedText: {
    color: colors.accent
  },
  machineLabel: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "700"
  },
  errorRow: {
    marginTop: 4,
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    gap: 8
  },
  downloadRow: {
    marginTop: 4,
    gap: 6,
    alignItems: "flex-start"
  },
  downloadExplainer: {
    color: colors.muted,
    fontSize: 11,
    lineHeight: 16
  },
  error: {
    color: colors.danger,
    fontSize: 11,
    flexShrink: 1
  },
  retryControl: {
    minHeight: 24,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "rgba(110,223,246,0.24)",
    backgroundColor: "rgba(110,223,246,0.06)",
    paddingHorizontal: 10,
    justifyContent: "center"
  },
  retryText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "800"
  },
  sheetScrim: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(1,6,14,0.54)"
  },
  sheet: {
    borderTopLeftRadius: 26,
    borderTopRightRadius: 26,
    borderWidth: 1,
    borderColor: "rgba(110,223,246,0.2)",
    backgroundColor: "#07111d",
    padding: 18,
    paddingBottom: 28,
    gap: 10
  },
  sheetEyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 1.8,
    textTransform: "uppercase"
  },
  sheetTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    marginBottom: 4
  },
  sheetAction: {
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
    backgroundColor: "rgba(255,255,255,0.04)",
    padding: 14,
    gap: 4
  },
  sheetActionSelected: {
    borderColor: colors.accent,
    backgroundColor: "rgba(54,229,143,0.12)"
  },
  sheetActionTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  sheetActionSubtitle: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  pressed: {
    opacity: 0.72
  },
  disabled: {
    opacity: 0.58
  }
}));
