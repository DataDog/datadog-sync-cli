# Changelog

## 0.4.1 / 2023-07-17
This release is same as 0.4.0. It includes the executable missing from version `0.4.0`

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/v0.4.0...0.4.1

## 0.4.0 / 2023-07-17

### Added
* Add filtering support to sync command by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/147
* Add support for syncing slo alert monitors by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/149
* Add support for `restricted_roles` in synthetics and add additional readOnly fields by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/153
### Changed
* Switch to use `scm_version` versioning by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/148

## New Contributors
* @abbasalizaidi made their first contribution in https://github.com/DataDog/datadog-sync-cli/pull/151

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/v0.3.1...0.4.0

## 0.3.1 / 2023-06-27

### Fixed
* Bump python base image in Dockerfile by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/139
* Import `exit` before usage by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/142


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/v0.3.0...0.3.1

## 0.3.0 / 2023-05-17

### Added
* Make request timeout configurable by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/136


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/v0.2.0...0.3.0

## 0.2.0 / 2023-05-09

### Fixed
* Fix syncing synthetics_tests with global variables by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/128
* Cleanup and update typing by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/129
* Fixes for `roles` and `synthetics_test` resources by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/130
### Added
* Add support for `spans_metrics` resource by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/131
### Changed
* Add formal sync order by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/115

## New Contributors
* @nkzou made their first contribution in https://github.com/DataDog/datadog-sync-cli/pull/125

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/v0.1.0...0.2.0

## 0.1.0 / 2023-03-21

* [Added] Initial beta release of the datadog-sync cli tool
