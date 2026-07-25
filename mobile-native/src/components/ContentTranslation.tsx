import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleProp, StyleSheet, Text, TextStyle, View } from "react-native";
import {
  getTranslationPreference,
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
};

export function ContentTranslation({
  contentType,
  contentRef,
  text,
  sourceLanguage = "auto",
  textStyle,
  numberOfLines,
  renderText
}: ContentTranslationProps) {
  const { locale } = useTimeZonePreference();
  const targetLanguage = useMemo(() => locale.replace("_", "-").toLowerCase(), [locale]);
  const [policy, setPolicy] = useState<TranslationPolicy>("ask");
  const [translatedText, setTranslatedText] = useState("");
  const [showTranslated, setShowTranslated] = useState(false);
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
    let mounted = true;
    setTranslatedText("");
    setShowTranslated(false);
    setError("");
    getTranslationPreference(sourceLanguage, targetLanguage)
      .then((preference) => {
        if (!mounted || activeRequest.current !== requestKey) return;
        setPolicy(preference.policy);
        if (preference.policy === "always") requestTranslation(false);
      })
      .catch(() => {
        if (mounted) setPolicy("ask");
      });
    return () => {
      mounted = false;
    };
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
  pressed: {
    opacity: 0.72
  },
  disabled: {
    opacity: 0.58
  }
});
