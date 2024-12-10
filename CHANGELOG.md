# Changelog

## 1.2.1 / 2024-12-10

### Fixed
* Remove `public_id` from synthetics sub step by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/302
### Added
* Add metric to mark when a command starts by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/300
* Add a reset command to remove destination resources by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/293
* Add security rules as a resource by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/304
### Changed
* Cleanup logging by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/303


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/1.1.1...1.2.1

## 1.1.1 / 2024-11-04

### Fixed
* Fix bugs found in tests that relate to API changes by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/298


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/1.1.0...1.1.1

## 1.1.0 / 2024-10-30

### Fixed
* Fix subdomain for integration log pipelines by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/296
### Added
* Allow the resources directories to be passed in by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/292


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/1.0.0...1.1.0

## 1.0.0 / 2024-10-07

### Changed
* Check DDR status in order to run by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/289
* Change the default value of `--create-global-downtime` by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/290


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.13.0...1.0.0

## 0.13.0 / 2024-09-27

### Fixed
* Update browser test variables ID by @romainberger in https://github.com/DataDog/datadog-sync-cli/pull/276
### Added
* Inject resource context into logs by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/282
* Add support for SDS resources by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/284
* Add support for logs_archives resource by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/270
* Add observability metrics to sync-cli by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/283

## New Contributors
* @romainberger made their first contribution in https://github.com/DataDog/datadog-sync-cli/pull/276
* @jack-edmonds-dd made their first contribution in https://github.com/DataDog/datadog-sync-cli/pull/277

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.12.2...0.13.0

## 0.12.2 / 2024-09-10

### Changed
* Copy the trigger logic for building the artifacts to building and publishing the docker image by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/279


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.12.1...0.12.2

## 0.12.1 / 2024-09-06

### Added
* HAMR-179 Build the Docker image and push it to GitHub's registry by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/274


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.12.0...0.12.1

## 0.12.0 / 2024-09-05

### Fixed
* HAMR-179 Add pyproject.toml to fix docker build by @michael-richey in https://github.com/DataDog/datadog-sync-cli/pull/271
### Added
* Add support for `authn_mappings` by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/255
### Changed
* Add support for dedicated storage handler by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/265
* Remove percentile filter in `metric_percentile` by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/266

## New Contributors
* @michael-richey made their first contribution in https://github.com/DataDog/datadog-sync-cli/pull/271

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.11.0...0.12.0

## 0.11.0 / 2024-06-27

### Fixed
* Fix endless process spawn when "frozen" by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/259
### Changed
* Use `certifi` certs by default by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/258


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.10.0...0.11.0

## 0.10.0 / 2024-06-24

### Fixed
* Handle complex source queries for Logs integration pipeline syncing by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/247
* Ensure only valid indexes are imported for `logs_indexes_order` by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/246
* Ensure clean exit on invalid keys by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/250
* Cleanup deprecation function and remove dead fn call by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/252
### Added
* [APITL-856] Add support for logs-indexes-order by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/238
* [APITL-862] Add support for moving index to end of order list during deletion by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/233
* [APITL-855] Add support for enabling logs integration pipelines by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/237
* Add support for Powerpacks by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/245
* Add support for metrics metadata syncing by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/249
* Add support for metric percentiles syncing by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/251
* Handle invalid integration pipelines by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/254


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.9.3...0.10.0

## 0.9.3 / 2024-05-02

### Fixed
* Fix logs index creation request by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/243


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.9.2...0.9.3

## 0.9.2 / 2024-04-29

### Fixed
* Make sure `Lock` object is initialized in the same event loop by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/241


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.9.1...0.9.2

## 0.9.1 / 2024-04-25

### Fixed
* Ensure we dump synced resources before exiting when interupted by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/232
* Acquire lock before filter by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/239
### Added
* Add support for UrlObject by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/234


**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.9.0...0.9.1

## 0.9.0 / 2024-04-24

### Fixed
* Move permission retrieval into import step by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/210
* Handle when import `id` is changed during dependency resolution by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/220
### Added
* Handle host remapping in get call by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/206
* Add support for `restricted_roles` connection in synthetics private location by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/209
* Refactor abstract methods so they are not called directly by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/207
* Add support for teams by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/214
* Inject default tags to supported resources by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/211
* Add support for `restriction_policies` by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/213
* Add progress bar for get_resources and debug logging for paginated requests by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/230
* Ensure progress bar is continuously updated by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/231
### Changed
* Migrate ThreadPoolUsage to `asyncio` by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/222
* Update dependencies by @skarimo in https://github.com/DataDog/datadog-sync-cli/pull/229

## New Contributors
* @tim-chaplin-dd made their first contribution in https://github.com/DataDog/datadog-sync-cli/pull/223

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.8.0...0.9.0

## 0.8.0 / 2024-01-10

### Fixed
* Minor fix for the User Resource and correction in find_attr by @aldrickdev in https://github.com/DataDog/datadog-sync-cli/pull/196
* Fix `downtime_schedule` one time schedule syncing by @aldrickdev in https://github.com/DataDog/datadog-sync-cli/pull/197
* Adds the editor attribute to the exclude  by @aldrickdev in https://github.com/DataDog/datadog-sync-cli/pull/198
### Added
* Added support for the "Not" Operator by @aldrickdev in https://github.com/DataDog/datadog-sync-cli/pull/195

## New Contributors
* @aldrickdev made their first contribution in https://github.com/DataDog/datadog-sync-cli/pull/196

**Full Changelog**: https://github.com/DataDog/datadog-sync-cli/compare/0.7.0...0.8.0

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
