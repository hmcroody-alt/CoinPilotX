/**
 * A removed song must read as removed, not as absent.
 *
 * The server blanks a taken-down track's title and artist — it will not serve a
 * removed song's metadata — so the label these functions build collapses to the
 * empty string and the music line disappears from the status entirely. The
 * attachment is still there and the status is unchanged; what the viewer sees is
 * a status that used to have a song, now silent, with nothing saying why. That
 * reads as a bug in the app rather than as a decision, which is the one thing
 * the takedown surface is supposed not to do.
 *
 * The distinction being pinned is between a status with a removed track and a
 * status that never had music. Both arrive with no title. Only one of them
 * should say anything.
 */
import { statusMusicLabel } from "../status";

function status(music: Record<string, unknown> | null) {
  return { id: "1", music } as never;
}

it("says the audio is unavailable when the server marks it removed", () => {
  expect(statusMusicLabel(status({ audio_unavailable: true, title: "", artist: "" }))).toBe("Audio unavailable");
});

it("says nothing about audio for a status that never had music", () => {
  expect(statusMusicLabel(status(null))).toBe("");
  expect(statusMusicLabel(status({}))).toBe("");
});

it("still names the track when it has not been removed", () => {
  expect(statusMusicLabel(status({ title: "Pool Song A", artist: "Night Signal" }))).toBe("Pool Song A · Night Signal");
  expect(statusMusicLabel(status({ audio_title: "Pool Song A" }))).toBe("Pool Song A");
});

it("does not let a stale title survive the removal flag", () => {
  // Defence in depth against the server's blanking, not a second opinion about
  // it: if a title ever does arrive alongside the flag, the flag is the newer
  // fact and naming the song would be advertising something that is gone.
  expect(
    statusMusicLabel(status({ audio_unavailable: true, title: "Pool Song A", artist: "Night Signal" }))
  ).toBe("Audio unavailable");
});
