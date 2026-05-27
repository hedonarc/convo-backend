from .base import *
from .base import SIMPLE_JWT, env

DEBUG = False

# Strict secrets - no defaults
SECRET_KEY = env("SECRET_KEY")

DATABASES = {"default": env.db("DATABASE_URL")}

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

# Strict — must be set in env. Base defines a localhost default that's
# inappropriate for prod, so override here without a fallback.
BACKEND_URL = env.str("BACKEND_URL")
FRONTEND_URL = env.str("FRONTEND_URL")

# Strict security settings
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "None"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "None"
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS")
SECURE_HSTS_SECONDS = 31536000  # 1 year

# Force secure + strict cookies in production
SIMPLE_JWT = {
    **SIMPLE_JWT,
    "AUTH_COOKIE_SECURE": True,
    "AUTH_COOKIE_SAMESITE": "None",
}

# Use Redis URL from environment for WebSockets
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [env.str("REDIS_URL")],
        },
    },
}

# WhiteNoise — compressed + content-hashed static files for production.
# `CompressedManifestStaticFilesStorage` rewrites filenames to include a
# content hash (e.g. `base.abc123.css`) so we can serve everything with
# `Cache-Control: max-age=1y, immutable`. Requires `collectstatic` to have
# run before boot — Render's buildCommand handles that.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Email Configuration (Brevo via Anymail, over HTTPS)
EMAIL_BACKEND = "anymail.backends.brevo.EmailBackend"
ANYMAIL = {
    "BREVO_API_KEY": env.str("BREVO_API_KEY"),
}
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL")
