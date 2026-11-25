// Minimal DOM for running scratch-vm + scratch-render in plain Node.
//
// scratch-render is browser code. It needs: document.createElement('canvas')
// with a real 2D context (node-canvas), <img> elements that can decode SVG data
// URIs, WebGL* constructors published as globals (twgl does
// `x instanceof WebGLTexture`), and ImageData that headless-gl can actually
// upload (a bare typed array, not an object).
//
// This module installs process-global state, so call installDom() exactly once
// per process, before requiring scratch-vm or scratch-render.
const {JSDOM} = require('jsdom');
const {createCanvas, Image: NodeCanvasImage, CanvasRenderingContext2D} = require('canvas');

// A real-looking ImageData: scratch-render's Silhouette reads `.data`, and
// headless-gl's extractImageData only accepts an object with width/height/data.
class CompatImageData {
    constructor (width, height) {
        this.width = width;
        this.height = height;
        this.data = new Uint8ClampedArray(width * height * 4);
    }
}

// node-canvas returns its own ImageData object; headless-gl's texImage2D only
// accepts ArrayBufferViews and silently uploads zeros otherwise (blank SVG MIPs).
let patched = false;
function patchCanvasImageData () {
    if (patched) return;
    patched = true;
    const nativeGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    const nativePutImageData = CanvasRenderingContext2D.prototype.putImageData;
    const nativeCreateImageData = CanvasRenderingContext2D.prototype.createImageData;

    CanvasRenderingContext2D.prototype.getImageData = function (x, y, w, h, ...rest) {
        const native = nativeGetImageData.call(this, x, y, w, h, ...rest);
        const out = new CompatImageData(native.width, native.height);
        out.data.set(native.data);
        return out;
    };
    CanvasRenderingContext2D.prototype.createImageData = function (w, h) {
        return typeof w === 'object'
            ? new CompatImageData(w.width, w.height)
            : new CompatImageData(w, h);
    };
    CanvasRenderingContext2D.prototype.putImageData = function (img, dx, dy) {
        // node-canvas requires its own ImageData instance; ours is a look-alike.
        const native = nativeCreateImageData.call(this, img.width, img.height);
        native.data.set(img.data);
        return nativePutImageData.call(this, native, dx, dy);
    };
}

// SVGSkin sets `img.src = 'data:image/svg+xml;utf8,...'` and reads naturalWidth/
// naturalHeight. node-canvas only accepts base64 SVG data URIs, and exposes no
// natural* properties or addEventListener, so wrap it here.
class ShimImage extends NodeCanvasImage {
    set src (value) {
        if (typeof value === 'string' && value.startsWith('data:image/svg+xml;utf8,')) {
            const body = decodeURIComponent(value.slice('data:image/svg+xml;utf8,'.length));
            value = 'data:image/svg+xml;base64,' + Buffer.from(body, 'utf8').toString('base64');
        }
        super.src = value;
    }

    get src () { return super.src; }
    get naturalWidth () { return this.width; }
    get naturalHeight () { return this.height; }
    addEventListener (type, fn) { this['on' + type] = fn; }
    removeEventListener (type) { delete this['on' + type]; }
}

function installDom () {
    patchCanvasImageData();
    // `runScripts` is required, not optional: scratch-svg-renderer measures a
    // vector costume's real bounds by posting it into a sandboxed iframe and
    // running a script there. Without script execution in frames that call
    // never gets an answer, so every SVG costume waits out the sandbox's 30 s
    // timeout and then falls back to wrong dimensions. Projects with several
    // vector costumes would stall the load for minutes.
    //
    // Our location is not http(s), so the sandbox delivers its script inline
    // rather than by URL, and no `resources` loader is needed.
    const dom = new JSDOM('<!doctype html><html><body></body></html>', {
        pretendToBeVisual: true,
        runScripts: 'dangerously'
    });
    const {window} = dom;

    // jsdom canvas has no 2D backing store: back each element with node-canvas,
    // and keep that backing store in sync when scratch-render resizes by
    // assigning width/height directly.
    const proto = window.HTMLCanvasElement.prototype;
    proto.__w = 300;
    proto.__h = 150;
    proto.getContext = function (type) {
        if (String(type).startsWith('webgl')) return null;   // headless-gl is wired separately
        if (!this.__nc) this.__nc = createCanvas(this.__w, this.__h);
        return this.__nc.getContext('2d');
    };
    for (const dim of ['width', 'height']) {
        Object.defineProperty(proto, dim, {
            configurable: true,
            get () { return this['__' + dim[0]]; },
            set (value) {
                this['__' + dim[0]] = Number(value);
                if (this.__nc) {
                    this.__nc.width = this.__w;
                    this.__nc.height = this.__h;
                }
            }
        });
    }
    Object.defineProperty(proto, 'toDataURL', {
        value: function (...args) { return this.__nc ? this.__nc.toDataURL(...args) : 'data:,'; }
    });

    const makeElement = window.document.createElement.bind(window.document);
    window.document.createElement = function (tagName, options) {
        if (String(tagName).toLowerCase() === 'img') return new ShimImage();
        return makeElement(tagName, options);
    };

    for (const key of ['window', 'document', 'navigator', 'HTMLCanvasElement', 'HTMLImageElement',
        'DOMParser', 'XMLSerializer', 'XMLHttpRequest', 'Blob', 'URL', 'Node', 'Element',
        'requestAnimationFrame', 'cancelAnimationFrame', 'HTMLVideoElement', 'HTMLMediaElement',
        'AudioContext', 'webkitAudioContext', 'MediaStream', 'MediaStreamTrack', 'MediaRecorder',
        'Worker', 'FileReader', 'FormData', 'Path2D']) {
        if (window[key] !== undefined) global[key] = window[key];
    }
    global.Image = ShimImage;
    global.ImageData = CompatImageData;
    global.getComputedStyle = window.getComputedStyle.bind(window);
    return dom;
}

// headless-gl returns real WebGL* objects but never publishes the constructors;
// twgl's `instanceof WebGLTexture` checks then fail with "unknown attachment
// type". Export each constructor from a throwaway instance.
function publishWebGLConstructors (gl) {
    const make = {
        WebGLTexture: () => gl.createTexture(),
        WebGLRenderbuffer: () => gl.createRenderbuffer(),
        WebGLFramebuffer: () => gl.createFramebuffer(),
        WebGLBuffer: () => gl.createBuffer(),
        WebGLShader: () => gl.createShader(gl.VERTEX_SHADER),
        WebGLProgram: () => gl.createProgram()
    };
    for (const [name, factory] of Object.entries(make)) global[name] = factory().constructor;
}

module.exports = {installDom, publishWebGLConstructors, CompatImageData, ShimImage};
