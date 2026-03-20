package deviceid

import (
	"os"
	"path/filepath"
	"testing"
)

func TestResolveFromBluetoothSysfs_FirstAdapter(t *testing.T) {
	root := t.TempDir()
	bluetoothDir := filepath.Join(root, "class", "bluetooth")
	if err := os.MkdirAll(filepath.Join(bluetoothDir, "hci0"), 0o755); err != nil {
		t.Fatalf("mkdir hci0: %v", err)
	}
	if err := os.MkdirAll(filepath.Join(bluetoothDir, "hci1"), 0o755); err != nil {
		t.Fatalf("mkdir hci1: %v", err)
	}

	if err := os.WriteFile(filepath.Join(bluetoothDir, "hci1", "address"), []byte("AA:BB:CC:DD:EE:FF\n"), 0o644); err != nil {
		t.Fatalf("write address: %v", err)
	}
	if err := os.WriteFile(filepath.Join(bluetoothDir, "hci0", "address"), []byte("11:22:33:44:55:66"), 0o644); err != nil {
		t.Fatalf("write address: %v", err)
	}

	got, err := resolveFromBluetoothSysfs(root)
	if err != nil {
		t.Fatalf("resolveFromBluetoothSysfs returned error: %v", err)
	}

	// hci0 should be preferred when present.
	if got != "11:22:33:44:55:66" {
		t.Fatalf("unexpected device id: got %q", got)
	}
}

func TestResolveFromBluetoothSysfs_NoAddress(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "class", "bluetooth", "hci0"), 0o755); err != nil {
		t.Fatalf("mkdir hci0: %v", err)
	}

	prevExec := execCommand
	execCommand = func(name string, args ...string) ([]byte, error) {
		return nil, os.ErrNotExist
	}
	defer func() { execCommand = prevExec }()

	if _, err := resolveFromBluetoothSysfs(root); err == nil {
		t.Fatal("expected error when no address file exists")
	}
}

func TestResolveFromBluetoothSysfs_HciSymlink(t *testing.T) {
	root := t.TempDir()
	bluetoothDir := filepath.Join(root, "class", "bluetooth")
	devicesDir := filepath.Join(root, "devices")
	if err := os.MkdirAll(bluetoothDir, 0o755); err != nil {
		t.Fatalf("mkdir bluetooth dir: %v", err)
	}
	if err := os.MkdirAll(filepath.Join(devicesDir, "bt0"), 0o755); err != nil {
		t.Fatalf("mkdir bt0: %v", err)
	}
	if err := os.WriteFile(filepath.Join(devicesDir, "bt0", "address"), []byte("AA:BB:CC:DD:EE:FF"), 0o644); err != nil {
		t.Fatalf("write address: %v", err)
	}
	if err := os.Symlink(filepath.Join(devicesDir, "bt0"), filepath.Join(bluetoothDir, "hci0")); err != nil {
		t.Fatalf("create hci0 symlink: %v", err)
	}

	got, err := resolveFromBluetoothSysfs(root)
	if err != nil {
		t.Fatalf("resolveFromBluetoothSysfs returned error: %v", err)
	}
	if got != "AA:BB:CC:DD:EE:FF" {
		t.Fatalf("unexpected device id: got %q", got)
	}
}

func TestParseHciConfigAddress(t *testing.T) {
	out := `hci0:	Type: Primary  Bus: UART
	BD Address: 88:A2:9E:28:B3:FE  ACL MTU: 1021:8  SCO MTU: 64:1
	UP RUNNING`
	got, ok := parseHciConfigAddress(out)
	if !ok {
		t.Fatal("expected to parse BD Address")
	}
	if got != "88:A2:9E:28:B3:FE" {
		t.Fatalf("unexpected parsed address: %q", got)
	}
}

func TestParseBluetoothctlListAddress(t *testing.T) {
	out := "Controller 88:A2:9E:28:B3:FE PixelDart-b3fe [default]"
	got, ok := parseBluetoothctlListAddress(out)
	if !ok {
		t.Fatal("expected to parse bluetoothctl list output")
	}
	if got != "88:A2:9E:28:B3:FE" {
		t.Fatalf("unexpected parsed address: %q", got)
	}
}

func TestResolveFromBluetoothSysfs_FallsBackToCommands(t *testing.T) {
	root := t.TempDir()
	bluetoothDir := filepath.Join(root, "class", "bluetooth")
	if err := os.MkdirAll(filepath.Join(bluetoothDir, "hci0"), 0o755); err != nil {
		t.Fatalf("mkdir hci0: %v", err)
	}

	prevExec := execCommand
	execCommand = func(name string, args ...string) ([]byte, error) {
		if name == "hciconfig" {
			return []byte("BD Address: 88:A2:9E:28:B3:FE"), nil
		}
		return nil, os.ErrNotExist
	}
	defer func() { execCommand = prevExec }()

	got, err := resolveFromBluetoothSysfs(root)
	if err != nil {
		t.Fatalf("expected fallback success, got error: %v", err)
	}
	if got != "88:A2:9E:28:B3:FE" {
		t.Fatalf("unexpected address: %q", got)
	}
}
