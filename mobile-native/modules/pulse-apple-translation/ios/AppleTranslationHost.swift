import SwiftUI
import Translation

// The SwiftUI layer that actually vends `TranslationSession`.
//
// `.translationTask(_:action:)` is the only way to obtain a session, it comes
// from the Translation/SwiftUI cross-import overlay, and the session's lifetime
// is the lifetime of the view's task. Everything here therefore exists purely to
// keep a `.translationTask` mounted for each language pair that has work, and to
// hand the resulting session to the coordinator.
//
// The hierarchy is visually inert — 1×1pt, fully transparent, hit-testing off,
// hidden from accessibility — but it is genuinely in the view hierarchy, which
// is required both for `.translationTask` to fire and for Apple's own language
// download sheet to have somewhere to present from.

@available(iOS 18.0, *)
struct AppleTranslationHostRoot: View {
  @ObservedObject var coordinator: AppleTranslationCoordinator

  // Mount accounting belongs to `AppleTranslationHostView`, not here: SwiftUI's
  // `onAppear` has no reliably-paired teardown callback, so reporting a mount
  // from this layer as well would double-count against the coordinator's
  // refcount and mean it never reaches zero — i.e. logout would never reset.
  var body: some View {
    ZStack {
      ForEach(coordinator.hosts, id: \.self) { descriptor in
        AppleTranslationPairHost(descriptor: descriptor, coordinator: coordinator)
      }
    }
    .frame(width: 1, height: 1)
    .opacity(0.01)
    .allowsHitTesting(false)
    .accessibilityHidden(true)
  }
}

@available(iOS 18.0, *)
private struct AppleTranslationPairHost: View {
  let descriptor: AppleTranslationCoordinator.HostDescriptor
  let coordinator: AppleTranslationCoordinator

  /// Held in `@State` rather than recomputed inline so that a re-render of the
  /// parent cannot hand `.translationTask` a different `Configuration` value and
  /// restart an in-flight session.
  @State private var configuration: TranslationSession.Configuration?

  var body: some View {
    Color.clear
      .frame(width: 1, height: 1)
      .translationTask(configuration) { session in
        await coordinator.run(session: session, descriptor: descriptor)
      }
      .onAppear {
        guard configuration == nil else { return }
        configuration = Self.makeConfiguration(for: descriptor.pair)
      }
  }

  /// A nil `source` is meaningful, not a placeholder: it asks Apple to detect
  /// the source language, which is how Stage 4's automatic detection works.
  private static func makeConfiguration(
    for pair: TranslationPairKey
  ) -> TranslationSession.Configuration {
    TranslationSession.Configuration(
      source: pair.source.map { Locale.Language(identifier: $0) },
      target: Locale.Language(identifier: pair.target)
    )
  }
}

/// Rendered on iOS 15–17, where the Translation framework does not exist. Keeps
/// the RN tree identical across OS versions so no screen needs a version check.
struct AppleTranslationUnsupportedHost: View {
  var body: some View {
    Color.clear
      .frame(width: 1, height: 1)
      .allowsHitTesting(false)
      .accessibilityHidden(true)
  }
}
