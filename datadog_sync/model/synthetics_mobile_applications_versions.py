# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations

import aiohttp
import base64
import certifi
import hashlib
import ssl
import sys
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import SkipResource

class SyntheticsMobileApplicationsVersions(BaseResource):
    resource_type = "synthetics_mobile_applications_versions"
    resource_config = ResourceConfig(
        base_path="/api/unstable/synthetics/mobile/applications/versions",
        resource_connections={
            "synthetics_mobile_applications": ["versions.id", "application_id"],
            "synthetics_mobile_applications_versions_blobs": ["file_name"],
        },
        excluded_attributes=[
            "id",
            "created_at",
            "extracted_metadata",
            "file_name",
        ],
    )
    # Additional Synthetics Mobile Applications Versions specific attributes
    applications_path="/api/unstable/synthetics/mobile/applications"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        """
        Mobile Application Versions don't have a list endpoint of their own
        """
        resp = await client.get(self.applications_path)

        resources = []
        for application in resp["applications"]:
            for version in application["versions"]:
                _id = version["id"]
                resource = (await client.get(self.resource_config.base_path + f"/{_id}"))
                resources.append(resource)
        return resources

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))

        resource = cast(dict, resource)
        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def _create_mobile_version(self, _id: str, resource: Dict) -> Tuple[str, str]:
        """
        Before creating the version we need to upload the mobile applicaiton
        """
        self.config.logger.debug(f"create mobile app for resource: {_id}")

        # get the presigned url from source
        source_client = self.config.source_client
        presigned_download_url = (await source_client.post(self.resource_config.base_path + f"/{_id}/download", {}))
        presigned_download_url = presigned_download_url.replace("'", "")
        self.config.logger.debug(f"downloading from: {presigned_download_url}")

        # download the blob the blob
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context))
        async with session.get(presigned_download_url) as response:
            blob = await response.read()
        app_size = sys.getsizeof(blob)
        self.config.logger.debug(f"app_size: {app_size}")

        # chunk size, 5 MB is the minimum or googleapis throws errors
        chunk_size = 1024 * 1024 * 5

        # calculate parts
        parts = {
            "appSize": app_size,
            "parts": [],
        }
        num_of_parts = max(app_size // chunk_size, 1)
        self.config.logger.debug(f"num_of_parts: {num_of_parts}")
        for part_number in list(range(0, num_of_parts)):
            start = part_number * chunk_size
            end = (part_number + 1) * chunk_size
            if end > len(blob):
                end = len(blob)
            chunk = blob[start:end]
            md5_digest = base64.b64encode((hashlib.md5(chunk).digest())).decode('utf-8')
            part = {
                "md5": md5_digest,
                "partNumber": part_number + 1,
            }
            parts["parts"].append(part)
        self.config.logger.debug(f"parts: {parts}")

        # get multipart presigned urls
        source_application_id = resource["application_id"]
        destination_application_id = self.config.state.destination["synthetics_mobile_applications"][source_application_id]["id"]
        resp  = await self.config.destination_client.post(self.applications_path + f"/{destination_application_id}/multipart-presigned-urls", parts)

        file_name = resp["file_name"]
        upload_id = resp["multipart_presigned_urls_params"]["upload_id"]
        key = resp["multipart_presigned_urls_params"]["key"]
        urls = resp["multipart_presigned_urls_params"]["urls"]
        self.config.logger.debug(f"file_name: {file_name}")

        # post to multipart presigned urls
        complete_parts = []
        for part in parts["parts"]:
            headers = {
                "content-md5": part["md5"],
            }
            part_number = part["partNumber"]
            url = urls[str(part_number)]
            start = (part_number - 1) * chunk_size
            end = part_number * chunk_size
            chunk = blob[start:end]
            async with session.put(url=url,data=chunk,headers=headers) as response:
                _ = await response.read()
                if "Etag" in response.headers:
                    complete_parts.append({"PartNumber": int(part_number), "ETag": response.headers["Etag"].replace("\"","")})
                else:
                    raise SkipResource(_id, self.resource_type, f"Could not upload mobile application: {response}")
        await session.close()
        self.config.logger.debug("all parts uploaded")

        # complete multipart upload
        body = {
            "key": key,
            "uploadId": upload_id,
            "parts": complete_parts,
        }
        _  = await self.config.destination_client.post(self.applications_path + f"/{destination_application_id}/multipart-upload-complete", body)
        self.config.logger.debug("mobile app upload completed")
        return file_name, destination_application_id

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        file_name, application_id = await self._create_mobile_version(_id, resource)

        self.config.logger.debug(f"got file_name: {file_name} and application_id: {application_id}")
        resource["file_name"] = file_name
        resource["application_id"] = application_id
        destination_client = self.config.destination_client
        resp = await destination_client.post(self.resource_config.base_path, resource)
        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        destination_id = self.config.state.destination[self.resource_type][_id]["id"]

        # if the resource doesn't exist at the destination then create it
        existing_resources = await self.get_resources(destination_client)
        existing_resource_ids = {r["id"]:r for r in existing_resources}
        if destination_id not in existing_resource_ids:
            self.config.logger.debug(f"{destination_id} not found, creating it")
            return await self.create_resource(_id, resource)

        # resource exists so we can update it (only 2 fields are updatable)
        if resource["version_name"] == existing_resource_ids[destination_id]["version_name"] and resource["is_latest"] == existing_resource_ids[destination_id]["is_latest"]:
            raise SkipResource(_id, self.resource_type, f"No change to version fields")
            
        resp = await destination_client.put(
            self.resource_config.base_path + "/" + destination_id,
            {"version_name": resource["version_name"], "is_latest": resource["is_latest"]},
        )
        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass
