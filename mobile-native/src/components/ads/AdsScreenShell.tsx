/**
 * Shared chrome for the wave-2 Advertising sub-screens (Reports, Wallet,
 * Audiences, Library, Policy Center, Insights): the navy gradient header with a
 * back chevron, and the scroll body with the bottom-nav clearance every other
 * ads surface uses. Kept as one component so the six screens cannot drift on
 * paddings, tap targets or header type.
 *
 * `adsSubStyles` is the card vocabulary those screens share — the same tokens
 * `AdsSubPageScreen` set, extracted rather than copied six more times.
 */

import { ReactElement, ReactNode } from "react";
import { Pressable, RefreshControlProps, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { adsLight } from "../../theme/adsLight";

export function AdsScreenShell({
  title,
  backLabel,
  onBack,
  children,
  refreshControl
}: {
  title: string;
  /** Accessibility label for the back chevron — i18n'd by the caller. */
  backLabel: string;
  onBack?: () => void;
  children: ReactNode;
  refreshControl?: ReactElement<RefreshControlProps>;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View style={shellStyles.root}>
      <LinearGradient
        colors={[adsLight.bg.headerFrom, adsLight.bg.headerTo]}
        style={[shellStyles.header, { paddingTop: insets.top + 8 }]}
      >
        <Pressable
          onPress={onBack}
          style={shellStyles.iconButton}
          accessibilityRole="button"
          accessibilityLabel={backLabel}
          hitSlop={6}
        >
          <Ionicons name="chevron-back" size={24} color={adsLight.text.onDark} />
        </Pressable>
        <Text style={shellStyles.headerTitle} numberOfLines={1} accessibilityRole="header">
          {title}
        </Text>
      </LinearGradient>
      <ScrollView
        contentContainerStyle={[
          shellStyles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        showsVerticalScrollIndicator={false}
        refreshControl={refreshControl}
        keyboardShouldPersistTaps="handled"
      >
        {children}
      </ScrollView>
    </View>
  );
}

const shellStyles = StyleSheet.create({
  root: { flex: 1, backgroundColor: adsLight.bg.page },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: adsLight.space.card,
    paddingBottom: 12
  },
  iconButton: {
    minWidth: adsLight.size.tapTarget,
    minHeight: adsLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  headerTitle: { flex: 1, fontSize: 20, fontWeight: "700", color: adsLight.text.onDark },
  content: { paddingTop: 12, gap: 14 }
});

/** The card vocabulary the wave-2 sub-screens share. */
export const adsSubStyles = StyleSheet.create({
  stack: { gap: 10, paddingHorizontal: adsLight.space.card },
  sectionTitle: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary },
  card: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    padding: adsLight.space.card,
    gap: 8
  },
  cardTitle: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary, lineHeight: 20 },
  cardBody: { fontSize: 13, color: adsLight.text.muted, lineHeight: 19 },
  meta: { fontSize: 12, color: adsLight.text.muted, lineHeight: 17 },
  headRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 8
  },
  notice: { fontSize: 13, fontWeight: "700", color: adsLight.text.primary, lineHeight: 19 },
  reasonBox: {
    padding: 10,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.strip,
    gap: 2
  },
  reasonLabel: { fontSize: 11, fontWeight: "700", color: adsLight.text.muted },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    minHeight: adsLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 14,
    borderRadius: adsLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    backgroundColor: adsLight.bg.strip
  },
  chipActive: { backgroundColor: adsLight.cta.from, borderColor: adsLight.cta.to },
  chipDimmed: { opacity: 0.45 },
  chipText: { fontSize: 13, fontWeight: "800", color: adsLight.text.primary },
  chipTextActive: { color: adsLight.cta.text },
  inputLabel: { fontSize: 12, fontWeight: "700", color: adsLight.text.muted },
  input: {
    minHeight: adsLight.size.tapTarget,
    borderRadius: adsLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    backgroundColor: adsLight.bg.card,
    paddingHorizontal: 12,
    fontSize: 14,
    color: adsLight.text.primary
  },
  primaryBtn: {
    minHeight: adsLight.size.tapTarget,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.cta.from,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 16
  },
  primaryBtnText: { fontSize: 14, fontWeight: "800", color: adsLight.cta.text },
  secondaryBtn: {
    minHeight: adsLight.size.tapTarget,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 16
  },
  secondaryBtnText: { fontSize: 14, fontWeight: "800", color: adsLight.text.primary },
  inlineLink: { fontSize: 13, fontWeight: "700", color: adsLight.text.link, paddingVertical: 4 },
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(15,17,17,0.45)",
    justifyContent: "center",
    padding: 24
  },
  modalCard: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    padding: adsLight.space.card,
    gap: 10
  }
});

export default AdsScreenShell;
