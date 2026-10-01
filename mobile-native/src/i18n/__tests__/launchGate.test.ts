/**
 * The launch gate: what `ready` promises, and what it deliberately does not do.
 *
 * The provider's contract has two halves that pull in opposite directions, and
 * a reader who sees only one half will "fix" the other:
 *
 *   1. `ready` must not flip until the core catalogs for the resolved language
 *      are resident AND the locale has been committed. `App.tsx` withholds the
 *      first visible frame on it, so a `ready` that leads the catalogs by even
 *      one render is exactly the flash of English this whole tier exists to
 *      prevent.
 *   2. Children must render anyway, from the very first frame, with `ready`
 *      false. Their effects are where the session restore starts; moving the
 *      hold into the provider would queue a network round trip behind a local
 *      catalog read and cost launch time for no visible gain.
 *
 * So this file asserts the absence of a state rather than a sequence of calls:
 * no render is ever observed in which `ready` is true and the copy is still
 * English. Recording every render and filtering is what catches an ordering
 * regression that a single post-settle assertion would sleep straight through.
 *
 * Two mechanics worth knowing before editing:
 *
 *   - Every module is required *inside* the helper, after `jest.resetModules()`.
 *     The engine's catalog cache, its active locale and the JS layout direction
 *     are module-level, so without the reset the second case would start with
 *     the first case's catalogs already resident and could pass while genuinely
 *     broken. And once the registry is reset, React must be required from the
 *     same side of it as the renderer — a top-level `import` (including the JSX
 *     runtime's implicit one) leaves the provider's hooks pointing at a React
 *     whose dispatcher the fresh renderer never installed, which surfaces as
 *     "Cannot read properties of null (reading 'useRef')". Hence
 *     `createElement` here rather than JSX, and hence the `.ts` extension.
 *   - `api/account` is mocked at the module boundary because the global `fetch`
 *     is rejected suite-wide; the launch path's account lookup is best-effort
 *     and would otherwise settle asynchronously after the test has finished.
 */

jest.mock("../../api/account", () => ({
  getAccountLanguage: jest.fn().mockResolvedValue({ language: null }),
  updateAccountLanguage: jest.fn().mockResolvedValue({ ok: true })
}));

/**
 * The real `activateLocale` resolves within a microtask, because catalogs are
 * plain `require`s of bundled JSON. That makes an ordering bug INVISIBLE to a
 * test: React coalesces a premature `setReady(true)` with the locale commit that
 * follows one microtask later, so the bad intermediate state is never rendered
 * and every assertion still passes. Moving `setReady` above the catalog load was
 * confirmed to leave this file green before this wrapper existed.
 *
 * Pushing the load onto a macrotask restores the window without touching what
 * the engine actually does. It is also the honest model of where this is
 * heading: the moment a catalog arrives over the air, or a language is split
 * behind a real dynamic import, the microtask coincidence that hides the bug in
 * production disappears too.
 */
jest.mock("../engine", () => {
  const actual = jest.requireActual("../engine");
  return {
    ...actual,
    activateLocale: async (locale: string, namespaces: unknown) => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      return actual.activateLocale(locale, namespaces);
    }
  };
});

const LANGUAGE_KEY = "pulsesoc.i18n.language.v1";
const FOLLOW_DEVICE_KEY = "pulsesoc.i18n.followDevice.v1";

/** "Save" in each language's core tier — the cheapest proof of which catalog is live. */
const SAVE = { en: "Save", es: "Guardar", ar: "حفظ" } as const;

interface Sample {
  ready: boolean;
  locale: string;
  direction: string;
  save: string;
}

/** Mounts the provider with a pinned language, recording one sample per render. */
async function mountWithStoredLanguage(language: keyof typeof SAVE) {
  jest.resetModules();

  // The package's jest mock is CommonJS, so the ESM default is absent here even
  // though app code imports it as a default.
  const asyncStorageModule = require("@react-native-async-storage/async-storage");
  const AsyncStorage = asyncStorageModule.default ?? asyncStorageModule;
  await AsyncStorage.clear();
  await AsyncStorage.multiSet([
    [LANGUAGE_KEY, language],
    [FOLLOW_DEVICE_KEY, "0"]
  ]);

  const React = require("react");
  const TestRenderer = require("react-test-renderer");
  const { I18nProvider, useI18n } = require("../I18nContext");

  const samples: Sample[] = [];
  /** `ready` as observed by a child effect on mount — the session-restore slot. */
  let readyAtFirstEffect: boolean | null = null;

  function Probe() {
    const { ready, locale, direction, t } = useI18n();
    samples.push({ ready, locale, direction, save: t("common:actions.save") });
    const recorded = React.useRef(false);
    React.useEffect(() => {
      if (recorded.current) return;
      recorded.current = true;
      readyAtFirstEffect = ready;
    });
    return null;
  }

  await TestRenderer.act(async () => {
    TestRenderer.create(React.createElement(I18nProvider, null, React.createElement(Probe)));
  });
  // The launch path is a chain of awaits (storage read, then catalog load, then
  // the catalog-version write), so one act only guarantees the queue was drained
  // once. Settling on a timer rather than `Promise.resolve` is required: the
  // catalog load above is deliberately a macrotask, which a microtask flush
  // would never reach.
  for (let attempt = 0; attempt < 20 && !samples[samples.length - 1]?.ready; attempt += 1) {
    await TestRenderer.act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  expect(samples[samples.length - 1]?.ready).toBe(true);

  return { samples, readyAtFirstEffect: () => readyAtFirstEffect };
}

describe("i18n launch gate", () => {
  // The pre-ready frame has no catalogs resident at all, not even the default
  // one, so probing `t()` there trips the engine's dev-only missing-key warning
  // every run. That is the correct behaviour — and a sharper statement of the
  // stakes than the header comment makes, since an ungated first frame would
  // paint raw keys rather than English — but it is not a signal worth printing
  // three times per run.
  let warnSpy: jest.SpyInstance;
  beforeEach(() => {
    warnSpy = jest.spyOn(console, "warn").mockImplementation(() => undefined);
  });
  afterEach(() => {
    warnSpy.mockRestore();
  });

  it("never reports ready while the copy is still English", async () => {
    const { samples } = await mountWithStoredLanguage("es");

    const readySamples = samples.filter((sample) => sample.ready);
    expect(readySamples.length).toBeGreaterThan(0);
    for (const sample of readySamples) {
      expect(sample.save).toBe(SAVE.es);
      expect(sample.locale).toBe("es");
    }
    // Stated as an absence too: the loop above passes vacuously if `ready` never
    // carried the English string because it never carried any string at all.
    expect(samples.some((sample) => sample.ready && sample.save === SAVE.en)).toBe(false);
  });

  it("commits the RTL direction no later than ready, not after it", async () => {
    const { samples } = await mountWithStoredLanguage("ar");

    const readySamples = samples.filter((sample) => sample.ready);
    expect(readySamples.length).toBeGreaterThan(0);
    for (const sample of readySamples) {
      expect(sample.save).toBe(SAVE.ar);
      expect(sample.direction).toBe("rtl");
    }
  });

  it("renders children before ready so their effects can start", async () => {
    const { samples, readyAtFirstEffect } = await mountWithStoredLanguage("es");

    // The first render is the frame `App.tsx` covers with its text-free spinner.
    expect(samples[0].ready).toBe(false);
    // And the first child effect ran in that frame. This is the half that a
    // provider-level `if (!ready) return <Splash />` would silently remove.
    expect(readyAtFirstEffect()).toBe(false);
  });
});
