# datadog-sync-cli
Datadog cli tool to sync resources across organizations.

## Purpose

The purpose of the datadog-sync-cli package is providing an easy way to sync resources across Datadog organizations.

## Requirements

- Terraform ~< v0.12.x
- Terraformer >= v0.8.13
- Python >= v3.7

## Using the package

1) Clone the project repo
2) CD into the repo directory and install the datadog-sync-cli via `pip install .`
3) Initialize the Datadog terraform provider in an empty directory by placing the file below within it and running `terraform init`
```hcl
#### provider.tf
terraform {
  required_providers {
    datadog = {
      version = "~> 2.25.0"
      source  = "datadog/datadog"
    }
  }
}
```
4) Run the sync command `datadog-sync sync`

## Using the package with docker
1) Clone the project repo
2) CD into the repo directory and build the docker image `docker build ~/Dev/datadog-sync-cli -t datadog-sync`
3) Initialize the Datadog terraform provider in an empty directory by placing the file below within it and running `terraform init`
```hcl
#### provider.tf
terraform {
  required_providers {
    datadog = {
      version = "~> 2.25.0"
      source  = "datadog/datadog"
    }
  }
}
```
4) Run the docker image using entrypoint below:
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

- **Role**
- **User**
- **Monitor**
- **Dashboard_json**
- **Downtime**
