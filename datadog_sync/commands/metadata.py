# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class CommandCapabilities:
    api_reads: bool
    api_writes: bool
    state_reads: bool
    state_writes: bool
    may_prompt: bool
    supports_plan: bool
    plan_command: Optional[str] = None

    def to_dict(self):
        return asdict(self)


COMMAND_CAPABILITIES: Dict[str, CommandCapabilities] = {
    "import": CommandCapabilities(True, False, True, True, False, False),
    "sync": CommandCapabilities(True, True, True, True, True, True, "diffs"),
    "diffs": CommandCapabilities(True, False, True, False, False, True, "diffs"),
    "migrate": CommandCapabilities(True, True, True, True, True, True, "diffs"),
    "reset": CommandCapabilities(True, True, True, True, True, False),
    "prune": CommandCapabilities(True, False, True, True, True, True, "prune --dry-run"),
}


@dataclass(frozen=True)
class OptionPolicy:
    category: str = "Advanced"
    sensitive: bool = False
    requires: Tuple[str, ...] = field(default_factory=tuple)
    conflicts_with: Tuple[str, ...] = field(default_factory=tuple)
    stdin_supported: bool = False


SENSITIVE_OPTIONS = {
    "config",
    "source_api_key",
    "source_app_key",
    "source_jwt",
    "destination_api_key",
    "destination_app_key",
    "destination_jwt",
    "aws_secret_access_key",
    "aws_session_token",
    "gcs_service_account_key_file",
    "azure_storage_account_key",
    "azure_storage_connection_string",
}

STORAGE_OPTIONS = {
    "storage_type",
    "source_resources_path",
    "destination_resources_path",
    "aws_access_key_id",
    "aws_bucket_name",
    "aws_bucket_key_prefix_source",
    "aws_bucket_key_prefix_destination",
    "aws_region_name",
    "aws_secret_access_key",
    "aws_session_token",
    "gcs_bucket_name",
    "gcs_bucket_key_prefix_source",
    "gcs_bucket_key_prefix_destination",
    "gcs_service_account_key_file",
    "azure_container_name",
    "azure_container_key_prefix_source",
    "azure_container_key_prefix_destination",
    "azure_storage_account_name",
    "azure_storage_account_key",
    "azure_storage_connection_string",
}
RESOURCE_OPTIONS = {"resources", "resource", "filter", "filter_file", "filter_operator", "id_file"}
EXECUTION_OPTIONS = {
    "max_workers",
    "max_workers_per_type",
    "worker_limit",
    "max_concurrent_reads",
    "http_client_retry_timeout",
    "http_client_timeout",
    "transient_failure_threshold_pct",
    "validate",
    "no_validate",
    "send_metrics",
    "no_send_metrics",
}
OUTPUT_OPTIONS = {"emit_json", "root_emit_json", "verbose", "show_progress_bar", "no_show_progress_bar"}
SAFETY_OPTIONS = {
    "cleanup",
    "force",
    "dry_run",
    "do_not_backup",
    "verify_ddr_status",
    "no_verify_ddr_status",
    "verify_ssl_certificates",
    "no_verify_ssl_certificates",
    "create_global_downtime",
    "no_create_global_downtime",
    "read_only",
    "non_interactive",
    "yes",
}
REQUIRES = {
    "minimize_reads": ("resource_per_file", "resources|resource"),
    "skip_state_load": ("resource_per_file", "resources|resource"),
    "id_file": ("resources|resource",),
}
CONFLICTS = {
    "minimize_reads": ("skip_state_load",),
    "skip_state_load": ("minimize_reads",),
}


def option_policy(name: str) -> OptionPolicy:
    category = "Advanced"
    if name in STORAGE_OPTIONS:
        category = "Storage"
    elif name.startswith(("source_", "destination_")) and name not in {
        "source_resources_path",
        "destination_resources_path",
    }:
        category = "Credentials"
    elif name in RESOURCE_OPTIONS:
        category = "Resource selection"
    elif name in EXECUTION_OPTIONS:
        category = "Execution"
    elif name in OUTPUT_OPTIONS:
        category = "Output"
    elif name in SAFETY_OPTIONS:
        category = "Safety"
    return OptionPolicy(
        category=category,
        sensitive=name in SENSITIVE_OPTIONS,
        requires=REQUIRES.get(name, ()),
        conflicts_with=CONFLICTS.get(name, ()),
        stdin_supported=name in {"id_file", "filter_file"},
    )
