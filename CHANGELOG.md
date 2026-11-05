# Changelog

All notable changes to this project will be documented in this file. See [commit-and-tag-version](https://github.com/absolute-version/commit-and-tag-version) for commit guidelines.

## [1.0.3](https://github.com/blackopsrepl/computer-use-sway/compare/v1.0.2...v1.0.3) (2026-11-05)


### Bug Fixes

* **agent:** keep the agent's cursor home between calls, without moving its focus cbdad9f
* **agent:** never leave the agent's cursor on the user's screen after a call 4e4f8c7
* **screenshot:** leave every agent output out of the default capture 885dc4a
* **toolkits:** support kitty as the only terminal 70de726


### Build System

* one release gate whose exit status is the verdict 654004d


### Documentation

* claim what was measured, not more 98de565
* **findings:** cite tests in the repository, not scratch probes 08531d5

## [1.0.2](https://github.com/blackopsrepl/computer-use-sway/compare/v1.0.1...v1.0.2) (2026-11-05)


### Bug Fixes

* **agent:** stop input before it can reach the user's seat after a seat change f0711cb

## [1.0.1](https://github.com/blackopsrepl/computer-use-sway/compare/v1.0.0...v1.0.1) (2026-11-05)


### Bug Fixes

* **agent:** never draw the agent's cursor on the user's screen cc542ea

## [1.0.0](https://github.com/blackopsrepl/computer-use-sway/compare/v0.3.2...v1.0.0) (2026-11-05)


### ⚠ BREAKING CHANGES

* wtype and /dev/uinput are no longer used; the agent seat needs
a one-time `computer-use-sway --install-sway-config`; input defaults to the
agent seat in seat mode; input tool results gain effect/evidence/seat.

### Features

* **action:** observe scenes, effect verdicts, delivery ladder, launch, waits, run plans 8fff356
* agent-seat tool surface with observe, act, run, launch and verdicts f301d38
* **agent:** anchor the agent seat's devices for the life of the sway session c4fc579
* **agent:** config-declared agent seat, off-screen agent output, idle sentinel, sandbox d18e64a
* **agent:** keep an agent-launched app's later windows on the agent workspace f936182
* **bench:** scripted benchmark (make bench) and the real-agent harness e4b8969
* **perception:** AT-SPI client, toolkit profiles, geometry, capture, OCR ensemble, refs 7a0e8bd
* **transport:** stdlib Sway IPC, Wayland wire, virtual seat input and D-Bus clients 674cdad


### Bug Fixes

* **act:** escalate set_text past a failed readback and fall back to press for blocked clicks dedd0b2
* **act:** press GTK3 widgets through the pointer; skip apps stalled in a modal loop 25f4b1c
* **act:** read element bounds right before pointer input; launch returns settled geometry a75526d
* **agent:** judge reload recovery against seat0's device count at recovery time c1033b7
* **bench:** give each layer-2 sandbox the user's idle hardware on seat0 707d526
* **capture:** treat a blinking caret as noise; OCR refs compare their own box 9d7c6d9
* **keymap:** bind special keysyms to F13-F24 slots so Chromium and Electron receive them 92aa79f
* **keymap:** type characters on keys that Ghostty and Chromium both read as text e7a14fa
* **launch:** keep sway's activation token so every toolkit maps on the agent workspace 7e5d162
* **launch:** return only once the tree is populated and no page is loading a26b406
* **launch:** run apps under a subreaper so sway's pid placement holds for Electron c199115
* **observe:** reach what the benchmark showed missing, structure before pixels c6ded11
* **ocr:** keep one reading per spot instead of joining the variants' readings ee7094f
* **query:** a role matches its family in either spelling fc766f3
* **sandbox:** reap every sandbox process before removing its root e8d2fcb
* **sandbox:** tear down when the process is terminated 3ce5957
* **screenshot:** capture the user's outputs by default ff5f2a2
* **sentinel:** restore the user's focus by workspace name when it was empty a136c67
* **tests:** keep spike and Electron-oracle artefacts in the XDG cache c3125b3
* **toolkits:** measured terminal profiles d476f9a


### Documentation

* **bench:** layer-2 result: all arms 24/24, v1 at 1.86 M tokens and $2.08 765587d
* **findings:** O12, A12-A14, P3, P4, I6, I7 with the tests that measure them 2e4b239
* module docstrings for core and desktop 4b76cfa
* **plan:** proven 1.0 implementation plan for a Sway-native agent seat 6963488
* the 1.0 surface, architecture, operating procedure and repository rules 1deae3d

## [0.3.2](https://github.com/blackopsrepl/computer-use-sway/compare/v0.3.1...v0.3.2) (2026-10-04)


### Bug Fixes

* **drag:** move the pointer before pressing, or the press goes nowhere 58d75ef

## [0.3.1](https://github.com/blackopsrepl/computer-use-sway/compare/v0.3.0...v0.3.1) (2026-10-04)


### Bug Fixes

* **drag:** hold the button with a virtual pointer, not a cursor warp 0f01f4e


### Build System

* **release:** match seshat's changelog type visibility 12574cc


### Documentation

* **drag:** the virtual pointer requirement, on every surface d033e86

## [0.3.0](https://github.com/blackopsrepl/computer-use-sway/compare/v0.2.6...v0.3.0) (2026-09-29)


### ⚠ BREAKING CHANGES

* **recording:** the recording_start, recording_status, recording_stop, recording_timeline, recording_voiceover and recording_scenes tools are gone from this server. Use seshat (mcp__seshat__recording_*) for recording and narration; this server now only publishes its actions to that timeline.

* **recording:** drop the recording and narration surface 8ca3001


### Features

* **timeline:** publish this server's actions to the take timeline 4f2b38e

## [0.2.6](https://github.com/blackopsrepl/computer-use-sway/compare/v0.2.5...v0.2.6) (2026-09-25)


### Features

* **recording:** address a specific take by id 449c2c3
* **screenshot:** add save_path for flat image budgets 065f731


### Bug Fixes

* **narration:** schedule and mux from the published artifact 7da5f65

## [0.2.5](https://github.com/blackopsrepl/computer-use-sway/compare/v0.2.4...v0.2.5) (2026-09-25)


### Bug Fixes

* restore names lost when server.py was split 1b54a2e

## [0.2.4](https://github.com/blackopsrepl/computer-use-sway/compare/v0.2.3...v0.2.4) (2026-09-25)


### Features

* **recording:** default to MP4/H.264 for widest compatibility cbdfaac

## [0.2.3](https://github.com/blackopsrepl/computer-use-sway/compare/v0.2.2...v0.2.3) (2026-09-24)


### Features

* **recording:** burn styled subtitles into narrated videos bc5ba86

## [0.2.2](https://github.com/blackopsrepl/computer-use-sway/compare/v0.2.1...v0.2.2) (2026-09-24)

## [0.2.1](https://github.com/blackopsrepl/computer-use-sway/compare/v0.2.0...v0.2.1) (2026-09-24)


### Features

* **recording:** add timeline capture and scripted voiceover af51a18

## 0.2.0 (2026-09-17)


### Features

* add AV1 WebM and constrained GIF encoding profiles 741ec58
* add recording lifecycle MCP tools 845be4e
* add recording session foundations 10a1742
* deliver operating instructions over MCP initialize 9bde630
* extract Sway computer-use MCP 9c19942
