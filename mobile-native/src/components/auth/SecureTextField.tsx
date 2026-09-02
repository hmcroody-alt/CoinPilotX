import { Ionicons } from "@expo/vector-icons";
import { forwardRef, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, TextInputProps, View } from "react-native";
import { useTranslation } from "../../i18n";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";
import { createThemedStyles } from "../../theme/themedStyles";

type SecureTextFieldProps = TextInputProps & {
  label: string;
  iconName: keyof typeof Ionicons.glyphMap;
  secureToggle?: boolean;
  errorText?: string;
  testID?: string;
};

export const SecureTextField = forwardRef<TextInput, SecureTextFieldProps>(function SecureTextField(
  { label, iconName, secureToggle, errorText, secureTextEntry, testID, ...inputProps },
  ref
) {
  const { t } = useTranslation();
  const [focused, setFocused] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const hasError = Boolean(errorText);
  const borderColor = hasError ? colors.danger : focused ? colors.accentStrong : colors.border;

  return (
    <View style={styles.root}>
      <View style={[styles.field, { borderColor }]}>
        <Ionicons name={iconName} size={18} color={focused ? colors.accentStrong : colors.muted} style={styles.icon} />
        <TextInput
          ref={ref}
          {...inputProps}
          accessibilityLabel={label}
          placeholder={label}
          placeholderTextColor={colors.muted}
          secureTextEntry={secureToggle ? !revealed : secureTextEntry}
          style={styles.input}
          testID={testID}
          onFocus={(event) => {
            setFocused(true);
            inputProps.onFocus?.(event);
          }}
          onBlur={(event) => {
            setFocused(false);
            inputProps.onBlur?.(event);
          }}
        />
        {secureToggle ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={revealed ? t("auth:signIn.hidePassword") : t("auth:signIn.showPassword")}
            hitSlop={10}
            onPress={() => setRevealed((value) => !value)}
          >
            <Ionicons name={revealed ? "eye-off-outline" : "eye-outline"} size={20} color={colors.muted} />
          </Pressable>
        ) : null}
      </View>
      {hasError ? (
        <Text style={styles.error} accessibilityLiveRegion="polite">
          {errorText}
        </Text>
      ) : null}
    </View>
  );
});

const styles = createThemedStyles(() => ({
  root: {
    gap: 6
  },
  field: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1.5,
    flexDirection: "row",
    minHeight: 54,
    paddingHorizontal: 14
  },
  icon: {
    marginRight: 10
  },
  input: {
    color: colors.text,
    flex: 1,
    fontSize: 16,
    fontWeight: "600",
    paddingVertical: 12
  },
  error: {
    color: colors.danger,
    fontSize: 12,
    fontWeight: "700",
    paddingHorizontal: 4
  }
}));
