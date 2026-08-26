/**
 * Which request each connection call actually makes.
 *
 * `/api/pages/<id>/links` is one URL serving three different acts — read what
 * is connected, connect something, connect nothing — separated only by the
 * verb. The Connections screen test mocks this module, so it can prove the
 * Disconnect button calls `clearPageLink`; it cannot prove `clearPageLink`
 * isn't POSTing, which against this route is a *connect* with a missing ref.
 * One word in an object literal is the whole difference, and it typechecks
 * either way.
 *
 * The link type is also pinned as a body field, where the GET's filter and the
 * POST's payload live too. The server does accept `?type=` on DELETE as a
 * transit fallback — a DELETE body has no defined semantics in HTTP and an
 * intermediary may drop it — so nothing would *fail* if this client drifted
 * into the query string. It would just quietly become the one verb on this
 * route that names its subject somewhere else.
 */

const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import { clearPageLink, getPageLinkOptions, listPageLinks, setPageLink } from "../pages";

beforeEach(() => {
  mockPulseApi.mockReset();
  mockPulseApi.mockResolvedValue({ ok: true });
});

describe("connecting", () => {
  it("posts the kind and the thing to the links route", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, link: { link_type: "store", ref_id: "42" } });
    await setPageLink(41, "store", "42");

    const [url, init] = mockPulseApi.mock.calls[0];
    expect(url).toBe("/api/pages/41/links");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ link_type: "store", ref_id: "42" });
  });
});

describe("disconnecting", () => {
  it("deletes rather than posting", async () => {
    // POST to this same URL is a connect. A verb typo here would send a
    // connect-with-no-ref and be reported to the member as a disconnect.
    mockPulseApi.mockResolvedValue({ ok: true, link: { link_type: "store", ref_id: "" } });
    await clearPageLink(41, "store");

    const [url, init] = mockPulseApi.mock.calls[0];
    expect(url).toBe("/api/pages/41/links");
    expect(init.method).toBe("DELETE");
    expect(init.method).not.toBe("POST");
  });

  it("names the kind where the other two verbs name theirs — the body", async () => {
    await clearPageLink(41, "music_artist");

    const [url, init] = mockPulseApi.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ link_type: "music_artist" });
    expect(String(url)).not.toContain("music_artist");
    expect(String(url)).not.toContain("?");
  });

  it("does not carry a ref id it was never given", async () => {
    // Disconnect is "point at nothing", not "point at the empty string": the
    // server's DELETE branch reads only the kind, and a stray ref_id here would
    // be a second, silently-ignored idea of what this call means.
    await clearPageLink(41, "store");
    expect(JSON.parse(mockPulseApi.mock.calls[0][1].body)).not.toHaveProperty("ref_id");
  });

  it("hands the server's refusal back rather than swallowing it", async () => {
    const { PulseApiError } = jest.requireActual("../pulseApi");
    mockPulseApi.mockRejectedValue(new PulseApiError("There is nothing connected.", 404));
    await expect(clearPageLink(41, "store")).rejects.toThrow("There is nothing connected.");
  });
});

describe("reading what is connected", () => {
  it("asks the links route and reads an empty answer as none", async () => {
    mockPulseApi.mockResolvedValue({ ok: true });
    await expect(listPageLinks(41)).resolves.toEqual([]);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pages/41/links");
  });

  it("filters by kind through the query string on the read", async () => {
    // Safe here in a way it is not on the DELETE: this one only reads.
    mockPulseApi.mockResolvedValue({ ok: true, links: [{ link_type: "store", ref_id: "42" }] });
    await listPageLinks(41, "store");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pages/41/links?type=store");
  });

  it("reads the options from the options route, which is not the links route", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, page_id: 41, role: "OWNER", links: [] });
    const view = await getPageLinkOptions(41);

    expect(mockPulseApi).toHaveBeenCalledWith("/api/pages/41/link-options");
    expect(view.page_id).toBe(41);
    expect(view.role).toBe("OWNER");
  });

  it("reads a page with nothing offered as nothing offered, not as a crash", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, page_id: 41, role: "ANALYST" });
    await expect(getPageLinkOptions(41)).resolves.toEqual({
      page_id: 41,
      role: "ANALYST",
      links: []
    });
  });
});
