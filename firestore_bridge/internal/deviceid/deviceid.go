package deviceid

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

const defaultSysfsRoot = "/sys"

var macPattern = regexp.MustCompile(`(?i)([0-9a-f]{2}:){5}[0-9a-f]{2}`)
var execCommand = func(name string, args ...string) ([]byte, error) {
	return exec.Command(name, args...).Output()
}

// Resolve returns the device identifier derived from the local BLE adapter MAC.
func Resolve() (string, error) {
	return resolveFromBluetoothSysfs(defaultSysfsRoot)
}

func resolveFromBluetoothSysfs(sysfsRoot string) (string, error) {
	bluetoothDir := filepath.Join(sysfsRoot, "class", "bluetooth")
	entries, err := os.ReadDir(bluetoothDir)
	if err != nil {
		return "", fmt.Errorf("read bluetooth adapters: %w", err)
	}

	var adapterNames []string
	for _, entry := range entries {
		name := entry.Name()
		if strings.HasPrefix(name, "hci") {
			adapterNames = append(adapterNames, name)
		}
	}

	if len(adapterNames) == 0 {
		return "", fmt.Errorf("no bluetooth adapters found in %s", bluetoothDir)
	}

	sort.Strings(adapterNames)
	for _, name := range adapterNames {
		addressPath := filepath.Join(bluetoothDir, name, "address")
		raw, readErr := os.ReadFile(addressPath)
		if readErr != nil {
			continue
		}
		address := strings.TrimSpace(string(raw))
		if address == "" {
			continue
		}
		return strings.ToUpper(address), nil
	}

	if address, ok := resolveFromCommands(); ok {
		return address, nil
	}

	return "", fmt.Errorf("no adapter address found in %s", bluetoothDir)
}

func resolveFromCommands() (string, bool) {
	if out, err := execCommand("hciconfig", "-a"); err == nil {
		if address, ok := parseHciConfigAddress(string(out)); ok {
			return address, true
		}
	}

	if out, err := execCommand("bluetoothctl", "list"); err == nil {
		if address, ok := parseBluetoothctlListAddress(string(out)); ok {
			return address, true
		}
	}

	return "", false
}

func parseHciConfigAddress(output string) (string, bool) {
	for _, line := range strings.Split(output, "\n") {
		if !strings.Contains(line, "BD Address:") {
			continue
		}
		match := macPattern.FindString(line)
		if match == "" {
			continue
		}
		return strings.ToUpper(match), true
	}
	return "", false
}

func parseBluetoothctlListAddress(output string) (string, bool) {
	for _, line := range strings.Split(output, "\n") {
		if !strings.Contains(line, "Controller") {
			continue
		}
		match := macPattern.FindString(line)
		if match == "" {
			continue
		}
		return strings.ToUpper(match), true
	}
	return "", false
}
