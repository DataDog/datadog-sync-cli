# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class IntegrationsSlackChannels(BaseResource):
    resource_type = "integrations_slack_channels"
    resource_config = ResourceConfig(
        base_path="/api/v1/integration/slack/configuration/accounts/{account_name}/channels",
        excluded_attributes=[
            "id",
        ]
    )
    # Additional Incidents specific attributes
    slack_account_name = "deepo"  # <-- to edit

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(
            self.resource_config.base_path.format(account_name=self.slack_account_name)
        ).json()
        # fabricate id == channel name as required by datadog_sync
        return [{"id": r["name"].strip('#'), **r} for r in resp]

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        if _id:
            # there is only one settings, ignoring id
            source_client = self.config.source_client
            resource = source_client.get(
                self.resource_config.base_path.format(account_name=self.slack_account_name) +
                f"/{_id}"
            ).json()

        resource = cast(dict, resource)
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = resource
        resp = destination_client.post(
            self.resource_config.base_path.format(account_name=self.slack_account_name),
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = resource
        resp = destination_client.post(
            # same id in source & destination: the channel name
            self.resource_config.base_path.format(account_name=self.slack_account_name) +
            f"/{_id}",
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            # same id in source & destination: the channel name
            self.resource_config.base_path.format(account_name=self.slack_account_name) +
            f"/{_id}"
        )


    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass
