Pulse Communications V2 Media Upload Limits

Current limits:
- Images: 25 MB default.
- Videos: 250 MB default.
- Audio: 25 MB default.
- Voice notes: 15 MB default and 300 seconds default.
- Files: 50 MB default.
- Attachments per message: 8 default.

Environment overrides:
- COMM_V2_IMAGE_MAX_MB
- COMM_V2_VIDEO_MAX_MB
- COMM_V2_AUDIO_MAX_MB
- COMM_V2_VOICE_MAX_MB
- COMM_V2_VOICE_MAX_SECONDS
- COMM_V2_FILE_MAX_MB
- COMM_V2_MAX_ATTACHMENTS

True resumable uploads remain a future backend upgrade. This phase adds safe retry, clear validation, and no duplicate messages on retry.
