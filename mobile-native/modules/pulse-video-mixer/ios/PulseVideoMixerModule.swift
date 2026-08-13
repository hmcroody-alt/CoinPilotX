import AVFoundation
import ExpoModulesCore

public final class PulseVideoMixerModule: Module {
  public func definition() -> ModuleDefinition {
    Name("PulseVideoMixer")

    AsyncFunction("mixVideoWithMusic") { (options: [String: Any], promise: Promise) in
      Task {
        do {
          let result = try await self.mix(options)
          promise.resolve(result)
        } catch {
          promise.reject("VIDEO_MIX_FAILED", error.localizedDescription)
        }
      }
    }
  }

  private func mix(_ options: [String: Any]) async throws -> [String: Any] {
    guard let videoUrl = url(options["videoUri"] as? String),
          let musicUrl = url(options["musicUri"] as? String) else {
      throw MixError.invalidSource
    }

    let videoAsset = AVURLAsset(url: videoUrl)
    let localMusicUrl = try await localAudioUrl(from: musicUrl)
    let musicAsset = AVURLAsset(url: localMusicUrl)
    let duration = try await videoAsset.load(.duration)
    guard duration.isValid && duration.seconds > 0 else { throw MixError.emptyVideo }

    let composition = AVMutableComposition()
    guard let sourceVideo = try await videoAsset.loadTracks(withMediaType: .video).first,
          let videoTrack = composition.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid) else {
      throw MixError.missingVideo
    }
    try videoTrack.insertTimeRange(CMTimeRange(start: .zero, duration: duration), of: sourceVideo, at: .zero)
    videoTrack.preferredTransform = try await sourceVideo.load(.preferredTransform)

    let audioMix = AVMutableAudioMix()
    var parameters: [AVMutableAudioMixInputParameters] = []
    var hasMicAudio = false
    if let sourceMic = try await videoAsset.loadTracks(withMediaType: .audio).first,
       let micTrack = composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) {
      try micTrack.insertTimeRange(CMTimeRange(start: .zero, duration: duration), of: sourceMic, at: .zero)
      let mic = AVMutableAudioMixInputParameters(track: micTrack)
      mic.setVolume(safeGain(options["micVolume"] as? Double, ceiling: 0.82), at: .zero)
      parameters.append(mic)
      hasMicAudio = true
    }

    var hasMusicAudio = false
    if let sourceMusic = try await musicAsset.loadTracks(withMediaType: .audio).first,
       let musicTrack = composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) {
      let musicDuration = try await musicAsset.load(.duration)
      let requestedOffset = max(0, options["musicStartSeconds"] as? Double ?? 0)
      let offset = CMTime(seconds: min(requestedOffset, max(0, musicDuration.seconds - 0.05)), preferredTimescale: 600)
      let available = CMTimeSubtract(musicDuration, offset)
      let insertDuration = CMTimeMinimum(duration, available)
      if insertDuration.seconds > 0 {
        try musicTrack.insertTimeRange(CMTimeRange(start: offset, duration: insertDuration), of: sourceMusic, at: .zero)
        let music = AVMutableAudioMixInputParameters(track: musicTrack)
        // User-facing 100% maps below unity. Combined with the microphone's
        // ceiling this reserves mix headroom instead of raw-summing two tracks.
        music.setVolume(safeGain(options["musicVolume"] as? Double, ceiling: 0.64), at: .zero)
        parameters.append(music)
        hasMusicAudio = true
      }
    }
    guard hasMusicAudio else { throw MixError.missingMusic }
    audioMix.inputParameters = parameters

    let outputUrl = FileManager.default.temporaryDirectory
      .appendingPathComponent("pulsesoc-video-mix-\(UUID().uuidString).mp4")
    guard let exporter = AVAssetExportSession(asset: composition, presetName: AVAssetExportPresetHighestQuality) else {
      throw MixError.exportUnavailable
    }
    exporter.outputURL = outputUrl
    exporter.outputFileType = .mp4
    exporter.shouldOptimizeForNetworkUse = true
    exporter.audioMix = audioMix
    try await export(exporter)
    guard exporter.status == .completed else {
      throw exporter.error ?? MixError.exportFailed
    }
    return [
      "uri": outputUrl.absoluteString,
      "durationSeconds": duration.seconds,
      "hasMicAudio": hasMicAudio,
      "hasMusicAudio": hasMusicAudio
    ]
  }

  private func safeGain(_ value: Double?, ceiling: Float) -> Float {
    return Float(max(0, min(1, value ?? 1))) * ceiling
  }

  private func export(_ session: AVAssetExportSession) async throws {
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
      session.exportAsynchronously {
        if session.status == .completed { continuation.resume() }
        else { continuation.resume(throwing: session.error ?? MixError.exportFailed) }
      }
    }
  }

  private func localAudioUrl(from source: URL) async throws -> URL {
    guard source.isFileURL == false else { return source }
    let (temporary, response) = try await URLSession.shared.download(from: source)
    if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) { throw MixError.musicDownloadFailed }
    let ext = source.pathExtension.isEmpty ? "m4a" : source.pathExtension
    let destination = FileManager.default.temporaryDirectory.appendingPathComponent("pulsesoc-video-music-\(UUID().uuidString).\(ext)")
    try FileManager.default.moveItem(at: temporary, to: destination)
    return destination
  }

  private func url(_ value: String?) -> URL? {
    guard let value, !value.isEmpty else { return nil }
    if let url = URL(string: value), url.scheme != nil { return url }
    return URL(fileURLWithPath: value)
  }
}

private enum MixError: LocalizedError {
  case invalidSource, emptyVideo, missingVideo, missingMusic, musicDownloadFailed, exportUnavailable, exportFailed

  var errorDescription: String? {
    switch self {
    case .invalidSource: return "Video or music source is invalid."
    case .emptyVideo: return "The recorded video is empty."
    case .missingVideo: return "The recording does not contain video."
    case .missingMusic: return "The selected music could not be decoded."
    case .musicDownloadFailed: return "The selected music could not be downloaded for the final mix."
    case .exportUnavailable: return "The device cannot create the final video mix."
    case .exportFailed: return "The final video mix could not be exported."
    }
  }
}
