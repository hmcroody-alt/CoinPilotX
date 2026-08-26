import { readdirSync, readFileSync, statSync } from "fs";
import { join } from "path";

import {
  BUSINESS_OS_MODULES,
  businessOsModules,
  hasBusinessOsModules,
  lockedBusinessOsModules,
  readyBusinessOsModules
} from "../businessOsReadiness";
import { BUSINESS_OS_SECTIONS, businessOsSection } from "../../api/businessOs";
import { readinessBadge } from "../launchReadiness";

const SRC = join(__dirname, "..", "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      return entry === "__tests__" || entry === "node_modules" ? [] : sourceFiles(full);
    }
    return /\.tsx?$/.test(entry) ? [full] : [];
  });
}

const allSource = sourceFiles(SRC).map((file) => ({ file, text: readFileSync(file, "utf8") }));

describe("the Business OS readiness table", () => {
  it("declares a list for every section in the registry, and no key that is not one", () => {
    const registryKeys = BUSINESS_OS_SECTIONS.map((section) => section.key).sort();
    expect(Object.keys(BUSINESS_OS_MODULES).sort()).toEqual(registryKeys);
  });

  /**
   * The dead-button rule, enforced at the config layer rather than left to each
   * screen. A READY module is one a member can open, so it has to say where.
   */
  it("gives every READY module a destination", () => {
    BUSINESS_OS_SECTIONS.forEach((section) => {
      readyBusinessOsModules(section.key).forEach((module) => {
        expect(typeof module.route).toBe("string");
        expect(module.route!.length).toBeGreaterThan(0);
      });
    });
  });

  /**
   * The mirror of the rule above. A locked module with a route is a workflow
   * someone wired up and then locked in a way that a later edit could quietly
   * un-lock — the state and the destination must not disagree.
   */
  it("gives no locked module a destination", () => {
    BUSINESS_OS_SECTIONS.forEach((section) => {
      lockedBusinessOsModules(section.key).forEach((module) => {
        expect(module.route).toBeUndefined();
      });
    });
  });

  it("points every READY module at a screen the navigator registers", () => {
    const navigator = readFileSync(join(SRC, "navigation", "AppNavigator.tsx"), "utf8");
    const registered = new Set(
      Array.from(navigator.matchAll(/<Stack\.Screen\s+name="([^"]+)"/g)).map((m) => m[1])
    );
    expect(registered.size).toBeGreaterThan(20);

    BUSINESS_OS_SECTIONS.forEach((section) => {
      readyBusinessOsModules(section.key).forEach((module) => {
        expect(registered.has(module.route!)).toBe(true);
      });
    });
  });

  it("gives every module a distinct key inside its own section", () => {
    BUSINESS_OS_SECTIONS.forEach((section) => {
      const keys = businessOsModules(section.key).map((module) => module.key);
      expect(new Set(keys).size).toBe(keys.length);
    });
  });

  it("gives every module a label and a blurb a member can read", () => {
    BUSINESS_OS_SECTIONS.forEach((section) => {
      businessOsModules(section.key).forEach((module) => {
        expect(module.label.trim().length).toBeGreaterThan(0);
        expect(module.blurb.trim().length).toBeGreaterThan(0);
        // The label is a product name, not a key echoed back at the member.
        expect(module.label).not.toBe(module.key);
      });
    });
  });

  /**
   * A locked module is a promise on a member's screen. The copy that carries it
   * must not read as a fault — "error", "failed", "unavailable" and the like all
   * say the app is broken, which is the opposite of "this is coming".
   */
  it("never describes an unbuilt module as a failure", () => {
    const faultWords = [/error/i, /failed/i, /unavailable/i, /not implemented/i, /broken/i, /sorry/i];
    BUSINESS_OS_SECTIONS.forEach((section) => {
      businessOsModules(section.key).forEach((module) => {
        faultWords.forEach((word) => {
          expect(module.label).not.toMatch(word);
          expect(module.blurb).not.toMatch(word);
        });
      });
    });
  });

  it("gives the two locked states different badges and READY none", () => {
    expect(readinessBadge("COMING_SOON")).toBe("COMING SOON");
    expect(readinessBadge("BUILDING")).toBe("BUILDING");
    expect(readinessBadge("READY")).toBeNull();
  });
});

describe("every section that promises modules has somewhere to show them", () => {
  /**
   * The failure this catches is invisible by construction and cannot be caught
   * by rendering any single screen: a section gains modules in the table, no
   * screen mounts `BusinessOsModules` for it, and the roadmap silently never
   * appears. Types are satisfied, every test passes, and the member sees
   * nothing. So the check reads the source for the mount instead.
   *
   * Preview sections are exempt because `BusinessOsSectionScreen` mounts the
   * panel generically from the route param — there is no per-section literal to
   * find, and that screen is covered by its own test.
   */
  const previewKeys = new Set(
    BUSINESS_OS_SECTIONS.filter((section) => section.preview).map((section) => section.key)
  );

  const withModules = BUSINESS_OS_SECTIONS.filter(
    (section) => hasBusinessOsModules(section.key) && !previewKeys.has(section.key)
  );

  it("finds sections to check", () => {
    expect(withModules.length).toBeGreaterThan(0);
  });

  it.each(withModules.map((section) => [section.key]))(
    "mounts the roadmap panel for %s",
    (key) => {
      const mounted = allSource.some(
        ({ file, text }) =>
          file.includes(join("src", "screens")) && text.includes(`<BusinessOsModules section="${key}"`)
      );
      expect(mounted).toBe(true);
    }
  );

  it("mounts nothing for a section that declares no modules", () => {
    const empty = BUSINESS_OS_SECTIONS.filter((section) => !hasBusinessOsModules(section.key));
    expect(empty.length).toBeGreaterThan(0);
    empty.forEach((section) => {
      const mounted = allSource.some(({ text }) =>
        text.includes(`<BusinessOsModules section="${section.key}"`)
      );
      expect(mounted).toBe(false);
    });
  });

  /**
   * Settings and Verification are reached from a dozen places outside Business
   * OS. A roadmap panel on either would follow members into Trust & Safety and
   * the profile sheet, which is not this layer's business.
   */
  it("keeps the roadmap off the shared surfaces", () => {
    expect(businessOsModules("settings")).toEqual([]);
    expect(businessOsModules("verification")).toEqual([]);
    expect(businessOsSection("settings")?.tab).toBe(true);
  });
});

describe("the sections with no working screen behind them", () => {
  it.each([["customers"], ["team"]] as const)("gives %s something to show", (key) => {
    const modules = businessOsModules(key);
    expect(modules.length).toBeGreaterThan(0);
    // A landing page of nothing but locks tells a member the section is dead.
    // Customers carries a working one; Team is honest that it has none yet.
    expect(lockedBusinessOsModules(key).length).toBeGreaterThan(0);
  });

  it("gives Customers a module that actually opens", () => {
    const ready = readyBusinessOsModules("customers");
    expect(ready.length).toBeGreaterThan(0);
    expect(ready[0].route).toBe("BusinessOsMessages");
  });
});
