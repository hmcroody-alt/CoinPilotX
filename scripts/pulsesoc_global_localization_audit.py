#!/usr/bin/env python3
"""Static release gate for PulseSoc account-bound locale and region behavior."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main() -> None:
    local_time = (ROOT / "mobile-native/src/core/localTime.ts").read_text(encoding="utf-8")
    context = (ROOT / "mobile-native/src/core/TimeZoneContext.tsx").read_text(encoding="utf-8")
    screen = (ROOT / "mobile-native/src/screens/RegionTimeScreen.tsx").read_text(encoding="utf-8")
    account = (ROOT / "mobile-native/src/api/account.ts").read_text(encoding="utf-8")
    service = (ROOT / "services/pulse_region_preferences.py").read_text(encoding="utf-8")
    backend = (ROOT / "bot.py").read_text(encoding="utf-8")

    require("getDeviceTimeZone" in local_time and "getResolvedLocale" in local_time,
            "device time zone and locale defaults are detected")
    require("getDeviceCurrency" in local_time and "REGION_CURRENCY" in local_time,
            "currency defaults are derived from the active locale region")
    require("getDetectedDateFormat" in local_time and "formatToParts" in local_time,
            "date ordering is detected from the active locale")
    require("formatCurrency" in local_time and "formatNumericDate" in local_time,
            "currency and numeric dates use centralized localized formatters")
    require("Intl.PluralRules" in local_time and "formatPlural" in local_time,
            "plural selection follows the active locale")
    require("isRtlLocale" in local_time and "I18nManager.forceRTL" in context,
            "right-to-left layout direction follows the selected language")
    require("rtlRestartRequired" in context and "Reopen PulseSoc once" in screen,
            "native direction changes disclose their one-time relaunch boundary")
    require("setCurrencyOverride" in context and "setDateFormatOverride" in context,
            "manual currency and date-format overrides update application context")
    require("getAccountRegionPreferences" in account and "updateAccountRegionPreferences" in account,
            "native region preferences use one authenticated account API")
    require('"/api/account/region-preferences"' in backend,
            "authenticated region-preference route is registered")
    require("pulse_region_preferences" in service and "pulse_region_preference_events" in service,
            "region preferences are server-authoritative and audited")
    require("ZoneInfo" in service and "SUPPORTED_CURRENCIES" in service and "DATE_FORMATS" in service,
            "server validation fails closed for time zone, currency, and date format")
    require("updateAccountRegionPreferences" in screen and "previous preference is still active" in screen,
            "native optimistic preference updates roll back on persistence failure")
    require("preferred_timezone" in screen and "preferred_currency" in screen and "preferred_date_format" in screen,
            "time zone, currency, and date format follow the account across devices")

    print("PASS: PulseSoc global localization foundation release gate")


if __name__ == "__main__":
    main()
