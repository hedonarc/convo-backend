from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class AuthApiTests(APITestCase):
    def setUp(self):
        self.register_url = "/api/register/"
        self.login_url = "/api/login/"
        self.password = "StrongPass123!"
        self.user = User.objects.create_user(
            username="existing_user",
            email="existing@example.com",
            password=self.password,
            first_name="Existing",
            last_name="User",
        )

    def test_register_creates_user_and_returns_token(self):
        """Create a new user, set JWT cookies, and return user data on register."""
        payload = {
            "username": "new_user",
            "email": "new@example.com",
            "password": "BrandNewPass123!",
            "confirm_password": "BrandNewPass123!",
            "first_name": "New",
            "last_name": "User",
        }

        response = self.client.post(self.register_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["message"], "You are registered")
        self.assertNotIn("token", response.data)  # tokens are now in cookies, not body
        self.assertEqual(response.data["user"]["username"], "new_user")
        self.assertTrue(User.objects.filter(username="new_user").exists())
        # Verify httpOnly cookies were set
        self.assertIn("access_token", response.cookies)
        self.assertIn("refresh_token", response.cookies)
        self.assertTrue(response.cookies["access_token"]["httponly"])
        self.assertTrue(response.cookies["refresh_token"]["httponly"])

    def test_register_rejects_duplicate_email(self):
        """Reject registration when the email is already in use."""
        payload = {
            "username": "another_user",
            "email": "existing@example.com",
            "password": "BrandNewPass123!",
            "confirm_password": "BrandNewPass123!",
        }

        response = self.client.post(self.register_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("User with this email already exists.", response.data["email"])

    def test_register_rejects_password_mismatch(self):
        """Reject registration when password confirmation does not match."""
        payload = {
            "username": "another_user",
            "email": "another@example.com",
            "password": "BrandNewPass123!",
            "confirm_password": "DifferentPass123!",
        }

        response = self.client.post(self.register_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Passwords do not match", response.data["non_field_errors"])

    def test_login_with_username_returns_token(self):
        """Authenticate with username, set JWT cookies, and return user data."""
        payload = {"username": "existing_user", "password": self.password}

        response = self.client.post(self.login_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "You are logged in.")
        self.assertEqual(response.data["user"]["username"], "existing_user")
        self.assertNotIn("token", response.data)  # tokens are now in cookies, not body
        # Verify httpOnly cookies were set
        self.assertIn("access_token", response.cookies)
        self.assertIn("refresh_token", response.cookies)
        self.assertTrue(response.cookies["access_token"]["httponly"])

    def test_login_with_email_returns_token(self):
        """Authenticate with email, set JWT cookies, and return user data."""
        payload = {"username": "existing@example.com", "password": self.password}

        response = self.client.post(self.login_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"]["email"], "existing@example.com")
        self.assertNotIn("token", response.data)  # tokens are now in cookies, not body
        self.assertIn("access_token", response.cookies)

    def test_login_with_invalid_credentials_fails(self):
        """Return 400 when credentials are invalid during login."""
        payload = {"username": "existing_user", "password": "WrongPass123!"}

        response = self.client.post(self.login_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid credentials", response.data["non_field_errors"])

    def test_logout_clears_cookies_matching_current_simple_jwt_settings(self):
        """
        Logout must delete cookies with the attributes (cookie name,
        secure, samesite) matching the current SIMPLE_JWT settings.
        """
        from django.conf import settings
        from django.test import override_settings

        prod_simple_jwt = {
            **settings.SIMPLE_JWT,
            "AUTH_COOKIE_SECURE": True,
            "AUTH_COOKIE_SAMESITE": "None",
        }

        with override_settings(SIMPLE_JWT=prod_simple_jwt):
            response = self.client.post("/api/logout/")
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            access_cookie = response.cookies.get("access_token")
            refresh_cookie = response.cookies.get("refresh_token")

            self.assertIsNotNone(access_cookie)
            self.assertIsNotNone(refresh_cookie)

            self.assertEqual(access_cookie["max-age"], 0)
            self.assertEqual(refresh_cookie["max-age"], 0)

            # These assertions will fail with the original code on production settings
            self.assertTrue(access_cookie["secure"])
            self.assertEqual(access_cookie["samesite"], "None")
            self.assertTrue(refresh_cookie["secure"])
            self.assertEqual(refresh_cookie["samesite"], "None")
