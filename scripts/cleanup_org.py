# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import os
import json
import re
from sys import exit

import requests


TEST_ORG_ENV_NAME = "DD_TEST_ORG"
DEFAULT_TEST_ORG = "13865f06-bfcc-11eb-8e11-da7ad0900005"


class Cleanup:
    def __init__(self):
        self.headers = get_headers()
        self.base_url = os.getenv("DD_DESTINATION_API_URL")

        # Validate test org
        self.validate_org()

        # Delete all supported resources
        self.cleanup_service_level_objectives()
        self.cleanup_slo_corrections()
        self.cleanup_synthetics_tests()
        self.cleanup_synthetics_private_locations()
        self.cleanup_synthetics_global_variables()
        self.cleanup_spans_metrics()
        self.cleanup_dashboard_lists()
        self.cleanup_dashboards()
        self.cleanup_downtimes()
        self.cleanup_downtime_schedules()
        self.cleanup_logs_custom_pipelines()
        self.cleanup_monitors()
        self.cleanup_notebooks()
        self.cleanup_users()
        self.cleanup_roles()
        self.cleanup_logs_metrics()
        self.cleanup_metric_tag_configurations()
        self.cleanup_host_tags()
        self.cleanup_logs_restriction_queries()
        # self.cleanup_integrations_aws()

    def validate_org(self):
        _id = os.getenv(TEST_ORG_ENV_NAME, DEFAULT_TEST_ORG)
        path = "/api/v1/org"
        url = f"{self.base_url}{path}/{_id}"

        try:
            resp = requests.get(url, headers=self.headers, timeout=60)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print("Error getting organization. Validate api+app keys %s: %s", url, e)
            exit(1)

    def cleanup_dashboards(
        self,
    ):
        path = "/api/v1/dashboard"
        res = self.get_resources(path)
        for resource in res["dashboards"]:
            self.delete_resource(resource["id"], path)

    def cleanup_dashboard_lists(
        self,
    ):
        path = "/api/v1/dashboard/lists/manual"
        res = self.get_resources(path)
        for resource in res["dashboard_lists"]:
            self.delete_resource(resource["id"], path)

    def cleanup_downtimes(
        self,
    ):
        path = "/api/v1/downtime"
        res = self.get_resources(path)
        for resource in res:
            if not resource["disabled"]:
                self.delete_resource(resource["id"], path)

    def cleanup_downtime_schedules(
        self,
    ):
        path = "/api/v2/downtime"
        res = self.get_resources(path)["data"]
        for resource in res:
            if not resource["attributes"]["canceled"]:
                self.delete_resource(resource["id"], path)

    def cleanup_logs_custom_pipelines(
        self,
    ):
        path = "/api/v1/logs/config/pipelines"
        res = self.get_resources(path)
        for resource in res:
            if not resource["is_read_only"]:
                self.delete_resource(resource["id"], path)

    def cleanup_monitors(
        self,
    ):
        path = "/api/v1/monitor"
        res = self.get_resources(path)
        for resource in res:
            if resource["type"] != "synthetics alert":
                self.delete_resource(resource["id"], path, params={"force": True})

    def cleanup_users(
        self,
    ):
        path = "/api/v2/users"
        res = self.get_resources(path, {"filter[status]": "Pending"})
        for resource in res["data"]:
            self.delete_resource(resource["id"], path)

    def cleanup_roles(
        self,
    ):
        path = "/api/v2/roles"
        res = self.get_resources(path)
        for resource in res["data"]:
            if resource["attributes"]["user_count"] == 0:
                self.delete_resource(resource["id"], path)

    def cleanup_synthetics_global_variables(
        self,
    ):
        path = "/api/v1/synthetics/variables"
        res = self.get_resources(path)
        for resource in res["variables"]:
            self.delete_resource(resource["id"], path)

    def cleanup_spans_metrics(
        self,
    ):
        path = "/api/v2/apm/config/metrics"
        res = self.get_resources(path)
        for resource in res["data"]:
            self.delete_resource(resource["id"], path)

    def cleanup_synthetics_private_locations(
        self,
    ):
        path = "/api/v1/synthetics/locations"
        delete_path = "/api/v1/synthetics/private-locations"
        pl_id = re.compile("^pl:.*")
        res = self.get_resources(path)
        for resource in res["locations"]:
            if pl_id.match(resource["id"]):
                self.delete_resource(resource["id"], delete_path)

    def cleanup_synthetics_tests(
        self,
    ):
        path = "/api/v1/synthetics/tests"
        res = self.get_resources(path)
        payload = {"public_ids": []}
        for resource in res["tests"]:
            payload["public_ids"].append(resource["public_id"])

        url = self.base_url + path + "/delete"
        if len(payload["public_ids"]) > 0:
            try:
                resp = requests.post(url, headers=self.headers, timeout=60, data=json.dumps(payload))
                resp.raise_for_status()
                print("deleted resource ", url, payload)
            except requests.exceptions.HTTPError as e:
                print("Error deleting resource: %s", e)

    def cleanup_service_level_objectives(self):
        path = "/api/v1/slo"
        res = self.get_resources(path)
        for resource in res["data"]:
            self.delete_resource(resource["id"], path, params={"force": True})

    def cleanup_slo_corrections(self):
        path = "/api/v1/slo/correction"
        res = self.get_resources(path)
        for resource in res["data"]:
            self.delete_resource(resource["id"], path)

    def cleanup_notebooks(self):
        path = "/api/v1/notebooks"
        res = self.get_resources(path)
        for resource in res["data"]:
            self.delete_resource(resource["id"], path)

    def cleanup_integrations_aws(
        self,
    ):
        path = "/api/v1/integration/aws"
        res = self.get_resources(path)
        for resource in res["accounts"]:
            url = self.base_url + path
            try:
                resp = requests.delete(
                    url,
                    headers=self.headers,
                    timeout=60,
                    data=json.dumps({"account_id": resource["account_id"], "role_name": resource["role_name"]}),
                )
                resp.raise_for_status()
                print("deleted resource ", url, resource["account_id"])
            except requests.exceptions.HTTPError as e:
                print("Error deleting resource: %s", e)

    def cleanup_logs_metrics(self):
        path = "/api/v2/logs/config/metrics"
        res = self.get_resources(path)
        for resource in res["data"]:
            self.delete_resource(resource["id"], path)

    def cleanup_metric_tag_configurations(self):
        path = "/api/v2/metrics"
        res = self.get_resources(path, params={"filter[configured]": "true"})
        for resource in res["data"]:
            self.delete_resource(f"{resource['id']}/tags", path)

    def cleanup_host_tags(self):
        path = "/api/v1/tags/hosts"
        res = self.get_resources(path)
        for host_list in res["tags"].values():
            for host in host_list:
                self.delete_resource(host, path)

    def cleanup_logs_restriction_queries(self):
        path = "/api/v2/logs/config/restriction_queries"
        res = self.get_resources(path)
        for logs_restriction_query in res["data"]:
            self.delete_resource(logs_restriction_query["id"], path)

    def get_resources(self, path, *args, **kwargs):
        url = self.base_url + path
        try:
            resp = requests.get(url, headers=self.headers, timeout=60, *args, **kwargs)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print("Error getting url %s: %s", url, e)
            return
        return resp.json()

    def delete_resource(self, _id, path, **kwargs):
        url = self.base_url + path
        try:
            resp = requests.delete(f"{url}/{_id}", headers=self.headers, timeout=60, **kwargs)
            resp.raise_for_status()
            print("deleted resource ", url, _id)
        except requests.exceptions.HTTPError as e:
            print("Error deleting resource: %s", e)


def get_headers():
    return {
        "DD-API-KEY": os.getenv("DD_DESTINATION_API_KEY"),
        "DD-APPLICATION-KEY": os.getenv("DD_DESTINATION_APP_KEY"),
        "Content-Type": "application/json",
    }


if __name__ == "__main__":
    Cleanup()
