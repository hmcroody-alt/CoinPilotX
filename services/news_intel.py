def fallback_news(country=None):
    scope = f" for {country}" if country else ""
    return {
        "items": [],
        "warning": f"Live news source is not connected yet{scope}. Showing educational guidance instead.",
    }
