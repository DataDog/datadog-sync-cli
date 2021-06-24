# datadog-sync-cli
Datadog cli tool to sync resources across organizations.

# Table of Contents
- [Purpose](#purpose)
- [Requirements](#requirements)
- [Usage](#usage)
  - [Filtering](#filtering)
- [Supported resources](#supported-resources)
- [Best Practices](#best-practices)

## Purpose

The purpose of the datadog-sync-cli package is providing an easy way to sync resources across Datadog organizations.

## Requirements

- Python >= v3.7

## Usage

```
Usage: datadog-sync [OPTIONS] COMMAND [ARGS]...

  Initialize cli

Options:
  --source-api-key TEXT                 Datadog source organization API key. [required]
  --source-app-key TEXT                 Datadog source organization APP key. [required]
  --source-api-url TEXT                 Datadog source organization API url.
  --destination-api-key TEXT            Datadog destination organization API key. [required]
  --destination-app-key TEXT            Datadog destination organization APP key. [required]
  --destination-api-url TEXT            Datadog destination organization API url.
  --http-client-retry-timeout INTEGER   The HTTP request retry timeout period. Defaults to `60s`.
  --resources TEXT                      Optional comma separated list of resource to
                                        import. All supported resources are imported
                                        by default.
  -v, --verbose                         Enable verbose logging.
  --filter TEXT                         Filter imported resources. See [Filtering] section for more details
  --config FILE                         Read configuration from FILE.
  --help                                Show this message and exit.

Commands:
  diffs   Log resource diffs.
  import  Import Datadog resources.
  sync    Sync Datadog resources to destination.
```
#### Filtering

Datadog sync cli tool supports filtering resources during import. Multiple filter flags can be passed. 

Filter option accepts a string made up of `key=value` pairs separated by `;`. For example
```
--filter 'Type=<resource>;Name=<attribute_name>;Value=<attribute_value>;Operator=<operator>'
```
Available keys:

- `Type`: Resource e.g. Monitors, Dashboards, etc. [required]
- `Name`: Attribute key to filter on. This can be any top level key in the individual resources retrieved from their respective list all endpoints. [required]
  - For example: Dashboards [list all endpoint](https://docs.datadoghq.com/api/latest/dashboards/#get-all-dashboards) returns dashboard summary response which contains the following attributes available for filtering: `author_handle, created_at, description, id, is_read_only, layout_type, modified_at, title, url`
- `Value`: Attribute value to filter by. [required]
- `Operator`: Available operators are below. All invalid operator's default to `ExactMatch`.
  - `SubString`: Sub string matching
  - `ExactMatch`: Exact string match.

### Using the package

1) Clone the project repo
2) CD into the repo directory and install the datadog-sync-cli via `pip install .`
3) Run cli tool `datadog-sync <options> <command>`

### Using the package with docker
1) Clone the project repo
2) CD into the repo directory and build the docker image `docker build . -t datadog-sync`
3) Run the docker image using entrypoint below:
```
docker run --rm -v $(pwd):/datadog-sync:rw \
  -e DD_SOURCE_API_KEY=<DATADOG_API_KEY> \
  -e DD_SOURCE_APP_KEY=<DATADOG_APP_KEY> \
  -e DD_SOURCE_API_URL=<DATADOG_API_URL> \
  -e DD_DESTINATION_API_KEY=<DATADOG_API_KEY> \
  -e DD_DESTINATION_APP_KEY=<DATADOG_APP_KEY> \
  -e DD_DESTINATION_API_URL=<DATADOG_API_URL> \
  datadog-sync:latest <options> <command>
```
Note: The above docker run command will mount your current working directory to the container.

## Supported resources

- **roles**
- **users**
- **synthetics_private_locations**
- **synthetics_tests**
- **synthetics_global_variables**
- **monitors**
- **downtimes**
- **service_level_objectives**
- **dashboards**
- **dashboard_lists**
- **logs_custom_pipelines**

## Best practices

Many Datadog resources are interdependent. For example, Users resource references Roles and Dashboards can include widgets which use Monitors or Synthetics. To ensure these dependencies are not broken, the datadog-sync tool imports and syncs these in a specific order. See the order(top -> bottom) in the [Supported resources](#supported-resources) section below.

If importing/syncing resources individually, ensure resource dependencies are imported and synced as well:

Resource                      | Dependencies
---                           | ---
roles                         | -
users                         | roles
synthetics_private_locations  | -
synthetics_tests              | synthetics_private_locations
synthetics_global_variables   | synthetics_tests
monitors                      | roles
downtimes                     | monitors
dashboards                    | monitors, roles, service_level_objectives
dashboard_lists               | dashboards
service_level_objectives      | monitors, synthetics_tests
logs_custom_pipelines         | -
