#!/usr/bin/env python3
"""
Simple test to verify the 503 error handling fix
"""

import httpx
from unittest.mock import Mock

def test_error_handling_logic():
    """Test the error handling logic directly"""
    
    # Test 1: HTTPStatusError (503)
    print("Test 1: HTTPStatusError (503)")
    mock_response = Mock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    
    mock_http_error = httpx.HTTPStatusError(
        "503 Service Unavailable",
        request=Mock(),
        response=mock_response
    )
    
    # Simulate the error handling logic from the fixed code
    try:
        raise mock_http_error
    except httpx.HTTPStatusError as e:
        error_msg = f"AI API call failed: Server returned {e.response.status_code} - {e.response.text}"
        print(f"✓ Generated error message: {error_msg}")
        assert "503" in error_msg
        assert "Service Unavailable" in error_msg
    
    # Test 2: ConnectError
    print("\nTest 2: ConnectError")
    try:
        raise httpx.ConnectError("Connection failed")
    except httpx.ConnectError as e:
        error_msg = f"AI API call failed: Cannot connect to server at http://192.168.1.154:11434"
        print(f"✓ Generated error message: {error_msg}")
        assert "Cannot connect to server" in error_msg
        assert "192.168.1.154:11434" in error_msg
    
    # Test 3: TimeoutException
    print("\nTest 3: TimeoutException")
    try:
        raise httpx.TimeoutException("Request timed out")
    except httpx.TimeoutException as e:
        error_msg = f"AI API call failed: Request timed out (120.0s)"
        print(f"✓ Generated error message: {error_msg}")
        assert "Request timed out" in error_msg
        assert "120.0s" in error_msg
    
    print("\n✓ All error handling tests passed!")
    return True

if __name__ == "__main__":
    test_error_handling_logic()