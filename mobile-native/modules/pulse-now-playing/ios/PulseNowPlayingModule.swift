import ExpoModulesCore
import MediaPlayer

// Exposes MPNowPlayingInfoCenter (lock-screen / Control Center metadata) and
// MPRemoteCommandCenter (lock-screen transport controls) to the PulseSoc
// Native JS queue engine (`src/core/pulseRadio.ts` via
// `src/native/nowPlayingBridge.ts`). This module owns no playback state of
// its own — it only mirrors state pushed from JS and forwards remote
// commands back to JS as events.
public class PulseNowPlayingModule: Module {
  private var artworkRequest: URLSessionDataTask?
  private var lastArtworkUrl: String?

  public func definition() -> ModuleDefinition {
    Name("PulseNowPlaying")

    Events("onRemoteCommand")

    OnCreate {
      self.configureRemoteCommands()
    }

    Function("setNowPlayingInfo") { (info: [String: Any]) in
      self.applyNowPlayingInfo(info)
    }

    Function("updatePlaybackProgress") { (positionSeconds: Double, isPlaying: Bool, rate: Double) in
      self.updateProgress(positionSeconds: positionSeconds, isPlaying: isPlaying, rate: rate)
    }

    Function("clearNowPlayingInfo") {
      self.lastArtworkUrl = nil
      self.artworkRequest?.cancel()
      MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }
  }

  private func configureRemoteCommands() {
    let center = MPRemoteCommandCenter.shared()

    center.playCommand.isEnabled = true
    center.playCommand.addTarget { [weak self] _ in
      self?.sendEvent("onRemoteCommand", ["command": "play"])
      return .success
    }

    center.pauseCommand.isEnabled = true
    center.pauseCommand.addTarget { [weak self] _ in
      self?.sendEvent("onRemoteCommand", ["command": "pause"])
      return .success
    }

    center.togglePlayPauseCommand.isEnabled = true
    center.togglePlayPauseCommand.addTarget { [weak self] _ in
      self?.sendEvent("onRemoteCommand", ["command": "toggle"])
      return .success
    }

    center.nextTrackCommand.isEnabled = true
    center.nextTrackCommand.addTarget { [weak self] _ in
      self?.sendEvent("onRemoteCommand", ["command": "next"])
      return .success
    }

    center.previousTrackCommand.isEnabled = true
    center.previousTrackCommand.addTarget { [weak self] _ in
      self?.sendEvent("onRemoteCommand", ["command": "previous"])
      return .success
    }

    center.changePlaybackPositionCommand.isEnabled = true
    center.changePlaybackPositionCommand.addTarget { [weak self] event in
      guard let positionEvent = event as? MPChangePlaybackPositionCommandEvent else {
        return .commandFailed
      }
      self?.sendEvent("onRemoteCommand", [
        "command": "seek",
        "positionSeconds": positionEvent.positionTime
      ])
      return .success
    }

    center.skipForwardCommand.isEnabled = true
    center.skipForwardCommand.preferredIntervals = [15]
    center.skipForwardCommand.addTarget { [weak self] event in
      let interval = (event as? MPSkipIntervalCommandEvent)?.interval ?? 15
      self?.sendEvent("onRemoteCommand", ["command": "skipForward", "intervalSeconds": interval])
      return .success
    }

    center.skipBackwardCommand.isEnabled = true
    center.skipBackwardCommand.preferredIntervals = [15]
    center.skipBackwardCommand.addTarget { [weak self] event in
      let interval = (event as? MPSkipIntervalCommandEvent)?.interval ?? 15
      self?.sendEvent("onRemoteCommand", ["command": "skipBackward", "intervalSeconds": interval])
      return .success
    }
  }

  private func applyNowPlayingInfo(_ info: [String: Any]) {
    var nowPlayingInfo: [String: Any] = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]

    if let title = info["title"] as? String {
      nowPlayingInfo[MPMediaItemPropertyTitle] = title
    }
    if let artist = info["artist"] as? String {
      nowPlayingInfo[MPMediaItemPropertyArtist] = artist
    }
    if let duration = info["durationSeconds"] as? Double, duration > 0 {
      nowPlayingInfo[MPMediaItemPropertyPlaybackDuration] = duration
    }
    if let position = info["positionSeconds"] as? Double {
      nowPlayingInfo[MPNowPlayingInfoPropertyElapsedPlaybackTime] = position
    }
    nowPlayingInfo[MPNowPlayingInfoPropertyMediaType] = MPNowPlayingInfoMediaType.audio.rawValue
    if let isPlaying = info["isPlaying"] as? Bool {
      nowPlayingInfo[MPNowPlayingInfoPropertyPlaybackRate] = isPlaying ? 1.0 : 0.0
    }

    MPNowPlayingInfoCenter.default().nowPlayingInfo = nowPlayingInfo

    let artworkUrlString = info["artworkUrl"] as? String
    if artworkUrlString != lastArtworkUrl {
      lastArtworkUrl = artworkUrlString
      artworkRequest?.cancel()
      artworkRequest = nil
      if let artworkUrlString, let url = URL(string: artworkUrlString) {
        loadArtwork(url: url)
      } else {
        var cleared = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]
        cleared[MPMediaItemPropertyArtwork] = nil
        MPNowPlayingInfoCenter.default().nowPlayingInfo = cleared
      }
    }
  }

  private func updateProgress(positionSeconds: Double, isPlaying: Bool, rate: Double) {
    guard var nowPlayingInfo = MPNowPlayingInfoCenter.default().nowPlayingInfo else { return }
    nowPlayingInfo[MPNowPlayingInfoPropertyElapsedPlaybackTime] = positionSeconds
    nowPlayingInfo[MPNowPlayingInfoPropertyPlaybackRate] = isPlaying ? rate : 0.0
    MPNowPlayingInfoCenter.default().nowPlayingInfo = nowPlayingInfo
  }

  private func loadArtwork(url: URL) {
    let requestedUrlString = url.absoluteString
    let task = URLSession.shared.dataTask(with: url) { [weak self] data, _, error in
      guard let self, error == nil, let data, let image = UIImage(data: data) else { return }
      DispatchQueue.main.async {
        // Only apply if this is still the most recently requested artwork.
        guard self.lastArtworkUrl == requestedUrlString else { return }
        var updated = MPNowPlayingInfoCenter.default().nowPlayingInfo ?? [:]
        updated[MPMediaItemPropertyArtwork] = MPMediaItemArtwork(boundsSize: image.size) { _ in image }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = updated
      }
    }
    artworkRequest = task
    task.resume()
  }
}
