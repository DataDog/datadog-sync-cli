# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import json
import os
import re

from typing import TYPE_CHECKING, List, Dict, Optional, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig, TaggingConfig
from datadog_sync.utils.resource_utils import SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient

class SyntheticsPrivateLocations(BaseResource):
    resource_type = "synthetics_private_locations"
    resource_config = ResourceConfig(
        resource_connections={"roles": ["metadata.restricted_roles"]},
        base_path="/api/v1/synthetics/private-locations",
        excluded_attributes=[
            "id",
            "modifiedAt",
            "createdAt",
            "createdBy",
            "secrets",
            "config",
            "result_encryption",
            "ddr_metadata",
        ],
        tagging_config=TaggingConfig(path="tags"),
        skip_resource_mapping=True,
    )
    # Additional SyntheticsPrivateLocations specific attributes
    base_locations_path: str = "/api/v1/synthetics/locations"
    pl_id_regex: re.Pattern = re.compile("^pl:.*")

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.base_locations_path)

        return resp["locations"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        import_id = _id or resource["id"]

        if not self.pl_id_regex.match(import_id):
            raise SkipResource(import_id, self.resource_type, "Managed location.")

        resp = await source_client.get(
            self.resource_config.base_path + f"/{import_id}",
        )

        self.config.state.source[self.resource_type][import_id] = resp

        return import_id, resp

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        source_client = self.config.source_client

        # Fetch pl_info from source API for DDR metadata
        pl_info = await source_client.get(
            self.resource_config.base_path + f"/{_id}",
            params={"include_pl_info": "true"},
        )

        # Strip null metadata — DDR endpoint requires it to be an object
        if resource.get("metadata") is None:
            resource.pop("metadata", None)

        resource["ddr_metadata"] = {
            "disaster_recovery": {
                "source_pl_id": pl_info["pl_id"],
                "source_name": _id,
                "source_dc": pl_info["datacenter"],
                "source_org_id": pl_info["org_id"],
            }
        }
        # test_encryption_public_key expects the JSON-stringified public_key_test object
        resource["test_encryption_public_key"] = json.dumps(pl_info["public_key_test"])
        # result_encryption_public_key expects {"pem": ..., "fingerprint": ...}
        pub_key_result = pl_info["public_key_result"]
        resource["result_encryption_public_key"] = {
            "pem": pub_key_result["key"],
            "fingerprint": pub_key_result["id"],
        }
        if self.config.datadog_host_override:
            resource["datadog_host_override"] = self.config.datadog_host_override

        resp = await destination_client.post(self.resource_config.base_path, resource)

        # DDR response: {"private_location": {...}, "publicKeysByMainDC": {...}}
        pl = resp["private_location"]

        # Save PL config to file for later use running the PL
        pl_config = {
            "publicKeysByMainDC": resp.get("publicKeysByMainDC"),
        }
        if self.config.datadog_host_override:
            pl_config["datadogHostOverride"] = self.config.datadog_host_override
        self._save_pl_config(pl.get("name", _id), pl_config)

        return _id, pl

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            resource,
        )

        r = self.config.state.destination[self.resource_type][_id]
        r.update(resp)
        return _id, r

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(SyntheticsPrivateLocations, self).connect_id(key, r_obj, resource_to_connect)

    def _save_pl_config(self, pl_name: str, config: Dict) -> None:
        destination_path = self.config.state._storage.destination_resources_path
        config_dir = os.path.join(destination_path, "synthetics_private_locations_config")
        os.makedirs(config_dir, exist_ok=True)

        sanitized_name = re.sub(r"[^\w\-]", "_", pl_name)
        config_file = os.path.join(config_dir, f"{sanitized_name}.json")

        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
