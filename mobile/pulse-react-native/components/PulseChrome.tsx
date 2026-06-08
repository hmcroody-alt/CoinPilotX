import React from "react";
import { Linking, Text, TouchableOpacity, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { colors, screenStyles } from "./theme";

type IconName = React.ComponentProps<typeof MaterialCommunityIcons>["name"];

export function PulseTopBar({ subtitle, safeTop }: { subtitle?: string; safeTop?: boolean }) {
  const insets = useSafeAreaInsets();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 12, marginTop: safeTop ? insets.top + 6 : 0, marginBottom: 14 }}>
      <TouchableOpacity accessibilityLabel="Open PulseSoc menu" style={iconButton}>
        <MaterialCommunityIcons name="menu" color={colors.text} size={22} />
      </TouchableOpacity>
      <View style={brandMark}>
        <Text style={{ color: colors.background, fontSize: 18, fontWeight: "900" }}>P</Text>
      </View>
      <View style={{ flex: 1 }}>
        <Text style={{ color: colors.text, fontSize: 22, fontWeight: "900" }}>PulseSoc</Text>
        {subtitle ? <Text style={{ color: colors.muted, fontSize: 12, marginTop: 1 }}>{subtitle}</Text> : null}
      </View>
      <TouchableOpacity accessibilityLabel="Search PulseSoc" style={iconButton} onPress={() => Linking.openURL("https://pulsesoc.com/search")}>
        <MaterialCommunityIcons name="magnify" color={colors.text} size={21} />
      </TouchableOpacity>
      <TouchableOpacity accessibilityLabel="Open PulseSoc notifications" style={iconButton}>
        <MaterialCommunityIcons name="bell-outline" color={colors.text} size={21} />
      </TouchableOpacity>
    </View>
  );
}

export function PulseHeroCard({
  eyebrow,
  title,
  body,
  actionLabel,
  actionIcon = "arrow-right",
  onAction
}: {
  eyebrow: string;
  title: string;
  body: string;
  actionLabel?: string;
  actionIcon?: IconName;
  onAction?: () => void;
}) {
  return (
    <View style={heroCard}>
      <PulsePill icon="earth" label={eyebrow} />
      <Text style={{ color: colors.text, fontSize: 30, lineHeight: 35, fontWeight: "900", marginTop: 18 }}>{title}</Text>
      <Text style={{ color: colors.muted, fontSize: 15, lineHeight: 22, marginTop: 10 }}>{body}</Text>
      {actionLabel && onAction ? (
        <TouchableOpacity onPress={onAction} style={[screenStyles.secondaryButton, { alignSelf: "flex-start", marginTop: 16, borderColor: colors.accentAlt }]}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <Text style={[screenStyles.secondaryButtonText, { color: colors.text }]}>{actionLabel}</Text>
            <MaterialCommunityIcons name={actionIcon} color={colors.accentAlt} size={18} />
          </View>
        </TouchableOpacity>
      ) : null}
    </View>
  );
}

export function PulseSectionCard({
  eyebrow,
  title,
  body,
  icon,
  accent = colors.accentAlt,
  children
}: {
  eyebrow: string;
  title: string;
  body?: string;
  icon: IconName;
  accent?: string;
  children?: React.ReactNode;
}) {
  return (
    <View style={[screenStyles.card, { borderColor: colors.borderSoft, backgroundColor: colors.surfaceStrong }]}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
        <View style={[smallIcon, { borderColor: accent }]}>
          <MaterialCommunityIcons name={icon} color={accent} size={19} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={{ color: accent, fontSize: 11, fontWeight: "900", letterSpacing: 0, textTransform: "uppercase" }}>{eyebrow}</Text>
          <Text style={screenStyles.cardTitle}>{title}</Text>
        </View>
      </View>
      {body ? <Text style={[screenStyles.muted, { marginTop: 9 }]}>{body}</Text> : null}
      {children ? <View style={{ marginTop: 12 }}>{children}</View> : null}
    </View>
  );
}

export function PulsePill({ icon, label, accent = colors.accent }: { icon: IconName; label: string; accent?: string }) {
  return (
    <View style={{ alignSelf: "flex-start", flexDirection: "row", alignItems: "center", gap: 7, borderWidth: 1, borderColor: colors.border, borderRadius: 999, paddingHorizontal: 11, paddingVertical: 7, backgroundColor: "#10253a" }}>
      <MaterialCommunityIcons name={icon} color={accent} size={15} />
      <Text style={{ color: colors.text, fontSize: 13, fontWeight: "900" }}>{label}</Text>
    </View>
  );
}

export function PulseActionChip({ icon, label, onPress }: { icon: IconName; label: string; onPress?: () => void }) {
  return (
    <TouchableOpacity disabled={!onPress} onPress={onPress} style={{ flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 1, borderColor: colors.border, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 7, backgroundColor: colors.surface }}>
      <MaterialCommunityIcons name={icon} color={colors.accentAlt} size={16} />
      <Text style={{ color: colors.text, fontSize: 12, fontWeight: "800" }}>{label}</Text>
    </TouchableOpacity>
  );
}

const iconButton = {
  width: 38,
  height: 38,
  borderRadius: 19,
  alignItems: "center" as const,
  justifyContent: "center" as const,
  borderWidth: 1,
  borderColor: colors.border,
  backgroundColor: colors.surface
};

const brandMark = {
  width: 45,
  height: 45,
  borderRadius: 16,
  alignItems: "center" as const,
  justifyContent: "center" as const,
  backgroundColor: colors.accentAlt,
  borderWidth: 1,
  borderColor: colors.accent
};

const heroCard = {
  borderWidth: 1,
  borderColor: colors.border,
  borderRadius: 24,
  padding: 18,
  marginBottom: 12,
  backgroundColor: colors.surfaceStrong
};

const smallIcon = {
  width: 38,
  height: 38,
  borderRadius: 19,
  alignItems: "center" as const,
  justifyContent: "center" as const,
  borderWidth: 1,
  backgroundColor: colors.surface
};
