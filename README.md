# datadog-sync-cli
Datadog cli tool to sync resources across organizations.

## Purpose

The purpose of the datadog-sync-cli package is providing an easy way to sync resources across Datadog organizations.

## Requirements

- Python >= v3.7

## Using the package

1) Clone the project repo
2) CD into the repo directory and install the datadog-sync-cli via `pip install .`
3) Run cli tool `datadog-sync <options> <command>`

## Using the package with docker
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

## Best practices

Many Datadog resources are interdependent. For example, Users resource references Roles and Dashboards can include widget which use Monitors or Synthetics resources. To ensure these dependencies are not broken, the datadog-sync tool imports and sync these resources in a specific order. See the order(top -> bottom) in the [Supported resources](#supported-resources).

If importing/syncing resources individually, ensure resource dependencies are imported and synced as well:

Resource                      | Dependencies
---                           | ---
roles                         | -
users                         | roles
monitors                      | roles
dashboards                    | monitors
downtimes                     | monitors
synthetics_tests              | synthetics_private_locations
synthetics_private_locations  | -
synthetics_global_variables   | synthetics_tests
logs_custom_pipelines         | -

## Supported resources

- **roles**
- **users**
- **monitors**
- **dashboards**
- **downtimes**
- **synthetics_tests**
- **synthetics_private_locations**
- **synthetics_global_variables**
- **logs_custom_pipelines**
