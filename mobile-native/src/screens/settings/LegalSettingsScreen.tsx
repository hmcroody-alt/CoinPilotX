import { useCallback, useEffect, useState } from "react";
import { Alert, BackHandler, Platform, StyleSheet, Text, View } from "react-native";
import { RouteProp, useRoute } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
import { SettingsHeader, SettingsSection, SettingsShell, animateNextLayout } from "../../settings/components/SettingsShell";
import { SettingsButton, SettingsRow } from "../../settings/components/SettingsControls";
import type { RootStackParamList } from "../../navigation/types";
import { useTheme } from "../../theme/ThemeContext";
import {
  LEGAL_DOCUMENTS,
  LEGAL_DOCUMENT_ORDER,
  isLegalDocumentKey,
  type LegalDocument,
  type LegalDocumentKey
} from "./legalContent";

type LegalRoute = RouteProp<RootStackParamList, "LegalSettings">;

const DOCUMENT_ICONS: Record<LegalDocumentKey, keyof typeof Ionicons.glyphMap> = {
  terms: "document-text-outline",
  privacy: "lock-closed-outline",
  guidelines: "people-outline",
  cookies: "cube-outline",
  licenses: "code-slash-outline"
};

/**
 * Legal.
 *
 * Renders the index of documents, or one document, entirely as native text —
 * the Settings platform ships no WebView, so there is no browser view to fall
 * back to and no `openSupportWebFallback` call anywhere in this file. The
 * canonical published URL is disclosed for legal accuracy, but this surface
 * does not hand off to the browser.
 *
 * Document selection is local state rather than a second navigation entry so a
 * deep link (`{ document: "privacy" }`) and an in-screen tap produce exactly the
 * same view, and so the screen works whether or not the host navigator chooses
 * to register a per-document route.
 */
export function LegalSettingsScreen() {
  const route = useRoute<LegalRoute>();
  const theme = useTheme();

  // Deep links are untrusted input; anything that isn't a known key lands on
  // the index instead of rendering an empty document.
  const initial = isLegalDocumentKey(route.params?.document) ? route.params.document : null;
  const [selected, setSelected] = useState<LegalDocumentKey | null>(initial);

  const document = selected ? LEGAL_DOCUMENTS[selected] : null;

  const showIndex = useCallback(() => {
    animateNextLayout(theme.reduceMotion);
    setSelected(null);
  }, [theme.reduceMotion]);

  const openDocument = useCallback(
    (key: LegalDocumentKey) => {
      animateNextLayout(theme.reduceMotion);
      setSelected(key);
    },
    [theme.reduceMotion]
  );

  // On Android the system back gesture must return to the index before it
  // leaves the screen — otherwise reading a document and pressing back drops
  // the user two levels up, which reads as a lost tap.
  useEffect(() => {
    if (Platform.OS !== "android" || !selected) return;
    const subscription = BackHandler.addEventListener("hardwareBackPress", () => {
      showIndex();
      return true;
    });
    return () => subscription.remove();
  }, [selected, showIndex]);

  const showCanonical = useCallback((target: LegalDocument) => {
    Alert.alert(
      "Published legal URL",
      `The full ${target.title} is published at ${target.canonicalUrl.replace(/^https:\/\//, "")}.`,
      [{ text: "OK" }]
    );
  }, []);

  if (!document) {
    return (
      <SettingsShell bottomDock={false}>
        <SettingsHeader
          title="Legal"
          subtitle="Plain-language summaries of the documents that govern your use of PulseSoc. Each one links to the full published version."
        />

        <SettingsSection
          title="Documents"
          footnote="These summaries are kept in step with the published documents. Where they differ, the published version is the one that applies."
        >
          {LEGAL_DOCUMENT_ORDER.map((key) => {
            const entry = LEGAL_DOCUMENTS[key];
            return (
              <SettingsRow
                key={key}
                testID={`legal-open-${key}`}
                title={entry.title}
                subtitle={entry.blurb}
                icon={DOCUMENT_ICONS[key]}
                chevron
                accessibilityRole="button"
                accessibilityHint={`Opens the ${entry.title} in the app.`}
                onPress={() => openDocument(key)}
              />
            );
          })}
        </SettingsSection>
      </SettingsShell>
    );
  }

  return (
    <SettingsShell bottomDock={false}>
      <SettingsHeader title={document.title} subtitle={document.blurb} />

      <View
        style={[
          styles.meta,
          { backgroundColor: theme.colors.surfaceRaised, borderColor: theme.colors.border, borderRadius: theme.metrics.radius }
        ]}
      >
        <Text style={{ color: theme.colors.text, fontSize: theme.scaleFont(13), fontWeight: "800" }}>
          Effective {document.effectiveDate}
        </Text>
        <Text
          style={{
            color: theme.colors.muted,
            fontSize: theme.scaleFont(13),
            lineHeight: theme.scaleFont(19),
            marginTop: 4
          }}
        >
          This is the summary shown in the app. The full canonical version — the one that is legally operative — is published
          at {document.canonicalUrl.replace(/^https:\/\//, "")}.
        </Text>
      </View>

      {/* Selectable so a user can copy a clause to send to someone. */}
      <View style={styles.body}>
        {document.sections.map((section) => (
          <View key={section.heading} style={styles.section}>
            <Text
              accessibilityRole="header"
              style={{
                color: theme.colors.text,
                fontSize: theme.scaleFont(18),
                fontWeight: theme.metrics.titleWeight,
                letterSpacing: -0.2,
                marginBottom: 8
              }}
            >
              {section.heading}
            </Text>
            {section.paragraphs.map((paragraph, index) => (
              <Text
                key={index}
                selectable
                style={{
                  color: theme.colors.muted,
                  fontSize: theme.scaleFont(15),
                  lineHeight: theme.scaleFont(23),
                  marginBottom: index === section.paragraphs.length - 1 ? 0 : 10
                }}
              >
                {paragraph}
              </Text>
            ))}
          </View>
        ))}
      </View>

      <View style={styles.actions}>
        <SettingsButton
          testID={`legal-open-full-${document.key}`}
          label="Show published URL"
          icon="link-outline"
          variant="primary"
          onPress={() => showCanonical(document)}
        />
        <SettingsButton
          testID="legal-back-to-index"
          label="All legal documents"
          icon="list-outline"
          variant="secondary"
          onPress={showIndex}
        />
      </View>
    </SettingsShell>
  );
}

const styles = StyleSheet.create({
  meta: { borderWidth: StyleSheet.hairlineWidth, marginTop: 16, padding: 14 },
  body: { marginTop: 24 },
  section: { marginBottom: 24 },
  actions: { gap: 10, marginTop: 4 }
});
