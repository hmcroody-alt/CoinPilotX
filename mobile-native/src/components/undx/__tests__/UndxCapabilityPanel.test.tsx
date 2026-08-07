/**
 * The capability panel must render exactly what the server declared and nothing
 * more. These tests assert three reader-level questions: does it show the real
 * company/founder identity, does it list only the capabilities the backend
 * advertised (grouped by domain), and — the honesty case — does a null payload
 * produce an explicit "unavailable" state rather than an empty or fabricated
 * list.
 */

import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

import { UndxCapabilityPanel } from "../UndxCapabilityPanel";
import { UndxSelfKnowledge } from "../../../api/undxSelfKnowledge";

function knowledge(): UndxSelfKnowledge {
  return {
    assistant: { name: "UNDX", description: "PulseSoc intelligence companion." },
    company: {
      version: 1,
      legal_name: "CoinPlotXAI Inc.",
      primary_product: "PulseSoc",
      founder: { name: "Roody Cherie", title: "Founder & CEO" },
      product_category: ["social platform"]
    },
    canonical: {
      company_explanation: "Roody Cherie is the Founder and CEO of CoinPlotXAI Inc.",
      pulsesoc_definition: "PulseSoc is an intelligent digital ecosystem."
    },
    capabilities: {
      counts: {
        total: 2,
        read_only: 1,
        write: 1,
        requires_confirmation: 1,
        by_domain: { crypto: 2 }
      },
      available: [
        {
          capability_id: "crypto.alerts.create",
          description: "Create a price alert.",
          domain: "crypto",
          status: "AVAILABLE",
          executionMode: "EXECUTE",
          requiresConfirmation: true,
          requiresVerification: true,
          receiptRequired: true
        },
        {
          capability_id: "crypto.alerts.list",
          description: "List price alerts.",
          domain: "crypto",
          status: "AVAILABLE",
          executionMode: "READ",
          requiresConfirmation: false,
          requiresVerification: false,
          receiptRequired: false
        }
      ]
    },
    honesty: {
      never_fabricates: ["revenue"],
      capability_rule: "Anything not listed here is not executable yet."
    },
    version: { company_identity: 1 }
  };
}

describe("UndxCapabilityPanel", () => {
  it("renders the server-declared company and founder identity", () => {
    const { getByText, getByTestId } = render(
      <UndxCapabilityPanel knowledge={knowledge()} />
    );
    expect(getByTestId("undx-company-identity")).toBeTruthy();
    expect(getByText("CoinPlotXAI Inc.")).toBeTruthy();
    expect(getByText("Roody Cherie · Founder & CEO")).toBeTruthy();
  });

  it("lists only the advertised capabilities, grouped by domain", () => {
    const { getByTestId } = render(<UndxCapabilityPanel knowledge={knowledge()} />);
    expect(getByTestId("undx-domain-crypto")).toBeTruthy();
    expect(getByTestId("undx-capability-crypto.alerts.create")).toBeTruthy();
    expect(getByTestId("undx-capability-crypto.alerts.list")).toBeTruthy();
  });

  it("surfaces the honesty rule text", () => {
    const { getByTestId } = render(<UndxCapabilityPanel knowledge={knowledge()} />);
    expect(getByTestId("undx-honesty-note").props.children).toContain(
      "not executable yet"
    );
  });

  it("shows an explicit unavailable state for a null payload (no fabrication)", () => {
    const { getByTestId, queryByTestId } = render(
      <UndxCapabilityPanel knowledge={null} />
    );
    expect(getByTestId("undx-capability-unavailable")).toBeTruthy();
    expect(queryByTestId("undx-capability-panel")).toBeNull();
  });

  it("shows a loading state distinct from unavailable", () => {
    const { getByTestId, queryByTestId } = render(
      <UndxCapabilityPanel knowledge={null} loading />
    );
    expect(getByTestId("undx-capability-loading")).toBeTruthy();
    expect(queryByTestId("undx-capability-unavailable")).toBeNull();
  });
});
