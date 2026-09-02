import { Ionicons } from "@expo/vector-icons";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useTranslation } from "../../i18n";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";
import { createThemedStyles } from "../../theme/themedStyles";

export function AccountActions({
  onCreateAccount,
  onForgotPassword,
  onUseAnotherAccount
}: {
  onCreateAccount: () => void;
  onForgotPassword: () => void;
  onUseAnotherAccount?: () => void;
}) {
  const { t } = useTranslation();

  return (
    <View style={styles.root}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t("auth:signIn.createNewAccount")}
        testID="create-account-button"
        style={({ pressed }) => [styles.createButton, { opacity: pressed ? 0.8 : 1 }]}
        onPress={onCreateAccount}
      >
        <Ionicons name="person-add-outline" size={18} color={colors.accentStrong} />
        <Text style={styles.createButtonText}>{t("auth:signIn.createNewAccount")}</Text>
      </Pressable>

      <View style={styles.linkRow}>
        <Pressable accessibilityRole="button" testID="forgot-password-link" onPress={onForgotPassword} hitSlop={8}>
          <View style={styles.linkInline}>
            <Ionicons name="lock-closed-outline" size={14} color={colors.muted} />
            <Text style={styles.linkText}>{t("auth:signIn.forgotPassword")}</Text>
          </View>
        </Pressable>
        {onUseAnotherAccount ? (
          <>
            <Text style={styles.divider}>|</Text>
            <Pressable accessibilityRole="button" testID="use-another-account-link" onPress={onUseAnotherAccount} hitSlop={8}>
              <View style={styles.linkInline}>
                <Ionicons name="person-outline" size={14} color={colors.muted} />
                <Text style={styles.linkText}>{t("auth:signIn.useAnotherAccount")}</Text>
              </View>
            </Pressable>
          </>
        ) : null}
      </View>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  root: {
    gap: logiNexus.spacing.md
  },
  createButton: {
    alignItems: "center",
    borderColor: colors.accentStrong,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1.5,
    flexDirection: "row",
    gap: 8,
    justifyContent: "center",
    minHeight: 52
  },
  createButtonText: {
    color: colors.accentStrong,
    fontSize: 15,
    fontWeight: "900"
  },
  linkRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    justifyContent: "center"
  },
  linkInline: {
    alignItems: "center",
    flexDirection: "row",
    gap: 5
  },
  linkText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "700",
    textDecorationLine: "underline"
  },
  divider: {
    color: colors.border,
    fontSize: 13
  }
}));
