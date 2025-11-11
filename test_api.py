"""
Simple script to test the OG-AI API endpoints.
Make sure the server is running before executing this script.
"""

import requests
import json
import sys

BASE_URL = "http://localhost:8000"


def test_health():
    """Test the health endpoint."""
    print("Testing /health endpoint...")
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status Code: {response.status_code}")
    try:
        data = response.json()
        print(f"Response: {json.dumps(data, indent=2)}\n")
    except json.JSONDecodeError:
        print(f"✗ Failed to parse JSON response from /health endpoint.")
        print(f"Raw response: {response.text}\n")
        return False
    return response.status_code == 200


def test_root():
    """Test the root endpoint."""
    print("Testing / endpoint...")
    response = requests.get(f"{BASE_URL}/")
    print(f"Status Code: {response.status_code}")
    try:
        data = response.json()
        print(f"Response: {json.dumps(data, indent=2)}\n")
    except json.JSONDecodeError:
        print(f"✗ Failed to parse JSON response from / endpoint.")
        print(f"Raw response: {response.text}\n")
        return False
    return response.status_code == 200


def test_chat(message):
    """Test the chat endpoint."""
    print(f"Testing /chat endpoint with message: '{message}'")
    response = requests.post(
        f"{BASE_URL}/chat",
        json={"message": message}
    )
    print(f"Status Code: {response.status_code}")
    try:
        data = response.json()
        print(f"Response: {json.dumps(data, indent=2)}\n")
    except json.JSONDecodeError:
        print(f"✗ Failed to parse JSON response from /chat endpoint.")
        print(f"Raw response: {response.text}\n")
        return False
    return response.status_code == 200


def test_history():
    """Test the history endpoint."""
    print("Testing /history endpoint...")
    response = requests.get(f"{BASE_URL}/history")
    print(f"Status Code: {response.status_code}")
    try:
        data = response.json()
        print(f"Message Count: {data.get('message_count', 0)}")
        print(f"Response: {json.dumps(data, indent=2)}\n")
    except json.JSONDecodeError:
        print(f"✗ Failed to parse JSON response from /history endpoint.")
        print(f"Raw response: {response.text}\n")
        return False
    return response.status_code == 200


def test_reset():
    """Test the reset endpoint."""
    print("Testing /reset endpoint...")
    response = requests.post(f"{BASE_URL}/reset")
    print(f"Status Code: {response.status_code}")
    try:
        data = response.json()
        print(f"Response: {json.dumps(data, indent=2)}\n")
    except json.JSONDecodeError:
        print(f"✗ Failed to parse JSON response from /reset endpoint.")
        print(f"Raw response: {response.text}\n")
        return False
    return response.status_code == 200


def main():
    """Run all tests."""
    print("=" * 60)
    print("OG-AI API Test Suite")
    print("=" * 60)
    print(f"Testing API at: {BASE_URL}")
    print("Make sure the server is running (python app.py)\n")
    
    tests_passed = 0
    tests_total = 0
    
    # Test 1: Health check
    tests_total += 1
    if test_health():
        tests_passed += 1
    
    # Test 2: Root endpoint
    tests_total += 1
    if test_root():
        tests_passed += 1
    
    # Test 3: Chat with greeting
    tests_total += 1
    if test_chat("Hello!"):
        tests_passed += 1
    
    # Test 4: Chat with name question
    tests_total += 1
    if test_chat("What is your name?"):
        tests_passed += 1
    
    # Test 5: Get history
    tests_total += 1
    if test_history():
        tests_passed += 1
    
    # Test 6: Reset conversation
    tests_total += 1
    if test_reset():
        tests_passed += 1
    
    # Test 7: Verify history is empty after reset
    tests_total += 1
    print("Verifying history is empty after reset...")
    response = requests.get(f"{BASE_URL}/history")
    json_parse_success = True
    try:
        data = response.json()
    except json.JSONDecodeError:
        print("✗ Failed to parse JSON response from /history endpoint.")
        print(f"Raw response: {response.text}\n")
        json_parse_success = False
        data = {}
    if json_parse_success and response.status_code == 200 and data.get('message_count', 0) == 0:
        print("✓ History successfully cleared\n")
        tests_passed += 1
    else:
        print("✗ History was not cleared\n")
    
    # Summary
    print("=" * 60)
    print(f"Test Results: {tests_passed}/{tests_total} tests passed")
    print("=" * 60)
    
    if tests_passed == tests_total:
        print("✓ All tests passed!")
        return 0
    else:
        print(f"✗ {tests_total - tests_passed} test(s) failed")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to the API server.")
        print("Make sure the server is running: python app.py")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nTest interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
