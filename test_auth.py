"""
Comprehensive unit tests for authentication functionality.
Tests cover registration, login, token validation, and protected endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from app import app
from auth import user_storage, get_password_hash
import auth as auth_module


# Create test client
client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_users():
    """Fixture to reset user storage between tests."""
    import os
    # Clear the existing user storage
    auth_module.user_storage.users = {}
    # Remove test file if it exists
    if os.path.exists("test_users.json"):
        os.remove("test_users.json")
    yield
    # Cleanup after test
    auth_module.user_storage.users = {}
    if os.path.exists("test_users.json"):
        os.remove("test_users.json")


class TestUserRegistration:
    """Test user registration endpoint."""

    def test_register_new_user_success(self):
        """Test successful user registration."""
        response = client.post(
            "/auth/register",
            json={"username": "testuser", "password": "password123"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "testuser"
        assert "created_at" in data
        assert "password" not in data
        assert "hashed_password" not in data

    def test_register_duplicate_username(self):
        """Test registration with duplicate username fails."""
        # Register first user
        client.post(
            "/auth/register",
            json={"username": "testuser", "password": "password123"}
        )
        # Try to register same username again
        response = client.post(
            "/auth/register",
            json={"username": "testuser", "password": "different_password"}
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_register_short_username(self):
        """Test registration with too short username fails."""
        response = client.post(
            "/auth/register",
            json={"username": "ab", "password": "password123"}
        )
        assert response.status_code == 422  # Validation error

    def test_register_invalid_username_characters(self):
        """Test registration with invalid characters in username fails."""
        response = client.post(
            "/auth/register",
            json={"username": "test@user", "password": "password123"}
        )
        assert response.status_code == 422  # Validation error

    def test_register_short_password(self):
        """Test registration with too short password fails."""
        response = client.post(
            "/auth/register",
            json={"username": "testuser", "password": "12345"}
        )
        assert response.status_code == 422  # Validation error

    def test_register_empty_username(self):
        """Test registration with empty username fails."""
        response = client.post(
            "/auth/register",
            json={"username": "", "password": "password123"}
        )
        assert response.status_code == 422

    def test_register_empty_password(self):
        """Test registration with empty password fails."""
        response = client.post(
            "/auth/register",
            json={"username": "testuser", "password": ""}
        )
        assert response.status_code == 422

    def test_register_missing_username(self):
        """Test registration without username fails."""
        response = client.post(
            "/auth/register",
            json={"password": "password123"}
        )
        assert response.status_code == 422

    def test_register_missing_password(self):
        """Test registration without password fails."""
        response = client.post(
            "/auth/register",
            json={"username": "testuser"}
        )
        assert response.status_code == 422


class TestUserLogin:
    """Test user login endpoint."""

    @pytest.fixture
    def registered_user(self):
        """Fixture to create a registered user."""
        client.post(
            "/auth/register",
            json={"username": "testuser", "password": "password123"}
        )

    def test_login_success(self, registered_user):
        """Test successful login returns token."""
        response = client.post(
            "/auth/login",
            json={"username": "testuser", "password": "password123"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0

    def test_login_wrong_password(self, registered_user):
        """Test login with wrong password fails."""
        response = client.post(
            "/auth/login",
            json={"username": "testuser", "password": "wrongpassword"}
        )
        assert response.status_code == 401
        assert "Incorrect username or password" in response.json()["detail"]

    def test_login_nonexistent_user(self):
        """Test login with non-existent user fails."""
        response = client.post(
            "/auth/login",
            json={"username": "nonexistent", "password": "password123"}
        )
        assert response.status_code == 401

    def test_login_empty_credentials(self):
        """Test login with empty credentials fails."""
        response = client.post(
            "/auth/login",
            json={"username": "", "password": ""}
        )
        assert response.status_code == 401

    def test_login_missing_username(self, registered_user):
        """Test login without username fails."""
        response = client.post(
            "/auth/login",
            json={"password": "password123"}
        )
        assert response.status_code == 422

    def test_login_missing_password(self, registered_user):
        """Test login without password fails."""
        response = client.post(
            "/auth/login",
            json={"username": "testuser"}
        )
        assert response.status_code == 422


class TestTokenValidation:
    """Test JWT token validation."""

    @pytest.fixture
    def auth_token(self):
        """Fixture to get a valid auth token."""
        client.post(
            "/auth/register",
            json={"username": "testuser", "password": "password123"}
        )
        response = client.post(
            "/auth/login",
            json={"username": "testuser", "password": "password123"}
        )
        return response.json()["access_token"]

    def test_get_current_user_with_valid_token(self, auth_token):
        """Test /auth/me endpoint with valid token."""
        response = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "testuser"
        assert "created_at" in data

    def test_get_current_user_without_token(self):
        """Test /auth/me endpoint without token fails."""
        response = client.get("/auth/me")
        assert response.status_code == 403  # Forbidden

    def test_get_current_user_with_invalid_token(self):
        """Test /auth/me endpoint with invalid token fails."""
        response = client.get(
            "/auth/me",
            headers={"Authorization": "Bearer invalid_token_here"}
        )
        assert response.status_code == 401

    def test_get_current_user_with_malformed_header(self, auth_token):
        """Test /auth/me endpoint with malformed auth header."""
        response = client.get(
            "/auth/me",
            headers={"Authorization": auth_token}  # Missing "Bearer"
        )
        assert response.status_code == 403


class TestProtectedEndpoints:
    """Test that chat, history, and reset endpoints require authentication."""

    @pytest.fixture
    def auth_token(self):
        """Fixture to get a valid auth token."""
        client.post(
            "/auth/register",
            json={"username": "testuser", "password": "password123"}
        )
        response = client.post(
            "/auth/login",
            json={"username": "testuser", "password": "password123"}
        )
        return response.json()["access_token"]

    def test_chat_without_auth(self):
        """Test /chat endpoint requires authentication."""
        response = client.post(
            "/chat",
            json={"message": "Hello"}
        )
        assert response.status_code == 403

    def test_chat_with_auth(self, auth_token):
        """Test /chat endpoint works with valid token."""
        response = client.post(
            "/chat",
            json={"message": "Hello"},
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "agent_name" in data

    def test_history_without_auth(self):
        """Test /history endpoint requires authentication."""
        response = client.get("/history")
        assert response.status_code == 403

    def test_history_with_auth(self, auth_token):
        """Test /history endpoint works with valid token."""
        response = client.get(
            "/history",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "conversation" in data
        assert "message_count" in data

    def test_reset_without_auth(self):
        """Test /reset endpoint requires authentication."""
        response = client.post("/reset")
        assert response.status_code == 403

    def test_reset_with_auth(self, auth_token):
        """Test /reset endpoint works with valid token."""
        response = client.post(
            "/reset",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_clear_without_auth(self):
        """Test /clear endpoint requires authentication."""
        response = client.post("/clear")
        assert response.status_code == 403

    def test_clear_with_auth(self, auth_token):
        """Test /clear endpoint works with valid token."""
        response = client.post(
            "/clear",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        assert response.status_code == 200


class TestUserIsolation:
    """Test that users have isolated conversation histories."""

    @pytest.fixture
    def user1_token(self):
        """Fixture for user1 auth token."""
        client.post(
            "/auth/register",
            json={"username": "user1", "password": "password123"}
        )
        response = client.post(
            "/auth/login",
            json={"username": "user1", "password": "password123"}
        )
        return response.json()["access_token"]

    @pytest.fixture
    def user2_token(self):
        """Fixture for user2 auth token."""
        client.post(
            "/auth/register",
            json={"username": "user2", "password": "password123"}
        )
        response = client.post(
            "/auth/login",
            json={"username": "user2", "password": "password123"}
        )
        return response.json()["access_token"]

    def test_users_have_separate_histories(self, user1_token, user2_token):
        """Test that different users have separate conversation histories."""
        # User 1 sends a message
        client.post(
            "/chat",
            json={"message": "Hello from user1"},
            headers={"Authorization": f"Bearer {user1_token}"}
        )

        # User 2 sends a different message
        client.post(
            "/chat",
            json={"message": "Hello from user2"},
            headers={"Authorization": f"Bearer {user2_token}"}
        )

        # Check user 1's history
        response1 = client.get(
            "/history",
            headers={"Authorization": f"Bearer {user1_token}"}
        )
        history1 = response1.json()["conversation"]

        # Check user 2's history
        response2 = client.get(
            "/history",
            headers={"Authorization": f"Bearer {user2_token}"}
        )
        history2 = response2.json()["conversation"]

        # Find user messages in each history
        user1_messages = [msg for msg in history1 if msg["role"] == "user"]
        user2_messages = [msg for msg in history2 if msg["role"] == "user"]

        # Verify they are different
        assert len(user1_messages) > 0
        assert len(user2_messages) > 0
        assert user1_messages[0]["content"] == "Hello from user1"
        assert user2_messages[0]["content"] == "Hello from user2"

    def test_reset_only_affects_own_history(self, user1_token, user2_token):
        """Test that resetting history only affects the user's own history."""
        # Both users send messages
        client.post(
            "/chat",
            json={"message": "Message from user1"},
            headers={"Authorization": f"Bearer {user1_token}"}
        )
        client.post(
            "/chat",
            json={"message": "Message from user2"},
            headers={"Authorization": f"Bearer {user2_token}"}
        )

        # User 1 resets their history
        client.post(
            "/reset",
            headers={"Authorization": f"Bearer {user1_token}"}
        )

        # Check both histories
        response1 = client.get(
            "/history",
            headers={"Authorization": f"Bearer {user1_token}"}
        )
        response2 = client.get(
            "/history",
            headers={"Authorization": f"Bearer {user2_token}"}
        )

        # User 1's history should be empty
        assert response1.json()["message_count"] == 0

        # User 2's history should still have messages
        assert response2.json()["message_count"] > 0
