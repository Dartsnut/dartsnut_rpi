// Dartsnut shared-memory layout, transcribed from pydartsnut 1.4.0.
//
// Display (name = "game_shm" for games, "dartsnut_sideload_<id>" for sideload):
//   shm[0]      1 = producer may write, 0 = fresh frame, 2 = consumer busy
//   shm[1..]    RGB888, PANEL.width * PANEL.height
//
// Input (always named "pdoshm", created by the matrix service, read-only here):
//   pdoshm[0]     button bitmask, see BUTTON_BITS order below
//   pdoshm[1..48] 12 darts, 4 bytes each: x_lo, x_hi, y_lo, y_hi (LE)
//   pdoshm[49]    brightness (written by the app, not read here)
//
// Two details worth stating because they are easy to get wrong:
//
// 1. The dart byte offsets start at 1, not 0. pydartsnut reads
//    x = buf[i*4+1] | buf[i*4+2]<<8, so slot i occupies bytes 1+i*4 .. 4+i*4.
//    Byte 0 is the button mask, sharing the segment with the dart array.
//
// 2. A raw coordinate of 0xffff on *either* axis means "no dart" -- the terms
//    are OR'd, not AND'd. pydartsnut maps hardware 1800..39800 to 0..127 with
//    floor((v - 1800) / 299), clamping below 1800 to 0 and above 39800 to 127.
//    That clamp makes a hardware reading of exactly 0 unreachable as a mapped
//    coordinate: it maps to 0. pydartsnut's InputHandler then treats the
//    *mapped* pair [0, 0] as invalid too (_is_invalid_dart). We reproduce that
//    behaviour exactly so Scratch games see the same dart semantics as Python
//    games rather than a subtly better set.
//
// 3. On this hardware every dart from one physical field reports the SAME slot
//    index, and a dart left in the board keeps that slot reading present. Hit
//    detection therefore keys on the mapped *coordinate*, not on the slot's
//    absent -> present edge; see DartReader. Keying on the edge registered one
//    throw out of three. This is a deliberate difference from pydartsnut's
//    default (MIN_ACTIVE_DURATION = 0, which is also edge-based), because the
//    measured hardware behaviour makes the edge unusable; pydartsnut has the
//    same coordinate-based path available via MIN_ACTIVE_DURATION > 0.
const fs = require('fs');
const os = require('os');

const DART_COUNT = 12;
const DART_ABSENT = 0xffff;
const HW_MIN = 1800;
const HW_MAX = 39800;
const HW_STEP = 299;

// pydartsnut InputHandler.IDLE_UNBLOCK_DURATION default, in seconds.
const IDLE_UNBLOCK_SECONDS = 0.2;

class SharedMemory {
    constructor (name, size, create = true) {
        this.path = `/dev/shm/${name}`;
        if (!fs.existsSync('/dev/shm')) this.path = `${os.tmpdir()}/${name}`;
        if (create) {
            const fd = fs.openSync(this.path, 'a+');
            const stat = fs.fstatSync(fd);
            if (stat.size < size) fs.ftruncateSync(fd, size);
            fs.closeSync(fd);
        }
        this.fd = fs.openSync(this.path, 'r+');
    }

    byteAt (offset) {
        const b = Buffer.alloc(1);
        fs.readSync(this.fd, b, 0, 1, offset);
        return b[0];
    }

    setByte (offset, value) {
        fs.writeSync(this.fd, Buffer.from([value & 0xff]), 0, 1, offset);
    }

    readByte (offset) {
        return this.byteAt(offset);
    }

    write (offset, buffer) {
        fs.writeSync(this.fd, buffer, 0, buffer.length, offset);
    }

    read (offset, length) {
        const b = Buffer.alloc(length);
        fs.readSync(this.fd, b, 0, length, offset);
        return b;
    }

    close () {
        try {
            fs.closeSync(this.fd);
        } catch (e) { /* already closed */ }
    }
}

function mapAxis (raw) {
    if (raw <= HW_MIN) return 0;
    if (raw >= HW_MAX) return 127;
    return Math.floor((raw - HW_MIN) / HW_STEP);
}

// Mirrors pydartsnut _is_invalid_dart: [-1, -1] or [0, 0] are both "absent".
function isInvalidDart (x, y) {
    return (x === -1 && y === -1) || (x === 0 && y === 0);
}

// Converts one raw 4-byte slot into {present, px, py} in device coordinates.
function decodeDartSlot (raw, index) {
    const rx = raw[index * 4] + (raw[index * 4 + 1] << 8);
    const ry = raw[index * 4 + 2] + (raw[index * 4 + 3] << 8);
    if ((rx === DART_ABSENT) || (ry === DART_ABSENT)) {
        return {present: false, px: 0, py: 0};
    }
    const px = mapAxis(rx);
    const py = mapAxis(ry);
    if (isInvalidDart(px, py)) return {present: false, px, py};
    return {present: true, px, py};
}

// Emits one hit when a slot first reads present, and again whenever that slot's
// mapped position MOVES to a different place.
//
// Keying on the absent -> present transition alone loses most throws on real
// hardware. Measured on a device: every dart from one field reports the same
// sensor index, and a dart left in the board keeps that field reading present
// (it never returns to "absent"), so a slot that already fired stays suppressed
// and the next dart thrown into the same field is silently dropped. Three darts
// thrown two seconds apart registered as one hit.
//
// Coordinate change is therefore the signal, which also matches what
// pydartsnut's InputHandler does for its own MIN_ACTIVE_DURATION guard: it tracks
// active *coordinates*, not slots.
//
// Two things keep a resting dart from firing continuously:
//
//   * DART_MOVE_THRESHOLD_PX: a coordinate must move at least this far, in
//     device pixels, to count. A dart in the board jitters by a pixel or two
//     under sensor noise; without this the reader would fire every frame.
//   * The idle re-arm: after IDLE_UNBLOCK_SECONDS of a slot being absent, the
//     remembered position is dropped, so the same field can be hit again from
//     scratch.
const DART_MOVE_THRESHOLD_PX = 4;

class DartReader {
    constructor (shm, {
        idleUnblockSeconds = IDLE_UNBLOCK_SECONDS,
        moveThresholdPx = DART_MOVE_THRESHOLD_PX
    } = {}) {
        this.shm = shm;
        this.idleUnblockMs = idleUnblockSeconds * 1000;
        this.moveThresholdPx = moveThresholdPx;
        this.absentSince = new Array(DART_COUNT).fill(0);
        // Last position reported for each slot, or null when nothing is
        // remembered (never seen, or re-armed after going absent).
        this.lastHit = new Array(DART_COUNT).fill(null);
    }

    // Drops the remembered position for a slot that has been absent long enough,
    // so the next dart in that field reports even if it lands where the previous
    // one did.
    _rearm (index, present, now) {
        if (present) {
            this.absentSince[index] = 0;
            return;
        }
        if (!this.absentSince[index]) this.absentSince[index] = now;
        if (this.lastHit[index] !== null && now - this.absentSince[index] >= this.idleUnblockMs) {
            this.lastHit[index] = null;
        }
    }

    readNewDartHits (now = Date.now()) {
        const hits = [];
        const raw = this.shm.read(1, DART_COUNT * 4);
        for (let i = 0; i < DART_COUNT; i++) {
            const slot = decodeDartSlot(raw, i);
            this._rearm(i, slot.present, now);
            if (!slot.present) continue;

            const previous = this.lastHit[i];
            if (previous !== null) {
                const dx = Math.abs(slot.px - previous.px);
                const dy = Math.abs(slot.py - previous.py);
                // Euclidean, not per-axis: a 3 px drift on both axes is ~4.2 px
                // of real movement and should count as a different dart.
                if (Math.sqrt(dx * dx + dy * dy) < this.moveThresholdPx) continue;
            }
            this.lastHit[i] = {px: slot.px, py: slot.py};
            hits.push({index: i, px: slot.px, py: slot.py});
        }
        return hits;
    }
}

module.exports = {
    SharedMemory,
    DartReader,
    DART_COUNT,
    DART_ABSENT,
    DART_MOVE_THRESHOLD_PX,
    IDLE_UNBLOCK_SECONDS,
    HW_MIN,
    HW_MAX,
    mapAxis,
    isInvalidDart,
    decodeDartSlot
};
