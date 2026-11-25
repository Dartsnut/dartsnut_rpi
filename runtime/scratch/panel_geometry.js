// Panel geometry constants, verified against the firmware.
//
// The physical panel is 128x160, but the *play area* -- the region dart
// coordinates address and the region the game picker renders into -- is
// 128x128 at (0,0):
//   mcp_server/framebuffer.py  "top"    = crop(0,   0, 128, 128)
//                              "bottom" = crop(0, 128,  64, 160)
//   states/game.py             preview (128x128) at (0,0), game_sel.png (64x32) at (0,128)
//   pydartsnut get_darts()     both axes map to 0..127
//
// The Scratch stage is 480x360 (4:3), so it cannot fill a square play area. It
// is scaled to 128x96 and placed at the TOP of the play area (y=0..95), which
// leaves y=96..127 as a control band holding the green-flag and stop buttons
// that the editor puts above the stage. Dart hits on those buttons are dispatched
// to the runner; hits anywhere else in the play area are outside the stage and
// are dropped by the runner.
const PANEL = {width: 128, height: 160};
const PLAY = {x: 0, y: 0, width: 128, height: 128};
const FOOTER = {x: 0, y: 128, width: 64, height: 32};
const STAGE = {width: 480, height: 360};

function computeGeometry() {
    const scale = Math.min(PLAY.width / STAGE.width, PLAY.height / STAGE.height);
    const w = Math.floor(STAGE.width * scale);
    const h = Math.floor(STAGE.height * scale);
    return {
        scale,
        width: w,
        height: h,
        // Horizontally centred (the scale fills the width exactly at 128), but
        // pinned to the top so the leftover rows are one contiguous band.
        ox: PLAY.x + Math.floor((PLAY.width - w) / 2),
        oy: PLAY.y
    };
}

// The control band below the stage: the rest of the play area.
//
// Each button owns half the band edge to edge, rather than being a 32x32 square
// with dead gutters. A dart is a physical throw: a 2 px miss inside a small
// square does nothing at all, which reads as "the button is broken". Splitting
// the band means anything below the stage hits one of the two buttons, and the
// glyphs drawn inside are deliberately smaller than their cells.
function computeControls() {
    const g = computeGeometry();
    const top = g.oy + g.height;
    const height = PLAY.y + PLAY.height - top;
    const half = Math.floor(PANEL.width / 2);
    // The glyph is what the layout centres; the hit cell is the whole half-band.
    const size = Math.min(18, height);
    const y = top + Math.floor((height - size) / 2);
    return {
        top,
        height,
        size,
        y,
        flag: {x: 0, y: top, width: half, height, size, glyphY: y},
        stop: {x: half, y: top, width: PANEL.width - half, height, size, glyphY: y}
    };
}

// True when a play-area pixel falls inside the scaled stage rectangle.
function isInsideStage(px, py) {
    const g = computeGeometry();
    return px >= g.ox && px < g.ox + g.width && py >= g.oy && py < g.oy + g.height;
}

// Which control a dart hit, or null. Deliberately generous: a dart is a
// physical throw at a 128 px panel, so the whole cell around each icon counts.
function controlAt(px, py) {
    const controls = computeControls();
    for (const name of ['flag', 'stop']) {
        const box = controls[name];
        if (px >= box.x && px < box.x + box.width &&
            py >= box.y && py < box.y + box.height) {
            return name;
        }
    }
    return null;
}

// Device pixel (0..127 inside the play area) -> Scratch stage coordinate.
function panelToScratch(px, py) {
    const g = computeGeometry();
    return {
        x: (px - g.ox) / g.scale - STAGE.width / 2,
        y: STAGE.height / 2 - (py - g.oy) / g.scale
    };
}

function scratchToPanel(x, y) {
    const g = computeGeometry();
    return {
        x: g.ox + (x + STAGE.width / 2) * g.scale,
        y: g.oy + (STAGE.height / 2 - y) * g.scale
    };
}

// Which source pixels one output pixel covers, and by how much.
//
// Weights are integers in units of 1/dstLen: output pixel i spans
// [i*srcLen, (i+1)*srcLen) and source pixel s spans [s*dstLen, (s+1)*dstLen),
// so their overlap is a whole number and every output pixel's weights sum to
// srcLen exactly. Integer weights mean no rounding drift across the two passes
// and no per-pixel division in the inner loop.
//
// Offsets are pre-multiplied by `stride` so the inner loops do no index
// arithmetic, and the taps live in flat typed arrays: this runs 30 times a
// second on a Pi, where a nested array of pairs costs ~40% more.
function areaPlan (srcLen, dstLen, stride) {
    const starts = new Int32Array(dstLen);
    const counts = new Int32Array(dstLen);
    const offsets = [];
    const weights = [];
    for (let i = 0; i < dstLen; i++) {
        const start = i * srcLen;
        const end = (i + 1) * srcLen;
        starts[i] = weights.length;
        let n = 0;
        for (let s = Math.floor(start / dstLen); s < Math.ceil(end / dstLen); s++) {
            const lo = Math.max(start, s * dstLen);
            const hi = Math.min(end, (s + 1) * dstLen);
            if (hi <= lo) continue;
            offsets.push(s * stride);
            weights.push(hi - lo);
            n++;
        }
        counts[i] = n;
    }
    return {
        starts,
        counts,
        offsets: Int32Array.from(offsets),
        weights: Int32Array.from(weights)
    };
}

// Per-size plans and the intermediate row buffer, reused across frames. Keyed
// by stage and band size, which are fixed for the life of the runner.
const blitCache = new Map();
function blitState (stageWidth, stageHeight, width, height) {
    const key = `${stageWidth}x${stageHeight}->${width}x${height}`;
    let entry = blitCache.get(key);
    if (!entry) {
        entry = {
            // Horizontal pass accumulates rows of `width` pixels, in GL row
            // order. Byte offsets are into this buffer, hence the *3 strides.
            x: areaPlan(stageWidth, width, 4),
            y: areaPlan(stageHeight, height, width * 3),
            // Top-down taps become bottom-up offsets: row sy is GL row sh-1-sy.
            flipped: null,
            rows: new Uint32Array(width * stageHeight * 3),
            total: stageWidth * stageHeight,
            reciprocal: 1 / (stageWidth * stageHeight)
        };
        entry.flipped = Int32Array.from(
            entry.y.offsets,
            offset => (stageHeight - 1) * width * 3 - offset
        );
        blitCache.set(key, entry);
    }
    return entry;
}

// Stage pixels (GL is bottom-up RGBA) -> RGB888, placed in the letterbox band.
//
// Each output pixel is the *average* of the stage pixels it covers, not one
// sample of them. The stage is 480x360 and the band is 128x96, so a nearest
// sample throws away 93% of the picture: thin outline strokes land between
// samples and come out as dashes, which is what makes a sprite read as broken.
// Averaging keeps every source pixel's contribution. Measured against a Lanczos3
// downsample of a 4x render, nearest scores 28.5 dB and area averaging 40.4 dB.
//
// Pure geometry, deliberately kept here rather than in the runner: this module
// has no native dependencies, so the flip and the sampling can be tested without
// a GL context.
function blitStageIntoPanel (stagePixels, stageWidth, stageHeight, out) {
    const g = computeGeometry();
    const width = Math.min(g.width, PANEL.width);
    const height = Math.min(g.height, PANEL.height);
    const state = blitState(stageWidth, stageHeight, width, height);
    const xPlan = state.x;
    const rows = state.rows;

    // Horizontal pass, in GL row order.
    for (let sy = 0; sy < stageHeight; sy++) {
        const srcRow = sy * stageWidth * 4;
        const dstRow = sy * width * 3;
        for (let x = 0; x < width; x++) {
            const from = xPlan.starts[x];
            const to = from + xPlan.counts[x];
            let r = 0, gg = 0, b = 0;
            for (let t = from; t < to; t++) {
                const src = srcRow + xPlan.offsets[t];
                const w = xPlan.weights[t];
                r += stagePixels[src] * w;
                gg += stagePixels[src + 1] * w;
                b += stagePixels[src + 2] * w;
            }
            const dst = dstRow + x * 3;
            rows[dst] = r;
            rows[dst + 1] = gg;
            rows[dst + 2] = b;
        }
    }

    // Vertical pass, top-down, reading the flipped offsets.
    const yPlan = state.y;
    const flipped = state.flipped;
    const reciprocal = state.reciprocal;
    for (let y = 0; y < height; y++) {
        const from = yPlan.starts[y];
        const to = from + yPlan.counts[y];
        const dstRow = ((g.oy + y) * PANEL.width + g.ox) * 3;
        for (let x = 0; x < width; x++) {
            const column = x * 3;
            let r = 0, gg = 0, b = 0;
            for (let t = from; t < to; t++) {
                const src = flipped[t] + column;
                const w = yPlan.weights[t];
                r += rows[src] * w;
                gg += rows[src + 1] * w;
                b += rows[src + 2] * w;
            }
            const dst = dstRow + column;
            out[dst] = Math.round(r * reciprocal);
            out[dst + 1] = Math.round(gg * reciprocal);
            out[dst + 2] = Math.round(b * reciprocal);
        }
    }
}

// The control band: a green flag and a red stop octagon, drawn straight into the
// RGB888 panel buffer.
//
// Drawn rather than loaded as images: each glyph is an 18 px tile, so this is a
// couple of hundred pixel writes either way, and a bitmap would have to be
// shipped and kept in sync with the geometry above. The shapes are the familiar
// editor glyphs, not exact copies.
function fillRect (out, x0, y0, x1, y1, colour) {
    for (let y = y0; y < y1; y++) {
        if (y < 0 || y >= PANEL.height) continue;
        let i = (y * PANEL.width + Math.max(0, x0)) * 3;
        for (let x = Math.max(0, x0); x < Math.min(x1, PANEL.width); x++) {
            out[i] = colour[0];
            out[i + 1] = colour[1];
            out[i + 2] = colour[2];
            i += 3;
        }
    }
}

const FLAG_GREEN = [76, 191, 75];
const STOP_RED = [207, 66, 56];
const CHROME_DARK = [60, 60, 60];
const CHROME_LIGHT = [235, 235, 235];

// A green flag: pole, foot, and a cloth with a notch cut into its trailing edge.
//
//   G  green
//   .  transparent (band shows through)
const FLAG_MASK = [
    '...GGGGGGGGGGG....',
    '...GGGGGGGGGGGG...',
    '...GGGGGGGGGGGG...',
    '...GGGGGGGGGG.....',
    '...GGGGGGGGG......',
    '...GGGGGGGGGG.....',
    '...GGGGGGGGGGGG...',
    '...GGGGGGGGGGGG...',
    '...GGGGGGGGGGG....',
    '...GG.............',
    '...GG.............',
    '...GG.............',
    '...GG.............',
    '...GG.............',
    '...GG.............',
    '..GGGGGGG.........',
    '..GGGGGGG.........',
    '..................'
];

function drawFlag (out, box) {
    drawMasked(out, box, FLAG_MASK, {G: FLAG_GREEN});
}

// A stop button: a red octagon with a white outline, both corners cut by the
// same rule so the red follows the outline instead of reading as a rectangle on
// a disc.
//
// The mask is computed, not hand-placed: two octagons of different radii have to
// share one chamfer, which is easier to state as a formula than as 18 literal
// rows the next reader has to count.
//
//   S  stop red
//   W  white outline
//   .  transparent (band shows through)
const STOP_MASK = (() => {
    const rows = [];
    const half = 9;          // 18 px square
    const cut = 4;           // corner chamfer, ~0.44 * half
    for (let y = 0; y < half * 2; y++) {
        let row = '';
        for (let x = 0; x < half * 2; x++) {
            const dx = Math.abs(x + 0.5 - half);
            const dy = Math.abs(y + 0.5 - half);
            const inOuter = dx <= half - 0.5 && dy <= half - 0.5 &&
                dx + dy <= half - 0.5 + cut;
            // Two pixels of outline, following the same octagon.
            const inRed = dx + dy <= half - 2.5 + cut * 0.78 &&
                dx <= half - 2.5 && dy <= half - 2.5;
            row += !inOuter ? '.' : (inRed ? 'S' : 'W');
        }
        rows.push(row);
    }
    return rows;
})();

function drawMasked (out, box, mask, colours) {
    const rows = mask.length;
    const cols = mask[0].length;
    const x0 = Math.round(box.x + box.width / 2 - cols / 2);
    const y0 = Math.round(box.glyphY + box.size / 2 - rows / 2);
    for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols; col++) {
            const colour = colours[mask[row][col]];
            if (!colour) continue;
            const x = x0 + col;
            const y = y0 + row;
            if (x < 0 || x >= PANEL.width || y < 0 || y >= PANEL.height) continue;
            const i = (y * PANEL.width + x) * 3;
            out[i] = colour[0];
            out[i + 1] = colour[1];
            out[i + 2] = colour[2];
        }
    }
}

function drawStop (out, box) {
    drawMasked(out, box, STOP_MASK, {W: CHROME_LIGHT, S: STOP_RED});
}

// Paint the control band (everything below the stage) into the panel buffer.
function drawControls (out) {
    const controls = computeControls();
    // Clear the band so a previous frame's icons cannot bleed through.
    fillRect(out, 0, controls.top, PANEL.width, controls.top + controls.height, CHROME_DARK);
    drawFlag(out, controls.flag);
    drawStop(out, controls.stop);
}

module.exports = {
    PANEL,
    PLAY,
    FOOTER,
    STAGE,
    computeGeometry,
    computeControls,
    isInsideStage,
    controlAt,
    drawControls,
    panelToScratch,
    scratchToPanel,
    blitStageIntoPanel
};
