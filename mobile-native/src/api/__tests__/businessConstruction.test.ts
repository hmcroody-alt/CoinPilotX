import { authenticatedOwnerAccess } from "../businessConstruction";

describe("authenticatedOwnerAccess", () => {
  it.each(["cherieroody@gmail.com", "COINPILOTXAI@GMAIL.COM"])("grants canonical owner email %s", (email) => {
    expect(authenticatedOwnerAccess(email)?.can_access_private_business_os).toBe(true);
    expect(authenticatedOwnerAccess(email)?.developer_mode).toBe(true);
  });

  it("does not grant a display name or unrelated email", () => {
    expect(authenticatedOwnerAccess("Roody Cherie")).toBeNull();
    expect(authenticatedOwnerAccess("someone@example.com")).toBeNull();
  });
});
