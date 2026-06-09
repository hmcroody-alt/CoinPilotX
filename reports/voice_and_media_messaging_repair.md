# Voice And Media Messaging Repair

Date: 2026-06-09

- Communications V2 composer supports text, image, video, file, audio, and voice attachments.
- Voice recording UI supports start, pause/resume, stop, preview, delete, upload, send, and playback.
- Upload route validates staged attachments and links media rows to `comm_v2_attachments`.
- Mobile app declares microphone, photo/video media, camera, and notification permissions.
- Failed uploads keep retry/error state in the composer.

Second-pass validation:

- `pulse_comm_v2_attachment_send_audit.py`
- `pulse_voice_recording_audit.py`
- `pulse_voice_upload_audit.py`
- `pulse_voice_playback_audit.py`
- `pulse_voice_security_audit.py`
- `pulse_voice_mobile_audit.py`
- `pulse_attachment_security_audit.py`

Remaining QA: physical device recording and media picker confirmation on iOS and Android.
