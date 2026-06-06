# Pulse Email Notifications and Brevo

Status: foundation connected, external Brevo SMS setup still requires account-side configuration.

Implemented:
- Pulse preference model includes email by category.
- Centralized notification delivery can call `email_service.send_email`.
- PulseSoc sender alignment remains handled by the existing email service configuration.
- Security and premium categories default to email enabled in the Pulse preference model.

Brevo:
- Transactional email remains routed through the existing Brevo email service.
- SMS uses the existing SMS service foundation and requires Brevo SMS credentials/configuration in production.
- No secrets were read, printed, changed, committed, or rotated.

Templates queued for future provider-side refinement:
- New message digest
- Account security alert
- Password reset/security
- Premium alert
- Live started
- Roast battle invite

Anti-spam:
- Channel delivery respects user preferences.
- Full digest/rate-limit tuning should be layered onto the existing delivery logs once live traffic patterns are known.
