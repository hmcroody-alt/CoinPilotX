import { ComponentType, createElement, useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { getBusinessConstructionAccess, BusinessConstructionAccess, CONSTRUCTION_LOCKED } from "../api/businessConstruction";
import { GalacticConstructionScreen } from "./GalacticConstructionScreen";

type RouteProps = { navigation: { goBack: () => void }; [key: string]: unknown };
type LoadedModule = Record<string, ComponentType<any>>;

/**
 * Gate wrapper for every native Business OS / Marketplace / Advertising route.
 *
 * The construction screen renders *in place of* the requested route rather than
 * navigating anywhere, so the back stack is untouched.
 *
 * The protected module is only `require`d after the server has said yes, so a
 * locked sector never mounts or preloads.
 */
function protectedRoute(load: () => LoadedModule, exportName: string) {
  let Loaded: ComponentType<any> | null = null;
  return function ProtectedBusinessRoute(props: RouteProps) {
    const [access, setAccess] = useState<BusinessConstructionAccess | null>(null);

    useEffect(() => {
      let active = true;
      getBusinessConstructionAccess()
        .then((result) => { if (active) setAccess(result); })
        .catch(() => { if (active) setAccess(CONSTRUCTION_LOCKED); });
      return () => { active = false; };
    }, []);

    if (!access) return <View style={styles.loading}><ActivityIndicator color="#57D9FF" /><Text style={styles.loadingText}>Verifying sector access…</Text></View>;
    if (!access.can_access_private_business_os) {
      return <GalacticConstructionScreen onReturn={props.navigation.goBack} />;
    }
    Loaded = Loaded || load()[exportName];
    return <View style={styles.host}>{createElement(Loaded, props)}{access.developer_badge && access.construction_mode ? <View style={styles.badge}><Text style={styles.badgeText}>DEVELOPER • CONSTRUCTION</Text></View> : null}</View>;
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
