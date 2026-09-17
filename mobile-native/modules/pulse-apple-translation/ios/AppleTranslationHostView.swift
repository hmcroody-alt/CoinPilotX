import ExpoModulesCore
import SwiftUI
import UIKit

// The RN-mounted anchor for the SwiftUI translation hosts.
//
// `.translationTask` only fires for a view that is genuinely in the hierarchy,
// and Apple's language-download sheet needs a presenting view controller. So
// this is a real (if 1×1 and transparent) `ExpoView` that React Native mounts
// once, near the app root, keyed on the signed-in user id.
//
// Mounting is React's job on purpose: the brief forbids a permanent global
// session, and keying the mount on the user id makes account switching a
// teardown rather than something the cache layer has to reason about.
public final class AppleTranslationHostView: ExpoView {
  private var hostingController: UIViewController?
  private var didReportMount = false
  private var attachedToParent = false

  public required init(appContext: AppContext? = nil) {
    super.init(appContext: appContext)
    isUserInteractionEnabled = false
    accessibilityElementsHidden = true
    clipsToBounds = true
    backgroundColor = .clear
    installHostingController()
  }

  private func installHostingController() {
    let controller: UIViewController
    if #available(iOS 18.0, *) {
      controller = UIHostingController(
        rootView: AppleTranslationHostRoot(coordinator: AppleTranslationCoordinator.shared)
      )
    } else {
      // iOS 15–17: no Translation framework. Mounting an inert view keeps the RN
      // tree identical across OS versions so no screen needs a version check.
      controller = UIHostingController(rootView: AppleTranslationUnsupportedHost())
    }
    controller.view.backgroundColor = .clear
    controller.view.isUserInteractionEnabled = false
    controller.view.translatesAutoresizingMaskIntoConstraints = false
    addSubview(controller.view)
    NSLayoutConstraint.activate([
      controller.view.leadingAnchor.constraint(equalTo: leadingAnchor),
      controller.view.topAnchor.constraint(equalTo: topAnchor),
      controller.view.widthAnchor.constraint(equalToConstant: 1),
      controller.view.heightAnchor.constraint(equalToConstant: 1)
    ])
    hostingController = controller
  }

  public override func didMoveToWindow() {
    super.didMoveToWindow()
    guard window != nil else { return }
    attachToParentViewControllerIfNeeded()
    guard !didReportMount else { return }
    didReportMount = true
    if #available(iOS 18.0, *) {
      AppleTranslationCoordinator.shared.hostDidMount()
    }
  }

  /// Parents the hosting controller to the nearest UIViewController so that
  /// Apple's download UI has a presenter. Without this the SwiftUI view still
  /// renders but a presentation from inside it has no host and is dropped.
  private func attachToParentViewControllerIfNeeded() {
    guard !attachedToParent, let controller = hostingController else { return }
    guard let parent = nearestViewController() else { return }
    parent.addChild(controller)
    controller.didMove(toParent: parent)
    attachedToParent = true
  }

  private func nearestViewController() -> UIViewController? {
    var responder: UIResponder? = next
    while let current = responder {
      if let viewController = current as? UIViewController {
        return viewController
      }
      responder = current.next
    }
    return nil
  }

  deinit {
    let shouldReport = didReportMount
    let controller = hostingController
    Task { @MainActor in
      controller?.willMove(toParent: nil)
      controller?.view.removeFromSuperview()
      controller?.removeFromParent()
      if shouldReport, #available(iOS 18.0, *) {
        AppleTranslationCoordinator.shared.hostDidUnmount()
      }
    }
  }
}
