from .base import *
from .base import REST_FRAMEWORK, SIMPLE_JWT

DEBUG = False

SECRET_KEY = "django-insecure-test-key-for-testing"

# Hardcoded for tests
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver", "*"]
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# Disable secure flag so Django's test client (HTTP) can send cookies
SIMPLE_JWT = {
    **SIMPLE_JWT,
    "AUTH_COOKIE_SECURE": False,
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}

# No infrastructure. The channel layer runs in-process and the presence store
# talks to fakeredis, so the suite needs neither a broker nor a server.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}
REDIS_CLIENT = "apps.conversations.services.redis_store.fake_client"

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": [],
}
