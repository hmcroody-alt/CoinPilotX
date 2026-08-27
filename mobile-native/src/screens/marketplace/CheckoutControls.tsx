/**
 * The checkout's form primitives, on dark surfaces.
 *
 * Split out of `MarketplaceCheckoutScreen` because the screen's job is the
 * order state machine — which step, what the server said, whether a card has
 * been charged — and none of that is easier to read interleaved with picker
 * modals and radio geometry.
 *
 * The rule these share: **a control cannot emit a value the server would
 * reject.** The old screen was six `TextInput`s, so every field was a chance to
 * type something `validate_details` refuses — a date in the wrong order, a time
 * with an am/pm suffix, a country name where a code belongs. Here the calendar
 * emits `YYYY-MM-DD`, the clock emits `HH:MM`, and the country list emits an
 * ISO code the server told us it accepts. Validation still runs server-side and
 * still decides; these controls just stop the buyer being blamed for a format
 * they were never shown.
 */

import DateTimePicker, { type DateTimePickerEvent } from "@react-native-community/datetimepicker";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { useState } from "react";
import {
  ActivityIndicator,
  Image,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type KeyboardTypeOptions
} from "react-native";
import type { CheckoutCountry } from "../../api/checkoutCountries";
import {
  earliestSchedulableDate,
  formatDateLabel,
  formatTimeLabel,
  fromIsoDate,
  fromWireTime,
  toIsoDate,
  toWireTime
} from "../../api/checkoutSchedule";
import { checkoutDark, STORE_CTA } from "../../theme/marketplaceCheckoutDark";

/* ------------------------------------------------------------------ *
 * Progress
 * ------------------------------------------------------------------ */

export const CHECKOUT_STEPS = ["Details", "Review", "Payment", "Confirm"] as const;
export type CheckoutStepIndex = 0 | 1 | 2 | 3;

/**
 * Details → Review → Payment → Confirm.
 *
 * Fixed at four for every order type. A digital download skips nothing and a
 * booking adds nothing: the steps are the *commitment* ladder, not the field
 * count, and keeping them identical is what lets a buyer who has bought a
 * hoodie recognise where they are while buying a consultation.
 */
export function CheckoutStepper({ current }: { current: CheckoutStepIndex }) {
  return (
    <View accessibilityRole="progressbar" accessibilityLabel={`Step ${current + 1} of 4: ${CHECKOUT_STEPS[current]}`} style={styles.stepper}>
      {CHECKOUT_STEPS.map((label, index) => {
        const done = index <= current;
        return (
          <View key={label} style={styles.stepCell}>
            <View style={styles.stepRow}>
              <View style={[styles.stepTrack, index === 0 && styles.stepTrackHidden, index <= current && styles.stepTrackDone]} />
              <View style={[styles.stepDot, done && styles.stepDotDone]}>
                {index < current ? <Ionicons name="checkmark" size={11} color={STORE_CTA.text} /> : null}
              </View>
              <View style={[styles.stepTrack, index === CHECKOUT_STEPS.length - 1 && styles.stepTrackHidden, index < current && styles.stepTrackDone]} />
            </View>
            <Text style={[styles.stepLabel, done && styles.stepLabelDone]} numberOfLines={1}>{label}</Text>
          </View>
        );
      })}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Product summary
 * ------------------------------------------------------------------ */

/** Tint for the listing-type pill. Unknown types get the neutral plate rather
 * than being hidden — "what am I buying" is worth answering even vaguely. */
function badgeTone(kind: string) {
  const key = String(kind || "").toLowerCase();
  if (key.includes("digital")) return checkoutDark.badge.digital;
  if (key.includes("event") || key.includes("ticket")) return checkoutDark.badge.event;
  if (key.includes("booking") || key.includes("appointment")) return checkoutDark.badge.booking;
  if (key.includes("service")) return checkoutDark.badge.service;
  if (key.includes("physical") || key.includes("item")) return checkoutDark.badge.physical;
  return checkoutDark.badge.neutral;
}

/**
 * What is being bought, at the top of the form.
 *
 * Present because the form below is adaptive: a buyer who is asked for a
 * postal address on one order and a date on the next needs the reason on the
 * same screen. The type pill *is* that reason.
 */
export function ProductSummaryCard({
  title,
  seller,
  typeLabel,
  price,
  quantity,
  imageUrl
}: {
  title: string;
  seller: string;
  typeLabel: string;
  price: string;
  quantity?: number;
  imageUrl?: string;
}) {
  const tone = badgeTone(typeLabel);
  return (
    <View style={styles.summary}>
      {imageUrl ? (
        <Image source={{ uri: imageUrl }} style={styles.summaryImage} accessibilityIgnoresInvertColors />
      ) : (
        <View style={[styles.summaryImage, styles.summaryImageEmpty]}>
          <Ionicons name="bag-handle-outline" size={22} color={checkoutDark.text.faint} />
        </View>
      )}
      <View style={styles.summaryCopy}>
        <Text style={styles.summaryTitle} numberOfLines={2}>{title}</Text>
        {seller ? <Text style={styles.summarySeller} numberOfLines={1}>{seller}</Text> : null}
        {typeLabel ? (
          <View style={[styles.summaryBadge, { backgroundColor: tone.bg }]}>
            <Text style={[styles.summaryBadgeText, { color: tone.text }]}>{typeLabel}</Text>
          </View>
        ) : null}
      </View>
      <View style={styles.summaryPriceCol}>
        <Text style={styles.summaryPrice}>{price}</Text>
        {quantity && quantity > 1 ? <Text style={styles.summaryQty}>×{quantity}</Text> : null}
      </View>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Layout
 * ------------------------------------------------------------------ */

export function Section({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      {title ? <Text style={styles.sectionTitle}>{title}</Text> : null}
      {children}
    </View>
  );
}

/** The dark equivalent of a filled input: a label sitting above its value in a
 * recessed well. Used by every control below so a picker and a text box read as
 * the same kind of thing. */
function FieldShell({
  label,
  children,
  invalid = false
}: {
  label: string;
  children: React.ReactNode;
  invalid?: boolean;
}) {
  return (
    <View style={[styles.well, invalid && styles.wellInvalid]}>
      <Text style={styles.wellLabel}>{label}</Text>
      {children}
    </View>
  );
}

export function InfoNote({ children }: { children: React.ReactNode }) {
  return (
    <View style={styles.info}>
      <Ionicons name="information-circle-outline" size={18} color={checkoutDark.text.accent} style={styles.infoIcon} />
      <Text style={styles.infoText}>{children}</Text>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Text
 * ------------------------------------------------------------------ */

export function TextField({
  label,
  value,
  onChange,
  optional = false,
  multiline = false,
  keyboardType,
  autoCapitalize = "sentences",
  textContentType,
  placeholder
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  optional?: boolean;
  multiline?: boolean;
  keyboardType?: KeyboardTypeOptions;
  autoCapitalize?: "none" | "sentences" | "words" | "characters";
  textContentType?: React.ComponentProps<typeof TextInput>["textContentType"];
  placeholder?: string;
}) {
  return (
    <FieldShell label={optional ? `${label} (optional)` : label}>
      <TextInput
        accessibilityLabel={label}
        style={[styles.wellValue, styles.input, multiline && styles.inputMultiline]}
        value={value}
        onChangeText={onChange}
        multiline={multiline}
        keyboardType={keyboardType}
        autoCapitalize={autoCapitalize}
        textContentType={textContentType}
        placeholder={placeholder}
        placeholderTextColor={checkoutDark.text.faint}
      />
    </FieldShell>
  );
}

/* ------------------------------------------------------------------ *
 * Choosing from a list
 * ------------------------------------------------------------------ */

export type SelectOption = { value: string; label: string; hint?: string };

/**
 * A value chosen from a list the server supplied, in a sheet.
 *
 * Replaces the two-character country box and the ticket-tier text field. The
 * distinction that matters is `value` vs `label`: the buyer picks "United
 * States", the server receives `US`. Nothing typed ever becomes either.
 */
export function SelectField({
  label,
  value,
  options,
  onChange,
  placeholder = "Select",
  sheetTitle
}: {
  label: string;
  value: string;
  options: readonly SelectOption[];
  onChange: (next: string) => void;
  placeholder?: string;
  sheetTitle?: string;
}) {
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.value === value);
  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityValue={{ text: selected?.label || placeholder }}
        onPress={() => setOpen(true)}
      >
        <FieldShell label={label}>
          <View style={styles.wellRow}>
            <Text style={[styles.wellValue, !selected && styles.wellValueEmpty]} numberOfLines={1}>
              {selected?.label || placeholder}
            </Text>
            <Ionicons name="chevron-down" size={18} color={checkoutDark.text.muted} />
          </View>
        </FieldShell>
      </Pressable>
      <Modal visible={open} animationType="slide" transparent onRequestClose={() => setOpen(false)}>
        <Pressable style={styles.sheetScrim} onPress={() => setOpen(false)} accessibilityLabel="Close" />
        <View style={styles.sheet}>
          <View style={styles.sheetGrabber} />
          <Text style={styles.sheetTitle}>{sheetTitle || label}</Text>
          <ScrollView style={styles.sheetList}>
            {options.map((option) => {
              const active = option.value === value;
              return (
                <Pressable
                  key={option.value}
                  accessibilityRole="radio"
                  accessibilityState={{ selected: active }}
                  style={[styles.sheetRow, active && styles.sheetRowActive]}
                  onPress={() => { onChange(option.value); setOpen(false); }}
                >
                  <View style={styles.sheetRowCopy}>
                    <Text style={styles.sheetRowText}>{option.label}</Text>
                    {option.hint ? <Text style={styles.sheetRowHint}>{option.hint}</Text> : null}
                  </View>
                  {active ? <Ionicons name="checkmark" size={18} color={checkoutDark.text.accent} /> : null}
                </Pressable>
              );
            })}
          </ScrollView>
        </View>
      </Modal>
    </>
  );
}

/* ------------------------------------------------------------------ *
 * Date and time
 * ------------------------------------------------------------------ */

/** iOS keeps the picker in a sheet the buyer dismisses; Android's is already a
 * dialog that closes itself on pick. One component, two idioms, because a
 * modal wrapped around Android's dialog produces two stacked scrims. */
function useNativePicker(onCommit: (value: Date) => void, close: () => void) {
  return (event: DateTimePickerEvent, next?: Date) => {
    if (Platform.OS === "android") {
      close();
      if (event.type === "dismissed" || !next) return;
      onCommit(next);
      return;
    }
    if (next) onCommit(next);
  };
}

/**
 * A calendar, where `YYYY-MM-DD` used to be typed.
 *
 * Emits the same ISO string the server has always validated, built from local
 * calendar parts rather than a UTC conversion — see `toIsoDate` for why that
 * distinction decides whether a 9pm booking lands on the right day.
 */
export function DateField({
  label,
  value,
  onChange,
  minimumDate = earliestSchedulableDate(),
  maximumDate
}: {
  label: string;
  value: string;
  onChange: (isoDate: string) => void;
  minimumDate?: Date;
  maximumDate?: Date;
}) {
  const [open, setOpen] = useState(false);
  const parsed = fromIsoDate(value);
  const handle = useNativePicker((next) => onChange(toIsoDate(next)), () => setOpen(false));
  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityValue={{ text: parsed ? formatDateLabel(parsed) : "Not set" }}
        onPress={() => setOpen(true)}
      >
        <FieldShell label={label}>
          <View style={styles.wellRow}>
            <Text style={[styles.wellValue, !parsed && styles.wellValueEmpty]}>
              {parsed ? formatDateLabel(parsed) : "Select a date"}
            </Text>
            <Ionicons name="calendar-outline" size={18} color={checkoutDark.text.muted} />
          </View>
        </FieldShell>
      </Pressable>
      {open && Platform.OS === "android" ? (
        <DateTimePicker
          value={parsed || minimumDate}
          mode="date"
          display="calendar"
          minimumDate={minimumDate}
          maximumDate={maximumDate}
          onChange={handle}
        />
      ) : null}
      {Platform.OS === "ios" ? (
        <Modal visible={open} animationType="slide" transparent onRequestClose={() => setOpen(false)}>
          <Pressable style={styles.sheetScrim} onPress={() => setOpen(false)} accessibilityLabel="Close" />
          <View style={styles.sheet}>
            <View style={styles.sheetGrabber} />
            <Text style={styles.sheetTitle}>{label}</Text>
            <DateTimePicker
              value={parsed || minimumDate}
              mode="date"
              display="inline"
              themeVariant="dark"
              accentColor={STORE_CTA.from}
              minimumDate={minimumDate}
              maximumDate={maximumDate}
              onChange={handle}
            />
            <SheetDoneButton onPress={() => { if (!parsed) onChange(toIsoDate(minimumDate)); setOpen(false); }} />
          </View>
        </Modal>
      ) : null}
    </>
  );
}

/**
 * A clock face, where `HH:MM` used to be typed.
 *
 * The picker may show a 12-hour face with AM/PM if that is the device locale.
 * What travels is always 24-hour `HH:MM`, so the locale never reaches the wire
 * and "10:30 PM" is no longer something a buyer can submit.
 */
export function TimeField({
  label,
  value,
  onChange
}: {
  label: string;
  value: string;
  onChange: (wireTime: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const parsed = fromWireTime(value);
  const fallback = parsed || new Date();
  const handle = useNativePicker((next) => onChange(toWireTime(next)), () => setOpen(false));
  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityValue={{ text: parsed ? formatTimeLabel(parsed) : "Not set" }}
        onPress={() => setOpen(true)}
      >
        <FieldShell label={label}>
          <View style={styles.wellRow}>
            <Text style={[styles.wellValue, !parsed && styles.wellValueEmpty]}>
              {parsed ? formatTimeLabel(parsed) : "Select a time"}
            </Text>
            <Ionicons name="time-outline" size={18} color={checkoutDark.text.muted} />
          </View>
        </FieldShell>
      </Pressable>
      {open && Platform.OS === "android" ? (
        <DateTimePicker value={fallback} mode="time" display="clock" onChange={handle} />
      ) : null}
      {Platform.OS === "ios" ? (
        <Modal visible={open} animationType="slide" transparent onRequestClose={() => setOpen(false)}>
          <Pressable style={styles.sheetScrim} onPress={() => setOpen(false)} accessibilityLabel="Close" />
          <View style={styles.sheet}>
            <View style={styles.sheetGrabber} />
            <Text style={styles.sheetTitle}>{label}</Text>
            <DateTimePicker
              value={fallback}
              mode="time"
              display="spinner"
              themeVariant="dark"
              accentColor={STORE_CTA.from}
              onChange={handle}
            />
            <SheetDoneButton onPress={() => { if (!parsed) onChange(toWireTime(fallback)); setOpen(false); }} />
          </View>
        </Modal>
      ) : null}
    </>
  );
}

function SheetDoneButton({ onPress }: { onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" style={styles.sheetDone} onPress={onPress}>
      <Text style={styles.sheetDoneText}>Done</Text>
    </Pressable>
  );
}

/**
 * The timezone, stated rather than asked.
 *
 * This is the field the mission called out by name. The device knows the
 * answer, `Intl` reports it in the exact IANA form the server validates, and a
 * buyer typing it can only make it worse. So it renders as a line of context
 * under the time — visible, because a booking's timezone is worth confirming;
 * not editable, because there is nothing here the buyer can improve.
 */
export function TimezoneNote({ label }: { label: string }) {
  return (
    <View style={styles.tzRow}>
      <Ionicons name="globe-outline" size={15} color={checkoutDark.text.muted} />
      <Text style={styles.tzText}>Times shown in {label}, detected from your device.</Text>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Options and actions
 * ------------------------------------------------------------------ */

export function RadioRow({
  selected,
  title,
  detail,
  trailing,
  disabled = false,
  onPress
}: {
  selected: boolean;
  title: string;
  detail?: string;
  trailing?: string;
  disabled?: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ selected, disabled }}
      accessibilityLabel={title}
      accessibilityHint={detail}
      disabled={disabled}
      onPress={onPress}
      style={[styles.radio, selected && styles.radioSelected, disabled && styles.radioDisabled]}
    >
      <View style={[styles.radioMark, selected && styles.radioMarkSelected, disabled && styles.radioMarkDisabled]}>
        {selected ? <View style={[styles.radioPip, disabled && styles.radioPipDisabled]} /> : null}
      </View>
      <View style={styles.radioCopy}>
        <Text style={[styles.radioTitle, disabled && styles.radioTextDisabled]}>{title}</Text>
        {detail ? <Text style={[styles.radioDetail, disabled && styles.radioTextDisabled]}>{detail}</Text> : null}
      </View>
      {trailing ? <Text style={[styles.radioTrailing, disabled && styles.radioTrailingDisabled]}>{trailing}</Text> : null}
    </Pressable>
  );
}

/** Minus / count / plus, for ticket quantity. Clamped at both ends so the
 * control cannot produce a quantity the server would reject. */
export function QuantityStepper({
  value,
  min = 1,
  max = 20,
  onChange
}: {
  value: number;
  min?: number;
  max?: number;
  onChange: (next: number) => void;
}) {
  const set = (next: number) => onChange(Math.max(min, Math.min(max, next)));
  return (
    <View style={styles.qty}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Decrease quantity"
        accessibilityState={{ disabled: value <= min }}
        disabled={value <= min}
        style={[styles.qtyButton, value <= min && styles.qtyButtonOff]}
        onPress={() => set(value - 1)}
      >
        <Ionicons name="remove" size={18} color={value <= min ? checkoutDark.text.faint : checkoutDark.text.primary} />
      </Pressable>
      <Text accessibilityLabel={`Quantity ${value}`} style={styles.qtyValue}>{value}</Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Increase quantity"
        accessibilityState={{ disabled: value >= max }}
        disabled={value >= max}
        style={[styles.qtyButton, value >= max && styles.qtyButtonOff]}
        onPress={() => set(value + 1)}
      >
        <Ionicons name="add" size={18} color={value >= max ? checkoutDark.text.faint : checkoutDark.text.primary} />
      </Pressable>
    </View>
  );
}

/**
 * The step's single forward action.
 *
 * `busy` is not the same as `disabled`: disabled means the form is incomplete,
 * busy means a request is already in flight. Both block the press, but only
 * busy shows a spinner — and blocking the second press is the point, since the
 * request behind this button creates a payment intent.
 */
export function PrimaryButton({
  label,
  onPress,
  disabled = false,
  busy = false,
  icon = "arrow-forward"
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  busy?: boolean;
  icon?: React.ComponentProps<typeof Ionicons>["name"] | null;
}) {
  const off = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: off, busy }}
      accessibilityLabel={label}
      disabled={off}
      onPress={onPress}
      style={styles.ctaHit}
    >
      <LinearGradient
        colors={off ? [checkoutDark.bg.well, checkoutDark.bg.well] : [STORE_CTA.from, STORE_CTA.to]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 0 }}
        style={styles.cta}
      >
        {busy ? <ActivityIndicator size="small" color={checkoutDark.text.muted} /> : null}
        <Text style={[styles.ctaText, off && styles.ctaTextOff]}>{label}</Text>
        {!busy && icon ? <Ionicons name={icon} size={18} color={off ? checkoutDark.text.faint : STORE_CTA.text} /> : null}
      </LinearGradient>
    </Pressable>
  );
}

export function SecondaryButton({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} style={styles.secondary} onPress={onPress}>
      <Text style={styles.secondaryText}>{label}</Text>
    </Pressable>
  );
}

export function SummaryRow({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <View style={styles.row}>
      <Text style={[styles.rowLabel, strong && styles.rowStrong]}>{label}</Text>
      <Text style={[styles.rowValue, strong && styles.rowStrong]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  stepper: {
    flexDirection: "row",
    backgroundColor: checkoutDark.bg.card,
    borderRadius: checkoutDark.radius.card,
    borderWidth: 1,
    borderColor: checkoutDark.border.hairline,
    paddingVertical: 12,
    paddingHorizontal: 4
  },
  stepCell: { flex: 1, alignItems: "center", gap: 6 },
  stepRow: { flexDirection: "row", alignItems: "center", alignSelf: "stretch" },
  stepTrack: { flex: 1, height: 2, backgroundColor: checkoutDark.stepper.track },
  stepTrackHidden: { backgroundColor: "transparent" },
  stepTrackDone: { backgroundColor: checkoutDark.stepper.done },
  stepDot: {
    width: 16,
    height: 16,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: checkoutDark.stepper.pending,
    backgroundColor: checkoutDark.bg.card,
    alignItems: "center",
    justifyContent: "center"
  },
  stepDotDone: { backgroundColor: checkoutDark.stepper.done, borderColor: checkoutDark.stepper.done },
  stepLabel: { color: checkoutDark.text.faint, fontSize: 11, fontWeight: "700" },
  stepLabelDone: { color: checkoutDark.text.primary },

  summary: {
    flexDirection: "row",
    gap: 12,
    alignItems: "flex-start",
    backgroundColor: checkoutDark.bg.card,
    borderRadius: checkoutDark.radius.card,
    borderWidth: 1,
    borderColor: checkoutDark.border.hairline,
    padding: 12
  },
  summaryImage: { width: 62, height: 62, borderRadius: 10, backgroundColor: checkoutDark.bg.skeleton },
  summaryImageEmpty: { alignItems: "center", justifyContent: "center" },
  summaryCopy: { flex: 1, gap: 4 },
  summaryTitle: { color: checkoutDark.text.primary, fontSize: 16, fontWeight: "800", lineHeight: 21 },
  summarySeller: { color: checkoutDark.text.muted, fontSize: 13 },
  summaryBadge: { alignSelf: "flex-start", borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3, marginTop: 2 },
  summaryBadgeText: { fontSize: 11, fontWeight: "800" },
  summaryPriceCol: { alignItems: "flex-end", gap: 2 },
  summaryPrice: { color: checkoutDark.text.accent, fontSize: 17, fontWeight: "900" },
  summaryQty: { color: checkoutDark.text.muted, fontSize: 12, fontWeight: "700" },

  section: {
    backgroundColor: checkoutDark.bg.card,
    borderRadius: checkoutDark.radius.card,
    borderWidth: 1,
    borderColor: checkoutDark.border.hairline,
    padding: checkoutDark.space.gutter,
    gap: checkoutDark.space.field
  },
  sectionTitle: { color: checkoutDark.text.primary, fontSize: 15, fontWeight: "800" },

  well: {
    backgroundColor: checkoutDark.bg.well,
    borderRadius: checkoutDark.radius.well,
    borderWidth: 1,
    borderColor: checkoutDark.border.hairline,
    paddingHorizontal: 12,
    paddingTop: 7,
    paddingBottom: 8,
    gap: 1
  },
  wellInvalid: { borderColor: checkoutDark.status.error },
  wellLabel: { color: checkoutDark.text.faint, fontSize: 11, fontWeight: "700" },
  wellRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10, minHeight: 24 },
  wellValue: { color: checkoutDark.text.primary, fontSize: 15, flexShrink: 1 },
  wellValueEmpty: { color: checkoutDark.text.faint },
  input: { padding: 0, minHeight: 24 },
  inputMultiline: { minHeight: 62, textAlignVertical: "top", paddingTop: 2 },

  info: {
    flexDirection: "row",
    gap: 9,
    alignItems: "flex-start",
    backgroundColor: checkoutDark.bg.info,
    borderRadius: checkoutDark.radius.well,
    padding: 12
  },
  infoIcon: { marginTop: 1 },
  infoText: { flex: 1, color: checkoutDark.text.muted, fontSize: 13, lineHeight: 18 },

  tzRow: { flexDirection: "row", alignItems: "center", gap: 7, paddingTop: 2 },
  tzText: { flex: 1, color: checkoutDark.text.muted, fontSize: 12, lineHeight: 17 },

  radio: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: checkoutDark.bg.well,
    borderWidth: 1,
    borderColor: checkoutDark.border.hairline,
    borderRadius: checkoutDark.radius.well,
    padding: 13,
    minHeight: 56
  },
  radioSelected: { borderColor: checkoutDark.border.selected, backgroundColor: checkoutDark.bg.selected },
  radioDisabled: { opacity: 0.58, backgroundColor: "rgba(148, 163, 184, 0.08)" },
  radioMark: {
    width: 20,
    height: 20,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: checkoutDark.border.strong,
    alignItems: "center",
    justifyContent: "center"
  },
  radioMarkDisabled: { borderColor: checkoutDark.text.faint },
  radioMarkSelected: { borderColor: checkoutDark.border.selected },
  radioPip: { width: 10, height: 10, borderRadius: 5, backgroundColor: checkoutDark.border.selected },
  radioPipDisabled: { backgroundColor: checkoutDark.text.faint },
  radioCopy: { flex: 1, gap: 2 },
  radioTitle: { color: checkoutDark.text.primary, fontSize: 14, fontWeight: "700" },
  radioDetail: { color: checkoutDark.text.muted, fontSize: 12, lineHeight: 17 },
  radioTrailing: { color: checkoutDark.text.accent, fontSize: 14, fontWeight: "800" },
  radioTrailingDisabled: { color: checkoutDark.text.faint },
  radioTextDisabled: { color: checkoutDark.text.faint },

  qty: { flexDirection: "row", alignItems: "center", gap: 4, alignSelf: "flex-start" },
  qtyButton: {
    width: 40,
    height: 40,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: checkoutDark.border.hairline,
    backgroundColor: checkoutDark.bg.well,
    alignItems: "center",
    justifyContent: "center"
  },
  qtyButtonOff: { opacity: 0.5 },
  qtyValue: { color: checkoutDark.text.primary, fontSize: 16, fontWeight: "800", minWidth: 44, textAlign: "center" },

  ctaHit: { borderRadius: 14, overflow: "hidden" },
  cta: { minHeight: 52, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 9, paddingHorizontal: 18 },
  ctaText: { color: STORE_CTA.text, fontSize: 16, fontWeight: "900", textAlign: "center" },
  ctaTextOff: { color: checkoutDark.text.faint },
  secondary: {
    minHeight: 50,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: checkoutDark.border.strong,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 18
  },
  secondaryText: { color: checkoutDark.text.primary, fontSize: 15, fontWeight: "800" },

  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", gap: 14, minHeight: 22 },
  rowLabel: { color: checkoutDark.text.muted, fontSize: 14, flex: 1 },
  rowValue: { color: checkoutDark.text.primary, fontSize: 14, textAlign: "right", flexShrink: 1 },
  rowStrong: { color: checkoutDark.text.primary, fontWeight: "900", fontSize: 16 },

  sheetScrim: { flex: 1, backgroundColor: "rgba(0,0,0,0.65)" },
  sheet: {
    backgroundColor: checkoutDark.bg.card,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: checkoutDark.space.gutter,
    paddingTop: 10,
    paddingBottom: 30,
    gap: 10,
    maxHeight: "72%"
  },
  sheetGrabber: { alignSelf: "center", width: 38, height: 4, borderRadius: 2, backgroundColor: checkoutDark.border.strong },
  sheetTitle: { color: checkoutDark.text.primary, fontSize: 17, fontWeight: "800" },
  sheetList: { flexGrow: 0 },
  sheetRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    minHeight: 50,
    paddingHorizontal: 12,
    borderRadius: checkoutDark.radius.well
  },
  sheetRowActive: { backgroundColor: checkoutDark.bg.selected },
  sheetRowCopy: { flex: 1, gap: 2 },
  sheetRowText: { color: checkoutDark.text.primary, fontSize: 15 },
  sheetRowHint: { color: checkoutDark.text.muted, fontSize: 12 },
  sheetDone: {
    minHeight: 48,
    borderRadius: 12,
    backgroundColor: checkoutDark.bg.well,
    alignItems: "center",
    justifyContent: "center"
  },
  sheetDoneText: { color: checkoutDark.text.accent, fontSize: 15, fontWeight: "800" }
});
