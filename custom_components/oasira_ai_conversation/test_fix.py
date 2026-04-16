#!/usr/bin/env python3
"""
Test script to verify the 503 error fix in automation_analysis.py
"""

import asyncio
import logging
from unittest.mock import Mock, AsyncMock
import httpx

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock the HomeAssistant and related components
class MockHomeAssistant:
    pass

class MockState:
    def __init__(self, entity_id, state, last_changed):
        self.entity_id = entity_id
        self.state = state
        self.last_changed = last_changed

# Test the error handling
async def test_503_error_handling():
    """Test that 503 errors are properly handled"""
    
    # Import the function
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    
    # Import required modules
    from functions.automation_analysis import AutomationAnalysisFunction
    
    # Create the function instance
    func = AutomationAnalysisFunction()
    
    # Create a mock client that will raise a 503 error
    mock_client = Mock()
    mock_client.base_url = "http://192.168.1.154:11434"
    mock_client.timeout = 120.0
    
    # Mock the list_models method to return a model
    mock_client.list_models = AsyncMock(return_value=[{"name": "llama2"}])
    
    # Mock the chat method to raise a 503 error
    mock_response = Mock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    
    mock_http_error = httpx.HTTPStatusError(
        "503 Service Unavailable",
        request=Mock(),
        response=mock_response
    )
    
    mock_client.chat = AsyncMock(side_effect=mock_http_error)
    
    # Create test data
    hass = MockHomeAssistant()
    function_config = {"type": "analyze"}
    arguments = {}
    
    # Mock exposed entities
    exposed_entities = [
        {"entity_id": "light.living_room", "name": "Living Room Light"},
        {"entity_id": "sensor.motion", "name": "Motion Sensor"}
    ]
    
    # Mock patterns (empty to test entity-based suggestions)
    patterns = []
    
    # Mock entities
    entities = exposed_entities
    
    # Test the _enhance_with_ai method directly
    try:
        result = await func._enhance_with_ai(
            mock_client, patterns, entities, 7
        )
        print("ERROR: Expected exception was not raised!")
        return False
    except Exception as e:
        error_message = str(e)
        print(f"Caught expected exception: {error_message}")
        
        # Check if the error message contains the proper 503 information
        if "503" in error_message and "Service Unavailable" in error_message:
            print("✓ 503 error properly handled and reported")
            return True
        else:
            print(f"✗ Error message doesn't contain expected 503 info: {error_message}")
            return False

async def test_connection_error_handling():
    """Test that connection errors are properly handled"""
    
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    
    from functions.automation_analysis import AutomationAnalysisFunction
    
    func = AutomationAnalysisFunction()
    
    # Create a mock client that will raise a connection error
    mock_client = Mock()
    mock_client.base_url = "http://192.168.1.154:11434"
    mock_client.timeout = 120.0
    
    # Mock the list_models method to raise a connection error
    mock_client.list_models = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))
    
    # Create test data
    hass = MockHomeAssistant()
    exposed_entities = [{"entity_id": "light.living_room", "name": "Living Room Light"}]
    patterns = []
    entities = exposed_entities
    
    try:
        result = await func._enhance_with_ai(
            mock_client, patterns, entities, 7
        )
        print("ERROR: Expected exception was not raised!")
        return False
    except Exception as e:
        error_message = str(e)
        print(f"Caught expected exception: {error_message}")
        
        if "Cannot connect to server" in error_message and "192.168.1.154:11434" in error_message:
            print("✓ Connection error properly handled and reported")
            return True
        else:
            print(f"✗ Error message doesn't contain expected connection info: {error_message}")
            return False

async def main():
    """Run all tests"""
    print("Testing 503 error handling fix...")
    print("=" * 50)
    
    test1_passed = await test_503_error_handling()
    print()
    test2_passed = await test_connection_error_handling()
    
    print("\n" + "=" * 50)
    if test1_passed and test2_passed:
        print("✓ All tests passed! The 503 error fix is working correctly.")
        return True
    else:
        print("✗ Some tests failed. Please check the implementation.")
        return False

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)