/**
 * Where the seller's money goes when it leaves — and, just as often, the fact
 * that it currently has nowhere to go.
 *
 * What this card deliberately does not say
 * ----------------------------------------
 * The reference design shows "•••• 4321 · Checking". This platform stores no
 * bank account number anywhere: `seller_payout_accounts` holds a Stripe
 * *connected account* id (`acct_…`), which is an identifier for a relationship,
 * not for an account. So the card names the Stripe connection and shows a
 * masked reference to it, labelled as a connection.
 *
 * Rendering "•••• 4321 · Checking" from the last four characters of a Stripe id
 * would have looked right, matched the mock, and been a fabrication — the digits
 * would be from an internal identifier and would match nothing on the seller's
 * bank statement. It is recorded as a MOCK-DATA gap instead.
 *
 * The mask arrives already masked from the server. The full identifier never
 * reaches the client, so there is nothing here to accidentally log or screenshot.
 *
 * The "none" state outranks everything
 * ------------------------------------
 * A seller with no payout method cannot be paid, and that fact should reach
 * them before any other prompt on the screen competes for attention. The
 * `state === "none"` branch renders as a prominent action rather than a quiet
 * row, and the screen places it above the ledger.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { paymentsLight } from "../../theme/paymentsLight";
import type { PayoutMethod } from "../../api/paymentsHub";

export type PayoutMethodState = "none" | "incomplete" | "ready" | "unknown";

export type PayoutMethodCardProps = {
  state: PayoutMethodState;
  method: PayoutMethod | null;
  /** Opens Stripe onboarding or the payout-settings surface. */
  onManage?: () => void;
  busy?: boolean;
};

export function PayoutMethodCard({ state, method, onManage, busy = false }: PayoutMethodCardProps) {
  if (state === "unknown") return null;

  const copy = describe(state, method);

  return (
    <View
      style={[styles.card, state === "none" && styles.cardUrgent]}
      accessible
      accessibilityLabel={[copy.title, copy.body, copy.detail].filter(Boolean).join(", ")}
    >
      <View style={styles.headRow}>
        <View style={[styles.pip, { backgroundColor: copy.accent }]} />
        <Text style={styles.title} allowFontScaling numberOfLines={2}>
          {copy.title}
        </Text>
      </View>

      <Text style={styles.body} allowFontScaling>
        {copy.body}
      </Text>

      {copy.detail ? (
        <Text style={styles.detail} allowFontScaling numberOfLines={2}>
          {copy.detail}
        </Text>
      ) : null}

      {onManage ? (
        <Pressable
          onPress={onManage}
          disabled={busy}
          style={[styles.action, busy && styles.actionBusy]}
          accessibilityRole="button"
          accessibilityLabel={copy.action}
          accessibilityState={{ disabled: busy }}
        >
          <Text style={styles.actionText}>{busy ? "Opening…" : copy.action}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

function describe(state: PayoutMethodState, method: PayoutMethod | null) {
  if (state === "none") {
    return {
      accent: paymentsLight.payoutMethod.missing,
      title: "No payout method",
      body: "Your earnings are safe, but there is nowhere to send them yet.",
      detail: "",
      action: "Set up payouts"
    };
  }

  // The masked reference is a Stripe connection id, so it is introduced as one.
  // "Connection ····9999" invites no assumption about a bank account; "····9999"
  // on its own would.
  const mask = method?.destination_masked
    ? `Connection ${method.destination_masked}`
    : "";

  if (state === "incomplete") {
    const missing = method?.missing_requirements?.length
      ? `Still needed: ${method.missing_requirements.join(", ")}`
      : "Stripe still needs a few details before payouts can start.";
    return {
      accent: paymentsLight.payoutMethod.incomplete,
      title: "Payout setup unfinished",
      body: missing,
      detail: mask,
      action: "Finish setup"
    };
  }

  return {
    accent: paymentsLight.payoutMethod.connected,
    title: "Payouts connected",
    body: `Paid out through ${method?.provider || "Stripe"}.`,
    detail: mask,
    action: "Manage payouts"
  };
}

const styles = StyleSheet.create({
  card: {
    marginHorizontal: paymentsLight.space.gutter,
    padding: paymentsLight.space.card,
    borderRadius: paymentsLight.radius.card,
    backgroundColor: paymentsLight.bg.card,
    borderWidth: 1,
    borderColor: paymentsLight.border.hairline
  },
  cardUrgent: {
    backgroundColor: paymentsLight.bg.warning,
    borderColor: paymentsLight.border.warning
  },
  headRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8
  },
  pip: {
    width: 8,
    height: 8,
    borderRadius: 4
  },
  title: {
    flex: 1,
    color: paymentsLight.text.primary,
    fontSize: 15,
    fontWeight: "700"
  },
  body: {
    marginTop: 6,
    color: paymentsLight.text.primary,
    fontSize: 13,
    lineHeight: 18
  },
  detail: {
    marginTop: 4,
    color: paymentsLight.payoutMethod.mask,
    fontSize: 12,
    fontVariant: ["tabular-nums"]
  },
  action: {
    marginTop: 12,
    alignSelf: "flex-start",
    minHeight: paymentsLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 18,
    borderRadius: paymentsLight.radius.control,
    borderWidth: 1,
    borderColor: paymentsLight.border.secondaryButton,
    backgroundColor: paymentsLight.bg.card
  },
  actionBusy: {
    opacity: 0.6
  },
  actionText: {
    color: paymentsLight.text.primary,
    fontSize: 14,
    fontWeight: "700"
  }
});
