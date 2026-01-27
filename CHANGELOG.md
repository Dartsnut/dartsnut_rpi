# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.25] - 2026-01-27

### Changed
- refactor: align BLE server error handling with websocket server standards

### Added
- feat: add WiFi-specific error codes for nmcli return codes

## [1.0.24] - 2026-01-26

### Fixed
- fix: reintroduce image processing logic in process_widget_fields with length check for legacy data

## [1.0.23] - 2026-01-26

### Fixed
- fix: comment out image processing code in process_widget_fields

### Documentation
- docs: add ERROR_MESSAGES.md and update ERROR_CODES.md with new format and grouping strategy

### Changed
- refactor: update error message format to 'message (XX-YY)' and unify error messages by category
- refactor: format code and add string truncation to JSON read operations

### Added
- debug: add response logging to websocket endpoint
- feat: improve widget rendering with readiness detection and small widget handling

## [1.0.22] - 2026-01-22

### Added
- feat: add locate_device command support to BLE server
- feat: implement dim window logic with button-triggered temporary restore
- feat: add dim window persistence and WebSocket API with enable flag

### Changed
- chore(requirements): remove pybluez dependency from requirements.txt

## [1.0.21] - 2026-01-20

### Fixed
- fix(downloads): clear DOWNLOAD_PROGRESS for game_id when remove_directory succeeds

### Added
- feat(downloads): return DOWNLOAD_ALREADY_IN_PROGRESS when same url+md5 or game_id already in progress
- feat(websocket): non-blocking handler via asyncio.to_thread, receive queue, and fire-and-forget sync download_app
- feat(file_operations): add version from apps/{game_id}/conf.json to download_progress on completion

## [1.0.20] - 2026-01-19

### Added
- feat: update get_download_progress handler to accept game_ids list
- feat: support multiple game_ids in get_download_progress function

## [1.0.19] - 2026-01-16

### Added
- feat: add --data-store argument to game and widget launch commands

## [1.0.18] - 2026-01-16

### Added
- feat: add firmware update flag display and removal in menu state
- feat: create firmware update flag file on successful update

## [1.0.17] - 2026-01-16

### Changed
- chore: change cron schedule to 3am daily in setup.sh and update.sh
- refactor(git_operations): use update.sh for regular updates, keep setup.sh for setup changes

### Added
- feat: add update.sh script for dependency updates and service restart
- feat: add cron job for automatic git updates in setup.sh
- feat: add automatic git update check script

### Fixed
- fix: refactor widget rendering to use page_img as single source of truth
- fix(pico8): loading animiaiton no longer stuck on screen after pico8 game launch

## [1.0.16] - 2026-01-13

### Added
- feat: add automatic widget update feature with version checking and background downloads

## [1.0.15] - 2026-01-12

### Added
- feat: add websocket endpoints for getting device brightness and volume

## [1.0.14] - 2026-01-10

_No changes documented_

## [1.0.13] - 2026-01-10

### Added
- feat: add unified error handling module with error codes and user-friendly messages
- feat: add WebSocket handler for remote volume control
- feat: add automatic game playtime tracking on start/stop
- feat: add persistent user data storage with websocket endpoints
- feat: modify download_app to require url/md5, use async download with progress tracking when game_id provided
- feat(websocket_server): add game download v2 and progress query
- feat(file_operations): add async game download progress tracking

### Changed
- refactor: update websocket server to use unified error handling and wrap actions in error handlers
- refactor: update operation modules to use unified error handling with error codes
- chore: remove obsolete configuration and resource files for factory tool
- chore: update device brightness setting
- chore: update device volume setting

### Documentation
- docs: add comprehensive error codes reference documentation

### Reverted
- Revert "chore: remove apps/conf.json from version control"

## [1.0.12] - 2025-12-05

### Added
- feat: add new game selection image to enhance user interface
- feat: use big loading sprite for widgets in top area
- feat: add big loading sprite for top area and increase animation to 10fps
- feat: replace static loading image with animated sprite sheet
- chore: add loading sprite sheet with 7 frames

### Changed
- refactor: update settings UI to use font8 bitmap font
- refactor: rename font12 to font8 to reflect 8px font size
- chore: remove unused image files (loading.png, game_sel1.png)
- chore: update .gitignore to include .DS_Store and ensure device.json is ignored
- updated position and size of widget loading images

### Fixed
- fix: improve network reliability and controller support
- fix: update game selection image to improve visual quality
- fix: improve loading sprite rendering and switch to bitmap font

## [1.0.11] - 2025-12-03

### Added
- update: adding wireless controller for device control

## [1.0.10] - 2025-12-01

### Changed
- update ssh operations, optimaize read device.json in main.py
- update with ble name and change the identify image

## [1.0.9] - 2025-11-25

### Added
- Add WiFi management, status indicators, widget control, and UDP broadcast
- update the pause and resume widget processes

## [1.0.8] - 2025-11-21

### Changed
- update pixeldart to 144hz 80% brightness

## [1.0.7] - 2025-11-21

### Changed
- update pixeldart fps to 120hz

## [1.0.6] - 2025-11-20

_No changes documented_

## [1.0.5] - 2025-11-04

### Changed
- update: change fps in DartsnutRGBMatrix

## [1.0.4] - 2025-10-27

### Fixed
- fix audio volume at 0 issue

## [1.0.3] - 2025-10-23

_No changes documented_

## [1.0.2] - 2025-10-21

### Added
- update with pausing game in menu
- update with lock incon and press B in menu to end game

## [1.0.1] - 2025-10-20

### Changed
- update README.md

## [1.0.0] - 2025-10-20

### Added
- Initial release

### Changed
- update README.md and update ble_server to make sure the bluetooth is unblocked and powered on
- update README.md
- update requirement.txt with aiohttp
- update to TRIXIE
- update main.py: replace SIGTERM with SIGKILL
- update font and update the rgb matrix

## [Unreleased]

_No unreleased changes_

---

## [Pre-1.0.0] - 2025-08-25 to 2025-10-19

### Initial Development

This section includes commits from the initial development phase before version 1.0.0:

- Initial project setup and configuration
- Core functionality implementation
- Service configuration and setup scripts
- Factory tools development
- Bluetooth and network configuration
- RGB matrix integration
- Game and widget system development
- Various updates and improvements throughout the development cycle
