import XCTest

final class PulseSocNativeCameraStudioQATests: XCTestCase {
  private let app = XCUIApplication(bundleIdentifier: "com.pulsesoc.nativeapp.dev")
  private let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")

  override func setUpWithError() throws {
    continueAfterFailure = false
    app.launchEnvironment["PULSESOC_NATIVE_QA_XCTEST"] = "1"
    app.launchEnvironment["PULSESOC_NATIVE_QA_BUNDLE_ID"] = "com.pulsesoc.nativeapp.dev"
    app.launch()
    capture("01-app-launch")
  }

  func testCameraStudioRouteAndControls() throws {
    XCTAssertTrue(app.exists, "QA XCTest must launch the parallel native QA bundle.")

    authenticateIfNeeded()
    openCameraStudioIfPossible()
    capture("02-camera-studio-open")

    if !waitForAnyVisible(["PulseSoc Camera", "Camera permission needed", "Camera preview requires a device build"], timeout: 20) {
      capture("02-camera-studio-route-blocked")
      throw XCTSkip("Camera Studio route was not reached. Provide a restored QA session, PULSESOC_QA_IDENTIFIER/PULSESOC_QA_PASSWORD, or PULSESOC_QA_CAMERA_DEEPLINK against the dev/local QA auth path.")
    }

    tapIfVisible("Feed")
    capture("03-feed-photo-mode")
    tapIfVisible("Status")
    capture("04-status-mode")
    tapIfVisible("Reel")
    capture("05-reel-video-mode")

    tapIfVisible("Photo")
    capture("06-photo-mode")
    tapIfVisible("Video")
    capture("07-video-mode")

    if tapIfVisible("Allow Camera", timeout: 3) {
      handleSystemPermissionPromptIfVisible()
      capture("08-camera-permission-state")
    } else {
      capture("08-camera-permission-state")
    }

    _ = tapIfVisible("Mic", timeout: 3) || tapIfVisible("Muted", timeout: 3)
    handleSystemPermissionPromptIfVisible()
    capture("09-microphone-permission-state")

    _ = tapIfVisible("Gallery", timeout: 3)
    handlePhotoPickerIfVisible()
    capture("10-gallery-picker-state")

    _ = tapIfVisible("Snap", timeout: 3)
    capture("11-photo-capture-attempt")

    _ = tapIfVisible("Record", timeout: 3)
    capture("12-video-capture-attempt")
    _ = tapIfVisible("Stop", timeout: 3)

    _ = tapIfVisible("Flip", timeout: 3)
    capture("13-front-back-switch")

    XCTAssertTrue(waitForAnyVisible(["Publish", "Retake", "Compression policy", "Server validation"], timeout: 10))
    capture("14-preview-publish-controls")
  }

  func testAuthenticationEntryAndRecovery() throws {
    let identifier = app.textFields["Email or username"]
    let password = app.secureTextFields["Password"]
    let signIn = app.buttons["Sign in"]

    XCTAssertTrue(identifier.waitForExistence(timeout: 15), "Existing-account identifier field must render on a clean launch.")
    XCTAssertTrue(password.exists, "Existing-account password field must render on a clean launch.")
    XCTAssertTrue(signIn.exists && signIn.isHittable, "Sign in must be visible and interactive.")
    XCTAssertTrue(app.buttons["Forgot password or need email verification?"].exists)
    XCTAssertTrue(app.buttons["New to PulseSoc? Create an account"].exists)
    capture("auth-clean-login")

    app.buttons["Forgot password or need email verification?"].tap()
    XCTAssertTrue(app.staticTexts["Recover access"].waitForExistence(timeout: 10))
    XCTAssertTrue(app.textFields["Existing account email"].exists)
    XCTAssertTrue(app.buttons["Reset password"].exists)
    XCTAssertTrue(app.buttons["Resend email verification"].exists)
    capture("auth-recovery")

    app.buttons["Back to sign in"].tap()
    XCTAssertTrue(app.textFields["Email or username"].waitForExistence(timeout: 10))
    capture("auth-return-to-login")
  }

  func testMessengerNewChatCreatesCanonicalConversationAndSendsFirstMessage() throws {
    authenticateIfNeeded()
    XCTAssertTrue(app.buttons["Open Messages"].waitForExistence(timeout: 20), "Authenticated native navigation must expose Messenger.")
    app.buttons["Open Messages"].tap()
    XCTAssertTrue(app.staticTexts["Messenger V3"].waitForExistence(timeout: 20), "Messenger inbox must open.")
    capture("new-chat-01-inbox")

    XCTAssertTrue(tapIfVisible("New chat", timeout: 8), "Top New Chat action must be interactive.")
    XCTAssertTrue(app.staticTexts["Start a conversation"].waitForExistence(timeout: 10), "Dedicated New Chat screen must open.")
    capture("new-chat-02-empty")

    let search = app.textFields["Search PulseSoc people"]
    XCTAssertTrue(search.waitForExistence(timeout: 8), "New Chat must expose native people search.")
    search.tap()
    search.typeText("native_new_chat_peer")
    let recipient = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Message Native New Chat Peer")).firstMatch
    XCTAssertTrue(recipient.waitForExistence(timeout: 15), "Controlled backend user must appear in native search.")
    capture("new-chat-03-results")
    recipient.tap()

    let composer = app.textViews.matching(NSPredicate(format: "label BEGINSWITH %@", "Message composer")).firstMatch
    XCTAssertTrue(composer.waitForExistence(timeout: 15), "Recipient selection must open the canonical conversation.")
    capture("new-chat-04-conversation")
    composer.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.02)).tap()
    composer.typeText("Hello from native New Chat QA")
    XCTAssertTrue(app.buttons["Send message"].isHittable, "First-message send must become available.")
    app.buttons["Send message"].tap()
    let sentMessage = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "Hello from native New Chat QA")).firstMatch
    XCTAssertTrue(sentMessage.waitForExistence(timeout: 15), "First canonical message must reconcile into the thread.")
    capture("new-chat-05-first-message")
    app.swipeDown()
    XCTAssertTrue(sentMessage.waitForExistence(timeout: 5), "Sent message must remain visible after dismissing the keyboard.")
    capture("new-chat-06-first-message-keyboard-dismissed")
  }

  private func authenticateIfNeeded() {
    if app.staticTexts["PulseSoc Camera"].waitForExistence(timeout: 3) {
      return
    }

    if let identifier = ProcessInfo.processInfo.environment["PULSESOC_QA_IDENTIFIER"],
       let password = ProcessInfo.processInfo.environment["PULSESOC_QA_PASSWORD"],
       !identifier.isEmpty,
       !password.isEmpty {
      if app.textFields["Email or username"].waitForExistence(timeout: 10) {
        app.textFields["Email or username"].tap()
        app.textFields["Email or username"].typeText(identifier)
        app.secureTextFields["Password"].tap()
        app.secureTextFields["Password"].typeText(password)
        tapIfVisible("Log in", timeout: 5)
        capture("auth-login-submit")
      }
    }
  }

  private func openCameraStudioIfPossible() {
    if app.staticTexts["PulseSoc Camera"].waitForExistence(timeout: 4) {
      return
    }

    if let deepLink = ProcessInfo.processInfo.environment["PULSESOC_QA_CAMERA_DEEPLINK"], !deepLink.isEmpty {
      openDeepLinkThroughSafari(deepLink)
      if app.staticTexts["PulseSoc Camera"].waitForExistence(timeout: 20) {
        return
      }
    }

    for label in ["Camera", "Open Camera", "Create", "Feed Composer", "Status Creator"] {
      if tapIfVisible(label, timeout: 2), app.staticTexts["PulseSoc Camera"].waitForExistence(timeout: 10) {
        return
      }
    }
  }

  private func openDeepLinkThroughSafari(_ url: String) {
    let safari = XCUIApplication(bundleIdentifier: "com.apple.mobilesafari")
    safari.launch()
    capture("deeplink-safari-launch")

    let addressCandidates = [
      safari.textFields["Address"],
      safari.textFields["Search or enter website name"],
      safari.otherElements["URL"]
    ]
    if let address = addressCandidates.first(where: { $0.waitForExistence(timeout: 5) }) {
      address.tap()
      address.typeText(url)
      address.typeText("\n")
    }

    let openButton = springboard.buttons["Open"]
    if openButton.waitForExistence(timeout: 5) {
      openButton.tap()
    }

    app.activate()
    capture("deeplink-return-to-app")
  }

  @discardableResult
  private func tapIfVisible(_ label: String, timeout: TimeInterval = 5) -> Bool {
    let candidates = [
      app.buttons[label],
      app.staticTexts[label],
      app.otherElements[label]
    ]
    guard let element = candidates.first(where: { $0.waitForExistence(timeout: timeout) && $0.isHittable }) else {
      return false
    }
    element.tap()
    return true
  }

  private func waitForAnyVisible(_ labels: [String], timeout: TimeInterval) -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
      if labels.contains(where: { label in
        app.staticTexts[label].exists || app.buttons[label].exists || app.otherElements[label].exists
      }) {
        return true
      }
      RunLoop.current.run(until: Date().addingTimeInterval(0.25))
    }
    return false
  }

  private func handleSystemPermissionPromptIfVisible() {
    for label in ["Allow", "Allow While Using App", "OK", "Continue"] {
      let button = springboard.buttons[label]
      if button.waitForExistence(timeout: 2) {
        button.tap()
        return
      }
    }
    for label in ["Don’t Allow", "Don't Allow"] {
      let button = springboard.buttons[label]
      if button.waitForExistence(timeout: 1) {
        capture("permission-deny-option-visible")
        return
      }
    }
  }

  private func handlePhotoPickerIfVisible() {
    let allowButton = springboard.buttons["Allow Full Access"]
    if allowButton.waitForExistence(timeout: 2) {
      allowButton.tap()
    }
    let firstCell = app.cells.element(boundBy: 0)
    if firstCell.waitForExistence(timeout: 4) && firstCell.isHittable {
      firstCell.tap()
      tapIfVisible("Add", timeout: 2)
      tapIfVisible("Choose", timeout: 2)
      tapIfVisible("Done", timeout: 2)
    }
  }

  private func capture(_ name: String) {
    let attachment = XCTAttachment(screenshot: app.screenshot())
    attachment.name = "PulseSocNative-CameraStudio-\(name)"
    attachment.lifetime = .keepAlways
    add(attachment)
  }
}
