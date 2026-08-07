const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import {
  UndxSelfKnowledge,
  availableCapabilityIds,
  capabilitiesByDomain,
  capabilitiesRequiringConfirmation,
  fetchUndxSelfKnowledge,
  isCapabilityAvailable,
  isCapabilityClaimHonest
} from "../undxSelfKnowledge";

function knowledge(): UndxSelfKnowledge {
  return {
    assistant: { name: "UNDX", description: "PulseSoc intelligence companion." },
    company: {
      version: 1,
      legal_name: "CoinPlotXAI Inc.",
      primary_product: "PulseSoc",
      founder: { name: "Roody Cherie", title: "Founder & CEO" },
      product_category: ["social platform", "artificial intelligence platform"]
    },
    canonical: {
      company_explanation: "Roody Cherie is the Founder and CEO of CoinPlotXAI Inc.",
      pulsesoc_definition: "PulseSoc is an intelligent digital ecosystem."
    },
    capabilities: {
      counts: {
        total: 3,
        read_only: 1,
        write: 2,
        requires_confirmation: 1,
        by_domain: { crypto: 2, account: 1 }
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
        },
        {
          capability_id: "account.settings.update",
          description: "Update account settings.",
          domain: "account",
          status: "AVAILABLE",
          executionMode: "EXECUTE",
          requiresConfirmation: false,
          requiresVerification: true,
          receiptRequired: true
        }
      ]
    },
    honesty: {
      never_fabricates: ["revenue", "valuation", "investors"],
      capability_rule: "Anything not listed here is not executable yet."
    },
    version: { company_identity: 1 }
  };
}

beforeEach(() => {
  mockPulseApi.mockReset();
});

describe("fetchUndxSelfKnowledge", () => {
  it("reads the self_knowledge block from the conversation bootstrap", async () => {
    mockPulseApi.mockResolvedValue({ self_knowledge: knowledge(), messages: [] });
    const result = await fetchUndxSelfKnowledge();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse-ai/conversation");
    expect(result?.company.legal_name).toBe("CoinPlotXAI Inc.");
    expect(result?.company.founder.name).toBe("Roody Cherie");
  });

  it("returns null when the backend omits self_knowledge (older server)", async () => {
    mockPulseApi.mockResolvedValue({ messages: [] });
    expect(await fetchUndxSelfKnowledge()).toBeNull();
  });

  it("returns null when self_knowledge is explicitly null", async () => {
    mockPulseApi.mockResolvedValue({ self_knowledge: null });
    expect(await fetchUndxSelfKnowledge()).toBeNull();
  });
});

describe("capability selectors", () => {
  it("treats a listed capability as available and an unlisted one as not", () => {
    const k = knowledge();
    expect(isCapabilityAvailable(k, "crypto.alerts.create")).toBe(true);
    expect(isCapabilityAvailable(k, "crypto.trade.execute")).toBe(false);
  });

  it("keeps capability claims honest: unknown knowledge means not available", () => {
    expect(isCapabilityAvailable(null, "crypto.alerts.create")).toBe(false);
    expect(isCapabilityClaimHonest(null, "crypto.alerts.create")).toBe(false);
    expect(isCapabilityClaimHonest(knowledge(), "not.a.real.capability")).toBe(false);
  });

  it("lists advertised capability ids", () => {
    expect(availableCapabilityIds(knowledge())).toEqual([
      "crypto.alerts.create",
      "crypto.alerts.list",
      "account.settings.update"
    ]);
    expect(availableCapabilityIds(null)).toEqual([]);
  });

  it("groups by domain and sorts within a domain", () => {
    const grouped = capabilitiesByDomain(knowledge());
    expect(Object.keys(grouped).sort()).toEqual(["account", "crypto"]);
    expect(grouped.crypto.map((v) => v.capability_id)).toEqual([
      "crypto.alerts.create",
      "crypto.alerts.list"
    ]);
    expect(capabilitiesByDomain(null)).toEqual({});
  });

  it("selects only capabilities that require confirmation", () => {
    const confirming = capabilitiesRequiringConfirmation(knowledge());
    expect(confirming.map((v) => v.capability_id)).toEqual(["crypto.alerts.create"]);
    expect(capabilitiesRequiringConfirmation(null)).toEqual([]);
  });
});
