(function () {
  "use strict";

  const STORAGE_KEY = "pulse.preferred.language";
  const builtIn = new Set(["en", "es", "fr", "ht", "pt", "de", "it", "ar"]);
  const languagePattern = /^[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,3}$/;
  const rtlLanguages = new Set(["ar", "dv", "fa", "he", "ks", "ku", "ps", "sd", "ug", "ur", "yi"]);
  const missingLogged = new Set();

  const messages = {
    en: {
      // PulseDrop commerce attachment. The overlay ships these keys beside an
      // English fallback and the native card renders through them; the web card
      // read the fallback alone, so a member on a translated post met an English
      // badge and an English button. Copied verbatim from the app's catalogue
      // (mobile-native/src/i18n/catalogs/<lang>/extended.json) so the two
      // surfaces say the same words, not two translations of one idea.
      "commerce:pulsedrop.label.trending": "Trending",
      "commerce:pulsedrop.label.newDrop": "New drop",
      "commerce:pulsedrop.label.popular": "Popular",
      "commerce:pulsedrop.label.topPick": "Top pick",
      "commerce:pulsedrop.label.discovery": "Discover",
      "commerce:pulsedrop.cta.viewProduct": "View product",
      "commerce:pulsedrop.seller.visitStore": "Visit store",
      "commerce:pulsedrop.availability.unavailable": "No longer available",
      "commerce:pulsedrop.availability.notPriced": "Not priced yet",
      "commerce:marketplace.outOfStock": "Out of stock",
      "commerce:marketplace.statusRemoved": "Removed",
      "settings.saved": "Settings saved.",
      "language.saved": "Language preference saved.",
      "auth.login_required": "Login required.",
      "security.updated": "Security updated.",
      "notifications.empty": "No notifications yet.",
      "welcome.first_login.title": "Welcome to PulseSoc, Explorer 🌌",
      "welcome.first_login.body": "Your journey begins now.",
      "welcome.first_login.subtext": "The galaxy is waiting for your first signal.",
      "welcome.first_login.cta": "Enter the Galaxy 🚀",
      "welcome.welcome_back.title": "Welcome back to the Galaxy, {name} 👋",
      "welcome.welcome_back.body": "The galaxy is better with you in it.",
      "welcome.welcome_back.subtext": "New adventures. New opportunities. Let's build something extraordinary. 🚀",
      "welcome.welcome_back.cta": "Let's Go! ✨",
      "welcome.session_return.title": "Mission resumed, {name} 🚀",
      "welcome.session_return.body": "Your universe kept moving while you were away.",
      "welcome.session_return.subtext": "Let's continue.",
      "welcome.session_return.cta": "Resume Mission",
      "welcome.version_update.title": "PulseSoc just leveled up ⚡",
      "welcome.version_update.body": "New systems are online.",
      "welcome.version_update.subtext": "Explore what's new in the galaxy.",
      "welcome.version_update.cta": "Explore PulseSoc",
      "welcome.manual.title": "You belong here, {name} ✨",
      "welcome.manual.body": "Let's make today legendary.",
      "welcome.manual.subtext": "Your PulseSoc galaxy is ready.",
      "welcome.manual.cta": "Enter the Galaxy",
      "pulse.call.default_title": "PulseSoc",
      "pulse.call.audio_meta": "Voice Connection",
      "pulse.call.video_meta": "Video Connection",
      "pulse.call.outgoing": "Pulsing...",
      "pulse.call.searching": "Searching for secure connection...",
      "pulse.call.waiting": "Waiting for response...",
      "pulse.call.preparing_voice": "Preparing communication channel...",
      "pulse.call.preparing_video": "Synchronizing video channel...",
      "pulse.call.accepted": "Pulse Accepted",
      "pulse.call.synchronizing": "Synchronizing...",
      "pulse.call.establishing": "Establishing Secure Connection...",
      "pulse.call.connected": "Pulse Connected",
      "pulse.call.excellent": "Excellent Connection",
      "pulse.call.restoring": "Restoring Pulse...",
      "pulse.call.restored": "Pulse Restored",
      "pulse.call.lost": "Pulse Lost",
      "pulse.call.interrupted": "Pulse Interrupted",
      "pulse.call.declined": "Pulse Declined",
      "pulse.call.missed": "Missed Pulse",
      "pulse.call.ended": "Pulse Ended",
      "pulse.call.busy": "Pulse Busy",
      "pulse.call.remote_camera_off": "Camera Off",
      "pulse.call.local_camera_off": "Camera off",
      "pulse.call.waiting_video": "Waiting for video...",
      "pulse.call.incoming_suffix": "is Pulsing You...",
      "pulse.call.incoming_voice": "Voice Connection",
      "pulse.call.incoming_video": "Video Connection",
      "pulse.call.recipient_notified": "Recipient notified.",
      "pulse.call.push_unavailable": "Waiting for recipient. Push delivery unavailable.",
      "pulse.call.notification_failed": "Pulse started, but recipient could not be notified.",
      "pulse.call.speaker_device": "Audio output follows this device.",
      "pulse.call.speaker_changed": "Audio output switched.",
      "pulse.call.mic_muted": "Microphone muted.",
      "pulse.call.mic_on": "Microphone on.",
      "pulse.call.camera_off": "Camera off.",
      "pulse.call.camera_on": "Camera on.",
      "pulse.call.camera_switched": "Camera switched.",
      "pulse.call.camera_switch_unsupported": "Camera switching is not supported by this browser session.",
      "pulse.call.background_limited": "Device background mode may pause microphone. PulseSoc will restore it when possible.",
      "pulse.call.no_connection": "Unable to establish a secure connection.",
      "pulse.call.try_again": "Try Again"
    },
    es: {
      // PulseDrop commerce attachment. The overlay ships these keys beside an
      // English fallback and the native card renders through them; the web card
      // read the fallback alone, so a member on a translated post met an English
      // badge and an English button. Copied verbatim from the app's catalogue
      // (mobile-native/src/i18n/catalogs/<lang>/extended.json) so the two
      // surfaces say the same words, not two translations of one idea.
      "commerce:pulsedrop.label.trending": "Tendencias",
      "commerce:pulsedrop.label.newDrop": "Novedad",
      "commerce:pulsedrop.label.popular": "Popular",
      "commerce:pulsedrop.label.topPick": "Nuestra selección",
      "commerce:pulsedrop.label.discovery": "Descubre",
      "commerce:pulsedrop.cta.viewProduct": "Ver producto",
      "commerce:pulsedrop.seller.visitStore": "Ver tienda",
      "commerce:pulsedrop.availability.unavailable": "Ya no está disponible",
      "commerce:pulsedrop.availability.notPriced": "Sin precio aún",
      "commerce:marketplace.outOfStock": "Sin existencias",
      "commerce:marketplace.statusRemoved": "Retirado",
      "settings.saved": "Configuracion guardada.",
      "language.saved": "Idioma guardado.",
      "auth.login_required": "Inicia sesion.",
      "security.updated": "Seguridad actualizada.",
      "notifications.empty": "No hay notificaciones.",
      "welcome.first_login.title": "Bienvenido a PulseSoc, Explorador 🌌",
      "welcome.first_login.body": "Tu viaje empieza ahora.",
      "welcome.first_login.subtext": "La galaxia espera tu primera senal.",
      "welcome.first_login.cta": "Entrar a la galaxia 🚀",
      "welcome.welcome_back.title": "Bienvenido de nuevo a la galaxia, {name} 👋",
      "welcome.welcome_back.body": "La galaxia es mejor contigo.",
      "welcome.welcome_back.subtext": "Nuevas aventuras. Nuevas oportunidades. Construyamos algo extraordinario. 🚀",
      "welcome.welcome_back.cta": "Vamos! ✨",
      "welcome.session_return.title": "Mision reanudada, {name} 🚀",
      "welcome.session_return.body": "Tu universo siguio avanzando mientras no estabas.",
      "welcome.session_return.subtext": "Continuemos.",
      "welcome.session_return.cta": "Reanudar mision",
      "welcome.version_update.title": "PulseSoc subio de nivel ⚡",
      "welcome.version_update.body": "Nuevos sistemas estan en linea.",
      "welcome.version_update.subtext": "Explora lo nuevo en la galaxia.",
      "welcome.version_update.cta": "Explorar PulseSoc",
      "welcome.manual.title": "Tu perteneces aqui, {name} ✨",
      "welcome.manual.body": "Hagamos que hoy sea legendario.",
      "welcome.manual.subtext": "Tu galaxia PulseSoc esta lista.",
      "welcome.manual.cta": "Entrar a la galaxia"
    },
    fr: {
      // PulseDrop commerce attachment. The overlay ships these keys beside an
      // English fallback and the native card renders through them; the web card
      // read the fallback alone, so a member on a translated post met an English
      // badge and an English button. Copied verbatim from the app's catalogue
      // (mobile-native/src/i18n/catalogs/<lang>/extended.json) so the two
      // surfaces say the same words, not two translations of one idea.
      "commerce:pulsedrop.label.trending": "Tendances",
      "commerce:pulsedrop.label.newDrop": "Nouveauté",
      "commerce:pulsedrop.label.popular": "Populaire",
      "commerce:pulsedrop.label.topPick": "Notre sélection",
      "commerce:pulsedrop.label.discovery": "Découvrir",
      "commerce:pulsedrop.cta.viewProduct": "Voir le produit",
      "commerce:pulsedrop.seller.visitStore": "Voir la boutique",
      "commerce:pulsedrop.availability.unavailable": "Plus disponible",
      "commerce:pulsedrop.availability.notPriced": "Prix non défini",
      "commerce:marketplace.outOfStock": "Rupture de stock",
      "commerce:marketplace.statusRemoved": "Retirée",
      "settings.saved": "Parametres enregistres.",
      "language.saved": "Langue enregistree.",
      "auth.login_required": "Connexion requise.",
      "security.updated": "Securite mise a jour.",
      "notifications.empty": "Aucune notification.",
      "welcome.first_login.title": "Bienvenue sur PulseSoc, Explorateur 🌌",
      "welcome.first_login.body": "Votre voyage commence maintenant.",
      "welcome.first_login.subtext": "La galaxie attend votre premier signal.",
      "welcome.first_login.cta": "Entrer dans la galaxie 🚀",
      "welcome.welcome_back.title": "Bon retour dans la galaxie, {name} 👋",
      "welcome.welcome_back.body": "La galaxie est meilleure avec vous.",
      "welcome.welcome_back.subtext": "Nouvelles aventures. Nouvelles opportunites. Construisons quelque chose d'extraordinaire. 🚀",
      "welcome.welcome_back.cta": "Allons-y! ✨",
      "welcome.session_return.title": "Mission reprise, {name} 🚀",
      "welcome.session_return.body": "Votre univers a continue d'avancer pendant votre absence.",
      "welcome.session_return.subtext": "Continuons.",
      "welcome.session_return.cta": "Reprendre la mission",
      "welcome.version_update.title": "PulseSoc vient de monter de niveau ⚡",
      "welcome.version_update.body": "De nouveaux systemes sont en ligne.",
      "welcome.version_update.subtext": "Explorez les nouveautes de la galaxie.",
      "welcome.version_update.cta": "Explorer PulseSoc",
      "welcome.manual.title": "Vous avez votre place ici, {name} ✨",
      "welcome.manual.body": "Rendons cette journee legendaire.",
      "welcome.manual.subtext": "Votre galaxie PulseSoc est prete.",
      "welcome.manual.cta": "Entrer dans la galaxie"
    },
    ht: {
      // PulseDrop commerce attachment. The overlay ships these keys beside an
      // English fallback and the native card renders through them; the web card
      // read the fallback alone, so a member on a translated post met an English
      // badge and an English button. Copied verbatim from the app's catalogue
      // (mobile-native/src/i18n/catalogs/<lang>/extended.json) so the two
      // surfaces say the same words, not two translations of one idea.
      "commerce:pulsedrop.label.trending": "Sa k cho",
      "commerce:pulsedrop.label.newDrop": "Nouvo rive",
      "commerce:pulsedrop.label.popular": "Popilè",
      "commerce:pulsedrop.label.topPick": "Chwa nou",
      "commerce:pulsedrop.label.discovery": "Dekouvri",
      "commerce:pulsedrop.cta.viewProduct": "Gade pwodwi a",
      "commerce:pulsedrop.seller.visitStore": "Gade boutik la",
      "commerce:pulsedrop.availability.unavailable": "Li pa disponib ankò",
      "commerce:pulsedrop.availability.notPriced": "Pa gen pri ankò",
      "commerce:marketplace.outOfStock": "Li fini",
      "commerce:marketplace.statusRemoved": "Retire",
      "settings.saved": "Paramet yo anrejistre.",
      "language.saved": "Lang lan anrejistre.",
      "auth.login_required": "Ou dwe konekte.",
      "security.updated": "Sekirite mete ajou.",
      "notifications.empty": "Pa gen notifikasyon.",
      "welcome.first_login.title": "Byenveni sou PulseSoc, Eksplorate 🌌",
      "welcome.first_login.body": "Vwayaj ou komanse kounye a.",
      "welcome.first_login.subtext": "Galaksi a ap tann premye signal ou.",
      "welcome.first_login.cta": "Antre nan galaksi a 🚀",
      "welcome.welcome_back.title": "Byen retounen nan galaksi a, {name} 👋",
      "welcome.welcome_back.body": "Galaksi a pi bon avek ou ladan.",
      "welcome.welcome_back.subtext": "Nouvo avanti. Nouvo opotinite. Ann bati yon bagay ekstraodine. 🚀",
      "welcome.welcome_back.cta": "Ann ale! ✨",
      "welcome.session_return.title": "Misyon an rekomanse, {name} 🚀",
      "welcome.session_return.body": "Linive ou te kontinye deplase pandan ou pa t la.",
      "welcome.session_return.subtext": "Ann kontinye.",
      "welcome.session_return.cta": "Kontinye misyon an",
      "welcome.version_update.title": "PulseSoc monte nivo ⚡",
      "welcome.version_update.body": "Nouvo sistem yo sou liy.",
      "welcome.version_update.subtext": "Eksplore sa ki nouvo nan galaksi a.",
      "welcome.version_update.cta": "Eksplore PulseSoc",
      "welcome.manual.title": "Ou gen plas ou isit la, {name} ✨",
      "welcome.manual.body": "Ann fe jounen an vin lejande.",
      "welcome.manual.subtext": "Galaksi PulseSoc ou a pare.",
      "welcome.manual.cta": "Antre nan galaksi a"
    }
  };

  function normalize(language) {
    const raw = String(language || "").trim().toLowerCase().replace("_", "-").slice(0, 16);
    if (raw === "auto" || raw === "browser" || raw === "system") {
      return normalize(navigator.languages?.[0] || navigator.language || document.documentElement.lang || "en");
    }
    if (builtIn.has(raw)) return raw;
    const base = raw.split("-", 1)[0];
    if (builtIn.has(base)) return raw.includes("-") && languagePattern.test(raw) ? raw : base;
    return languagePattern.test(raw) ? raw : "en";
  }

  function readCachedLanguage() {
    try {
      return normalize(localStorage.getItem(STORAGE_KEY) || navigator.languages?.[0] || navigator.language || document.documentElement.lang || "en");
    } catch (error) {
      return normalize(navigator.language || document.documentElement.lang || "en");
    }
  }

  function cacheLanguage(language) {
    try {
      localStorage.setItem(STORAGE_KEY, normalize(language));
    } catch (error) {}
  }

  function applyLanguage(language) {
    const normalized = normalize(language);
    const base = normalized.split("-", 1)[0];
    document.documentElement.lang = normalized;
    document.documentElement.dir = rtlLanguages.has(base) ? "rtl" : "ltr";
    document.documentElement.dataset.preferredLanguage = normalized;
    document.documentElement.dataset.translationFallback = messages[normalized] ? "native" : messages[base] ? "base" : "english";
    // Swept before the event, not after: a listener that reads the DOM it just
    // translated -- the commerce card rebuilds an `aria-label` out of its own
    // translated chips -- would otherwise read the previous language's text.
    translateMarkedNodes();
    document.dispatchEvent(new CustomEvent("PulseLanguageChanged", { detail: { language: normalized } }));
    return normalized;
  }

  function logMissing(key, language) {
    const marker = `${language}:${key}`;
    if (missingLogged.has(marker)) return;
    missingLogged.add(marker);
    fetch("/api/i18n/missing", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, language })
    }).catch(() => undefined);
  }

  function t(key, fallback) {
    const language = normalize(document.documentElement.dataset.preferredLanguage || readCachedLanguage());
    const base = language.split("-", 1)[0];
    const value = messages[language]?.[key] || messages[base]?.[key] || messages.en?.[key];
    if (!messages[language]?.[key] && !messages[base]?.[key] && language !== "en") logMissing(key, language);
    return value || fallback || key;
  }

  function translateMarkedNodes(root) {
    const scope = root || document;
    scope.querySelectorAll?.("[data-i18n]").forEach((node) => {
      const key = node.getAttribute("data-i18n");
      if (!key) return;
      node.textContent = t(key, node.textContent || key);
    });
    scope.querySelectorAll?.("[data-i18n-placeholder]").forEach((node) => {
      const key = node.getAttribute("data-i18n-placeholder");
      if (!key) return;
      node.setAttribute("placeholder", t(key, node.getAttribute("placeholder") || key));
    });
  }

  function attachLanguageToForms() {
    document.querySelectorAll("form").forEach((form) => {
      if (form.dataset.pulseLanguageBound === "1") return;
      form.dataset.pulseLanguageBound = "1";
      form.addEventListener("submit", () => {
        let field = form.querySelector('input[name="preferred_language"]');
        if (!field) {
          field = document.createElement("input");
          field.type = "hidden";
          field.name = "preferred_language";
          form.appendChild(field);
        }
        field.value = normalize(document.documentElement.dataset.preferredLanguage || readCachedLanguage());
      });
    });
  }

  async function loadServerLanguage() {
    try {
      const response = await fetch("/api/account/language", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return readCachedLanguage();
      const data = await response.json();
      const language = normalize(data.preferred_language || data.language || readCachedLanguage());
      cacheLanguage(language);
      applyLanguage(language);
      return language;
    } catch (error) {
      return readCachedLanguage();
    }
  }

  async function setLanguage(language, options) {
    const normalized = applyLanguage(language);
    cacheLanguage(normalized);
    if (options?.skipServer) return normalized;
    const response = await fetch("/api/account/language", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferred_language: normalized })
    });
    if (!response.ok) throw new Error("Language preference could not be saved.");
    return normalized;
  }

  window.PulseI18n = {
    getLanguage: () => normalize(document.documentElement.dataset.preferredLanguage || readCachedLanguage()),
    setLanguage,
    t,
    // Exported for markup that arrives after the DOMContentLoaded sweep. Feed
    // and reel cards are appended on scroll, so without a way to translate a
    // subtree they stay in the server's fallback language for the session.
    translateMarkedNodes,
    applyLanguage,
    loadServerLanguage,
    supportsLanguage: (language) => languagePattern.test(String(language || "").trim().toLowerCase().replace("_", "-").slice(0, 16)),
    builtInLanguages: () => Array.from(builtIn)
  };

  applyLanguage(readCachedLanguage());
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      translateMarkedNodes();
      attachLanguageToForms();
      loadServerLanguage();
    }, { once: true });
  } else {
    translateMarkedNodes();
    attachLanguageToForms();
    loadServerLanguage();
  }
})();
