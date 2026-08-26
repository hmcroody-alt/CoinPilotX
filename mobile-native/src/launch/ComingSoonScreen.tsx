/**
 * The Coming Soon message as a whole screen.
 *
 * The sheet in `ComingSoonSheet` answers a tap on a card. This answers arriving
 * at a gated route by any other means: a deep link, a `navigation.navigate` left
 * behind in some other surface, or navigation state restored after a cold start.
 *
 * Both surfaces exist because a card-level check alone is not a gate — it is a
 * convention, and conventions are what deep links walk straight past. A route
 * that renders this cannot be entered, only read.
 *
 * Same copy, same vocabulary, same promise as the sheet.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useTranslation } from "../i18n";
import { colors } from "../theme/colors";
import { presenceTheme } from "../theme/presenceTheme";
import { createThemedStyles } from "../theme/themedStyles";
import type { LaunchModuleId } from "./readiness";

export function ComingSoonScreen({
  moduleId,
  label,
  onBack
}: {
  moduleId: LaunchModuleId;
  /** The module's own name — the screen still says which door this is. */
  label: string;
  onBack?: () => void;
}) {
  const { t } = useTranslation();

  return (
    <View style={styles.root} testID={`coming-soon-screen-${moduleId}`}>
      {onBack ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("common:actions.back")}
          onPress={onBack}
          style={styles.back}
        >
          <Ionicons name="chevron-back" size={24} color={colors.text} />
        </Pressable>
      ) : null}
      <View style={styles.body}>
        <View style={styles.halo} />
        <Ionicons name="sparkles-outline" size={32} color={presenceTheme.teal} />
        <Text style={styles.eyebrow}>{t("commerce:launch.comingSoonTitle")}</Text>
        <Text style={styles.module}>{label}</Text>
        <Text style={styles.text}>{t("commerce:launch.comingSoonBody")}</Text>
        {onBack ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("commerce:launch.comingSoonAction")}
            testID="coming-soon-screen-dismiss"
            onPress={onBack}
            style={styles.action}
          >
            <Text style={styles.actionText}>{t("commerce:launch.comingSoonAction")}</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  action: {
    alignItems: "center",
    backgroundColor: presenceTheme.teal,
    borderRadius: 12,
    justifyContent: "center",
    marginTop: 8,
    minHeight: 48,
    paddingHorizontal: 28
  },
  actionText: {
    color: "#08110f",
    fontSize: 15,
    fontWeight: "800"
  },
  back: {
    alignItems: "center",
    height: 44,
    justifyContent: "center",
    marginLeft: 8,
    marginTop: 52,
    width: 44
  },
  body: {
    alignItems: "center",
    flex: 1,
    gap: 10,
    justifyContent: "center",
    padding: 32
  },
  eyebrow: {
    color: presenceTheme.teal,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 2
  },
  /** Static wash of brand teal. Nothing on this screen moves — see the sheet. */
  halo: {
    backgroundColor: presenceTheme.tealSoft,
    borderRadius: 999,
    height: 220,
    position: "absolute",
    width: 220
  },
  module: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "800",
    textAlign: "center"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  text: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 22,
    maxWidth: 380,
    textAlign: "center"
  }
}));
