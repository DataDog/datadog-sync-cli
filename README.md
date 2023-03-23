# datadog-sync-cli
Datadog cli tool to sync resources across organizations.

# Table of Contents
- [Purpose](#purpose)
- [Requirements](#requirements)
- [Installation](#Installation)
- [Usage](#usage)
  - [API URL](#api-url)
  - [Filtering](#filtering)
  - [Config File](#config-file)
- [Supported resources](#supported-resources)
- [Best Practices](#best-practices)

## Purpose

The purpose of the datadog-sync-cli package is providing an easy way to sync resources across Datadog organizations.

The source organization will not be modified, but the destination organization will have resources created and updated during by `sync` command.

## Requirements

- Python >= v3.9

## Installation

### Installing from source

1) Clone the project repo and CD into the directory `git clone https://github.com/DataDog/datadog-sync-cli.git; cd datadog-sync-cli`
2) Install datadog-sync-cli tool using pip `pip install .`
3) Invoke the cli tool using `datadog-sync <command> <options>`

### Installing from source

#### MacOS and Linux

1) Download the executable from [releases](https://github.com/DataDog/datadog-sync-cli/releases) page
2) Provide the executable with executable permission `chmod +x datadog-sync-cli-{system-name}-{machine-type}`
3) Move the executable to your bin directory `sudo mv datadog-sync-cli-{system-name}-{machine-type} /usr/local/bin/datadog-sync`
4) Invoke the cli tool using `datadog-sync <command> <options>`

#### Windows

1) Download the executable with extension `.exe` from [releases](https://github.com/DataDog/datadog-sync-cli/releases) page
2) Add the directory containing the `exe` file to your [path](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/path)
3) Invoke the cli tool in cmd/powershell using the file name ommiting the extention `datadog-sync-cli-windows-amd64 <command> <options>`

### Using docker and building the image
1) Clone the project repo and CD into the directory `git clone https://github.com/DataDog/datadog-sync-cli.git; cd datadog-sync-cli`
2) Build the probided Dockerfile `docker build . -t datadog-sync`
3) Run the docker image using entrypoint below:
```
docker run --rm -v $(pwd):/datadog-sync:rw \
  -e DD_SOURCE_API_KEY=<DATADOG_API_KEY> \
  -e DD_SOURCE_APP_KEY=<DATADOG_APP_KEY> \
  -e DD_SOURCE_API_URL=<DATADOG_API_URL> \
  -e DD_DESTINATION_API_KEY=<DATADOG_API_KEY> \
  -e DD_DESTINATION_APP_KEY=<DATADOG_APP_KEY> \
  -e DD_DESTINATION_API_URL=<DATADOG_API_URL> \
  datadog-sync:latest <command> <options>
```
Note: The above docker run command will mount your current working directory to the container.


## Usage

```
Usage: datadog-sync COMMAND [OPTIONS]

  Initialize cli

Options:
  --source-api-key TEXT                       Datadog source organization API key. [required for import]
  --source-app-key TEXT                       Datadog source organization APP key. [required for import]
  --source-api-url TEXT                       Datadog source organization API url.
  --destination-api-key TEXT                  Datadog destination organization API key. [required for sync/diffs]
  --destination-app-key TEXT                  Datadog destination organization APP key. [required for sync/diffs]
  --destination-api-url TEXT                  Datadog destination organization API url.
  --validate BOOLEAN                          Enables validation of the provided API
                                              during client initialization. On import,
                                              only source api key is validated. On
                                              sync/diffs, only destination api key is validated. [default: True]
  --http-client-retry-timeout INTEGER         The HTTP request retry timeout period. Defaults to `60s`.
  --resources TEXT                            Optional comma separated list of resource to
                                              import. All supported resources are imported
                                              by default. See [Filtering] section for more details.
  --cleanup [True|False|Force]                Cleanup resources from destination org. [default: False]
  -v, --verbose                               Enable verbose logging.
  --filter TEXT                               Filter imported resources. See [Filtering] section for more details.
  --filter-operator TEXT                      Filter operator when multiple filters are passed. Supports `AND` or `OR`.
  --config FILE                               Read configuration from FILE. See [Config] section for more details.
  --max-workers INTEGER                       Max number of workers when running
                                              operations in multi-threads. Defaults to the number of processors on the machine, multiplied by 5.
  --skip-failed-resource-connections BOOLEAN  Skip resource if resource connection fails. [default: True]  [sync + import only]
  --force-missing-dependencies                Force importing and syncing resources that
                                              could be potential dependencies to the
                                              requested resources. [sync only]
  --help                                      Show this message and exit.

Commands:
  diffs   Log resource diffs.
  import  Import Datadog resources.
  sync    Sync Datadog resources to destination.
```

#### API URL

Available URL's for the source and destination API URLs are:

- `https://api.datadoghq.com`
- `https://api.datadoghq.eu`
- `https://api.us5.datadoghq.com`
- `https://api.us3.datadoghq.com`
- `https://api.ddog-gov.com`

See https://docs.datadoghq.com/getting_started/site/ for all available regions.

#### Filtering

Filtering is done on two levels, at top resource level and per individual resource using `--resources` and `--filter` respectevily.

##### Top resources level filtering

By default all resources are imported, synced, etc. If you would like to perform actions on a specific top level resource, or subset of resources, use `--resources` option. For example, the command `datadog-sync import --resources="dashboard_lists,dashboards"` will import ALL dashboards and dashboard lists in your Datadog organization.

##### Per resource level filtering

Individual resources can be further filtered using the `--filter` flag. For example, the following command `datadog-sync import --resources="dashboards,dashboard_lists" --filter='Type=dashboard_lists;Name=name;Value=My custom list'`, will import ALL dashboards and ONLY dashboard lists with the `name` attribute equal to `My custom list`.

Filter option (`--filter`) accepts a string made up of `key=value` pairs separated by `;`.
```
--filter 'Type=<resource>;Name=<attribute_name>;Value=<attribute_value>;Operator=<operator>'
```
Available keys:

- `Type`: Resource e.g. Monitors, Dashboards, etc. [required]
- `Name`: Attribute key to filter on. This can be any attribute represented in dot notation (e.g. `attributes.user_count`). [required]
- `Value`: Attribute value to filter by. [required]
- `Operator`: Available operators are below. All invalid operator's default to `ExactMatch`.
  - `SubString`: Sub string matching
  - `ExactMatch`: Exact string match.

By default, if multiple filters are passed for the same resource, `OR` logic is applied to the filters. This behavior can be adjusted using the `--filter-operator` option.

#### Config file

Custom config textfile can be passed in place of options. Example config file:
```
# config

destination_api_url="https://api.datadoghq.eu"
destination_api_key="<API_KEY>"
destination_app_key="<APP_KEY>"
source_api_key="<API_KEY>"
source_app_key="<APP_KEY>"
source_api_url="https://api.datadoghq.com"
filter=["Type=Dashboards;Name=title;Value=Test screenboard", "Type=Monitors;Name=tags;Value=sync:true"]
```

Usage: `datadog-sync import --config config`

## Workflow

To use the tool, first run the `import` command, which will read the wanted items from the specified resources and save them locally into JSON files in the directory `resources/source`.

Then, you can run the `sync` command which will use that local cache (unless `--force-missing-dependencies` is passed) to create
the resources on the destination, and saves locally what has been pushed.

## Supported resources

- **roles**
- **users**
- **synthetics_private_locations**
- **synthetics_tests**
- **synthetics_global_variables**
- **monitors**
- **downtimes**
- **service_level_objectives**
- **slo_corrections**
- **dashboards**
- **dashboard_lists**
- **logs_custom_pipelines**
- **notebooks**
- **host_tags**
- **logs_indexes**
- **logs_metrics**
- **logs_restriction_queries**
- **metric_tag_configurations**

## Best practices

Many Datadog resources are interdependent. For example, Users resource references Roles and Dashboards can include widgets which use Monitors or Synthetics. To ensure these dependencies are not broken, the datadog-sync tool imports and syncs these in a specific order. See the order(top -> bottom) in the [Supported resources](#supported-resources) section below.

If importing/syncing resources individually, ensure resource dependencies are imported and synced as well:

| Resource                     | Dependencies                              |
|------------------------------|-------------------------------------------|
| roles                        | -                                         |
| users                        | roles                                     |
| synthetics_private_locations | -                                         |
| synthetics_tests             | synthetics_private_locations              |
| synthetics_global_variables  | synthetics_tests                          |
| monitors                     | roles                                     |
| downtimes                    | monitors                                  |
| service_level_objectives     | monitors, synthetics_tests                |
| slo_corrections              | service_level_objectives                  |
| dashboards                   | monitors, roles, service_level_objectives |
| dashboard_lists              | dashboards                                |
| logs_custom_pipelines        | -                                         |
| notebooks                    | -                                         |
| host_tags                    | -                                         |
| logs_indexes                 | -                                         |
| logs_metrics                 | -                                         |
| logs_restriction_queries     | roles                                     |
| metric_tag_configurations    | -                                         |
