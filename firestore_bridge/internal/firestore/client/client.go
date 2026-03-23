package client

import (
	"context"
	"fmt"
	"sort"
	"time"

	"github.com/dartsnut/firestore_bridge/internal/auth/firebaseauth"
	"github.com/dartsnut/firestore_bridge/internal/config"
	firestorepb "google.golang.org/genproto/googleapis/firestore/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// Client wraps a Firestore gRPC client and Firebase Auth for ID-token auth.
type Client struct {
	dbName string
	auth   *firebaseauth.Client
	raw    firestorepb.FirestoreClient
	conn   *grpc.ClientConn
}

// New creates a new Firestore client connected to firestore.googleapis.com.
func New(ctx context.Context, auth *firebaseauth.Client) (*Client, error) {
	creds := credentials.NewClientTLSFromCert(nil, "")
	conn, err := grpc.DialContext(
		ctx,
		"firestore.googleapis.com:443",
		grpc.WithTransportCredentials(creds),
		grpc.WithBlock(),
		grpc.WithTimeout(10*time.Second),
	)
	if err != nil {
		return nil, err
	}
	raw := firestorepb.NewFirestoreClient(conn)
	return &Client{
		dbName: config.DatabaseName(),
		auth:   auth,
		raw:    raw,
		conn:   conn,
	}, nil
}

// Close closes the underlying gRPC connection.
func (c *Client) Close() error {
	if c.conn != nil {
		return c.conn.Close()
	}
	return nil
}

// documentName builds the full resource name for a document path like "devices/<id>".
func (c *Client) documentName(docPath string) string {
	return fmt.Sprintf("%s/documents/%s", c.dbName, docPath)
}

func (c *Client) withAuth(ctx context.Context) (context.Context, error) {
	idToken, err := c.auth.CurrentIDToken(ctx)
	if err != nil {
		return nil, err
	}
	// Attach Authorization plus routing headers for the specific database.
	// For named databases, Firestore expects the database resource to match
	// between the request and headers.
	md := metadata.Pairs(
		"authorization", "Bearer "+idToken,
		// Routing header as full database resource name.
		"x-goog-request-params", fmt.Sprintf("database=%s", c.dbName),
		// Some clients also send google-cloud-resource-prefix; include it for safety.
		"google-cloud-resource-prefix", c.dbName,
	)
	return metadata.NewOutgoingContext(ctx, md), nil
}

// GetDocument retrieves a document by relative path (e.g. "devices/<id>").
func (c *Client) GetDocument(ctx context.Context, docPath string) (*firestorepb.Document, error) {
	ctx, err := c.withAuth(ctx)
	if err != nil {
		return nil, err
	}
	req := &firestorepb.GetDocumentRequest{
		Name: c.documentName(docPath),
	}
	return c.raw.GetDocument(ctx, req)
}

// IsNotFound reports whether an error is a Firestore NOT_FOUND.
func IsNotFound(err error) bool {
	if err == nil {
		return false
	}
	st, ok := status.FromError(err)
	if !ok {
		return false
	}
	return st.Code().String() == "NotFound"
}

// CommitCreate creates a document with the given fields, replacing any existing doc.
func (c *Client) CommitCreate(ctx context.Context, docPath string, fields map[string]*firestorepb.Value) error {
	ctx, err := c.withAuth(ctx)
	if err != nil {
		return err
	}
	doc := &firestorepb.Document{
		Name:   c.documentName(docPath),
		Fields: fields,
	}
	req := &firestorepb.CommitRequest{
		Database: c.dbName,
		Writes: []*firestorepb.Write{
			{
				Operation: &firestorepb.Write_Update{Update: doc},
			},
		},
	}
	_, err = c.raw.Commit(ctx, req)
	return err
}

// CommitMerge performs a partial update (merge) of the document at docPath.
func (c *Client) CommitMerge(ctx context.Context, docPath string, fields map[string]*firestorepb.Value) error {
	ctx, err := c.withAuth(ctx)
	if err != nil {
		return err
	}
	docName := c.documentName(docPath)
	doc := &firestorepb.Document{
		Name:   docName,
		Fields: fields,
	}
	// Build update mask from leaf field paths so nested map updates
	// don't overwrite sibling keys (e.g. device_info.id/sn).
	paths := buildUpdateMaskPaths(decodeFieldsToMap(fields))
	req := &firestorepb.CommitRequest{
		Database: c.dbName,
		Writes: []*firestorepb.Write{
			{
				Operation: &firestorepb.Write_Update{Update: doc},
				UpdateMask: &firestorepb.DocumentMask{
					FieldPaths: paths,
				},
			},
		},
	}
	_, err = c.raw.Commit(ctx, req)
	return err
}

func buildUpdateMaskPaths(payload map[string]any) []string {
	var paths []string

	var walk func(prefix string, v any)
	walk = func(prefix string, v any) {
		m, ok := v.(map[string]any)
		if !ok || m == nil {
			if prefix != "" {
				paths = append(paths, prefix)
			}
			return
		}
		if len(m) == 0 {
			if prefix != "" {
				paths = append(paths, prefix)
			}
			return
		}
		for k, child := range m {
			next := k
			if prefix != "" {
				next = prefix + "." + k
			}
			walk(next, child)
		}
	}

	for k, v := range payload {
		walk(k, v)
	}
	sort.Strings(paths)
	return paths
}

func decodeFieldsToMap(fields map[string]*firestorepb.Value) map[string]any {
	out := make(map[string]any, len(fields))
	for k, v := range fields {
		out[k] = decodeFieldValue(v)
	}
	return out
}

func decodeFieldValue(v *firestorepb.Value) any {
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
			arr = append(arr, decodeFieldValue(e))
		}
		return arr
	case *firestorepb.Value_MapValue:
		return decodeFieldsToMap(t.MapValue.GetFields())
	default:
		return nil
	}
}

// ListenDocument starts a Listen stream for a single document and invokes the callback
// each time the document changes. It returns when the context is done or the stream ends.
func (c *Client) ListenDocument(ctx context.Context, docPath string, onChange func(*firestorepb.Document)) error {
	ctx, err := c.withAuth(ctx)
	if err != nil {
		return err
	}

	stream, err := c.raw.Listen(ctx)
	if err != nil {
		return err
	}

	fullName := c.documentName(docPath)

	// Add target.
	addReq := &firestorepb.ListenRequest{
		Database: c.dbName,
		TargetChange: &firestorepb.ListenRequest_AddTarget{
			AddTarget: &firestorepb.Target{
				TargetId: 1,
				TargetType: &firestorepb.Target_Documents{
					Documents: &firestorepb.Target_DocumentsTarget{
						Documents: []string{fullName},
					},
				},
			},
		},
	}
	if err := stream.Send(addReq); err != nil {
		return err
	}

	for {
		resp, err := stream.Recv()
		if err != nil {
			return err
		}
		switch t := resp.ResponseType.(type) {
		case *firestorepb.ListenResponse_DocumentChange:
			if t.DocumentChange.GetDocument().GetName() == fullName {
				onChange(t.DocumentChange.GetDocument())
			}
		default:
			// Ignore other response types (target changes, filters, etc.).
		}
	}
}

