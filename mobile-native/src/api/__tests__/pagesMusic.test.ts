/**
 * The Music tab's wire shape, pinned — the same three-part answer as Events,
 * and unpinned until now for the same reason it took a while to matter: the
 * screen read only `tracks` and threw the rest away, so there was nothing to
 * get wrong.
 *
 * `/api/pages/:id/music` answers whether this presence has been pointed at a
 * catalogue, *which* catalogue, and the releases in it. The screen branches on
 * all three:
 *
 *   - `linked` separates "connect a catalogue" from "the connected catalogue is
 *     empty". Those are the same empty list and want opposite sentences; the
 *     team that gets the wrong one is exactly the team that already connected
 *     something and is now being told to go and connect it.
 *   - `artist` is the catalogue's name, and the screen shows it when it differs
 *     from the presence. The link stores a name and connecting one is an
 *     ordinary `manage_links` write, so a presence publishing somebody else's
 *     releases renders identically to one publishing its own. This string is
 *     the only thing that tells them apart.
 *
 * `linked` defaults to `false` for the Events reason: a server old enough not
 * to send it cannot be assumed to have a catalogue, and being wrong in that
 * direction only offers a step nobody needed.
 */

const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import { listPageMusic } from "../pages";

const TRACK = {
  id: "t1",
  title: "Signal",
  artist: "Night Signal",
  genre: "Synth",
  cover_art_url: "https://cdn/art.jpg"
};

beforeEach(() => {
  mockPulseApi.mockReset();
  mockPulseApi.mockResolvedValue({ ok: true, artist: "", tracks: [], linked: false });
});

describe("listPageMusic", () => {
  it("asks the presence's own music endpoint and carries the limit", async () => {
    await listPageMusic(41, 5);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pages/41/music?limit=5");
  });

  it("defaults the limit rather than asking for the whole catalogue", async () => {
    await listPageMusic(41);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pages/41/music?limit=24");
  });

  it("hands back the tracks the server sent, unreshaped", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, artist: "Night Signal", linked: true, tracks: [TRACK] });
    const result = await listPageMusic(41);

    expect(result.tracks).toEqual([TRACK]);
    expect(result.tracks[0].cover_art_url).toBe("https://cdn/art.jpg");
  });

  it("carries which catalogue the releases came from", async () => {
    // Not cosmetic: this is the string the screen compares against the page's
    // own name to decide whether a presence is publishing somebody else's work.
    mockPulseApi.mockResolvedValue({ ok: true, artist: "Other Artist", linked: true, tracks: [TRACK] });
    await expect(listPageMusic(41)).resolves.toMatchObject({ artist: "Other Artist" });
  });

  it("carries whether anything is connected at all", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, artist: "Night Signal", linked: true, tracks: [] });
    const result = await listPageMusic(41);

    // An empty catalogue that *is* connected. Dropping `linked` here collapses
    // this into the unconnected case and sends the team to redo a done step.
    expect(result.linked).toBe(true);
    expect(result.tracks).toEqual([]);
  });

  it("reads a missing linked flag as unconnected rather than as connected", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, tracks: [] });
    await expect(listPageMusic(41)).resolves.toEqual({ artist: "", linked: false, tracks: [] });
  });

  it("does not invent a catalogue name the server did not send", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, linked: true, tracks: [TRACK] });
    // Empty, not the page's name and not "Unknown": the screen only speaks when
    // it has been told whose catalogue this is.
    await expect(listPageMusic(41)).resolves.toMatchObject({ artist: "" });
  });

  it("does not invent a list when the server omits one", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, artist: "Night Signal", linked: true });
    await expect(listPageMusic(41)).resolves.toMatchObject({ tracks: [] });
  });

  it("lets a failure reach the caller instead of reporting an empty catalogue", async () => {
    // The screen has a distinct "we couldn't load this section" branch, and it
    // can only reach it if the rejection survives this layer. Swallowing it
    // here would report a working catalogue as having no music in it.
    const { PulseApiError } = jest.requireActual("../pulseApi");
    mockPulseApi.mockRejectedValue(new PulseApiError("We couldn't load this section.", 503));
    await expect(listPageMusic(41)).rejects.toThrow("We couldn't load this section.");
  });
});
