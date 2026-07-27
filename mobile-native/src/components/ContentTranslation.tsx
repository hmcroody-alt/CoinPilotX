import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Modal, Pressable, StyleProp, StyleSheet, Text, TextStyle, View } from "react-native";
import {
  peekTranslationPreference,
  subscribeTranslationPreference,
  TranslatableContentType,
  TranslationPolicy,
  translatePulseContent,
  updateTranslationPreference
} from "../api/translation";
import { useTimeZonePreference } from "../core/TimeZoneContext";
import { colors } from "../theme/colors";

type ContentTranslationProps = {
  contentType: TranslatableContentType;
  contentRef: string | number;
  text: string;
  sourceLanguage?: string;
  textStyle?: StyleProp<TextStyle>;
  numberOfLines?: number;
  renderText?: (text: string, translated: boolean) => ReactNode;
  controlsMode?: "inline" | "compact";
};

const UNKNOWN_LANGUAGE = new Set(["", "auto", "unknown", "und", "undefined", "null"]);
const NON_ENGLISH_HINTS = /\b(mwen|ou|pa|pou|ak|nan|banm|bonjou|merci|hola|gracias|bonjour|salut|ça|oui|non|por|para|que|não|sim| danke| bitte|안녕|你好|مرحبا|नमस्ते)\b/i;
const NON_LATIN_OR_ACCENTED = /[^\u0000-\u007f]/;

function normalizeLanguageTag(language: string) {
  return language.trim().replace("_", "-").toLowerCase();
}

function sameLanguage(left: string, right: string) {
  const a = normalizeLanguageTag(left);
  const b = normalizeLanguageTag(right);
  if (UNKNOWN_LANGUAGE.has(a) || UNKNOWN_LANGUAGE.has(b)) return false;
  return a === b || a.split("-")[0] === b.split("-")[0];
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
  controlsMode = "inline"
}: ContentTranslationProps) {
  const { locale } = useTimeZonePreference();
  const targetLanguage = useMemo(() => locale.replace("_", "-").toLowerCase(), [locale]);
  const [policy, setPolicy] = useState<TranslationPolicy>("ask");
  const [translatedText, setTranslatedText] = useState("");
  const [showTranslated, setShowTranslated] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const busyRef = useRef(false);
  const requestKey = `${contentType}:${contentRef}:${targetLanguage}:${text}`;
  const activeRequest = useRef(requestKey);
  activeRequest.current = requestKey;

  const requestTranslation = useCallback(
    async (force = false) => {
      if (!text.trim() || busyRef.current) return;
      const expectedKey = requestKey;
      busyRef.current = true;
      setBusy(true);
      setError("");
      try {
        const result = await translatePulseContent({
          contentType,
          contentRef,
          text,
          sourceLanguage,
          targetLanguage,
          force
        });
        if (activeRequest.current !== expectedKey) return;
        if (result.translated_text) {
          setTranslatedText(result.translated_text);
          setShowTranslated(true);
        } else if (result.reason === "same_language") {
          setShowTranslated(false);
        } else if (result.reason === "never_translate") {
          setShowTranslated(false);
          setPolicy("never");
        }
      } catch (requestError) {
        if (activeRequest.current === expectedKey) {
          setError(requestError instanceof Error ? requestError.message : "Translation failed. Try again.");
        }
      } finally {
        busyRef.current = false;
        if (activeRequest.current === expectedKey) setBusy(false);
      }
    },
    [contentRef, contentType, requestKey, sourceLanguage, targetLanguage, text]
  );

  useEffect(() => {
    setTranslatedText("");
    setShowTranslated(false);
    setError("");
    const applyPreference = (preference: { policy: TranslationPolicy }) => {
      if (activeRequest.current !== requestKey) return;
      setPolicy(preference.policy);
      if (preference.policy === "always") requestTranslation(false);
    };
    const cached = peekTranslationPreference(sourceLanguage, targetLanguage);
    if (cached) applyPreference(cached);
    else setPolicy("ask");
    return subscribeTranslationPreference(sourceLanguage, targetLanguage, applyPreference);
  }, [requestKey, requestTranslation, sourceLanguage, targetLanguage]);

  const changePolicy = useCallback(
    async (nextPolicy: TranslationPolicy) => {
      const previous = policy;
      setPolicy(nextPolicy);
      setError("");
      if (nextPolicy === "never") setShowTranslated(false);
      try {
        const saved = await updateTranslationPreference(sourceLanguage, targetLanguage, nextPolicy);
        setPolicy(saved.policy);
        if (saved.policy === "always") await requestTranslation(true);
      } catch (preferenceError) {
        setPolicy(previous);
        setError(preferenceError instanceof Error ? preferenceError.message : "Could not save translation preference.");
      }
    },
    [policy, requestTranslation, sourceLanguage, targetLanguage]
  );

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
          <View style={styles.compactRow} accessibilityLabel={`Translation. Target language ${targetLanguage}.`}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={showTranslated ? "Translation options. Showing translated text." : `Translate message to ${targetLanguage}`}
              disabled={busy}
              onPress={(event) => {
                event?.stopPropagation?.();
                setShowOptions(true);
              }}
              style={({ pressed }) => [styles.compactControl, pressed && styles.pressed, busy && styles.disabled]}
            >
              {busy ? <ActivityIndicator size="small" color={colors.accent} /> : <Text style={styles.globe}>🌐</Text>}
              <Text style={styles.compactControlText}>{showTranslated ? "Original / Translate" : "Translate"}</Text>
            </Pressable>
            {showTranslated ? <Text style={styles.machineLabel}>Translated · {targetLanguage.toUpperCase()}</Text> : null}
          </View>
        ) : (
          <View style={styles.controls} accessibilityLabel={`Translation controls. Target language ${targetLanguage}.`}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={showTranslated ? "Show original text" : `Translate to ${targetLanguage}`}
              disabled={busy}
              onPress={(event) => {
                event?.stopPropagation?.();
                if (showTranslated) setShowTranslated(false);
                else if (translatedText) setShowTranslated(true);
                else requestTranslation(true);
              }}
              style={({ pressed }) => [styles.control, pressed && styles.pressed, busy && styles.disabled]}
            >
              {busy ? <ActivityIndicator size="small" color={colors.accent} /> : null}
              <Text style={styles.controlText}>{showTranslated ? "Show original" : "Translate"}</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Always translate to ${targetLanguage}`}
              accessibilityState={{ selected: policy === "always" }}
              onPress={(event) => {
                event?.stopPropagation?.();
                changePolicy(policy === "always" ? "ask" : "always");
              }}
              style={({ pressed }) => [styles.control, policy === "always" && styles.selected, pressed && styles.pressed]}
            >
              <Text style={[styles.controlText, policy === "always" && styles.selectedText]}>Always</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Never translate to ${targetLanguage}`}
              accessibilityState={{ selected: policy === "never" }}
              onPress={(event) => {
                event?.stopPropagation?.();
                changePolicy(policy === "never" ? "ask" : "never");
              }}
              style={({ pressed }) => [styles.control, policy === "never" && styles.selected, pressed && styles.pressed]}
            >
              <Text style={[styles.controlText, policy === "never" && styles.selectedText]}>Never</Text>
            </Pressable>
            {showTranslated ? <Text style={styles.machineLabel}>Translated · {targetLanguage.toUpperCase()}</Text> : null}
          </View>
        )
      ) : null}
      <Modal animationType="fade" transparent visible={showOptions} onRequestClose={() => setShowOptions(false)}>
        <Pressable accessibilityRole="button" accessibilityLabel="Close translation options" style={styles.sheetScrim} onPress={() => setShowOptions(false)}>
          <Pressable accessibilityRole="menu" style={styles.sheet} onPress={(event) => event?.stopPropagation?.()}>
            <Text style={styles.sheetEyebrow}>Translation</Text>
            <Text style={styles.sheetTitle}>Message language options</Text>
            <Pressable
              accessibilityRole="menuitem"
              accessibilityLabel={showTranslated ? "Show original message" : `Translate message to ${targetLanguage}`}
              disabled={busy}
              onPress={() => {
                if (showTranslated) setShowTranslated(false);
                else if (translatedText) setShowTranslated(true);
                else requestTranslation(true);
                setShowOptions(false);
              }}
              style={({ pressed }) => [styles.sheetAction, pressed && styles.pressed]}
            >
              <Text style={styles.sheetActionTitle}>{showTranslated ? "Show original" : "Translate now"}</Text>
              <Text style={styles.sheetActionSubtitle}>{showTranslated ? "Return this bubble to the original message." : `Translate this message to ${targetLanguage.toUpperCase()}.`}</Text>
            </Pressable>
            <Pressable
              accessibilityRole="menuitem"
              accessibilityLabel={`Always translate to ${targetLanguage}`}
              accessibilityState={{ selected: policy === "always" }}
              onPress={() => {
                void changePolicy(policy === "always" ? "ask" : "always");
                setShowOptions(false);
              }}
              style={({ pressed }) => [styles.sheetAction, policy === "always" && styles.sheetActionSelected, pressed && styles.pressed]}
            >
              <Text style={styles.sheetActionTitle}>Always translate</Text>
              <Text style={styles.sheetActionSubtitle}>Automatically translate this language when PulseSoc can detect it.</Text>
            </Pressable>
            <Pressable
              accessibilityRole="menuitem"
              accessibilityLabel={`Never translate to ${targetLanguage}`}
              accessibilityState={{ selected: policy === "never" }}
              onPress={() => {
                void changePolicy(policy === "never" ? "ask" : "never");
                setShowOptions(false);
              }}
              style={({ pressed }) => [styles.sheetAction, policy === "never" && styles.sheetActionSelected, pressed && styles.pressed]}
            >
              <Text style={styles.sheetActionTitle}>Never translate</Text>
              <Text style={styles.sheetActionSubtitle}>Keep this language in its original form.</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
      {error ? <Text accessibilityLiveRegion="polite" style={styles.error}>{error}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
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
  compactControl: {
    minHeight: 28,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "rgba(110,223,246,0.24)",
    backgroundColor: "rgba(110,223,246,0.05)",
    paddingHorizontal: 8,
    flexDirection: "row",
    alignItems: "center",
    gap: 4
  },
  compactControlText: {
    color: colors.muted,
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
  error: {
    marginTop: 4,
    color: colors.danger,
    fontSize: 11
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
});
