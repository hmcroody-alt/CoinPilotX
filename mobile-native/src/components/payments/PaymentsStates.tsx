/**
 * The non-happy paths, which on a money screen are most of the screen's job.
 *
 * The rule that shapes all of these: **a state must never be mistakable for a
 * financial fact.** A skeleton must not look like a zero; an error must not look
 * like an empty ledger; an offline balance must not look like a current one.
 * Each component below is written against a specific way that could go wrong.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { paymentsLight } from "../../theme/paymentsLight";

/**
 * Loading skeletons.
 *
 * The amount placeholders are deliberately bars, never "$0.00" and never "—".
 * A skeleton showing a currency figure of any kind is a number on screen that
 * the seller may read before it resolves, and "$0.00" flashing before the real
 * balance arrives is a small heart attack for someone checking their earnings.
 *
 * `accessibilityLiveRegion` is off and the whole block is one labelled element,
 * so an AT user hears "Loading your balances" once rather than a stream of
 * placeholder announcements.
 */
export function PaymentsLoading() {
  return (
    <View accessible accessibilityLabel="Loading your balances" style={styles.wrap}>
      <View style={[styles.bar, { width: "42%", height: 12 }]} />
      <View style={[styles.bar, { width: "62%", height: 30, marginTop: 10 }]} />
      <View style={[styles.bar, { width: "50%", height: 12, marginTop: 10 }]} />
      <View style={styles.cardRow}>
        <View style={styles.skeletonCard} />
        <View style={styles.skeletonCard} />
      </View>
      {[0, 1, 2, 3].map((i) => (
        <View key={i} style={styles.skeletonRow}>
          <View style={styles.skeletonCircle} />
          <View style={styles.skeletonRowBody}>
            <View style={[styles.bar, { width: "58%", height: 11 }]} />
            <View style={[styles.bar, { width: "34%", height: 9, marginTop: 6 }]} />
          </View>
          <View style={[styles.bar, { width: 62, height: 12 }]} />
        </View>
      ))}
    </View>
  );
}

/** A seller who has never sold. Not an error, and not phrased as a shortfall. */
export function PaymentsEmpty({ hasPayoutMethod }: { hasPayoutMethod: boolean }) {
  return (
    <View style={styles.message} accessible>
      <Text style={styles.messageTitle} allowFontScaling>
        No money movement yet
      </Text>
      <Text style={styles.messageBody} allowFontScaling>
        {hasPayoutMethod
          ? "When your first sale is paid for, it will appear here."
          : "When your first sale is paid for, it will appear here. Setting up payouts now means nothing is waiting later."}
      </Text>
    </View>
  );
}

/**
 * A balance read that failed. **The only place on this screen that says so.**
 *
 * Says what it does not know, not what the money is. There is no cached figure
 * shown here on purpose — a stale balance rendered during a fresh-read failure
 * is a number presented as current, which is the one thing the brief rules out
 * for balances specifically. Cached figures appear only in `PaymentsOffline`,
 * where they carry a timestamp.
 *
 * ## Why this is the single error surface
 *
 * The screen used to state the same failure three times: an em dash in the hero
 * with a Retry attached to it, a sub-line reading "We could not read your
 * balance just now", and then this card with a second Try again underneath.
 * Three statements of one fact read as three faults, and two retries for one
 * action leaves the seller wondering whether they do different things. The hero
 * now shows only the dash — which is a statement about the number, not about
 * the system — and everything the seller needs to *understand and act on* the
 * failure is here, once.
 *
 * `supportReference` exists because "it was broken" is not a thing a seller can
 * usefully tell support about their money. See `supportReferenceFor` for why it
 * is a timestamp rather than an opaque token.
 */
export function PaymentsError({
  onRetry,
  supportReference
}: {
  onRetry?: () => void;
  supportReference?: string | null;
}) {
  return (
    <View style={styles.message} accessible accessibilityLiveRegion="polite">
      <Text style={styles.messageTitle} allowFontScaling>
        Balances unavailable
      </Text>
      <Text style={styles.messageBody} allowFontScaling>
        We could not reach your account just now. Nothing has changed — this is a
        display problem, not a change to your money.
      </Text>
      {onRetry ? (
        <Pressable
          onPress={onRetry}
          style={styles.retry}
          accessibilityRole="button"
          accessibilityLabel="Try again"
          hitSlop={8}
        >
          <Text style={styles.retryText} allowFontScaling>
            Try again
          </Text>
        </Pressable>
      ) : null}
      {supportReference ? (
        // Selectable so the seller can copy it rather than transcribe it, and
        // spoken as separated characters because a screen reader reading
        // "PAY-20260803-0914-3O" as a word helps nobody quote it.
        <Text
          style={styles.reference}
          allowFontScaling
          selectable
          accessibilityLabel={`If you contact support, quote reference ${supportReference
            .split("")
            .join(" ")}`}
        >
          {`Reference ${supportReference}`}
        </Text>
      ) : null}
    </View>
  );
}

/**
 * Offline, with a cached ledger.
 *
 * The timestamp is required, not optional. A cached balance without an "as of"
 * is indistinguishable from a live one, and the whole justification for showing
 * it at all is that the seller can see how old it is.
 */
export function PaymentsOffline({ asOf }: { asOf: string }) {
  return (
    <View style={styles.offline} accessible accessibilityLiveRegion="polite">
      <Text style={styles.offlineText} allowFontScaling>
        {`Offline — showing your last synced activity as of ${asOf}. Money actions are paused until you reconnect.`}
      </Text>
    </View>
  );
}

/** A payout the backend says is in flight. Reassurance, not a control. */
export function PayoutInFlightNotice({ detail }: { detail: string }) {
  return (
    <View style={styles.inFlight} accessible accessibilityLiveRegion="polite">
      <Text style={styles.inFlightText} allowFontScaling>
        {detail}
      </Text>
    </View>
  );
}

/**
 * A payout that failed.
 *
 * Shows the provider's real reason when there is one. A generic "something went
 * wrong" on a failed transfer leaves the seller with no idea whether to fix
 * their bank details or simply wait.
 */
export function PayoutFailedNotice({ reason }: { reason: string }) {
  return (
    <View style={styles.failed} accessible accessibilityRole="alert">
      <Text style={styles.failedTitle} allowFontScaling>
        A payout did not go through
      </Text>
      <Text style={styles.failedBody} allowFontScaling>
        {reason || "Your provider did not give a reason. The money is still in your balance."}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    paddingHorizontal: paymentsLight.space.gutter,
    paddingTop: 14
  },
  bar: {
    backgroundColor: paymentsLight.bg.skeleton,
    borderRadius: 6
  },
  cardRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 18
  },
  skeletonCard: {
    flex: 1,
    height: 92,
    borderRadius: paymentsLight.radius.card,
    backgroundColor: paymentsLight.bg.skeleton
  },
  skeletonRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginTop: 18
  },
  skeletonCircle: {
    width: paymentsLight.size.iconCircle,
    height: paymentsLight.size.iconCircle,
    borderRadius: paymentsLight.radius.iconCircle,
    backgroundColor: paymentsLight.bg.skeleton
  },
  skeletonRowBody: {
    flex: 1
  },
  message: {
    marginHorizontal: paymentsLight.space.gutter,
    marginTop: paymentsLight.space.section,
    padding: paymentsLight.space.card,
    borderRadius: paymentsLight.radius.card,
    backgroundColor: paymentsLight.bg.card,
    borderWidth: 1,
    borderColor: paymentsLight.border.hairline
  },
  messageTitle: {
    color: paymentsLight.text.primary,
    fontSize: 15,
    fontWeight: "700"
  },
  messageBody: {
    marginTop: 6,
    color: paymentsLight.text.muted,
    fontSize: 13,
    lineHeight: 18
  },
  retry: {
    alignSelf: "flex-start",
    borderColor: paymentsLight.border.secondaryButton,
    borderRadius: paymentsLight.radius.control,
    borderWidth: 1,
    justifyContent: "center",
    marginTop: 12,
    minHeight: paymentsLight.size.tapTarget,
    paddingHorizontal: 18
  },
  retryText: {
    color: paymentsLight.text.link,
    fontSize: 14,
    fontWeight: "700"
  },
  reference: {
    marginTop: 10,
    color: paymentsLight.text.muted,
    fontSize: 12,
    fontVariant: ["tabular-nums"]
  },
  offline: {
    marginHorizontal: paymentsLight.space.gutter,
    marginTop: 12,
    padding: 12,
    borderRadius: paymentsLight.radius.control,
    backgroundColor: paymentsLight.bg.warning,
    borderWidth: 1,
    borderColor: paymentsLight.border.warning
  },
  offlineText: {
    color: paymentsLight.text.primary,
    fontSize: 12,
    lineHeight: 17
  },
  inFlight: {
    marginHorizontal: paymentsLight.space.gutter,
    marginTop: 12,
    padding: 12,
    borderRadius: paymentsLight.radius.control,
    backgroundColor: paymentsLight.bg.strip,
    borderWidth: 1,
    borderColor: paymentsLight.border.hairline
  },
  inFlightText: {
    color: paymentsLight.text.primary,
    fontSize: 12,
    lineHeight: 17
  },
  failed: {
    marginHorizontal: paymentsLight.space.gutter,
    marginTop: 12,
    padding: 12,
    borderRadius: paymentsLight.radius.control,
    backgroundColor: paymentsLight.refundBanner.from,
    borderWidth: 1,
    borderColor: paymentsLight.refundBanner.border
  },
  failedTitle: {
    color: paymentsLight.refundBanner.heading,
    fontSize: 14,
    fontWeight: "700"
  },
  failedBody: {
    marginTop: 4,
    color: paymentsLight.text.primary,
    fontSize: 12,
    lineHeight: 17
  }
});
