import { ComponentType, createElement } from "react";
import { StyleSheet, View } from "react-native";

type RouteProps = { navigation: { goBack: () => void }; [key: string]: unknown };
type LoadedModule = Record<string, ComponentType<any>>;

/**
 * Lazy-load wrapper for the Business OS / Marketplace / Advertising routes.
 *
 * These sectors are open to every signed-in account; the module is still
 * `require`d on first render rather than at import time so the heavy commerce
 * screens stay out of the startup bundle.
 */
function lazyRoute(load: () => LoadedModule, exportName: string) {
  let Loaded: ComponentType<any> | null = null;
  return function BusinessRoute(props: RouteProps) {
    Loaded = Loaded || load()[exportName];
    return <View style={styles.host}>{createElement(Loaded, props)}</View>;
  };
}

export const ProtectedBusinessHubRoute = lazyRoute(() => require("./BusinessHubRoute"), "BusinessHubRoute");
export const ProtectedBusinessProfileScreen = lazyRoute(() => require("./BusinessProfileScreen"), "BusinessProfileScreen");
export const ProtectedBusinessBuyerPreviewScreen = lazyRoute(() => require("./BusinessBuyerPreviewScreen"), "BusinessBuyerPreviewScreen");
export const ProtectedMarketplaceManagerScreen = lazyRoute(() => require("./MarketplaceManagerScreen"), "MarketplaceManagerScreen");
export const ProtectedAdvertisingRoute = lazyRoute(() => require("./AdvertisingRoute"), "AdvertisingRoute");
export const ProtectedOrdersRoute = lazyRoute(() => require("./OrdersRoute"), "OrdersRoute");
export const ProtectedMessagesRoute = lazyRoute(() => require("./MessagesRoute"), "MessagesRoute");
export const ProtectedEventsRoute = lazyRoute(() => require("./EventsRoute"), "EventsRoute");
export const ProtectedActivityRoute = lazyRoute(() => require("./ActivityRoute"), "ActivityRoute");
export const ProtectedBusinessOsInsightsScreen = lazyRoute(() => require("./BusinessOsInsightsScreen"), "BusinessOsInsightsScreen");
export const ProtectedBusinessOsPaymentsScreen = lazyRoute(() => require("./BusinessOsPaymentsScreen"), "BusinessOsPaymentsScreen");
export const ProtectedSellerApplicationScreen = lazyRoute(() => require("./SellerApplicationScreen"), "SellerApplicationScreen");
export const ProtectedSellerListingComposerScreen = lazyRoute(() => require("./SellerListingComposerScreen"), "SellerListingComposerScreen");
export const ProtectedSellerStoreScreen = lazyRoute(() => require("./SellerStoreScreen"), "SellerStoreScreen");
export const ProtectedSellerStoreRoute = lazyRoute(() => require("./SellerStoreRoute"), "SellerStoreRoute");
export const ProtectedDeveloperSettingsScreen = lazyRoute(() => require("./settings/DeveloperSettingsScreen"), "DeveloperSettingsScreen");

const styles = StyleSheet.create({ host: { flex: 1, backgroundColor: "#030716" } });
