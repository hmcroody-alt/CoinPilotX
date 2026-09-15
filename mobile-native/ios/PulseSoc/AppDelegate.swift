import Expo
import React
import ReactAppDependencyProvider
import CallKit
import PushKit

@UIApplicationMain
public class AppDelegate: ExpoAppDelegate {
  var window: UIWindow?

  var reactNativeDelegate: ExpoReactNativeFactoryDelegate?
  var reactNativeFactory: RCTReactNativeFactory?

  public override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    let delegate = ReactNativeDelegate()
    let factory = ExpoReactNativeFactory(delegate: delegate)
    delegate.dependencyProvider = RCTAppDependencyProvider()

    reactNativeDelegate = delegate
    reactNativeFactory = factory
    bindReactNativeFactory(factory)

#if os(iOS) || os(tvOS)
    window = UIWindow(frame: UIScreen.main.bounds)
    factory.startReactNative(
      withModuleName: "main",
      in: window,
      launchOptions: launchOptions)
#endif

    // Create the PushKit registry natively, at launch, rather than waiting for JS to ask.
    //
    // This looks like something `callKitNativeProvider.ts` could do on its own — it calls
    // `VoipPushNotification.registerVoipToken()`, which reaches the same `voipRegistration`.
    // But the case this whole feature exists for is a *terminated* app: iOS relaunches
    // PulseSoc to deliver the VoIP push, and it will only do that if a PKPushRegistry with
    // `desiredPushTypes` containing `.voIP` exists. At that moment React Native has not
    // started, so a JS-side registration has not run and cannot run in time. Registering
    // here means the registry exists before the push is dispatched on every launch path.
    //
    // `voipRegistration` sets the registry's delegate to the app delegate (see
    // RNVoipPushNotificationManager.m), which is why the PKPushRegistryDelegate conformance
    // below lives on this class. It is idempotent — the JS call is a no-op after this one.
    RNVoipPushNotificationManager.voipRegistration()

    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  // Linking API
  public override func application(
    _ app: UIApplication,
    open url: URL,
    options: [UIApplication.OpenURLOptionsKey: Any] = [:]
  ) -> Bool {
    return super.application(app, open: url, options: options) || RCTLinkingManager.application(app, open: url, options: options)
  }

  // Universal Links
  public override func application(
    _ application: UIApplication,
    continue userActivity: NSUserActivity,
    restorationHandler: @escaping ([UIUserActivityRestoring]?) -> Void
  ) -> Bool {
    let result = RCTLinkingManager.application(application, continue: userActivity, restorationHandler: restorationHandler)
    return super.application(application, continue: userActivity, restorationHandler: restorationHandler) || result
  }
}

// MARK: - PushKit (incoming calls)

/// PulseSoc's incoming-call delivery path on iOS.
///
/// The ordering rule in here is the whole feature. iOS 13 made it a termination offence to
/// accept a VoIP push and not report a call to CallKit, and it enforces that against the
/// *push*, not against the app's intentions: if this method returns without
/// `reportNewIncomingCall`, the process is killed, and after a few offences iOS stops
/// delivering VoIP pushes to the app at all. That failure is invisible in testing because
/// the first few pushes still work.
///
/// So nothing may happen before the CallKit report — no `fetch`, no token refresh, no
/// "check the call is still ringing" round trip, not even a JS bridge hop, because when the
/// app was launched *by* this push the bridge does not exist yet. Everything the system UI
/// needs is already in the payload (see `_incoming_payload` in
/// `services/pulsesoc_voip_push.py`, which is deliberately 10 flat keys and carries no Agora
/// credential). The server is re-consulted only after the user answers.
extension AppDelegate: PKPushRegistryDelegate {

  public func pushRegistry(
    _ registry: PKPushRegistry,
    didUpdate pushCredentials: PKPushCredentials,
    for type: PKPushType
  ) {
    guard type == .voIP else { return }
    // Hands the token to JS, which registers it against this device id so the backend can
    // suppress the ordinary alert push for this device only.
    RNVoipPushNotificationManager.didUpdatePushCredentials(pushCredentials, forType: type.rawValue as String)
  }

  public func pushRegistry(_ registry: PKPushRegistry, didInvalidatePushTokenFor type: PKPushType) {
    guard type == .voIP else { return }
    // An invalidated token must reach the server, or it keeps suppressing the alert push for
    // a device that can no longer be rung — the one failure mode that ends in a silent phone.
    // JS revokes it; the server also revokes on an APNs 410 as a backstop.
    NotificationCenter.default.post(name: Notification.Name("PulseSocVoipTokenInvalidated"), object: nil)
  }

  public func pushRegistry(
    _ registry: PKPushRegistry,
    didReceiveIncomingPushWith payload: PKPushPayload,
    for type: PKPushType,
    completion: @escaping () -> Void
  ) {
    guard type == .voIP else { completion(); return }

    let dict = payload.dictionaryPayload
    let event = dict["event"] as? String ?? "incoming_call"

    // The server issues the UUID (a UUIDv5 of the call's public id) and every layer reuses
    // it, so CallKit, the backend and Agora all name the same call. Falling back to a fresh
    // UUID here would break that identity, so a payload without one is dropped instead —
    // but the completion still runs, because not calling it also kills the app.
    guard let uuid = (dict["uuid"] as? String), !uuid.isEmpty else {
      completion()
      return
    }

    if event == "cancel_call" {
      // A cancel is still a VoIP push, so it still owes CallKit a report. Reporting a call
      // we are about to end looks redundant, and is: it exists purely to satisfy the iOS 13
      // rule. When the call is already on screen — the normal case — CallKit rejects the
      // duplicate UUID harmlessly and the `endCall` below tears down the real one.
      let reason = endedReason(for: dict["reason"] as? String)
      RNCallKeep.reportNewIncomingCall(
        uuid,
        handle: uuid,
        handleType: "generic",
        hasVideo: false,
        localizedCallerName: "PulseSoc",
        supportsHolding: false,
        supportsDTMF: false,
        supportsGrouping: false,
        supportsUngrouping: false,
        fromPushKit: true,
        payload: dict,
        withCompletionHandler: {
          RNCallKeep.endCall(withUUID: uuid, reason: reason)
          completion()
        }
      )
      RNVoipPushNotificationManager.didReceiveIncomingPush(withPayload: payload, forType: type.rawValue as String)
      return
    }

    let hasVideo = (dict["has_video"] as? Bool) ?? ((dict["call_type"] as? String) == "video")
    let callerName = dict["caller_name"] as? String ?? "PulseSoc caller"
    // `handle` is the call UUID, not the caller's user id: CallKit writes the handle into
    // Recents and the system call log, which is not a place for an internal identifier.
    let handle = dict["handle"] as? String ?? uuid

    RNCallKeep.reportNewIncomingCall(
      uuid,
      handle: handle,
      handleType: "generic",
      hasVideo: hasVideo,
      localizedCallerName: callerName,
      supportsHolding: true,
      supportsDTMF: false,
      supportsGrouping: false,
      supportsUngrouping: false,
      fromPushKit: true,
      payload: dict,
      withCompletionHandler: completion
    )

    // Only now, with CallKit already ringing, does JS hear about it.
    RNVoipPushNotificationManager.didReceiveIncomingPush(withPayload: payload, forType: type.rawValue as String)
  }

  /// Maps the server's cancel reason onto a CXCallEndedReason.
  ///
  /// This is the difference between answering on your iPhone and finding a phantom "missed
  /// call" on your iPad. `answeredElsewhere` and `declinedElsewhere` tell iOS the call was
  /// handled, so it is not logged as missed; a genuine caller hang-up is `unanswered`, which
  /// is logged as missed and should be.
  private func endedReason(for reason: String?) -> Int32 {
    switch reason ?? "" {
    case "answered_elsewhere": return Int32(CXCallEndedReason.answeredElsewhere.rawValue)
    case "declined_elsewhere", "declined": return Int32(CXCallEndedReason.declinedElsewhere.rawValue)
    case "failed": return Int32(CXCallEndedReason.failed.rawValue)
    default: return Int32(CXCallEndedReason.unanswered.rawValue)
    }
  }
}

class ReactNativeDelegate: ExpoReactNativeFactoryDelegate {
  // Extension point for config-plugins

  override func sourceURL(for bridge: RCTBridge) -> URL? {
    // needed to return the correct URL for expo-dev-client.
    bridge.bundleURL ?? bundleURL()
  }

  override func bundleURL() -> URL? {
#if DEBUG
    return RCTBundleURLProvider.sharedSettings().jsBundleURL(forBundleRoot: ".expo/.virtual-metro-entry")
#else
    return Bundle.main.url(forResource: "main", withExtension: "jsbundle")
#endif
  }
}
