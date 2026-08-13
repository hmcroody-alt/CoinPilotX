export type PulseVideoMixOptions = {
  videoUri: string;
  musicUri: string;
  musicStartSeconds: number;
  musicVolume: number;
  micVolume: number;
};

export type PulseVideoMixResult = {
  uri: string;
  durationSeconds: number;
  hasMicAudio: boolean;
  hasMusicAudio: boolean;
};

export declare const isPulseVideoMixerSupported: boolean;
export declare function mixVideoWithMusic(options: PulseVideoMixOptions): Promise<PulseVideoMixResult>;
