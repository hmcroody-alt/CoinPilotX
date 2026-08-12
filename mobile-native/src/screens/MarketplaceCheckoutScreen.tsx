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
import { RootStackParamList } from "../navigation/types";
import { MARKETPLACE_CART_CTA, storeLight } from "../theme/marketplaceLight";

type Props = NativeStackScreenProps<RootStackParamList, "MarketplaceCheckout">;
type Stage = "review" | "opening" | "processing" | "confirmed" | "failed";

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
          const result = await checkoutCartGroup(Number(params.sellerUserId), intentKey.current);
          url = result.checkoutUrl;
          ids = [...result.transactionIds];
        } else {
          const result = await openMarketplaceCheckout(Number(params.listingId), intentKey.current);
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
      setMessage(error instanceof Error ? error.message : "Checkout could not start.");
    }
  }, [checkoutUrl, params.listingId, params.mode, params.sellerUserId, stage, transactionIds]);

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

      <Section title={params.fulfillment === "pickup" ? "Pickup" : params.fulfillment === "digital" ? "Delivery" : "Ship to"}>
        <Text style={styles.body}>{params.fulfillment === "pickup" ? "You'll arrange pickup with the seller after your order is confirmed." : params.fulfillment === "digital" ? "This item is delivered digitally — no shipping address needed." : "You'll enter your delivery address on the secure payment page."}</Text>
        <Text style={styles.muted}>Delivery cost is added there, not estimated here.</Text>
      </Section>

      <Section title="Payment">
        <Text style={styles.body}>Card or Apple Pay, handled by Stripe</Text>
        <Text style={styles.muted}>Your card details go straight to Stripe — PulseSoc never sees them, and neither does the seller.</Text>
      </Section>

      <Section title="Order summary">
        <SummaryRow label={params.itemTitle || "Marketplace items"} value={params.quantity ? `×${params.quantity}` : ""} />
        <SummaryRow label="Seller" value={params.sellerName || "PulseSoc seller"} />
        <SummaryRow label="Subtotal" value={amount} />
        <SummaryRow label="Shipping" value="Added at payment" />
        <SummaryRow label="Taxes and fees" value="Added at payment" />
        <View style={styles.rule} />
        {/* The subtotal, not a guess at the final charge. Shipping and tax are
            computed by Stripe from the address the buyer enters there, so a
            "total" here would be a number this screen cannot stand behind. */}
        <SummaryRow label="Total so far" value={amount} strong />
      </Section>

      {message ? <Text style={styles.error}>{message}</Text> : null}
      <Pressable accessibilityRole="button" accessibilityState={{ disabled: stage === "opening" }} disabled={stage === "opening"} style={[styles.primary, stage === "opening" && styles.disabled]} onPress={() => void beginCheckout()}>
        <Text style={styles.primaryText}>{stage === "opening" ? "Opening secure payment…" : `Pay securely · ${amount}`}</Text>
      </Pressable>
      <Text style={styles.footnote}>Your order isn't confirmed until your payment clears.</Text>
    </ScrollView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text>{children}</View>;
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
