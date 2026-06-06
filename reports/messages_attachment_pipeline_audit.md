# Messages Attachment Pipeline Audit

## Pipeline

1. User selects media in Pulse Messages V2.
2. Frontend posts `FormData` to `/api/pulse/communications/v2/attachments/upload`.
3. Backend validates file type, size, MIME, extension, and conversation access.
4. Upload is staged through the shared Pulse upload pipeline.
5. Media row is stored in `chat_media_uploads`.
6. User sends message with `media_ids`.
7. Backend validates attachment ownership and availability.
8. Message is inserted into `comm_v2_messages`.
9. Attachment rows are inserted into `comm_v2_attachments`.
10. Message payload returns with attachments.

## Findings

- Upload and send are correctly separate steps.
- Missing validation before send could allow attachment-linking failures to become generic Messenger errors.
- Voice notes needed MIME-aware handling because browser containers differ across iPhone Safari, Android Chrome, and desktop Chrome.

## Fixed

- Pre-send attachment validation added.
- Audio/WebM and iPhone M4A style voice uploads are now supported.
- Route-level trace logging was added for future production diagnosis.

## Supported Voice MIME/Extensions

- `audio/webm` / `.webm`
- `audio/ogg` / `.ogg`
- `audio/mp4` / `.m4a`
- `audio/x-m4a` / `.m4a`
- `audio/m4a` / `.m4a`
- `audio/aac` / `.aac`
- `audio/mp4a-latm` / `.aac`
- `audio/mpeg` / `.mp3`
- `audio/wav` / `.wav`

## Audit Results

- Communications V2 audit: PASS
- Attachment foundation audit: PASS
- Voice upload audit: PASS
- Voice security audit: PASS
