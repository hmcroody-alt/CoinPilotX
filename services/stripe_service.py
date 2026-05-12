import os


def configuration_status():
    return {
        "secret_key_loaded": bool(os.getenv("STRIPE_SECRET_KEY")),
        "publishable_key_loaded": bool(os.getenv("STRIPE_PUBLISHABLE_KEY")),
        "webhook_secret_loaded": bool(os.getenv("STRIPE_WEBHOOK_SECRET")),
        "price_id_loaded": bool(os.getenv("STRIPE_PRICE_ID")),
        "app_base_url_loaded": bool(os.getenv("APP_BASE_URL") or os.getenv("BASE_URL") or os.getenv("DOMAIN")),
    }
