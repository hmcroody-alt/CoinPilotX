import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as Device from "expo-device";
import * as ImagePicker from "expo-image-picker";
import * as Notifications from "expo-notifications";
import { ActivityIndicator, BackHandler, Linking, Platform, Share, StatusBar, Text, TouchableOpacity, Vibration, View } from "react-native";
import { Camera } from "react-native-vision-camera";
import { SafeAreaProvider, SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import WebView, { WebViewMessageEvent, WebViewNavigation } from "react-native-webview";
import type { WebView as WebViewType } from "react-native-webview";
import { NativeLiveBroadcast } from "./components/NativeLiveBroadcast";
import { colors, screenStyles } from "./components/theme";
import { ensureNotificationPresentation, getInitialNotificationUrl, getNativePushToken, presentNativeDeviceAlert, setActiveConversationFromUrl, wireNotificationLinks, wireNotificationPresentation } from "./services/push";

const PULSESOC_ORIGIN = "https://pulsesoc.com";
const PULSESOC_START_URL = `${PULSESOC_ORIGIN}/login?next=/pulse`;
const PULSESOC_HOSTS = new Set(["pulsesoc.com", "www.pulsesoc.com"]);
const PULSESOC_NATIVE_USER_AGENT = `PulseSocNativeApp/1.0 (${Platform.OS}; com.pulsesoc.app)`;
const PULSESHELL_VERSION = "2026.06.30";
const PULSESHELL_PERFORMANCE_TIERS = ["ultra", "balanced", "battery-saver", "reduced-motion", "low-end"] as const;
const PULSESHELL_SERVER_VALIDATED_ACTIONS = new Set([
  "camera.requestPermission",
  "microphone.requestPermission",
  "live.startHostSession",
  "push.registerDevice",
  "share.openNativeShareSheet",
  "filePicker.open",
  "haptics.impact",
  "deepLinks.open",
  "permissions.request"
]);

type PulseShellPerformanceMode = typeof PULSESHELL_PERFORMANCE_TIERS[number];

export default function PulseSocMobileApp() {
  return (
    <SafeAreaProvider>
      <StatusBar barStyle="light-content" backgroundColor={colors.background} />
      <PulseSocWebShell />
    </SafeAreaProvider>
  );
}

function PulseSocWebShell() {
  const webViewRef = useRef<WebViewType>(null);
  const webRequestRef = useRef(new Map<string, { resolve: (value: Record<string, unknown>) => void; reject: (reason?: unknown) => void }>());
  const safeAreaInsets = useSafeAreaInsets();
  const [sourceUrl, setSourceUrl] = useState(PULSESOC_START_URL);
  const [canGoBack, setCanGoBack] = useState(false);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [nativeLiveOpen, setNativeLiveOpen] = useState(false);
  const [performanceMode, setPerformanceMode] = useState<PulseShellPerformanceMode>(() => detectNativePerformanceMode());

  const injectedJavaScript = useMemo(() => createInjectedBridge(), []);

  const navigateToAppUrl = useCallback((incomingUrl: string) => {
    const next = toPulseSocWebUrl(incomingUrl);
    setActiveConversationFromUrl(next);
    setOffline(false);
    setSourceUrl(next);
    webViewRef.current?.injectJavaScript(`window.location.href = ${JSON.stringify(next)}; true;`);
  }, []);

  const callWebApi = useCallback((path: string, options?: { method?: string; body?: unknown }) => {
    const requestId = `native-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const method = options?.method || "GET";
    const body = options?.body === undefined ? null : JSON.stringify(options.body);
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      webRequestRef.current.set(requestId, { resolve, reject });
      webViewRef.current?.injectJavaScript(`
        (async function () {
          try {
            var response = await fetch(${JSON.stringify(path)}, {
              method: ${JSON.stringify(method)},
              credentials: 'include',
              cache: 'no-store',
              headers: { 'Content-Type': 'application/json' },
              body: ${body === null ? "undefined" : JSON.stringify(body)}
            });
            var text = await response.text();
            var data = {};
            try { data = text ? JSON.parse(text) : {}; } catch (parseError) { data = { ok: response.ok, text: text }; }
            window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify({
              type: 'PULSESOC_WEB_API_RESULT',
              requestId: ${JSON.stringify(requestId)},
              ok: response.ok && data.ok !== false,
              status: response.status,
              data: data
            }));
          } catch (error) {
            window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify({
              type: 'PULSESOC_WEB_API_RESULT',
              requestId: ${JSON.stringify(requestId)},
              ok: false,
              status: 0,
              message: error && error.message ? error.message : 'Native bridge request failed.'
            }));
          }
        })();
        true;
      `);
      setTimeout(() => {
        const pending = webRequestRef.current.get(requestId);
        if (!pending) return;
        webRequestRef.current.delete(requestId);
        pending.reject(new Error("PulseSoc website did not answer the native live request in time."));
      }, 30000);
    });
  }, []);

  useEffect(() => {
    ensureNotificationPresentation().catch(() => undefined);
    Linking.getInitialURL().then(url => {
      if (url) navigateToAppUrl(url);
    }).catch(() => undefined);
    getInitialNotificationUrl().then(url => {
      if (url) navigateToAppUrl(url);
    }).catch(() => undefined);

    const linkSubscription = Linking.addEventListener("url", event => navigateToAppUrl(event.url));
    const notificationSubscription = wireNotificationLinks(url => navigateToAppUrl(url));
    const presentationSubscription = wireNotificationPresentation();
    return () => {
      linkSubscription.remove();
      notificationSubscription.remove();
      presentationSubscription.remove();
    };
  }, [navigateToAppUrl]);

  useEffect(() => {
    if (Platform.OS !== "android") return undefined;
    const subscription = BackHandler.addEventListener("hardwareBackPress", () => {
      if (canGoBack) {
        webViewRef.current?.goBack();
        return true;
      }
      return false;
    });
    return () => subscription.remove();
  }, [canGoBack]);

  useEffect(() => {
    postToWeb(webViewRef.current, {
      type: "PULSESHELL_PERFORMANCE_MODE",
      mode: performanceMode,
      tiers: PULSESHELL_PERFORMANCE_TIERS
    });
  }, [performanceMode]);

  const handleMessage = useCallback(async (event: WebViewMessageEvent) => {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(event.nativeEvent.data);
    } catch {
      return;
    }

    if (payload.type === "PULSESHELL_NATIVE_CALL") {
      try {
        const result = await handlePulseShellNativeCall(payload, {
          sourceUrl,
          performanceMode,
          safeAreaInsets,
          setPerformanceMode,
          navigateToAppUrl,
          openNativeLive: () => navigateToAppUrl(`${PULSESOC_ORIGIN}/pulse/live/studio?context_type=native`),
          registerPushToken: token => injectPushTokenRegistration(webViewRef.current, token),
          validatePulseShellCall: call => validatePulseShellCall(callWebApi, call)
        });
        postPulseShellResult(webViewRef.current, payload.requestId, result);
      } catch (error) {
        postPulseShellResult(webViewRef.current, payload.requestId, {
          ok: false,
          available: false,
          message: error instanceof Error ? error.message : "PulseShell native call failed."
        });
      }
      return;
    }

    if (payload.type === "PULSESOC_REGISTER_PUSH") {
      const result = await getNativePushToken();
      if (!result.ok) {
        postToWeb(webViewRef.current, { type: "PULSESOC_PUSH_RESULT", ok: false, message: result.message });
        return;
      }
      injectPushTokenRegistration(webViewRef.current, result.token);
      return;
    }

    if (payload.type === "PULSESOC_WEB_API_RESULT") {
      const requestId = typeof payload.requestId === "string" ? payload.requestId : "";
      const pending = webRequestRef.current.get(requestId);
      if (!pending) return;
      webRequestRef.current.delete(requestId);
      if (payload.ok) {
        pending.resolve((payload.data || {}) as Record<string, unknown>);
      } else {
        const data = (payload.data || {}) as Record<string, unknown>;
        pending.reject(new Error(String(data.message || data.error || payload.message || "PulseSoc request failed.")));
      }
      return;
    }

    if (payload.type === "PULSESOC_OPEN_NATIVE_LIVE") {
      navigateToAppUrl(`${PULSESOC_ORIGIN}/pulse/live/studio?context_type=native`);
      return;
    }

    if (payload.type === "PULSESOC_SHARE") {
      const url = typeof payload.url === "string" ? payload.url : sourceUrl;
      const title = typeof payload.title === "string" ? payload.title : "PulseSoc";
      const text = typeof payload.text === "string" ? payload.text : title;
      await Share.share({ title, message: `${text}\n${url}`, url }).catch(() => undefined);
      return;
    }

    if (payload.type === "PULSESOC_NOTIFY_DEVICE") {
      await presentNativeDeviceAlert(payload).catch(() => undefined);
    }
  }, [navigateToAppUrl, performanceMode, safeAreaInsets, sourceUrl]);

  const shouldStartLoad = useCallback((request: { url: string }) => {
    if (request.url === "about:blank") return true;
    const nextUrl = normalizeUrl(request.url);
    if (!nextUrl) return false;
    if (nextUrl.protocol === "pulse:") {
      navigateToAppUrl(request.url);
      return false;
    }
    if (nextUrl.protocol !== "http:" && nextUrl.protocol !== "https:") {
      Linking.openURL(request.url).catch(() => undefined);
      return false;
    }
    if (PULSESOC_HOSTS.has(nextUrl.hostname)) return true;
    Linking.openURL(request.url).catch(() => undefined);
    return false;
  }, [navigateToAppUrl]);

  function handleNavigation(navState: WebViewNavigation) {
    setCanGoBack(navState.canGoBack);
    setActiveConversationFromUrl(navState.url);
  }

  if (offline) {
    return <OfflineScreen onRetry={() => {
      setOffline(false);
      webViewRef.current?.reload();
    }} />;
  }

  return (
    <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: colors.background }}>
      <WebView
        ref={webViewRef}
        source={{ uri: sourceUrl }}
        applicationNameForUserAgent={PULSESOC_NATIVE_USER_AGENT}
        style={{ flex: 1, backgroundColor: colors.background }}
        containerStyle={{ backgroundColor: colors.background }}
        originWhitelist={["https://*", "http://*", "pulse://*"]}
        javaScriptEnabled
        domStorageEnabled
        sharedCookiesEnabled
        thirdPartyCookiesEnabled
        cacheEnabled
        androidLayerType="hardware"
        decelerationRate={Platform.OS === "ios" ? "normal" : undefined}
        automaticallyAdjustContentInsets={false}
        contentInsetAdjustmentBehavior="never"
        bounces
        overScrollMode="always"
        nestedScrollEnabled
        allowsBackForwardNavigationGestures
        allowsInlineMediaPlayback
        mediaPlaybackRequiresUserAction={false}
        pullToRefreshEnabled
        setSupportMultipleWindows={false}
        startInLoadingState
        injectedJavaScriptBeforeContentLoaded={injectedJavaScript}
        onMessage={handleMessage}
        onShouldStartLoadWithRequest={shouldStartLoad}
        onNavigationStateChange={handleNavigation}
        onLoadStart={() => setLoading(true)}
        onLoadEnd={() => setLoading(false)}
        onError={() => setOffline(true)}
        onHttpError={event => {
          if (event.nativeEvent.statusCode >= 500) setOffline(true);
        }}
        renderLoading={() => <LoadingOverlay />}
      />
      {loading ? <LoadingOverlay compact /> : null}
      {nativeLiveOpen ? (
        <NativeLiveBroadcast
          apiRequest={callWebApi}
          onClose={() => setNativeLiveOpen(false)}
          onOpenWebPath={(path) => {
            setNativeLiveOpen(false);
            navigateToAppUrl(`${PULSESOC_ORIGIN}${path}`);
          }}
        />
      ) : null}
    </SafeAreaView>
  );
}

function LoadingOverlay({ compact }: { compact?: boolean }) {
  return (
    <View pointerEvents="none" style={{
      position: "absolute",
      top: compact ? 10 : 0,
      right: compact ? 10 : 0,
      bottom: compact ? undefined : 0,
      left: compact ? undefined : 0,
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: compact ? "transparent" : colors.background
    }}>
      <ActivityIndicator color={colors.accent} />
    </View>
  );
}

function OfflineScreen({ onRetry }: { onRetry: () => void }) {
  return (
    <SafeAreaView edges={["top", "bottom"]} style={screenStyles.centered}>
      <Text style={screenStyles.title}>PulseSoc is offline</Text>
      <Text style={[screenStyles.subtitle, { textAlign: "center" }]}>Check your connection and try again. The app will reload the live PulseSoc website when you are back online.</Text>
      <TouchableOpacity style={screenStyles.button} onPress={onRetry}>
        <Text style={screenStyles.buttonText}>Retry</Text>
      </TouchableOpacity>
    </SafeAreaView>
  );
}

function normalizeUrl(value: string) {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

function toPulseSocWebUrl(incomingUrl: string) {
  if (incomingUrl.startsWith("/")) {
    return `${PULSESOC_ORIGIN}${incomingUrl}`;
  }
  const parsed = normalizeUrl(incomingUrl);
  if (!parsed) return PULSESOC_ORIGIN;
  if (parsed.protocol === "pulse:") {
    const path = [parsed.hostname, parsed.pathname].filter(Boolean).join("").replace(/^\/?/, "/");
    return `${PULSESOC_ORIGIN}${path === "/" ? "" : path}${parsed.search}${parsed.hash}`;
  }
  if (PULSESOC_HOSTS.has(parsed.hostname)) {
    parsed.protocol = "https:";
    parsed.hostname = "pulsesoc.com";
    return parsed.toString();
  }
  return PULSESOC_ORIGIN;
}

function postToWeb(webView: WebViewType | null, payload: Record<string, unknown>) {
  webView?.injectJavaScript(`
    window.dispatchEvent(new CustomEvent('PulseSocNativeMessage', { detail: ${JSON.stringify(payload)} }));
    true;
  `);
}

function postPulseShellResult(webView: WebViewType | null, requestId: unknown, payload: Record<string, unknown>) {
  webView?.injectJavaScript(`
    window.dispatchEvent(new CustomEvent('PulseShellNativeResult', {
      detail: Object.assign({ requestId: ${JSON.stringify(stringValue(requestId))} }, ${JSON.stringify(payload)})
    }));
    true;
  `);
}

function injectPushTokenRegistration(webView: WebViewType | null, token: string) {
  webView?.injectJavaScript(`
    (async function () {
      try {
        const response = await fetch('/api/push/subscribe', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            endpoint: ${JSON.stringify(token)},
            provider: 'expo',
            token: ${JSON.stringify(token)},
            subscription: { expo_push_token: ${JSON.stringify(token)} },
            device_type: 'native_webview',
            platform: ${JSON.stringify(Platform.OS)},
            app_version: '1.0.0'
          })
        });
        const text = await response.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch (parseError) { data = {}; }
        window.dispatchEvent(new CustomEvent('PulseSocNativeMessage', {
          detail: {
            type: 'PULSESOC_PUSH_RESULT',
            ok: response.ok && data.ok !== false,
            status: response.status,
            message: data.message || (response.ok ? 'Push device registered.' : 'Push registration failed.'),
            active_subscriptions: data.active_subscriptions || 0,
            active_devices: data.active_devices || 0
          }
        }));
      } catch (error) {
        window.dispatchEvent(new CustomEvent('PulseSocNativeMessage', { detail: { type: 'PULSESOC_PUSH_RESULT', ok: false, message: 'Push registration failed.' } }));
      }
    })();
    true;
  `);
}

async function validatePulseShellCall(
  callWebApi: (path: string, options?: { method?: string; body?: unknown }) => Promise<Record<string, unknown>>,
  call: Record<string, unknown>
) {
  const timestamp = Math.floor(Date.now() / 1000);
  const nonce = `ps-${timestamp}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
  return callWebApi("/api/pulseshell/validate", {
    method: "POST",
    body: {
      module: stringValue(call.module),
      action: stringValue(call.action),
      request_id: stringValue(call.requestId),
      timestamp,
      nonce,
      payload: call.payload && typeof call.payload === "object" ? call.payload : {}
    }
  });
}

type PulseShellCallContext = {
  sourceUrl: string;
  performanceMode: PulseShellPerformanceMode;
  safeAreaInsets: { top: number; right: number; bottom: number; left: number };
  setPerformanceMode: (mode: PulseShellPerformanceMode) => void;
  navigateToAppUrl: (url: string) => void;
  openNativeLive: () => void;
  registerPushToken: (token: string) => void;
  validatePulseShellCall: (payload: Record<string, unknown>) => Promise<Record<string, unknown>>;
};

async function handlePulseShellNativeCall(payload: Record<string, unknown>, context: PulseShellCallContext): Promise<Record<string, unknown>> {
  const moduleName = stringValue(payload.module);
  const action = stringValue(payload.action);
  const body = (payload.payload && typeof payload.payload === "object" ? payload.payload : {}) as Record<string, unknown>;
  const key = `${moduleName}.${action}`;
  if (PULSESHELL_SERVER_VALIDATED_ACTIONS.has(key)) {
    const validation = await context.validatePulseShellCall({
      module: moduleName,
      action,
      requestId: stringValue(payload.requestId),
      payload: body
    });
    if (!validation.ok) {
      return {
        ok: false,
        available: true,
        serverAuthoritative: true,
        message: stringValue(validation.message) || "PulseShell request was not approved by the server.",
        reason: stringValue(validation.reason) || "server_validation_failed"
      };
    }
  }

  switch (key) {
    case "device.getInfo":
      return ok({
        shell: "PulseShell",
        shellVersion: PULSESHELL_VERSION,
        native: true,
        platform: Platform.OS,
        appVersion: "1.0.0",
        sourceUrl: context.sourceUrl,
        isDevice: Device.isDevice,
        brand: Device.brand || "",
        manufacturer: Device.manufacturer || "",
        modelName: Device.modelName || "",
        osName: Device.osName || Platform.OS,
        osVersion: Device.osVersion || "",
        performanceMode: context.performanceMode
      });

    case "performance.getMode":
      return ok({ mode: context.performanceMode, tiers: PULSESHELL_PERFORMANCE_TIERS });

    case "performance.setMode": {
      const requested = normalizePerformanceMode(body.mode);
      if (!requested) return unavailable("Unsupported performance mode.");
      context.setPerformanceMode(requested);
      return ok({ mode: requested, tiers: PULSESHELL_PERFORMANCE_TIERS });
    }

    case "safeArea.getInsets":
      return ok({ insets: context.safeAreaInsets });

    case "deepLinks.open": {
      const url = stringValue(body.url || body.path);
      if (!url) return unavailable("No route was provided.");
      context.navigateToAppUrl(url);
      return ok({ url });
    }

    case "live.startHostSession":
      context.openNativeLive();
      return ok({ opened: true });

    case "push.registerDevice": {
      const result = await getNativePushToken();
      if (!result.ok) return { ok: false, available: true, message: result.message };
      context.registerPushToken(result.token);
      return ok({ registered: true, provider: "expo", tokenReturnedToWeb: false });
    }

    case "share.openNativeShareSheet": {
      const url = stringValue(body.url) || context.sourceUrl;
      const title = stringValue(body.title) || "PulseSoc";
      const text = stringValue(body.text) || title;
      await Share.share({ title, message: `${text}\n${url}`, url });
      return ok({ shared: true });
    }

    case "haptics.impact": {
      const style = stringValue(body.style || body.level || "medium").toLowerCase();
      Vibration.vibrate(style === "heavy" ? 55 : style === "light" ? 18 : 32);
      return ok({ style: style === "heavy" || style === "light" ? style : "medium" });
    }

    case "permissions.check":
      return checkPermission(stringValue(body.permission || body.name));

    case "permissions.request":
      return requestPermission(stringValue(body.permission || body.name));

    case "camera.requestPermission":
      return requestPermission("camera");

    case "microphone.requestPermission":
      return requestPermission("microphone");

    case "filePicker.open":
      return openNativeFilePicker(body);

    default:
      return unavailable(`PulseShell module '${moduleName || "unknown"}' action '${action || "unknown"}' is not available in this build.`);
  }
}

async function checkPermission(permission: string): Promise<Record<string, unknown>> {
  const normalized = normalizePermissionName(permission);
  if (normalized === "notifications") {
    const status = await Notifications.getPermissionsAsync();
    return ok({ permission: normalized, status: permissionStatus(status) });
  }
  if (normalized === "camera") {
    const status = await callVisionCameraPermission("getCameraPermissionStatus");
    return status ? ok({ permission: normalized, status }) : unavailable("Camera permission status is unavailable in this native build.");
  }
  if (normalized === "microphone") {
    const status = await callVisionCameraPermission("getMicrophonePermissionStatus");
    return status ? ok({ permission: normalized, status }) : unavailable("Microphone permission status is unavailable in this native build.");
  }
  if (normalized === "photo-library") {
    const status = await ImagePicker.getMediaLibraryPermissionsAsync();
    return ok({ permission: normalized, status: permissionStatus(status) });
  }
  return unavailable("Unsupported permission.");
}

async function requestPermission(permission: string): Promise<Record<string, unknown>> {
  const normalized = normalizePermissionName(permission);
  if (normalized === "notifications") {
    const status = await Notifications.requestPermissionsAsync();
    return ok({ permission: normalized, status: permissionStatus(status) });
  }
  if (normalized === "camera") {
    const status = await callVisionCameraPermission("requestCameraPermission");
    return status ? ok({ permission: normalized, status }) : unavailable("Camera permission request is unavailable in this native build.");
  }
  if (normalized === "microphone") {
    const status = await callVisionCameraPermission("requestMicrophonePermission");
    return status ? ok({ permission: normalized, status }) : unavailable("Microphone permission request is unavailable in this native build.");
  }
  if (normalized === "photo-library") {
    const status = await ImagePicker.requestMediaLibraryPermissionsAsync();
    return ok({ permission: normalized, status: permissionStatus(status) });
  }
  return unavailable("Unsupported permission.");
}

async function callVisionCameraPermission(methodName: string): Promise<unknown> {
  const method = (Camera as unknown as Record<string, unknown>)[methodName];
  if (typeof method !== "function") return "";
  return method.call(Camera);
}

async function openNativeFilePicker(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  const allowsMultipleSelection = payload.multiple !== false;
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ["images", "videos"] as ImagePicker.MediaType[],
    allowsMultipleSelection,
    quality: 0.86,
    videoQuality: ImagePicker.UIImagePickerControllerQualityType.Medium
  });
  if (result.canceled) return ok({ canceled: true, assets: [] });
  return ok({
    canceled: false,
    assets: result.assets.map(asset => ({
      uri: asset.uri,
      fileName: asset.fileName || "",
      mimeType: asset.mimeType || "",
      width: asset.width || 0,
      height: asset.height || 0,
      duration: asset.duration || 0,
      type: asset.type || ""
    }))
  });
}

function detectNativePerformanceMode(): PulseShellPerformanceMode {
  if (!Device.isDevice) return "balanced";
  if (Platform.OS === "android") return "balanced";
  return "balanced";
}

function normalizePerformanceMode(value: unknown): PulseShellPerformanceMode | "" {
  const mode = stringValue(value).toLowerCase().replace(/_/g, "-");
  return PULSESHELL_PERFORMANCE_TIERS.includes(mode as PulseShellPerformanceMode) ? mode as PulseShellPerformanceMode : "";
}

function normalizePermissionName(value: unknown) {
  const permission = stringValue(value).toLowerCase().replace(/_/g, "-");
  if (["camera", "microphone", "notifications", "photo-library"].includes(permission)) return permission;
  if (permission === "photos" || permission === "photo" || permission === "media-library") return "photo-library";
  if (permission === "push" || permission === "push-notifications") return "notifications";
  if (permission === "mic") return "microphone";
  return permission;
}

function permissionStatus(value: unknown) {
  const permission = value as { granted?: boolean; status?: string; canAskAgain?: boolean };
  return {
    granted: permission.granted === true || permission.status === "granted",
    status: permission.status || (permission.granted ? "granted" : "undetermined"),
    canAskAgain: permission.canAskAgain !== false
  };
}

function ok(data: Record<string, unknown>) {
  return { ok: true, available: true, data };
}

function unavailable(message: string) {
  return { ok: false, available: false, message };
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value.trim() : value === undefined || value === null ? "" : String(value).trim();
}

function createInjectedBridge() {
  return `
    (function () {
      if (window.PulseSocNative && window.PulseShell) return true;
      var pending = window.__PulseShellPending || (window.__PulseShellPending = {});
      var shellVersion = ${JSON.stringify(PULSESHELL_VERSION)};
      function post(payload) {
        try {
          window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify(payload));
        } catch (error) {}
      }
      function nativeCall(moduleName, action, payload, timeoutMs) {
        return new Promise(function (resolve) {
          var requestId = 'pulseshell-' + Date.now() + '-' + Math.random().toString(16).slice(2);
          var timer = setTimeout(function () {
            if (!pending[requestId]) return;
            delete pending[requestId];
            resolve({ ok: false, available: false, message: 'PulseShell native bridge timed out.' });
          }, Math.max(1000, timeoutMs || 15000));
          pending[requestId] = function (result) {
            clearTimeout(timer);
            resolve(result || { ok: false, available: false, message: 'PulseShell returned an empty result.' });
          };
          post({
            type: 'PULSESHELL_NATIVE_CALL',
            requestId: requestId,
            module: moduleName,
            action: action,
            payload: payload || {}
          });
        });
      }
      window.addEventListener('PulseShellNativeResult', function (event) {
        var detail = event && event.detail ? event.detail : {};
        var requestId = detail.requestId;
        if (!requestId || !pending[requestId]) return;
        var callback = pending[requestId];
        delete pending[requestId];
        callback(detail);
      });
      window.PulseShell = {
        version: shellVersion,
        platform: ${JSON.stringify(Platform.OS)},
        isNative: true,
        isAvailable: true,
        camera: { requestPermission: function () { return nativeCall('camera', 'requestPermission'); } },
        microphone: { requestPermission: function () { return nativeCall('microphone', 'requestPermission'); } },
        live: { startHostSession: function (payload) { return nativeCall('live', 'startHostSession', payload); } },
        push: { registerDevice: function (payload) { return nativeCall('push', 'registerDevice', payload, 30000); } },
        share: { openNativeShareSheet: function (payload) { return nativeCall('share', 'openNativeShareSheet', payload); } },
        filePicker: { open: function (payload) { return nativeCall('filePicker', 'open', payload, 60000); } },
        haptics: { impact: function (style) { return nativeCall('haptics', 'impact', typeof style === 'object' ? style : { style: style || 'medium' }); } },
        deepLinks: { open: function (url) { return nativeCall('deepLinks', 'open', typeof url === 'object' ? url : { url: url }); } },
        device: { getInfo: function () { return nativeCall('device', 'getInfo'); } },
        permissions: {
          check: function (permission) { return nativeCall('permissions', 'check', typeof permission === 'object' ? permission : { permission: permission }); },
          request: function (permission) { return nativeCall('permissions', 'request', typeof permission === 'object' ? permission : { permission: permission }); }
        },
        performance: {
          getMode: function () { return nativeCall('performance', 'getMode'); },
          setMode: function (mode) { return nativeCall('performance', 'setMode', typeof mode === 'object' ? mode : { mode: mode }); }
        },
        safeArea: { getInsets: function () { return nativeCall('safeArea', 'getInsets'); } },
        backgroundAudio: { status: function () { return Promise.resolve({ ok: false, available: false, message: 'BackgroundAudioBridge is not enabled in this build.' }); } },
        payment: { status: function () { return Promise.resolve({ ok: false, available: false, message: 'PaymentBridge requires a dedicated compliant IAP/payment release.' }); } },
        crashRecovery: { status: function () { return Promise.resolve({ ok: false, available: false, message: 'CrashRecoveryBridge is planned; native offline fallback is active.' }); } }
      };
      window.PulseSocNative = {
        registerPush: function () { return window.PulseShell.push.registerDevice(); },
        goLive: function (payload) { return window.PulseShell.live.startHostSession(payload); },
        openNativeLive: function (payload) { return window.PulseShell.live.startHostSession(payload); },
        share: function (payload) { return window.PulseShell.share.openNativeShareSheet(payload); },
        notify: function (payload) { post(Object.assign({ type: 'PULSESOC_NOTIFY_DEVICE' }, payload || {})); }
      };
      document.addEventListener('click', function (event) {
        var target = event.target && event.target.closest && event.target.closest('[data-go-live-native],[data-open-native-live]');
        if (!target) return;
        event.preventDefault();
        post({ type: 'PULSESOC_OPEN_NATIVE_LIVE', source: 'web-click', path: '/pulse/live/studio?context_type=native' });
      }, true);
      window.dispatchEvent(new CustomEvent('PulseShellReady', { detail: { native: true, version: shellVersion, platform: ${JSON.stringify(Platform.OS)} } }));
      window.dispatchEvent(new CustomEvent('PulseSocNativeReady'));
      true;
    })();
  `;
}
