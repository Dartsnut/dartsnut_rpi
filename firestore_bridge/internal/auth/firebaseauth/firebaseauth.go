package firebaseauth

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// Config holds the Firebase Auth configuration for email/password sign-in.
type Config struct {
	APIKey   string
	Email    string
	Password string
}

// tokenResponse models the JSON returned by signInWithPassword.
type tokenResponse struct {
	IDToken      string `json:"idToken"`
	RefreshToken string `json:"refreshToken"`
	ExpiresIn    string `json:"expiresIn"` // seconds as string
}

// refreshResponse models the JSON returned by securetoken token refresh.
type refreshResponse struct {
	IDToken      string `json:"id_token"`
	RefreshToken string `json:"refresh_token"`
	ExpiresIn    string `json:"expires_in"` // seconds as string
}

// Client manages Firebase ID / refresh tokens with automatic refresh.
type Client struct {
	cfg    Config
	client *http.Client

	mu          sync.Mutex
	idToken     string
	refreshTok  string
	expiry      time.Time
	refreshSkew time.Duration
}

// NewClient creates a new Firebase Auth client.
func NewClient(cfg Config) *Client {
	return &Client{
		cfg:         cfg,
		client:      &http.Client{Timeout: 10 * time.Second},
		refreshSkew: 30 * time.Second,
	}
}

// CurrentIDToken returns a valid ID token, refreshing or signing in as needed.
func (c *Client) CurrentIDToken(ctx context.Context) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.idToken == "" || time.Until(c.expiry) < c.refreshSkew {
		if c.refreshTok != "" {
			if err := c.refreshLocked(ctx); err == nil {
				return c.idToken, nil
			}
			// If refresh fails, fall back to full sign-in below.
		}
		if err := c.signInLocked(ctx); err != nil {
			return "", err
		}
	}
	return c.idToken, nil
}

func (c *Client) signInLocked(ctx context.Context) error {
	body := map[string]any{
		"email":             c.cfg.Email,
		"password":          c.cfg.Password,
		"returnSecureToken": true,
	}
	data, err := json.Marshal(body)
	if err != nil {
		return err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key="+c.cfg.APIKey,
		bytes.NewReader(data),
	)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return errors.New("firebaseauth: signInWithPassword failed: " + resp.Status)
	}

	var tr tokenResponse
	if err := json.NewDecoder(resp.Body).Decode(&tr); err != nil {
		return err
	}
	return c.updateTokensLocked(tr.IDToken, tr.RefreshToken, tr.ExpiresIn)
}

func (c *Client) refreshLocked(ctx context.Context) error {
	values := map[string]string{
		"grant_type":    "refresh_token",
		"refresh_token": c.refreshTok,
	}
	data, err := json.Marshal(values)
	if err != nil {
		return err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		"https://securetoken.googleapis.com/v1/token?key="+c.cfg.APIKey,
		bytes.NewReader(data),
	)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return errors.New("firebaseauth: token refresh failed: " + resp.Status)
	}

	var rr refreshResponse
	if err := json.NewDecoder(resp.Body).Decode(&rr); err != nil {
		return err
	}
	return c.updateTokensLocked(rr.IDToken, rr.RefreshToken, rr.ExpiresIn)
}

func (c *Client) updateTokensLocked(idTok, refreshTok, expiresInSec string) error {
	secs, err := strconv.ParseInt(expiresInSec, 10, 64)
	if err != nil {
		return err
	}
	c.idToken = idTok
	c.refreshTok = refreshTok
	c.expiry = time.Now().Add(time.Duration(secs) * time.Second)
	return nil
}

