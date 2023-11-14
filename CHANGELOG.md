# Changelog

## 0.7.0 / 2023-11-14

### Fixed
* Pin `setuptools_scm` to < 8 by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/184
* Pass all keyword/ arguments to avoid panics with `setuptools_scm` > 8 by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/187
### Added
* Add `downtime_schedules` resource and deprecate `downtimes` by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/192

## New Contributors
* @alai97 made their first contribution in https://github.com/DataDog/datadog-sync-cli/pull/190

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.6.1...0.7.0

## 0.6.1 / 2023-09-19

### Fixed
* Fix monitors ID resolution and add support for new `burn_rate` SLO queries by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/182


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.6.0...0.6.1

## 0.6.0 / 2023-09-18

### Added
* Add new `logs_pipelines` resource and deprecate `logs_custom_pipelines` by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/176
* Add support for `logs_pipelines_order` resource by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/179


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.5.1...0.6.0

## 0.5.1 / 2023-09-13

### Fixed
* Bump PyInstaller and add tests by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/174


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.5.0...0.5.1

## 0.5.0 / 2023-08-24

### Added
* Add support for paginated monitors by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/167


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.4.2...0.5.0

## 0.4.2 / 2023-08-23

### Fixed
* Dump synthetics private location config data on create by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/164


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.4.1...0.4.2

## 0.4.1 / 2023-07-17
The release contents are same as `v0.4.0`. It includes the executables missing from version `v0.4.0`

Note: This release also drops the prefix `v` from release tag.

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/v0.3.1...0.4.1

## 0.4.0 / 2023-07-17

### Added
* Add filtering support to sync command by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/147
* Add support for syncing slo alert monitors by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/149
* Add support for `restricted_roles` in synthetics and add additional readOnly fields by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/153
### Changed
* Switch to use `scm_version` versioning by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/148

## New Contributors
* @abbasalizaidi made their first contribution in https://github.com/DataDog/datadog-sync-cli/pull/151

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/v0.3.1...v0.4.0

## 0.3.1 / 2023-06-27

### Fixed
* Bump python base image in Dockerfile by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/139
* Import `exit` before usage by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/142


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/v0.3.0...v0.3.1

## 0.3.0 / 2023-05-17

### Added
* Make request timeout configurable by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/136


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/v0.2.0...v0.3.0

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

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/v0.1.0...v0.2.0

## 0.1.0 / 2023-03-21

* [Added] Initial beta release of the datadog-sync cli tool
