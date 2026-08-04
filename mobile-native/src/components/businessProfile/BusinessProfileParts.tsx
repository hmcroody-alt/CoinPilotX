/**
 * Parts for the rebuilt owner Business Profile screen.
 *
 * These are separate from `BusinessLiveParts` rather than added to it because the
 * two files answer different questions. `BusinessLiveParts` was written for a screen
 * that mixed editing, previewing and system status into one scroll; its `DetailRow`
 * takes a `value` and an `emptyConsequence` and nothing else, which is exactly right
 * for a read-only line and exactly wrong for a row that has to say "under review",
 * "3 fields missing", or "changing this needs a re-check".
 *
 * The pieces here share one rule: **a row states its own condition.** Nothing in this
 * file renders a value without also rendering what the platform knows about that
 * value — missing, private, queued for review, or measured but not yet meaningful.
 * That rule is what stops the screen printing a dash and leaving the seller to guess
 * whether it means zero, unknown, or broken.
 */

import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
  ViewStyle
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { logiNexus } from "../../theme/logiNexus";

const palette = logiNexus.colors.businessLive;
const { spacing, typography, radius } = logiNexus;

/* ===================================================================== rows */

/**
 * The condition a row is in. Rendered as a chip beside the label, because the state
 * of a field is as much a fact about it as its value: a seller who cannot see that
 * "Business name" is queued for review will read the unchanged public profile as a
 * failed save and try again.
 */
export type RowCondition =
  // `ok` carries a note too — not because an untroubled row usually needs one, but
  // because the alternative is a union where `condition.note` is only reachable
  // after narrowing four ways, and the row renders the note identically in all
  // five cases. Omitting it here made this file fail to compile.
  | { kind: "ok"; note?: string }
  | { kind: "missing"; note?: string }
  | { kind: "review"; note?: string }
  | { kind: "blocked"; note?: string }
  | { kind: "private"; note?: string };

const CONDITION_CHIP: Record<
  RowCondition["kind"],
  { label: string; color: string; background: string } | null
> = {
  ok: null,
  missing: { label: "Missing", color: palette.textMuted, background: palette.panelStrong },
  review: { label: "Needs review", color: palette.warning, background: palette.warningSoft },
  blocked: { label: "Locked", color: palette.warning, background: palette.warningSoft },
  private: { label: "Private", color: palette.textMuted, background: palette.panelStrong }
};

export function IdentityRow({
  icon,
  label,
  value,
  hint,
  condition = { kind: "ok" },
  onPress,
  disabled
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  value: string;
  /** What a buyer loses while this is empty. Only shown when the value is empty. */
  hint?: string;
  condition?: RowCondition;
  onPress: () => void;
  disabled?: boolean;
}) {
  const chip = CONDITION_CHIP[condition.kind];
  const empty = !value.trim();
  // The accessible name carries the condition too. A screen reader announcing a bare
  // "Business name, Harbour Goods" over a row that is locked would send someone into
  // an editor that is going to refuse them.
  const accessibilityLabel = [
    label,
    empty ? "not set" : value,
    chip ? chip.label.toLowerCase() : ""
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{ disabled: Boolean(disabled) }}
      accessibilityHint={disabled ? undefined : `Opens the ${label.toLowerCase()} editor`}
      onPress={disabled ? undefined : onPress}
      style={({ pressed }) => [styles.row, pressed && !disabled ? styles.pressed : null, disabled ? styles.dim : null]}
    >
      <View style={styles.rowIcon}>
        <Ionicons name={icon} size={16} color={empty ? palette.textDim : palette.accent} />
      </View>
      <View style={styles.rowBody}>
        <View style={styles.rowLabelLine}>
          <Text style={styles.rowLabel}>{label}</Text>
          {chip ? (
            <View style={[styles.chip, { backgroundColor: chip.background }]}>
              <Text style={[styles.chipText, { color: chip.color }]}>{chip.label}</Text>
            </View>
          ) : null}
        </View>
        {empty ? (
          <Text style={styles.rowEmpty}>{hint || "Not set"}</Text>
        ) : (
          <Text style={styles.rowValue} numberOfLines={2}>
            {value}
          </Text>
        )}
        {condition.note ? <Text style={styles.rowNote}>{condition.note}</Text> : null}
      </View>
      {disabled ? null : <Ionicons name="chevron-forward" size={16} color={palette.textDim} />}
    </Pressable>
  );
}

/* ============================================================ completeness */

/**
 * The completeness breakdown.
 *
 * The old card said "65% · Halfway there." and stopped, which tells a seller their
 * grade and not their homework. This lists both halves: what is done, so the number
 * is auditable, and what is left, so it is actionable. Each missing item is tappable
 * and lands in the editor for that exact field.
 */
export function CompletenessBreakdown({
  percent,
  completed,
  missing,
  onOpen,
  onCompleteNext,
  nextLabel
}: {
  percent: number;
  completed: { key: string; label: string }[];
  missing: { key: string; label: string }[];
  onOpen: (key: string) => void;
  onCompleteNext: () => void;
  nextLabel: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const rounded = Math.round(Number.isFinite(percent) ? percent : 0);
  const done = completed.length;
  const total = done + missing.length;

  return (
    <View style={styles.completeness}>
      <View style={styles.completenessHead}>
        <View style={styles.completenessFigure}>
          <Text style={styles.completenessPercent} accessibilityLabel={`Profile completeness ${rounded} percent`}>
            {rounded}%
          </Text>
          <Text style={styles.completenessCaption}>
            {done} of {total} done
          </Text>
        </View>
        <View style={styles.completenessCopy}>
          <Text style={styles.completenessTitle}>Profile completeness</Text>
          <Text style={styles.completenessBody}>
            {missing.length === 0
              ? "Everything buyers look for is filled in."
              : `${missing.length} ${missing.length === 1 ? "thing is" : "things are"} still missing before buyers see a full profile.`}
          </Text>
        </View>
      </View>

      <View style={styles.trackOuter} accessible accessibilityLabel={`${rounded} percent complete`}>
        <View style={[styles.trackFill, { width: `${Math.max(2, Math.min(100, rounded))}%` }]} />
      </View>

      {missing.length ? (
        <View style={styles.itemBlock}>
          <Text style={styles.itemHeading}>Still needed</Text>
          {missing.map((item) => (
            <Pressable
              key={item.key}
              accessibilityRole="button"
              accessibilityLabel={`Add ${item.label}`}
              onPress={() => onOpen(item.key)}
              style={({ pressed }) => [styles.item, pressed ? styles.pressed : null]}
            >
              <Ionicons name="ellipse-outline" size={14} color={palette.textDim} />
              <Text style={styles.itemLabel}>{item.label}</Text>
              <Ionicons name="chevron-forward" size={14} color={palette.textDim} />
            </Pressable>
          ))}
        </View>
      ) : null}

      {completed.length ? (
        <View style={styles.itemBlock}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={expanded ? "Hide completed items" : `Show ${completed.length} completed items`}
            accessibilityState={{ expanded }}
            onPress={() => setExpanded((value) => !value)}
            style={({ pressed }) => [styles.disclosure, pressed ? styles.pressed : null]}
          >
            <Text style={styles.itemHeading}>Completed ({completed.length})</Text>
            <Ionicons name={expanded ? "chevron-up" : "chevron-down"} size={14} color={palette.textDim} />
          </Pressable>
          {expanded
            ? completed.map((item) => (
                <View key={item.key} style={styles.item}>
                  <Ionicons name="checkmark-circle" size={14} color={palette.accent} />
                  <Text style={[styles.itemLabel, styles.itemLabelDone]}>{item.label}</Text>
                </View>
              ))
            : null}
        </View>
      ) : null}

      {nextLabel ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Complete profile — next: ${nextLabel}`}
          onPress={onCompleteNext}
          style={({ pressed }) => [styles.primaryButton, pressed ? styles.pressed : null]}
        >
          <Text style={styles.primaryButtonLabel}>Complete profile</Text>
          <Ionicons name="arrow-forward" size={15} color={palette.background} />
        </Pressable>
      ) : null}
    </View>
  );
}

/* ============================================================== reputation */

/**
 * One reputation metric.
 *
 * `value` is null when the platform has no measurement — which is not the same as
 * zero. A rating of zero means every buyer hated you; no rating means nobody has
 * reviewed you. The old ticker printed "—" for both.
 */
export type ReputationMetric = {
  key: string;
  label: string;
  value: string | null;
  /** Shown in place of the value when there is nothing to show. */
  emptyLabel: string;
  /** How the number is worked out. Required — a metric nobody can audit is a rumour. */
  method: string;
  icon: keyof typeof Ionicons.glyphMap;
};

/**
 * A stable grid, not a scrolling ticker.
 *
 * The ticker clipped its own values ("Profile views to…"), moved while being read,
 * and put six metrics in a lane that fits three. A grid holds still, wraps its
 * labels, and lets a seller compare two numbers without waiting for the carousel.
 */
export function ReputationGrid({
  metrics,
  onExplain
}: {
  metrics: ReputationMetric[];
  onExplain: (metric: ReputationMetric) => void;
}) {
  return (
    <View style={styles.grid}>
      {metrics.map((metric) => {
        const measured = metric.value != null;
        return (
          <Pressable
            key={metric.key}
            accessibilityRole="button"
            accessibilityLabel={`${metric.label}: ${measured ? metric.value : metric.emptyLabel}. How this is calculated.`}
            onPress={() => onExplain(metric)}
            style={({ pressed }) => [styles.gridCell, pressed ? styles.pressed : null]}
          >
            <Ionicons name={metric.icon} size={15} color={measured ? palette.accent : palette.textDim} />
            {measured ? (
              <Text style={styles.gridValue}>{metric.value}</Text>
            ) : (
              <Text style={styles.gridEmpty}>{metric.emptyLabel}</Text>
            )}
            <Text style={styles.gridLabel}>{metric.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/* =============================================================== connected */

export type SystemState =
  | "not_connected"
  | "setup_incomplete"
  | "ready"
  | "open"
  | "paused"
  | "restricted"
  | "suspended"
  | "under_review";

export const SYSTEM_STATE_LABELS: Record<SystemState, string> = {
  not_connected: "Not connected",
  setup_incomplete: "Setup incomplete",
  ready: "Ready",
  open: "Open",
  paused: "Paused",
  restricted: "Restricted",
  suspended: "Suspended",
  under_review: "Under review"
};

const SYSTEM_STATE_TONE: Record<SystemState, "good" | "warn" | "idle"> = {
  not_connected: "idle",
  setup_incomplete: "warn",
  ready: "good",
  open: "good",
  paused: "warn",
  restricted: "warn",
  suspended: "warn",
  under_review: "warn"
};

export type ConnectedSystem = {
  key: string;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  state: SystemState;
  /** The count that matters for this system, already formatted. Empty when none. */
  detail: string;
  /** The one thing standing in the way. Empty when nothing is. */
  blocker: string;
  /** What tapping does. Always present — a status row with no next step is a dead end. */
  actionLabel: string;
  onPress: () => void;
};

/**
 * The connected-systems registry.
 *
 * Every row answers the same four questions in the same order — connected?, ready?,
 * how many?, what next? — so a seller can scan the column rather than read ten
 * differently-shaped cards. The old section showed three rows, two of which said
 * only "No listings yet".
 */
export function ConnectedSystemRow({ system, last }: { system: ConnectedSystem; last?: boolean }) {
  const tone = SYSTEM_STATE_TONE[system.state];
  const color = tone === "good" ? palette.accent : tone === "warn" ? palette.warning : palette.textDim;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={[
        system.label,
        SYSTEM_STATE_LABELS[system.state],
        system.detail,
        system.blocker
      ]
        .filter(Boolean)
        .join(", ")}
      accessibilityHint={system.actionLabel}
      onPress={system.onPress}
      style={({ pressed }) => [styles.systemRow, last ? null : styles.systemRowBorder, pressed ? styles.pressed : null]}
    >
      <View style={styles.rowIcon}>
        <Ionicons name={system.icon} size={16} color={color} />
      </View>
      <View style={styles.rowBody}>
        <View style={styles.rowLabelLine}>
          <Text style={styles.rowLabel}>{system.label}</Text>
          <View style={[styles.stateDot, { backgroundColor: color }]} />
          <Text style={[styles.stateText, { color }]}>{SYSTEM_STATE_LABELS[system.state]}</Text>
        </View>
        {system.detail ? <Text style={styles.rowValue}>{system.detail}</Text> : null}
        {system.blocker ? (
          <Text style={styles.systemBlocker}>
            <Ionicons name="alert-circle-outline" size={11} color={palette.warning} /> {system.blocker}
          </Text>
        ) : null}
        <Text style={styles.systemAction}>{system.actionLabel}</Text>
      </View>
      <Ionicons name="chevron-forward" size={16} color={palette.textDim} />
    </Pressable>
  );
}

/* ================================================================= editors */

export type EditorKind = "text" | "multiline" | "handle" | "choice" | "url";

export type EditorChoice = { key: string; label: string; description?: string };

/**
 * A focused single-field editor in a sheet.
 *
 * Focused rather than inline because the brief's fields are not equal: a handle
 * change has a cooldown, a redirect window and an availability check; a tagline has
 * a character limit. Inline rows have room for a value and nothing else, which is how
 * the old screen ended up with a "Contact" line reading `me@x.com · +353…` squeezed
 * into one truncated row with no way to set the visibility of either half.
 *
 * The sheet owns its own draft. `onSave` receives the value only when the user
 * commits, so backing out of an editor cannot leave a half-typed value in the
 * screen's dirty set.
 */
export function FieldEditorSheet({
  visible,
  title,
  description,
  kind,
  initialValue,
  placeholder,
  helpText,
  choices,
  maxLength,
  reviewWarning,
  blocked,
  error,
  busy,
  previewLabel,
  previewValue,
  onCheck,
  checkResult,
  onSave,
  onClose,
  extra
}: {
  visible: boolean;
  title: string;
  description?: string;
  kind: EditorKind;
  initialValue: string;
  placeholder?: string;
  helpText?: string;
  choices?: EditorChoice[];
  maxLength?: number;
  /** Shown when saving this field will queue it for a reviewer. Not a lock. */
  reviewWarning?: string;
  /** Shown, with save disabled, when enforcement genuinely forbids the change. */
  blocked?: string;
  error?: string;
  busy?: boolean;
  previewLabel?: string;
  previewValue?: (value: string) => string;
  onCheck?: (value: string) => void;
  checkResult?: { available: boolean; reason: string } | null;
  onSave: (value: string) => void;
  onClose: () => void;
  extra?: ReactNode;
}) {
  const [value, setValue] = useState(initialValue);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (visible) setValue(initialValue);
  }, [visible, initialValue]);

  useEffect(() => {
    if (!onCheck || !visible) return undefined;
    // Debounced. An availability check on every keystroke would ask the server
    // whether "h", "ha", "har"… are free, and the answer for a three-character
    // prefix is never the answer the seller is waiting for.
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => onCheck(value), 400);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, visible]);

  const dirty = value !== initialValue;
  const canSave = dirty && !blocked && !busy && (!checkResult || checkResult.available);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.sheetBackdrop}>
        <Pressable style={StyleSheet.absoluteFill} accessibilityLabel="Close editor" onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.sheetHandle} />
          <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.sheetBody}>
            <Text style={styles.sheetTitle}>{title}</Text>
            {description ? <Text style={styles.sheetDescription}>{description}</Text> : null}

            {blocked ? (
              <View style={styles.warnBox}>
                <Ionicons name="lock-closed-outline" size={14} color={palette.warning} />
                <Text style={styles.warnText}>{blocked}</Text>
              </View>
            ) : reviewWarning ? (
              <View style={styles.warnBox}>
                <Ionicons name="shield-checkmark-outline" size={14} color={palette.warning} />
                <Text style={styles.warnText}>{reviewWarning}</Text>
              </View>
            ) : null}

            {kind === "choice" ? (
              <View style={styles.choiceList}>
                {(choices || []).map((choice) => {
                  const selected = choice.key === value;
                  return (
                    <Pressable
                      key={choice.key}
                      accessibilityRole="radio"
                      accessibilityState={{ selected }}
                      accessibilityLabel={choice.label}
                      onPress={() => setValue(choice.key)}
                      style={({ pressed }) => [
                        styles.choice,
                        selected ? styles.choiceSelected : null,
                        pressed ? styles.pressed : null
                      ]}
                    >
                      <Ionicons
                        name={selected ? "radio-button-on" : "radio-button-off"}
                        size={17}
                        color={selected ? palette.accent : palette.textDim}
                      />
                      <View style={styles.choiceCopy}>
                        <Text style={[styles.choiceLabel, selected ? styles.choiceLabelSelected : null]}>
                          {choice.label}
                        </Text>
                        {choice.description ? <Text style={styles.choiceDescription}>{choice.description}</Text> : null}
                      </View>
                    </Pressable>
                  );
                })}
              </View>
            ) : (
              <View>
                {kind === "handle" ? <Text style={styles.inputPrefix}>@</Text> : null}
                <TextInput
                  accessibilityLabel={title}
                  value={value}
                  onChangeText={setValue}
                  placeholder={placeholder}
                  placeholderTextColor={palette.textDim}
                  editable={!blocked && !busy}
                  multiline={kind === "multiline"}
                  numberOfLines={kind === "multiline" ? 5 : 1}
                  maxLength={maxLength}
                  autoCapitalize={kind === "handle" || kind === "url" ? "none" : "sentences"}
                  autoCorrect={kind !== "handle" && kind !== "url"}
                  keyboardType={kind === "url" ? "url" : "default"}
                  style={[
                    styles.input,
                    kind === "multiline" ? styles.inputMultiline : null,
                    kind === "handle" ? styles.inputHandle : null
                  ]}
                />
                {maxLength ? (
                  <Text style={styles.counter}>
                    {value.length}/{maxLength}
                  </Text>
                ) : null}
              </View>
            )}

            {checkResult ? (
              <Text style={[styles.checkLine, checkResult.available ? styles.checkOk : styles.checkBad]}>
                {checkResult.reason}
              </Text>
            ) : null}
            {error ? <Text style={styles.errorLine}>{error}</Text> : null}
            {helpText ? <Text style={styles.helpText}>{helpText}</Text> : null}

            {previewLabel && previewValue ? (
              <View style={styles.previewBox}>
                <Text style={styles.previewLabel}>{previewLabel}</Text>
                <Text style={styles.previewValue}>{previewValue(value) || "—"}</Text>
              </View>
            ) : null}

            {extra}
          </ScrollView>

          <View style={styles.sheetFooter}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Cancel"
              onPress={onClose}
              style={({ pressed }) => [styles.ghostButton, pressed ? styles.pressed : null]}
            >
              <Text style={styles.ghostButtonLabel}>Cancel</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={busy ? "Saving" : "Save"}
              accessibilityState={{ disabled: !canSave }}
              onPress={canSave ? () => onSave(value) : undefined}
              style={({ pressed }) => [
                styles.primaryButton,
                styles.sheetSave,
                !canSave ? styles.dim : null,
                pressed && canSave ? styles.pressed : null
              ]}
            >
              {busy ? (
                <ActivityIndicator color={palette.background} size="small" />
              ) : (
                <Text style={styles.primaryButtonLabel}>Save</Text>
              )}
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

/* ------------------------------------------------------- visibility picker */

/**
 * Who can see a contact channel.
 *
 * Every contact field gets one, and the default is `private`, because publishing a
 * phone number is the kind of decision that has to be taken rather than inherited.
 */
export function VisibilityPicker({
  value,
  onChange,
  label
}: {
  value: string;
  onChange: (next: string) => void;
  label: string;
}) {
  const options = [
    { key: "private", label: "Private", description: "Only you and support see this." },
    { key: "after_purchase", label: "Visible after purchase", description: "Shown once a buyer has paid." },
    { key: "public", label: "Visible to all buyers", description: "Shown on your public profile." }
  ];
  return (
    <View style={styles.visibility}>
      <Text style={styles.visibilityLabel}>{label}</Text>
      {options.map((option) => {
        const selected = option.key === value;
        return (
          <Pressable
            key={option.key}
            accessibilityRole="radio"
            accessibilityState={{ selected }}
            accessibilityLabel={`${label}: ${option.label}`}
            onPress={() => onChange(option.key)}
            style={({ pressed }) => [styles.choice, selected ? styles.choiceSelected : null, pressed ? styles.pressed : null]}
          >
            <Ionicons
              name={selected ? "radio-button-on" : "radio-button-off"}
              size={17}
              color={selected ? palette.accent : palette.textDim}
            />
            <View style={styles.choiceCopy}>
              <Text style={[styles.choiceLabel, selected ? styles.choiceLabelSelected : null]}>{option.label}</Text>
              <Text style={styles.choiceDescription}>{option.description}</Text>
            </View>
          </Pressable>
        );
      })}
    </View>
  );
}

/* ------------------------------------------------------------ hours editor */

export type HoursDayDraft = { weekday: string; label: string; state: string; opens: string; closes: string };

/**
 * The opening-hours editor.
 *
 * The screen it replaces printed "Opening hours aren't stored yet — this field is
 * coming", an implementation note in a production surface. Three states per day, not
 * two, because a day with no answer is different from a day that is closed: the buyer
 * view says "Hours not provided" for the first and "Closed today" for the second.
 */
export function HoursEditorSheet({
  visible,
  mode,
  days,
  busy,
  error,
  onChangeMode,
  onChangeDay,
  onSave,
  onClose
}: {
  visible: boolean;
  mode: string;
  days: HoursDayDraft[];
  busy?: boolean;
  error?: string;
  onChangeMode: (mode: string) => void;
  onChangeDay: (weekday: string, patch: Partial<HoursDayDraft>) => void;
  onSave: () => void;
  onClose: () => void;
}) {
  const modes = [
    { key: "weekly", label: "Set weekly hours" },
    { key: "by_appointment", label: "By appointment only" },
    { key: "temporarily_closed", label: "Temporarily closed" },
    { key: "unset", label: "Don't show hours" }
  ];

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.sheetBackdrop}>
        <Pressable style={StyleSheet.absoluteFill} accessibilityLabel="Close editor" onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.sheetHandle} />
          <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.sheetBody}>
            <Text style={styles.sheetTitle}>Opening hours</Text>
            <Text style={styles.sheetDescription}>
              Buyers see “Open now” or “Closes at 7:00 PM”. Leave a day unset and they see nothing for it rather
              than a wrong answer.
            </Text>

            <View style={styles.choiceList}>
              {modes.map((option) => {
                const selected = option.key === mode;
                return (
                  <Pressable
                    key={option.key}
                    accessibilityRole="radio"
                    accessibilityState={{ selected }}
                    accessibilityLabel={option.label}
                    onPress={() => onChangeMode(option.key)}
                    style={({ pressed }) => [
                      styles.choice,
                      selected ? styles.choiceSelected : null,
                      pressed ? styles.pressed : null
                    ]}
                  >
                    <Ionicons
                      name={selected ? "radio-button-on" : "radio-button-off"}
                      size={17}
                      color={selected ? palette.accent : palette.textDim}
                    />
                    <Text style={[styles.choiceLabel, selected ? styles.choiceLabelSelected : null]}>
                      {option.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            {mode === "weekly"
              ? days.map((day) => (
                  <View key={day.weekday} style={styles.dayRow}>
                    <Text style={styles.dayLabel}>{day.label}</Text>
                    <Switch
                      accessibilityLabel={`${day.label} open`}
                      value={day.state === "open"}
                      onValueChange={(open) =>
                        onChangeDay(day.weekday, {
                          state: open ? "open" : "closed",
                          opens: open && !day.opens ? "09:00" : day.opens,
                          closes: open && !day.closes ? "17:00" : day.closes
                        })
                      }
                      trackColor={{ false: palette.panelStrong, true: palette.accentSoft }}
                      thumbColor={day.state === "open" ? palette.accent : palette.textDim}
                    />
                    {day.state === "open" ? (
                      <View style={styles.timeGroup}>
                        <TextInput
                          accessibilityLabel={`${day.label} opens at`}
                          value={day.opens}
                          onChangeText={(text) => onChangeDay(day.weekday, { opens: text })}
                          placeholder="09:00"
                          placeholderTextColor={palette.textDim}
                          style={styles.timeInput}
                          maxLength={5}
                        />
                        <Text style={styles.timeDash}>–</Text>
                        <TextInput
                          accessibilityLabel={`${day.label} closes at`}
                          value={day.closes}
                          onChangeText={(text) => onChangeDay(day.weekday, { closes: text })}
                          placeholder="17:00"
                          placeholderTextColor={palette.textDim}
                          style={styles.timeInput}
                          maxLength={5}
                        />
                      </View>
                    ) : (
                      <Text style={styles.dayState}>{day.state === "closed" ? "Closed" : "Not set"}</Text>
                    )}
                  </View>
                ))
              : null}

            {error ? <Text style={styles.errorLine}>{error}</Text> : null}
          </ScrollView>

          <View style={styles.sheetFooter}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Cancel"
              onPress={onClose}
              style={({ pressed }) => [styles.ghostButton, pressed ? styles.pressed : null]}
            >
              <Text style={styles.ghostButtonLabel}>Cancel</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={busy ? "Saving" : "Save hours"}
              accessibilityState={{ disabled: Boolean(busy) }}
              onPress={busy ? undefined : onSave}
              style={({ pressed }) => [styles.primaryButton, styles.sheetSave, pressed ? styles.pressed : null]}
            >
              {busy ? (
                <ActivityIndicator color={palette.background} size="small" />
              ) : (
                <Text style={styles.primaryButtonLabel}>Save hours</Text>
              )}
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

/* ----------------------------------------------------------- sync sheet */

/**
 * What "Live sync" actually means, spelled out.
 *
 * The badge on the old screen pulsed green permanently. A permanent green light next
 * to unsaved form edits tells a seller their draft is already public, which is both
 * false and the most expensive kind of false: they stop editing because they think
 * they are done.
 */
export function SyncStatusSheet({
  visible,
  stateLabel,
  explainer,
  publishedAt,
  freshness,
  reviewProtected,
  blocked,
  readyCount,
  totalSystems,
  busy,
  onPublish,
  onClose
}: {
  visible: boolean;
  stateLabel: string;
  explainer: string;
  publishedAt: string;
  freshness: string;
  reviewProtected: string[];
  blocked: string[];
  readyCount: number;
  totalSystems: number;
  busy?: boolean;
  onPublish?: () => void;
  onClose: () => void;
}) {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.sheetBackdrop}>
        <Pressable style={StyleSheet.absoluteFill} accessibilityLabel="Close" onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.sheetHandle} />
          <ScrollView contentContainerStyle={styles.sheetBody}>
            <Text style={styles.sheetTitle}>Live sync · {stateLabel}</Text>
            <Text style={styles.sheetDescription}>{explainer}</Text>

            <SyncLine label="Last published" value={publishedAt} />
            <SyncLine label="Public profile" value={freshness} />
            <SyncLine
              label="Protected fields"
              value={
                reviewProtected.length
                  ? `${reviewProtected.length} need a review when changed`
                  : "None — every field saves straight away"
              }
            />
            {blocked.length ? <SyncLine label="Blocked fields" value={blocked.join(", ")} tone="warn" /> : null}
            <SyncLine label="Connected systems" value={`${readyCount} of ${totalSystems} ready`} />
          </ScrollView>

          <View style={styles.sheetFooter}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Close"
              onPress={onClose}
              style={({ pressed }) => [styles.ghostButton, pressed ? styles.pressed : null]}
            >
              <Text style={styles.ghostButtonLabel}>Close</Text>
            </Pressable>
            {onPublish ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={busy ? "Publishing" : "Publish changes"}
                accessibilityState={{ disabled: Boolean(busy) }}
                onPress={busy ? undefined : onPublish}
                style={({ pressed }) => [styles.primaryButton, styles.sheetSave, pressed ? styles.pressed : null]}
              >
                {busy ? (
                  <ActivityIndicator color={palette.background} size="small" />
                ) : (
                  <Text style={styles.primaryButtonLabel}>Publish changes</Text>
                )}
              </Pressable>
            ) : null}
          </View>
        </View>
      </View>
    </Modal>
  );
}

function SyncLine({ label, value, tone }: { label: string; value: string; tone?: "warn" }) {
  return (
    <View style={styles.syncLine}>
      <Text style={styles.syncLabel}>{label}</Text>
      <Text style={[styles.syncValue, tone === "warn" ? styles.syncWarn : null]}>{value}</Text>
    </View>
  );
}

/* ------------------------------------------------------ verification strip */

/**
 * Verification as one compact row.
 *
 * The old screen gave verification a full-width promotional card with a shimmering
 * border, which is how a status ended up looking like an advertisement — and how the
 * same screen managed to show "application in review" above "Verification · Approved"
 * without either one looking wrong. One row, one state, one source.
 */
export function VerificationStrip({
  stateLabel,
  body,
  tone,
  onPress
}: {
  stateLabel: string;
  body: string;
  tone: "good" | "warn" | "idle";
  onPress: () => void;
}) {
  const color = tone === "good" ? palette.accent : tone === "warn" ? palette.warning : palette.textDim;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`Verification: ${stateLabel}. ${body}`}
      accessibilityHint="Opens the verification centre"
      onPress={onPress}
      style={({ pressed }) => [styles.verificationRow, pressed ? styles.pressed : null]}
    >
      <Ionicons name="shield-checkmark-outline" size={17} color={color} />
      <View style={styles.rowBody}>
        <Text style={styles.rowLabel}>Verification · {stateLabel}</Text>
        <Text style={styles.rowNote}>{body}</Text>
      </View>
      <Ionicons name="chevron-forward" size={16} color={palette.textDim} />
    </Pressable>
  );
}

/* ------------------------------------------------------------- empty state */

export function EmptyValue({ prompt }: { prompt: string }) {
  return <Text style={styles.emptyValue}>{prompt}</Text>;
}

export function Panel({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  return <View style={[styles.panel, style]}>{children}</View>;
}

export function useSectionKeys<T extends { key: string }>(items: T[]) {
  return useMemo(() => items.map((item) => item.key), [items]);
}

/* ================================================================== styles */

const styles = StyleSheet.create({
  pressed: { opacity: 0.72 },
  dim: { opacity: 0.45 },

  panel: {
    backgroundColor: palette.panel,
    borderRadius: radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline,
    overflow: "hidden"
  },

  /* rows */
  row: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md + 2,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.hairline
  },
  rowIcon: { width: 26, alignItems: "center", paddingTop: 2 },
  rowBody: { flex: 1, gap: 3 },
  rowLabelLine: { flexDirection: "row", alignItems: "center", gap: spacing.sm, flexWrap: "wrap" },
  rowLabel: { ...typography.label, color: palette.textMuted },
  rowValue: { ...typography.body, color: palette.textPrimary },
  rowEmpty: { ...typography.body, color: palette.textDim, fontStyle: "italic" },
  rowNote: { ...typography.metadata, color: palette.textDim },

  chip: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.capsule
  },
  chipText: { ...typography.metadata, fontSize: 10, letterSpacing: 0.4, textTransform: "uppercase" },

  /* completeness */
  completeness: { padding: spacing.lg, gap: spacing.md },
  completenessHead: { flexDirection: "row", alignItems: "center", gap: spacing.lg },
  completenessFigure: { alignItems: "center", minWidth: 68 },
  completenessPercent: { ...typography.display, color: palette.accent },
  completenessCaption: { ...typography.metadata, color: palette.textDim },
  completenessCopy: { flex: 1, gap: 3 },
  completenessTitle: { ...typography.sectionTitle, color: palette.textPrimary },
  completenessBody: { ...typography.metadata, color: palette.textMuted },

  trackOuter: {
    height: 6,
    borderRadius: radius.capsule,
    backgroundColor: palette.panelStrong,
    overflow: "hidden"
  },
  trackFill: { height: 6, borderRadius: radius.capsule, backgroundColor: palette.accent },

  itemBlock: { gap: 2 },
  itemHeading: {
    ...typography.metadata,
    color: palette.textDim,
    textTransform: "uppercase",
    letterSpacing: 0.6,
    marginTop: spacing.sm
  },
  disclosure: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  item: { flexDirection: "row", alignItems: "center", gap: spacing.sm, paddingVertical: spacing.sm },
  itemLabel: { ...typography.body, color: palette.textPrimary, flex: 1 },
  itemLabelDone: { color: palette.textMuted },

  primaryButton: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.capsule,
    backgroundColor: palette.accent,
    marginTop: spacing.sm
  },
  primaryButtonLabel: { ...typography.button, color: palette.background },

  /* reputation */
  grid: { flexDirection: "row", flexWrap: "wrap" },
  gridCell: {
    width: "33.333%",
    paddingVertical: spacing.lg,
    paddingHorizontal: spacing.md,
    alignItems: "center",
    gap: 4,
    borderRightWidth: StyleSheet.hairlineWidth,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline
  },
  gridValue: { ...typography.metric, color: palette.textPrimary, textAlign: "center" },
  gridEmpty: { ...typography.metadata, color: palette.textDim, textAlign: "center", minHeight: 28 },
  gridLabel: { ...typography.metadata, color: palette.textMuted, textAlign: "center" },

  /* connected */
  systemRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md + 2
  },
  systemRowBorder: { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: palette.hairline },
  stateDot: { width: 6, height: 6, borderRadius: 3 },
  stateText: { ...typography.metadata, fontSize: 11 },
  systemBlocker: { ...typography.metadata, color: palette.warning },
  systemAction: { ...typography.metadata, color: palette.secondary },

  /* sheets */
  sheetBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.62)", justifyContent: "flex-end" },
  sheet: {
    maxHeight: "88%",
    backgroundColor: palette.panelRaised,
    borderTopLeftRadius: radius.panel,
    borderTopRightRadius: radius.panel,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairlineStrong
  },
  sheetHandle: {
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: palette.hairlineStrong,
    alignSelf: "center",
    marginTop: spacing.md
  },
  sheetBody: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xl },
  sheetTitle: { ...typography.title, color: palette.textPrimary },
  sheetDescription: { ...typography.metadata, color: palette.textMuted, lineHeight: 18 },
  sheetFooter: {
    flexDirection: "row",
    gap: spacing.md,
    padding: spacing.lg,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.hairline
  },
  sheetSave: { flex: 1, marginTop: 0 },

  ghostButton: {
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.xl,
    borderRadius: radius.capsule,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairlineStrong,
    alignItems: "center",
    justifyContent: "center"
  },
  ghostButtonLabel: { ...typography.button, color: palette.textMuted },

  input: {
    ...typography.body,
    color: palette.textPrimary,
    backgroundColor: palette.panelStrong,
    borderRadius: radius.medium,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairlineStrong,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    minHeight: 46
  },
  inputMultiline: { minHeight: 118, textAlignVertical: "top" },
  inputHandle: { paddingLeft: spacing.xl + 4 },
  inputPrefix: {
    ...typography.body,
    color: palette.textDim,
    position: "absolute",
    left: spacing.md,
    top: 13,
    zIndex: 2
  },
  counter: { ...typography.metadata, color: palette.textDim, alignSelf: "flex-end", marginTop: 4 },

  checkLine: { ...typography.metadata },
  checkOk: { color: palette.accent },
  checkBad: { color: palette.warning },
  errorLine: { ...typography.metadata, color: palette.warning },
  helpText: { ...typography.metadata, color: palette.textDim, lineHeight: 17 },

  warnBox: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radius.medium,
    backgroundColor: palette.warningSoft
  },
  warnText: { ...typography.metadata, color: palette.warning, flex: 1, lineHeight: 17 },

  previewBox: {
    padding: spacing.md,
    borderRadius: radius.medium,
    backgroundColor: palette.panelStrong,
    gap: 3
  },
  previewLabel: {
    ...typography.metadata,
    color: palette.textDim,
    textTransform: "uppercase",
    letterSpacing: 0.6
  },
  previewValue: { ...typography.body, color: palette.textPrimary },

  choiceList: { gap: spacing.sm },
  choice: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.md,
    padding: spacing.md,
    borderRadius: radius.medium,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.hairline
  },
  choiceSelected: { borderColor: palette.accent, backgroundColor: palette.accentSoft },
  choiceCopy: { flex: 1, gap: 2 },
  choiceLabel: { ...typography.body, color: palette.textMuted },
  choiceLabelSelected: { color: palette.textPrimary },
  choiceDescription: { ...typography.metadata, color: palette.textDim },

  visibility: { gap: spacing.sm, marginTop: spacing.sm },
  visibilityLabel: {
    ...typography.metadata,
    color: palette.textDim,
    textTransform: "uppercase",
    letterSpacing: 0.6
  },

  dayRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.hairline
  },
  dayLabel: { ...typography.body, color: palette.textPrimary, width: 44 },
  dayState: { ...typography.metadata, color: palette.textDim, flex: 1, textAlign: "right" },
  timeGroup: { flexDirection: "row", alignItems: "center", gap: spacing.sm, flex: 1, justifyContent: "flex-end" },
  timeInput: {
    ...typography.body,
    color: palette.textPrimary,
    backgroundColor: palette.panelStrong,
    borderRadius: radius.small,
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    width: 66,
    textAlign: "center"
  },
  timeDash: { ...typography.body, color: palette.textDim },

  syncLine: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.hairline
  },
  syncLabel: { ...typography.metadata, color: palette.textDim },
  syncValue: { ...typography.body, color: palette.textPrimary, flex: 1, textAlign: "right" },
  syncWarn: { color: palette.warning },

  verificationRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md + 2
  },

  emptyValue: { ...typography.body, color: palette.textDim, fontStyle: "italic" }
});
