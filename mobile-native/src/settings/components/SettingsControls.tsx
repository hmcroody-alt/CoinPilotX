/**
 * Settings controls.
 *
 * Every control here shares one contract:
 *  - it renders inside a `SettingsSection` card and draws no outer border
 *    (the section owns separators),
 *  - it exposes correct `accessibilityRole` / `accessibilityState`,
 *  - it has a >=44pt touch target regardless of density or font scale,
 *  - it reports its own busy state rather than blocking the screen.
 */

import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Animated,
  I18nManager,
  LayoutChangeEvent,
  PanResponder,
  Platform,
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
  ViewStyle
} from "react-native";
import * as Haptics from "expo-haptics";
import { Ionicons } from "@expo/vector-icons";
import { useTheme } from "../../theme/ThemeContext";

type RowTone = "default" | "danger" | "accent";

function toneColor(tone: RowTone, theme: ReturnType<typeof useTheme>) {
  if (tone === "danger") return theme.colors.danger;
  if (tone === "accent") return theme.colors.accent;
  return theme.colors.text;
}

/* -------------------------------------------------------------------------- */
/*                                  Base row                                   */
/* -------------------------------------------------------------------------- */

export type SettingsRowProps = {
  title: string;
  subtitle?: string;
  icon?: keyof typeof Ionicons.glyphMap;
  iconColor?: string;
  /** Trailing content: a value label, switch, spinner, badge. */
  accessory?: ReactNode;
  /** Shows a chevron and makes the row feel tappable. */
  chevron?: boolean;
  onPress?: () => void;
  disabled?: boolean;
  busy?: boolean;
  tone?: RowTone;
  testID?: string;
  accessibilityRole?: "button" | "switch" | "link" | "none";
  accessibilityState?: { checked?: boolean; disabled?: boolean; selected?: boolean };
  accessibilityHint?: string;
  style?: ViewStyle;
};

export function SettingsRow({
  title,
  subtitle,
  icon,
  iconColor,
  accessory,
  chevron = false,
  onPress,
  disabled = false,
  busy = false,
  tone = "default",
  testID,
  accessibilityRole,
  accessibilityState,
  accessibilityHint,
  style
}: SettingsRowProps) {
  const theme = useTheme();
  const interactive = Boolean(onPress) && !disabled;
  const color = toneColor(tone, theme);

  return (
    <Pressable
      testID={testID}
      accessibilityRole={accessibilityRole ?? (interactive ? "button" : "none")}
      accessibilityState={{ disabled: disabled || busy, ...accessibilityState }}
      accessibilityHint={theme.hapticFeedback ? accessibilityHint : accessibilityHint}
      accessibilityLabel={subtitle ? `${title}. ${subtitle}` : title}
      disabled={!interactive || busy}
      onPress={onPress}
      android_ripple={interactive ? { color: theme.colors.signalDim } : undefined}
      style={({ pressed }) => [
        styles.row,
        {
          minHeight: Math.max(44, theme.metrics.rowMinHeight),
          paddingVertical: theme.metrics.rowPaddingVertical,
          paddingHorizontal: theme.metrics.rowPaddingHorizontal,
          backgroundColor: pressed && interactive && Platform.OS === "ios" ? theme.colors.surfaceRaised : "transparent",
          opacity: disabled ? 0.45 : 1
        },
        style
      ]}
    >
      {icon ? (
        <View style={[styles.iconWell, { backgroundColor: theme.colors.surfaceRaised, borderRadius: 8 }]}>
          <Ionicons name={icon} size={theme.scaleFont(17)} color={iconColor || (tone === "danger" ? theme.colors.danger : theme.colors.accent)} />
        </View>
      ) : null}

      <View style={styles.rowBody}>
        <Text
          numberOfLines={2}
          style={{
            color,
            fontSize: theme.scaleFont(16),
            fontWeight: theme.metrics.bodyWeight === "600" ? "700" : "600"
          }}
        >
          {title}
        </Text>
        {subtitle ? (
          <Text
            style={{
              color: theme.colors.muted,
              fontSize: theme.scaleFont(13),
              lineHeight: theme.scaleFont(18),
              marginTop: 2
            }}
          >
            {subtitle}
          </Text>
        ) : null}
      </View>

      <View style={styles.rowAccessory}>
        {busy ? <ActivityIndicator size="small" color={theme.colors.accent} /> : accessory}
        {chevron ? (
          <Ionicons
            name={I18nManager.isRTL ? "chevron-back" : "chevron-forward"}
            size={theme.scaleFont(17)}
            color={theme.colors.muted}
          />
        ) : null}
      </View>
    </Pressable>
  );
}

/** Right-aligned muted value text, e.g. the current selection on a nav row. */
export function SettingsValue({ children }: { children: ReactNode }) {
  const theme = useTheme();
  return (
    <Text numberOfLines={1} style={{ color: theme.colors.muted, fontSize: theme.scaleFont(15), maxWidth: 170 }}>
      {children}
    </Text>
  );
}

/* -------------------------------------------------------------------------- */
/*                                   Switch                                    */
/* -------------------------------------------------------------------------- */

export function SettingsSwitch({
  title,
  subtitle,
  icon,
  value,
  onValueChange,
  disabled = false,
  busy = false,
  testID
}: {
  title: string;
  subtitle?: string;
  icon?: keyof typeof Ionicons.glyphMap;
  value: boolean;
  onValueChange: (next: boolean) => void;
  disabled?: boolean;
  busy?: boolean;
  testID?: string;
}) {
  const theme = useTheme();

  const handleChange = useCallback(
    (next: boolean) => {
      if (theme.hapticFeedback) {
        void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
      }
      onValueChange(next);
    },
    [onValueChange, theme.hapticFeedback]
  );

  return (
    <SettingsRow
      testID={testID}
      title={title}
      subtitle={subtitle}
      icon={icon}
      disabled={disabled}
      accessibilityRole="switch"
      accessibilityState={{ checked: value, disabled: disabled || busy }}
      // Tapping the row toggles too — a 56pt-tall target instead of a 51pt-wide one.
      onPress={disabled || busy ? undefined : () => handleChange(!value)}
      accessory={
        <Switch
          value={value}
          onValueChange={handleChange}
          disabled={disabled || busy}
          trackColor={{ false: theme.colors.border, true: theme.colors.accent }}
          thumbColor={Platform.OS === "android" ? (value ? theme.colors.surface : theme.colors.muted) : undefined}
          ios_backgroundColor={theme.colors.border}
        />
      }
      busy={busy}
    />
  );
}

/* -------------------------------------------------------------------------- */
/*                              Select / radio list                            */
/* -------------------------------------------------------------------------- */

export type SelectOption<T extends string> = {
  value: T;
  label: string;
  description?: string;
  icon?: keyof typeof Ionicons.glyphMap;
};

/**
 * Inline radio group. Preferred over a modal picker for <=5 options: the user
 * sees every choice and its consequence without a second navigation step.
 */
export function SettingsSelect<T extends string>({
  options,
  value,
  onChange,
  disabled = false,
  testID
}: {
  options: SelectOption<T>[];
  value: T;
  onChange: (next: T) => void;
  disabled?: boolean;
  testID?: string;
}) {
  const theme = useTheme();

  const handleSelect = useCallback(
    (next: T) => {
      if (next === value) return;
      if (theme.hapticFeedback) {
        void Haptics.selectionAsync().catch(() => undefined);
      }
      onChange(next);
    },
    [onChange, theme.hapticFeedback, value]
  );

  return (
    <>
      {options.map((option, index) => {
        const selected = option.value === value;
        return (
          <View key={option.value}>
            {index > 0 ? (
              <View style={[styles.separator, { backgroundColor: theme.colors.border, marginLeft: theme.metrics.rowPaddingHorizontal }]} />
            ) : null}
            <SettingsRow
              testID={testID ? `${testID}-${option.value}` : undefined}
              title={option.label}
              subtitle={option.description}
              icon={option.icon}
              disabled={disabled}
              accessibilityRole="button"
              accessibilityState={{ selected, disabled }}
              onPress={() => handleSelect(option.value)}
              accessory={
                selected ? (
                  <Ionicons name="checkmark" size={theme.scaleFont(20)} color={theme.colors.accent} />
                ) : (
                  <View style={{ width: theme.scaleFont(20) }} />
                )
              }
            />
          </View>
        );
      })}
    </>
  );
}

/* -------------------------------------------------------------------------- */
/*                                   Slider                                    */
/* -------------------------------------------------------------------------- */

/**
 * Stepped slider built on PanResponder.
 *
 * Written against RN core rather than adding a native slider dependency: the
 * app ships without `@react-native-community/slider`, and a new native module
 * would force a rebuild of both dev clients for one control.
 *
 * Drag emits continuously so the preview updates live; the store's 400ms
 * coalescing window is what keeps that from becoming 40 network writes.
 */
export function SettingsSlider({
  title,
  subtitle,
  value,
  minimumValue,
  maximumValue,
  step,
  onChange,
  onSlidingComplete,
  formatValue,
  disabled = false,
  testID
}: {
  title?: string;
  subtitle?: string;
  value: number;
  minimumValue: number;
  maximumValue: number;
  step: number;
  onChange: (next: number) => void;
  onSlidingComplete?: (next: number) => void;
  formatValue?: (value: number) => string;
  disabled?: boolean;
  testID?: string;
}) {
  const theme = useTheme();
  const [trackWidth, setTrackWidth] = useState(0);
  const widthRef = useRef(0);
  const valueRef = useRef(value);
  const lastEmitted = useRef(value);
  valueRef.current = value;

  const range = Math.max(maximumValue - minimumValue, 0.0001);
  const ratio = Math.min(1, Math.max(0, (value - minimumValue) / range));

  const quantize = useCallback(
    (raw: number) => {
      const clamped = Math.min(maximumValue, Math.max(minimumValue, raw));
      const steps = Math.round((clamped - minimumValue) / step);
      return Number((minimumValue + steps * step).toFixed(4));
    },
    [maximumValue, minimumValue, step]
  );

  const emit = useCallback(
    (raw: number, final: boolean) => {
      const next = quantize(raw);
      if (next !== lastEmitted.current) {
        lastEmitted.current = next;
        if (theme.hapticFeedback) {
          void Haptics.selectionAsync().catch(() => undefined);
        }
        onChange(next);
      }
      if (final) onSlidingComplete?.(next);
    },
    [onChange, onSlidingComplete, quantize, theme.hapticFeedback]
  );

  const positionToValue = useCallback(
    (x: number) => {
      const width = widthRef.current;
      if (width <= 0) return valueRef.current;
      const clampedX = Math.min(width, Math.max(0, I18nManager.isRTL ? width - x : x));
      return minimumValue + (clampedX / width) * range;
    },
    [minimumValue, range]
  );

  const panResponder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => !disabled,
        onMoveShouldSetPanResponder: () => !disabled,
        // Claim the gesture so the enclosing ScrollView does not steal the drag.
        onPanResponderTerminationRequest: () => false,
        onPanResponderGrant: (event) => emit(positionToValue(event.nativeEvent.locationX), false),
        onPanResponderMove: (event, gesture) => {
          const width = widthRef.current;
          if (width <= 0) return;
          const startX = (I18nManager.isRTL ? 1 - ratio : ratio) * width;
          emit(positionToValue(startX + gesture.dx), false);
        },
        onPanResponderRelease: () => emit(valueRef.current, true),
        onPanResponderTerminate: () => emit(valueRef.current, true)
      }),
    [disabled, emit, positionToValue, ratio]
  );

  const onLayout = useCallback((event: LayoutChangeEvent) => {
    const width = event.nativeEvent.layout.width;
    widthRef.current = width;
    setTrackWidth(width);
  }, []);

  const display = formatValue ? formatValue(value) : String(value);

  return (
    <View
      style={[
        styles.sliderContainer,
        { paddingHorizontal: theme.metrics.rowPaddingHorizontal, paddingVertical: theme.metrics.rowPaddingVertical + 4 }
      ]}
    >
      {title ? (
        <View style={styles.sliderHeader}>
          <Text style={{ color: theme.colors.text, fontSize: theme.scaleFont(16), fontWeight: "600" }}>{title}</Text>
          <Text style={{ color: theme.colors.accent, fontSize: theme.scaleFont(15), fontWeight: "700" }}>{display}</Text>
        </View>
      ) : null}
      {subtitle ? (
        <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(13), lineHeight: theme.scaleFont(18), marginBottom: 10 }}>
          {subtitle}
        </Text>
      ) : null}

      <View
        testID={testID}
        accessible
        accessibilityRole="adjustable"
        accessibilityLabel={title}
        accessibilityValue={{ min: minimumValue, max: maximumValue, now: value, text: display }}
        accessibilityState={{ disabled }}
        accessibilityActions={[{ name: "increment" }, { name: "decrement" }]}
        onAccessibilityAction={(event) => {
          if (disabled) return;
          const delta = event.nativeEvent.actionName === "increment" ? step : -step;
          emit(value + delta, true);
        }}
        onLayout={onLayout}
        style={styles.sliderTouchArea}
        {...panResponder.panHandlers}
      >
        <View style={[styles.sliderTrack, { backgroundColor: theme.colors.border, opacity: disabled ? 0.5 : 1 }]}>
          <View style={[styles.sliderFill, { backgroundColor: theme.colors.accent, width: `${ratio * 100}%` }]} />
        </View>
        <View
          style={[
            styles.sliderThumb,
            {
              backgroundColor: theme.colors.surface,
              borderColor: theme.colors.accent,
              opacity: disabled ? 0.5 : 1,
              transform: [{ translateX: Math.max(0, ratio * trackWidth - 12) }]
            }
          ]}
        />
      </View>
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                 Text field                                  */
/* -------------------------------------------------------------------------- */

export function SettingsTextField({
  label,
  value,
  onChangeText,
  placeholder,
  helperText,
  errorText,
  multiline = false,
  maxLength,
  keyboardType,
  autoCapitalize = "none",
  secureTextEntry = false,
  editable = true,
  testID
}: {
  label: string;
  value: string;
  onChangeText: (next: string) => void;
  placeholder?: string;
  helperText?: string;
  errorText?: string;
  multiline?: boolean;
  maxLength?: number;
  keyboardType?: "default" | "email-address" | "phone-pad" | "url" | "number-pad";
  autoCapitalize?: "none" | "sentences" | "words";
  secureTextEntry?: boolean;
  editable?: boolean;
  testID?: string;
}) {
  const theme = useTheme();
  const [focused, setFocused] = useState(false);
  const invalid = Boolean(errorText);

  return (
    <View style={{ paddingHorizontal: theme.metrics.rowPaddingHorizontal, paddingVertical: theme.metrics.rowPaddingVertical + 2 }}>
      <View style={styles.fieldLabelRow}>
        <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(12), fontWeight: "700", letterSpacing: 0.6 }}>
          {label.toUpperCase()}
        </Text>
        {maxLength ? (
          <Text style={{ color: value.length > maxLength * 0.9 ? theme.colors.warning : theme.colors.muted, fontSize: theme.scaleFont(12) }}>
            {value.length}/{maxLength}
          </Text>
        ) : null}
      </View>
      <TextInput
        testID={testID}
        value={value}
        onChangeText={onChangeText}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder={placeholder}
        placeholderTextColor={theme.colors.muted}
        multiline={multiline}
        maxLength={maxLength}
        keyboardType={keyboardType}
        autoCapitalize={autoCapitalize}
        autoCorrect={autoCapitalize !== "none"}
        secureTextEntry={secureTextEntry}
        editable={editable}
        accessibilityLabel={label}
        accessibilityState={{ disabled: !editable }}
        style={[
          styles.field,
          {
            backgroundColor: theme.colors.surfaceRaised,
            borderColor: invalid ? theme.colors.danger : focused ? theme.colors.accent : theme.colors.border,
            borderRadius: 10,
            color: theme.colors.text,
            fontSize: theme.scaleFont(16),
            minHeight: multiline ? theme.scaleFont(88) : Math.max(44, theme.scaleFont(44)),
            opacity: editable ? 1 : 0.6,
            textAlignVertical: multiline ? "top" : "center"
          }
        ]}
      />
      {invalid ? (
        <Text style={{ color: theme.colors.danger, fontSize: theme.scaleFont(12), marginTop: 6 }}>{errorText}</Text>
      ) : helperText ? (
        <Text style={{ color: theme.colors.muted, fontSize: theme.scaleFont(12), marginTop: 6, lineHeight: theme.scaleFont(17) }}>
          {helperText}
        </Text>
      ) : null}
    </View>
  );
}

/* -------------------------------------------------------------------------- */
/*                                   Buttons                                   */
/* -------------------------------------------------------------------------- */

export function SettingsButton({
  label,
  onPress,
  variant = "primary",
  busy = false,
  disabled = false,
  icon,
  testID,
  full = true
}: {
  label: string;
  onPress: () => void;
  variant?: "primary" | "secondary" | "destructive";
  busy?: boolean;
  disabled?: boolean;
  icon?: keyof typeof Ionicons.glyphMap;
  testID?: string;
  full?: boolean;
}) {
  const theme = useTheme();
  const scale = useRef(new Animated.Value(1)).current;

  const background =
    variant === "primary" ? theme.colors.accent : variant === "destructive" ? theme.colors.dangerSoft : "transparent";
  const foreground =
    variant === "primary" ? (theme.scheme === "light" ? "#ffffff" : "#08110f") : variant === "destructive" ? theme.colors.danger : theme.colors.text;
  const border = variant === "secondary" ? theme.colors.border : variant === "destructive" ? theme.colors.danger : "transparent";

  const press = (to: number) =>
    Animated.spring(scale, { toValue: to, useNativeDriver: true, speed: 40, bounciness: 0 }).start();

  return (
    <Animated.View style={{ transform: [{ scale }], width: full ? "100%" : undefined }}>
      <Pressable
        testID={testID}
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityState={{ disabled: disabled || busy, busy }}
        disabled={disabled || busy}
        onPressIn={() => !theme.reduceMotion && press(0.97)}
        onPressOut={() => !theme.reduceMotion && press(1)}
        onPress={onPress}
        style={[
          styles.button,
          {
            backgroundColor: background,
            borderColor: border,
            borderRadius: 12,
            minHeight: Math.max(48, theme.scaleFont(48)),
            opacity: disabled ? 0.45 : 1
          }
        ]}
      >
        {busy ? (
          <ActivityIndicator size="small" color={foreground} />
        ) : (
          <>
            {icon ? <Ionicons name={icon} size={theme.scaleFont(17)} color={foreground} /> : null}
            <Text style={{ color: foreground, fontSize: theme.scaleFont(16), fontWeight: "800" }}>{label}</Text>
          </>
        )}
      </Pressable>
    </Animated.View>
  );
}

/* -------------------------------------------------------------------------- */
/*                              Confirmation dialog                            */
/* -------------------------------------------------------------------------- */

/**
 * Promise-based confirmation. Resolves `true` only when the user picks the
 * confirm action; dismissing resolves `false`, so callers can `if (!await ...)`
 * and bail without duplicating cancel handling at every call site.
 */
export function confirm({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false
}: {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
}): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (result: boolean) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    Alert.alert(
      title,
      message,
      [
        { text: cancelLabel, style: "cancel", onPress: () => settle(false) },
        { text: confirmLabel, style: destructive ? "destructive" : "default", onPress: () => settle(true) }
      ],
      { cancelable: true, onDismiss: () => settle(false) }
    );
  });
}

/**
 * Confirmation that requires typing an exact phrase. Used for irreversible
 * account actions where a single mis-tap must not be sufficient.
 */
export function DestructiveConfirmField({
  phrase,
  value,
  onChangeText,
  label
}: {
  phrase: string;
  value: string;
  onChangeText: (next: string) => void;
  label?: string;
}) {
  const theme = useTheme();
  const matched = value.trim().toUpperCase() === phrase.toUpperCase();
  return (
    <SettingsTextField
      label={label || `Type ${phrase} to confirm`}
      value={value}
      onChangeText={onChangeText}
      placeholder={phrase}
      autoCapitalize="none"
      helperText={matched ? "Confirmed." : `This cannot be undone.`}
      errorText={value.length > 0 && !matched ? `Type ${phrase} exactly.` : undefined}
      testID="destructive-confirm-field"
    />
  );
}

/** Small status pill, e.g. "Verified", "2FA on". */
export function SettingsBadge({ label, tone = "accent" }: { label: string; tone?: "accent" | "danger" | "warning" | "muted" }) {
  const theme = useTheme();
  const color =
    tone === "danger" ? theme.colors.danger : tone === "warning" ? theme.colors.warning : tone === "muted" ? theme.colors.muted : theme.colors.accent;
  const background =
    tone === "danger" ? theme.colors.dangerSoft : tone === "warning" ? theme.colors.warningSoft : tone === "muted" ? "transparent" : theme.colors.signalDim;
  return (
    <View style={[styles.badge, { backgroundColor: background, borderColor: color }]}>
      <Text style={{ color, fontSize: theme.scaleFont(11), fontWeight: "800", letterSpacing: 0.3 }}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { alignItems: "center", flexDirection: "row", gap: 12 },
  rowBody: { flex: 1, justifyContent: "center" },
  rowAccessory: { alignItems: "center", flexDirection: "row", gap: 6 },
  iconWell: { alignItems: "center", height: 30, justifyContent: "center", width: 30 },
  separator: { height: StyleSheet.hairlineWidth },
  sliderContainer: { width: "100%" },
  sliderHeader: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", marginBottom: 4 },
  sliderTouchArea: { height: 44, justifyContent: "center" },
  sliderTrack: { borderRadius: 3, height: 6, overflow: "hidden", width: "100%" },
  sliderFill: { height: "100%" },
  sliderThumb: {
    borderRadius: 12,
    borderWidth: 2,
    height: 24,
    left: 0,
    position: "absolute",
    width: 24
  },
  fieldLabelRow: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", marginBottom: 6 },
  field: { borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 12, paddingVertical: 10 },
  button: { alignItems: "center", borderWidth: StyleSheet.hairlineWidth, flexDirection: "row", gap: 8, justifyContent: "center", paddingHorizontal: 18 },
  badge: { borderRadius: 6, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 7, paddingVertical: 3 }
});
