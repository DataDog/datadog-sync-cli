# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.integrations import Integrations
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


@pytest.fixture
def mock_config():
    """Create a mock configuration"""
    config = MagicMock()
    config.source_client = AsyncMock()
    config.destination_client = AsyncMock()
    config.state = MagicMock()
    config.state.destination = {"integrations": {}}
    return config


@pytest.fixture
def integrations_resource(mock_config):
    """Create an Integrations resource instance"""
    return Integrations(mock_config)


class TestIntegrationsResource:
    def test_resource_type(self, integrations_resource):
        """Test that resource type is correct"""
        assert integrations_resource.resource_type == "integrations"

    def test_resource_config(self, integrations_resource):
        """Test resource configuration"""
        config = integrations_resource.resource_config
        assert config.base_path == "/api/v1/integration/aws"
        assert "secret_access_key" in config.excluded_attributes
        assert "id" in config.excluded_attributes
        assert config.concurrent is True

    @pytest.mark.asyncio
    async def test_get_resources_with_accounts(self, integrations_resource, mock_config):
        """Test get_resources with accounts response"""
        mock_response = {
            "accounts": [
                {"account_id": "123456789012", "role_name": "DatadogIntegrationRole"},
                {"account_id": "987654321098", "role_name": "DatadogIntegrationRole"}
            ]
        }
        mock_config.source_client.get.return_value = mock_response
        
        result = await integrations_resource.get_resources(mock_config.source_client)
        
        assert len(result) == 2
        assert all(resource["name"] == "aws" for resource in result)
        assert result[0]["account_id"] == "123456789012"
        assert result[1]["account_id"] == "987654321098"

    @pytest.mark.asyncio
    async def test_get_resources_with_list_response(self, integrations_resource, mock_config):
        """Test get_resources with list response"""
        mock_response = [
            {"account_id": "123456789012", "role_name": "DatadogIntegrationRole"},
            {"account_id": "987654321098", "role_name": "DatadogIntegrationRole"}
        ]
        mock_config.source_client.get.return_value = mock_response
        
        result = await integrations_resource.get_resources(mock_config.source_client)
        
        assert len(result) == 2
        assert all(resource["name"] == "aws" for resource in result)

    @pytest.mark.asyncio
    async def test_get_resources_with_single_response(self, integrations_resource, mock_config):
        """Test get_resources with single dict response"""
        mock_response = {"account_id": "123456789012", "role_name": "DatadogIntegrationRole"}
        mock_config.source_client.get.return_value = mock_response
        
        result = await integrations_resource.get_resources(mock_config.source_client)
        
        assert len(result) == 1
        assert result[0]["name"] == "aws"
        assert result[0]["account_id"] == "123456789012"

    @pytest.mark.asyncio
    async def test_import_resource_success(self, integrations_resource, mock_config):
        """Test successful import of AWS integration"""
        account_id = "123456789012"
        mock_response = {"account_id": account_id, "role_name": "DatadogIntegrationRole"}
        mock_config.source_client.get.return_value = mock_response
        
        result_id, result_resource = await integrations_resource.import_resource(_id=account_id)
        
        assert result_id == account_id
        assert result_resource["name"] == "aws"
        assert result_resource["account_id"] == account_id
        mock_config.source_client.get.assert_called_once_with("/api/v1/integration/aws/123456789012")

    @pytest.mark.asyncio
    async def test_import_resource_with_resource_dict(self, integrations_resource, mock_config):
        """Test import when resource dict is provided"""
        resource_dict = {"account_id": "123456789012"}
        mock_response = {"account_id": "123456789012", "role_name": "DatadogIntegrationRole"}
        mock_config.source_client.get.return_value = mock_response
        
        result_id, result_resource = await integrations_resource.import_resource(resource=resource_dict)
        
        assert result_id == "123456789012"
        assert result_resource["name"] == "aws"

    @pytest.mark.asyncio
    async def test_import_resource_no_account_id(self, integrations_resource):
        """Test import with no account ID provided"""
        with pytest.raises(SkipResource) as exc_info:
            await integrations_resource.import_resource()
        
        assert "No account_id provided" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_import_resource_not_found(self, integrations_resource, mock_config):
        """Test import when AWS integration is not found"""
        account_id = "999999999999"
        mock_config.source_client.get.side_effect = CustomClientHTTPError(
            status_code=404, 
            message="Not found"
        )
        
        with pytest.raises(SkipResource) as exc_info:
            await integrations_resource.import_resource(_id=account_id)
        
        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_import_resource_forbidden(self, integrations_resource, mock_config):
        """Test import when access is forbidden"""
        account_id = "123456789012"
        mock_config.source_client.get.side_effect = CustomClientHTTPError(
            status_code=403, 
            message="Forbidden"
        )
        
        with pytest.raises(SkipResource) as exc_info:
            await integrations_resource.import_resource(_id=account_id)
        
        assert "No access to" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_import_resource_other_http_error(self, integrations_resource, mock_config):
        """Test import with other HTTP errors"""
        account_id = "123456789012"
        mock_config.source_client.get.side_effect = CustomClientHTTPError(
            status_code=500, 
            message="Internal Server Error"
        )
        
        with pytest.raises(CustomClientHTTPError):
            await integrations_resource.import_resource(_id=account_id)

    @pytest.mark.asyncio
    async def test_create_resource(self, integrations_resource, mock_config):
        """Test creating a new AWS integration"""
        resource = {
            "account_id": "123456789012",
            "role_name": "DatadogIntegrationRole",
            "name": "aws"  # This should be removed before API call
        }
        mock_response = {"account_id": "123456789012", "role_name": "DatadogIntegrationRole"}
        mock_config.destination_client.post.return_value = mock_response
        
        result_id, result_resource = await integrations_resource.create_resource("123456789012", resource)
        
        assert result_id == "123456789012"
        # Verify the API call was made without the 'name' field
        called_resource = mock_config.destination_client.post.call_args[0][1]
        assert "name" not in called_resource
        assert called_resource["account_id"] == "123456789012"

    @pytest.mark.asyncio
    async def test_update_resource(self, integrations_resource, mock_config):
        """Test updating an AWS integration"""
        # Mock existing resource in destination state
        mock_config.state.destination["integrations"]["123456789012"] = {
            "account_id": "123456789012"
        }
        
        resource = {
            "account_id": "123456789012",
            "role_name": "UpdatedDatadogRole",
            "name": "aws"  # This should be removed before API call
        }
        mock_response = {"account_id": "123456789012", "role_name": "UpdatedDatadogRole"}
        mock_config.destination_client.put.return_value = mock_response
        
        result_id, result_resource = await integrations_resource.update_resource("123456789012", resource)
        
        assert result_id == "123456789012"
        # Verify the API call was made without the 'name' field and used correct URL
        mock_config.destination_client.put.assert_called_once()
        called_url = mock_config.destination_client.put.call_args[0][0]
        called_resource = mock_config.destination_client.put.call_args[0][1]
        assert "/api/v1/integration/aws/123456789012" in called_url
        assert "name" not in called_resource

    @pytest.mark.asyncio
    async def test_delete_resource(self, integrations_resource, mock_config):
        """Test deleting an AWS integration"""
        # Mock existing resource in destination state
        mock_config.state.destination["integrations"]["123456789012"] = {
            "account_id": "123456789012"
        }
        
        await integrations_resource.delete_resource("123456789012")
        
        # Verify the API call was made with correct URL
        mock_config.destination_client.delete.assert_called_once_with(
            "/api/v1/integration/aws/123456789012"
        )