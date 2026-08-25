import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AppState, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { openMarketplaceCheckout } from "../api/marketplace";
import {
  firstMissingFulfillmentField,
  fulfillmentDestinationSummary,
  fulfillmentFields,
  fulfillmentNeedsAddress,
  fulfillmentTypeLabel,
  isAutoFilledField,
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
import { fetchShippingCountries, type CheckoutCountry } from "../api/checkoutCountries";
import {
  deviceTimezone,
  formatDateLabel,
  formatTimeLabel,
  fromIsoDate,
  fromWireTime,
  timezoneDisplayLabel
} from "../api/checkoutSchedule";
import {
  isPaymentSheetAvailable,
  presentPaymentSheet,
  type PaymentSheetBootstrap
} from "../api/stripePaymentSheet";
import { RootStackParamList } from "../navigation/types";
import { checkoutDark, STORE_CTA } from "../theme/marketplaceCheckoutDark";
import { PaymentController } from "../payments/PaymentController";
import {
  CheckoutStepper,
  DateField,
  InfoNote,
  PrimaryButton,
  ProductSummaryCard,
  RadioRow,
  SecondaryButton,
  Section,
  SelectField,
  SummaryRow,
  TextField,
  TimeField,
  TimezoneNote,
  type CheckoutStepIndex,
  type SelectOption
} from "./marketplace/CheckoutControls";

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

/** The step the progress bar should light. `opening` is still Payment — the
 * sheet is being built — and `failed` returns the buyer to Review, which is
 * where the retry lives. */
function stepIndexFor(stage: Stage): CheckoutStepIndex {
  if (stage === "details") return 0;
  if (stage === "review" || stage === "failed") return 1;
  if (stage === "confirmed") return 3;
  return 2;
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

/** Keyboard and capitalisation per field type, so a postcode does not open a
 * prose keyboard and a name is not lowercased. */
function inputTraitsFor(field: FulfillmentField) {
  if (field.type === "phone") return { keyboardType: "phone-pad" as const, autoCapitalize: "none" as const, textContentType: "telephoneNumber" as const };
  if (field.type === "name") return { keyboardType: "default" as const, autoCapitalize: "words" as const, textContentType: "name" as const };
  if (field.key === "address_line1") return { keyboardType: "default" as const, autoCapitalize: "words" as const, textContentType: "streetAddressLine1" as const };
  if (field.key === "address_line2") return { keyboardType: "default" as const, autoCapitalize: "words" as const, textContentType: "streetAddressLine2" as const };
  if (field.key === "address_city") return { keyboardType: "default" as const, autoCapitalize: "words" as const, textContentType: "addressCity" as const };
  if (field.key === "address_region") return { keyboardType: "default" as const, autoCapitalize: "words" as const, textContentType: "addressState" as const };
  if (field.key === "address_postal_code") return { keyboardType: "default" as const, autoCapitalize: "characters" as const, textContentType: "postalCode" as const };
  return { keyboardType: "default" as const, autoCapitalize: "sentences" as const, textContentType: undefined };
}

/**
 * Native review and authoritative post-payment state for Marketplace.
 *
 * Stripe still owns card entry and any enabled wallet presentation. Returning
 * from Stripe only starts polling; this screen shows success exclusively when
 * PulseSoc's authenticated order endpoint reports the webhook-confirmed paid
 * state. That keeps receipts, inventory capture and seller proceeds aligned.
 *
 * What the buyer is *asked* is decided by the order's fulfilment kind, which is
 * resolved server-side from the stored listing row — see
 * `services/marketplace_fulfillment.py`. This screen renders that decision; it
 * does not make it. Every control below is chosen so it cannot emit a value the
 * server would reject: the calendar produces the ISO date, the clock produces
 * 24-hour time, the country list is the one the server said it accepts, and the
 * timezone is read from the device rather than typed.
 */
export function MarketplaceCheckoutScreen({ route, navigation }: Props) {
  const params = route.params;
  const insets = useSafeAreaInsets();
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

  // The device's timezone, seeded once. It is a required field for every
  // scheduled kind and the buyer is never shown an input for it — see
  // `AUTO_FILLED_FIELD_KEYS`. Seeding at mount rather than at submit means the
  // review step can state the zone it is actually sending.
  const timezone = useMemo(() => deviceTimezone(), []);
  const [details, setDetails] = useState<Record<string, string>>({ timezone });
  const needsDetailsStep = mustChooseLane || fulfillmentFields(declaredKind, tickets).length > 0;
  const [stage, setStage] = useState<Stage>(needsDetailsStep ? "details" : "review");
  const [transactionIds, setTransactionIds] = useState<number[]>([]);
  const [checkoutUrl, setCheckoutUrl] = useState("");
  // The native-sheet bootstrap for this intent, cached alongside the ids so a
  // retry re-presents the same PaymentIntent rather than minting a second one.
  const [sheet, setSheet] = useState<PaymentSheetBootstrap | null>(null);
  const [message, setMessage] = useState("");
  const [countries, setCountries] = useState<CheckoutCountry[]>([]);
  const checking = useRef(false);

  const needsAddress = fulfillmentNeedsAddress(kind);

  // Only fetched when an address is actually going to be asked for. A digital
  // download has no country field, so the request would be pure overhead on the
  // step that most needs to feel instant.
  useEffect(() => {
    if (!needsAddress || countries.length) return;
    let alive = true;
    void fetchShippingCountries().then((list) => { if (alive) setCountries(list); });
    return () => { alive = false; };
  }, [countries.length, needsAddress]);

  useLayoutEffect(() => {
    navigation.setOptions({
      headerStyle: { backgroundColor: checkoutDark.bg.page },
      headerTintColor: checkoutDark.text.primary,
      headerTitleStyle: { color: checkoutDark.text.primary },
      headerShadowVisible: false
    });
  }, [navigation]);

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
  const typeLabel = params.listingTypeLabel || fulfillmentTypeLabel(kind);
  const timezoneLabel = useMemo(() => timezoneDisplayLabel(timezone), [timezone]);

  const setField = useCallback((key: string, next: string) => {
    setDetails((current) => ({ ...current, [key]: next }));
    setMessage("");
  }, []);

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

  const summary = (
    <ProductSummaryCard
      title={params.itemTitle || "Marketplace items"}
      seller={params.sellerName || ""}
      typeLabel={typeLabel}
      price={amount}
      quantity={params.quantity}
      imageUrl={params.imageUrl}
    />
  );

  if (stage === "confirmed") {
    const primaryId = transactionIds[0];
    return (
      <ScrollView style={styles.root} contentContainerStyle={[styles.centerContent, { paddingBottom: insets.bottom + 32 }]}>
        <CheckoutStepper current={3} />
        <View style={styles.check}><Ionicons name="checkmark" size={40} color={STORE_CTA.text} /></View>
        <Text style={styles.confirmedTitle}>Order confirmed</Text>
        <Text style={styles.centerCopy}>{confirmationCopy(kind)}</Text>
        <Section>
          <SummaryRow label="Order" value={`#${primaryId}`} />
          <SummaryRow label="Seller" value={params.sellerName || "PulseSoc seller"} />
          <SummaryRow label={params.itemTitle || "Item"} value={params.quantity && params.quantity > 1 ? `×${params.quantity}` : ""} />
          <SummaryRow label={destinationTitle(kind)} value={fulfillmentDestinationSummary(kind, details)} />
          {isScheduled(kind) && details.scheduled_date ? (
            <SummaryRow label="When" value={scheduleSentence(details, timezoneLabel)} />
          ) : null}
          <View style={styles.rule} />
          <SummaryRow label="Amount paid" value={amount} strong />
        </Section>
        <PrimaryButton
          label="View order and receipt"
          icon="receipt-outline"
          onPress={() => navigation.replace("BuyerOrderDetail", { orderId: primaryId, source: "seller_transactions", title: "Order confirmed" })}
        />
        <SecondaryButton label="Continue shopping" onPress={() => navigation.navigate("MarketplaceDetail", { title: "Marketplace" })} />
      </ScrollView>
    );
  }

  if (stage === "processing") {
    return (
      <ScrollView style={styles.root} contentContainerStyle={[styles.centerContent, { paddingBottom: insets.bottom + 32 }]}>
        <CheckoutStepper current={2} />
        <View style={styles.processingMark}><Ionicons name="hourglass-outline" size={32} color={checkoutDark.text.accent} /></View>
        <Text style={styles.confirmedTitle}>Processing your payment</Text>
        <Text style={styles.centerCopy}>Please do not close this screen or start another checkout. We'll confirm as soon as your payment clears.</Text>
        {message ? <Text style={styles.note}>{message}</Text> : null}
        <PrimaryButton label="Check payment status" icon="refresh" onPress={() => void checkStatus()} />
      </ScrollView>
    );
  }

  // Everything the buyer has to tell the seller, asked before the total is shown
  // and long before a card is. Nothing on this step is optional to reach: the
  // review step below cannot be entered until it passes.
  if (stage === "details") {
    const blocked = (mustChooseLane && !lane) || !!firstMissingFulfillmentField(kind, tickets, details);
    return (
      <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 28 }]} keyboardShouldPersistTaps="handled">
          <CheckoutStepper current={0} />
          {summary}
          <Text style={styles.subtitle}>{detailsSubtitle(declaredKind)}</Text>

          {mustChooseLane ? (
            <Section title="How do you want it?">
              <Text style={styles.muted}>This seller offers more than one option. Pick one — it decides what else you need to give.</Text>
              {lanesFor(declaredKind).map((option) => (
                <RadioRow
                  key={option}
                  selected={lane === option}
                  title={LANE_COPY[option].title}
                  detail={LANE_COPY[option].detail}
                  onPress={() => { setLane(option); setMessage(""); }}
                />
              ))}
            </Section>
          ) : null}

          {kind === "digital" ? (
            <Section title="Digital delivery">
              <InfoNote>
                You'll receive access and download information in your PulseSoc account as soon as your payment is confirmed. Nothing is shipped.
              </InfoNote>
            </Section>
          ) : null}

          {fields.length ? renderFieldGroups({ kind, fields, details, setField, countries, timezoneLabel }) : null}

          {message ? <Text style={styles.error}>{message}</Text> : null}
          <PrimaryButton label="Continue to Review" disabled={blocked} onPress={() => { setMessage(""); setStage("review"); }} />
          <Text style={styles.footnote}>No payment will be taken yet.</Text>
        </ScrollView>
      </KeyboardAvoidingView>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 28 }]}>
      <CheckoutStepper current={stepIndexFor(stage)} />
      {summary}

      <Section title={destinationTitle(kind)}>
        <Text style={styles.body}>{fulfillmentDestinationSummary(kind, details)}</Text>
        {isScheduled(kind) && details.scheduled_date ? (
          <Text style={styles.muted}>{scheduleSentence(details, timezoneLabel)}</Text>
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
      {/* The CTA states an amount only when this screen knows the exact charge.
          Otherwise it promises nothing it cannot keep. */}
      <PrimaryButton
        label={stage === "opening"
          ? "Opening secure payment…"
          : knowsFinalAmount
            ? `Pay securely · ${amount}`
            : "Continue to Payment"}
        icon={stage === "opening" ? null : "lock-closed"}
        busy={stage === "opening"}
        onPress={() => void beginCheckout()}
      />
      <Text style={styles.footnote}>Your order isn't confirmed until your payment clears.</Text>
    </ScrollView>
  );
}

/* ------------------------------------------------------------------ *
 * Field rendering
 * ------------------------------------------------------------------ */

const ADDRESS_KEYS = new Set([
  "address_line1", "address_line2", "address_city", "address_region", "address_postal_code", "address_country"
]);
const SCHEDULE_KEYS = new Set(["scheduled_date", "scheduled_time", "timezone"]);
const NOTE_KEYS = new Set(["notes", "delivery_notes"]);

/**
 * The server's flat field list, grouped the way a buyer reads a form.
 *
 * `fulfillmentFields` returns one ordered sequence because that is what the
 * validation contract is. A form is not one sequence though — contact, then
 * where, then when, then anything optional — so the grouping happens here,
 * against the same keys, without the server's list changing shape.
 *
 * Auto-filled keys are dropped from rendering entirely; `timezone` is the only
 * one, and it is still in `details` and still submitted.
 */
function renderFieldGroups({
  kind,
  fields,
  details,
  setField,
  countries,
  timezoneLabel
}: {
  kind: MarketplaceFulfillmentKind;
  fields: FulfillmentField[];
  details: Record<string, string>;
  setField: (key: string, next: string) => void;
  countries: CheckoutCountry[];
  timezoneLabel: string;
}) {
  const visible = fields.filter((field) => !isAutoFilledField(field.key));
  const contact = visible.filter((f) => !ADDRESS_KEYS.has(f.key) && !SCHEDULE_KEYS.has(f.key) && !NOTE_KEYS.has(f.key));
  const address = visible.filter((f) => ADDRESS_KEYS.has(f.key));
  const schedule = visible.filter((f) => SCHEDULE_KEYS.has(f.key));
  const notes = visible.filter((f) => NOTE_KEYS.has(f.key));

  const countryOptions: SelectOption[] = countries.map((c) => ({ value: c.code, label: c.name, hint: c.code }));

  return (
    <>
      {contact.length ? (
        <Section title={kind.startsWith("event_") ? "Attendee information" : "Contact information"}>
          {contact.map((field) => renderField(field, details, setField, countryOptions))}
        </Section>
      ) : null}

      {schedule.length ? (
        <Section title={kind.startsWith("event_") ? "When" : "Appointment"}>
          {schedule.map((field) => renderField(field, details, setField, countryOptions))}
          <TimezoneNote label={timezoneLabel} />
        </Section>
      ) : null}

      {address.length ? (
        <Section title={kind === "shipping" ? "Shipping address" : "Service location"}>
          {address.map((field) => renderField(field, details, setField, countryOptions))}
        </Section>
      ) : null}

      {notes.length ? (
        <Section title={kind === "shipping" ? "Delivery note" : "Notes for the seller"}>
          {notes.map((field) => renderField(field, details, setField, countryOptions))}
        </Section>
      ) : null}
    </>
  );
}

function renderField(
  field: FulfillmentField,
  details: Record<string, string>,
  setField: (key: string, next: string) => void,
  countryOptions: SelectOption[]
) {
  const value = details[field.key] || "";
  const onChange = (next: string) => setField(field.key, next);

  if (field.type === "date") {
    return <DateField key={field.key} label={field.label} value={value} onChange={onChange} />;
  }
  if (field.type === "time") {
    return <TimeField key={field.key} label={field.label} value={value} onChange={onChange} />;
  }
  if (field.type === "country") {
    return (
      <SelectField
        key={field.key}
        label={field.label}
        value={value}
        options={countryOptions}
        onChange={onChange}
        placeholder={countryOptions.length ? "Select a country" : "Loading…"}
        sheetTitle="Country or region"
      />
    );
  }
  if (field.type === "choice") {
    return (
      <SelectField
        key={field.key}
        label={field.label}
        value={value}
        options={(field.options || []).map((option) => ({ value: option, label: option }))}
        onChange={onChange}
        placeholder="Select"
      />
    );
  }
  const traits = inputTraitsFor(field);
  return (
    <TextField
      key={field.key}
      label={field.label}
      value={value}
      onChange={onChange}
      optional={!field.required}
      multiline={field.type === "multiline"}
      keyboardType={traits.keyboardType}
      autoCapitalize={traits.autoCapitalize}
      textContentType={traits.textContentType}
      placeholder={field.type === "multiline" ? "Add a note for the seller…" : undefined}
    />
  );
}

/* ------------------------------------------------------------------ *
 * Copy
 * ------------------------------------------------------------------ */

function isScheduled(kind: MarketplaceFulfillmentKind) {
  return kind.startsWith("service_") || kind.startsWith("booking_");
}

/** `Tue, Aug 25 · 10:30 AM · Eastern Time` — the mockup's three scheduling
 * lines, read back as one sentence. Falls back to the stored strings if either
 * fails to parse, so a legacy order still renders something true. */
function scheduleSentence(details: Record<string, string>, timezoneLabel: string) {
  const date = fromIsoDate(details.scheduled_date || "");
  const time = fromWireTime(details.scheduled_time || "");
  return [
    date ? formatDateLabel(date) : details.scheduled_date,
    time ? formatTimeLabel(time) : details.scheduled_time,
    timezoneLabel
  ].filter(Boolean).join(" · ");
}

function confirmationCopy(kind: MarketplaceFulfillmentKind) {
  if (kind === "digital") return "Your download and access information is now available in your PulseSoc account.";
  if (kind === "pickup") return "Your seller will be in touch to arrange collection.";
  if (kind === "shipping") return "Your seller will prepare this item for shipment.";
  if (kind.startsWith("event_")) return "Your ticket is available in PulseSoc.";
  if (isScheduled(kind)) return "Your appointment is confirmed. The seller has your contact details.";
  return "Your payment went through and your receipt is ready.";
}

function detailsSubtitle(kind: MarketplaceFulfillmentKind) {
  if (kind === "shipping_or_pickup") return "We only ask for what's needed to fulfill your order.";
  if (kind === "service_choice") return "Tell us how this should happen, then when.";
  if (kind.startsWith("event_")) return "Tell us who's attending.";
  if (kind.startsWith("service_") || kind.startsWith("booking_")) return "Tell us when this should happen and how to reach you.";
  if (kind === "pickup") return "Tell us who's collecting, so the seller knows who to expect.";
  if (kind === "digital") return "Nothing ships — we only need where to send your access.";
  return "We only ask for information needed to fulfill your order.";
}

function destinationTitle(kind: MarketplaceFulfillmentKind) {
  if (kind === "digital") return "Delivery";
  if (kind === "pickup") return "Pickup";
  if (kind === "shipping") return "Ship to";
  if (kind.startsWith("event_")) return "Attendee";
  return "When and where";
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: checkoutDark.bg.page },
  content: { padding: checkoutDark.space.gutter, gap: checkoutDark.space.section },
  centerContent: { padding: checkoutDark.space.gutter, gap: checkoutDark.space.section, alignItems: "stretch" },
  subtitle: { color: checkoutDark.text.muted, fontSize: 14, lineHeight: 20 },
  body: { color: checkoutDark.text.primary, fontSize: 15, lineHeight: 21 },
  muted: { color: checkoutDark.text.muted, fontSize: 13, lineHeight: 18 },
  rule: { height: StyleSheet.hairlineWidth, backgroundColor: checkoutDark.border.hairline, marginVertical: 4 },
  editLink: { color: checkoutDark.text.accent, fontSize: 14, fontWeight: "800", paddingVertical: 6 },
  error: { color: checkoutDark.status.error, fontSize: 14, lineHeight: 20, textAlign: "center" },
  footnote: { color: checkoutDark.text.faint, fontSize: 12, lineHeight: 17, textAlign: "center" },
  check: {
    alignSelf: "center",
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: STORE_CTA.from,
    alignItems: "center",
    justifyContent: "center"
  },
  processingMark: {
    alignSelf: "center",
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: checkoutDark.bg.card,
    borderWidth: 2,
    borderColor: checkoutDark.border.strong,
    alignItems: "center",
    justifyContent: "center"
  },
  confirmedTitle: { color: checkoutDark.text.primary, fontSize: 24, fontWeight: "900", textAlign: "center" },
  centerCopy: { color: checkoutDark.text.muted, fontSize: 15, lineHeight: 21, textAlign: "center" },
  note: { color: checkoutDark.text.muted, fontSize: 13, lineHeight: 18, textAlign: "center" }
});
