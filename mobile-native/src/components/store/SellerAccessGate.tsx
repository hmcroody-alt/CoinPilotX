/**
 * What a seller sees instead of the Store when they cannot open it yet.
 *
 * The bug this replaces was not a wrong screen — it was *no* screen. Business
 * OS opened the Store dashboard unconditionally, so an account with a half
 * finished merchant application got the full seller UI, loaded it against an
 * account with no listings and no orders, and read as "your store is broken"
 * rather than "you haven't finished applying". Four production accounts are in
 * exactly that state.
 *
 * So every blocked status gets a named destination rather than a shrug. The
 * mapping lives in `api/sellerAccess.sellerAccessDestination` and is total over
 * the status union, which is what stops a new status from silently rendering
 * "Apply to sell" at a suspended seller.
 *
 * Two states are deliberately not failures:
 *
 * - `SUSPENDED` still offers a route to existing orders. Fulfilment, refunds
 *   and disputes are obligations to buyers, not seller privileges, and locking
 *   someone out of them strands the people who already paid.
 * - A *read* failure renders a retry, not a denial. The server re-checks every
 *   mutation, so the cost of showing the door to someone who cannot open it is
 *   a refused request; the cost of falsely telling an approved seller they have
 *   no store is a support ticket.
 */

import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { useTranslation } from "../../i18n";
import { storeLight } from "../../theme/storeLight";
import {
  sellerAccessDestination,
  type SellerAccessState
} from "../../api/sellerAccess";

export type SellerAccessAction =
  | "APPLY"
  | "RESUME"
  | "STATUS"
  | "RESPOND"
  | "ORDERS"
  | "SUPPORT"
  | "RETRY";

type Props = {
  state: SellerAccessState;
  loading: boolean;
  failed: boolean;
  onAction: (action: SellerAccessAction) => void;
  testID?: string;
};

type Copy = {
  title: string;
  body: string;
  cta: string;
  action: SellerAccessAction;
  secondary?: { label: string; action: SellerAccessAction };
};

/** True when this state should render the gate instead of the seller surface. */
export function shouldGateSellerSurface(state: SellerAccessState, loading: boolean): boolean {
  return loading || state.store_access !== true;
}

export function SellerAccessGate({ state, loading, failed, onAction, testID }: Props) {
  const { t } = useTranslation();

  if (loading) {
    return (
      <View style={styles.page} testID={testID ? `${testID}-loading` : undefined}>
        <ActivityIndicator color={storeLight.text.link} />
        <Text style={styles.body}>{t("commerce:sellerAccess.checking")}</Text>
      </View>
    );
  }

  // A failed read is its own state. Rendering the "apply to sell" copy here
  // would tell an approved seller they have no store because the network
  // hiccuped — a far worse lie than an honest "we couldn't check".
  const copy: Copy = failed
    ? {
        title: t("commerce:sellerAccess.unreadableTitle"),
        body: t("commerce:sellerAccess.unreadableBody"),
        cta: t("commerce:sellerAccess.retry"),
        action: "RETRY"
      }
    : copyFor(state, t);

  return (
    <View style={styles.page} testID={testID}>
      <Text style={styles.title}>{copy.title}</Text>
      <Text style={styles.body}>{copy.body}</Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={copy.cta}
        onPress={() => onAction(copy.action)}
        style={styles.cta}
        testID={testID ? `${testID}-cta` : undefined}
      >
        <Text style={styles.ctaText}>{copy.cta}</Text>
      </Pressable>
      {copy.secondary ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={copy.secondary.label}
          onPress={() => onAction(copy.secondary!.action)}
          style={styles.secondary}
          testID={testID ? `${testID}-secondary` : undefined}
        >
          <Text style={styles.secondaryText}>{copy.secondary.label}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

function copyFor(state: SellerAccessState, t: (key: string) => string): Copy {
  switch (sellerAccessDestination(state)) {
    case "RESUME_APPLICATION":
      return {
        title: t("commerce:sellerAccess.draftTitle"),
        body: t("commerce:sellerAccess.draftBody"),
        cta: t("commerce:sellerAccess.draftCta"),
        action: "RESUME"
      };
    case "APPLICATION_STATUS":
      // Declined and under-review share a destination but not their copy: one
      // is "wait", the other is "here is why". The status field distinguishes
      // them; the destination deliberately does not.
      return state.seller_application_status === "DECLINED"
        ? {
            title: t("commerce:sellerAccess.declinedTitle"),
            body: t("commerce:sellerAccess.declinedBody"),
            cta: t("commerce:sellerAccess.declinedCta"),
            action: "STATUS"
          }
        : {
            title: t("commerce:sellerAccess.reviewTitle"),
            body: t("commerce:sellerAccess.reviewBody"),
            cta: t("commerce:sellerAccess.reviewCta"),
            action: "STATUS"
          };
    case "COMPLETE_REQUESTED_CHANGES":
      return {
        title: t("commerce:sellerAccess.moreInfoTitle"),
        body: t("commerce:sellerAccess.moreInfoBody"),
        cta: t("commerce:sellerAccess.moreInfoCta"),
        action: "RESPOND"
      };
    case "RESTRICTED":
      return {
        title: t("commerce:sellerAccess.suspendedTitle"),
        body: t("commerce:sellerAccess.suspendedBody"),
        cta: t("commerce:sellerAccess.suspendedCta"),
        action: "ORDERS",
        secondary: {
          label: t("commerce:sellerAccess.suspendedSupportCta"),
          action: "SUPPORT"
        }
      };
    case "SELLER_TOOLS":
    case "SELLER_APPLICATION":
    default:
      // `SELLER_TOOLS` is unreachable here — an approved seller never renders
      // the gate — but falling through to "apply" is the safe landing if a
      // caller ever renders it anyway.
      return {
        title: t("commerce:sellerAccess.noApplicationTitle"),
        body: t("commerce:sellerAccess.noApplicationBody"),
        cta: t("commerce:sellerAccess.noApplicationCta"),
        action: "APPLY"
      };
  }
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: storeLight.space.section,
    paddingHorizontal: 28,
    backgroundColor: storeLight.bg.page
  },
  title: {
    fontSize: 20,
    fontWeight: "700",
    textAlign: "center",
    color: storeLight.text.primary
  },
  body: {
    fontSize: 15,
    lineHeight: 21,
    textAlign: "center",
    color: storeLight.text.muted
  },
  cta: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 24,
    borderRadius: storeLight.radius.control,
    backgroundColor: storeLight.cta.from
  },
  ctaText: {
    fontSize: 15,
    fontWeight: "700",
    color: storeLight.cta.text
  },
  secondary: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 24,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  secondaryText: {
    fontSize: 15,
    fontWeight: "600",
    color: storeLight.text.primary
  }
});
