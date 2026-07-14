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
