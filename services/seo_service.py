"""SEO route registry helpers."""


PRIVATE_PREFIXES = (
    "/account",
    "/dashboard",
    "/admin",
    "/api",
    "/debug",
    "/login",
    "/logout",
    "/signup",
    "/upgrade",
    "/forgot-username",
    "/forgot-password",
    "/reset-password",
    "/verify-email",
    "/create-checkout-session",
    "/checkout",
    "/stripe-webhook",
)


def is_private_path(path):
    path = path or ""
    return path.startswith(PRIVATE_PREFIXES)


def robots_policy(path):
    return "noindex,nofollow" if is_private_path(path) else "index,follow"
