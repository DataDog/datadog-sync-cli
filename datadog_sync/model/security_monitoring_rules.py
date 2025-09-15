"""This is the model for the security monitoring rules"""

# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import copy
import json
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig
from datadog_sync.utils.resource_utils import (
    CustomClientHTTPError,
    SkipResource,
    check_diff,
)


if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SecurityMonitoringRules(BaseResource):
    """Security Monitoring Rules inherits from BaseResource"""

    resource_type = "security_monitoring_rules"
    resource_config = ResourceConfig(
        base_path="/api/v2/security_monitoring/rules",
        excluded_attributes=[
            "createdAt",
            "creationAuthorId",
            "updateAuthorId",
            "updatedAt",
            "isPartner",
            "isBeta",
            "isDeleted",
            "isDeprecated",
            "defaultTags",
            "version",
            "options.anomalyDetectionOptions",
            "options.impossibleTravelOptions",
            "cases.condition",
        ],
        non_nullable_attr=["queries.additionalFilters", "blocking", "metadata", "creator", "updater"],
        null_values={
            "additionalFilters": [""],
            "blocking": [False],
            "metadata": [{"entities": None, "sources": None}],
            "creator": [{"handle": "", "name": ""}],
            "updater": [{"handle": "", "name": ""}],
        },
    )
    # maximum page_size for this endpoint is 100 according to public api doc
    pagination_config = PaginationConfig(
        page_size=100,
        page_number_param="page[number]",
        page_size_param="page[size]",
        remaining_func=lambda *args: 1,
    )
    destination_rules = {}
    # can't even enable or disable immutable rules
    immutable_rule_names = [
        "Impossible travel event leads to permission enumeration",
    ]
    errors_to_skip = [
        "Invalid rule configuration",
    ]

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        self.destination_rules = await self.get_destination_rules()
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path, pagination_config=self.pagination_config
        )

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]

        resource = cast(dict, resource)

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        matching_destination_rule = self.destination_rules.get(resource["name"], None)
        if resource.get("isDefault", False) and not matching_destination_rule:
            raise SkipResource(_id, self.resource_type, "Default rule does not exist at destination")
        if resource["name"] in self.immutable_rule_names:
            raise SkipResource(_id, self.resource_type, "This rule is immutable")
        if matching_destination_rule and matching_destination_rule.get("isDeprecated", False):
            raise SkipResource(_id, self.resource_type, "Cannot update deprecated rules")

    async def pre_apply_hook(self) -> None:
        self.destination_rules = await self.get_destination_rules()

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        # this method uses rule name for matching default rules
        rule_name = resource["name"]

        # rule does not exist at the destination, so create it
        if rule_name not in self.destination_rules and not resource["isDefault"]:
            destination_client = self.config.destination_client
            self.handle_special_case_attr(resource)
            try:
                resp = await destination_client.post(self.resource_config.base_path, resource)
                return _id, resp
            except CustomClientHTTPError as err:
                if err.status_code == 400:
                    preamble = "400 Bad Request - "
                    error_json_no_preamble = err.args[0][len(preamble) :]
                    error_obj = json.loads(error_json_no_preamble)
                    errors = error_obj["errors"]
                    for error_message in errors:
                        if error_message in self.errors_to_skip:
                            raise SkipResource(_id, self.resource_type, err.args[0])
                raise err

        # Skip any default rules that do no exist at the destination
        matching_destination_rule = self.destination_rules.get(rule_name, None)
        if not matching_destination_rule:
            raise SkipResource(_id, self.resource_type, "Default rule does not exist at destination")

        # if they're different then run an update
        rule_copy = copy.deepcopy(resource)
        rule_copy.update(matching_destination_rule)
        if check_diff(self.resource_config, resource, rule_copy):
            self.config.state.destination[self.resource_type][_id] = rule_copy
            return await self.update_resource(_id, resource)

        # do nothing if they're the same
        return _id, rule_copy

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        # Skip any default rules that do no exist at the destination
        matching_destination_rule = self.destination_rules.get(resource["name"], None)
        if not matching_destination_rule:
            raise SkipResource(_id, self.resource_type, "Default rule does not exist at destination")

        if resource["isDefault"] != matching_destination_rule["isDefault"]:
            raise SkipResource(_id, self.resource_type, "Default status differs between source and destination")

        if matching_destination_rule.get("isDeprecated", False):
            raise SkipResource(_id, self.resource_type, "Cannot update deprecated rules")

        if resource["name"] in self.immutable_rule_names:
            raise SkipResource(_id, self.resource_type, "This rule is immutable")

        # set the version correctly
        resource["version"] = matching_destination_rule["version"]

        # only certain fields can be updated on default rules
        if (
            resource.get("isDefault", False)
            or resource.get("isPartner", False)
            or resource.get("partnerIntegrationId", None)
        ):
            self.limit_resource(resource)

        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            resource,
        )

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_resource = self.config.state.destination[self.resource_type][_id]

        if destination_resource["name"] in self.immutable_rule_names:
            raise SkipResource(_id, self.resource_type, "This rule is immutable")

        if destination_resource.get("isDefault", False):
            raise SkipResource(_id, self.resource_type, "Default rule cannot be deleted")

        if destination_resource.get("isPartner", False):
            raise SkipResource(_id, self.resource_type, "Cannot delete partner rules")

        await destination_client.delete(self.resource_config.base_path + f"/{destination_resource['id']}")

    @staticmethod
    def limit_resource(resource):
        """Default and partner security rules have some fields that cannot be updated we need to remove them"""
        for field in [
            "message",
            "name",
            "hasExtendedTitle",
            "cases",
            "complianceSignalOptions",
            "filters",
            "options",
            "queries",
            "referenceTables",
            "thirdPartyCases",
            "type",
        ]:
            resource.pop(field, None)
        resource.pop("isDefault", None)
        resource.pop("isPartner", None)
        resource.pop("partnerIntegrationId", None)

    @staticmethod
    def handle_special_case_attr(resource):
        """Handle default ComplianceSignal attributes"""
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
        """Get the existing rules from the destination"""
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
