package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/dartsnut/firestore_bridge/internal/auth/firebaseauth"
	"github.com/dartsnut/firestore_bridge/internal/bridge"
	"github.com/dartsnut/firestore_bridge/internal/config"
	"github.com/dartsnut/firestore_bridge/internal/deviceid"
	fsclient "github.com/dartsnut/firestore_bridge/internal/firestore/client"
)

func main() {
	var socketPath string

	flag.StringVar(&socketPath, "socket-path", "", "Unix domain socket path")
	flag.Parse()

	if socketPath == "" {
		fmt.Fprintln(os.Stderr, "Usage: bridge --socket-path=<path>")
		os.Exit(1)
	}

	deviceID, err := deviceid.Resolve()
	if err != nil {
		fmt.Fprintln(os.Stderr, "failed to determine local BLE MAC:", err)
		os.Exit(1)
	}

	ctx, cancel := signalContext()
	defer cancel()

	authClient := firebaseauth.NewClient(firebaseauth.Config{
		APIKey:   config.APIKey,
		Email:    config.Email,
		Password: config.Password,
	})

	fs, err := fsclient.New(ctx, authClient)
	if err != nil {
		fmt.Fprintln(os.Stderr, "failed to create Firestore client:", err)
		os.Exit(1)
	}
	defer fs.Close()

	if err := bridge.Run(ctx, deviceID, socketPath, fs); err != nil && err != context.Canceled {
		fmt.Fprintln(os.Stderr, "bridge error:", err)
		os.Exit(1)
	}
}

func signalContext() (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancel(context.Background())
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-ch
		cancel()
	}()
	return ctx, cancel
}

