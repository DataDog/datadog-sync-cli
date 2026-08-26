# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast
from copy import deepcopy

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class Notebooks(BaseResource):
    resource_type = "notebooks"
    resource_config = ResourceConfig(
        base_path="/api/v1/notebooks",
        excluded_attributes=[
            "id",
            "attributes.cells.id",
            "attributes.created",
            "attributes.modified",
            "attributes.author",
            "attributes.metadata",
        ],
        non_nullable_attr=["attributes.schema_version"],
        null_values={
            "schema_version": [0],
        },
        skip_resource_mapping=True,
        # The lightweight LIST endpoint omits attributes.cells. Filters that
        # reference cells.* are list-unsafe and are deferred to the post-GET
        # pass in base_resource._import_resource (which evaluates --filter
        # against the full body, raising FilteredResource on rejection).
        # Metadata filters like --filter Type=notebooks;Name=attributes.name
        # continue to short-circuit at LIST-time on the cheap per-page
        # response. Without this, a positive filter on attributes.cells.*
        # would silently no-op against the cell-less LIST item (missing path
        # → False at filter.py:_is_match_helper).
        list_omitted_attr_prefixes=["attributes.cells"],
    )
    # Additional Notebooks specific attributes
    pagination_config = PaginationConfig(
        page_size=100,
        page_size_param="count",
        page_number_param="start",
        remaining_func=lambda idx, resp, page_size, page_number: (resp["meta"]["page"]["total_count"])
        - (page_size * (idx + 1)),
        page_number_func=lambda idx, page_size, page_number: page_size * (idx + 1),
    )

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        # LIST without include_cells: returns notebook metadata only. The per-page
        # response with include_cells=true grows with cell count and dominates the
        # discovery-phase wall-clock on populated orgs (one user's full cell payload
        # per page). import_resource() now fetches each notebook individually below,
        # matching the dashboards pattern, which parallelises cleanly under
        # --max-workers and bounds the per-request payload to a single notebook.
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path, pagination_config=self.pagination_config
        )

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        import_id = _id if _id is not None else (resource or {}).get("id")
        if import_id is None:
            raise ValueError("import_resource requires either _id or resource['id']")

        # Short-circuit when the caller already supplied a full body (cells
        # present). This is the --id-file path: get_resources_by_ids
        # (base_resource.py) already did the per-id GET and stored the body
        # in tmp_storage; the queue handler then calls _import_resource with
        # that body. Without this guard we would GET each notebook a second
        # time, doubling rate-limit pressure on id-file runs. Detection is by
        # cells presence — the lightweight LIST never has cells, so a
        # resource with attributes.cells came from a per-id GET.
        if resource is not None and isinstance(resource.get("attributes"), dict) and "cells" in resource["attributes"]:
            resource = cast(dict, resource)
            self.handle_special_case_attr(resource)
            return str(resource["id"]), resource

        try:
            resource = (await source_client.get(self.resource_config.base_path + f"/{import_id}"))["data"]
        except CustomClientHTTPError as err:
            # 403: notebook is in the LIST but restricted from per-id reads. Skip
            # rather than hard-fail so a single ACL'd notebook does not poison the
            # whole import run. Mirrors dashboards.import_resource.
            # 404: notebook was deleted between LIST enumeration and the per-id GET.
            # Skip — there is nothing to import.
            if err.status_code == 403:
                raise SkipResource(import_id, self.resource_type, "No access to restricted notebook")
            if err.status_code == 404:
                raise SkipResource(
                    import_id,
                    self.resource_type,
                    "Notebook deleted between list and fetch",
                )
            raise

        resource = cast(dict, resource)
        self.handle_special_case_attr(resource)

        # State writes go through str(_id) (base_resource._import_resource); the
        # API returns id as an int for notebooks. Cast here so callers and tests
        # see a consistent string contract, matching dashboards.
        return str(resource["id"]), resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        payload = {"data": self._sanitize_for_api(resource)}
        resp = await destination_client.post(self.resource_config.base_path, payload)
        self._restore_state_attrs(resp["data"], resource)
        self.handle_special_case_attr(resp["data"])

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        payload = {"data": self._sanitize_for_api(resource)}
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            payload,
        )
        self._restore_state_attrs(resp["data"], resource)
        self.handle_special_case_attr(resp["data"])

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    # Server-managed AI usage tag keys injected by the Notebooks API on every write.
    # These reflect per-org interaction history (MCP vs human), not notebook content,
    # and must be stripped to avoid non-converging diffs during sync.
    _ai_usage_tag_keys = frozenset({"ai_generated", "ai_edited", "human_edited"})

    @staticmethod
    def handle_special_case_attr(resource):
        # Handle template_variables attribute
        if "template_variables" in resource["attributes"] and not resource["attributes"]["template_variables"]:
            resource["attributes"].pop("template_variables")

        # Strip server-managed AI usage tags
        tags = resource["attributes"].get("tags")
        if tags:
            resource["attributes"]["tags"] = [t for t in tags if t.split(":")[0] not in Notebooks._ai_usage_tag_keys]

    # ------------------------------------------------------------------
    # API-payload sanitization + state restoration
    # ------------------------------------------------------------------
    # Two constructs in real source notebooks are rejected by the destination
    # Notebooks API with 400 Bad Request, so the resource can never be synced:
    #
    #   1. `attributes.time` with start == end
    #      -> {"errors":["API input validation failed: {'time': {'_schema':
    #          ['Start time ... must be less than end time ...']}}"]}
    #   2. a `sort` object inside a cell's `transformations` list carrying an
    #      extra `"type":"sort"` key
    #      -> {"errors":["API input validation failed: {'cells': {N: {'errors':
    #          [{'detail': "$.query.query.transformations[0].sort: Additional
    #          properties are not allowed ('type' was unexpected)"...}]}}}"]}
    #
    # The fix sends an API-safe *copy* to the destination (nudge start back
    # one second; drop the offending `type` key) but leaves the resource
    # stored in state untouched. Because the API stores the sanitized values,
    # the raw response would differ from the source on exactly these fields
    # and re-diff every run; `_restore_state_attrs` copies the original
    # source values back onto the response before it is written to
    # destination state, so source and destination state compare equal and
    # the resource does not update every run.

    @staticmethod
    def _subtract_one_second(iso_str: str) -> str:
        # datetime.fromisoformat (3.7+) does not accept a trailing 'Z' until
        # 3.11; normalize so we work on every supported interpreter.
        normalized = iso_str.replace("Z", "+00:00") if iso_str.endswith("Z") else iso_str
        return (datetime.fromisoformat(normalized) - timedelta(seconds=1)).isoformat()

    @staticmethod
    def _sanitize_for_api(resource: Dict) -> Dict:
        """Return a deep-copied, API-safe copy of `resource` for POST/PUT.

        Mutates only the copy; the caller's `resource` (used for state and
        diffing) is left byte-for-byte unchanged.
        """
        payload = deepcopy(resource)
        attrs = payload.get("attributes", {})

        # 1. time window: nudge start back one second when start == end so the
        #    API's `start < end` validation passes.
        time = attrs.get("time")
        if isinstance(time, dict) and time.get("start") and time.get("end") and time["start"] == time["end"]:
            time["start"] = Notebooks._subtract_one_second(time["start"])

        # 2. strip the rejected `"type":"sort"` key from `sort` objects
        #    anywhere in the cells tree (the API rejects it as an additional
        #    property on transformation sort objects).
        Notebooks._strip_sort_type(attrs.get("cells"))

        return payload

    @staticmethod
    def _strip_sort_type(node) -> None:
        """Recursively remove `type` from `sort` dicts whose value is "sort".

        Only transformation sort objects carry `"type":"sort"`; other sort
        shapes (e.g. `{"count": N, "order_by": [...]}`) do not, so this
        is a no-op on them.
        """
        if isinstance(node, dict):
            sort = node.get("sort")
            if isinstance(sort, dict) and sort.get("type") == "sort":
                sort.pop("type", None)
            for value in node.values():
                Notebooks._strip_sort_type(value)
        elif isinstance(node, list):
            for item in node:
                Notebooks._strip_sort_type(item)

    @staticmethod
    def _restore_state_attrs(dest: Dict, source: Dict) -> None:
        """Copy the sanitized fields back from `source` onto the API response
        `dest` so destination state compares equal to source state.

        The destination API stores the sanitized payload (start nudged, sort
        `type` dropped) and returns those values; without restoration the
        destination would diff from the source every run and trigger a
        perpetual update. We restore exactly the fields we sanitized:
        `attributes.time` and the `type` key on `sort` objects.
        """
        dest_attrs = dest.get("attributes", {})
        source_attrs = source.get("attributes", {})

        if "time" in source_attrs:
            dest_attrs["time"] = deepcopy(source_attrs["time"])

        Notebooks._restore_sort_type(source_attrs.get("cells"), dest_attrs.get("cells"))

    @staticmethod
    def _restore_sort_type(src, dst) -> None:
        """Walk `src` and `dst` in parallel; where `src` has a `sort` dict
        carrying a `type` key, stamp that `type` onto the parallel `sort`
        dict in `dst`.

        The API strips `type` from sort on read, so without this the
        destination sort would be missing `type` and diff from source. The
        walk assumes structural parity between source and the API response
        (same keys / list lengths), which holds because the response echoes
        the payload we sent.
        """
        if isinstance(src, dict) and isinstance(dst, dict):
            src_sort = src.get("sort")
            if isinstance(src_sort, dict) and "type" in src_sort:
                dst_sort = dst.get("sort")
                if isinstance(dst_sort, dict):
                    dst_sort["type"] = src_sort["type"]
            for key, src_value in src.items():
                if key in dst:
                    Notebooks._restore_sort_type(src_value, dst[key])
        elif isinstance(src, list) and isinstance(dst, list):
            for src_item, dst_item in zip(src, dst):
                Notebooks._restore_sort_type(src_item, dst_item)
