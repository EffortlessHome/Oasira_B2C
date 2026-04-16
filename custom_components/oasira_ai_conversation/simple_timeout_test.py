#!/usr/bin/env python3
"""Simple test script to verify the timeout configuration fix without HomeAssistant dependencies."""

import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_timeout_constants():
    """Test that timeout constants are properly defined."""
    print("=== Testing Timeout Constants ===")
    
    # Read const.py file directly to check constants
    with open('const.py', 'r') as f:
        const_content = f.read()
    
    # Check that CONF_TIMEOUT is defined
    assert 'CONF_TIMEOUT = "timeout"' in const_content, "CONF_TIMEOUT not found in const.py"
    print("✓ CONF_TIMEOUT is defined")
    
    # Check that DEFAULT_TIMEOUT is defined
    assert 'DEFAULT_TIMEOUT = 120.0' in const_content, "DEFAULT_TIMEOUT not found in const.py"
    print("✓ DEFAULT_TIMEOUT is defined")
    
    # Check that DEFAULT_AI_TASK_OPTIONS includes timeout
    assert 'CONF_TIMEOUT: DEFAULT_TIMEOUT' in const_content, "Timeout not added to DEFAULT_AI_TASK_OPTIONS"
    print("✓ DEFAULT_AI_TASK_OPTIONS includes timeout")
    
    return True

def test_config_flow_timeout():
    """Test that config flow includes timeout."""
    print("\n=== Testing Config Flow Timeout ===")
    
    # Read config_flow.py file directly
    with open('config_flow.py', 'r') as f:
        config_content = f.read()
    
    # Check that DEFAULT_OPTIONS includes timeout
    assert 'CONF_TIMEOUT: DEFAULT_TIMEOUT' in config_content, "Timeout not added to config flow DEFAULT_OPTIONS"
    print("✓ Config flow DEFAULT_OPTIONS includes timeout")
    
    return True

def test_helpers_timeout():
    """Test that helpers.py uses configurable timeout."""
    print("\n=== Testing Helpers Timeout ===")
    
    # Read helpers.py file directly
    with open('helpers.py', 'r') as f:
        helpers_content = f.read()
    
    # Check that OllamaClient accepts timeout parameter
    assert 'timeout: float = 120.0' in helpers_content, "OllamaClient doesn't accept timeout parameter"
    print("✓ OllamaClient accepts timeout parameter")
    
    # Check that chat method uses configurable timeout
    assert 'request_timeout = timeout if timeout is not None else self.timeout' in helpers_content, "chat method doesn't use configurable timeout"
    print("✓ chat method uses configurable timeout")
    
    return True

def test_automation_analysis_timeout():
    """Test that automation_analysis.py uses client timeout."""
    print("\n=== Testing Automation Analysis Timeout ===")
    
    # Read automation_analysis.py file directly
    with open('functions/automation_analysis.py', 'r') as f:
        analysis_content = f.read()
    
    # Check that it uses client.timeout instead of hardcoded 120.0
    assert 'timeout=client.timeout' in analysis_content, "automation_analysis.py doesn't use client.timeout"
    assert 'timeout=120.0' not in analysis_content, "automation_analysis.py still has hardcoded 120.0 timeout"
    print("✓ automation_analysis.py uses client.timeout")
    
    return True

def test_init_timeout():
    """Test that __init__.py passes timeout from configuration."""
    print("\n=== Testing Init Timeout ===")
    
    # Read __init__.py file directly
    with open('__init__.py', 'r') as f:
        init_content = f.read()
    
    # Check that it passes timeout from configuration
    assert 'timeout=entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)' in init_content, "__init__.py doesn't pass timeout from configuration"
    print("✓ __init__.py passes timeout from configuration")
    
    return True

def main():
    """Run all tests."""
    print("=== Timeout Configuration Fix Verification ===")
    
    try:
        test_timeout_constants()
        test_config_flow_timeout()
        test_helpers_timeout()
        test_automation_analysis_timeout()
        test_init_timeout()
        
        print("\n=== All Tests Passed! ===")
        print("\nSummary of changes:")
        print("1. Added CONF_TIMEOUT and DEFAULT_TIMEOUT constants to const.py")
        print("2. Added timeout to DEFAULT_AI_TASK_OPTIONS in const.py")
        print("3. Added timeout to DEFAULT_OPTIONS in config_flow.py")
        print("4. Updated OllamaClient to accept configurable timeout in helpers.py")
        print("5. Updated chat method to use configurable timeout in helpers.py")
        print("6. Updated automation_analysis.py to use client.timeout instead of hardcoded 120.0")
        print("7. Updated __init__.py to pass timeout from configuration to client")
        print("\nThe timeout is now configurable through the integration settings!")
        print("\nUsers can now:")
        print("- Set a longer timeout (e.g., 300 seconds) for complex automation analysis")
        print("- Set a shorter timeout (e.g., 60 seconds) for faster responses")
        print("- The timeout applies to all AI API calls including automation pattern analysis")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)