from .base import *
from .base import BASE_DIR, INSTALLED_APPS, MIDDLEWARE, SIMPLE_JWT, env

DEBUG = True

SECRET_KEY = env("SECRET_KEY", default="django-insecure-local-key")

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "testserver", "https://app.apidog.com"],
)
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000", "http://testserver", "https://app.apidog.com"],
)
CORS_ALLOW_CREDENTIALS = True

CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = False
CSRF_TRUSTED_ORIGINS = ["http://localhost:3000", "https://app.apidog.com"]

SESSION_COOKIE_SAMESITE = "Lax"

# Use HTTP-safe cookies in local development
SIMPLE_JWT = {
    **SIMPLE_JWT,
    "AUTH_COOKIE_SECURE": False,
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
