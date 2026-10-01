/**
 * Stage 3/6/7/8 regression tests for Messenger media access URLs.
 *
 * The incident these guard against: the message row carried a protected API
 * path (`/api/messages/media/<id>/download`) and that path was handed straight
 * to the platform image loader. The loader cannot send an Authorization header,
 * so the server fell through to persistent-cookie restoration and rotated the
 * mobile refresh token on every thumbnail. Four thumbnails opening at once read
 * as refresh-token reuse; the loader's own User-Agent read as a device
 * mismatch. Both revoke the session family, so viewing a picture logged the
 * user out — in both directions, sender and receiver.
 *
 * The properties asserted here are the ones that make that impossible:
 *   - identity (attachment id) and access URL are separate things, and only the
 *     identity is ever derived from message content;
 *   - N concurrent renders of one attachment produce exactly ONE grant request;
 *   - a failed grant is a media failure, never a session failure;
 *   - sign-out drops every grant, so account B cannot inherit account A's.
 */
const mockPulseApi = jest.fn();

jest.mock("../../api/pulseApi", () => ({
  pulseApi: (path: string, options?: unknown) => mockPulseApi(path, options)
}));

import {
  attachmentIdFromMediaUrl,
  grantMessengerMediaAccess,
  isProtectedMessengerMediaUrl,
  resetMessengerMediaAccess,
  resolveCanonicalMessengerMediaId,
  resolveMessengerMediaAccessUrl
} from "../messengerMediaAccess";

class FakeApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

function grant(attachmentId: number, token = "tok", expiresIn = 900) {
  return {
    ok: true,
    attachment_id: attachmentId,
    access_url: `/api/messages/media/${attachmentId}/download?mt=${token}`,
    expires_in: expiresIn
  };
}

beforeEach(() => {
  mockPulseApi.mockReset();
  resetMessengerMediaAccess();
});

describe("media identity vs media access URL", () => {
  it("recognises the protected download path in every URL shape", () => {
    expect(isProtectedMessengerMediaUrl("/api/messages/media/42/download")).toBe(true);
    expect(isProtectedMessengerMediaUrl("https://pulsesoc.com/api/messages/media/42/download?mt=x")).toBe(true);
    expect(isProtectedMessengerMediaUrl("/api/messages/media/42/download#frag")).toBe(true);
  });

  it("leaves URLs that never needed a grant alone", () => {
    // R2 signed URLs, static assets and local files already carry their own
    // authorization (or need none). Routing them through the grant endpoint
    // would add a network round trip and a new failure mode for nothing.
    for (const url of [
      "https://cdn.example.com/r2/object.jpg?X-Amz-Signature=abc",
      "/static/img/placeholder.png",
      "file:///cache/pulsesoc/media/42.jpg",
      ""
    ]) {
      expect(isProtectedMessengerMediaUrl(url)).toBe(false);
    }
  });

  it("recovers the canonical attachment id from a legacy URL", () => {
    // Messages written by older builds carry the protected path as content.
    // Identity has to survive that, because the on-disk cache keys on it.
    expect(attachmentIdFromMediaUrl("/api/messages/media/7331/download")).toBe(7331);
    expect(attachmentIdFromMediaUrl("https://pulsesoc.com/api/messages/media/7331/download?mt=zz")).toBe(7331);
    expect(attachmentIdFromMediaUrl("https://cdn.example.com/object.jpg")).toBe(0);
  });

  it("refuses to request a grant without an attachment id", async () => {
    await expect(resolveMessengerMediaAccessUrl(0)).rejects.toThrow("messenger_media_attachment_required");
    expect(mockPulseApi).not.toHaveBeenCalled();
  });
});

describe("canonical media identity", () => {
  /**
   * The historical divergence, taken from production rows.
   *
   * A Comm-v2 message carries TWO different integers for one picture:
   * `attachment_id` is the comm_v2 attachment row, `media_upload_id` is the
   * foundation `message_attachments` row. Only the foundation id addresses
   * `/api/messages/media/<id>/access`. Historical messages requested ids in the
   * 422-585 band against `message_attachments` and every one returned 404,
   * while freshly uploaded foundation attachments worked — because the sender's
   * upload path happens to produce a message whose two ids agree.
   *
   * `Number(attachmentId || 0) || attachmentIdFromMediaUrl(fallbackUrl)` is the
   * expression that caused it: any truthy transport id shadowed the canonical
   * id sitting in the URL beside it.
   */
  it("prefers the foundation media id over a divergent transport attachment id", async () => {
    const identity = { attachmentId: 422, mediaUploadId: 33 };
    const url = "/api/messages/media/33/download";

    const canonical = resolveCanonicalMessengerMediaId(identity, url);
    expect(canonical.id).toBe(33);
    expect(canonical.source).toBe("media_upload_id");

    mockPulseApi.mockResolvedValue(grant(33));
    await grantMessengerMediaAccess(canonical);

    expect(mockPulseApi).toHaveBeenCalledWith("/api/messages/media/33/access", undefined);
    for (const [path] of mockPulseApi.mock.calls) {
      expect(path).not.toBe("/api/messages/media/422/access");
    }
  });

  it("falls back to the id in the protected URL, never to an unproven attachment id", () => {
    // Same divergence, but the payload predates media_upload_id. The URL is
    // still direct evidence: the server mints that path FROM the foundation id.
    const canonical = resolveCanonicalMessengerMediaId(
      { attachmentId: 585 },
      "/api/messages/media/96/download"
    );
    expect(canonical.id).toBe(96);
    expect(canonical.source).toBe("media_url");
    expect(canonical.alternates).not.toContain(585);
  });

  it("uses attachment_id only when the caller proves it is a foundation media id", () => {
    // The sender's own upload: /api/messages/media/init answers with a
    // foundation id, so that caller may say so explicitly.
    expect(
      resolveCanonicalMessengerMediaId({ attachmentId: 901, attachmentIdIsFoundationMedia: true }, "")
    ).toMatchObject({ id: 901, source: "attachment_id" });
    // Without that proof the same integer is not an identity at all.
    expect(resolveCanonicalMessengerMediaId({ attachmentId: 901 }, "")).toMatchObject({
      id: 0,
      source: "unresolved"
    });
  });

  it("reports unresolved rather than guessing", () => {
    expect(resolveCanonicalMessengerMediaId(undefined, "https://cdn.example.com/o.jpg").id).toBe(0);
    expect(resolveCanonicalMessengerMediaId({ mediaUploadId: 0, attachmentId: 0 }, "").id).toBe(0);
    expect(resolveCanonicalMessengerMediaId({ mediaUploadId: -4 }, "").id).toBe(0);
  });
});

describe("bounded recovery", () => {
  it("re-resolves ONCE to a canonical alternate when the first id is stale", async () => {
    // media_upload_id is present but stale; the protected URL names a media row
    // that does exist. One correction, one new grant, no loop.
    const canonical = resolveCanonicalMessengerMediaId(
      { mediaUploadId: 41, attachmentId: 422 },
      "/api/messages/media/33/download"
    );
    expect(canonical).toMatchObject({ id: 41, alternates: [33] });

    mockPulseApi
      .mockRejectedValueOnce(new FakeApiError(404, "attachment_not_found"))
      .mockResolvedValueOnce(grant(33));

    await expect(grantMessengerMediaAccess(canonical)).resolves.toMatchObject({ attachmentId: 33 });
    expect(mockPulseApi).toHaveBeenCalledTimes(2);
    expect(mockPulseApi.mock.calls.map(([path]) => path)).toEqual([
      "/api/messages/media/41/access",
      "/api/messages/media/33/access"
    ]);
  });

  it("refreshes an expired grant once and retries the same id", async () => {
    mockPulseApi
      .mockRejectedValueOnce(new FakeApiError(410, "media_grant_expired"))
      .mockResolvedValueOnce(grant(33, "fresh"));
    await expect(grantMessengerMediaAccess({ id: 33, alternates: [] })).resolves.toMatchObject({
      url: expect.stringContaining("mt=fresh"),
      attachmentId: 33
    });
    expect(mockPulseApi).toHaveBeenCalledTimes(2);
  });

  it("treats a true 404 on the canonical id as terminal unavailable", async () => {
    // No alternate to correct to: the media is gone. One request, then stop.
    mockPulseApi.mockRejectedValue(new FakeApiError(404, "attachment_not_found"));
    await expect(grantMessengerMediaAccess({ id: 33, alternates: [] })).rejects.toMatchObject({ status: 404 });
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
  });

  it("never retries forever: a second failure is the last one", async () => {
    mockPulseApi.mockRejectedValue(new FakeApiError(404, "attachment_not_found"));
    await expect(grantMessengerMediaAccess({ id: 41, alternates: [33] })).rejects.toMatchObject({ status: 404 });
    expect(mockPulseApi).toHaveBeenCalledTimes(2);
  });

  it("does not re-resolve on a transient network failure", async () => {
    // Only a missing-media answer is evidence that the IDENTITY was wrong.
    // Swapping ids on a flaky connection would request the wrong media.
    mockPulseApi.mockRejectedValue(new Error("network"));
    await expect(grantMessengerMediaAccess({ id: 41, alternates: [33] })).rejects.toThrow("network");
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
  });

  it("recovery still only ever touches the grant route", async () => {
    // The hard invariant: no refresh, no session restore, no rotation, no
    // revocation. Whatever goes wrong, this layer speaks to /access and stops.
    mockPulseApi
      .mockRejectedValueOnce(new FakeApiError(404, "attachment_not_found"))
      .mockResolvedValueOnce(grant(33));
    await grantMessengerMediaAccess({ id: 41, alternates: [33] });
    for (const [path] of mockPulseApi.mock.calls) {
      expect(path).toMatch(/^\/api\/messages\/media\/\d+\/access$/);
    }
  });
});

describe("concurrent thumbnail loads", () => {
  it("issues ONE grant request for four simultaneous renders of one attachment", async () => {
    // This is the exact shape of the production failure: a conversation opens
    // and several thumbnails for the same attachment mount in the same tick.
    let release: (value: unknown) => void = () => undefined;
    mockPulseApi.mockImplementation(
      () => new Promise((resolve) => {
        release = resolve;
      })
    );

    const pending = [
      resolveMessengerMediaAccessUrl(42),
      resolveMessengerMediaAccessUrl(42),
      resolveMessengerMediaAccessUrl(42),
      resolveMessengerMediaAccessUrl(42)
    ];
    release(grant(42));
    const urls = await Promise.all(pending);

    expect(mockPulseApi).toHaveBeenCalledTimes(1);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/messages/media/42/access", undefined);
    expect(new Set(urls).size).toBe(1);
  });

  it("still requests separately for distinct attachments", async () => {
    mockPulseApi.mockImplementation(async (path: string) =>
      grant(Number(/media\/(\d+)\//.exec(path)?.[1] || 0))
    );
    const [a, b, c] = await Promise.all([
      resolveMessengerMediaAccessUrl(1),
      resolveMessengerMediaAccessUrl(2),
      resolveMessengerMediaAccessUrl(3)
    ]);
    expect(mockPulseApi).toHaveBeenCalledTimes(3);
    expect([a, b, c].map(attachmentIdFromMediaUrl)).toEqual([1, 2, 3]);
  });

  it("reuses an unexpired grant instead of asking again", async () => {
    mockPulseApi.mockResolvedValue(grant(42));
    await resolveMessengerMediaAccessUrl(42);
    await resolveMessengerMediaAccessUrl(42);
    await resolveMessengerMediaAccessUrl(42);
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
  });

  it("re-requests once a grant is inside the renewal margin", async () => {
    // A grant that expires in 5s must not be handed to a load that is about to
    // start; renewing early is cheaper than a spurious "Image unavailable".
    mockPulseApi.mockResolvedValueOnce(grant(42, "old", 5)).mockResolvedValueOnce(grant(42, "new", 900));
    const first = await resolveMessengerMediaAccessUrl(42);
    const second = await resolveMessengerMediaAccessUrl(42);
    expect(first).toContain("mt=old");
    expect(second).toContain("mt=new");
    expect(mockPulseApi).toHaveBeenCalledTimes(2);
  });

  it("does not cache a failure, and does not wedge the attachment", async () => {
    // A transient grant failure must leave the attachment retryable. The
    // in-flight entry has to be cleared on rejection as well as on success.
    mockPulseApi.mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce(grant(42));
    await expect(resolveMessengerMediaAccessUrl(42)).rejects.toThrow("network");
    await expect(resolveMessengerMediaAccessUrl(42)).resolves.toContain("mt=tok");
  });

  it("treats a grant response with no URL as a media failure", async () => {
    mockPulseApi.mockResolvedValue({ ok: true });
    await expect(resolveMessengerMediaAccessUrl(42)).rejects.toThrow("messenger_media_access_url_missing");
  });
});

describe("sender and receiver paths", () => {
  it("sender: a freshly uploaded attachment renders from a grant, not the raw path", async () => {
    // Stage 7. The upload response hands back the protected path; the renderer
    // must exchange it rather than load it directly.
    mockPulseApi.mockResolvedValue(grant(901));
    const uploaded = "/api/messages/media/901/download";
    expect(isProtectedMessengerMediaUrl(uploaded)).toBe(true);
    const rendered = await resolveMessengerMediaAccessUrl(attachmentIdFromMediaUrl(uploaded));
    expect(rendered).toContain("mt=");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/messages/media/901/access", undefined);
  });

  it("receiver: opening the same attachment full-screen reuses the thumbnail's grant", async () => {
    // Stage 8. Tapping through to the viewer must not mint a second credential
    // or issue a second authenticated request for an image already on screen.
    mockPulseApi.mockResolvedValue(grant(902));
    const thumbnail = await resolveMessengerMediaAccessUrl(902);
    const fullScreen = await resolveMessengerMediaAccessUrl(902);
    expect(fullScreen).toBe(thumbnail);
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
  });

  it("a denied grant surfaces as a media error and never as a session error", async () => {
    // The whole incident was a media failure being escalated into an auth
    // failure. Whatever the server says, this layer only ever rejects.
    mockPulseApi.mockRejectedValue(new Error("media_credential_required"));
    await expect(resolveMessengerMediaAccessUrl(903)).rejects.toThrow("media_credential_required");
    // No token refresh, no logout hook, no session call — only the grant route.
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
    expect(mockPulseApi.mock.calls[0][0]).toBe("/api/messages/media/903/access");
  });
});

describe("account isolation", () => {
  it("sign-out drops every grant so the next account starts empty", async () => {
    mockPulseApi.mockResolvedValueOnce(grant(42, "user-a")).mockResolvedValueOnce(grant(42, "user-b"));
    const asUserA = await resolveMessengerMediaAccessUrl(42);
    expect(asUserA).toContain("mt=user-a");

    resetMessengerMediaAccess();

    const asUserB = await resolveMessengerMediaAccessUrl(42);
    expect(asUserB).toContain("mt=user-b");
    expect(mockPulseApi).toHaveBeenCalledTimes(2);
  });

  it("clearing while a grant is in flight does not leak it to the next account", async () => {
    let release: (value: unknown) => void = () => undefined;
    mockPulseApi.mockImplementationOnce(
      () => new Promise((resolve) => {
        release = resolve;
      })
    );
    const inFlight = resolveMessengerMediaAccessUrl(42);
    resetMessengerMediaAccess();
    release(grant(42, "user-a"));
    await inFlight;

    mockPulseApi.mockResolvedValueOnce(grant(42, "user-b"));
    resetMessengerMediaAccess();
    await expect(resolveMessengerMediaAccessUrl(42)).resolves.toContain("mt=user-b");
  });
});
