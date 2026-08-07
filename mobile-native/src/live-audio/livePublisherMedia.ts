/**
 * Canonical microphone-first publisher startup shared by video calls and Live.
 *
 * This module deliberately knows nothing about screens, rooms, flags, or
 * product roles. Callers supply their authorized publication operations while
 * this coordinator owns the proven ordering around the iOS camera/audio race.
 */
export async function initializeCallGradePublisherMedia(options: {
  video: boolean;
  publishMicrophone: () => Promise<number>;
  enableCamera: () => Promise<void>;
  reassertMicrophone: () => Promise<number>;
  stabilizeAfterCamera?: () => Promise<void>;
  onPhase?: (phase: "microphone_publishing" | "microphone_published" | "camera_publishing" | "camera_published" | "audio_stabilizing" | "audio_stabilized") => Promise<void> | void;
}): Promise<number> {
  await options.onPhase?.("microphone_publishing");
  let audioTrackCount = await options.publishMicrophone();
  await options.onPhase?.("microphone_published");
  if (audioTrackCount <= 0 || !options.video) return audioTrackCount;

  await options.onPhase?.("camera_publishing");
  await options.enableCamera();
  await options.onPhase?.("camera_published");

  audioTrackCount = await options.reassertMicrophone();
  if (audioTrackCount <= 0) audioTrackCount = await options.publishMicrophone();

  if (options.stabilizeAfterCamera) {
    await options.onPhase?.("audio_stabilizing");
    await options.stabilizeAfterCamera();
    await options.onPhase?.("audio_stabilized");
  }
  return audioTrackCount;
}
