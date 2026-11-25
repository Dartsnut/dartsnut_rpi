// Scratch (sb3) runner for the Dartsnut panel shm ABI.
//
// Launch (both kinds):
//   node sb3_runner.js --project <dir> --shm <display-shm-name> [--data-store <dir>]
//                      [--fps 30] [--scratch-vars]
//
// Display shm is "game_shm" for a game (started by start_game_process) and
// "dartsnut_sideload_<id>" for a sideload session. Input is always "pdoshm",
// created by the matrix service; this process only reads it.
//
// Contract, transcribed from pydartsnut 1.4.0:
//   display  shm[0]      1 = producer may write, 0 = fresh frame, 2 = consumer busy
//            shm[1..]     RGB888, PANEL.width * PANEL.height
//   input    pdoshm[0]    button bitmask: a|b|up|right|left|down|home|reserved
//            pdoshm[1..48] 12 darts, 4 bytes each, LE
//
// Dart delivery mirrors pydartsnut's InputHandler: a slot contributes one hit on
// the absent -> present transition, and re-arms only after the slot has been
// absent for IDLE_UNBLOCK_SECONDS. A hit on one of the two chrome buttons below
// the stage runs that button; any other hit outside the 128x96 stage is dropped
// entirely (not clamped to the nearest edge, and it does not fire the dart
// broadcast).
const fs = require('fs');
const path = require('path');
const createGL = require('gl');
const shim = require('./dom_shim');
const {
    PANEL,
    PLAY,
    STAGE,
    computeGeometry,
    isInsideStage,
    controlAt,
    drawControls,
    panelToScratch,
    blitStageIntoPanel
} = require('./panel_geometry');
const {SharedMemory, DartReader, DART_COUNT} = require('./shm_abi');
const {DEFAULT_FPS, frameIntervalMs, nextPresent} = require('./frame_clock');

// Bit order comes from pydartsnut get_buttons().
const BUTTON_BITS = [
    'btn_a', 'btn_b', 'btn_up', 'btn_right',
    'btn_left', 'btn_down', 'btn_home', 'btn_reserved'
];

// Scratch key names, matching what the editor's key hat reports.
const BUTTON_KEYS = {
    btn_a: ' ',
    btn_b: 'x',
    btn_up: 'ArrowUp',
    btn_down: 'ArrowDown',
    btn_left: 'ArrowLeft',
    btn_right: 'ArrowRight'
};
const DART_BROADCAST = 'dart';

// How often the shutdown supervisor wakes to check for a signal. The VM's own
// 60 Hz step timer drives the game; this only bounds how long SIGTERM handling
// waits, and it yields on every iteration so the signal handler always runs.
const SHUTDOWN_POLL_MS = 250;

// Stage variables the runner maintains so a project can read dart state without
// relying on extension blocks. Optional: a project that never declares them
// simply gets nothing set.
const DART_VARIABLES = ['dart_x', 'dart_y', 'dart_index'];

function parseArgs (argv) {
    const args = {
        project: null,
        shm: 'game_shm',
        dataStore: null,
        fps: DEFAULT_FPS,
        scratchVars: true
    };
    for (let i = 2; i < argv.length; i++) {
        const arg = argv[i];
        if (arg === '--project') args.project = argv[++i];
        else if (arg === '--shm') args.shm = argv[++i];
        else if (arg === '--data-store') args.dataStore = argv[++i];
        else if (arg === '--fps') args.fps = Number(argv[++i]) || DEFAULT_FPS;
        else if (arg === '--no-scratch-vars') args.scratchVars = false;
        else if (arg === '--params') {
            try {
                const params = JSON.parse(argv[++i]);
                if (params && typeof params === 'object') {
                    if (params.project) args.project = params.project;
                    if (params.fps) args.fps = Number(params.fps) || DEFAULT_FPS;
                }
            } catch (e) { /* ignore malformed params */ }
        }
    }
    return args;
}

// The install step guarantees exactly one .sb3 at the payload root, but the
// runner does not trust that: a hand-copied project may have zero or several.
function findProject (dir) {
    let entries;
    try {
        entries = fs.readdirSync(dir);
    } catch (e) {
        return {error: `cannot read project directory ${dir}: ${e.message}`};
    }
    // Skip AppleDouble files: a payload staged on macOS can legitimately carry
    // `._game.sb3` beside `game.sb3`, and the install-time validator ignores
    // those. Counting them here would refuse a project the installer accepted.
    const sb3 = entries
        .filter(f => f.toLowerCase().endsWith('.sb3') && !f.startsWith('._'))
        .sort();
    if (sb3.length === 1) {
        return {file: path.join(dir, sb3[0])};
    }
    if (sb3.length > 1) {
        return {error: `expected exactly one .sb3 in ${dir}, found ${sb3.length}: ${sb3.join(', ')}`};
    }
    return {error: `no .sb3 found in ${dir}`};
}

function bootVm (stageWidth = STAGE.width, stageHeight = STAGE.height) {
    shim.installDom();
    const gl = createGL(stageWidth, stageHeight, {
        alpha: false,
        stencil: true,
        antialias: false,
        preserveDrawingBuffer: true
    });
    if (!gl) {
        throw new Error('headless-gl could not create a WebGL context');
    }
    shim.publishWebGLConstructors(gl);

    const canvas = {
        width: stageWidth,
        height: stageHeight,
        clientWidth: stageWidth,
        clientHeight: stageHeight,
        style: {},
        getContext: type => (String(type).startsWith('webgl') ? gl : null),
        addEventListener () {},
        removeEventListener () {},
        getBoundingClientRect: () => ({left: 0, top: 0, width: stageWidth, height: stageHeight})
    };
    gl.canvas = canvas;

    const {ScratchRender} = require('@scratch/scratch-render');
    const VM = require('@scratch/scratch-vm');
    const {ScratchStorage} = require('@scratch/scratch-storage');

    const renderer = new ScratchRender(canvas);
    const vm = new VM();
    vm.attachStorage(new ScratchStorage());
    vm.attachRenderer(renderer);
    // Without this, projects with bitmap costumes reject on load.
    vm.attachV2BitmapAdapter({
        resize: image => image,
        convertDataURIToBinary: uri => new Uint8Array(Buffer.from(uri.split(',')[1], 'base64'))
    });
    return {gl, renderer, vm, stageWidth, stageHeight};
}

// Device input -> Scratch. The only two entry points into the VM.
function createInputBridge (vm, {scratchVars = true} = {}) {
    const held = new Set();
    const stage = () => vm.runtime.getTargetForStage();

    function setStageVariable (name, value) {
        if (!scratchVars) return;
        const target = stage();
        if (!target) return;
        const variable = target.lookupVariableByNameAndType(name, '');
        if (variable) variable.value = value;
    }

    return {
        // pdoshm[0] -> key hats and "key pressed?" reporters. Only buttons that
        // map to a Scratch key are forwarded; home/reserved have no equivalent.
        handleButtonMask (mask) {
            BUTTON_BITS.forEach((button, bit) => {
                const down = Boolean(mask & (1 << bit));
                const key = BUTTON_KEYS[button];
                if (!key) return;
                if (down && !held.has(button)) {
                    held.add(button);
                    vm.postIOData('keyboard', {key, isDown: true});
                    vm.runtime.startHats('event_whenkeypressed', {KEY_OPTION: key});
                } else if (!down && held.has(button)) {
                    held.delete(button);
                    vm.postIOData('keyboard', {key, isDown: false});
                }
            });
        },

        // A dart landing at device pixel (px, py) inside the play area.
        handleDart ({px, py, index}) {
            const {x, y} = panelToScratch(px, py);
            setStageVariable('dart_x', x);
            setStageVariable('dart_y', y);
            setStageVariable('dart_index', index);
            vm.runtime.startHats('event_whenbroadcastreceived', {BROADCAST_OPTION: DART_BROADCAST});
        },

        dartVariables: DART_VARIABLES
    };
}

function openShm ({displayName = 'game_shm', inputName = 'pdoshm'} = {}) {
    const display = new SharedMemory(displayName, 1 + PANEL.width * PANEL.height * 3, true);
    let input = null;
    let dartReader = null;
    try {
        input = new SharedMemory(inputName, 1 + DART_COUNT * 4, false);
        dartReader = new DartReader(input);
    } catch (e) {
        // No pdoshm (e.g. running on a dev machine): render, but deliver no input.
        console.error(`input shm ${inputName} unavailable: ${e.message}`);
    }
    return {
        display,
        input,
        readButtonMask: () => (input ? input.readByte(0) : 0),
        readNewDartHits: () => (dartReader ? dartReader.readNewDartHits() : [])
    };
}

async function main () {
    const args = parseArgs(process.argv);
    if (!args.project) {
        console.error('usage: node sb3_runner.js --project <dir> --shm <name>');
        process.exit(2);
    }
    const projectDir = path.resolve(args.project);
    const found = findProject(projectDir);
    if (found.error) {
        console.error(found.error);
        process.exit(2);
    }

    const {vm, renderer, gl, stageWidth, stageHeight} = bootVm();
    const device = openShm({displayName: args.shm});
    const input = createInputBridge(vm, {scratchVars: args.scratchVars});

    await vm.loadProject(fs.readFileSync(found.file));
    vm.greenFlag();

    const stagePixels = new Uint8Array(stageWidth * stageHeight * 4);
    const panelBytes = Buffer.alloc(PANEL.width * PANEL.height * 3);
    const frameMs = frameIntervalMs(args.fps);
    // performance.now() is monotonic. The panel is often brought up before NTP
    // settles, and a backward wall-clock step would stretch one interval into a
    // multi-second freeze; a forward step is harmless but monotonic is free.
    let deadline = performance.now();

    // The VM steps at its own 60 Hz (THREAD_STEP_INTERVAL) and ends every step by
    // drawing the scene, so that draw is the one place a frame is available. It
    // is wrapped here rather than after the step so the hook cannot be skipped:
    // whatever triggers a draw (the step timer, a resize, a pen snapshot) leaves
    // the framebuffer consistent, and only a draw that lands on a presentation
    // slot is read back and sent to the panel.
    //
    // Presenting on every step instead would read back 480x360 and blit 60 times
    // a second for a panel that shows 30. Drawing twice per step (the shape this
    // replaced) did that and doubled the GL work on top of it.
    const drawScene = renderer.draw.bind(renderer);
    renderer.draw = () => {
        drawScene();
        const now = performance.now();
        const pacing = nextPresent(deadline, now, frameMs);
        deadline = pacing.deadline;
        if (pacing.present) presentFrame();
    };

    function presentFrame () {
        // Device input is sampled here rather than on a timer: the panel's frame
        // is the only consumer, and pydartsnut's 200 ms idle-unblock window is
        // far longer than a dropped sample, so 30 Hz polling cannot miss a dart
        // that a faster poll would have caught.
        input.handleButtonMask(device.readButtonMask());
        for (const hit of device.readNewDartHits()) {
            // A dart on one of the two chrome buttons below the stage does what
            // the same button does in the editor: green flag restarts the
            // project, stop halts every script. Checked first, because the band
            // used to be letterbox and a hit there was simply dropped.
            const control = controlAt(hit.px, hit.py);
            if (control === 'flag') {
                vm.greenFlag();
                continue;
            }
            if (control === 'stop') {
                vm.stopAll();
                continue;
            }
            // Anything else outside the stage is dropped, not clamped: the play
            // area is 128x128 but the 4:3 stage only fills the top 128x96.
            if (!isInsideStage(hit.px, hit.py)) continue;
            input.handleDart(hit);
        }
        gl.readPixels(0, 0, stageWidth, stageHeight, gl.RGBA, gl.UNSIGNED_BYTE, stagePixels);
        // The control band outlives this process, so it is painted every frame
        // rather than once: a stale frame from a previous project must not
        // survive below the stage.
        panelBytes.fill(0);
        blitStageIntoPanel(stagePixels, stageWidth, stageHeight, panelBytes);
        drawControls(panelBytes);
        // shm[0] == 2 means the consumer is mid-read; skip this write rather
        // than tearing the frame it is reading.
        if (device.display.byteAt(0) !== 2) {
            device.display.write(1, panelBytes);
            device.display.setByte(0, 0);
        }
    }

    // start() installs the 60 Hz step timer and sets currentStepTime, which is
    // the sequencer's per-step work budget. Both are wanted as-is; the previous
    // loop cleared the timer and stepped by hand, which tied script speed to the
    // panel rate and halved every project's speed against the editor.
    vm.runtime.start();

    let running = true;
    for (const signal of ['SIGINT', 'SIGTERM']) {
        process.on(signal, () => { running = false; });
    }
    // The step timer keeps this process alive; the loop only supervises shutdown
    // and never starves the event loop (it yields on every iteration, including
    // when a frame overran its slot), so a signal is always delivered.
    while (running) {
        await new Promise(resolve => setTimeout(resolve, SHUTDOWN_POLL_MS));
    }
    // quit() clears the 60 Hz step timer. Without it the timer outlives this
    // function, and the draw hook keeps presenting through a closed segment.
    vm.runtime.quit();
    device.display.close();
    if (device.input) device.input.close();
}

if (require.main === module) {
    main().catch(err => {
        const detail = err && err.stack ? err.stack.split('\n').slice(0, 6).join('\n') : String(err);
        console.error('runner failed:', detail);
        process.exit(1);
    });
}

module.exports = {
    blitStageIntoPanel,
    bootVm,
    createInputBridge,
    parseArgs,
    findProject,
    BUTTON_BITS,
    BUTTON_KEYS,
    DEFAULT_FPS,
    DART_BROADCAST,
    DART_VARIABLES,
    PLAY
};
