package client

import (
	"reflect"
	"testing"
)

func TestBuildUpdateMaskPaths_NestedMapUsesLeafPaths(t *testing.T) {
	fields := map[string]any{
		"device_info": map[string]any{
			"name": "PixelDart",
		},
	}

	got := buildUpdateMaskPaths(fields)
	want := []string{"device_info.name"}

	if !reflect.DeepEqual(got, want) {
		t.Fatalf("unexpected update mask paths: got %v want %v", got, want)
	}
}

func TestBuildUpdateMaskPaths_MixedPayload(t *testing.T) {
	fields := map[string]any{
		"brightness": float64(80),
		"device_info": map[string]any{
			"name": "PixelDart",
			"sn":   "SER123",
		},
	}

	got := buildUpdateMaskPaths(fields)
	want := []string{"brightness", "device_info.name", "device_info.sn"}

	if !reflect.DeepEqual(got, want) {
		t.Fatalf("unexpected update mask paths: got %v want %v", got, want)
	}
}
