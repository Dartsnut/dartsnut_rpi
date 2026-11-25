// Frame pacing for the runner.
//
// Two rates matter and they are not the same:
//
//   step rate     scratch-vm's THREAD_STEP_INTERVAL, 1000/60. A project's timing
//                 blocks ("wait 1 seconds", glide durations, the timer) and its
//                 per-step motion all assume it. Runtime.start() schedules at
//                 this rate and sets currentStepTime from it.
//   present rate  how often the panel actually receives a frame. The panel is
//                 30 Hz, and each present costs a 480x360 readPixels plus the
//                 blit, so presenting on every step is wasted work.
//
// Driving the step loop by hand at the panel rate instead (stepping 30 times a
// second) halves every project's speed against the editor: "repeat 10 [move 10
// steps]" takes 0.33 s rather than 0.17 s, and motion and collision feel halved.
// So the runner leaves Runtime.start()'s 60 Hz timer alone and gates only the
// presentation, which happens inside the draw the VM already performs.
//
// Kept separate from the runner because it is the part that can be asserted
// exactly: no native dependencies, so the arithmetic is testable without a GL
// context or a device.
const DEFAULT_FPS = 30;

// The presentation interval for a `--fps` value. An out-of-range value falls
// back to the default rather than producing a zero or negative interval.
function frameIntervalMs (fps = DEFAULT_FPS) {
    if (!Number.isFinite(fps) || fps <= 0) return 1000 / DEFAULT_FPS;
    return 1000 / fps;
}

// Decide whether the frame being drawn right now should be presented, and when
// the following one is due.
//
// Returns {present, deadline}. `present` is true when `now` has reached the next
// presentation slot, in which case the caller sends this frame to the panel and
// the deadline advances by one interval.
//
// The deadline advances by whole intervals rather than being recomputed from
// `now`, so per-step overhead does not accumulate into drift: after N presents it
// is start + N * frameMs regardless of how long each present took.
//
// When the loop falls behind (a project too slow for 30 fps, so `now` is already
// past the next slot) the deadline is held at most one interval behind `now`.
// That keeps every subsequent frame presentable and bounds the catch-up: a long
// stall must not turn into a burst of presents to "repay" frames the panel never
// could have shown.
function nextPresent (deadline, now, frameMs) {
    if (!Number.isFinite(deadline)) return {present: false, deadline: now + frameMs};
    if (now >= deadline) {
        const next = deadline + frameMs;
        const floor = now - frameMs;
        return {present: true, deadline: next < floor ? floor : next};
    }
    return {present: false, deadline};
}

module.exports = {
    DEFAULT_FPS,
    frameIntervalMs,
    nextPresent
};
