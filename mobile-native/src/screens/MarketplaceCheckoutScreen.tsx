import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppState, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { openMarketplaceCheckout } from "../api/marketplace";
import {
  firstMissingFulfillmentField,
  fulfillmentDestinationSummary,
  fulfillmentFields,
  fulfillmentNeedsAddress,
  resolveFulfillmentChoice,
  UNDECIDED_KINDS,
  type FulfillmentField,
  type MarketplaceFulfillmentKind
} from "../api/marketplaceFulfillment";
import { marketplaceCheckoutStage } from "../api/marketplaceCheckoutState";
import {
  checkoutCartGroup,
  getMarketplacePaymentOrder,
  validateCart
} from "../api/marketplaceCommerce";
import { buyerErrorCopy } from "../api/marketplaceErrors";
import {
  isPaymentSheetAvailable,
  presentPaymentSheet,
  type PaymentSheetBootstrap
} from "../api/stripePaymentSheet";
import { RootStackParamList } from "../navigation/types";
import { MARKETPLACE_CART_CTA, storeLight } from "../theme/marketplaceLight";
import { PaymentController } from "../payments/PaymentController";

type Props = NativeStackScreenProps<RootStackParamList, "MarketplaceCheckout">;
type Stage = "details" | "review" | "opening" | "processing" | "confirmed" | "failed";

const POLL_INTERVAL_MS = 2500;

/** Older navigations carry only the four physical lanes. Read them as kinds so
 * a screen opened before this build shipped still lands somewhere coherent. */
function kindFromParams(
  kind: MarketplaceFulfillmentKind | undefined,
  legacy: "digital" | "pickup" | "shipping" | "both" | undefined
): MarketplaceFulfillmentKind {
  if (kind) return kind;
  if (legacy === "digital") return "digital";
  if (legacy === "pickup") return "pickup";
  if (legacy === "both") return "shipping_or_pickup";
  return "shipping";
}

const LANE_COPY: Record<string, { title: string; detail: string }> = {
  pickup: {
    title: "Local pickup",
    detail: "Arrange a time and place with the seller after your order is confirmed. No delivery address, no delivery charge."
  },
  shipping: {
    title: "Shipping",
    detail: "Give the seller a delivery address on the next step. The seller ships to it."
  },
  remote: {
    title: "Online",
    detail: "The seller runs this remotely. You'll pick a date and time, and they send you the link."
  },
  in_person: {
    title: "In person",
    detail: "The seller comes to you. You'll pick a date and time and give them an address."
  }
};

function lanesFor(kind: MarketplaceFulfillmentKind) {
  return kind === "service_choice" ? ["remote", "in_person"] : ["pickup", "shipping"];
}

function placeholderFor(field: FulfillmentField) {
  if (field.type === "date") return "YYYY-MM-DD";
  if (field.type === "time") return "HH:MM";
  if (field.type === "country") return "US";
  if (field.type === "timezone") return "America/New_York";
  return "";
}

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
  // What this order type actually needs, decided before anything is asked. A
  // booking is not a parcel and a download is not either; the fields below come
  // from the kind, so neither is handed a shipping form it has no use for.
  const declaredKind = kindFromParams(params.fulfillmentKind, params.fulfillment);
  const tickets = useMemo(() => params.ticketOptions ?? [], [params.ticketOptions]);
  // A cart group whose kind resolved to shipping can still contain a line the
  // seller offers either way, and the server refuses the whole group until that
  // line's lane is named. The legacy param is the only thing that still carries
  // it, so it keeps the picker on screen.
  const mustChooseLane = UNDECIDED_KINDS.includes(declaredKind) || params.fulfillment === "both";
  const [lane, setLane] = useState("");
  const kind = resolveFulfillmentChoice(declaredKind, lane) ?? declaredKind;
  const fields = useMemo(
    () => (mustChooseLane && !lane ? [] : fulfillmentFields(kind, tickets)),
    [kind, lane, mustChooseLane, tickets]
  );
  const [details, setDetails] = useState<Record<string, string>>({});
  const needsDetailsStep = mustChooseLane || fulfillmentFields(declaredKind, tickets).length > 0;
  const [stage, setStage] = useState<Stage>(needsDetailsStep ? "details" : "review");
  const [transactionIds, setTransactionIds] = useState<number[]>([]);
  const [checkoutUrl, setCheckoutUrl] = useState("");
  // The native-sheet bootstrap for this intent, cached alongside the ids so a
  // retry re-presents the same PaymentIntent rather than minting a second one.
  const [sheet, setSheet] = useState<PaymentSheetBootstrap | null>(null);
  const [message, setMessage] = useState("");
  const checking = useRef(false);

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
      setMessage("Choose how you want this order fulfilled before you pay.");
      setStage("details");
      return;
    }
    // The server validates this again against the listing row. Checking here is
    // only so the buyer fixes a blank field on the step that owns it rather than
    // reading a rejection after tapping Pay.
    const missing = firstMissingFulfillmentField(kind, tickets, details);
    if (missing) {
      setMessage(`${missing.label} is required before you can pay.`);
      setStage("details");
      return;
    }
    setStage("opening");
    setMessage("");
    try {
      if (!isPaymentSheetAvailable()) {
        throw new Error("This build cannot open secure in-app checkout. Update PulseSoc and try again.");
      }
      const policy = await PaymentController.instruction(
        params.mode === "cart" ? "marketplace_cart" : "marketplace_listing",
        params.mode === "cart" ? {} : { resourceId: Number(params.listingId) }
      );
      if (!policy.ok || policy.provider !== "stripe" || policy.flow !== "payment_sheet") {
        throw new Error("PulseSoc could not authorize the payment method for this purchase.");
      }
      const paymentMode = "payment_sheet";
      let url = checkoutUrl;
      let ids = transactionIds;
      let bootstrap = sheet;
      if ((!url && !bootstrap) || !ids.length) {
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
            mustChooseLane ? lane : "",
            paymentMode,
            details
          );
          url = result.checkoutUrl;
          ids = [...result.transactionIds];
          bootstrap = result.sheet;
        } else {
          const result = await openMarketplaceCheckout(
            Number(params.listingId),
            intentKey.current,
            mustChooseLane ? lane : "",
            paymentMode,
            details
          );
          url = result.handoff.checkoutUrl;
          ids = [...result.handoff.transactionIds];
          bootstrap = result.handoff.sheet;
        }
        if (!ids.length || !bootstrap) throw new Error("Secure in-app checkout could not be created.");
        setCheckoutUrl(url);
        setTransactionIds(ids);
        setSheet(bootstrap);
      }

      // Native, in-app Stripe sheet is the path whenever the server handed one
      // back — no Safari, no webview. Success here is not proof of payment: the
      // order is only paid once the webhook says so, which the poller below
      // confirms. So every non-error outcome routes into `processing`.
      if (bootstrap) {
        // Never true any more: an order that needs an address collected it on the
        // details step and the server passed it to Stripe, so asking again would
        // be asking twice. Kept explicit rather than dropped so the next reader
        // sees that the decision was made, not forgotten.
        const outcome = await presentPaymentSheet(bootstrap, { collectAddress: false });
        if (outcome.result === "completed") {
          setStage("processing");
          setMessage("Confirming your payment…");
          return;
        }
        if (outcome.result === "canceled") {
          setStage("review");
          setMessage("You closed the payment sheet before paying. No card was charged.");
          return;
        }
        if (outcome.result === "failed") {
          setStage("review");
          setMessage(outcome.message || "Your payment could not be completed. No card was charged.");
          return;
        }
        throw new Error("Secure in-app checkout became unavailable. No card was charged.");
      }
      throw new Error("Secure in-app checkout could not be created.");
    } catch (error) {
      setStage("review");
      // A creation attempt that reached Stripe and failed has spent this key on
      // those exact parameters, and the next attempt is built against a fresh
      // transaction id — so reusing the key makes every retry fail as "same key,
      // different request" regardless of whether the original cause is gone.
      // Tapping again after reading an error is a new attempt; collapsing a
      // double-tap of the *same* attempt is the `opening` guard's job, above.
      // Skipped once a bootstrap exists, because then nothing was re-created.
      if (!sheet) intentKey.current = makeIntentKey(params.mode, subject);
      // The locally thrown messages above already name the buyer's next move,
      // so they are their own fallback; `buyerErrorCopy` is what keeps a server
      // 500's "temporary service issue" from becoming the dominant sentence.
      const local = error instanceof Error ? error.message : "";
      setMessage(buyerErrorCopy(error, local || "Checkout could not start. No card was charged."));
    }
  }, [checkoutUrl, details, kind, lane, mustChooseLane, params.listingId, params.mode, params.sellerUserId, sheet, stage, subject, tickets, transactionIds]);

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
        <Pressable accessibilityRole="button" style={styles.primary} onPress={() => void checkStatus()}>
          <Text style={styles.primaryText}>Check payment status</Text>
        </Pressable>
      </View>
    );
  }

  // Everything the buyer has to tell the seller, asked before the total is shown
  // and long before a card is. Nothing on this step is optional to reach: the
  // review step below cannot be entered until it passes.
  if (stage === "details") {
    const blocked = (mustChooseLane && !lane) || !!firstMissingFulfillmentField(kind, tickets, details);
    return (
      <ScrollView style={styles.root} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={styles.kicker}>PULSESOC MARKETPLACE</Text>
        <Text style={styles.title}>Order details</Text>
        <Text style={styles.subtitle}>{detailsSubtitle(declaredKind)}</Text>

        {mustChooseLane ? (
          <Section title="How do you want it?">
            <Text style={styles.muted}>This seller offers more than one option. Pick one — it decides what else you need to give.</Text>
            {lanesFor(declaredKind).map((option) => (
              <LaneOption
                key={option}
                selected={lane === option}
                title={LANE_COPY[option].title}
                detail={LANE_COPY[option].detail}
                onPress={() => { setLane(option); setMessage(""); }}
              />
            ))}
          </Section>
        ) : null}

        {fields.length ? (
          <Section title={fieldsSectionTitle(kind)}>
            {fields.map((field) => (
              <FulfillmentInput
                key={field.key}
                field={field}
                value={details[field.key] || ""}
                onChange={(next) => {
                  setDetails((current) => ({ ...current, [field.key]: next }));
                  setMessage("");
                }}
              />
            ))}
          </Section>
        ) : null}

        {message ? <Text style={styles.error}>{message}</Text> : null}
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ disabled: blocked }}
          disabled={blocked}
          style={[styles.primary, blocked && styles.disabled]}
          onPress={() => { setMessage(""); setStage("review"); }}
        >
          <Text style={styles.primaryText}>Continue to review</Text>
        </Pressable>
        <Text style={styles.footnote}>Nothing is charged yet. You'll see the full total on the next step.</Text>
      </ScrollView>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.kicker}>PULSESOC MARKETPLACE</Text>
      <Text style={styles.title}>Review your order</Text>
      <Text style={styles.subtitle}>Check the details below, then pay securely.</Text>

      <Section title={destinationTitle(kind)}>
        <Text style={styles.body}>{fulfillmentDestinationSummary(kind, details)}</Text>
        {details.scheduled_date ? (
          <Text style={styles.muted}>
            {[details.scheduled_date, details.scheduled_time, details.timezone].filter(Boolean).join(" · ")}
          </Text>
        ) : null}
        {details.contact_name || details.attendee_name ? (
          <Text style={styles.muted}>
            {[details.contact_name || details.attendee_name, details.contact_phone, details.ticket_type].filter(Boolean).join(" · ")}
          </Text>
        ) : null}
        {needsDetailsStep ? (
          // Back to the step that owns these fields, with what was typed still in
          // it — correcting a postcode is not a reason to start over.
          <Pressable accessibilityRole="button" onPress={() => { setMessage(""); setStage("details"); }}>
            <Text style={styles.editLink}>Edit order details</Text>
          </Pressable>
        ) : null}
      </Section>

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
        <SummaryRow label="Delivery" value={kind === "pickup" ? "Free — you collect" : "No delivery charge"} />
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
        accessibilityState={{ disabled: stage === "opening" }}
        disabled={stage === "opening"}
        style={[styles.primary, stage === "opening" && styles.disabled]}
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

function detailsSubtitle(kind: MarketplaceFulfillmentKind) {
  if (kind === "shipping_or_pickup") return "Tell us how you want this order, then who it's for.";
  if (kind === "service_choice") return "Tell us how this should happen, then when.";
  if (kind.startsWith("event_")) return "Tell us who's attending.";
  if (kind.startsWith("service_") || kind.startsWith("booking_")) return "Tell us when this should happen and how to reach you.";
  if (kind === "pickup") return "Tell us who's collecting, so the seller knows who to expect.";
  return "Tell us where this order is going.";
}

function fieldsSectionTitle(kind: MarketplaceFulfillmentKind) {
  if (kind.startsWith("event_")) return "Attendee";
  if (kind === "pickup") return "Pickup contact";
  if (fulfillmentNeedsAddress(kind)) return kind === "shipping" ? "Delivery address" : "Contact and address";
  return "Your details";
}

function destinationTitle(kind: MarketplaceFulfillmentKind) {
  if (kind === "digital") return "Delivery";
  if (kind === "pickup") return "Pickup";
  if (kind === "shipping") return "Ship to";
  if (kind.startsWith("event_")) return "Attendee";
  return "When and where";
}

function FulfillmentInput({
  field,
  value,
  onChange
}: {
  field: FulfillmentField;
  value: string;
  onChange: (next: string) => void;
}) {
  if (field.type === "choice") {
    return (
      <View style={styles.field}>
        <Text style={styles.fieldLabel}>{field.label}</Text>
        {(field.options || []).map((option) => (
          <Pressable
            key={option}
            accessibilityRole="radio"
            accessibilityState={{ selected: value === option }}
            accessibilityLabel={option}
            onPress={() => onChange(option)}
            style={[styles.choice, value === option && styles.choiceSelected]}
          >
            <Text style={styles.body}>{option}</Text>
          </Pressable>
        ))}
      </View>
    );
  }
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>
        {field.label}
        {field.required ? "" : " (optional)"}
      </Text>
      <TextInput
        accessibilityLabel={field.label}
        style={[styles.input, field.type === "multiline" && styles.inputMultiline]}
        value={value}
        onChangeText={onChange}
        placeholder={placeholderFor(field)}
        placeholderTextColor={storeLight.text.muted}
        multiline={field.type === "multiline"}
        autoCapitalize={field.type === "country" ? "characters" : field.type === "name" ? "words" : "sentences"}
        keyboardType={field.type === "phone" ? "phone-pad" : "default"}
      />
    </View>
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
  field: { gap: 6 },
  fieldLabel: { color: storeLight.text.muted, fontSize: 13, fontWeight: "700" },
  input: { minHeight: 48, borderWidth: 1, borderColor: storeLight.border.hairline, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12, color: storeLight.text.primary, fontSize: 15 },
  inputMultiline: { minHeight: 84, textAlignVertical: "top" },
  choice: { minHeight: 48, borderWidth: 1, borderColor: storeLight.border.hairline, borderRadius: 12, paddingHorizontal: 14, justifyContent: "center" },
  choiceSelected: { borderColor: MARKETPLACE_CART_CTA.to, borderWidth: 2 },
  editLink: { color: storeLight.text.link, fontSize: 14, fontWeight: "800", paddingVertical: 6 },
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
