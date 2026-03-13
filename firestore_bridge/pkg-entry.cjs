#!/usr/bin/env node

// CommonJS wrapper that boots the ESM build output.
// pkg will use this as the binary entrypoint.
import('./dist/index.js').catch((err) => {
  console.error('Failed to start Firestore bridge:', err);
  process.exit(1);
});

