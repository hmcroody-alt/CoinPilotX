# PulseSoc Data Safety Draft

This draft is for App Store privacy labels and Google Play Data Safety. Final answers must be confirmed against production behavior before submission.

## Data Types Used

- Account identifiers: email, username, display name, user ID.
- Optional profile data: avatar, cover image, bio, links, expertise tags.
- User-generated content: posts, comments, reels, videos, statuses, messages, media uploads.
- Contact data: optional phone number for SMS verification/notifications.
- Payment status: premium and Founder membership state, Stripe customer/subscription references stored server-side.
- Notifications: push token, notification preferences, delivery status.
- Security data: login timestamps, device/session activity, password reset and account security logs.
- Diagnostics: request trace IDs, delivery logs, performance and reliability events.

## Purposes

- App functionality.
- Account management.
- User communication.
- User-generated content publishing.
- Notifications.
- Fraud prevention, security, and abuse prevention.
- Payments and subscription state.
- Analytics and product reliability.

## Sharing

Service providers may process data for hosting, email/SMS delivery, payments, media delivery, analytics, and notifications. PulseSoc should not sell personal data.

## User Controls

- Privacy policy: `https://pulsesoc.com/privacy`
- Support: `support@pulsesoc.com`
- Security: `security@pulsesoc.com`
- Privacy center: `https://pulsesoc.com/privacy-center`

## Submission Blockers

- Confirm account deletion request path and reviewer-facing instructions.
- Confirm Apple/Google payment compliance for Premium/Founder benefits.
- Confirm final push notification provider and tokens.
- Confirm real-device media permission behavior.
