/**
 * Compatibility adapter for the original Live V2 module boundary.
 * Publication ownership now lives in the shared realtime audio foundation so
 * calls and Live cannot drift into different microphone behavior again.
 */
export {
  REALTIME_AUDIO_PUBLISH_TIMEOUT_MS as LIVE_AUDIO_PUBLISH_TIMEOUT_MS,
  publishRealtimeMicrophone as publishLiveMicrophone,
  publishedRealtimeAudioTrackCount as publishedLiveAudioTrackCount
} from "../core/realtimeMicrophonePublisher";
export type {
  RealtimePublishOutcome as PublishOutcome,
  RealtimePublishResult as PublishResult
} from "../core/realtimeMicrophonePublisher";
