import { readFileSync } from "fs";
import { join } from "path";

/**
 * The app has one background. These tests guard the four places that decide
 * whether it is visible at all, and the list of surfaces that must keep covering
 * it up.
 *
 * They read source as text, in the style of `businessOsRoutes.test.ts`, because
 * every defect in this area is invisible to a render test. An opaque view above
 * `PulseBackground` does not make `PulseBackground` render incorrectly — it
 * renders perfectly, underneath something. Nothing in a unit render of a screen
 * can see the view the navigator put above it, and nothing in a unit render of
 * the navigator can see the view `node_modules` put above that.
 */

const ROOT = join(__dirname, "..", "..", "..");
const SRC = join(ROOT, "src");
const MODULES = join(ROOT, "node_modules");

const read = (...parts: string[]) => readFileSync(join(...parts), "utf8");
/** Whitespace-normalised, so a reformat cannot fail a pin that still holds. */
const flat = (source: string) => source.replace(/\s+/g, " ");

const appNavigator = read(SRC, "navigation", "AppNavigator.tsx");
const appEntry = read(ROOT, "App.tsx");
const screenShells = read(SRC, "components", "Screen.tsx");
const settingsShell = read(SRC, "settings", "components", "SettingsShell.tsx");

describe("the navigators do not paint over the shared background", () => {
  it("finds the sources it is guarding", () => {
    expect(appNavigator).toContain("<Tabs.Navigator");
    expect(appNavigator).toContain("<Stack.Navigator");
    expect(appEntry).toContain("<SafeAreaProvider");
  });

  /**
   * The one that would otherwise be missed.
   *
   * `@react-navigation/bottom-tabs` wraps every tab scene in
   * `@react-navigation/elements`' `Screen`, which renders a `Background` view
   * painting the navigation theme's `colors.background`. That view is not in
   * this app's source — grepping `src/` for it returns nothing — and it renders
   * the same way whether or not the integration is correct, so no render test
   * fails when it covers the background.
   *
   * It is the only surface behind all fifteen tabs, which is the app's entire
   * primary surface. Losing it presents as "the background works on detail
   * screens but not on Home", which points investigators at the screens.
   */
  it("opens the tab scene container, or the background is invisible on all fifteen tabs", () => {
    const navigator = flat(appNavigator.slice(appNavigator.indexOf("<Tabs.Navigator")));
    const declaration = navigator.match(/sceneContainerStyle=\{([A-Za-z_$][\w$]*|\{[^}]*\})\}/);
    expect(declaration).not.toBeNull();

    const value = declaration![1];
    // Either an inline literal or a constant — resolve a constant to its literal
    // so the assertion is on the colour, not on the spelling.
    const resolved = value.startsWith("{")
      ? value
      : flat(appNavigator).match(new RegExp(`const ${value} = (\\{[^}]*\\})`))?.[1];
    expect(resolved).toBeTruthy();
    expect(resolved!.replace(/'/g, '"')).toContain('backgroundColor: "transparent"');
  });

  /**
   * The library half of the test above. `sceneContainerStyle` is the *only*
   * override for that view, so this pins that the mechanism still exists: that
   * `Background` still paints `colors.background`, and that `BottomTabView`
   * still threads `sceneContainerStyle` into the style array where it can win.
   *
   * bottom-tabs v7 renames the prop to `sceneStyle`, at which point
   * `sceneContainerStyle` becomes a silently ignored prop and the background
   * disappears across the whole app again with nothing else failing. This test
   * is what turns that upgrade into a red build instead of a bug report.
   */
  it("still needs that override — the library view it defeats is unchanged", () => {
    const background = flat(read(MODULES, "@react-navigation", "elements", "lib", "module", "Background.js"));
    expect(background).toContain("backgroundColor: colors.background");

    const elementsScreen = flat(read(MODULES, "@react-navigation", "elements", "lib", "module", "Screen.js"));
    // `Background` receives `[styles.container, style]` — the caller's style is
    // last, which is why an override is possible at all.
    expect(elementsScreen).toContain("style: [styles.container, style]");

    const bottomTabView = flat(read(MODULES, "@react-navigation", "bottom-tabs", "lib", "module", "views", "BottomTabView.js"));
    expect(bottomTabView).toContain("sceneContainerStyle");
    expect(bottomTabView).toContain("style: sceneContainerStyle");
  });

  it("opens the stack content container across every stack route", () => {
    const flattened = flat(appNavigator);
    const declaration = flattened.match(/contentStyle:\s*([A-Za-z_$][\w$]*|\{[^}]*\})/);
    expect(declaration).not.toBeNull();

    const value = declaration![1];
    const resolved = value.startsWith("{")
      ? value
      : flattened.match(new RegExp(`const ${value} = (\\{[^}]*\\})`))?.[1];
    expect(resolved).toBeTruthy();
    expect(resolved!.replace(/'/g, '"')).toContain('backgroundColor: "transparent"');
    // The app's own fill, and the thing it must no longer be.
    expect(flattened).not.toContain("contentStyle: { backgroundColor: colors.background }");
  });
});

describe("the background is mounted once, at the root", () => {
  it("mounts exactly one PulseBackground", () => {
    expect(appEntry.match(/<PulseBackground/g)).toHaveLength(1);
    expect(appEntry).toContain('from "./src/components/PulseBackground"');
  });

  /**
   * Position is the whole point. Inside `SafeAreaProvider` so it fills the
   * window; before `ThemedNavigationShell` so React Native paints it first and
   * therefore underneath (siblings without `zIndex` paint in document order);
   * outside `NavigationContainer` so a navigation event can never remount it.
   */
  it("mounts it under everything and outside the navigation container", () => {
    const safeArea = appEntry.indexOf("<SafeAreaProvider");
    const providers = appEntry.indexOf("<SettingsProviders");
    const background = appEntry.indexOf("<PulseBackground");
    const shell = appEntry.indexOf("<ThemedNavigationShell");
    const container = appEntry.indexOf("<NavigationContainer");
    expect(safeArea).toBeGreaterThan(-1);
    expect(background).toBeGreaterThan(providers);
    expect(providers).toBeGreaterThan(safeArea);
    expect(background).toBeLessThan(shell);
    // `NavigationContainer` lives in `ThemedNavigationShell`, further down the
    // file — so a lower index here would mean the layer moved inside it.
    expect(background).toBeLessThan(container);
  });

  /**
   * The transition colour. It is not a rendering surface: its job is to be what
   * is visible in the gap between two cards mid-push, and to back the tab scenes
   * on the themes where `PulseBackground` renders nothing at all (White's
   * `galacticBackground` profile is disabled by design). Transparent here is
   * what produces a white or black flash, because what shows through is then
   * whatever the platform placed behind the navigator.
   */
  it("leaves the navigation theme's background an opaque colour", () => {
    const theme = appEntry.slice(appEntry.indexOf("const navigationTheme"), appEntry.indexOf("</NavigationContainer>"));
    expect(flat(theme)).toContain("background: theme.colors.background");
    expect(theme).not.toMatch(/background:\s*["']transparent["']/);
  });
});

describe("the shared shells defer to it", () => {
  it("makes both shells in components/Screen.tsx transparent", () => {
    const flattened = flat(screenShells);
    // `root` backs `Screen` and `LogiNexusScrollContainer`; `shell` backs
    // `LogiNexusScreenShell`. Between them they cover thirteen screens.
    expect(flattened).toMatch(/root: \{ flex: 1, backgroundColor: "transparent" \}/);
    expect(flattened).toMatch(/shell: \{ backgroundColor: "transparent", flex: 1 \}/);
    expect(flattened).not.toMatch(/(root|shell): \{[^}]*backgroundColor: colors\.background/);
  });

  /**
   * `SettingsShell` painted `theme.colors.background` inline rather than through
   * `colors`, so it never matched a `colors.background` grep — one line covering
   * all seventeen settings screens.
   */
  it("removes the inline fill from the settings shell", () => {
    expect(flat(settingsShell)).toContain('root: { flex: 1, backgroundColor: "transparent" }');
    expect(flat(settingsShell)).not.toContain("styles.root, { backgroundColor: theme.colors.background }");
  });
});

/**
 * The Home Feed.
 *
 * It gets its own block because it is the app's most-visited surface and
 * because it is the one that was wrong: `HomeScreen` dropped its own
 * `GalacticAtmosphere` backdrop in favour of the shared layer but kept the
 * opaque fill that backdrop had been drawn over, so the feed spent that whole
 * period looking like the background feature had never shipped. An opaque fill
 * and a missing background are the same screenshot.
 *
 * Nothing here can be caught by rendering the screen. Each assertion pins one
 * surface in the stack between `PulseBackground` and a post; any one of them
 * turning opaque again puts the flat page back.
 */
describe("the Home Feed defers to the shared background", () => {
  const homeScreen = read(SRC, "screens", "HomeScreen.tsx");
  const globalNavigation = read(SRC, "navigation", "GlobalNavigation.tsx");
  const sponsoredAdCard = read(SRC, "components", "SponsoredAdCard.tsx");
  const postCard = read(SRC, "components", "PostCard.tsx");

  /** The declaration the whole feed's appearance turns on. */
  it("keeps the feed root transparent", () => {
    expect(flat(homeScreen)).toMatch(/root: \{ backgroundColor: "transparent", flex: 1 \}/);
    expect(flat(homeScreen)).not.toContain("root: { backgroundColor: logiNexus.colors.home.backgroundDeepSpace");
  });

  /**
   * The list and its content container sit between the root and every row, so a
   * fill on either would undo the line above without touching it.
   */
  it("keeps the list and its content container unpainted", () => {
    expect(flat(homeScreen)).toMatch(/list: \{ backgroundColor: "transparent", flex: 1 \}/);
    const content = flat(homeScreen).match(/content: \{[^}]*\}/);
    expect(content).not.toBeNull();
    expect(content![0]).not.toContain("backgroundColor");
  });

  /**
   * Home is the only `mode="home"` caller of the global header, and that header
   * is inside the feed's own scroll surface rather than above it. `headerShell`
   * keeps its near-opaque plate for every other screen; this override is what
   * stops Home growing a black band across the top with a seam under the
   * wordmark.
   */
  it("keeps the Home header shell transparent while the standard one stays opaque", () => {
    expect(flat(globalNavigation)).toMatch(/headerShellHome: \{ backgroundColor: "transparent"/);
    expect(flat(globalNavigation)).toContain('headerShell: { backgroundColor: "rgba(3, 9, 18, 0.96)"');
    expect(homeScreen).toContain('mode="home"');
  });

  /**
   * Rows. `PostCard` carries no fill at all — the post sits directly on the
   * field — and the sponsored card, the only other row type, is translucent so
   * it does not punch a rectangle through the field every few posts.
   */
  it("keeps both feed row types off an opaque fill", () => {
    const card = flat(postCard).match(/card: \{[^}]*\}/);
    expect(card).not.toBeNull();
    expect(card![0]).not.toContain("backgroundColor");

    expect(flat(sponsoredAdCard)).toContain("card: { backgroundColor: logiNexus.colors.home.surfaceGlass");
    expect(flat(sponsoredAdCard)).not.toContain("card: { backgroundColor: colors.surface");
  });

  /**
   * The hairline. It is the feed's separator, and it belongs to the field's
   * family rather than the pale cyan it used when the feed had a flat fill of
   * its own — a few hundred separators in a hue the background does not contain
   * is what reads as two designs stitched together.
   */
  it("keeps the feed hairline in the background's own family", () => {
    const tokens = flat(read(SRC, "theme", "logiNexus.ts"));
    expect(tokens).toContain('borderSubtle: "rgba(100, 160, 255, 0.28)"');
    expect(tokens).not.toContain('borderSubtle: "rgba(121, 210, 255, 0.18)"');
  });

  /**
   * Architecture. The mission that produced this block called for exactly one
   * background environment behind the feed rather than one per row, and the
   * feed is virtualized — a backdrop inside `renderItem` would be instantiated,
   * animated and torn down per post. `PulseBackground` is mounted once at the
   * app root, so the guard here is that the feed never imports it at all.
   */
  it("does not mount a second background inside the feed", () => {
    // Rendered and imported, not merely named: these files are allowed to
    // mention the component in a comment explaining why they defer to it.
    for (const source of [homeScreen, postCard, sponsoredAdCard]) {
      expect(source).not.toContain("<PulseBackground");
      expect(source).not.toMatch(/import .*PulseBackground.* from/);
    }
  });

  /**
   * The hero panel's `GalacticAtmosphere` is deliberate and stays: it is
   * clipped inside one panel as a design element. What must not come back is a
   * second full-screen backdrop — the thing that hid the shared layer here in
   * the first place.
   */
  it("keeps the hero atmosphere clipped and does not restore a full-screen one", () => {
    expect(flat(homeScreen)).toContain('heroAtmosphere: { ...StyleSheet.absoluteFillObject');
    expect(homeScreen).not.toContain("homeAtmosphereRoot");
    expect(homeScreen).not.toContain("homeNebula");
  });
});

/**
 * The exclusion list.
 *
 * Making the containers transparent means every one of these now depends on its
 * *own* fill to stay opaque. They are immersive surfaces where the content is
 * the background — a camera preview, a video, a remote track — or fixed-palette
 * commerce screens that do not follow the app theme at all. An ambient layer
 * behind them is either invisible or actively wrong, and a screen on this list
 * losing its fill would show the space field bleeding through a video.
 *
 * Each entry pins the specific declaration that keeps it opaque, so a future
 * sweep converting screen roots to `transparent` cannot quietly take one of
 * these with it.
 */
describe("immersive and fixed-palette surfaces keep their own opaque fill", () => {
  const OPAQUE: Record<string, string> = {
    "screens/CameraStudioScreen.tsx": 'backgroundColor: "#02050b"',
    "screens/ReelsScreen.tsx": 'backgroundColor: "#02050b"',
    "screens/LiveHostSessionScreen.tsx": 'backgroundColor: "#02040a"',
    // `LiveStudioScreen` used to be here, pinned to the `#02050b` behind its
    // camera preview. The preview moved out to the host session screen when the
    // studio became a management dashboard, so there is no longer an immersive
    // surface on it to keep opaque — it is an ordinary themed screen now and
    // belongs to the general rule, not to this exclusion list.
    "screens/LiveScreen.tsx": 'backgroundColor: "#02050b"',
    "screens/ReplayViewerScreen.tsx": 'backgroundColor: "#02040a"',
    "screens/CallScreen.tsx": 'backgroundColor: "#030812"',
    "calls/IncomingCallLayer.tsx": 'backgroundColor: "#06090f"',
    "components/NativeMediaViewer.tsx": 'backgroundColor: "#02050b"',
    "components/StatusViewerCard.tsx": 'backgroundColor: "#02050b"',
    // Presented as a `fullScreenModal`; its overlays assume a dark page.
    "screens/ContentPreviewScreen.tsx": "container: { flex: 1, backgroundColor: colors.background }",
    "live/PreLiveConfigurationSheet.tsx": "backgroundColor: colors.background",
    // Not darkness but the opposite: a QR code is unreadable without its white
    // quiet zone, so this panel must stay opaque white over anything.
    "screens/PulseIdentityScreen.tsx": 'backgroundColor: "#FFFFFF"'
  };

  it.each(Object.entries(OPAQUE))("%s still paints its own background", (file, declaration) => {
    expect(flat(read(SRC, ...file.split("/")))).toContain(declaration);
  });

  /**
   * The Business OS / commerce family is deliberately light and fixed: a
   * `#EAEDED` page from `storeLight` and its re-exports, ignoring the app theme.
   * A dark space layer under them would never show, and one of them turning
   * transparent would simply look broken.
   */
  const LIGHT_PAGE = [
    "ActivityScreen",
    "AdsManagerScreen",
    "AdsSubPageScreen",
    "BusinessHubScreen",
    "BusinessOsAdvertisingScreen",
    "BusinessOsInsightsScreen",
    "BusinessOsPaymentsScreen",
    "CommerceInboxScreen",
    "EventsManagerScreen",
    "MarketplaceCartScreen",
    "MarketplaceManagerScreen",
    "OrdersManagerScreen",
    "StoreDashboardScreen"
  ];

  it.each(LIGHT_PAGE)("%s keeps its fixed light page colour", (screen) => {
    expect(read(SRC, "screens", `${screen}.tsx`)).toMatch(/backgroundColor:\s*\w*[Ll]ight\.bg\.page/);
  });

  it("keeps the fixed light page colour itself opaque", () => {
    expect(flat(read(SRC, "theme", "storeLight.ts"))).toContain('page: "#EAEDED"');
  });

  /** The two screens in that family on the dark `businessLive` palette. */
  it.each(["BusinessProfileScreen", "BusinessBuyerPreviewScreen"])("%s keeps its own palette fill", (screen) => {
    expect(read(SRC, "screens", `${screen}.tsx`)).toMatch(/backgroundColor:\s*palette\.background/);
  });
});
