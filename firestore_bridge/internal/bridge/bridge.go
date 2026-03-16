package bridge

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"time"

	fsclient "github.com/dartsnut/firestore_bridge/internal/firestore/client"
	firestorepb "google.golang.org/genproto/googleapis/firestore/v1"
)

type message struct {
	Kind    string          `json:"kind"`
	Payload json.RawMessage `json:"payload"`
}

// Run starts the bridge loop for a single device over a Unix socket.
func Run(ctx context.Context, deviceID, socketPath string, fs *fsclient.Client) error {
	conn, err := net.Dial("unix", socketPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "bridge: failed to dial socket:", err)
		return err
	}
	defer conn.Close()

	writer := bufio.NewWriter(conn)
	reader := bufio.NewScanner(conn)

	// Send ready.
	if err := sendJSON(writer, "ready", map[string]any{}); err != nil {
		fmt.Fprintln(os.Stderr, "bridge: failed to send ready:", err)
		return err
	}

	// Start Firestore listener after initial_state is processed.
	var listenStarted bool

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		if !reader.Scan() {
			if err := reader.Err(); err != nil {
				fmt.Fprintln(os.Stderr, "bridge: socket read error:", err)
				return err
			}
			fmt.Fprintln(os.Stderr, "bridge: socket closed")
			return errors.New("socket closed")
		}
		line := reader.Bytes()
		if len(line) == 0 {
			continue
		}

		var msg message
		if err := json.Unmarshal(line, &msg); err != nil {
			continue
		}
		switch msg.Kind {
		case "initial_state":
			var payload map[string]any
			if err := json.Unmarshal(msg.Payload, &payload); err != nil {
				continue
			}
			docPath := fmt.Sprintf("devices/%s", deviceID)
			doc, err := fs.GetDocument(ctx, docPath)
			if err != nil {
				if fsclient.IsNotFound(err) {
					fields := encodeMapToFields(payload)
					if cerr := fs.CommitCreate(ctx, docPath, fields); cerr != nil {
						fmt.Fprintln(os.Stderr, "bridge: CommitCreate failed:", cerr)
					} else {
						fmt.Fprintln(os.Stderr, "bridge: created document for", docPath)
					}
				} else {
					fmt.Fprintln(os.Stderr, "bridge: GetDocument failed:", err)
					continue
				}
			} else {
				data := decodeFieldsToMap(doc.GetFields())
				if err := sendJSON(writer, "config_initial", data); err != nil {
					fmt.Fprintln(os.Stderr, "bridge: failed to send config_initial:", err)
				}
			}
			if !listenStarted {
				listenStarted = true
				go func() {
					if err := fs.ListenDocument(ctx, docPath, func(d *firestorepb.Document) {
						data := decodeFieldsToMap(d.GetFields())
						if err := sendJSON(writer, "config", data); err != nil {
							fmt.Fprintln(os.Stderr, "bridge: failed to send config:", err)
						}
					}); err != nil && !errors.Is(err, context.Canceled) {
						fmt.Fprintln(os.Stderr, "bridge: ListenDocument error:", err)
					}
				}()
			}
		case "device_state":
			var payload map[string]any
			if err := json.Unmarshal(msg.Payload, &payload); err != nil {
				continue
			}
			docPath := fmt.Sprintf("devices/%s", deviceID)
			fields := encodeMapToFields(payload)
			if err := fs.CommitMerge(ctx, docPath, fields); err != nil {
				fmt.Fprintln(os.Stderr, "bridge: CommitMerge failed:", err)
			}
		default:
			// ignore unknown kinds
		}
	}
}

func sendJSON(w *bufio.Writer, kind string, payload any) error {
	lineBytes, err := json.Marshal(map[string]any{
		"kind":    kind,
		"payload": payload,
	})
	if err != nil {
		return err
	}
	if _, err := w.Write(lineBytes); err != nil {
		return err
	}
	if err := w.WriteByte('\n'); err != nil {
		return err
	}
	return w.Flush()
}

// encodeMapToFields converts a generic map into Firestore Value fields.
func encodeMapToFields(m map[string]any) map[string]*firestorepb.Value {
	out := make(map[string]*firestorepb.Value, len(m))
	for k, v := range m {
		out[k] = encodeValue(v)
	}
	return out
}

func encodeValue(v any) *firestorepb.Value {
	switch x := v.(type) {
	case nil:
		return &firestorepb.Value{ValueType: &firestorepb.Value_NullValue{}}
	case bool:
		return &firestorepb.Value{ValueType: &firestorepb.Value_BooleanValue{BooleanValue: x}}
	case float64:
		return &firestorepb.Value{ValueType: &firestorepb.Value_DoubleValue{DoubleValue: x}}
	case string:
		return &firestorepb.Value{ValueType: &firestorepb.Value_StringValue{StringValue: x}}
	case []any:
		var vals []*firestorepb.Value
		for _, e := range x {
			vals = append(vals, encodeValue(e))
		}
		return &firestorepb.Value{
			ValueType: &firestorepb.Value_ArrayValue{
				ArrayValue: &firestorepb.ArrayValue{Values: vals},
			},
		}
	case map[string]any:
		fields := encodeMapToFields(x)
		return &firestorepb.Value{
			ValueType: &firestorepb.Value_MapValue{
				MapValue: &firestorepb.MapValue{Fields: fields},
			},
		}
	default:
		// Fallback: try JSON number to float64 via fmt.
		return &firestorepb.Value{
			ValueType: &firestorepb.Value_StringValue{StringValue: fmt.Sprint(v)},
		}
	}
}

// decodeFieldsToMap converts Firestore fields into a simple map.
func decodeFieldsToMap(fields map[string]*firestorepb.Value) map[string]any {
	out := make(map[string]any, len(fields))
	for k, v := range fields {
		out[k] = decodeValue(v)
	}
	return out
}

func decodeValue(v *firestorepb.Value) any {
	switch t := v.ValueType.(type) {
	case *firestorepb.Value_NullValue:
		return nil
	case *firestorepb.Value_BooleanValue:
		return t.BooleanValue
	case *firestorepb.Value_DoubleValue:
		return t.DoubleValue
	case *firestorepb.Value_IntegerValue:
		return t.IntegerValue
	case *firestorepb.Value_StringValue:
		return t.StringValue
	case *firestorepb.Value_TimestampValue:
		return t.TimestampValue.AsTime().Format(time.RFC3339Nano)
	case *firestorepb.Value_ArrayValue:
		var arr []any
		for _, e := range t.ArrayValue.GetValues() {
			arr = append(arr, decodeValue(e))
		}
		return arr
	case *firestorepb.Value_MapValue:
		return decodeFieldsToMap(t.MapValue.GetFields())
	default:
		return nil
	}
}

