package bridge

import "testing"

func TestEnsureDeviceInfoMetadata_WhenMissingDeviceInfo(t *testing.T) {
	payload := map[string]any{}

	ensureDeviceInfoMetadata(payload, "AA:BB:CC")

	deviceInfo, ok := payload["device_info"].(map[string]any)
	if !ok {
		t.Fatalf("device_info was not created as map")
	}
	if got := deviceInfo["id"]; got != "AA:BB:CC" {
		t.Fatalf("device_info.id mismatch: got %v", got)
	}
	if got := deviceInfo["sn"]; got != "" {
		t.Fatalf("device_info.sn mismatch: got %v", got)
	}
}

func TestEnsureDeviceInfoMetadata_BackfillsMissingOrEmptySN(t *testing.T) {
	cases := []map[string]any{
		{"device_info": map[string]any{"serial": "SERIAL-123", "name": "PixelDart"}},
		{"device_info": map[string]any{"sn": "", "serial": "SERIAL-456"}},
		{"device_info": map[string]any{"sn": "   ", "serial": " SERIAL-789 "}},
	}

	for _, payload := range cases {
		ensureDeviceInfoMetadata(payload, "AA:BB:CC")
		deviceInfo, ok := payload["device_info"].(map[string]any)
		if !ok {
			t.Fatalf("device_info should be map")
		}
		if got := deviceInfo["sn"]; got == "" {
			t.Fatalf("expected non-empty backfilled sn, got %v", got)
		}
		if got := deviceInfo["id"]; got != "AA:BB:CC" {
			t.Fatalf("expected id set, got %v", got)
		}
	}
}

func TestEnsureDeviceInfoMetadata_TrimmedSerialBackfillsSN(t *testing.T) {
	payload := map[string]any{
		"device_info": map[string]any{
			"sn":     "   ",
			"serial": " SERIAL-XYZ ",
		},
	}

	ensureDeviceInfoMetadata(payload, "AA:BB:CC")
	deviceInfo, ok := payload["device_info"].(map[string]any)
	if !ok {
		t.Fatalf("device_info should be map")
	}
	if got := deviceInfo["sn"]; got != "SERIAL-XYZ" {
		t.Fatalf("expected trimmed serial to backfill sn, got %v", got)
	}
}

func TestEnsureDeviceInfoMetadata_PreservesNonEmptySN(t *testing.T) {
	payload := map[string]any{
		"device_info": map[string]any{
			"sn": "SERIAL-123",
		},
	}

	ensureDeviceInfoMetadata(payload, "AA:BB:CC")

	deviceInfo, ok := payload["device_info"].(map[string]any)
	if !ok {
		t.Fatalf("device_info should be map")
	}
	if got := deviceInfo["sn"]; got != "SERIAL-123" {
		t.Fatalf("non-empty sn should be preserved, got %v", got)
	}
	if got := deviceInfo["id"]; got != "AA:BB:CC" {
		t.Fatalf("expected id set, got %v", got)
	}
}

func TestBuildDeviceInfoRepairPayload_AddsMissingID(t *testing.T) {
	remote := map[string]any{
		"device_info": map[string]any{
			"name": "PixelDart",
		},
	}
	initial := map[string]any{
		"device_info": map[string]any{
			"sn": "SERIAL-123",
		},
	}

	got := buildDeviceInfoRepairPayload(remote, "AA:BB:CC", initial)
	info := got["device_info"].(map[string]any)
	if info["id"] != "AA:BB:CC" {
		t.Fatalf("expected id repair, got %v", info["id"])
	}
}

func TestBuildDeviceInfoRepairPayload_AddsSNOnlyWhenAvailable(t *testing.T) {
	remote := map[string]any{
		"device_info": map[string]any{
			"id": "AA:BB:CC",
		},
	}
	initial := map[string]any{
		"device_info": map[string]any{
			"sn": " SERIAL-XYZ ",
		},
	}

	got := buildDeviceInfoRepairPayload(remote, "AA:BB:CC", initial)
	info := got["device_info"].(map[string]any)
	if info["sn"] != "SERIAL-XYZ" {
		t.Fatalf("expected sn repair from initial payload, got %v", info["sn"])
	}
}

func TestBuildDeviceInfoRepairPayload_NoOpWhenAlreadyPresent(t *testing.T) {
	remote := map[string]any{
		"device_info": map[string]any{
			"id": "AA:BB:CC",
			"sn": "SERIAL-123",
		},
	}
	initial := map[string]any{
		"device_info": map[string]any{
			"sn": "SERIAL-123",
		},
	}

	got := buildDeviceInfoRepairPayload(remote, "AA:BB:CC", initial)
	if got != nil {
		t.Fatalf("expected nil repair payload, got %v", got)
	}
}
