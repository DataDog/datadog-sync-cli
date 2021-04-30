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
    }
  }
}
```
4) Run the sync command `datadog-sync sync`

## Supported resources

- **Role**: All roles with at least 1 associated user are synced
- **User**: User's assigned to Role `@DatadogSync` are synced
- **Monitor**: All monitors with tags `datadog:sync` are synced
- **Dashboards**: All dashboards in Dashboard Lists with a name including `@DatadogSync` are synced