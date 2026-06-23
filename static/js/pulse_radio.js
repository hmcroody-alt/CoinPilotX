(function () {
  "use strict";

  const launchButtons = [...document.querySelectorAll("[data-pulse-radio-toggle]")];
  const player = document.querySelector("[data-pulse-radio-player]");
  const audio = player?.querySelector("[data-pulse-radio-audio]");
  if (!launchButtons.length || !player || !audio) return;

  const state = {
    loading: false,
    playing: false,
    index: -1,
    tracks: [],
    playHistory: [],
    failedTracks: new Set(),
    lastEventKey: "",
  };

  function toast(message) {
    const node = document.getElementById("toast") || document.querySelector("[data-toast]");
    if (!node) return;
    node.textContent = String(message || "Pulse Radio updated.");
    node.classList.add("show");
    clearTimeout(node._pulseRadioTimer);
    node._pulseRadioTimer = setTimeout(() => node.classList.remove("show"), 3200);
  }

  async function api(url, options = {}) {
    const controller = typeof AbortController === "undefined" ? null : new AbortController();
    const timer = controller ? setTimeout(() => controller.abort(), Number(options.timeoutMs || 10000)) : null;
    const isForm = options.body instanceof FormData;
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        headers: isForm ? { ...(options.headers || {}) } : { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
        signal: options.signal || controller?.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(data.message || data.error || "Pulse Radio request failed.");
      return data;
    } catch (error) {
      if (error?.name === "AbortError") throw new Error("Pulse Radio is taking too long to respond.");
      throw error;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  function trackUrl(track) {
    return String(track?.audio_url || track?.preview_url || "").trim();
  }

  function normalizeTrack(track) {
    return {
      id: track.id || track.track_id || "",
      title: String(track.title || "PulseSoc Radio").trim().slice(0, 140),
      artist: String(track.artist || track.artist_name || "PulseSoc Music").trim().slice(0, 140),
      audio_url: trackUrl(track),
      cover_art_url: String(track.cover_art_url || track.thumbnail_url || "").trim(),
      duration_seconds: Number(track.duration_seconds || track.duration || 0) || 0,
    };
  }

  function shuffleTracks(tracks) {
    const copy = [...(tracks || [])];
    const secureCrypto = window.crypto || window.msCrypto;
    if (secureCrypto?.getRandomValues) {
      for (let index = copy.length - 1; index > 0; index -= 1) {
        const random = new Uint32Array(1);
        secureCrypto.getRandomValues(random);
        const swap = random[0] % (index + 1);
        [copy[index], copy[swap]] = [copy[swap], copy[index]];
      }
      return copy;
    }
    for (let index = copy.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1));
      [copy[index], copy[swap]] = [copy[swap], copy[index]];
    }
    return copy;
  }

  async function loadTracks() {
    if (state.tracks.length || state.loading) return state.tracks;
    state.loading = true;
    player.removeAttribute("hidden");
    setPlayerText("Pulse Radio", "Loading approved PulseSoc sounds...");
    try {
      const data = await api("/api/pulse/music/radio?limit=80", { timeoutMs: 9000 });
      state.tracks = shuffleTracks((data.items || [])
        .map(normalizeTrack)
        .filter(track => track.id && track.audio_url)
        .slice(0, 80));
      if (!state.tracks.length) throw new Error("No playable approved PulseSoc tracks are available yet.");
      return state.tracks;
    } finally {
      state.loading = false;
    }
  }

  function setPlayerText(title, artist) {
    const titleNode = player.querySelector("[data-pulse-radio-title]");
    const artistNode = player.querySelector("[data-pulse-radio-artist]");
    if (titleNode) titleNode.textContent = title || "Pulse Radio";
    if (artistNode) artistNode.textContent = artist || "PulseSoc Music pool";
  }

  function currentTrack() {
    return state.tracks[state.index] || null;
  }

  function syncUi() {
    const track = currentTrack();
    player.classList.toggle("is-playing", state.playing);
    player.toggleAttribute("hidden", !track && !state.loading);
    launchButtons.forEach(button => {
      button.classList.toggle("is-playing", state.playing);
      button.setAttribute("aria-pressed", state.playing ? "true" : "false");
      button.setAttribute("aria-label", state.playing ? "Pause Pulse Radio" : "Play Pulse Radio from approved PulseSoc Music");
    });
    const icon = player.querySelector("[data-pulse-radio-play-icon]");
    if (icon) icon.textContent = state.playing ? "Pause" : "Play";
    if (track) setPlayerText(track.title, `${track.artist} · Pulse Radio`);
  }

  function updateMediaSession(track) {
    if (!("mediaSession" in navigator) || !track) return;
    try {
      const artwork = track.cover_art_url ? [{ src: track.cover_art_url, sizes: "512x512", type: "image/png" }] : [];
      navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title,
        artist: track.artist,
        album: "Pulse Radio",
        artwork,
      });
      navigator.mediaSession.setActionHandler("play", () => playCurrent().catch(() => {}));
      navigator.mediaSession.setActionHandler("pause", pauseRadio);
      navigator.mediaSession.setActionHandler("nexttrack", () => playNext().catch(() => {}));
      navigator.mediaSession.setActionHandler("previoustrack", () => playPrevious().catch(() => {}));
    } catch (_) {}
  }

  function logTrackEvent(track, eventType = "play") {
    if (!track?.id) return;
    const eventKey = `${eventType}:${track.id}:${Math.floor(Date.now() / 15000)}`;
    if (state.lastEventKey === eventKey) return;
    state.lastEventKey = eventKey;
    api(`/api/pulse/music/${encodeURIComponent(track.id)}/event`, {
      method: "POST",
      body: JSON.stringify({ event_type: eventType, surface: "pulse_radio" }),
      timeoutMs: 5000,
    }).catch(() => {});
  }

  async function prepareTrack(index) {
    await loadTracks();
    if (!state.tracks.length) return null;
    if (state.index >= state.tracks.length - 1 && state.playHistory.length >= state.tracks.length - state.failedTracks.size) {
      const currentId = currentTrack()?.id || "";
      state.tracks = shuffleTracks(state.tracks);
      if (state.tracks.length > 1 && String(state.tracks[0]?.id || "") === String(currentId)) {
        state.tracks.push(state.tracks.shift());
      }
      state.playHistory = [];
      index = 0;
    }
    let nextIndex = ((index % state.tracks.length) + state.tracks.length) % state.tracks.length;
    for (let attempts = 0; attempts < state.tracks.length; attempts += 1) {
      const candidate = state.tracks[nextIndex];
      if (candidate && !state.failedTracks.has(String(candidate.id))) break;
      nextIndex = (nextIndex + 1 + Math.floor(Math.random() * Math.max(1, state.tracks.length - 1))) % state.tracks.length;
    }
    state.index = nextIndex;
    const track = currentTrack();
    if (!track || state.failedTracks.size >= state.tracks.length) {
      throw new Error("Pulse Radio could not find a playable track right now.");
    }
    audio.src = track.audio_url;
    audio.dataset.trackId = String(track.id);
    state.playHistory.push(String(track.id));
    if (state.playHistory.length > state.tracks.length) state.playHistory.shift();
    audio.load();
    updateMediaSession(track);
    syncUi();
    return track;
  }

  async function playCurrent() {
    const track = currentTrack() || await prepareTrack(state.index >= 0 ? state.index : 0);
    if (!track) return;
    try {
      await audio.play();
      state.playing = true;
      syncUi();
      logTrackEvent(track, "play");
    } catch (error) {
      state.playing = false;
      syncUi();
      toast(error?.name === "NotAllowedError" ? "Tap Pulse Radio again to start audio." : "Pulse Radio could not play this track.");
      throw error;
    }
  }

  async function playIndex(index) {
    await prepareTrack(index);
    await playCurrent();
  }

  function pauseRadio() {
    audio.pause();
    state.playing = false;
    syncUi();
  }

  async function playNext() {
    await playIndex((state.index < 0 ? 0 : state.index + 1));
  }

  async function playPrevious() {
    await playIndex((state.index < 0 ? 0 : state.index - 1));
  }

  async function toggleRadio() {
    if (state.playing) {
      pauseRadio();
      return;
    }
    if (!currentTrack()) await prepareTrack(0);
    await playCurrent();
  }

  launchButtons.forEach(button => button.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    toggleRadio().catch(error => toast(error?.message || "Pulse Radio could not start."));
  }));

  player.querySelector("[data-pulse-radio-play]")?.addEventListener("click", event => {
    event.preventDefault();
    toggleRadio().catch(error => toast(error?.message || "Pulse Radio could not start."));
  });

  player.querySelector("[data-pulse-radio-next]")?.addEventListener("click", event => {
    event.preventDefault();
    playNext().catch(error => toast(error?.message || "Pulse Radio could not skip."));
  });

  audio.addEventListener("play", () => {
    state.playing = true;
    syncUi();
  });

  audio.addEventListener("pause", () => {
    state.playing = false;
    syncUi();
  });

  audio.addEventListener("ended", () => {
    playNext().catch(() => {
      state.playing = false;
      syncUi();
    });
  });

  audio.addEventListener("error", () => {
    const track = currentTrack();
    if (track?.id) state.failedTracks.add(String(track.id));
    state.playing = false;
    syncUi();
    if (state.failedTracks.size < state.tracks.length) {
      playNext().catch(() => toast("Pulse Radio could not play the next approved track."));
    } else {
      toast("Pulse Radio has no playable approved tracks right now.");
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) syncUi();
  });

  syncUi();
})();
