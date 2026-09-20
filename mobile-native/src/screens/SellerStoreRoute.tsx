/**
 * Router — and access gate — for the `SellerStore` route.
 *
 * The Business "Sections" grid opens Store with `mode: "dashboard"`, and that
 * one mode renders the rebuilt dashboard. Every other mode — `overview`,
 * `apply`, `profile`, `create`, `payouts`, and the `orders` mode the Orders
 * card uses — still renders `SellerStoreScreen` exactly as before.
 *
 * The split lives here rather than in the navigator so that `SellerStore`
 * remains a single registered route: deep links (`pulse/merchant/...`), the
 * `MerchantDashboard` and `MerchantProfile` aliases, and any `navigate("SellerStore", …)`
 * call anywhere in the app all keep working without knowing there are two
 * screens behind them.
 *
 * That single-registration property is exactly why the seller gate belongs
 * here too. `navigate("SellerStore", …)` is called from the Business sections
 * grid, two dashboard-routing paths, a deep-link handler and a notification
 * router; gating at each caller would mean five copies of one rule, and the
 * sixth caller — the one added next month — would have none. Here there is one
 * copy and no way past it.
 *
 * Which modes are gated is a deliberate short list rather than "everything":
 *
 * - `dashboard`, `create` and `payouts` are seller *tools* and need approval.
 * - `apply` must stay open, or the gate would block the only screen that can
 *   clear it — a seller told to finish their application, sent to the
 *   application, and gated out of it.
 * - `orders` stays open because a suspended seller still owes their buyers
 *   fulfilment, refunds and dispute responses. The gate's own suspended copy
 *   routes *to* this mode, so gating it would close a door the gate opens.
 * - `profile` and `overview` are read-only views of the seller's own account.
 */

import { useCallback } from "react";
import { SellerStoreScreen } from "./SellerStoreScreen";
import { StoreDashboardScreen } from "./StoreDashboardScreen";
import { SellerAccessGate, type SellerAccessAction } from "../components/store/SellerAccessGate";
import { useSellerAccess } from "../marketplace/useSellerAccess";
import { RootStackParamList } from "../navigation/types";

type Props = {
  route?: { params?: RootStackParamList["SellerStore"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

/** True when this route should render the rebuilt Store dashboard. */
export function isStoreDashboardRoute(params?: RootStackParamList["SellerStore"]) {
  return params?.mode === "dashboard";
}

/** The modes that require an approved seller. See the note above on the omissions. */
const GATED_MODES = new Set(["dashboard", "create", "payouts"]);

export function requiresSellerApproval(params?: RootStackParamList["SellerStore"]) {
  return GATED_MODES.has(String(params?.mode ?? ""));
}

export function SellerStoreRoute(props: Props) {
  const gated = requiresSellerApproval(props.route?.params);
  const { state, loading, failed, unsupported, refresh } = useSellerAccess();

  const handleAction = useCallback(
    (action: SellerAccessAction) => {
      switch (action) {
        case "RETRY":
          void refresh();
          return;
        case "ORDERS":
          props.navigation.navigate("SellerStore", { mode: "orders", title: "Orders" });
          return;
        case "SUPPORT":
          props.navigation.navigate("TrustSafetySupport");
          return;
        case "APPLY":
        case "RESUME":
        case "RESPOND":
        case "STATUS":
        default:
          // All four land on the application screen. It already renders the
          // right panel for the applicant's own status — a draft resumes, a
          // submitted one shows the review state — so routing them to separate
          // screens would duplicate a decision the server already publishes.
          props.navigation.navigate("MerchantApply");
      }
    },
    [props.navigation, refresh]
  );

  // `unsupported` short-circuits the gate entirely. A deployment without the
  // access-state route has no verdict to honour and no new enforcement behind
  // it, so gating here would take the Store away from sellers on a server that
  // never agreed to gate them. The modes below re-check server-side regardless.
  if (gated && !unsupported && (loading || !state.store_access)) {
    return (
      <SellerAccessGate
        state={state}
        loading={loading}
        failed={failed}
        onAction={handleAction}
        testID="seller-access-gate"
      />
    );
  }

  if (isStoreDashboardRoute(props.route?.params)) {
    // The card-payment status travels down from here rather than being fetched
    // again inside the dashboard: this component already holds the seller
    // verdict — it is what decided the dashboard may render — and a second
    // reader would mean two requests per focus and a window in which the two
    // disagree. It is the *other* axis of the same state object, never the one
    // that opened the door: an approved seller with no Connect account gets the
    // full dashboard and a "Set up payments" card, not a gate.
    return <StoreDashboardScreen {...props} cardPaymentStatus={state.card_payment_status} />;
  }
  return <SellerStoreScreen {...props} />;
}
