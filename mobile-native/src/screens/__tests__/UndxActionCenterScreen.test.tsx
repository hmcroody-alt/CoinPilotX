import React from "react";
import { Alert } from "react-native";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

const mockFetchCenter = jest.fn();
const mockFetchTools = jest.fn();
const mockFetchPermissions = jest.fn();
const mockCreateDraft = jest.fn();
const mockPlanPublish = jest.fn();
const mockExecutePublish = jest.fn();

jest.mock("../../api/undxActions", () => ({
  fetchUndxActionCenter: (...args: unknown[]) => mockFetchCenter(...args),
  fetchUndxTools: (...args: unknown[]) => mockFetchTools(...args),
  fetchUndxPermissions: (...args: unknown[]) => mockFetchPermissions(...args),
  createUndxMarketplaceListingDraft: (...args: unknown[]) => mockCreateDraft(...args),
  planUndxMarketplaceListingPublish: (...args: unknown[]) => mockPlanPublish(...args),
  executeUndxMarketplaceListingPublish: (...args: unknown[]) => mockExecutePublish(...args)
}));

import { UndxActionCenterScreen } from "../UndxActionCenterScreen";

describe("UNDX Action Center Marketplace confirmation", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockFetchCenter.mockResolvedValue({ ok: true, result: {} });
    mockFetchTools.mockResolvedValue({ ok: true, result: { tools: [] } });
    mockFetchPermissions.mockResolvedValue({ ok: true, result: { permissions: [] } });
    mockPlanPublish.mockResolvedValue({
      ok: true,
      result: {
        request: { request_id: "req_123" },
        confirmation: { request_id: "req_123" },
        plan: {
          confirmation_token: "secret_single_use_token",
          risk: "high",
          summary: "Publish this Marketplace product.",
          expires_at: "2026-07-25T20:00:00.000000Z"
        }
      }
    });
    mockExecutePublish.mockResolvedValue({ ok: true, result: {} });
  });

  it("keeps the token private and requires a native confirmation before publish", async () => {
    const alert = jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
    const screen = render(
      <UndxActionCenterScreen
        route={{ key: "undx", name: "UndxActionCenter", params: {
          orgId: "attacker-controlled-org",
          actor: "attacker-controlled-actor"
        } } as never}
        navigation={{} as never}
      />
    );
    await screen.findByText("Governed Marketplace workflow");

    fireEvent.changeText(screen.getByPlaceholderText("Product ID"), "product_123");
    await act(async () => {
      fireEvent.press(screen.getByText("Plan publish"));
    });
    await screen.findByText("Publish approval ready");

    expect(screen.queryByPlaceholderText("Confirmation token from publish plan")).toBeNull();
    expect(screen.queryByDisplayValue("secret_single_use_token")).toBeNull();

    fireEvent.press(screen.getByText("Review and publish"));
    expect(mockExecutePublish).not.toHaveBeenCalled();
    expect(alert).toHaveBeenCalledTimes(1);

    const buttons = alert.mock.calls[0][2] || [];
    const publish = buttons.find((button) => button.text === "Publish");
    await act(async () => {
      publish?.onPress?.();
    });
    await waitFor(() => expect(mockExecutePublish).toHaveBeenCalledWith({
      org_id: "attacker-controlled-org",
      actor: "attacker-controlled-actor",
      request_id: "req_123",
      product_id: "product_123",
      confirmation_token: "secret_single_use_token"
    }));
    alert.mockRestore();
  });
});
