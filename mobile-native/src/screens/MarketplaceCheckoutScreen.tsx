import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppState, Linking, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { openMarketplaceCheckout } from "../api/marketplace";
import { marketplaceCheckoutStage } from "../api/marketplaceCheckoutState";
import {
  checkoutCartGroup,
  getMarketplacePaymentOrder,
  validateCart
} from "../api/marketplaceCommerce";
import { buyerErrorCopy } from "../api/marketplaceErrors";
import { RootStackParamList } from "../navigation/types";
import { MARKETPLACE_CART_CTA, storeLight } from "../theme/marketplaceLight";

type Props = NativeStackScreenProps<RootStackParamList, "MarketplaceCheckout">;
type Stage = "review" | "opening" | "processing" | "confirmed" | "failed";
type Lane = "pickup" | "shipping";

const POLL_INTERVAL_MS = 2500;

function formatMinor(minor = 0, currency = "USD") {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(minor / 100);
  } catch {
    return `${currency} ${(minor / 100).toFixed(2)}`;
  }
}

function makeIntentKey(mode: "cart" | "buy_now", subject: number) {
  return `${mode}-${subject}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Native review and authoritative post-payment state for Marketplace.
 *
 * Stripe still owns card entry and any enabled wallet presentation. Returning
 * from Stripe only starts polling; this screen shows success exclusively when
 * PulseSoc's authenticated order endpoint reports the webhook-confirmed paid
 * state. That keeps receipts, inventory capture and seller proceeds aligned.
 */
export function MarketplaceCheckoutScreen({ route, navigation }: Props) {
  const params = route.params;
  const subject = Number(params.sellerUserId || params.listingId || 0);
  const intentKey = useRef(makeIntentKey(params.mode, subject));
  const [stage, setStage] = useState<Stage>("review");
  const [transactionIds, setTransactionIds] = useState<number[]>([]);
  const [checkoutUrl, setCheckoutUrl] = useState("");
  const [message, setMessage] = useState("");
  const checking = useRef(false);
  // Only asked when the seller offers both lanes. There is no default: picking
  // one for the buyer is how someone collecting in person ends up entering a
  // delivery address, and the server refuses the session without an answer.
  const mustChooseLane = params.fulfillment === "both";
  const [lane, setLane] = useState<Lane | "">("");
  const resolvedLane: Lane | "digital" | "" = mustChooseLane
    ? lane
    : params.fulfillment === "pickup"
      ? "pickup"
      : params.fulfillment === "digital"
        ? "digital"
        : "shipping";

  // Whether this screen knows the exact amount PulseSoc will charge. It does
  // whenever it was handed a minor-unit subtotal: the Stripe session is built
  // from `price × quantity` alone — no shipping options, no automatic tax — so
  // that number *is* the charge, not a running estimate.
  const knowsFinalAmount = params.subtotalMinor != null;
  const amount = useMemo(
    () => params.subtotalMinor != null
      ? formatMinor(params.subtotalMinor, params.currency || "USD")
      : params.priceLabel || "Shown at checkout",
    [params.currency, params.priceLabel, params.subtotalMinor]
  );

  const checkStatus = useCallback(async () => {
    if (!transactionIds.length || checking.current) return;
    checking.current = true;
    try {
      const orders = await Promise.all(transactionIds.map(getMarketplacePaymentOrder));
      const resolved = marketplaceCheckoutStage(orders);
      if (resolved === "confirmed") {
        setStage("confirmed");
        setMessage("");
      } else if (resolved === "failed") {
        setStage("failed");
        setMessage("Payment was not completed. Your order was not marked paid.");
      } else {
        setStage("processing");
        setMessage("Waiting for your payment to be confirmed…");
      }
    } catch {
      setMessage("Still confirming your payment. We'll keep checking.");
    } finally {
      checking.current = false;
    }
  }, [transactionIds]);

  useEffect(() => {
    if (stage !== "processing" || !transactionIds.length) return;
    void checkStatus();
    const timer = setInterval(() => void checkStatus(), POLL_INTERVAL_MS);
    const subscription = AppState.addEventListener("change", (next) => {
      if (next === "active") void checkStatus();
    });
    return () => {
      clearInterval(timer);
      subscription.remove();
    };
  }, [checkStatus, stage, transactionIds.length]);

  const beginCheckout = useCallback(async () => {
    if (stage === "opening") return;
    if (mustChooseLane && !lane) {
      setMessage("Choose pickup or delivery before you pay.");
      return;
    }
    setStage("opening");
    setMessage("");
    try {
      let url = checkoutUrl;
      let ids = transactionIds;
      if (!url || !ids.length) {
        if (params.mode === "cart") {
          const validation = await validateCart();
          const sellerLines = validation.lines.filter(
            (line) => line.seller_user_id === Number(params.sellerUserId || 0)
          );
          const sellerLineIds = new Set(sellerLines.map((line) => line.line_id));
          const blocked = validation.blockingLineIds.some((id) => sellerLineIds.has(id));
          const changed = validation.priceChangedLineIds.some((id) => sellerLineIds.has(id));
          if (!sellerLines.length) throw new Error("These cart items are no longer available.");
          if (blocked) throw new Error("An item is no longer available. Return to your cart to review it.");
          if (changed) throw new Error("A price changed. Return to your cart and accept the new total.");
          const result = await checkoutCartGroup(
            Number(params.sellerUserId),
            intentKey.current,
            mustChooseLane ? (lane as Lane) : ""
          );
          url = result.checkoutUrl;
          ids = [...result.transactionIds];
        } else {
          const result = await openMarketplaceCheckout(
            Number(params.listingId),
            intentKey.current,
            mustChooseLane ? (lane as Lane) : ""
          );
          url = result.checkout_url || "";
          ids = result.transaction_id ? [Number(result.transaction_id)] : [];
        }
        if (!url || !ids.length) throw new Error("Secure checkout could not be created.");
        setCheckoutUrl(url);
        setTransactionIds(ids);
      }
      setStage("processing");
      setMessage("Complete payment securely, then return to PulseSoc.");
      await Linking.openURL(url);
    } catch (error) {
      setStage("review");
      // The locally thrown messages above already name the buyer's next move,
      // so they are their own fallback; `buyerErrorCopy` is what keeps a server
      // 500's "temporary service issue" from becoming the dominant sentence.
      const local = error instanceof Error ? error.message : "";
      setMessage(buyerErrorCopy(error, local || "Checkout could not start. No card was charged."));
    }
  }, [checkoutUrl, lane, mustChooseLane, params.listingId, params.mode, params.sellerUserId, stage, transactionIds]);

  if (stage === "confirmed") {
    const primaryId = transactionIds[0];
    return (
      <View style={styles.center}>
        <View style={styles.check}><Text style={styles.checkText}>✓</Text></View>
        <Text style={styles.confirmedTitle}>Order confirmed</Text>
        <Text style={styles.centerCopy}>Your payment went through and your receipt is ready.</Text>
        <SummaryRow label="Order" value={`#${primaryId}`} />
        <SummaryRow label="Seller" value={params.sellerName || "PulseSoc seller"} />
        <SummaryRow label="Amount" value={amount} />
        <Pressable accessibilityRole="button" style={styles.primary} onPress={() => navigation.replace("BuyerOrderDetail", { orderId: primaryId, source: "seller_transactions", title: "Order confirmed" })}>
          <Text style={styles.primaryText}>View order and receipt</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.secondary} onPress={() => navigation.navigate("MarketplaceDetail", { title: "Marketplace" })}>
          <Text style={styles.secondaryText}>Continue shopping</Text>
        </Pressable>
      </View>
    );
  }

  if (stage === "processing") {
    return (
      <View style={styles.center}>
        <View style={styles.processingMark}><Text style={styles.processingIcon}>⌛</Text></View>
        <Text style={styles.confirmedTitle}>Processing your payment</Text>
        <Text style={styles.centerCopy}>Please do not close this screen or start another checkout. We'll confirm as soon as your payment clears.</Text>
        {message ? <Text style={styles.note}>{message}</Text> : null}
        {checkoutUrl ? (
          <Pressable accessibilityRole="button" style={styles.secondary} onPress={() => void Linking.openURL(checkoutUrl)}>
            <Text style={styles.secondaryText}>Return to secure checkout</Text>
          </Pressable>
        ) : null}
        <Pressable accessibilityRole="button" style={styles.primary} onPress={() => void checkStatus()}>
          <Text style={styles.primaryText}>Check payment status</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.kicker}>PULSESOC MARKETPLACE</Text>
      <Text style={styles.title}>Review your order</Text>
      <Text style={styles.subtitle}>Check the details below, then pay securely.</Text>

      {mustChooseLane ? (
        <Section title="How do you want it?">
          <Text style={styles.muted}>This seller offers both. Pick one — it decides whether you need to give a delivery address.</Text>
          <LaneOption
            selected={lane === "pickup"}
            title="Local pickup"
            detail="Arrange a time and place with the seller after your order is confirmed. No delivery address, no delivery charge."
            onPress={() => { setLane("pickup"); setMessage(""); }}
          />
          <LaneOption
            selected={lane === "shipping"}
            title="Shipping"
            detail="You'll enter your delivery address on the secure payment page. The seller ships to it."
            onPress={() => { setLane("shipping"); setMessage(""); }}
          />
        </Section>
      ) : (
        <Section title={resolvedLane === "pickup" ? "Pickup" : resolvedLane === "digital" ? "Delivery" : "Ship to"}>
          <Text style={styles.body}>
            {resolvedLane === "pickup"
              ? "You'll arrange pickup with the seller after your order is confirmed."
              : resolvedLane === "digital"
                ? "This item is delivered digitally — no delivery address needed."
                : "You'll enter your delivery address on the secure payment page."}
          </Text>
          <Text style={styles.muted}>
            {resolvedLane === "shipping"
              ? "The address is for the seller to ship to. It does not change what you pay."
              : "Nothing is added to your total for delivery."}
          </Text>
        </Section>
      )}

      <Section title="Payment">
        <Text style={styles.body}>Card or Apple Pay, handled by Stripe</Text>
        <Text style={styles.muted}>Your card details go straight to Stripe — PulseSoc never sees them, and neither does the seller.</Text>
      </Section>

      <Section title="Order summary">
        <SummaryRow label={params.itemTitle || "Marketplace items"} value={params.quantity ? `×${params.quantity}` : ""} />
        <SummaryRow label="Seller" value={params.sellerName || "PulseSoc seller"} />
        <SummaryRow label="Item total" value={amount} />
        {/* Not "added at payment". The Stripe session is built from item price ×
            quantity with no shipping options and no automatic tax, so there is
            no second number waiting at the payment page. Saying otherwise made
            the buyer brace for a charge that never comes — and would have hidden
            a real one if it ever did. */}
        <SummaryRow label="Delivery" value={resolvedLane === "pickup" ? "Free — you collect" : "No delivery charge"} />
        <SummaryRow label="Taxes and fees" value="None added by PulseSoc" />
        <View style={styles.rule} />
        <SummaryRow label={knowsFinalAmount ? "Total to pay" : "Total"} value={amount} strong />
        {knowsFinalAmount ? (
          <Text style={styles.muted}>This is the full amount you'll be charged. Nothing is added after this screen.</Text>
        ) : (
          <Text style={styles.muted}>The exact amount is confirmed on the secure payment page before you authorize anything.</Text>
        )}
      </Section>

      {message ? <Text style={styles.error}>{message}</Text> : null}
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: stage === "opening" || (mustChooseLane && !lane) }}
        disabled={stage === "opening" || (mustChooseLane && !lane)}
        style={[styles.primary, (stage === "opening" || (mustChooseLane && !lane)) && styles.disabled]}
        onPress={() => void beginCheckout()}
      >
        {/* The CTA states an amount only when this screen knows the exact charge.
            Otherwise it promises nothing it cannot keep. */}
        <Text style={styles.primaryText}>
          {stage === "opening"
            ? "Opening secure payment…"
            : knowsFinalAmount
              ? `Pay securely · ${amount}`
              : "Continue to secure payment"}
        </Text>
      </Pressable>
      <Text style={styles.footnote}>Your order isn't confirmed until your payment clears.</Text>
    </ScrollView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text>{children}</View>;
}

function LaneOption({
  selected,
  title,
  detail,
  onPress
}: {
  selected: boolean;
  title: string;
  detail: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ selected }}
      accessibilityLabel={title}
      accessibilityHint={detail}
      onPress={onPress}
      style={[styles.lane, selected && styles.laneSelected]}
    >
      <View style={[styles.laneMark, selected && styles.laneMarkSelected]}>
        {selected ? <Text style={styles.laneTick}>✓</Text> : null}
      </View>
      <View style={styles.laneCopy}>
        <Text style={styles.laneTitle}>{title}</Text>
        <Text style={styles.muted}>{detail}</Text>
      </View>
    </Pressable>
  );
}

function SummaryRow({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return <View style={styles.row}><Text style={[styles.rowLabel, strong && styles.strong]}>{label}</Text><Text style={[styles.rowValue, strong && styles.strong]}>{value}</Text></View>;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { padding: 18, paddingBottom: 44, gap: 14 },
  kicker: { color: storeLight.text.link, fontSize: 12, fontWeight: "900", letterSpacing: 1 },
  title: { color: storeLight.text.primary, fontSize: 28, fontWeight: "900" },
  subtitle: { color: storeLight.text.muted, fontSize: 15, lineHeight: 21 },
  section: { backgroundColor: storeLight.bg.card, borderWidth: 1, borderColor: storeLight.border.hairline, borderRadius: 16, padding: 16, gap: 9 },
  sectionTitle: { color: storeLight.text.primary, fontSize: 17, fontWeight: "800" },
  body: { color: storeLight.text.primary, fontSize: 15, lineHeight: 21 },
  muted: { color: storeLight.text.muted, fontSize: 13, lineHeight: 18 },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", gap: 14, minHeight: 24 },
  rowLabel: { color: storeLight.text.muted, fontSize: 14, flex: 1 },
  rowValue: { color: storeLight.text.primary, fontSize: 14, textAlign: "right", flexShrink: 1 },
  strong: { color: storeLight.text.primary, fontWeight: "900", fontSize: 15 },
  rule: { height: StyleSheet.hairlineWidth, backgroundColor: storeLight.border.hairline, marginVertical: 3 },
  lane: { flexDirection: "row", alignItems: "flex-start", gap: 12, borderWidth: 1, borderColor: storeLight.border.hairline, borderRadius: 14, padding: 14, minHeight: 64 },
  laneSelected: { borderColor: MARKETPLACE_CART_CTA.to, borderWidth: 2 },
  laneMark: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: storeLight.border.secondaryButton, alignItems: "center", justifyContent: "center", marginTop: 1 },
  laneMarkSelected: { backgroundColor: MARKETPLACE_CART_CTA.to, borderColor: MARKETPLACE_CART_CTA.to },
  laneTick: { color: "#fff", fontSize: 13, fontWeight: "900" },
  laneCopy: { flex: 1, gap: 4 },
  laneTitle: { color: storeLight.text.primary, fontSize: 15, fontWeight: "800" },
  primary: { minHeight: 52, borderRadius: 14, backgroundColor: MARKETPLACE_CART_CTA.to, alignItems: "center", justifyContent: "center", paddingHorizontal: 18 },
  primaryText: { color: "#fff", fontSize: 16, fontWeight: "900", textAlign: "center" },
  secondary: { minHeight: 50, borderRadius: 14, borderWidth: 1, borderColor: storeLight.border.secondaryButton, alignItems: "center", justifyContent: "center", paddingHorizontal: 18, width: "100%" },
  secondaryText: { color: storeLight.text.link, fontSize: 15, fontWeight: "800", textAlign: "center" },
  disabled: { opacity: 0.55 },
  error: { color: storeLight.status.error, fontSize: 14, lineHeight: 20, textAlign: "center" },
  footnote: { color: storeLight.text.muted, fontSize: 12, lineHeight: 17, textAlign: "center" },
  center: { flex: 1, backgroundColor: storeLight.bg.page, alignItems: "center", justifyContent: "center", padding: 24, gap: 16 },
  check: { width: 76, height: 76, borderRadius: 38, backgroundColor: MARKETPLACE_CART_CTA.to, alignItems: "center", justifyContent: "center" },
  checkText: { color: "#fff", fontSize: 42, fontWeight: "900" },
  processingMark: { width: 76, height: 76, borderRadius: 38, backgroundColor: storeLight.bg.card, borderWidth: 2, borderColor: storeLight.border.secondaryButton, alignItems: "center", justifyContent: "center" },
  processingIcon: { fontSize: 32 },
  confirmedTitle: { color: storeLight.text.primary, fontSize: 26, fontWeight: "900", textAlign: "center" },
  centerCopy: { color: storeLight.text.muted, fontSize: 15, lineHeight: 21, textAlign: "center", maxWidth: 360 },
  note: { color: storeLight.text.muted, fontSize: 13, lineHeight: 18, textAlign: "center" }
});
