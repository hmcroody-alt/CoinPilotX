/**
 * The vault chrome — the shared parts of every money layer.
 *
 * Why these are here and not in `components/payments`
 * --------------------------------------------------
 * That barrel is the hub's, and the hub is a light surface. These are the dark
 * ones. Putting a dark card into `components/payments` would mean every
 * component in there needed a theme prop, which is a refactor of a working
 * screen in service of a new one — the thing this mission's own rule ("improve
 * the existing payments system, do not rebuild it") exists to prevent.
 *
 * Every string arrives as a prop
 * ------------------------------
 * Nothing in this file writes English. Not a label, not an accessibility hint,
 * not an empty-state sentence. The layers translate and pass down. That is
 * partly the hardcoded-string gate and partly that a component which knows a
 * sentence has quietly decided what the screen is about.
 *
 * Colour is never the only signal
 * -------------------------------
 * `MoneyChip` takes a tone *and* a label; `MoneyFigure` takes a tone and never
 * uses it for the label. A seller who cannot distinguish gold from green must
 * lose nothing, which on a money screen is not a nicety — the gold/green split
 * is exactly the "this is the number" / "this state is good" distinction, and
 * both halves are said in words.
 */

import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { MONEY_HEADER_GRADIENT, moneyTheme, type MoneyTone } from "../../theme/moneyTheme";

/* ------------------------------------------------------------------ *
 * Header
 * ------------------------------------------------------------------ */

export type MoneyHeaderProps = {
  title: string;
  /** One sentence under the title. Optional — a layer with nothing to add omits it. */
  subtitle?: string;
  onBack?: () => void;
  /** Translated. The back control is a real control and needs a real name. */
  backLabel: string;
  /** The layer's headline figure, rendered inside the gradient. */
  children?: ReactNode;
};

/**
 * The gradient header every layer opens with.
 *
 * It is the hub's dark navy header, expanded — which is the whole reason the
 * transition into a dark layer does not read as a different app. The two stops
 * give the eye a horizon so the seller can tell where the header ends and the
 * scrolling body begins.
 */
export function MoneyHeader({ title, subtitle, onBack, backLabel, children }: MoneyHeaderProps) {
  const insets = useSafeAreaInsets();
  return (
    <LinearGradient
      colors={MONEY_HEADER_GRADIENT}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={[styles.header, { paddingTop: insets.top + 10 }]}
    >
      <View style={styles.headerRow}>
        <Pressable
          onPress={onBack}
          style={styles.back}
          accessibilityRole="button"
          accessibilityLabel={backLabel}
          hitSlop={10}
        >
          <Ionicons name="chevron-back" size={22} color={moneyTheme.text.primary} />
        </Pressable>
        <Text
          style={styles.headerTitle}
          allowFontScaling
          numberOfLines={2}
          ellipsizeMode="tail"
          maxFontSizeMultiplier={1.5}
          accessibilityRole="header"
        >
          {title}
        </Text>
        {/* Balances the back control so the title stays optically centred. */}
        <View style={styles.back} />
      </View>
      {subtitle ? (
        <Text style={styles.headerSubtitle} allowFontScaling numberOfLines={3}>
          {subtitle}
        </Text>
      ) : null}
      {children}
    </LinearGradient>
  );
}

/* ------------------------------------------------------------------ *
 * Cards and sections
 * ------------------------------------------------------------------ */

/**
 * `gold` for the one card that is the point of the screen, `green` for a card
 * whose subject is healthy, `plain` for everything else.
 *
 * At most one gold card per layer. Gold used twice stops meaning "this is the
 * number" and starts meaning "this app likes gold".
 */
export type MoneyAccent = "plain" | "gold" | "green";

export type MoneyCardProps = {
  accent?: MoneyAccent;
  onPress?: () => void;
  accessibilityLabel?: string;
  accessibilityHint?: string;
  children: ReactNode;
};

export function MoneyCard({
  accent = "plain",
  onPress,
  accessibilityLabel,
  accessibilityHint,
  children
}: MoneyCardProps) {
  const style = [
    styles.card,
    accent === "gold" && styles.cardGold,
    accent === "green" && styles.cardGreen
  ];
  if (!onPress) {
    return (
      <View style={style} accessible={Boolean(accessibilityLabel)} accessibilityLabel={accessibilityLabel}>
        {children}
      </View>
    );
  }
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityHint={accessibilityHint}
      style={({ pressed }) => [...style, pressed && styles.pressed]}
    >
      {children}
    </Pressable>
  );
}

export function MoneySectionTitle({ children }: { children: string }) {
  return (
    <Text style={styles.sectionTitle} allowFontScaling accessibilityRole="header">
      {children}
    </Text>
  );
}

/** Explanatory prose. The sentences that make a figure honest. */
export function MoneyNote({ children }: { children: string }) {
  return (
    <Text style={styles.note} allowFontScaling>
      {children}
    </Text>
  );
}

/* ------------------------------------------------------------------ *
 * Figures
 * ------------------------------------------------------------------ */

export type MoneyFigureProps = {
  label: string;
  /** Already formatted by `formatMoney`. This component performs no arithmetic. */
  amount: string;
  size?: "hero" | "figure" | "row";
  /**
   * `gold` for an amount that is the point of the screen, `green` for one whose
   * *state* is what matters, `plain` otherwise. A figure is never both.
   */
  accent?: MoneyAccent;
  /** True when the figure could not be read — renders the dash muted, not red. */
  unavailable?: boolean;
};

export function MoneyFigure({
  label,
  amount,
  size = "figure",
  accent = "plain",
  unavailable = false
}: MoneyFigureProps) {
  const color = unavailable
    ? moneyTheme.unavailable
    : accent === "gold"
      ? moneyTheme.gold
      : accent === "green"
        ? moneyTheme.green
        : moneyTheme.text.primary;
  const type = size === "hero" ? styles.moneyHero : size === "row" ? styles.moneyRow : styles.moneyFigure;
  return (
    <View>
      <Text style={styles.figureLabel} allowFontScaling numberOfLines={2}>
        {label}
      </Text>
      <Text
        style={[type, { color }]}
        allowFontScaling
        adjustsFontSizeToFit
        numberOfLines={1}
        accessibilityLabel={`${label}, ${amount}`}
      >
        {amount}
      </Text>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Chips
 * ------------------------------------------------------------------ */

export type MoneyChipProps = {
  /** Translated, or the server's raw status word when no translation exists. */
  label: string;
  tone: MoneyTone;
};

export function MoneyChip({ label, tone }: MoneyChipProps) {
  const color = moneyTheme.tone[tone];
  return (
    <View style={[styles.chip, { borderColor: color }]}>
      <Text style={[styles.chipText, { color }]} allowFontScaling numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * List rows
 * ------------------------------------------------------------------ */

export type MoneyListRowProps = {
  title: string;
  /** Status word, timestamp, reference — whatever makes the row identifiable. */
  meta?: string;
  /** Already formatted, sign included. */
  amount?: string;
  amountAccent?: MoneyAccent;
  chip?: MoneyChipProps | null;
  onPress?: () => void;
  accessibilityLabel: string;
  accessibilityHint?: string;
};

export function MoneyListRow({
  title,
  meta,
  amount,
  amountAccent = "plain",
  chip,
  onPress,
  accessibilityLabel,
  accessibilityHint
}: MoneyListRowProps) {
  const amountColor =
    amountAccent === "gold"
      ? moneyTheme.gold
      : amountAccent === "green"
        ? moneyTheme.green
        : moneyTheme.text.primary;
  const body = (
    <View style={styles.row}>
      <View style={styles.rowBody}>
        <Text style={styles.rowTitle} allowFontScaling numberOfLines={2}>
          {title}
        </Text>
        {meta ? (
          <Text style={styles.rowMeta} allowFontScaling numberOfLines={2}>
            {meta}
          </Text>
        ) : null}
      </View>
      <View style={styles.rowTail}>
        {amount ? (
          <Text
            style={[styles.moneyRow, { color: amountColor }]}
            allowFontScaling
            adjustsFontSizeToFit
            numberOfLines={1}
          >
            {amount}
          </Text>
        ) : null}
        {chip ? <MoneyChip {...chip} /> : null}
      </View>
      {onPress ? (
        <Ionicons name="chevron-forward" size={16} color={moneyTheme.text.muted} />
      ) : null}
    </View>
  );

  if (!onPress) {
    return (
      <View accessible accessibilityLabel={accessibilityLabel}>
        {body}
      </View>
    );
  }
  return (
    <Pressable
      onPress={onPress}
      accessible
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityHint={accessibilityHint}
      style={({ pressed }) => (pressed ? styles.pressed : undefined)}
    >
      {body}
    </Pressable>
  );
}

/* ------------------------------------------------------------------ *
 * Actions
 * ------------------------------------------------------------------ */

export type MoneyActionProps = {
  label: string;
  onPress: () => void;
  /** `gold` is the layer's primary action. At most one per screen. */
  accent?: MoneyAccent;
  disabled?: boolean;
  accessibilityHint?: string;
};

export function MoneyAction({
  label,
  onPress,
  accent = "plain",
  disabled = false,
  accessibilityHint
}: MoneyActionProps) {
  const tint =
    accent === "gold" ? moneyTheme.gold : accent === "green" ? moneyTheme.green : moneyTheme.text.primary;
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled }}
      style={({ pressed }) => [
        styles.action,
        { borderColor: tint },
        pressed && styles.pressed,
        disabled && styles.actionDisabled
      ]}
    >
      <Text style={[styles.actionText, { color: tint }]} allowFontScaling numberOfLines={2}>
        {label}
      </Text>
    </Pressable>
  );
}

/* ------------------------------------------------------------------ *
 * States
 * ------------------------------------------------------------------ */

/**
 * Loading, empty and failed, in one component so a layer cannot ship two of
 * them that disagree about spacing — or, worse, ship an empty state that looks
 * like a failure. An empty history and an unreadable history are different
 * facts and this takes them as different `kind`s.
 */
export type MoneyStateProps = {
  kind: "loading" | "empty" | "error";
  title?: string;
  body?: string;
  actionLabel?: string;
  onAction?: () => void;
  /** Quotable when a read fails. Never invented — comes from `supportReferenceFor`. */
  supportReference?: string;
};

export function MoneyState({
  kind,
  title,
  body,
  actionLabel,
  onAction,
  supportReference
}: MoneyStateProps) {
  if (kind === "loading") {
    return (
      <View style={styles.state} accessible accessibilityLabel={title}>
        <ActivityIndicator color={moneyTheme.gold} />
        {title ? (
          <Text style={styles.stateBody} allowFontScaling>
            {title}
          </Text>
        ) : null}
      </View>
    );
  }
  return (
    <View style={styles.state}>
      {title ? (
        <Text style={styles.stateTitle} allowFontScaling accessibilityRole="header">
          {title}
        </Text>
      ) : null}
      {body ? (
        <Text style={styles.stateBody} allowFontScaling>
          {body}
        </Text>
      ) : null}
      {supportReference ? (
        <Text style={styles.stateReference} allowFontScaling selectable>
          {supportReference}
        </Text>
      ) : null}
      {actionLabel && onAction ? (
        <View style={styles.stateAction}>
          <MoneyAction label={actionLabel} onPress={onAction} accent={kind === "error" ? "gold" : "plain"} />
        </View>
      ) : null}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Styles
 * ------------------------------------------------------------------ */

const styles = StyleSheet.create({
  header: {
    paddingHorizontal: moneyTheme.space.gutter,
    paddingBottom: 18
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8
  },
  back: {
    width: moneyTheme.tapTarget,
    height: moneyTheme.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  headerTitle: {
    flex: 1,
    textAlign: "center",
    color: moneyTheme.text.primary,
    fontSize: 17,
    fontWeight: "700"
  },
  headerSubtitle: {
    marginTop: 8,
    color: moneyTheme.text.secondary,
    fontSize: 13,
    lineHeight: 18
  },
  card: {
    backgroundColor: moneyTheme.bg.card,
    borderRadius: moneyTheme.radius.card,
    borderWidth: 1,
    borderColor: moneyTheme.border.hairline,
    padding: moneyTheme.space.card,
    gap: 10
  },
  cardGold: {
    backgroundColor: moneyTheme.bg.cardRaised,
    borderColor: moneyTheme.border.gold
  },
  cardGreen: {
    borderColor: moneyTheme.border.green
  },
  pressed: {
    opacity: 0.65
  },
  sectionTitle: {
    color: moneyTheme.text.primary,
    fontSize: 15,
    fontWeight: "700",
    marginTop: moneyTheme.space.section,
    marginBottom: 10
  },
  note: {
    color: moneyTheme.text.secondary,
    fontSize: 13,
    lineHeight: 19
  },
  // The three money type sizes, restated as StyleSheet entries. `moneyTheme`
  // holds them as a frozen token block, and RN's TextStyle wants a mutable
  // `fontVariant` array — so the tokens supply the values and the array literal
  // lives here rather than the token file losing its `as const`.
  moneyHero: {
    fontSize: moneyTheme.money.hero.fontSize,
    fontWeight: moneyTheme.money.hero.fontWeight,
    letterSpacing: moneyTheme.money.hero.letterSpacing,
    fontVariant: ["tabular-nums"]
  },
  moneyFigure: {
    fontSize: moneyTheme.money.figure.fontSize,
    fontWeight: moneyTheme.money.figure.fontWeight,
    fontVariant: ["tabular-nums"]
  },
  moneyRow: {
    fontSize: moneyTheme.money.row.fontSize,
    fontWeight: moneyTheme.money.row.fontWeight,
    fontVariant: ["tabular-nums"]
  },
  figureLabel: {
    color: moneyTheme.text.muted,
    fontSize: 12,
    fontWeight: "600",
    letterSpacing: 0.2,
    marginBottom: 4
  },
  chip: {
    alignSelf: "flex-start",
    borderWidth: 1,
    borderRadius: moneyTheme.radius.chip,
    paddingHorizontal: 10,
    paddingVertical: 3
  },
  chipText: {
    fontSize: 11,
    fontWeight: "700"
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
    // The row is its own tap target and clears 44pt without hitSlop.
    minHeight: moneyTheme.tapTarget
  },
  rowBody: {
    flex: 1
  },
  rowTitle: {
    color: moneyTheme.text.primary,
    fontSize: 14,
    fontWeight: "600"
  },
  rowMeta: {
    marginTop: 3,
    color: moneyTheme.text.muted,
    fontSize: 12
  },
  rowTail: {
    alignItems: "flex-end",
    gap: 4
  },
  action: {
    minHeight: moneyTheme.tapTarget,
    borderWidth: 1,
    borderRadius: moneyTheme.radius.control,
    paddingHorizontal: 16,
    alignItems: "center",
    justifyContent: "center"
  },
  actionDisabled: {
    opacity: 0.45
  },
  actionText: {
    fontSize: 14,
    fontWeight: "700",
    textAlign: "center"
  },
  state: {
    alignItems: "center",
    gap: 10,
    paddingVertical: 32,
    paddingHorizontal: moneyTheme.space.gutter
  },
  stateTitle: {
    color: moneyTheme.text.primary,
    fontSize: 16,
    fontWeight: "700",
    textAlign: "center"
  },
  stateBody: {
    color: moneyTheme.text.secondary,
    fontSize: 13,
    lineHeight: 19,
    textAlign: "center"
  },
  stateReference: {
    color: moneyTheme.text.muted,
    fontSize: 12,
    fontVariant: ["tabular-nums"]
  },
  stateAction: {
    marginTop: 6,
    minWidth: 180
  }
});
