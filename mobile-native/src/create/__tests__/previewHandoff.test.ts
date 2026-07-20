import { clearPreviewHandoff, peekPreviewHandoff, PreviewPublishResult, stashPreviewHandoff } from "../previewHandoff";
import { ComposerDraftInput } from "../draftToContentModel";

const draft: ComposerDraftInput = {
  mode: "post",
  body: "hello",
  visibility: "public",
  topic: "",
  musicTrack: null,
  media: []
};

describe("previewHandoff", () => {
  it("stashes and retrieves a handoff with a live publish callback", async () => {
    const publish = jest.fn(async (): Promise<PreviewPublishResult> => ({ ok: true }));
    const token = stashPreviewHandoff({ draft, publish });
    const handoff = peekPreviewHandoff(token);
    expect(handoff).not.toBeNull();
    expect(handoff?.draft.body).toBe("hello");
    await handoff?.publish();
    expect(publish).toHaveBeenCalledTimes(1);
  });

  it("clears a handoff so it cannot be replayed", () => {
    const token = stashPreviewHandoff({ draft, publish: async () => ({ ok: true }) });
    clearPreviewHandoff(token);
    expect(peekPreviewHandoff(token)).toBeNull();
  });

  it("returns null for an unknown token", () => {
    expect(peekPreviewHandoff("does-not-exist")).toBeNull();
  });

  it("issues unique tokens per stash", () => {
    const a = stashPreviewHandoff({ draft, publish: async () => ({ ok: true }) });
    const b = stashPreviewHandoff({ draft, publish: async () => ({ ok: true }) });
    expect(a).not.toBe(b);
  });
});
