# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import abc
import asyncio
from asyncio import Lock
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, ClassVar, Optional, Dict, List, Tuple, Union

from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import (
    DEFAULT_TAGS,
    CustomClientHTTPError,
    SkipResource,
    find_attr,
    ResourceConnectionError,
)
from datadog_sync.constants import Metrics

if TYPE_CHECKING:
    from datadog_sync.utils.configuration import Configuration


@dataclass
class TaggingConfig:
    path: str
    path_list: List[str] = field(init=False, default_factory=list)
    default_tags: ClassVar[List[str]] = DEFAULT_TAGS

    def __post_init__(self) -> None:
        self.path_list = self.path.split(".")

    def add_default_tags(self, resource: Dict) -> None:
        tmp = resource
        for p in self.path_list:
            if tmp is None:
                break

            val = tmp.get(p, None)
            if p == self.path_list[-1]:
                if val is None:
                    tmp[p] = self.default_tags
                    return
                else:
                    tmp[p].extend(self.default_tags)
                    return
            else:
                tmp = val


@dataclass
class ResourceConfig:
    base_path: str
    resource_connections: Optional[Dict[str, List[str]]] = None
    non_nullable_attr: Optional[List[str]] = None
    null_values: Optional[Dict[str]] = None
    excluded_attributes: Optional[List[str]] = None
    concurrent: bool = True
    deep_diff_config: dict = field(default_factory=lambda: {"ignore_order": True})
    tagging_config: Optional[TaggingConfig] = None
    async_lock: Optional[Lock] = None
    non_nullable_list_vals: Optional[List[Tuple[str, Dict[str, str]]]] = None
    resource_mapping_key: Optional[Union[str, Callable[[Dict], str]]] = None
    skip_resource_mapping: bool = False

    async def init_async(self) -> None:
        self.async_lock = Lock()

    def __post_init__(self) -> None:
        self.build_excluded_attributes()
        if not self.concurrent:
            self.async_lock = Lock()

    def build_excluded_attributes(self) -> None:
        if self.excluded_attributes:
            for i, attr in enumerate(self.excluded_attributes):
                self.excluded_attributes[i] = "root" + "".join(["['{}']".format(v) for v in attr.split(".")])


class BaseResource(abc.ABC):
    resource_type: str
    resource_config: ResourceConfig

    def __init__(self, config: Configuration) -> None:
        self.config = config
        self._existing_resources_map: Dict[str, Dict] = {}
        if not self.resource_config.skip_resource_mapping and self.resource_config.resource_mapping_key is None:
            raise ValueError(
                f"Resource {self.resource_type} has skip_resource_mapping=False "
                f"but resource_mapping_key is not defined"
            )

    async def init_async(self):
        await self.resource_config.init_async()

    def get_resource_mapping_key(self, resource: Dict) -> Optional[str]:
        """Extract the mapping key from a resource using resource_mapping_key config.

        Supports dot-path strings (e.g. "attributes.email") and callables.
        Returns None if resource_mapping_key is not configured, if the dot-path
        traversal encounters a missing key, if the terminal value is None, or if
        a callable raises an exception.
        """
        key_config = self.resource_config.resource_mapping_key
        if key_config is None:
            return None
        if callable(key_config):
            try:
                return key_config(resource)
            except (KeyError, TypeError, AttributeError):
                return None
        # Dot-path traversal
        tmp = resource
        for part in key_config.split("."):
            if not isinstance(tmp, dict) or part not in tmp:
                return None
            tmp = tmp[part]
        if tmp is None:
            return None
        return str(tmp)

    async def map_existing_resources(self) -> None:
        """Fetch destination resources and populate _existing_resources_map.

        Default implementation fetches all destination resources via
        self.get_resources(destination_client) and builds _existing_resources_map
        keyed by resource_mapping_key.

        Tier 2 resources override this for custom fetch/mapping logic.
        """
        dest_resources = await self.get_resources(self.config.destination_client)
        self._existing_resources_map = {}
        for resource in dest_resources:
            key = self.get_resource_mapping_key(resource)
            if key is not None:
                self._existing_resources_map[key] = resource

    @abc.abstractmethod
    async def get_resources(self, client: CustomClient) -> List[Dict]:
        pass

    async def _get_resources(self, client: CustomClient) -> List[Dict]:
        r = self.get_resources(client)
        return await r

    async def get_resources_by_ids(
        self,
        client: CustomClient,
        ids: List[str],
        max_concurrent_reads: int = 10,
    ) -> Tuple[List[Dict], List[str], List[Tuple[str, str, str]]]:
        """Fetch specific resources by ID instead of listing all.

        Returns: (resources, missing_ids, errored_ids)
          - resources: successfully-fetched resource dicts
          - missing_ids: IDs returning HTTP 404 (resource deleted between enum and fetch)
          - errored_ids: list of (id, class, reason) where class in {"transient","permanent","skipped"}

        Reuses the model's existing import_resource(_id=...) primitive, which inherits
        per-model semantics (envelope unwrap, SkipResource, etc.). Concurrency bounded by
        asyncio.Semaphore(max_concurrent_reads), separate from --max-workers.

        Models whose import_resource has the `if _id:` guard pattern (monitors, SLOs,
        notebooks, downtimes, synthetics) work with this default impl. Dashboards is an
        exception: its import_resource always GETs to fetch widget bodies that the list
        endpoint omits. For dashboards, this default still WORKS (each call fetches once);
        it just doesn't realize the simplification benefit.
        """
        sem = asyncio.Semaphore(max_concurrent_reads)
        resources: List[Dict] = []
        missing: List[str] = []
        errored: List[Tuple[str, str, str]] = []

        # Lazy import: aiohttp is already a sync-cli dependency (custom_client.py)
        # but importing at module load creates a heavier import graph for tests.
        import aiohttp

        async def fetch_one(id_: str):
            async with sem:
                try:
                    _, resource = await self.import_resource(_id=id_)
                    return ("ok", resource)
                except SkipResource as e:
                    return ("skipped", id_, str(e))
                except CustomClientHTTPError as e:
                    if e.status_code == 404:
                        return ("missing", id_)
                    if e.status_code == 429 or e.status_code >= 500:
                        return ("transient", id_, f"HTTP {e.status_code}")
                    return ("permanent", id_, f"HTTP {e.status_code}")
                except (asyncio.TimeoutError,):
                    return ("transient", id_, "timeout")
                except aiohttp.ClientError as e:
                    # Includes ClientConnectionError (DNS, connection refused, TCP reset),
                    # ServerDisconnectedError, etc. These are transport-shaped failures
                    # that downstream consumers should treat the same as 5xx/429.
                    return ("transient", id_, f"connection error: {type(e).__name__}")
                except Exception as e:
                    # request_with_retry() in custom_client.py raises a plain Exception
                    # with the literal prefix "retry limit exceeded" when its retry budget
                    # is exhausted without an inner ClientResponseError firing first.
                    # Treat that case as transient so sustained-throttling surfaces as
                    # rate-limit-shaped exit, not silent partial success.
                    # NOTE: substring match couples to a log-message format. The companion
                    # test test_request_with_retry_message_contract pins the message shape
                    # so a refactor in custom_client.py will fail the test rather than
                    # silently demote retry-budget exhaustion back to permanent.
                    msg = str(e)
                    if "retry limit exceeded" in msg:
                        return ("transient", id_, msg[:200])
                    return ("permanent", id_, msg[:200])

        results = await asyncio.gather(*(fetch_one(i) for i in ids))
        for r in results:
            tag = r[0]
            if tag == "ok":
                resources.append(r[1])
            elif tag == "missing":
                missing.append(r[1])
            elif tag == "skipped":
                errored.append((r[1], "skipped", r[2]))
            elif tag == "transient":
                errored.append((r[1], "transient", r[2]))
            else:
                errored.append((r[1], "permanent", r[2]))
        return resources, missing, errored

    @abc.abstractmethod
    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        pass

    async def _import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> str:
        _id, r = await self.import_resource(_id, resource)

        if self.resource_config.tagging_config is not None:
            try:
                self.resource_config.tagging_config.add_default_tags(r)
            except Exception as e:
                self.config.logger.warning(
                    f"Error while adding default tags to resource {self.resource_type}. {str(e)}"
                )

        self.config.state.source[self.resource_type][str(_id)] = r
        return str(_id)

    @abc.abstractmethod
    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def _pre_resource_action_hook(self, _id, resource: Dict) -> None:
        return await self.pre_resource_action_hook(_id, resource)

    @abc.abstractmethod
    async def pre_apply_hook(self) -> None:
        pass

    async def _pre_apply_hook(self) -> None:
        return await self.pre_apply_hook()

    @abc.abstractmethod
    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        pass

    async def _create_resource(self, _id: str, resource: Dict) -> None:
        _id, r = await self.create_resource(_id, resource)
        self.config.state.destination[self.resource_type][_id] = r

    @abc.abstractmethod
    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        pass

    async def _update_resource(self, _id: str, resource: Dict) -> None:
        _id, r = await self.update_resource(_id, resource)
        self.config.state.destination[self.resource_type][_id] = r

    @abc.abstractmethod
    async def delete_resource(self, _id: str) -> None:
        pass

    async def _delete_resource(self, _id: str) -> None:
        try:
            await self.delete_resource(_id)
        except CustomClientHTTPError as e:
            if e.status_code == 404:
                self.config.state.destination[self.resource_type].pop(_id, None)
                return None

            raise e

        self.config.state.destination[self.resource_type].pop(_id, None)

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        resources = self.config.state.destination[resource_to_connect]

        failed_connections = []
        if isinstance(r_obj[key], list):
            for i, v in enumerate(r_obj[key]):
                _id = str(v)
                if _id in resources:
                    # Cast resource id to str or int based on source type
                    type_attr = type(v)
                    r_obj[key][i] = type_attr(resources[_id]["id"])
                else:
                    failed_connections.append(_id)
        else:
            _id = str(r_obj[key])
            if _id in resources:
                # Cast resource id to str on int based on source type
                type_attr = type(r_obj[key])
                r_obj[key] = type_attr(resources[_id]["id"])
            else:
                failed_connections.append(_id)

        return failed_connections

    def extract_source_ids(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        """Extract dependency IDs referenced at r_obj[key] for resource_to_connect.

        Source-only: does NOT check destination state, does NOT mutate r_obj.
        Override in subclasses with custom connect_id logic (regex, prefix
        parsing, type dispatch, etc.).
        Mirror of connect_id -- keep in sync when connect_id changes.
        """
        if not r_obj.get(key):
            return None
        if isinstance(r_obj[key], list):
            return [str(v) for v in r_obj[key]]
        return [str(r_obj[key])]

    def connect_resources(self, _id: str, resource: Dict) -> None:
        if not self.resource_config.resource_connections:
            return

        failed_connections_dict = defaultdict(list)
        for resource_to_connect, v in self.resource_config.resource_connections.items():
            for attr_connection in v:
                c = find_attr(attr_connection, resource_to_connect, resource, self.connect_id)
                if c:
                    failed_connections_dict[resource_to_connect].extend(c)

        if failed_connections_dict:
            e = ResourceConnectionError(failed_connections_dict=failed_connections_dict)
            if not self.config.skip_failed_resource_connections:
                e = ResourceConnectionError(failed_connections_dict=failed_connections_dict)
                self.config.logger.info(f"skipping resource: {str(e)}", _id=_id, resource_type=self.resource_type)
                raise e
            else:
                self.config.logger.debug(f"{str(e)}", _id=_id, resource_type=self.resource_type)

    def filter(self, resource: Dict) -> bool:
        if not self.config.filters or self.resource_type not in self.config.filters:
            return True

        if self.config.filter_operator.lower() == "and":
            for _filter in self.config.filters[self.resource_type]:
                if not _filter.is_match(resource):
                    return False
            # All resources successfully matched
            return True
        else:
            # Fallback to 'OR' logic. Filter in if any filter applies to resource
            for _filter in self.config.filters[self.resource_type]:
                if _filter.is_match(resource):
                    return True
            # Filter was specified for resource type but resource did not match any
            return False

    async def _send_action_metrics(self, action: str, _id: str, status: str, tags: Optional[List[str]] = None) -> None:
        if not tags:
            tags = []
        if _id:
            tags.append(f"id:{_id}")
        tags.append(f"action_type:{action}")
        tags.append(f"status:{status}")
        tags.append(f"resource_type:{self.resource_type}")
        try:
            await self.config.destination_client.send_metric(Metrics.ACTION.value, tags + ["client_type:destination"])
            self.config.logger.debug(f"Sent metrics to destination for {self.resource_type}")
        except Exception as e:
            self.config.logger.debug(f"Failed to send metrics to destination for {self.resource_type}: {str(e)}")

        try:
            await self.config.source_client.send_metric(Metrics.ACTION.value, tags + ["client_type:source"])
            self.config.logger.debug(f"Sent metrics to source for {self.resource_type}")
        except Exception as e:
            self.config.logger.debug(f"Failed to send metrics to source for {self.resource_type}: {str(e)}")
