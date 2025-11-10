"""
Comprehensive unit tests for app.py (FastAPI application)
Tests cover all API endpoints, request validation, error handling, and edge cases.
"""

import pytest
import json
from fastapi.testclient import TestClient
import app as app_module
from app import app, get_agent
from ai_agent import AIAgent


# Create test client
client = TestClient(app)


@pytest.fixture
def reset_agent():
    """Fixture to reset the global agent between tests."""
    app_module.agent = None
    yield
    app_module.agent = None


@pytest.fixture
def mock_config_file(tmp_path):
    """Fixture to create a temporary config file."""
    config_path = tmp_path / "config.json"
    config_data = {
        "agent_name": "TestAgent",
        "system_prompt": "Test prompt"
    }
    with open(config_path, 'w') as f:
        json.dump(config_data, f)
    return str(config_path)


class TestRootEndpoint:
    """Test the root (/) endpoint."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_root_returns_200(self):
        """Test root endpoint returns 200 status."""
        response = client.get("/")
        assert response.status_code == 200
    
    @pytest.mark.usefixtures("reset_agent")
    def test_root_returns_json(self):
        """Test root endpoint returns JSON."""
        response = client.get("/")
        assert response.headers["content-type"] == "application/json"
    
    @pytest.mark.usefixtures("reset_agent")
    def test_root_response_structure(self):
        """Test root endpoint response has required fields."""
        response = client.get("/")
        data = response.json()
        
        assert "status" in data
        assert "agent_name" in data
        assert "message" in data
    
    @pytest.mark.usefixtures("reset_agent")
    def test_root_response_values(self):
        """Test root endpoint response values."""
        response = client.get("/")
        data = response.json()
        
        assert data["status"] == "healthy"
        assert isinstance(data["agent_name"], str)
        assert len(data["agent_name"]) > 0
        assert isinstance(data["message"], str)
    
    @pytest.mark.usefixtures("reset_agent")
    def test_root_mentions_docs(self):
        """Test root response mentions documentation."""
        response = client.get("/")
        data = response.json()
        
        assert "/docs" in data["message"]
    
    @pytest.mark.usefixtures("reset_agent")
    def test_root_creates_agent(self):
        """Test that calling root creates the agent."""
        assert app_module.agent is None
        
        response = client.get("/")
        assert response.status_code == 200
        assert app_module.agent is not None


class TestHealthEndpoint:
    """Test the /health endpoint."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_health_returns_200(self):
        """Test health endpoint returns 200 status."""
        response = client.get("/health")
        assert response.status_code == 200
    
    @pytest.mark.usefixtures("reset_agent")
    def test_health_returns_json(self):
        """Test health endpoint returns JSON."""
        response = client.get("/health")
        assert response.headers["content-type"] == "application/json"
    
    @pytest.mark.usefixtures("reset_agent")
    def test_health_response_structure(self):
        """Test health endpoint response structure."""
        response = client.get("/health")
        data = response.json()
        
        assert "status" in data
        assert "agent_name" in data
        assert "message" in data
    
    @pytest.mark.usefixtures("reset_agent")
    def test_health_status_healthy(self):
        """Test health status is 'healthy'."""
        response = client.get("/health")
        data = response.json()
        
        assert data["status"] == "healthy"
    
    @pytest.mark.usefixtures("reset_agent")
    def test_health_agent_name_present(self):
        """Test health response includes agent name."""
        response = client.get("/health")
        data = response.json()
        
        assert isinstance(data["agent_name"], str)
        assert len(data["agent_name"]) > 0
    
    @pytest.mark.usefixtures("reset_agent")
    def test_health_multiple_calls(self):
        """Test multiple health check calls."""
        response1 = client.get("/health")
        response2 = client.get("/health")
        
        assert response1.status_code == 200
        assert response2.status_code == 200
        assert response1.json()["status"] == "healthy"
        assert response2.json()["status"] == "healthy"


class TestChatEndpoint:
    """Test the /chat endpoint."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_simple_message(self):
        """Test sending a simple message to chat."""
        response = client.post("/chat", json={"message": "Hello"})
        
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "agent_name" in data
        assert "timestamp" in data
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_response_not_empty(self):
        """Test chat response is not empty."""
        response = client.post("/chat", json={"message": "Test"})
        data = response.json()
        
        assert len(data["response"]) > 0
        assert isinstance(data["response"], str)
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_greeting(self):
        """Test chat with greeting message."""
        response = client.post("/chat", json={"message": "Hello!"})
        data = response.json()
        
        assert response.status_code == 200
        assert "hello" in data["response"].lower() or "hi" in data["response"].lower()
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_help_request(self):
        """Test chat with help request."""
        response = client.post("/chat", json={"message": "I need help"})
        data = response.json()
        
        assert response.status_code == 200
        assert "help" in data["response"].lower() or "assist" in data["response"].lower()
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_name_query(self):
        """Test asking for agent's name."""
        response = client.post("/chat", json={"message": "What is your name?"})
        data = response.json()
        
        assert response.status_code == 200
        assert data["agent_name"] in data["response"]
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_empty_message(self):
        """Test sending empty message returns 400."""
        response = client.post("/chat", json={"message": ""})
        assert response.status_code == 400
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_whitespace_only_message(self):
        """Test sending whitespace-only message returns 400."""
        response = client.post("/chat", json={"message": "   "})
        assert response.status_code == 400
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_missing_message_field(self):
        """Test sending request without message field."""
        response = client.post("/chat", json={})
        assert response.status_code == 422  # Unprocessable Entity
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_null_message(self):
        """Test sending null message."""
        response = client.post("/chat", json={"message": None})
        assert response.status_code == 422
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_invalid_json(self):
        """Test sending invalid JSON."""
        response = client.post(
            "/chat",
            data="not valid json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_wrong_content_type(self):
        """Test sending with wrong content type."""
        response = client.post(
            "/chat",
            data="message=test",
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert response.status_code == 422
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_very_long_message(self):
        """Test sending very long message."""
        long_message = "test " * 1000
        response = client.post("/chat", json={"message": long_message})
        
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_special_characters(self):
        """Test message with special characters."""
        message = "Hello! @#$% 你好 🤖"
        response = client.post("/chat", json={"message": message})
        
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_newlines_in_message(self):
        """Test message with newlines."""
        message = "Line 1\nLine 2\nLine 3"
        response = client.post("/chat", json={"message": message})
        
        assert response.status_code == 200
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_timestamp_format(self):
        """Test that timestamp is returned in proper format."""
        response = client.post("/chat", json={"message": "Test"})
        data = response.json()
        
        assert isinstance(data["timestamp"], str)
        # Timestamp should be ISO format or empty string
        assert len(data["timestamp"]) > 0
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_multiple_messages(self):
        """Test sending multiple messages in sequence."""
        response1 = client.post("/chat", json={"message": "First message"})
        response2 = client.post("/chat", json={"message": "Second message"})
        response3 = client.post("/chat", json={"message": "Third message"})
        
        assert response1.status_code == 200
        assert response2.status_code == 200
        assert response3.status_code == 200
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_strips_whitespace(self):
        """Test that message whitespace is stripped."""
        response = client.post("/chat", json={"message": "  test  "})
        
        assert response.status_code == 200
        # Should be processed successfully


class TestHistoryEndpoint:
    """Test the /history endpoint."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_history_empty_initially(self):
        """Test history is empty initially."""
        response = client.get("/history")
        
        assert response.status_code == 200
        data = response.json()
        assert data["message_count"] == 0
        assert data["conversation"] == []
    
    @pytest.mark.usefixtures("reset_agent")
    def test_history_after_one_message(self):
        """Test history after sending one message."""
        client.post("/chat", json={"message": "Hello"})
        
        response = client.get("/history")
        data = response.json()
        
        assert response.status_code == 200
        assert data["message_count"] == 2  # user + assistant
        assert len(data["conversation"]) == 2
    
    @pytest.mark.usefixtures("reset_agent")
    def test_history_structure(self):
        """Test history response structure."""
        client.post("/chat", json={"message": "Test"})
        
        response = client.get("/history")
        data = response.json()
        
        assert "conversation" in data
        assert "history" in data  # Backward compatibility field
        assert "message_count" in data
        assert isinstance(data["conversation"], list)
        assert isinstance(data["history"], list)
        assert isinstance(data["message_count"], int)
        # Verify conversation and history contain the same data
        assert data["conversation"] == data["history"]
    
    @pytest.mark.usefixtures("reset_agent")
    def test_history_message_structure(self):
        """Test structure of messages in history."""
        client.post("/chat", json={"message": "Hello"})
        
        response = client.get("/history")
        data = response.json()
        
        message = data["conversation"][0]
        assert "role" in message
        assert "content" in message
        assert "timestamp" in message
    
    @pytest.mark.usefixtures("reset_agent")
    def test_history_message_roles(self):
        """Test message roles in history."""
        client.post("/chat", json={"message": "Test"})
        
        response = client.get("/history")
        data = response.json()
        
        assert data["conversation"][0]["role"] == "user"
        assert data["conversation"][1]["role"] == "assistant"
    
    @pytest.mark.usefixtures("reset_agent")
    def test_history_preserves_order(self):
        """Test that history preserves message order."""
        client.post("/chat", json={"message": "First"})
        client.post("/chat", json={"message": "Second"})
        
        response = client.get("/history")
        data = response.json()
        
        messages = data["conversation"]
        assert messages[0]["content"] == "First"
        assert messages[2]["content"] == "Second"
    
    @pytest.mark.usefixtures("reset_agent")
    def test_history_count_matches_length(self):
        """Test that message_count matches conversation length."""
        client.post("/chat", json={"message": "One"})
        client.post("/chat", json={"message": "Two"})
        
        response = client.get("/history")
        data = response.json()
        
        assert data["message_count"] == len(data["conversation"])
    
    @pytest.mark.usefixtures("reset_agent")
    def test_history_multiple_calls(self):
        """Test multiple history calls return consistent results."""
        client.post("/chat", json={"message": "Test"})
        
        response1 = client.get("/history")
        response2 = client.get("/history")
        
        assert response1.json() == response2.json()


class TestResetEndpoint:
    """Test the /reset endpoint."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_reset_returns_200(self):
        """Test reset endpoint returns 200."""
        response = client.post("/reset")
        assert response.status_code == 200
    
    @pytest.mark.usefixtures("reset_agent")
    def test_reset_response_structure(self):
        """Test reset response structure."""
        response = client.post("/reset")
        data = response.json()
        
        assert "status" in data
        assert "agent_name" in data
        assert "message" in data
    
    @pytest.mark.usefixtures("reset_agent")
    def test_reset_status_success(self):
        """Test reset returns success status."""
        response = client.post("/reset")
        data = response.json()
        
        assert data["status"] == "success"
    
    @pytest.mark.usefixtures("reset_agent")
    def test_reset_clears_history(self):
        """Test that reset clears conversation history."""
        # Add some messages
        client.post("/chat", json={"message": "First"})
        client.post("/chat", json={"message": "Second"})
        
        # Reset
        response = client.post("/reset")
        assert response.status_code == 200
        
        # Check history is empty
        history_response = client.get("/history")
        history_data = history_response.json()
        
        assert history_data["message_count"] == 0
        assert history_data["conversation"] == []
    
    @pytest.mark.usefixtures("reset_agent")
    def test_reset_empty_history(self):
        """Test resetting already empty history."""
        response = client.post("/reset")
        assert response.status_code == 200
        
        history_response = client.get("/history")
        assert history_response.json()["message_count"] == 0
    
    @pytest.mark.usefixtures("reset_agent")
    def test_reset_multiple_times(self):
        """Test resetting multiple times."""
        client.post("/chat", json={"message": "Test"})
        
        response1 = client.post("/reset")
        response2 = client.post("/reset")
        
        assert response1.status_code == 200
        assert response2.status_code == 200
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_after_reset(self):
        """Test sending message after reset."""
        client.post("/chat", json={"message": "Before reset"})
        client.post("/reset")
        
        response = client.post("/chat", json={"message": "After reset"})
        assert response.status_code == 200
        
        history = client.get("/history").json()
        assert history["message_count"] == 2  # Only the message after reset
    
    @pytest.mark.usefixtures("reset_agent")
    def test_clear_endpoint_backward_compatibility(self):
        """Test /clear endpoint for Flask API backward compatibility."""
        # Add some messages
        client.post("/chat", json={"message": "Test message"})
        
        # Use the old Flask /clear endpoint
        response = client.post("/clear")
        assert response.status_code == 200
        
        # Verify history is cleared
        history = client.get("/history").json()
        assert history["message_count"] == 0


class TestGetAgent:
    """Test the get_agent function."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_get_agent_creates_agent(self):
        """Test that get_agent creates an agent."""
        assert app_module.agent is None
        
        agent = get_agent()
        assert agent is not None
        assert isinstance(agent, AIAgent)
    
    @pytest.mark.usefixtures("reset_agent")
    def test_get_agent_returns_same_instance(self):
        """Test that get_agent returns the same instance."""
        agent1 = get_agent()
        agent2 = get_agent()
        
        assert agent1 is agent2
    
    @pytest.mark.usefixtures("reset_agent")
    def test_get_agent_loads_config(self, tmp_path, monkeypatch):
        """Test that get_agent loads config if it exists."""
        # Create a config file
        config_path = tmp_path / "config.json"
        config_data = {"agent_name": "ConfigAgent"}
        with open(config_path, 'w') as f:
            json.dump(config_data, f)
        
        # Change to temp directory
        monkeypatch.chdir(tmp_path)
        
        agent = get_agent()
        assert agent.name == "ConfigAgent"
    
    @pytest.mark.usefixtures("reset_agent")
    def test_get_agent_handles_missing_config(self, tmp_path, monkeypatch):
        """Test that get_agent handles missing config gracefully."""
        monkeypatch.chdir(tmp_path)
        
        agent = get_agent()
        assert agent.name == "OG-AI"  # Default name
    
    @pytest.mark.usefixtures("reset_agent")
    def test_get_agent_handles_invalid_config(self, tmp_path, monkeypatch):
        """Test that get_agent handles invalid config gracefully."""
        config_path = tmp_path / "config.json"
        with open(config_path, 'w') as f:
            f.write("invalid json {{{")
        
        monkeypatch.chdir(tmp_path)
        
        # Should not raise exception, use defaults
        agent = get_agent()
        assert agent.name == "OG-AI"


class TestCORSConfiguration:
    """Test CORS configuration."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_cors_headers_present(self):
        """Test that CORS headers are present in response."""
        # CORS headers only appear with origin header
        response = client.get("/health", headers={"Origin": "http://example.com"})
        
        assert "access-control-allow-origin" in response.headers
    
    @pytest.mark.usefixtures("reset_agent")
    def test_cors_allows_all_origins_by_default(self):
        """Test that CORS allows all origins by default."""
        response = client.get("/health", headers={"Origin": "http://example.com"})
        
        # Should allow the origin
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers


class TestErrorHandling:
    """Test error handling in various scenarios."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_404_on_invalid_endpoint(self):
        """Test that invalid endpoint returns 404."""
        response = client.get("/nonexistent")
        assert response.status_code == 404
    
    @pytest.mark.usefixtures("reset_agent")
    def test_405_on_wrong_method(self):
        """Test that wrong HTTP method returns 405."""
        response = client.get("/reset")  # Should be POST
        assert response.status_code == 405
    
    @pytest.mark.usefixtures("reset_agent")
    def test_405_on_post_to_get_endpoint(self):
        """Test POST to GET endpoint returns 405."""
        response = client.post("/health")
        assert response.status_code == 405
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_with_extra_fields(self):
        """Test chat with extra fields in request."""
        response = client.post("/chat", json={
            "message": "Test",
            "extra_field": "extra_value"
        })
        
        # Should still work, extra fields ignored
        assert response.status_code == 200


class TestPydanticModels:
    """Test Pydantic model validation."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_request_validation(self):
        """Test ChatRequest model validation."""
        # Valid request
        response = client.post("/chat", json={"message": "test"})
        assert response.status_code == 200
        
        # Missing required field
        response = client.post("/chat", json={})
        assert response.status_code == 422
    
    @pytest.mark.usefixtures("reset_agent")
    def test_chat_request_wrong_type(self):
        """Test ChatRequest with wrong type."""
        response = client.post("/chat", json={"message": 123})
        # Pydantic will coerce to string or fail
        # Depending on Pydantic version, this might work or fail
        assert response.status_code in [200, 422]


class TestIntegrationScenarios:
    """Test complete integration scenarios."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_full_conversation_flow(self):
        """Test a complete conversation flow."""
        # Start conversation
        response1 = client.post("/chat", json={"message": "Hello"})
        assert response1.status_code == 200
        
        # Continue conversation
        response2 = client.post("/chat", json={"message": "What is your name?"})
        assert response2.status_code == 200
        
        # Check history
        history = client.get("/history").json()
        assert history["message_count"] == 4
        
        # Reset and verify
        client.post("/reset")
        history = client.get("/history").json()
        assert history["message_count"] == 0
    
    @pytest.mark.usefixtures("reset_agent")
    def test_concurrent_conversations(self):
        """Test that conversations are sequential (shared state)."""
        client.post("/chat", json={"message": "First"})
        client.post("/chat", json={"message": "Second"})
        
        history = client.get("/history").json()
        # Both messages should be in history
        assert history["message_count"] == 4
    
    @pytest.mark.usefixtures("reset_agent")
    def test_service_health_during_conversation(self):
        """Test that health check works during conversation."""
        client.post("/chat", json={"message": "Test"})
        
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"
    
    @pytest.mark.usefixtures("reset_agent")
    def test_reset_during_conversation(self):
        """Test resetting in the middle of a conversation."""
        client.post("/chat", json={"message": "Message 1"})
        client.post("/chat", json={"message": "Message 2"})
        
        client.post("/reset")
        
        client.post("/chat", json={"message": "Message 3"})
        
        history = client.get("/history").json()
        assert history["message_count"] == 2  # Only message 3 and its response