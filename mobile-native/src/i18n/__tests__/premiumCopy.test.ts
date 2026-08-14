/**
 * Marketing honesty, enforced on the copy itself.
 *
 * The Premium brief bans a specific family of lies — a hardcoded "SAVE 17%", a
 * price that ignores the member's storefront, an invented "1,246 / 2,000
 * requests" or "10 GB / 10 GB" quota, a fabricated user count. Every one of
 * those has to be written down as a literal number somewhere before it can be
 * shown, so the sharpest test available is that the Premium namespace contains
 * no digits at all in any language. Real figures reach the screen only through
 * placeholders the server or StoreKit fills.
 *
 * The second rule is the flicker rule from the tile: there is deliberately no
 * word for the free state, so the tile cannot say "Free" and then correct
 * itself.
 */

import { loadCatalogBundle, type CatalogBundle } from "../catalogs";
import { SUPPORTED_LOCALE_CODES } from "../locales";

function bundle(locale: string): CatalogBundle {
  const loaded = loadCatalogBundle(locale, "premium");
  if (!loaded) throw new Error(`${locale} ships no premium namespace`);
  return loaded;
}

function leaves(value: CatalogBundle, path = ""): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  for (const [key, child] of Object.entries(value)) {
    const next = path ? `${path}.${key}` : key;
    if (typeof child === "string") out.push([next, child]);
    else if (child && typeof child === "object") out.push(...leaves(child as CatalogBundle, next));
  }
  return out;
}

describe.each(SUPPORTED_LOCALE_CODES)("premium copy (%s)", (locale) => {
  it("ships the namespace", () => {
    expect(Object.keys(bundle(locale)).length).toBeGreaterThan(0);
  });

  it("states no number of its own", () => {
    // Prices, savings percentages, allowances and founder numbers all arrive as
    // placeholders. A literal digit here would be a figure the app asserted
    // without being told it.
    const offenders = leaves(bundle(locale)).filter(([, text]) => /[0-9٠-٩۰-۹]/.test(text));
    expect(offenders).toEqual([]);
  });

  it("has no word for the free state on the tile", () => {
    // `premiumTileState` returns null for every non-member state, and null
    // renders no micro-status. There is nothing to translate, and nothing that
    // could flash at a paying member on a cold start.
    const status = (bundle(locale).tile as CatalogBundle).status as CatalogBundle;
    expect(Object.keys(status).sort()).toEqual(["active", "founder", "grace"]);
  });

  it("names every tile accessibility state, including the one with no badge", () => {
    const a11y = (bundle(locale).tile as CatalogBundle).a11y as CatalogBundle;
    expect(Object.keys(a11y).sort()).toEqual(["active", "founder", "grace", "none"]);
    for (const [, text] of leaves(a11y)) expect(text.trim().length).toBeGreaterThan(0);
  });

  it("keeps the savings badge tied to a computed placeholder", () => {
    const plans = bundle(locale).plans as CatalogBundle;
    expect(String(plans.save)).toContain("{{percent}}");
  });

  it("describes the allowance from server counts only", () => {
    const benefits = bundle(locale).benefits as CatalogBundle;
    expect(String(benefits.allowance)).toContain("{{used}}");
    expect(String(benefits.allowance)).toContain("{{limit}}");
  });

  it("offers no local cancel", () => {
    // Cancelling is Apple's to perform. The only management string is a handoff.
    const manage = bundle(locale).manage as CatalogBundle;
    expect(Object.keys(manage).sort()).toEqual(["action", "failed", "hint", "note"]);
  });
});
