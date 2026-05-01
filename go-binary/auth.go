package main

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/google"
)

const (
	envClientID     = "GCP_OAUTH_CLIENT_ID"
	envClientSecret = "GCP_OAUTH_CLIENT_SECRET"
	cloudPlatform   = "https://www.googleapis.com/auth/cloud-platform"
	appConfigDir    = "gcp-appliance-status"
	credsFilename   = "credentials.json"
)

// loadAuthClient returns an *http.Client that authenticates with Google as the
// signed-in user, refreshing the cached token automatically and injecting
// X-Goog-User-Project on every outbound call when quotaProject is non-empty.
//
// On first run, the OAuth client ID/secret must be supplied via env vars
// (GCP_OAUTH_CLIENT_ID / GCP_OAUTH_CLIENT_SECRET) and a browser consent screen
// is opened. Subsequent runs reuse the cached token.
func loadAuthClient(ctx context.Context, quotaProject string) (*http.Client, error) {
	credsPath, err := credentialsPath()
	if err != nil {
		return nil, err
	}

	cfg, err := oauthConfigFromEnv()
	if err != nil {
		// If we have a cached token but no client ID/secret in env, we can
		// still refresh it -- but only if the cache contains the config. We
		// don't persist the secret, so a fresh env is required for both
		// initial consent and refresh.
		return nil, err
	}

	tok, err := readToken(credsPath)
	if err != nil {
		tok, err = runConsentFlow(ctx, cfg)
		if err != nil {
			return nil, fmt.Errorf("oauth flow failed: %w", err)
		}
		if writeErr := writeToken(credsPath, tok); writeErr != nil {
			fmt.Fprintf(os.Stderr, "Warning: could not cache token at %s: %v\n", credsPath, writeErr)
		}
	}

	source := &cachingTokenSource{
		base:     cfg.TokenSource(ctx, tok),
		path:     credsPath,
		lastSeen: tok.AccessToken,
	}

	base := &oauth2.Transport{
		Source: source,
		Base:   http.DefaultTransport,
	}

	return &http.Client{
		Transport: &userProjectTransport{
			base:         base,
			quotaProject: quotaProject,
		},
		Timeout: 60 * time.Second,
	}, nil
}

func oauthConfigFromEnv() (*oauth2.Config, error) {
	clientID := os.Getenv(envClientID)
	clientSecret := os.Getenv(envClientSecret)
	if clientID == "" || clientSecret == "" {
		return nil, fmt.Errorf(
			"OAuth credentials not configured. Set %s and %s in your environment "+
				"(see README for how to obtain a desktop OAuth client)",
			envClientID, envClientSecret,
		)
	}
	return &oauth2.Config{
		ClientID:     clientID,
		ClientSecret: clientSecret,
		Endpoint:     google.Endpoint,
		Scopes:       []string{cloudPlatform},
		// RedirectURL is filled in at consent time once we know the port.
	}, nil
}

func credentialsPath() (string, error) {
	var base string
	switch runtime.GOOS {
	case "windows":
		base = os.Getenv("APPDATA")
		if base == "" {
			home, err := os.UserHomeDir()
			if err != nil {
				return "", err
			}
			base = filepath.Join(home, "AppData", "Roaming")
		}
	default:
		// Honour XDG_CONFIG_HOME when set, fall back to ~/.config.
		if xdg := os.Getenv("XDG_CONFIG_HOME"); xdg != "" {
			base = xdg
		} else {
			home, err := os.UserHomeDir()
			if err != nil {
				return "", err
			}
			base = filepath.Join(home, ".config")
		}
	}
	return filepath.Join(base, appConfigDir, credsFilename), nil
}

func readToken(path string) (*oauth2.Token, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	tok := &oauth2.Token{}
	if err := json.Unmarshal(data, tok); err != nil {
		return nil, fmt.Errorf("malformed cached token: %w", err)
	}
	if tok.RefreshToken == "" && tok.AccessToken == "" {
		return nil, errors.New("cached token is empty")
	}
	return tok, nil
}

func writeToken(path string, tok *oauth2.Token) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(tok, "", "  ")
	if err != nil {
		return err
	}
	// 0600 -- it carries a refresh token.
	return os.WriteFile(path, data, 0o600)
}

func runConsentFlow(ctx context.Context, cfg *oauth2.Config) (*oauth2.Token, error) {
	// Bind to a random local port so we can build an exact redirect URI.
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("could not start local listener: %w", err)
	}
	defer listener.Close()

	port := listener.Addr().(*net.TCPAddr).Port
	cfg.RedirectURL = fmt.Sprintf("http://127.0.0.1:%d/callback", port)

	stateBytes := make([]byte, 24)
	if _, err := rand.Read(stateBytes); err != nil {
		return nil, err
	}
	state := base64.RawURLEncoding.EncodeToString(stateBytes)

	authURL := cfg.AuthCodeURL(state,
		oauth2.AccessTypeOffline,
		oauth2.ApprovalForce, // force refresh_token issuance even on re-consent
	)

	type result struct {
		code string
		err  error
	}
	results := make(chan result, 1)

	mux := http.NewServeMux()
	mux.HandleFunc("/callback", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if errStr := q.Get("error"); errStr != "" {
			http.Error(w, "OAuth error: "+errStr, http.StatusBadRequest)
			results <- result{err: fmt.Errorf("consent denied: %s", errStr)}
			return
		}
		if got := q.Get("state"); got != state {
			http.Error(w, "state mismatch", http.StatusBadRequest)
			results <- result{err: errors.New("oauth state mismatch")}
			return
		}
		code := q.Get("code")
		if code == "" {
			http.Error(w, "missing code", http.StatusBadRequest)
			results <- result{err: errors.New("oauth code missing from callback")}
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, consentSuccessHTML)
		results <- result{code: code}
	})

	srv := &http.Server{Handler: mux}
	go func() {
		_ = srv.Serve(listener)
	}()
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
	}()

	fmt.Fprintln(os.Stderr, "Opening browser for Google sign-in...")
	fmt.Fprintln(os.Stderr, "If it does not open automatically, visit:")
	fmt.Fprintln(os.Stderr, "  "+authURL)

	if err := openBrowser(authURL); err != nil {
		fmt.Fprintf(os.Stderr, "(Could not auto-open browser: %v)\n", err)
	}

	select {
	case res := <-results:
		if res.err != nil {
			return nil, res.err
		}
		return cfg.Exchange(ctx, res.code)
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-time.After(5 * time.Minute):
		return nil, errors.New("timed out waiting for OAuth consent")
	}
}

func openBrowser(target string) error {
	if _, err := url.Parse(target); err != nil {
		return err
	}
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", target)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", target)
	default:
		cmd = exec.Command("xdg-open", target)
	}
	return cmd.Start()
}

const consentSuccessHTML = `<!doctype html>
<html><head><meta charset="utf-8"><title>Sign-in complete</title>
<style>body{font-family:system-ui,sans-serif;background:#f5f1e8;color:#1f1a14;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}div{max-width:520px;padding:32px;background:#fffdfa;border-radius:14px;box-shadow:0 16px 40px rgba(56,41,19,.14);text-align:center}h1{color:#0a5a42;margin-top:0}</style>
</head><body><div><h1>You're signed in</h1>
<p>You can close this window and return to your terminal.</p></div></body></html>`

// cachingTokenSource wraps an oauth2.TokenSource so that whenever the access
// token is refreshed we persist the new bundle (refresh_token + access_token +
// expiry) back to the credentials file.
type cachingTokenSource struct {
	base     oauth2.TokenSource
	path     string
	lastSeen string
}

func (c *cachingTokenSource) Token() (*oauth2.Token, error) {
	tok, err := c.base.Token()
	if err != nil {
		return nil, err
	}
	if tok.AccessToken != c.lastSeen {
		c.lastSeen = tok.AccessToken
		if err := writeToken(c.path, tok); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: could not refresh cached token: %v\n", err)
		}
	}
	return tok, nil
}

// userProjectTransport injects X-Goog-User-Project on every outbound call so
// that user-credential API requests are billed against the configured quota
// project instead of failing with "User project specified in the request is
// invalid".
type userProjectTransport struct {
	base         http.RoundTripper
	quotaProject string
}

func (t *userProjectTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if t.quotaProject != "" && req.Header.Get("X-Goog-User-Project") == "" {
		// Clone before mutating -- RoundTrippers must not modify the input.
		clone := req.Clone(req.Context())
		clone.Header.Set("X-Goog-User-Project", t.quotaProject)
		req = clone
	}
	return t.base.RoundTrip(req)
}
