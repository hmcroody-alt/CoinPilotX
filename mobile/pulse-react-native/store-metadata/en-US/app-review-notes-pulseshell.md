# PulseShell App Review Notes Template

PulseSoc is a social media and live-streaming platform.

This update adds PulseShell, a native bridge layer that improves camera, microphone, push notifications, sharing, file selection, haptics, deep links, offline handling, and app performance behavior inside the PulseSoc mobile app.

Camera and microphone are used only when users create media, start a Live, or request to join a Live as a co-host.

Live co-hosting can be tested with two non-admin accounts:

1. Account A starts a Live.
2. Account B opens the Live and taps Request to Co-host.
3. Account A accepts the request from Backstage.
4. Account B joins with camera and microphone after approval.

User-generated content safety:

- Users can report content and profiles.
- Users can block other users.
- Live hosts can deny co-host requests, remove guests, and control chat.
- PulseSoc Terms include a no-tolerance policy for objectionable content and abusive users.

Account deletion:

- Users can initiate account deletion in PulseSoc Settings > Account > Delete my account.
- Direct authenticated path: `/account/delete`.

Privacy and legal:

- Privacy Policy: `https://pulsesoc.com/privacy`
- Terms: `https://pulsesoc.com/terms`
- Support: `https://pulsesoc.com/support`

Backend services must be available during review.

Reviewer credentials:

- Add real non-admin reviewer credentials in App Store Connect only.
- Do not add admin credentials, owner credentials, billing credentials, or secrets.
