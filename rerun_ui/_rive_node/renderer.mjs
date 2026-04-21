import fs from 'node:fs/promises';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';

import { Canvas, DOMMatrix, Image, ImageData, Path2D } from 'skia-canvas';
import RiveCanvas from '@rive-app/canvas-advanced';

const SCRIPT_PATH = fileURLToPath(import.meta.url);
const SCRIPT_DIR = path.dirname(SCRIPT_PATH);
const WASM_PATH = path.join(SCRIPT_DIR, 'node_modules', '@rive-app', 'canvas-advanced', 'rive.wasm');

function writeProtocolMessage(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function formatError(error) {
  if (error instanceof Error) {
    return error.stack ?? error.message;
  }
  return String(error);
}

function installConsoleRedirect() {
  const redirect = (level) => (...args) => {
    const message = args.map((value) => (typeof value === 'string' ? value : JSON.stringify(value))).join(' ');
    process.stderr.write(`[${level}] ${message}\n`);
  };

  console.log = redirect('log');
  console.warn = redirect('warn');
  console.error = redirect('error');
}

function makeCanvas(width, height) {
  const canvas = new Canvas(width, height);
  canvas.style = {};
  canvas.clientWidth = width;
  canvas.clientHeight = height;
  canvas.getBoundingClientRect = () => ({
    x: 0,
    y: 0,
    width,
    height,
    top: 0,
    left: 0,
    right: width,
    bottom: height,
  });
  return canvas;
}

function installDomPolyfills() {
  const document = {
    currentScript: { src: SCRIPT_PATH },
    createElement(tagName) {
      if (tagName === 'canvas') {
        return makeCanvas(64, 64);
      }
      return { style: {}, appendChild() {}, remove() {} };
    },
    body: { appendChild() {}, remove() {} },
  };

  globalThis.window = globalThis;
  globalThis.document = document;
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { userAgent: 'node' },
  });
  globalThis.requestAnimationFrame = (callback) => setTimeout(() => callback(performance.now()), 16);
  globalThis.cancelAnimationFrame = (requestId) => clearTimeout(requestId);
  globalThis.Image = Image;
  globalThis.ImageData = ImageData;
  globalThis.Path2D = Path2D;
  globalThis.DOMMatrix = DOMMatrix;
  globalThis.HTMLCanvasElement = Canvas;
  globalThis.OffscreenCanvas = Canvas;
  globalThis.devicePixelRatio = 1;
}

function parseConfig() {
  if (!process.argv[2]) {
    throw new Error('missing JSON renderer config argument');
  }

  const config = JSON.parse(process.argv[2]);
  const width = Number.parseInt(String(config.width), 10);
  const height = Number.parseInt(String(config.height), 10);

  if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(height) || height <= 0) {
    throw new Error('width and height must be positive integers');
  }
  if (typeof config.riv_path !== 'string' || config.riv_path.trim() === '') {
    throw new Error('riv_path must be a non-empty string');
  }
  if (typeof config.artboard !== 'string' || config.artboard.trim() === '') {
    throw new Error('artboard must be a non-empty string');
  }
  if (typeof config.state_machine !== 'string' || config.state_machine.trim() === '') {
    throw new Error('state_machine must be a non-empty string');
  }

  return {
    rivPath: config.riv_path,
    artboard: config.artboard,
    stateMachine: config.state_machine,
    width,
    height,
  };
}

async function loadRuntime() {
  const wasmBytes = await fs.readFile(WASM_PATH);
  const wasmBinary = wasmBytes.buffer.slice(
    wasmBytes.byteOffset,
    wasmBytes.byteOffset + wasmBytes.byteLength,
  );
  return await RiveCanvas({ wasmBinary });
}

function buildInputRegistry(rive, stateMachine) {
  const inputs = new Map();
  for (let index = 0; index < stateMachine.inputCount(); index += 1) {
    const input = stateMachine.input(index);
    if (input.type === rive.SMIInput.number) {
      inputs.set(input.name, { kind: 'number', value: input.asNumber() });
    } else if (input.type === rive.SMIInput.bool) {
      inputs.set(input.name, { kind: 'bool', value: input.asBool() });
    } else if (input.type === rive.SMIInput.trigger) {
      inputs.set(input.name, { kind: 'trigger', value: input.asTrigger() });
    } else {
      throw new Error(`unsupported state machine input type ${input.type} for ${input.name}`);
    }
  }
  return inputs;
}

function stateMachineInputsAsObject(inputs) {
  return Object.fromEntries(Array.from(inputs.entries(), ([name, entry]) => [name, entry.kind]));
}

function ensureInput(state, inputName, expectedKind) {
  const input = state.inputs.get(inputName);
  if (!input) {
    throw new Error(`state machine input '${inputName}' was not found`);
  }
  if (input.kind !== expectedKind) {
    throw new Error(`state machine input '${inputName}' is ${input.kind}, expected ${expectedKind}`);
  }
  return input.value;
}

function settleArtboard(state, dtSeconds) {
  state.stateMachine.advanceAndApply(dtSeconds);
  state.artboard.advance(dtSeconds);
}

function renderFrame(state) {
  settleArtboard(state, 0);
  state.renderer.clear();
  state.renderer.save();
  state.renderer.align(
    state.rive.Fit.contain,
    state.rive.Alignment.center,
    {
      minX: 0,
      minY: 0,
      maxX: state.width,
      maxY: state.height,
    },
    state.artboard.bounds,
  );
  state.artboard.draw(state.renderer);
  state.renderer.restore();
  state.renderer.flush();
  state.rive.resolveAnimationFrame();

  const imageData = state.canvas.getContext('2d').getImageData(0, 0, state.width, state.height);
  return Buffer.from(imageData.data).toString('base64');
}

async function createRendererState() {
  installConsoleRedirect();
  installDomPolyfills();

  const config = parseConfig();
  const rive = await loadRuntime();
  const rivBytes = await fs.readFile(config.rivPath);
  const file = await rive.load(new Uint8Array(rivBytes));
  const artboard = file.artboardByName(config.artboard);
  if (!artboard) {
    throw new Error(`artboard '${config.artboard}' was not found in ${config.rivPath}`);
  }

  const stateMachineDefinition = artboard.stateMachineByName(config.stateMachine);
  if (!stateMachineDefinition) {
    throw new Error(`state machine '${config.stateMachine}' was not found on artboard '${config.artboard}'`);
  }

  const stateMachine = new rive.StateMachineInstance(stateMachineDefinition, artboard);
  const canvas = makeCanvas(config.width, config.height);
  const renderer = rive.makeRenderer(canvas);
  const inputs = buildInputRegistry(rive, stateMachine);
  settleArtboard({ stateMachine, artboard }, 0);

  return {
    rive,
    file,
    artboard,
    stateMachine,
    renderer,
    canvas,
    inputs,
    width: config.width,
    height: config.height,
  };
}

function cleanupRendererState(state) {
  const errors = [];
  const cleanup = (label, callback) => {
    try {
      callback();
    } catch (error) {
      errors.push(`${label}: ${formatError(error)}`);
    }
  };

  if (state.stateMachine) {
    cleanup('stateMachine.delete', () => state.stateMachine.delete());
    state.stateMachine = null;
  }
  if (state.artboard) {
    cleanup('artboard.delete', () => state.artboard.delete());
    state.artboard = null;
  }
  if (state.file) {
    cleanup('file.delete', () => state.file.delete());
    state.file = null;
  }
  if (state.renderer) {
    cleanup('renderer.delete', () => state.renderer.delete());
    state.renderer = null;
  }
  if (state.rive) {
    cleanup('rive.cleanup', () => state.rive.cleanup());
    state.rive = null;
  }

  if (errors.length > 0) {
    throw new Error(errors.join('\n'));
  }
}

function handleCommand(state, command) {
  if (!command || typeof command !== 'object') {
    throw new Error('command must be a JSON object');
  }

  switch (command.op) {
    case 'set_bool': {
      ensureInput(state, command.input_name, 'bool').value = Boolean(command.value);
      return { ok: true };
    }
    case 'set_number': {
      const numericValue = Number(command.value);
      if (!Number.isFinite(numericValue)) {
        throw new Error('number input values must be finite');
      }
      ensureInput(state, command.input_name, 'number').value = numericValue;
      return { ok: true };
    }
    case 'fire_trigger': {
      ensureInput(state, command.input_name, 'trigger').fire();
      return { ok: true };
    }
    case 'advance': {
      const dtSeconds = Number(command.dt_s);
      if (!Number.isFinite(dtSeconds) || dtSeconds < 0) {
        throw new Error('dt_s must be a finite, non-negative number');
      }
      settleArtboard(state, dtSeconds);
      return { ok: true };
    }
    case 'render': {
      return {
        ok: true,
        width: state.width,
        height: state.height,
        rgba_b64: renderFrame(state),
      };
    }
    case 'close': {
      cleanupRendererState(state);
      return { ok: true, closing: true };
    }
    default:
      throw new Error(`unsupported command '${command.op}'`);
  }
}

async function main() {
  const state = await createRendererState();
  writeProtocolMessage({ ok: true, event: 'ready', inputs: stateMachineInputsAsObject(state.inputs) });

  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  try {
    for await (const line of input) {
      if (!line.trim()) {
        continue;
      }

      let response;
      try {
        response = handleCommand(state, JSON.parse(line));
      } catch (error) {
        response = { ok: false, error: formatError(error) };
      }

      writeProtocolMessage(response);
      if (response.closing) {
        break;
      }
    }
  } finally {
    input.close();
    if (state.rive) {
      cleanupRendererState(state);
    }
  }
}

main().catch((error) => {
  writeProtocolMessage({ ok: false, error: formatError(error) });
  process.exitCode = 1;
});
