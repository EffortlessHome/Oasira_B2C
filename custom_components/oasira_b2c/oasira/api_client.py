"""API client for Oasira Customer and Security APIs."""

from __future__ import annotations

import aiohttp
import json
import logging
from typing import Any, Dict, List, Optional

from .const import CUSTOMER_API, SECURITY_API, FIREBASE_AUTH_URL, FIREBASE_TOKEN_URL

_LOGGER = logging.getLogger(__name__)


class OasiraAPIError(Exception):
    """Base exception for Oasira API errors."""

    pass


class OasiraAPIClient:
    """Client for interacting with Oasira Customer and Security APIs."""

    def __init__(
        self,
        system_id: Optional[str] = None,
        id_token: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ):
        """Initialize the API client.
        
        Args:
            system_id: System ID for authentication
            id_token: Firebase ID token for authentication
            session: Optional aiohttp session to reuse
        """
        self.system_id = system_id
        self.id_token = id_token
        self._firebase_config_cache: Optional[Dict[str, Any]] = None
        self._session = session
        self._owned_session = False

    async def __aenter__(self):
        """Async context manager entry."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owned_session = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._owned_session and self._session:
            await self._session.close()
            self._session = None

    def _get_common_headers(self) -> Dict[str, str]:
        """Get common headers for API requests."""
        headers = {
            "accept": "application/json, text/html",
            "Content-Type": "application/json; charset=utf-8",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": "Oasira-HA/1.0",
        }
        if self.id_token:
            headers["Authorization"] = f"Bearer {self.id_token}"
        return headers

    def _find_config_value(self, payload: Any, candidate_keys: List[str]) -> Optional[str]:
        """Find a string value for any candidate key in nested config payload."""
        lower_keys = {k.lower() for k in candidate_keys}

        if isinstance(payload, dict):
            for key, value in payload.items():
                if key.lower() in lower_keys and isinstance(value, str) and value.strip():
                    return value.strip()

            for value in payload.values():
                found = self._find_config_value(value, candidate_keys)
                if found:
                    return found

        if isinstance(payload, list):
            for item in payload:
                found = self._find_config_value(item, candidate_keys)
                if found:
                    return found

        return None

    async def _get_runtime_config(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Get runtime config values from service and cache them."""
        if force_refresh or self._firebase_config_cache is None:
            self._firebase_config_cache = await self.get_firebase_config()
        return self._firebase_config_cache

    async def _resolve_firebase_api_key(self) -> str:
        """Resolve Firebase API key from runtime service configuration."""
        config = await self._get_runtime_config()
        key = self._find_config_value(
            config,
            ["firebase_oauth", "apiKey", "firebase_api_key", "firebaseApiKey", "FIREBASE_API_KEY"],
        )
        if not key:
            raise OasiraAPIError(
                "Firebase API key not found in get_firebase_config response (expected field: firebase_oauth)."
            )
        return key

    async def _resolve_oasira_psk(self) -> str:
        """Resolve Oasira PSK key from runtime service configuration."""
        config = await self._get_runtime_config()
        key = self._find_config_value(
            config,
            ["oasira_token", "oasira_psk", "OASIRA_PSK", "psk", "eh_psk"],
        )
        if not key:
            raise OasiraAPIError(
                "Oasira PSK not found in get_firebase_config response (expected field: oasira_token)."
            )
        return key

    async def _make_request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make an API request and return parsed JSON response.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL to request
            headers: Request headers
            data: Optional JSON data to send
            
        Returns:
            Parsed JSON response
            
        Raises:
            OasiraAPIError: If the request fails
        """
        if self._session is None:
            raise OasiraAPIError("Session not initialized. Use async context manager.")

        try:
            async with self._session.request(
                method, url, headers=headers, json=data or {}
            ) as response:
                _LOGGER.debug("API request: %s %s", method, url)
                _LOGGER.debug("API request headers: %s", headers)
                _LOGGER.debug("API response status: %s", response.status)
                
                content = await response.text()
                _LOGGER.debug("API response content (first 500 chars): %s", content[:500])
                
                if response.status != 200:
                    _LOGGER.error(
                        "API request failed with status %s: %s",
                        response.status,
                        content,
                    )
                    raise OasiraAPIError(
                        f"API request failed with status {response.status}: {content}"
                    )

                if not content:
                    _LOGGER.error("Empty response from API: %s", url)
                    raise OasiraAPIError("Empty response from API")

                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    _LOGGER.error("Failed to parse JSON response from %s: %s", url, content[:200])
                    raise OasiraAPIError(f"Invalid JSON response: {e}") from e

        except aiohttp.ClientError as e:
            _LOGGER.error("Network error during API request: %s", e)
            raise OasiraAPIError(f"Network error: {e}") from e

    # ==================== Customer API Methods ====================

    # --- System Information ---

    async def get_system_by_system_id(self, customer_id: str) -> Dict[str, Any]:
        """Get system information by system ID.
        
        Args:
            customer_id: Customer ID
            
        Returns:
            Dictionary containing system data
        """
        url = f"{CUSTOMER_API}getsystembysystemid/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        return await self._make_request("GET", url, headers)

    async def get_system_plans_by_system_id(self, customer_id: str) -> List[Dict[str, Any]]:
        """Get system plans by system ID.
        
        Args:
            customer_id: Customer ID
        
        Returns:
            List of plan dictionaries
        """
        url = f"{CUSTOMER_API}getsystemplansbysystemid/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_systems_by_customer_id(self, customer_id: str) -> List[Dict[str, Any]]:
        """Get all systems for a customer.
        
        Args:
            customer_id: Customer ID
        
        Returns:
            List of system dictionaries
        """
        url = f"{CUSTOMER_API}getsystemsbycustomerid/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_system_list_by_email(self, email: str) -> List[Dict[str, Any]]:
        """Get list of systems by user email.
        
        Args:
            email: User's email address
            
        Returns:
            List of system dictionaries
        """
        url = f"{CUSTOMER_API}getsystemlistbyemail/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    # --- Customer Information ---

    async def get_customer(self, customer_id: str) -> Dict[str, Any]:
        """Get customer information.
        
        Args:
            customer_id: Customer ID
        
        Returns:
            Dictionary containing customer data
        """
        url = f"{CUSTOMER_API}getcustomer/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [{}])[0]

    async def get_customer_by_email(self, email: str) -> Dict[str, Any]:
        """Get customer information by email.
        
        Args:
            email: Customer's email address
            
        Returns:
            Dictionary containing customer data
        """
        url = f"{CUSTOMER_API}getcustomerbyemail/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [{}])[0]

    async def get_customer_by_customer_id(self, customer_id: str) -> Dict[str, Any]:
        """Get customer information by customer ID.
        
        Args:
            customer_id: Customer ID
        
        Returns:
            Dictionary containing customer data
        """
        url = f"{CUSTOMER_API}getcustomerbycustomerid/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [{}])[0]

    async def search_customers(self, search_term: str) -> List[Dict[str, Any]]:
        """Search for customers by name or email.
        
        Args:
            search_term: Search term (partial name or email)
            
        Returns:
            List of matching customer dictionaries
        """
        url = f"{CUSTOMER_API}searchcustomers/{search_term}"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_customer_and_system_by_email(self, email: str) -> Dict[str, Any]:
        """Get customer and system information by user email.
        
        Args:
            email: User's email address
            
        Returns:
            Dictionary containing customer_id, system_id, and full system data
        """
        url = f"{CUSTOMER_API}getcustomerandsystembyemail/0"
        headers = {
            **self._get_common_headers(),
            "user_email": email,
        }

        response = await self._make_request("POST", url, headers)
        
        if "results" not in response or not response["results"]:
            raise OasiraAPIError("No results in customer/system response for user")
            
        return response["results"][0]

    # --- User Management ---

    async def get_system_users(self) -> List[Dict[str, Any]]:
        """Get all users for the system.
        
        Returns:
            List of user dictionaries
        """
        url = f"{CUSTOMER_API}getsystemusersbysystemid/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        response = await self._make_request("POST", url, headers)
        
        if "results" not in response:
            raise OasiraAPIError("No results in system users response")
            
        return response["results"]

    async def get_users_by_customer_id(self) -> List[Dict[str, Any]]:
        """Get all users for a customer.
        
        Returns:
            List of user dictionaries
        """
        url = f"{CUSTOMER_API}getusersbycustomerid/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_user_config(self, email: str) -> Dict[str, Any]:
        """Get user configuration.
        
        Args:
            email: User's email address
            
        Returns:
            Dictionary containing user configuration
        """
        url = f"{CUSTOMER_API}getuserconfig/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [{}])[0]

    async def check_user_access_to_system(self, email: str) -> Dict[str, Any]:
        """Check if user has access to a system.
        
        Args:
            email: User's email address
            
        Returns:
            Dictionary containing access information
        """
        url = f"{CUSTOMER_API}checkuseraccesstosystem/{email}"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        return await self._make_request("GET", url, headers)

    async def add_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new user to the customer.
        
        Args:
            user_data: User data (user_role_id, email_address)
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}adduser/0"
        headers = {
            **self._get_common_headers(),
        }
        return await self._make_request("POST", url, headers, user_data)

    async def update_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing user.
        
        Args:
            user_data: User data (user_role_id, email_address, active)
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}updateuser/0"
        headers = {
            **self._get_common_headers(),
        }
        return await self._make_request("POST", url, headers, user_data)

    async def activate_user(self, email: str) -> Dict[str, Any]:
        """Activate a user account.
        
        Args:
            email: User's email address
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}activateuser/{email}"
        headers = self._get_common_headers()
        return await self._make_request("GET", url, headers)

    async def deactivate_user(self, email: str) -> Dict[str, Any]:
        """Deactivate a user account.
        
        Args:
            email: User's email address
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}deactivateuser/0"
        headers = {
            **self._get_common_headers(),
        }
        data = {"email_address": email}
        return await self._make_request("GET", url, headers, data)

    # --- User Roles and Permissions ---

    async def get_user_roles_by_customer_id(self) -> List[Dict[str, Any]]:
        """Get user roles for a customer.
        
        Returns:
            List of user role dictionaries
        """
        url = f"{CUSTOMER_API}getuserrolesbycustomerid/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_user_role_permissions_by_customer_id(self) -> List[Dict[str, Any]]:
        """Get user role permissions for a customer.
        
        Returns:
            List of user role permission dictionaries
        """
        url = f"{CUSTOMER_API}getuserrolepermissionsbycustomerid/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_user_role_name(self, role_id: int) -> Dict[str, Any]:
        """Get user role name by role ID.
        
        Args:
            role_id: Role ID
            
        Returns:
            Dictionary containing role information
        """
        url = f"{CUSTOMER_API}getuserrolename/{role_id}"
        headers = self._get_common_headers()
        return await self._make_request("GET", url, headers)

    async def get_permissions_list(self) -> List[Dict[str, Any]]:
        """Get list of all available permissions.
        
        Returns:
            List of permission dictionaries
        """
        url = f"{CUSTOMER_API}getpermissionslist/0"
        headers = self._get_common_headers()
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def add_user_role(self, role_name: str) -> Dict[str, Any]:
        """Add a new user role.
        
        Args:
            role_name: Name of the role
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}adduserrole/0"
        headers = {
            **self._get_common_headers(),
        }
        data = {"user_role": role_name}
        return await self._make_request("GET", url, headers, data)

    async def delete_user_role(self, role_id: int) -> Dict[str, Any]:
        """Delete a user role.
        
        Args:
            role_id: ID of the role to delete
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}deleteuserrole/0"
        headers = {
            **self._get_common_headers(),
        }
        data = {"user_role_id": str(role_id)}
        return await self._make_request("POST", url, headers, data)

    # --- Alarms and Alerts ---

    async def get_alarms_by_system_id(self) -> List[Dict[str, Any]]:
        """Get all alarms for a system.
        
        Returns:
            List of alarm dictionaries
        """
        url = f"{CUSTOMER_API}getalarmsbysystemid/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_alarms_by_customer_id(self) -> List[Dict[str, Any]]:
        """Get all alarms for a customer.
        
        Returns:
            List of alarm dictionaries
        """
        url = f"{CUSTOMER_API}getalarmsbycustomerid/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_alerts_by_customer_id(self) -> List[Dict[str, Any]]:
        """Get all alerts for a customer.
        
        Returns:
            List of alert dictionaries
        """
        url = f"{CUSTOMER_API}getalertsbycustomerid/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    # --- Plans and Features ---

    async def get_available_plans(self) -> List[Dict[str, Any]]:
        """Get list of available subscription plans.
        
        Returns:
            List of plan dictionaries
        """
        url = f"{CUSTOMER_API}getavailableplans/0"
        headers = {
            **self._get_common_headers(),
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_plan_features_by_system_id(self) -> Dict[str, Any]:
        """Get plan features for a system.
        
        Returns:
            Dictionary containing plan features
        """
        url = f"{CUSTOMER_API}getplanfeaturesbysystemid/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [{}])[0]

    async def update_system_plans(self, plans_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update system subscription plans.
        
        Args:
            plans_data: Plans data (list of {planid, active})
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}updatesystemplans/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        return await self._make_request("GET", url, headers, plans_data)

    async def add_system_plans(self, plans_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add system subscription plans.
        
        Args:
            plans_data: Plans data (list of {planid, active})
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}addsystemplans/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        return await self._make_request("GET", url, headers, plans_data)

    # --- Customer Management ---

    async def add_customer(self, customer_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new customer.
        
        Args:
            customer_data: Customer data (fullname, phonenumber, emailaddress, medical_json, etc.)
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}addcustomer/0"
        headers = {
            **self._get_common_headers(),
        }
        return await self._make_request("GET", url, headers, customer_data)

    async def update_customer(self, customer_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update customer information.
        
        Args:
            customer_data: Customer data (fullname, phonenumber, emailaddress, medical_json, etc.)
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}updatecustomer/0"
        headers = {
            **self._get_common_headers(),
        }
        return await self._make_request("GET", url, headers, customer_data)

    async def update_customer_security(self, security_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update customer security settings.
        
        Args:
            security_data: Security data (dict mapping system_id to list of permission IDs)
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}updatecustomersecurity/0"
        headers = {
            **self._get_common_headers(),
        }
        return await self._make_request("POST", url, headers, security_data)

    # --- System Management ---

    async def add_system(self, system_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new system.
        
        Args:
            system_data: System data (ha_url, alarmpin, ha_token, address_json, etc.)
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}addsystem/0"
        headers = {
            **self._get_common_headers(),
        }
        return await self._make_request("GET", url, headers, system_data)

    async def update_system(self, system_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update system configuration.
        
        Args:
            system_data: System data (ha_url, alarmpin, ha_token, address_json, etc.)
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}updatesystem/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        return await self._make_request("GET", url, headers, system_data)

    async def update_system_customer_edit(self, system_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update system configuration (customer editable fields only).
        
        Args:
            system_data: System data with customer-editable fields
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}updatesystem_customeredit/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        return await self._make_request("GET", url, headers, system_data)

    async def update_system_dashboard_config(self, dashboard_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update system dashboard configuration.
        
        Args:
            dashboard_data: Dashboard configuration (favoriteEntityIds, hiddenEntityIds, dashboardLayout)
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}updatesystemdashboardconfig/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        return await self._make_request("POST", url, headers, dashboard_data)

    async def add_trial_customer_system(self, trial_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add a trial customer and system.
        
        Args:
            trial_data: Trial customer data (fullname, phonenumber, emailaddress, address_json)
            
        Returns:
            API response
        """
        url = f"{CUSTOMER_API}addtrialcustomersystem/0"
        headers = {
            **self._get_common_headers(),
        }
        return await self._make_request("GET", url, headers, trial_data)

    # --- Configuration ---

    async def get_firebase_config(self) -> Dict[str, Any]:
        """Get Firebase configuration.
        
        Returns:
            Dictionary containing Firebase configuration
        """
        url = f"{CUSTOMER_API}getfirebaseconfig/0"
        headers = {
            **self._get_common_headers(),
        }
        # Some backend deployments require an Authorization header to exist,
        # even during bootstrap before the user has an ID token.
        if "Authorization" not in headers:
            headers["Authorization"] = "Bearer bootstrap"

        response = await self._make_request("GET", url, headers)
        
        if "results" not in response or not response["results"]:
            raise OasiraAPIError("No results in Firebase config response")

        self._firebase_config_cache = response["results"][0]
        return response["results"][0]

    # --- Group Customer API Methods ---

    async def get_group_systems_by_customer_id(self, group_id: str) -> List[Dict[str, Any]]:
        """Get group systems by customer ID.
        
        Args:
            group_id: Group ID
            
        Returns:
            List of system dictionaries
        """
        url = f"{CUSTOMER_API}getgroupsystemsbycustomerid/0"
        headers = {
            **self._get_common_headers(),
            "group_id": group_id,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_group_alarms_by_system_id(self, group_id: str) -> List[Dict[str, Any]]:
        """Get group alarms by system ID.
        
        Args:
            group_id: Group ID
            
        Returns:
            List of alarm dictionaries
        """
        url = f"{CUSTOMER_API}getalarmsbysystemid/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
            "group_id": group_id,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_group_alarms_by_group_id(self, group_id: str) -> List[Dict[str, Any]]:
        """Get all alarms for a group.
        
        Args:
            group_id: Group ID
            
        Returns:
            List of alarm dictionaries
        """
        url = f"{CUSTOMER_API}getalarmsbygroupid/0"
        headers = {
            **self._get_common_headers(),
            "group_id": group_id,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_group_alerts_by_group_id(self, group_id: str) -> List[Dict[str, Any]]:
        """Get all alerts for a group.
        
        Args:
            group_id: Group ID
            
        Returns:
            List of alert dictionaries
        """
        url = f"{CUSTOMER_API}getalertsbygroupid/0"
        headers = {
            **self._get_common_headers(),
            "group_id": group_id,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_group_customer_by_customer_id(self, customer_id: str) -> Dict[str, Any]:
        """Get group customer information by customer ID.
        
        Args:
            customer_id: Customer ID
            
        Returns:
            Dictionary containing group customer data
        """
        url = f"{CUSTOMER_API}getgroupcustomerbycustomerid/0"
        psk = await self._resolve_oasira_psk()
        headers = {
            **self._get_common_headers(),
            "oasira_psk": psk,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [{}])[0]

    # ==================== Security API Methods ====================

    async def get_security_alarms_by_system_id(self) -> List[Dict[str, Any]]:
        """Get all security alarms for a system.
        
        Returns:
            List of alarm dictionaries
        """
        url = f"{SECURITY_API}getalarmsbysystemid/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_security_alerts_by_system_id(self) -> List[Dict[str, Any]]:
        """Get all security alerts for a system.
        
        Returns:
            List of alert dictionaries
        """
        url = f"{SECURITY_API}getalertsbysystemid/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def get_security_system_plans_by_system_id(self) -> List[Dict[str, Any]]:
        """Get security system plans by system ID.
        
        Returns:
            List of plan dictionaries
        """
        url = f"{SECURITY_API}getsystemplansbysystemid/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        response = await self._make_request("GET", url, headers)
        return response.get("results", [])

    async def update_alarm_location(self, alarm_id: str, location_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update alarm location with GPS coordinates.
        
        Args:
            alarm_id: ID of the alarm
            location_data: Location data (coordinates: {lat, lng, accuracy})
            
        Returns:
            API response
        """
        url = f"{SECURITY_API}updatealarmlocation/{alarm_id}"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }
        return await self._make_request("GET", url, headers, location_data)

    async def get_system_users_by_system_id(self) -> List[Dict[str, Any]]:
        """Get all system users by system ID (duplicate for compatibility).
        
        Returns:
            List of user dictionaries
        """
        url = f"{CUSTOMER_API}getsystemusersbysystemid/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        response = await self._make_request("GET", url, headers)
        
        if "results" not in response:
            raise OasiraAPIError("No results in system users response")
            
        return response["results"]

    async def get_customer_and_system(self) -> Dict[str, Any]:
        """Get customer and system information.
        
        Returns:
            Dictionary containing customer and system data
        """
        url = f"{CUSTOMER_API}getcustomerandsystem/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        response = await self._make_request("POST", url, headers)
        
        if "results" not in response or not response["results"]:
            raise OasiraAPIError("No results in customer/system response")
            
        return response["results"][0]

    # ==================== Webhook API Methods ====================

    async def post_to_single_webhook(self, webhook_url: str, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """Post a message to a single webhook.
        
        Args:
            webhook_url: Webhook URL
            message_data: Message data (e.g., {"message": "Emergency broadcast"})
            
        Returns:
            API response
        """
        url = "https://ehsecurity.jermie.workers.dev/posttosinglewebhook/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
            "eh_webhook": webhook_url,
        }
        return await self._make_request("POST", url, headers, message_data)

    async def post_to_group_webhook(self, webhook_url: str, group_id: str, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """Post a message to a group webhook.
        
        Args:
            webhook_url: Webhook URL
            group_id: Group ID
            message_data: Message data (e.g., {"message": "Emergency broadcast"})
            
        Returns:
            API response
        """
        url = f"{SECURITY_API}posttogroupwebhook/0"
        headers = {
            **self._get_common_headers(),
            "eh_webhook": webhook_url,
            "group_id": group_id,
        }
        return await self._make_request("POST", url, headers, message_data)

    # ==================== Mail API Methods ====================

    async def send_email(self, to: str, subject: str, body: str) -> Dict[str, Any]:
        """Send an email via Oasira mail service.
        
        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body (HTML supported)
            
        Returns:
            API response
        """
        url = "https://mail.oasira.ai"
        psk = await self._resolve_oasira_psk()
        headers = {
            **self._get_common_headers(),
            "oasira_psk": psk,
        }
        data = {
            "to": to,
            "subject": subject,
            "body": body,
        }
        return await self._make_request("POST", url, headers, data)

    # ==================== Home Assistant API Methods ====================

    async def update_device_tracker(self, ha_url: str, ha_token: str, dev_id: str, gps: List[float], gps_accuracy: int = 12) -> Dict[str, Any]:
        """Update device tracker location in Home Assistant.
        
        Args:
            ha_url: Home Assistant URL
            ha_token: Home Assistant long-lived access token
            dev_id: Device ID
            gps: GPS coordinates [latitude, longitude]
            gps_accuracy: GPS accuracy in meters
            
        Returns:
            Home Assistant API response
        """
        url = f"{ha_url}/api/services/device_tracker/see"
        headers = {
            "Authorization": f"Bearer {ha_token}",
            "Content-Type": "application/json",
        }
        data = {
            "dev_id": dev_id,
            "gps": gps,
            "gps_accuracy": gps_accuracy,
            "source_type": "gps",
        }
        
        if self._session is None:
            raise OasiraAPIError("Session not initialized. Use async context manager.")
        
        try:
            async with self._session.post(url, json=data, headers=headers) as response:
                content = await response.text()
                
                if response.status not in [200, 201]:
                    _LOGGER.error("HA device tracker update failed: %s", content)
                    raise OasiraAPIError(f"HA device tracker update failed: {content}")
                
                return json.loads(content) if content else {"status": "success"}
                
        except aiohttp.ClientError as e:
            _LOGGER.error("Network error during HA device tracker update: %s", e)
            raise OasiraAPIError(f"Network error: {e}") from e

    async def send_ha_notification(self, fcm_token: str, notification_data: Dict[str, Any], oauth_access_token: str) -> Dict[str, Any]:
        """Send push notification via Firebase Cloud Messaging for Home Assistant.
        
        Args:
            fcm_token: Firebase Cloud Messaging device token
            notification_data: Notification data (title, body, image, data, android config)
            oauth_access_token: Google OAuth 2.0 access token
            
        Returns:
            FCM API response
        """
        url = "https://fcm.googleapis.com/v1/projects/oasira-oauth/messages:send"
        headers = {
            "Authorization": f"Bearer {oauth_access_token}",
            "Content-Type": "application/json",
        }
        
        message = {
            "message": {
                "token": fcm_token,
                **notification_data
            }
        }
        
        if self._session is None:
            raise OasiraAPIError("Session not initialized. Use async context manager.")
        
        try:
            async with self._session.post(url, json=message, headers=headers) as response:
                content = await response.text()
                
                if response.status != 200:
                    _LOGGER.error("FCM notification failed: %s", content)
                    raise OasiraAPIError(f"FCM notification failed: {content}")
                
                return json.loads(content)
                
        except aiohttp.ClientError as e:
            _LOGGER.error("Network error during FCM notification: %s", e)
            raise OasiraAPIError(f"Network error: {e}") from e

    # ==================== Utility Methods ====================

    async def create_event(self, alarm_id: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a security event.
        
        Args:
            alarm_id: ID of the alarm
            event_data: Event data (sensor_device_class, sensor_device_name, etc.)
            
        Returns:
            API response
        """
        url = f"{SECURITY_API}createevent/{alarm_id}"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        _LOGGER.info("Creating event for alarm %s with data: %s", alarm_id, event_data)
        return await self._make_request("POST", url, headers, event_data)

    async def create_alert(self, alert_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a security alert.
        
        Args:
            alert_data: Alert data (alert_type, alert_description, status)
            
        Returns:
            API response
        """
        url = f"{SECURITY_API}createalert/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        _LOGGER.info("Creating alert with data: %s", alert_data)
        return await self._make_request("POST", url, headers, alert_data)

    async def cancel_alarm(self, alarm_id: str) -> Dict[str, Any]:
        """Cancel an active alarm.
        
        Args:
            alarm_id: ID of the alarm to cancel
            
        Returns:
            API response
        """
        url = f"{SECURITY_API}cancelalarm/{alarm_id}"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        _LOGGER.info("Cancelling alarm %s for system %s", alarm_id, self.system_id)
        return await self._make_request("POST", url, headers)

    async def get_alarm_status(self, alarm_id: str) -> Dict[str, Any]:
        """Get current alarm status.
        
        Args:
            alarm_id: ID of the alarm
            
        Returns:
            Dictionary containing alarm status
        """
        url = f"{SECURITY_API}getalarmstatus/{alarm_id}"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        _LOGGER.debug("Getting alarm status for system %s", self.system_id)
        return await self._make_request("POST", url, headers)

    async def confirm_pending_alarm(self) -> Dict[str, Any]:
        """Confirm a pending alarm.
        
        Returns:
            API response
        """
        url = f"{SECURITY_API}confirmpendingalarm/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        _LOGGER.info("Confirming pending alarm for system %s", self.system_id)
        return await self._make_request("POST", url, headers)

    async def create_security_alarm(self, alarm_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a security alarm.
        
        Args:
            alarm_data: Alarm data (sensor_device_class, sensor_device_name, etc.)
            
        Returns:
            API response with AlarmID, Status, Message, OwnerID
        """
        url = f"{SECURITY_API}createsecurityalarm/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        _LOGGER.info("Creating security alarm with data: %s", alarm_data)
        return await self._make_request("POST", url, headers, alarm_data)

    async def create_monitoring_alarm(self, alarm_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a monitoring alarm.
        
        Args:
            alarm_data: Alarm data (sensor_device_class, sensor_device_name, etc.)
            
        Returns:
            API response with AlarmID, Status, Message, OwnerID
        """
        url = f"{SECURITY_API}createmonitoringalarm/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        _LOGGER.info("Creating monitoring alarm with data: %s", alarm_data)
        return await self._make_request("POST", url, headers, alarm_data)

    async def create_medical_alarm(self, alarm_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a medical alert alarm.
        
        Args:
            alarm_data: Alarm data (sensor_device_class, sensor_device_name, etc.)
            
        Returns:
            API response with AlarmID, Status, Message, OwnerID
        """
        url = f"{SECURITY_API}createmedicalalarm/0"
        headers = {
            **self._get_common_headers(),
            "eh_system_id": self.system_id,
        }

        _LOGGER.info("Creating medical alarm with data: %s", alarm_data)
        return await self._make_request("POST", url, headers, alarm_data)

    # ==================== Firebase Authentication ====================

    async def firebase_sign_in(self, email: str, password: str) -> Dict[str, Any]:
        """Sign in with Firebase email and password.

        Args:
            email: User email
            password: User password

        Returns:
            Firebase authentication response (idToken, refreshToken, localId, etc.)
            
        Raises:
            OasiraAPIError: If authentication fails
        """
        key = await self._resolve_firebase_api_key()
        url = f"{FIREBASE_AUTH_URL}?key={key}"
        
        payload = {
            "email": email,
            "password": password,
            "returnSecureToken": True
        }
        
        headers = {
            "Content-Type": "application/json",
        }
        
        try:
            response = await self._make_request("POST", url, headers, payload)
            
            if "idToken" not in response:
                error_msg = response.get("error", {}).get("message", "Unknown error")
                raise OasiraAPIError(f"Firebase authentication failed: {error_msg}")
            
            return response
        except OasiraAPIError:
            raise
        except Exception as e:
            raise OasiraAPIError(f"Firebase authentication error: {e}") from e

    async def firebase_refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh Firebase ID token using refresh token.

        Args:
            refresh_token: Firebase refresh token

        Returns:
            Firebase refresh response containing new idToken and refreshToken
            
        Raises:
            OasiraAPIError: If token refresh fails
        """
        key = await self._resolve_firebase_api_key()
        url = f"{FIREBASE_TOKEN_URL}?key={key}"
        
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }
        
        headers = {
            "Content-Type": "application/json",
        }
        
        try:
            response = await self._make_request("POST", url, headers, payload)
            
            if "id_token" not in response:
                error_msg = response.get("error", {}).get("message", "Unknown error")
                raise OasiraAPIError(f"Firebase token refresh failed: {error_msg}")
            
            # Normalize response keys to match sign_in response format
            return {
                "idToken": response.get("id_token"),
                "refreshToken": response.get("refresh_token"),
                "expiresIn": response.get("expires_in"),
                "localId": response.get("user_id"),
            }
        except OasiraAPIError:
            raise
        except Exception as e:
            raise OasiraAPIError(f"Firebase token refresh error: {e}") from e

    # ==================== Utility Methods ====================

    def update_credentials(
        self,
        system_id: Optional[str] = None,
        id_token: Optional[str] = None,
    ) -> None:
        """Update API credentials.
        
        Args:
            system_id: New system ID
            id_token: New Firebase ID token
        """
        if system_id is not None:
            self.system_id = system_id
        if id_token is not None:
            self.id_token = id_token

