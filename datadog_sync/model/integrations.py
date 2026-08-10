# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class Integrations(BaseResource):
    resource_type = "integrations"
    resource_config = ResourceConfig(
        base_path="/api/v1/integration/aws",
        excluded_attributes=[
            "id",
            "created_at",
            "modified_at",
            "secret_access_key",  # Security: exclude sensitive credential
        ],
        concurrent=True,
    )

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        """Get all AWS integrations from the API"""
        resp = await client.get(self.resource_config.base_path)
        
        # The AWS integration API returns the integrations directly in the response
        # We need to filter for AWS integrations and add a 'name' field for subtype identification
        integrations = []
        if isinstance(resp, dict) and "accounts" in resp:
            # Handle the case where AWS integrations are returned in an 'accounts' field
            for account in resp.get("accounts", []):
                account["name"] = "aws"  # Add subtype identifier
                integrations.append(account)
        elif isinstance(resp, list):
            # Handle the case where integrations are returned as a list
            for integration in resp:
                integration["name"] = "aws"  # Add subtype identifier
                integrations.append(integration)
        elif isinstance(resp, dict):
            # Handle single integration response
            resp["name"] = "aws"  # Add subtype identifier
            integrations.append(resp)
            
        return integrations

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        """Import a single AWS integration by account ID"""
        source_client = self.config.source_client
        import_id = _id or resource.get("account_id")
        
        if not import_id:
            raise SkipResource(
                _id or "unknown", 
                self.resource_type, 
                "No account_id provided for AWS integration import"
            )

        try:
            # Get the specific AWS integration configuration by account ID
            resource = await source_client.get(f"{self.resource_config.base_path}/{import_id}")
            
            # Add subtype identifier
            resource["name"] = "aws"
            
        except CustomClientHTTPError as err:
            if err.status_code == 404:
                raise SkipResource(
                    import_id, 
                    self.resource_type, 
                    f"AWS integration with account ID {import_id} not found"
                )
            elif err.status_code == 403:
                raise SkipResource(
                    import_id, 
                    self.resource_type, 
                    f"No access to AWS integration with account ID {import_id}"
                )
            raise err

        resource = cast(dict, resource)
        return import_id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        """Hook called before any resource action"""
        pass

    async def pre_apply_hook(self) -> None:
        """Hook called before applying changes"""
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        """Create a new AWS integration in the destination organization"""
        destination_client = self.config.destination_client
        
        # Remove the subtype identifier before sending to API
        resource_copy = resource.copy()
        resource_copy.pop("name", None)
        
        resp = await destination_client.post(self.resource_config.base_path, resource_copy)
        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        """Update an existing AWS integration in the destination organization"""
        destination_client = self.config.destination_client
        
        # Remove the subtype identifier before sending to API
        resource_copy = resource.copy()
        resource_copy.pop("name", None)
        
        # Use the account_id from the stored destination resource for the PUT request
        account_id = self.config.state.destination[self.resource_type][_id].get("account_id", _id)
        resp = await destination_client.put(
            f"{self.resource_config.base_path}/{account_id}", 
            resource_copy
        )
        
        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        """Delete an AWS integration from the destination organization"""
        destination_client = self.config.destination_client
        
        # Use the account_id from the stored destination resource for the DELETE request
        account_id = self.config.state.destination[self.resource_type][_id].get("account_id", _id)
        await destination_client.delete(f"{self.resource_config.base_path}/{account_id}")