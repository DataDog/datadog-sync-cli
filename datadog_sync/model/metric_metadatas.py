# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class MetricMetadatas(BaseResource):
    resource_type = "metric_metadatas"
    resource_config = ResourceConfig(
        base_path="/api/v1/metrics",
        excluded_attributes=["integration"],
    )
    # Additional MetricMetadatas specific attributes
    destination_metric_metadatas: Dict[str, Dict] = dict()

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get("/api/v2/metrics").json()["data"]

        # cleanup "type": "metrics",
        for metric in resp:
            del metric['type']

        # return objects with only "id" field
        return resp

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        if resource.keys() == {'id'}:
            # we get only the id from the metrics list, force getting metric metadata individually
            _id = resource['id']
        if _id:
            source_client = self.config.source_client
            resource = source_client.get(self.resource_config.base_path + f"/{_id}").json()
            resource['id'] = _id

        resource = cast(dict, resource)
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        self.destination_metric_metadatas = self.get_destination_metric_metadatas()

    def create_resource(self, _id: str, resource: Dict) -> None:
        if _id in self.destination_metric_metadatas:
            self.resource_config.destination_resources[_id] = self.destination_metric_metadatas[_id]
            self.update_resource(_id, resource)
            return

        raise Exception("creating metric_metadatas is not supported: push data-points to it and the rerun (it will then update it instead of trying to create)")

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = resource
        resp = destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            payload,
        ).json()
        resp['id'] = self.resource_config.destination_resources[_id]['id']

        self.resource_config.destination_resources[_id] = resp

    def delete_resource(self, _id: str) -> None:
        raise Exception("deleting metric_metadatas is not supported")

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass

    def get_destination_metric_metadatas(self) -> Dict[str, Dict]:
        destination_metric_metadatas = {}
        destination_client = self.config.destination_client

        resp = self.get_resources(destination_client)
        for metric_metadata in resp:
            destination_metric_metadatas[metric_metadata["id"]] = metric_metadata

        return destination_metric_metadatas
