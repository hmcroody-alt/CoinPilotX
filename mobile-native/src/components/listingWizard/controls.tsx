/**
 * Form controls for the marketplace listing wizard.
 *
 * These are white-commerce-surface controls: every colour comes from
 * `storeLight`, never from the dark `colors` theme, because the creation flow
 * lives on the same light surface as the Store dashboard. All user-visible text
 * arrives through props — callers translate with `t()` — so this file holds no
 * copy of its own.
 */

import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { ReactNode } from "react";
import {
  Modal,
  Pressable,
  ScrollView,
  StyleProp,
  StyleSheet,
  Text,
  TextInput,
  View,
  ViewStyle
} from "react-native";
import { STORE_CTA, storeLight } from "../../theme/storeLight";

export type WizardOption<K extends string = string> = {
  key: K;
  label: string;
  caption?: string;
};

/* ------------------------------------------------------------------ *
 * Layout
 * ------------------------------------------------------------------ */

export function WizardCard({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function WizardSectionTitle({ text }: { text: string }) {
  return <Text style={styles.sectionTitle}>{text}</Text>;
}

export function WizardHint({ text }: { text: string }) {
  return <Text style={styles.hint}>{text}</Text>;
}

export function WizardErrorText({ text }: { text: string }) {
  if (!text) return null;
  return <Text style={styles.error}>{text}</Text>;
}

/* ------------------------------------------------------------------ *
 * Text input
 * ------------------------------------------------------------------ */

export function WizardTextField({
  label,
  value,
  onChangeText,
  placeholder,
  error,
  multiline,
  keyboardType,
  maxLength,
  counterText,
  autoCapitalize
}: {
  label?: string;
  value: string;
  onChangeText: (next: string) => void;
  placeholder?: string;
  error?: string;
  multiline?: boolean;
  keyboardType?: "default" | "numeric" | "decimal-pad" | "url" | "numbers-and-punctuation";
  maxLength?: number;
  counterText?: string;
  autoCapitalize?: "none" | "sentences" | "words";
}) {
  return (
    <View style={styles.fieldBlock}>
      {label ? <Text style={styles.fieldLabel}>{label}</Text> : null}
      <TextInput
        style={[styles.input, multiline && styles.inputMultiline, Boolean(error) && styles.inputError]}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={storeLight.text.muted}
        multiline={multiline}
        keyboardType={keyboardType}
        maxLength={maxLength}
        autoCapitalize={autoCapitalize}
      />
      <View style={styles.fieldFootRow}>
        <View style={styles.fieldFootLeft}>
          <WizardErrorText text={error || ""} />
        </View>
        {counterText ? <Text style={styles.counter}>{counterText}</Text> : null}
      </View>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Segmented control
 * ------------------------------------------------------------------ */

export function WizardSegmented<K extends string>({
  label,
  options,
  value,
  onChange
}: {
  label?: string;
  options: WizardOption<K>[];
  value: K;
  onChange: (next: K) => void;
}) {
  return (
    <View style={styles.fieldBlock}>
      {label ? <Text style={styles.fieldLabel}>{label}</Text> : null}
      <View style={styles.segmentTrack}>
        {options.map((option) => {
          const active = option.key === value;
          return (
            <Pressable
              key={option.key}
              style={[styles.segment, active && styles.segmentActive]}
              onPress={() => onChange(option.key)}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              accessibilityLabel={option.label}
            >
              <Text style={[styles.segmentText, active && styles.segmentTextActive]} numberOfLines={1}>
                {option.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Radio group
 * ------------------------------------------------------------------ */

export function WizardRadioGroup<K extends string>({
  label,
  options,
  value,
  onChange,
  error
}: {
  label?: string;
  options: WizardOption<K>[];
  value: K;
  onChange: (next: K) => void;
  error?: string;
}) {
  return (
    <View style={styles.fieldBlock}>
      {label ? <Text style={styles.fieldLabel}>{label}</Text> : null}
      <View style={styles.radioGroup}>
        {options.map((option, index) => {
          const active = option.key === value;
          return (
            <Pressable
              key={option.key}
              style={[styles.radioRow, index > 0 && styles.radioRowDivider]}
              onPress={() => onChange(option.key)}
              accessibilityRole="radio"
              accessibilityState={{ selected: active }}
              accessibilityLabel={option.label}
            >
              <View style={[styles.radioOuter, active && styles.radioOuterActive]}>
                {active ? <View style={styles.radioInner} /> : null}
              </View>
              <View style={styles.radioTextBlock}>
                <Text style={styles.radioLabel}>{option.label}</Text>
                {option.caption ? <Text style={styles.radioCaption}>{option.caption}</Text> : null}
              </View>
            </Pressable>
          );
        })}
      </View>
      <WizardErrorText text={error || ""} />
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Quantity stepper
 * ------------------------------------------------------------------ */

export function WizardStepper({
  label,
  value,
  onChange,
  min = 1,
  max = 9999,
  error
}: {
  label: string;
  value: number;
  onChange: (next: number) => void;
  min?: number;
  max?: number;
  error?: string;
}) {
  const clamp = (next: number) => Math.max(min, Math.min(max, next));
  return (
    <View style={styles.fieldBlock}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <View style={styles.stepperRow}>
        <Pressable
          style={[styles.stepperButton, value <= min && styles.stepperButtonDisabled]}
          disabled={value <= min}
          onPress={() => onChange(clamp(value - 1))}
          accessibilityRole="button"
          accessibilityLabel={`${label} −1`}
        >
          <Ionicons name="remove" size={20} color={storeLight.text.primary} />
        </Pressable>
        <Text style={styles.stepperValue}>{value}</Text>
        <Pressable
          style={[styles.stepperButton, value >= max && styles.stepperButtonDisabled]}
          disabled={value >= max}
          onPress={() => onChange(clamp(value + 1))}
          accessibilityRole="button"
          accessibilityLabel={`${label} +1`}
        >
          <Ionicons name="add" size={20} color={storeLight.text.primary} />
        </Pressable>
      </View>
      <WizardErrorText text={error || ""} />
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Select (modal option sheet)
 * ------------------------------------------------------------------ */

export function WizardSelect<K extends string>({
  label,
  options,
  selectedKey,
  onSelect,
  error,
  open,
  onOpen,
  onClose,
  sheetTitle
}: {
  label?: string;
  options: WizardOption<K>[];
  selectedKey: K | "";
  onSelect: (next: K) => void;
  error?: string;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  sheetTitle: string;
}) {
  const selected = options.find((option) => option.key === selectedKey);
  return (
    <View style={styles.fieldBlock}>
      {label ? <Text style={styles.fieldLabel}>{label}</Text> : null}
      <Pressable
        style={[styles.input, styles.selectTrigger, Boolean(error) && styles.inputError]}
        onPress={onOpen}
        accessibilityRole="button"
        accessibilityLabel={label || sheetTitle}
      >
        <Text style={selected ? styles.selectValue : styles.selectPlaceholder} numberOfLines={1}>
          {selected ? selected.label : sheetTitle}
        </Text>
        <Ionicons name="chevron-down" size={16} color={storeLight.text.muted} />
      </Pressable>
      <WizardErrorText text={error || ""} />
      <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
        <Pressable style={styles.modalBackdrop} onPress={onClose}>
          <Pressable style={styles.modalSheet} onPress={(event) => event.stopPropagation()}>
            <Text style={styles.modalTitle}>{sheetTitle}</Text>
            <ScrollView style={styles.modalList}>
              {options.map((option) => {
                const active = option.key === selectedKey;
                return (
                  <Pressable
                    key={option.key}
                    style={styles.modalRow}
                    onPress={() => {
                      onSelect(option.key);
                      onClose();
                    }}
                    accessibilityRole="button"
                    accessibilityState={{ selected: active }}
                    accessibilityLabel={option.label}
                  >
                    <Text style={[styles.modalRowText, active && styles.modalRowTextActive]}>{option.label}</Text>
                    {active ? <Ionicons name="checkmark" size={18} color={storeLight.accent.brandOnLight} /> : null}
                  </Pressable>
                );
              })}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Buttons and chips
 * ------------------------------------------------------------------ */

export function WizardPrimaryButton({
  label,
  onPress,
  disabled
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityState={{ disabled: Boolean(disabled) }}
      accessibilityLabel={label}
      style={[styles.primaryWrap, disabled && styles.primaryDisabled]}
    >
      <LinearGradient
        colors={[STORE_CTA.from, STORE_CTA.to]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.primaryFill}
      >
        <Text style={styles.primaryText}>{label}</Text>
      </LinearGradient>
    </Pressable>
  );
}

export function WizardSecondaryButton({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.secondaryButton} onPress={onPress} accessibilityRole="button" accessibilityLabel={label}>
      <Text style={styles.secondaryText}>{label}</Text>
    </Pressable>
  );
}

export function WizardInlineAddButton({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable style={styles.inlineAdd} onPress={onPress} accessibilityRole="button" accessibilityLabel={label}>
      <Ionicons name="add-circle-outline" size={18} color={storeLight.text.link} />
      <Text style={styles.inlineAddText}>{label}</Text>
    </Pressable>
  );
}

export function WizardChip({ icon, label }: { icon?: keyof typeof Ionicons.glyphMap; label: string }) {
  return (
    <View style={styles.chip}>
      {icon ? <Ionicons name={icon} size={13} color={storeLight.text.primary} /> : null}
      <Text style={styles.chipText} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Styles
 * ------------------------------------------------------------------ */

const styles = StyleSheet.create({
  card: {
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    padding: storeLight.space.card,
    gap: 10
  },
  sectionTitle: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  hint: { fontSize: 12, lineHeight: 17, color: storeLight.text.muted },
  error: { fontSize: 12, color: storeLight.status.error, fontWeight: "600" },
  fieldBlock: { gap: 6 },
  fieldLabel: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  fieldFootRow: { flexDirection: "row", alignItems: "center" },
  fieldFootLeft: { flex: 1 },
  counter: { fontSize: 11, color: storeLight.text.muted },
  input: {
    minHeight: storeLight.size.tapTarget,
    borderWidth: 1,
    borderColor: storeLight.border.hairline,
    borderRadius: storeLight.radius.control,
    backgroundColor: storeLight.bg.card,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    color: storeLight.text.primary
  },
  inputMultiline: { minHeight: 110, textAlignVertical: "top" },
  inputError: { borderColor: storeLight.status.error },
  segmentTrack: {
    flexDirection: "row",
    backgroundColor: storeLight.bg.skeleton,
    borderRadius: storeLight.radius.control,
    padding: 3,
    gap: 3
  },
  segment: {
    flex: 1,
    minHeight: 36,
    borderRadius: storeLight.radius.control - 2,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 6
  },
  segmentActive: {
    backgroundColor: storeLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  segmentText: { fontSize: 12, fontWeight: "600", color: storeLight.text.muted },
  segmentTextActive: { color: storeLight.text.primary },
  radioGroup: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    borderRadius: storeLight.radius.control,
    overflow: "hidden"
  },
  radioRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  radioRowDivider: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: storeLight.border.hairline },
  radioOuter: {
    width: 20,
    height: 20,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: storeLight.border.secondaryButton,
    alignItems: "center",
    justifyContent: "center"
  },
  radioOuterActive: { borderColor: storeLight.accent.brandOnLight },
  radioInner: { width: 10, height: 10, borderRadius: 5, backgroundColor: storeLight.accent.brandOnLight },
  radioTextBlock: { flex: 1, gap: 1 },
  radioLabel: { fontSize: 14, fontWeight: "600", color: storeLight.text.primary },
  radioCaption: { fontSize: 12, color: storeLight.text.muted },
  stepperRow: { flexDirection: "row", alignItems: "center", gap: 14 },
  stepperButton: {
    width: storeLight.size.tapTarget,
    height: storeLight.size.tapTarget - 6,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: storeLight.bg.card
  },
  stepperButtonDisabled: { opacity: 0.4 },
  stepperValue: { minWidth: 44, textAlign: "center", fontSize: 17, fontWeight: "700", color: storeLight.text.primary },
  selectTrigger: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  selectValue: { flex: 1, fontSize: 15, color: storeLight.text.primary },
  selectPlaceholder: { flex: 1, fontSize: 15, color: storeLight.text.muted },
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(15, 17, 17, 0.45)",
    justifyContent: "flex-end"
  },
  modalSheet: {
    backgroundColor: storeLight.bg.card,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingTop: 16,
    paddingBottom: 28,
    maxHeight: "70%"
  },
  modalTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: storeLight.text.primary,
    paddingHorizontal: 16,
    paddingBottom: 8
  },
  modalList: { paddingHorizontal: 4 },
  modalRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: storeLight.border.hairline
  },
  modalRowText: { fontSize: 15, color: storeLight.text.primary },
  modalRowTextActive: { fontWeight: "700", color: storeLight.accent.brandOnLight },
  primaryWrap: { borderRadius: storeLight.radius.pill, overflow: "hidden" },
  primaryDisabled: { opacity: 0.5 },
  primaryFill: {
    minHeight: storeLight.size.tapTarget + 4,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 16
  },
  primaryText: { fontSize: 15, fontWeight: "800", color: STORE_CTA.text },
  secondaryButton: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    backgroundColor: storeLight.bg.card,
    paddingHorizontal: 16
  },
  secondaryText: { fontSize: 14, fontWeight: "600", color: storeLight.text.primary },
  inlineAdd: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    minHeight: 36,
    alignSelf: "flex-start"
  },
  inlineAddText: { fontSize: 13, fontWeight: "600", color: storeLight.text.link },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: storeLight.bg.skeleton,
    borderRadius: storeLight.radius.pill,
    paddingHorizontal: 10,
    paddingVertical: 5
  },
  chipText: { fontSize: 12, fontWeight: "600", color: storeLight.text.primary }
});
