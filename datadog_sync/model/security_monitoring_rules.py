"""This is the model for the security monitoring rules"""

# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import copy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, check_diff

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SecurityMonitoringRules(BaseResource):
    """Security Monitoring Rules inherits from BaseResource"""

    resource_type = "security_monitoring_rules"
    resource_config = ResourceConfig(
        base_path="/api/v2/security_monitoring/rules",
        excluded_attributes=[],
        non_nullable_attr=[],
        null_values={},
    )
    # maximum page_size for this endpoint is 100 according to public api doc
    pagination_config = PaginationConfig(
        page_size=100,
        page_number_param="page[number]",
        page_size_param="page[size]",
        remaining_func=lambda *args: 1,
    )

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path, pagination_config=self.pagination_config
        )

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        self.destination_rules = await self.get_destination_rules()

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        # this method uses rule name for matching default rules
        rule_name = resource["name"]

        import pdb;pdb.set_trace()
        # rule does not exist at the destination, so create it
        if rule_name not in self.destination_rules:
            destination_client = self.config.destination_client
            self.handle_special_case_attr(resource)
            resp = await destination_client.post(self.resource_config.base_path, resource)
            return _id, resp

        # rule already exists at the destination
        matching_destination_rule = self.destination_rules[rule_name]
        rule_copy = copy.deepcopy(resource)
        rule_copy.update(matching_destination_rule)

        if resource["isDefault"]:
            for field in ["creationAuthorId"]:
                resource.pop(field)
                rule_copy.pop(field)

        # stomp on the versioning
        resource["version"] = rule_copy["version"]

        if check_diff(self.resource_config, resource, rule_copy):
            self.config.state.destination[self.resource_type][_id] = rule_copy
            return await self.update_resource(_id, resource)

        return _id, rule_copy



    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            resource,
        )

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass

    @staticmethod
    def handle_special_case_attr(resource):
        # Handle default ComplianceSignal attributes
        if "complianceSignalOptions" in resource:
            default_activation_status = resource["complianceSignalOptions"].get("defaultActivationStatus", None)
            user_activation_status = resource["complianceSignalOptions"].get("userActivationStatus", None)
            if not user_activation_status:
                resource["complianceSignalOptions"]["userActivationStatus"] = default_activation_status
                resource["complianceSignalOptions"].pop("defaultActivationStatus")

            default_group_by_fields = resource["complianceSignalOptions"].get("defaultGroupByFields", None)
            user_group_by_fields = resource["complianceSignalOptions"].get("userGroupByFields", None)
            if not user_group_by_fields:
                resource["complianceSignalOptions"]["userGroupByFields"] = default_group_by_fields
                resource["complianceSignalOptions"].pop("defaultGroupByFields")

    async def get_destination_rules(self):
        destination_client = self.config.destination_client
        destination_rules = {}
        try:
            destination_rules_resp = await destination_client.paginated_request(destination_client.get)(
                self.resource_config.base_path
            )
        except CustomClientHTTPError as err:
            self.config.logger.error("error retrieving rules: %s", err)
            return destination_rules

        for rule in destination_rules_resp:
            destination_rules[rule["name"]] = rule

        return destination_rules
