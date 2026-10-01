/**
 * PulseSoc Native localization.
 *
 * Everything a screen needs comes from here:
 *
 *   import { useTranslation, useFormatters, useDirection } from "../i18n";
 *
 *   const { t } = useTranslation();
 *   const fmt = useFormatters();
 *   const dir = useDirection();
 *
 * The deeper modules stay importable for tooling and tests, but application
 * code should not need them.
 */

/* Provider and React bindings. */
export {
  I18nProvider,
  useI18n,
  useMissingTranslationKeys,
  useTranslation
} from "./I18nContext";
export type { I18nContextValue } from "./I18nContext";

export { useDirection, useFormatters } from "./hooks";
export type { DirectionHelpers, Formatters } from "./hooks";

/* The language registry — powers the picker and the detection chain. */
export {
  DEFAULT_LOCALE,
  SUPPORTED_LOCALES,
  getLocaleDefinition,
  isRtlLanguage,
  isSupportedLocale,
  normalizeTag,
  resolveSupportedLocale,
  searchLocales,
  toIntlLocale
} from "./locales";
export type { LocaleDefinition, TextDirection } from "./locales";

/* Detection, exposed for the settings screen's "detected from your device" copy. */
export { detectLocale, getDeviceLanguages, getDeviceLocale, getDeviceRegion } from "./detect";
export type { DetectionResult, DetectionSource } from "./detect";

/* Non-React translation, for use outside the component tree (API error mapping,
 * push-notification payloads, background tasks). */
export {
  getActiveIntlLocale,
  getActiveTranslationLocale,
  getMissingKeys,
  hasTranslation,
  translate
} from "./engine";
export type { TranslateOptions } from "./engine";

/* Formatters, likewise usable outside React. */
export {
  activeFormattingLocale,
  formatAccessibleDateTime,
  formatCount,
  formatCurrencyAmount,
  formatDate,
  formatDateTime,
  formatDay,
  formatDistance,
  formatDuration,
  formatFileSize,
  formatList,
  formatMeasurement,
  formatNumber,
  formatOrdinal,
  formatPercent,
  formatRange,
  formatRelative,
  formatRelativeLong,
  formatScheduled,
  formatTemperature,
  formatTime,
  formatWeight,
  monthNames,
  regionDisplayName,
  timeZoneLabel,
  usesImperialUnits,
  weekdayNames
} from "./format";

/* Direction primitives, for StyleSheet definitions built outside a component. */
export {
  borderRadiusLogical,
  directionSign,
  endEdge,
  getLayoutDirection,
  isLayoutRtl,
  isolateBidi,
  marginHorizontal,
  mirrorIconName,
  mirrorTransform,
  paddingHorizontal,
  positionEnd,
  positionStart,
  row,
  startEdge,
  subscribeToDirection,
  textAlign,
  writingDirectionStyle
} from "./rtl";

/* Catalog metadata, for the coverage panel and the version-migration check. */
export { CATALOG_NAMESPACES, getCatalogVersion } from "./catalogs";
export type { CatalogNamespace } from "./catalogs";

/* Persistence, for sign-out cleanup. */
export { clearStoredLanguage } from "./store";
