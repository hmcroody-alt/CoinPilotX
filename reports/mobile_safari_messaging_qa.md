# Mobile Safari Messaging QA

## Scope

Pulse Messages V2 mobile send path for:

- image attachments
- video attachments
- voice notes
- generic files

## Root Cause Verified

Mobile voice recordings can use MIME/container combinations that desktop code did not fully handle. The important case was `audio/webm` being treated like generic `.webm` video in some backend paths or being filename-mapped incorrectly on the frontend.

## Fixes

- Frontend voice filename extension now matches the actual recorded MIME type.
- Backend upload staging now prioritizes audio MIME types before video extensions.
- Durable media service now stores audio MIME uploads as audio even if the container extension is shared with video.
- Safari-compatible M4A/AAC MIME variants are accepted.

## QA Status

Automated local QA passed:

- Ogg voice upload/send
- Audio/WebM voice upload as audio
- Audio/MP4 M4A voice upload
- Communications V2 media message send

Manual production QA still recommended on:

- iPhone Safari
- iPhone PWA mode
- Android Chrome

Expected result:

- image sends and displays
- voice sends and plays
- video sends and plays or shows processing state
- failed attachments show an attachment-specific error, not a generic Messenger outage
