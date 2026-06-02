from .base import *
from .base import BASE_DIR, INSTALLED_APPS, MIDDLEWARE, REST_FRAMEWORK, SIMPLE_JWT, env

DEBUG = True

SECRET_KEY = env("SECRET_KEY", default="django-insecure-local-key")

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "testserver"],
)
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000", "http://testserver"],
)
CORS_ALLOW_CREDENTIALS = True

CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = False
CSRF_TRUSTED_ORIGINS = ["http://localhost:3000"]
SESSION_COOKIE_SAMESITE = "Lax"

# Use HTTP-safe cookies in local development
SIMPLE_JWT = {
    **SIMPLE_JWT,
    "AUTH_COOKIE_SECURE": False,
}

# Disable throttling for local development
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": [],
}

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Silkprofiler
SILKY_PYTHON_PROFILER = env.bool("SILKY_PYTHON_PROFILER", default=False)
if SILKY_PYTHON_PROFILER:
    MIDDLEWARE.insert(0, "silk.middleware.SilkyMiddleware")
    if "silk" not in INSTALLED_APPS:
        INSTALLED_APPS.append("silk")

FRONTEND_URL = env.str("FRONTEND_URL", default="http://localhost:3000")

# MailHog Configuration
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = (
    "localhost"  # Use '127.0.0.1' or the Docker container name if in a Docker network
)
EMAIL_PORT = 1025
EMAIL_HOST_USER = ""  # Not required by MailHog
EMAIL_HOST_PASSWORD = ""  # Not required by MailHog
EMAIL_USE_TLS = False
DEFAULT_FROM_EMAIL = "noreply@convo.local"
