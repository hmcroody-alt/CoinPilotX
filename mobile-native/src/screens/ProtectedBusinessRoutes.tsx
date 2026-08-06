import { ComponentType, createElement, useCallback, useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { getBusinessConstructionAccess, BusinessConstructionAccess, CONSTRUCTION_LOCKED } from "../api/businessConstruction";
import { useAuth } from "../session/auth";
import { hasLocalEngineerAccess, subscribeToEngineerAccess } from "../security/engineerAccessSession";
import { emitEngineerAccessDiagnostic } from "../security/engineerAccessDiagnostics";
import { GalacticConstructionScreen } from "./GalacticConstructionScreen";

type RouteProps = { navigation: { goBack: () => void }; [key: string]: unknown };
type LoadedModule = Record<string, ComponentType<any>>;

/**
 * Gate wrapper for every native Business OS / Marketplace / Advertising route.
 *
 * The construction screen renders *in place of* the requested route rather than
 * navigating anywhere. That is what preserves the mission's "continue to the
 * originally requested destination" rule for free: once a grant is obtained,
 * this same component re-resolves and mounts the real screen, so the engineer
 * lands exactly where they were headed — Business, Marketplace, Advertising —
 * instead of a generic dashboard, and the back stack is untouched.
 *
 * The protected module is only `require`d after the server has said yes, so a
 * locked sector never mounts or preloads.
 */
/**
 * The verdict a locally-issued development grant stands in for. It exists only
 * because the server half of the gate is not deployed: `construction-access`
 * has no engineer path yet, so asking it would lock the sector no matter what
 * the modal just accepted. Reachable only from `hasLocalEngineerAccess`, which
 * is false unless the development fallback was compiled in.
 */
const LOCAL_ENGINEER_ACCESS: BusinessConstructionAccess = {
  ok: true,
  mode: "development",
  can_access_private_business_os: true,
  construction_mode: true,
  developer_mode: true,
  developer_badge: true,
  engineer_access: true
};

function protectedRoute(load: () => LoadedModule, exportName: string) {
  let Loaded: ComponentType<any> | null = null;
  return function ProtectedBusinessRoute(props: RouteProps) {
    const { authState } = useAuth();
    const userId = Number(authState.user?.user_id || 0);
    const [access, setAccess] = useState<BusinessConstructionAccess | null>(null);

    const resolveAccess = useCallback(() => {
      let active = true;
      emitEngineerAccessDiagnostic({ stage: "destination_requested", destination: exportName });
      if (hasLocalEngineerAccess(userId)) {
        setAccess(LOCAL_ENGINEER_ACCESS);
        return () => { active = false; };
      }
      // Re-ask the server rather than trusting any local flag. The request
      // carries the engineer grant automatically (see pulseApi), so this single
      // call covers both the owner path and the engineer path.
      getBusinessConstructionAccess()
        .then((result) => { if (active) setAccess(result); })
        .catch(() => { if (active) setAccess(CONSTRUCTION_LOCKED); });
      return () => { active = false; };
    }, [userId]);

    useEffect(resolveAccess, [resolveAccess]);
    // A grant obtained (or revoked) while this screen is mounted re-resolves it.
    useEffect(() => subscribeToEngineerAccess(() => { setAccess(null); resolveAccess(); }), [resolveAccess]);
    // In an effect rather than in the body: a render-phase emit repeats on every
    // re-render, which would turn "arrived at the destination" into noise.
    const arrived = Boolean(access?.can_access_private_business_os);
    useEffect(() => {
      if (arrived) emitEngineerAccessDiagnostic({ stage: "navigation_completed", destination: exportName });
    }, [arrived]);

    if (!access) return <View style={styles.loading}><ActivityIndicator color="#57D9FF" /><Text style={styles.loadingText}>Verifying sector access…</Text></View>;
    if (!access.can_access_private_business_os) {
      return <GalacticConstructionScreen onReturn={props.navigation.goBack} onEngineerAccessGranted={resolveAccess} />;
    }
    Loaded = Loaded || load()[exportName];
    const badge = access.engineer_access ? "ENGINEER • CONSTRUCTION" : "DEVELOPER • CONSTRUCTION";
    return <View style={styles.host}>{createElement(Loaded, props)}{access.developer_badge && access.construction_mode ? <View style={styles.badge}><Text style={styles.badgeText}>{badge}</Text></View> : null}</View>;
  };
}

export const ProtectedBusinessHubRoute = protectedRoute(() => require("./BusinessHubRoute"), "BusinessHubRoute");
export const ProtectedBusinessProfileScreen = protectedRoute(() => require("./BusinessProfileScreen"), "BusinessProfileScreen");
export const ProtectedBusinessBuyerPreviewScreen = protectedRoute(() => require("./BusinessBuyerPreviewScreen"), "BusinessBuyerPreviewScreen");
export const ProtectedMarketplaceManagerScreen = protectedRoute(() => require("./MarketplaceManagerScreen"), "MarketplaceManagerScreen");
export const ProtectedAdvertisingRoute = protectedRoute(() => require("./AdvertisingRoute"), "AdvertisingRoute");
export const ProtectedOrdersRoute = protectedRoute(() => require("./OrdersRoute"), "OrdersRoute");
export const ProtectedMessagesRoute = protectedRoute(() => require("./MessagesRoute"), "MessagesRoute");
export const ProtectedEventsRoute = protectedRoute(() => require("./EventsRoute"), "EventsRoute");
export const ProtectedActivityRoute = protectedRoute(() => require("./ActivityRoute"), "ActivityRoute");
export const ProtectedBusinessOsInsightsScreen = protectedRoute(() => require("./BusinessOsInsightsScreen"), "BusinessOsInsightsScreen");
export const ProtectedBusinessOsPaymentsScreen = protectedRoute(() => require("./BusinessOsPaymentsScreen"), "BusinessOsPaymentsScreen");
export const ProtectedSellerApplicationScreen = protectedRoute(() => require("./SellerApplicationScreen"), "SellerApplicationScreen");
export const ProtectedSellerListingComposerScreen = protectedRoute(() => require("./SellerListingComposerScreen"), "SellerListingComposerScreen");
export const ProtectedSellerStoreScreen = protectedRoute(() => require("./SellerStoreScreen"), "SellerStoreScreen");
export const ProtectedSellerStoreRoute = protectedRoute(() => require("./SellerStoreRoute"), "SellerStoreRoute");
export const ProtectedDeveloperSettingsScreen = protectedRoute(() => require("./settings/DeveloperSettingsScreen"), "DeveloperSettingsScreen");

const styles = StyleSheet.create({ loading: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#030716" }, loadingText: { color: "#AEBBD2", marginTop: 12 }, host: { flex: 1 }, badge: { position: "absolute", right: 10, top: 8, zIndex: 999, backgroundColor: "#291B55", borderColor: "#A98BFF", borderWidth: 1, borderRadius: 12, paddingHorizontal: 9, paddingVertical: 5 }, badgeText: { color: "#E9E0FF", fontSize: 9, fontWeight: "900", letterSpacing: 0.8 } });
