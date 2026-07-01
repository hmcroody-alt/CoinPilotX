(function () {
  "use strict";

  const STORAGE_KEY = "pulse.preferred.language";
  const supported = new Set(["en", "es", "fr", "ht", "pt", "de", "it", "ar"]);
  const missingLogged = new Set();

  const messages = {
    en: {
      "settings.saved": "Settings saved.",
      "language.saved": "Language preference saved.",
      "auth.login_required": "Login required.",
      "security.updated": "Security updated.",
      "notifications.empty": "No notifications yet."
    },
    es: {
      "settings.saved": "Configuracion guardada.",
      "language.saved": "Idioma guardado.",
      "auth.login_required": "Inicia sesion.",
      "security.updated": "Seguridad actualizada.",
      "notifications.empty": "No hay notificaciones."
    },
    fr: {
      "settings.saved": "Parametres enregistres.",
      "language.saved": "Langue enregistree.",
      "auth.login_required": "Connexion requise.",
      "security.updated": "Securite mise a jour.",
      "notifications.empty": "Aucune notification."
    },
    ht: {
      "settings.saved": "Paramet yo anrejistre.",
      "language.saved": "Lang lan anrejistre.",
      "auth.login_required": "Ou dwe konekte.",
      "security.updated": "Sekirite mete ajou.",
      "notifications.empty": "Pa gen notifikasyon."
    }
  };

  function normalize(language) {
    const raw = String(language || "").trim().toLowerCase().replace("_", "-").slice(0, 16);
    if (supported.has(raw)) return raw;
    const base = raw.split("-", 1)[0];
    return supported.has(base) ? base : "en";
  }

  function readCachedLanguage() {
    try {
      return normalize(localStorage.getItem(STORAGE_KEY) || document.documentElement.lang || "en");
    } catch (error) {
      return normalize(document.documentElement.lang || "en");
    }
  }

  function cacheLanguage(language) {
    try {
      localStorage.setItem(STORAGE_KEY, normalize(language));
    } catch (error) {}
  }

  function applyLanguage(language) {
    const normalized = normalize(language);
    document.documentElement.lang = normalized;
    document.documentElement.dataset.preferredLanguage = normalized;
    document.dispatchEvent(new CustomEvent("PulseLanguageChanged", { detail: { language: normalized } }));
    translateMarkedNodes();
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
    const value = messages[language]?.[key] || messages.en?.[key];
    if (!messages[language]?.[key] && language !== "en") logMissing(key, language);
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
    applyLanguage,
    loadServerLanguage
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
