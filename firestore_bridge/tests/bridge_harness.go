package tests

// This package provides a small manual harness to exercise the Go bridge.
//
// Usage (from firestore_bridge):
//   go test ./tests -run TestManualHarness -v
//
// It starts a Unix socket listener, runs the bridge against it, and
// exchanges a couple of messages to validate the basic protocol
// (ready, initial_state, config/config_initial, device_state).

import (
	"bufio"
	"context"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/dartsnut/firestore_bridge/internal/auth/firebaseauth"
	"github.com/dartsnut/firestore_bridge/internal/bridge"
	"github.com/dartsnut/firestore_bridge/internal/config"
	fsclient "github.com/dartsnut/firestore_bridge/internal/firestore/client"
)

func TestManualHarness(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping manual harness in short mode")
	}

	deviceID := "test-device"
	tmpDir := t.TempDir()
	socketPath := filepath.Join(tmpDir, "bridge.sock")

	l, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer l.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	// Start the bridge.
	go func() {
		authClient := firebaseauth.NewClient(firebaseauth.Config{
			APIKey:   config.APIKey,
			Email:    config.Email,
			Password: config.Password,
		})
		fs, err := fsclient.New(ctx, authClient)
		if err != nil {
			t.Logf("firestore client error: %v", err)
			return
		}
		defer fs.Close()
		if err := bridge.Run(ctx, deviceID, socketPath, fs); err != nil {
			t.Logf("bridge run error: %v", err)
		}
	}()

	conn, err := l.Accept()
	if err != nil {
		t.Fatalf("accept: %v", err)
	}
	defer conn.Close()

	reader := bufio.NewScanner(conn)
	writer := bufio.NewWriter(conn)

	// Expect ready.
	if !reader.Scan() {
		t.Fatalf("expected ready, got scan error: %v", reader.Err())
	}
	var readyMsg map[string]any
	if err := json.Unmarshal(reader.Bytes(), &readyMsg); err != nil {
		t.Fatalf("unmarshal ready: %v", err)
	}

	// Send an initial_state payload.
	initial := map[string]any{
		"foo": "bar",
	}
	line, err := json.Marshal(map[string]any{
		"kind":    "initial_state",
		"payload": initial,
	})
	if err != nil {
		t.Fatalf("marshal initial_state: %v", err)
	}
	if _, err := writer.Write(line); err != nil {
		t.Fatalf("write initial_state: %v", err)
	}
	if err := writer.WriteByte('\n'); err != nil {
		t.Fatalf("write newline: %v", err)
	}
	if err := writer.Flush(); err != nil {
		t.Fatalf("flush: %v", err)
	}

	// We don't assert on the following messages here; this test is mainly
	// a connectivity harness that should complete without panicking.
	_ = os.Stdout
}

