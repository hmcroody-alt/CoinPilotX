/**
 * The premium gate, rendered wherever the server answered
 * `{ok:false, code:"premium_required"}` or a draft needs a capability the
 * account does not have.
 *
 * There is deliberately no purchase logic here. The app already has exactly one
 * selling surface — the Premium route (PremiumCenterScreen) — and every gate in
 * the app resolves by navigating there, so this panel does the same. A second
 * checkout path is how two billing systems start.
 */

import { Pressable, Text, View } from "react-native";
import { useTranslation } from "../../i18n";
import { colors } from "../../theme/colors";
import { createThemedStyles } from "../../theme/themedStyles";

export function PremiumUpsellPanel({ body, onUpgrade }: { body: string; onUpgrade: () => void }) {
  const { t } = useTranslation();
  return (
    <View style={styles.panel}>
      <Text style={styles.title}>{t("discovery:crypto.upsell.title")}</Text>
      <Text style={styles.body}>{body}</Text>
      <Pressable accessibilityRole="button" style={styles.button} onPress={onUpgrade}>
        <Text style={styles.buttonLabel}>{t("discovery:crypto.upsell.cta")}</Text>
      </Pressable>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  body: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 20
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 42,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  buttonLabel: {
    color: "#08110f",
    fontSize: 13,
    fontWeight: "900"
  },
  panel: {
    backgroundColor: "rgba(37, 208, 167, 0.08)",
    borderColor: colors.accent,
    borderRadius: 10,
    borderWidth: 1,
    gap: 10,
    padding: 14
  },
  title: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "900",
    textTransform: "uppercase"
  }
}));
