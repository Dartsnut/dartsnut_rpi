/**
 * Firestore bridge: connects to Python over a Unix socket, uses Firebase JS client
 * for Firestore (getDoc, setDoc, onSnapshot). Python listens; we connect and
 * speak newline-delimited JSON. Config is in source so it is bundled into the compiled executable.
 *
 * CLI: --device-id=<ble_suffix> --socket-path=<path>
 */

import { createConnection } from "node:net";
import { initializeApp } from "firebase/app";
import {
  initializeFirestore,
  doc,
  getDoc,
  setDoc,
  onSnapshot,
  type DocumentSnapshot,
} from "firebase/firestore";

const DEVICES_COLLECTION = "devices";

const firebaseConfig = {
  apiKey: "AIzaSyAiroI1etKgI8WfFR2rdQ5JNpqrVY-llEw",
  authDomain: "my-dartsnut.firebaseapp.com",
  projectId: "my-dartsnut",
  storageBucket: "my-dartsnut.firebasestorage.app",
  messagingSenderId: "610145404797",
  appId: "1:610145404797:web:bbcd208490f7ad1272ae14",
  measurementId: "G-ZZPVVZCE3F",
};

const app = initializeApp(firebaseConfig);
// Use long-polling instead of gRPC to avoid "Could not reach Cloud Firestore backend"
// and "GRPC error has no .code" on some networks/runtimes (e.g. Pi).
const db = initializeFirestore(app, { experimentalForceLongPolling: true });

function parseArgs(): { deviceId: string; socketPath: string } {
  let deviceId = "";
  let socketPath = "";
  for (const arg of process.argv.slice(2)) {
    if (arg.startsWith("--device-id=")) deviceId = arg.slice("--device-id=".length).trim();
    if (arg.startsWith("--socket-path=")) socketPath = arg.slice("--socket-path=".length).trim();
  }
  if (!deviceId || !socketPath) {
    console.error("Usage: --device-id=<ble_suffix> --socket-path=<path>");
    process.exit(1);
  }
  return { deviceId, socketPath };
}

function send(socket: NodeJS.WritableStream, kind: string, payload: unknown): void {
  const line = JSON.stringify({ kind, payload }) + "\n";
  socket.write(line);
}

function parseIncoming(buffer: string): { kind: string; payload: unknown } | null {
  const line = buffer.trim();
  if (!line) return null;
  try {
    const msg = JSON.parse(line) as { kind?: string; payload?: unknown };
    return { kind: msg.kind ?? "", payload: msg.payload };
  } catch {
    return null;
  }
}

const INITIAL_RETRY_DELAY_MS = 2000;
const MAX_INITIAL_RETRIES = 5;

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function getDocWithRetry(
  docRef: ReturnType<typeof doc>
): Promise<Awaited<ReturnType<typeof getDoc>>> {
  let lastErr: unknown;
  for (let attempt = 0; attempt < MAX_INITIAL_RETRIES; attempt++) {
    try {
      const snapshot = await getDoc(docRef);
      return snapshot;
    } catch (e) {
      lastErr = e;
      if (attempt < MAX_INITIAL_RETRIES - 1) {
        await delay(INITIAL_RETRY_DELAY_MS);
        continue;
      }
      throw e;
    }
  }
  throw lastErr;
}

async function main(): Promise<void> {
  const { deviceId, socketPath } = parseArgs();
  const docRef = doc(db, DEVICES_COLLECTION, deviceId);

  const socket = createConnection(socketPath, () => {
    send(socket, "ready", {});
  });

  let readBuffer = "";
  let unsubscribe: (() => void) | null = null;

  socket.on("data", async (chunk: Buffer) => {
    readBuffer += chunk.toString("utf8");
    let idx: number;
    while ((idx = readBuffer.indexOf("\n")) >= 0) {
      const line = readBuffer.slice(0, idx);
      readBuffer = readBuffer.slice(idx + 1);
      const msg = parseIncoming(line);
      if (!msg) continue;

      if (msg.kind === "initial_state") {
        const payload = msg.payload as Record<string, unknown> | undefined;
        if (!payload || typeof payload !== "object") continue;
        try {
          const snapshot = await getDocWithRetry(docRef);
          if (!snapshot.exists()) {
            await setDoc(docRef, payload);
          } else {
            const data = snapshot.data() ?? {};
            send(socket, "config", data);
          }
          unsubscribe = onSnapshot(
            docRef,
            (snap: DocumentSnapshot) => {
              const data = snap.data() ?? {};
              send(socket, "config", data);
            },
            (err: unknown) => console.error("Firestore onSnapshot error:", err)
          );
        } catch (e) {
          console.error("Firestore initial_state error:", e);
        }
        continue;
      }

      if (msg.kind === "device_state") {
        const payload = msg.payload as Record<string, unknown> | undefined;
        if (!payload || typeof payload !== "object") continue;
        try {
          await setDoc(docRef, payload, { merge: true });
        } catch (e) {
          console.error("Firestore device_state error:", e);
        }
      }
    }
  });

  socket.on("error", (err) => {
    console.error("Socket error:", err);
    if (unsubscribe) unsubscribe();
    process.exitCode = 1;
  });
  socket.on("close", () => {
    if (unsubscribe) unsubscribe();
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
