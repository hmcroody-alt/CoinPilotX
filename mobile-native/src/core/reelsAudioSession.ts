import { Audio, InterruptionModeAndroid, InterruptionModeIOS } from "expo-av";

/** Configure the shared iOS/Android session for foreground Reels playback. */
export async function configureReelsAudioSession() {
  await Audio.setAudioModeAsync({
    allowsRecordingIOS: false,
    interruptionModeIOS: InterruptionModeIOS.DoNotMix,
    playsInSilentModeIOS: true,
    staysActiveInBackground: false,
    interruptionModeAndroid: InterruptionModeAndroid.DuckOthers,
    playThroughEarpieceAndroid: false,
    shouldDuckAndroid: true
  });
}

/**
 * Configure the shared session for camera capture while monitoring a music bed.
 *
 * This lives here rather than next to the video-mixer helpers on purpose. expo-av's
 * setAudioModeAsync mutates the same AVAudioSession the real-time coordinator owns, so the
 * `expo_av_global_audio_mode` rule in config/realtime-audio-protected-paths.json freezes the
 * set of files allowed to call it at six. Capture monitoring routes through this module so the
 * mutation stays where review attention is concentrated instead of widening that set.
 */
export async function configureVideoCaptureMonitoringSession() {
  await Audio.setAudioModeAsync({
    allowsRecordingIOS: true,
    playsInSilentModeIOS: true,
    staysActiveInBackground: false,
    interruptionModeIOS: InterruptionModeIOS.MixWithOthers,
    shouldDuckAndroid: false,
    playThroughEarpieceAndroid: false
  });
}
