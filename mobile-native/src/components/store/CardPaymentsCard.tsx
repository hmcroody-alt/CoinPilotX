/**
 * The Store dashboard's card-payments row — and the way out of "Unavailable".
 *
 * An approved seller whose Stripe Connect account had never been created was
 * shown the word "Unavailable" with no sentence beside it and no button under
 * it. Nothing on the screen said that only *card* payments were affected, that
 * cash on collection still worked, or that the fix was one hosted flow away.
 *
 * The state machine — which state gets a button, and which deliberately does
 * not — lives in `marketplace/cardPaymentState` so it can be tested without a
 * renderer. This file is the paint.
 *
 * It is a presentational component with no data fetching of its own: the status
 * arrives from `useSellerAccess`, which already holds the one seller verdict
 * for the whole app, and the CTA hands off to the payout-onboarding layer that
 * already owns the Stripe conversation. Both of those are single-owner on
 * purpose, and this card adds no second opinion to either.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { useTranslation } from "../../i18n";
import { storeLight } from "../../theme/storeLight";
import type { CardPaymentStatus } from "../../api/sellerAccess";
import {
  cardPaymentPresentation,
  shouldShowCardPaymentCard,
  type CardPaymentTone
} from "../../marketplace/cardPaymentState";

type Props = {
  status: CardPaymentStatus;
  /** Called only for states whose presentation carries a CTA. */
  onSetUpPayments: () => void;
  testID?: string;
};

/**
 * The chip colour per tone.
 *
 * Colour is never the only carrier: the chip also spells the state out in
 * words, and the body sentence repeats it. A seller who cannot distinguish the
 * amber from the red still reads "Action required".
 */
const TONE_COLOR: Record<CardPaymentTone, string> = {
  ready: storeLight.status.success,
  progress: storeLight.text.muted,
  attention: storeLight.status.warning,
  blocked: storeLight.status.error
};

export function CardPaymentsCard({ status, onSetUpPayments, testID }: Props) {
  const { t } = useTranslation();

  if (!shouldShowCardPaymentCard(status)) return null;

  const view = cardPaymentPresentation(status);
  const ns = "commerce:sellerPayments";

  return (
    <View style={styles.card} testID={testID}>
      <Text style={styles.sectionTitle}>{t(`${ns}.sectionTitle`)}</Text>
      <View style={styles.chipRow}>
        <View style={[styles.chipDot, { backgroundColor: TONE_COLOR[view.tone] }]} />
        <Text
          style={[styles.chipText, { color: TONE_COLOR[view.tone] }]}
          testID={testID ? `${testID}-label` : undefined}
        >
          {t(`${ns}.${view.labelKey}`)}
        </Text>
      </View>
      <Text style={styles.body}>{t(`${ns}.${view.bodyKey}`)}</Text>
      {view.ctaKey ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t(`${ns}.${view.ctaKey}`)}
          onPress={onSetUpPayments}
          style={styles.cta}
          testID={testID ? `${testID}-cta` : undefined}
        >
          <Text style={styles.ctaText}>{t(`${ns}.${view.ctaKey}`)}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: 1,
    borderColor: storeLight.border.hairline,
    padding: storeLight.space.card,
    gap: 8,
    marginHorizontal: storeLight.space.gutter,
    marginBottom: storeLight.space.section
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: storeLight.text.primary
  },
  chipRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6
  },
  chipDot: {
    width: 8,
    height: 8,
    borderRadius: storeLight.radius.pill
  },
  chipText: {
    fontSize: 13,
    fontWeight: "700"
  },
  body: {
    fontSize: 14,
    lineHeight: 20,
    color: storeLight.text.muted
  },
  cta: {
    minHeight: storeLight.size.tapTarget,
    alignSelf: "flex-start",
    justifyContent: "center",
    paddingHorizontal: 20,
    borderRadius: storeLight.radius.control,
    backgroundColor: storeLight.cta.from
  },
  ctaText: {
    fontSize: 15,
    fontWeight: "700",
    color: storeLight.cta.text
  }
});
