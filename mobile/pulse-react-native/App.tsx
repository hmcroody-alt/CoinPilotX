import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, BackHandler, Linking, Platform, Share, StatusBar, Text, TouchableOpacity, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import WebView, { WebViewMessageEvent, WebViewNavigation } from "react-native-webview";
import type { WebView as WebViewType } from "react-native-webview";
import { NativeLiveBroadcast } from "./components/NativeLiveBroadcast";
import { colors, screenStyles } from "./components/theme";
import { ensureNotificationPresentation, getNativePushToken, presentNativeDeviceAlert, wireNotificationLinks, wireNotificationPresentation } from "./services/push";

const PULSESOC_ORIGIN = "https://pulsesoc.com";
const PULSESOC_START_URL = `${PULSESOC_ORIGIN}/login?next=/pulse`;
const PULSESOC_HOSTS = new Set(["pulsesoc.com", "www.pulsesoc.com"]);
const PULSESOC_NATIVE_USER_AGENT = `PulseSocNativeApp/1.0 (${Platform.OS}; com.pulsesoc.app)`;

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
  const [sourceUrl, setSourceUrl] = useState(PULSESOC_START_URL);
  const [canGoBack, setCanGoBack] = useState(false);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [nativeLiveOpen, setNativeLiveOpen] = useState(false);

  const injectedJavaScript = useMemo(() => createInjectedBridge(), []);

  const navigateToAppUrl = useCallback((incomingUrl: string) => {
    const next = toPulseSocWebUrl(incomingUrl);
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

  const handleMessage = useCallback(async (event: WebViewMessageEvent) => {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(event.nativeEvent.data);
    } catch {
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
      setNativeLiveOpen(true);
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
  }, [sourceUrl]);

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
            device_type: 'native_webview'
          })
        });
        window.dispatchEvent(new CustomEvent('PulseSocNativeMessage', { detail: { type: 'PULSESOC_PUSH_RESULT', ok: response.ok } }));
      } catch (error) {
        window.dispatchEvent(new CustomEvent('PulseSocNativeMessage', { detail: { type: 'PULSESOC_PUSH_RESULT', ok: false, message: 'Push registration failed.' } }));
      }
    })();
    true;
  `);
}

function createInjectedBridge() {
  return `
    (function () {
      if (window.PulseSocNative) return true;
      function post(payload) {
        try {
          window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify(payload));
        } catch (error) {}
      }
      window.PulseSocNative = {
        registerPush: function () { post({ type: 'PULSESOC_REGISTER_PUSH' }); },
        goLive: function (payload) { post(Object.assign({ type: 'PULSESOC_OPEN_NATIVE_LIVE' }, payload || {})); },
        openNativeLive: function (payload) { post(Object.assign({ type: 'PULSESOC_OPEN_NATIVE_LIVE' }, payload || {})); },
        share: function (payload) { post(Object.assign({ type: 'PULSESOC_SHARE' }, payload || {})); },
        notify: function (payload) { post(Object.assign({ type: 'PULSESOC_NOTIFY_DEVICE' }, payload || {})); }
      };
      document.addEventListener('click', function (event) {
        var target = event.target && event.target.closest && event.target.closest('[data-go-live-native],[data-open-native-live],[href="/pulse/live"][data-native-live]');
        if (!target) return;
        event.preventDefault();
        post({ type: 'PULSESOC_OPEN_NATIVE_LIVE', source: 'web-click' });
      }, true);
      window.dispatchEvent(new CustomEvent('PulseSocNativeReady'));
      true;
    })();
  `;
}
