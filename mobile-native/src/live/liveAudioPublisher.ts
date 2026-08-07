/**
 * The one and only Live microphone publisher.
 *
 * This used to forward to `core/realtimeMicrophonePublisher`, the module calls
 * use. It now forwards to `live-audio/liveMicrophonePublisher`, Live's own copy
 * of that implementation, so a change made for a broadcast cannot reach a call.
 *
 * It stays a re-export rather than becoming a second implementation. Two modules
 * that can each publish a microphone is exactly how a room ends up with two audio
 * tracks, which the server resolves by keeping one - heard as an echo or as
 * silence depending on which one it keeps. Every Live caller resolves to the same
 * function underneath, and that function serializes per room.
 */
export {
  LIVE_AUDIO_PUBLISH_TIMEOUT_MS,
  publishLiveMicrophone,
  publishedLiveAudioTrackCount
} from "../live-audio/liveMicrophonePublisher";
export type {
  LivePublishOutcome as PublishOutcome,
  LivePublishResult as PublishResult
} from "../live-audio/liveMicrophonePublisher";
