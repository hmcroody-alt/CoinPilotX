# PulseSoc App Store Review Repair

Date: June 13, 2026

## Apple findings addressed

1. Design 4.0: the login/signup welcome screen was clipped on iPad Air 11-inch.
   - Added a tablet-specific one-column layout for 761px-1180px widths.
   - Constrained the hero, logo, feature rail, and form to the available viewport.
   - Added horizontal overflow protection and safe-area spacing.

2. Privacy 5.1.1(v): account creation existed without in-app account deletion.
   - Added Account Settings -> Delete Account.
   - Added password confirmation and explicit permanent-deletion consent.
   - Deletion anonymizes personal profile data, revokes notification/reset/link records, removes public content where supported, and signs the device out.
   - Added `POST /api/account/delete` for native clients.

3. Payments 3.1.1: Stripe subscription purchase and billing links were available in the iOS app.
   - The WebView now appends `PulseSocNativeApp` to its user agent without replacing the normal iOS WebKit identity.
   - iOS-native requests cannot create Stripe checkout or billing portal sessions.
   - The iOS Premium page is informational and contains no external purchase path.
   - Website Stripe checkout remains unchanged.

4. Performance 2.1(a): controls were reported unresponsive on iPad.
   - The tablet layout no longer places oversized hero content over the account form.
   - Native WebView requests now have an explicit platform identity so server-side iOS behavior is consistent.

## App Review evidence required

Record a new iPad/iPhone screen recording showing:

1. Sign in with the App Review account.
2. Open Account Settings.
3. Tap Delete my account.
4. Enter the password, confirm permanent deletion, and complete deletion.
5. Show the Account Deleted confirmation.

Add this recording and the navigation path to App Review Notes before resubmission.

## App Store screenshots

The six user-selected screenshots were converted into App Store Connect's accepted 6.5-inch iPhone dimensions:

- `reports/app-store-screenshots/iphone-65/01-menu-utility.png` (`1242x2688`)
- `reports/app-store-screenshots/iphone-65/02-menu-primary.png` (`1242x2688`)
- `reports/app-store-screenshots/iphone-65/03-videos-menu.png` (`1242x2688`)
- `reports/app-store-screenshots/iphone-65/04-home-feed.png` (`1242x2688`)
- `reports/app-store-screenshots/iphone-65/05-login.png` (`1242x2688`)
- `reports/app-store-screenshots/iphone-65/06-welcome.png` (`1242x2688`)

## Validation

- `python -m py_compile bot.py`
- `python scripts/app_store_review_repair_audit.py`
- `npm run typecheck` in `mobile/pulse-react-native`
- iPad browser QA at 1180x820 and 820x1180 still needs to be repeated against a running local or deployed build after the next iOS build is produced.
- `git diff --check`

The replacement iOS build must be generated after these changes and selected in App Store Connect before resubmission.
