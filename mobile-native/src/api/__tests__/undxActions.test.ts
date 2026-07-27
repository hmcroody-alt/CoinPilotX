const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import {
  activateUndxEmergencyStop,
  createUndxMarketplaceListingDraft,
  executeUndxMarketplaceListingPublish,
  fetchUndxActionCenter,
  fetchUndxPermissions,
  fetchUndxTools,
  grantUndxActionPermission,
  planUndxMarketplaceListingPublish,
  recordUndxActionPolicy,
  recordUndxActionRequest,
  recordUndxActionReceipt,
  registerUndxActionTool
} from "../undxActions";

describe("UNDX governed action API", () => {
  beforeEach(() => mockPulseApi.mockReset());

  it("records governance policies and requests through server-authoritative routes", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, result: {} });

    await recordUndxActionPolicy({
      org_id: "coinplotxai",
      action_type: "marketplace.product.create",
      effect: "allow",
      max_risk: "medium"
    });
    await recordUndxActionRequest({
      org_id: "coinplotxai",
      actor: "user:7",
      action_type: "marketplace.product.create",
      risk: "low",
      params: { title: "Signal" }
    });

    expect(mockPulseApi.mock.calls[0][0]).toBe("/api/business-os/undx/policies");
    expect(mockPulseApi.mock.calls[1][0]).toBe("/api/business-os/undx/requests");
    expect(JSON.parse(mockPulseApi.mock.calls[1][1].body)).toMatchObject({
      org_id: "coinplotxai",
      actor: "user:7",
      params: { title: "Signal" }
    });
  });

  it("uses the shared tool, permission, receipt, and emergency-stop routes", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, result: {} });

    await registerUndxActionTool({
      tool_name: "marketplace.create_product",
      action_type: "marketplace.product.create",
      product_area: "marketplace"
    });
    await grantUndxActionPermission({
      org_id: "coinplotxai",
      actor: "user:7",
      action_type: "marketplace.product.create",
      effect: "allow"
    });
    await recordUndxActionReceipt({
      org_id: "coinplotxai",
      actor: "user:7",
      action_type: "marketplace.product.create",
      status: "verified"
    });
    await activateUndxEmergencyStop({
      org_id: "coinplotxai",
      actor: "admin:1",
      reason: "QA stop"
    });

    expect(mockPulseApi.mock.calls.map((call) => call[0])).toEqual([
      "/api/business-os/undx/tools",
      "/api/business-os/undx/permissions",
      "/api/business-os/undx/receipts",
      "/api/business-os/undx/emergency-stop"
    ]);
  });

  it("reads the Action Center, tools, and permissions with bounded query parameters", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, result: {} });

    await fetchUndxActionCenter({ orgId: "coinplotxai", limit: 12 });
    await fetchUndxTools({ productArea: "marketplace", limit: 20 });
    await fetchUndxPermissions({ orgId: "coinplotxai", actor: "user:7", limit: 5 });

    expect(mockPulseApi.mock.calls[0][0]).toBe("/api/business-os/undx/action-center?org_id=coinplotxai&limit=12");
    expect(mockPulseApi.mock.calls[1][0]).toBe("/api/business-os/undx/tools?product_area=marketplace&limit=20");
    expect(mockPulseApi.mock.calls[2][0]).toBe("/api/business-os/undx/permissions?org_id=coinplotxai&actor=user%3A7&limit=5");
  });

  it("creates and publishes Marketplace listings only through governed UNDX workflow routes", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, result: {} });

    await createUndxMarketplaceListingDraft({
      org_id: "coinplotxai",
      actor: "user:7",
      listing: {
        title: "Founders signal",
        description: "Native governed listing",
        price_cents: 1299,
        fulfillment_type: "physical",
        inventory_qty: 3
      }
    });
    await planUndxMarketplaceListingPublish({
      org_id: "coinplotxai",
      actor: "user:7",
      product_id: "p_123"
    });
    await executeUndxMarketplaceListingPublish({
      org_id: "coinplotxai",
      actor: "user:7",
      request_id: "req_123",
      product_id: "p_123",
      confirmation_token: "confirm"
    });

    expect(mockPulseApi.mock.calls.map((call) => call[0])).toEqual([
      "/api/business-os/undx/marketplace/listings/draft",
      "/api/business-os/undx/marketplace/listings/publish/plan",
      "/api/business-os/undx/marketplace/listings/publish/execute"
    ]);
    expect(JSON.parse(mockPulseApi.mock.calls[0][1].body).listing).toMatchObject({
      title: "Founders signal",
      price_cents: 1299,
      inventory_qty: 3
    });
  });
});
