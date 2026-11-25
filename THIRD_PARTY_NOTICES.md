# Third-Party Notices

This repository is licensed under the MIT License (see `LICENSE`). It
incorporates and depends on third-party software under other licenses, listed
here. Where a component's license requires notice, source availability, or a
written offer, those obligations are described below.

## Vendored binaries (committed to this repository)

| File | Upstream | License | Notes |
|---|---|---|---|
| `uv` | [astral-sh/uv](https://github.com/astral-sh/uv) | Apache-2.0 | Unmodified release binary; license text in the upstream distribution. |
| `watchdog` | Dartsnut | Proprietary | Dartsnut firmware watchdog agent. |
| `bridge` | Dartsnut | Proprietary | Remote sync bridge; source intentionally not published (see README). |
| `DartsnutRGBMatrix` | Dartsnut, based on [hzeller/rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix) | **GPL-2.0** (upstream) — see below | Drives the RGB LED matrix. |

### `DartsnutRGBMatrix` — GPL-2.0, pending source publication

`DartsnutRGBMatrix` statically links `rpi-rgb-led-matrix`
([hzeller/rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix)),
licensed **GPL-2.0**. Verified: 116 `rgb_matrix::*` symbols are defined in the
binary (static link, not a shared `librgbmatrix` dependency).

**Status: obligation not yet met.** GPL-2.0 §3 requires conveying
Corresponding Source with the binary (or a written offer). The source is
currently not published at any public location — `github.com/Dartsnut/rgbmatrix`
returns 404 as of 2026-11-02.

This is a tracked commitment, not an oversight: Dartsnut intends to open-source
the matrix driver at `https://github.com/Dartsnut/rgbmatrix`. **Until that
repository is public, the matrix binary is not covered by the MIT grant in
`LICENSE`, and it should not be shipped to customers outside a private
deployment.** Publishing the repo discharges the obligation; see
`docs/adr/0002-license-policy.md`.

## Python dependencies (installed at runtime, not vendored)

Resolved from `pyproject.toml` / `uv.lock`. Versions are pinned there.

| Package | License |
|---|---|
| bluezero | MIT |
| fastapi | MIT |
| numpy | BSD-3-Clause |
| pillow | MIT-CMU |
| **pybluez-dartsnut** | **GPL-2.0** (upstream PyBluez, unmodified) — see below |
| pydartsnut | Proprietary (Dartsnut) |
| pygame-ce | LGPL-2.1-or-later |
| qrcode | BSD |
| requests | Apache-2.0 |
| uvicorn | BSD-3-Clause |
| websockets | BSD-3-Clause |

Transitive dependencies not listed here retain their own upstream licenses;
`uv.lock` is the authoritative list of what a given firmware release installs.

### `pybluez-dartsnut` — GPL-2.0, unmodified upstream source

`python_websocket/bluetooth_operations.py` does `import bluetooth`, which
resolves to `pybluez-dartsnut`. Full audit performed 2026-11-02:

- **Source is unmodified.** `github.com/Dartsnut/pybluez` is byte-identical to
  `github.com/pybluez/pybluez` master (`diff -rq` → no differences).
- The published sdist differs from upstream in exactly two files:
  `setup.py` (`name`, `description`, `url`, `download_url`,
  `long_description`, `maintainer`) and `README.md`. Every `.c` / `.h` /
  `.py` source file is identical (`cmp` verified on `bluetooth/*.py` and
  `bluez/*.c`).
- Dartsnut's contribution is the **missing linux-aarch64 wheel** — upstream
  publishes no prebuilt wheel for 64-bit Raspberry Pi, so `pip install pybluez`
  falls back to the sdist and needs `libbluetooth-dev` on device.
- License is **GPL-2.0** (`COPYING`, identical to upstream; PyPI classifier
  `GNU General Public License (GPL)`).

**GPL-2.0 §3 is satisfied.** Both the wheel (object code) and the
corresponding sdist are published together on the same PyPI release
(`pybluez_dartsnut-0.30-cp313-cp313-manylinux_2_38_aarch64.whl` +
`pybluez_dartsnut-0.30.tar.gz`), and the full source is public at
`github.com/Dartsnut/pybluez`. §3(a) "accompany with complete corresponding
machine-readable source" is met.

**This package cannot be relicensed by Dartsnut.** The copyright is upstream's:
`setup.py` credits Albert Huang, `macos/` carries Bea Lam's notices, and the
repo has 42 contributors with no Dartsnut commits. Relicensing would need every
holder's consent. Treat GPL-2.0 as fixed.

**The open question is linkage, not licensing.** An in-process `import` of a
GPL-2.0 module is the classic copyleft-boundary question: the conservative
reading is that the importing program is a derivative; the aggressive reading
is that dynamic import is mere aggregation. This is disclosed rather than
silently inherited. See `docs/adr/0002-license-policy.md` for the options.

## Scratch 3 runtime (AGPL-3.0, only if a Scratch runtime is bundled)

When this firmware bundles the Scratch 3 runtime (`@scratch/scratch-vm`,
`@scratch/scratch-render`, `@scratch/scratch-storage`,
`@scratch/scratch-svg-renderer`), the following applies.

| Component | Version | License |
|---|---|---|
| `@scratch/scratch-vm` | 15.3.0 | AGPL-3.0-only |
| `@scratch/scratch-render` | 15.3.0 | AGPL-3.0-only |
| `@scratch/scratch-storage` | 15.3.0 | AGPL-3.0-only |
| `@scratch/scratch-svg-renderer` | 15.3.0 | AGPL-3.0-only |

Copyright (c) Massachusetts Institute of Technology and the Scratch Foundation.
Full license text: `LICENSE-AGPL-3.0.txt`.

These packages are used **unmodified**. AGPL-3.0 §4 (Conveying Verbatim Copies)
applies: the copyright notice, license text, and warranty disclaimer travel with
the code, and each package's npm tarball already ships `src/`, `LICENSE`, and
`TRADEMARK`, so Corresponding Source is present alongside the object code.

AGPL-3.0 §5(d) (interactive interfaces must display Appropriate Legal Notices) is
**not triggered**: it is scoped to §5 "Conveying Modified Source Versions", and
these packages are conveyed unmodified. It also carries an explicit carve-out
where the Program's own interfaces display no such notices, which is the case for
`scratch-vm` and `scratch-render` — both are headless libraries. The UI-bearing
package `scratch-gui` is not used.

AGPL-3.0 §13 (Remote Network Interaction) is **not triggered** for the same
reason: it applies only if the Program is modified.

**If any `@scratch/*` file is ever patched**, both obligations activate. At that
point this file, `docs/adr/0002-license-policy.md`, and the device's System UI
must be revisited; §13 then requires offering Corresponding Source to network
users, which in practice means a reachable URL surfaced from the device.

### Scratch trademarks

> The Scratch trademarks, including the Scratch name, logo, the Scratch Cat,
> Gobo, Pico, Nano, Tera and Giga graphics (the "Marks"), are property of the
> Scratch Foundation. Marks may not be used to endorse or promote products
> derived from this software without specific prior written permission.

Dartsnut is **not affiliated with or endorsed by the Scratch Foundation**.
"Scratch" is referenced descriptively to identify the project format and runtime
this firmware can execute. The Marks are not used in Dartsnut branding, product
naming, or on-device UI.
