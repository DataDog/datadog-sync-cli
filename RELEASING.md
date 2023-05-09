# Releasing
This document summarizes the process of doing a new release of this project.
Release can only be performed by Datadog maintainers of this repository.

## Schedule
This project does not have a strict release schedule. However, we would make a release at least every 2 months.
  - No release will be done if no changes got merged to the `main` branch during the above mentioned window.
  - Releases may be done more frequently than the above mentioned window.

## Prerelease checklist
* Check and upgrade dependencies where it applies and makes sense.
  - Create a distinct pull request and test your changes since it may introduce regressions.
  - While using the latest versions of dependencies is advised, it may not always be possible due to potential compatibility issues.
  - Upgraded dependencies should be thoroughly considered and tested to ensure they are safe!
* Make sure tests are passing.
  - Locally and in the continuous integration system.
* Manually test changes included in the new release.
* Make sure documentation is up-to-date.

## Release Process

The release process is controlled and run by GitHub Actions.
### Prerequisite

1. Make sure you have `write_repo` access.
1. Share your plan for the release with other maintainers to avoid conflicts during the release process.

### Update Changelog

1. Open [prepare release](https://github.com/DataDog/datadog-sync-cli/actions/workflows/prepare_release.yml) and click on `Run workflow` dropdown.
1. Optionally, enter new version identifier in the `New version number` input box (e.g. `1.8.0`). Otherwise minor is incremented by default.
1. Trigger the action by clicking on `Run workflow` button.

### Review

1. Review the generated pull-request for `release/<New version number>` branch.
1. If everything is fine, merge the pull-request.
1. Check that the [release](https://github.com/DataDog/datadog-sync-cli/actions/workflows/release.yml) action created new release on GitHub.

Check that the release is available in the [releases](https://github.com/DataDog/datadog-sync-cli/releases) page and the executables are attached to it.
