/**
 * One row of the money ledger, in five variants plus an honest fallback.
 *
 * Every row here is a real backend transaction record with its own stable id.
 * There is no synthetic row, no "opening balance" the client invented, no
 * grouping placeholder that looks like a transaction. If the server did not
 * return it, it does not appear.
 *
 * The sign rule, which is the whole reason this component is careful
 * ------------------------------------------------------------------
 * `entry.sign` is decided server-side and this component obeys it without
 * interpretation:
 *
 *   `+`     income, green, prefixed with a plus
 *   `-`     an outflow, neutral ink, prefixed with U+2212 (a real minus, not a
 *           hyphen — a hyphen in a proportional-adjacent context reads as a
 *           dash and is narrower than the plus it must align with)
 *   `none`  **unsigned**, for two different situations
 *
 * The second `none` case is the one that matters. An escrow hold is the
 * seller's money, waiting for release. Rendering it as `−$40.00` tells them
 * they lost forty dollars, and they will believe it, because every other minus
 * on the screen means exactly that. So a hold renders unsigned, in violet, with
 * the word "held" beside it, and announces to a screen reader as "held, still
 * yours". Three independent signals, none of them colour alone.
 *
 * The other `none` case is an entry type the server did not recognise. It gets
 * no sign either, for a related reason: a guessed direction on someone's money
 * is worse than an unlabelled figure.
 *
 * Failed and reversed rows stay visible with their real status word. A
 * transaction that quietly disappears once it fails is how a seller ends up
 * unable to explain their own balance.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { LEDGER_KIND_COLOR, LEDGER_KIND_WORD, paymentsLight } from "../../theme/paymentsLight";
import type { LedgerEntry, LedgerKind } from "../../api/paymentsHub";
import { describeEntryForAccessibility, formatSignedAmount } from "../../api/paymentsHub";

/** Statuses that mean the entry did not land. Shown, never hidden. */
const TROUBLED = new Set(["failed", "reversed", "canceled", "cancelled", "disputed"]);

/** A short glyph per kind. Paired with a word always — never the only signal. */
const KIND_GLYPH: Record<LedgerKind, string> = {
  income: "↓",
  spend: "↑",
  escrow: "◧",
  payout: "→",
  refund: "↩",
  other: "•"
};

export type LedgerRowProps = {
  entry: LedgerEntry;
  /** A 0 → 1 driver from `usePaymentsRowInsert` for a row that just arrived. */
  entrance?: Animated.Value | null;
  onPress?: (entry: LedgerEntry) => void;
};

export function LedgerRow({ entry, entrance = null, onPress }: LedgerRowProps) {
  const kind: LedgerKind = entry.kind in LEDGER_KIND_COLOR ? entry.kind : "other";
  const palette = LEDGER_KIND_COLOR[kind];
  const troubled = TROUBLED.has(String(entry.status || "").toLowerCase());

  // The meta line: the human status word, then the linked reference if there is
  // one. Both are text, so neither depends on the icon's colour to be read.
  const metaParts = [
    statusWord(entry.status),
    entry.reference ? `${referenceLabel(entry.reference.type)} ${entry.reference.id}` : null
  ].filter(Boolean);

  const enter = entrance
    ? {
        opacity: entrance,
        transform: [
          { translateY: entrance.interpolate({ inputRange: [0, 1], outputRange: [-12, 0] }) }
        ]
      }
    : undefined;

  const inner = (
    <View style={styles.row}>
      <View
        style={[
          styles.circle,
          { backgroundColor: palette.circleBg, borderColor: palette.circleBorder }
        ]}
      >
        <Text style={[styles.glyph, { color: palette.amount }]} allowFontScaling={false}>
          {KIND_GLYPH[kind]}
        </Text>
      </View>

      <View style={styles.body}>
        <Text style={styles.title} allowFontScaling numberOfLines={2}>
          {entry.title || LEDGER_KIND_WORD[kind]}
        </Text>
        {metaParts.length ? (
          <Text
            style={[styles.meta, troubled && styles.metaTroubled]}
            allowFontScaling
            numberOfLines={2}
          >
            {metaParts.join(" · ")}
          </Text>
        ) : null}
      </View>

      <View style={styles.amountColumn}>
        <Text
          style={[styles.amount, { color: palette.amount }]}
          allowFontScaling
          adjustsFontSizeToFit
          numberOfLines={1}
        >
          {formatSignedAmount(entry)}
        </Text>
        {/* The word that carries what the colour carries. "Held" is not
            decoration here — it is the difference between a seller believing
            this money is waiting and believing it is gone. */}
        {kind === "escrow" ? (
          <Text style={[styles.heldWord, { color: palette.amount }]} allowFontScaling>
            held
          </Text>
        ) : null}
      </View>
    </View>
  );

  const content = enter ? <Animated.View style={enter}>{inner}</Animated.View> : inner;
  const label = describeEntryForAccessibility(entry);

  if (!onPress) {
    return (
      <View accessible accessibilityLabel={label}>
        {content}
      </View>
    );
  }

  return (
    <Pressable
      onPress={() => onPress(entry)}
      accessible
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint="Opens this transaction"
      style={({ pressed }) => (pressed ? styles.pressed : undefined)}
    >
      {content}
    </Pressable>
  );
}

/**
 * The server's status token as a word a seller would use.
 *
 * Unmapped statuses fall through to the raw token rather than to a friendly
 * guess. An unfamiliar word is a small confusion; a wrong familiar word is a
 * confident lie about a transaction.
 */
function statusWord(status: string): string {
  const key = String(status || "").toLowerCase();
  const words: Record<string, string> = {
    posted: "Completed",
    available: "Completed",
    pending: "Pending",
    processing: "Processing",
    failed: "Failed",
    reversed: "Reversed",
    canceled: "Canceled",
    cancelled: "Canceled",
    disputed: "Disputed",
    paid: "Paid",
    in_transit: "On the way to your bank"
  };
  return words[key] || status || "";
}

function referenceLabel(type: string): string {
  const key = String(type || "").toLowerCase();
  const words: Record<string, string> = {
    order: "Order",
    transaction: "Transaction",
    payout: "Payout",
    refund: "Refund",
    campaign: "Campaign"
  };
  return words[key] || type || "Ref";
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
    paddingHorizontal: paymentsLight.space.gutter,
    // The row itself is the tap target and clears the 44pt minimum on its own,
    // so nothing here depends on hitSlop to be reachable.
    minHeight: paymentsLight.size.tapTarget
  },
  pressed: {
    opacity: 0.6
  },
  circle: {
    width: paymentsLight.size.iconCircle,
    height: paymentsLight.size.iconCircle,
    borderRadius: paymentsLight.radius.iconCircle,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center"
  },
  glyph: {
    fontSize: 15,
    fontWeight: "700"
  },
  body: {
    flex: 1
  },
  title: {
    color: paymentsLight.text.primary,
    fontSize: 14,
    fontWeight: "600"
  },
  meta: {
    marginTop: 2,
    color: paymentsLight.ledger.meta,
    fontSize: 12
  },
  metaTroubled: {
    color: paymentsLight.ledger.failedStatus,
    fontWeight: "600"
  },
  amountColumn: {
    alignItems: "flex-end",
    minWidth: 86
  },
  amount: {
    fontSize: paymentsLight.money.row.fontSize,
    fontWeight: paymentsLight.money.row.fontWeight,
    fontVariant: ["tabular-nums"]
  },
  heldWord: {
    marginTop: 1,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 0.3
  }
});
