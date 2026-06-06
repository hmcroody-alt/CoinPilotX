# Voice Note Mobile Safari QA

## Supported Mobile Voice Formats

Mobile Safari can produce or upload voice-like audio as:

- `.m4a`
- `audio/mp4`
- `audio/x-m4a`
- `audio/m4a`
- `audio/aac`
- `audio/mp4a-latm`

Android Chrome and desktop Chrome commonly produce:

- `.webm`
- `audio/webm`

## Fix Status

The Communications V2 pipeline now supports these formats for voice notes and stores them as audio attachments.

## Manual QA Checklist

- Start recording on iPhone Safari.
- Pause and resume.
- Stop recording.
- Preview recording.
- Send voice note.
- Confirm message appears.
- Play voice note.
- Seek waveform/progress.
- Test speed controls.
- Repeat in PWA mode.
- Repeat in Android Chrome.

## Expected Error Messages

- Unsupported format: `That recording format is not supported. Try recording again.`
- Too long: `Voice notes can be up to N minutes.`
- Too large: `Voice note is too large. Record a shorter note and try again.`
- Upload validation failure: `Unsupported audio format. Please record again or choose a supported audio file.`
