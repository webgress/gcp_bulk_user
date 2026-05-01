package main

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/google"
)

const (
	envClientID       = "GCP_OAUTH_CLIENT_ID"
	envClientSecret   = "GCP_OAUTH_CLIENT_SECRET"
	envCloudSDKConfig = "CLOUDSDK_CONFIG"
	cloudPlatform     = "https://www.googleapis.com/auth/cloud-platform"
	gcloudConfigDir   = "gcloud"
	adcFilename       = "application_default_credentials.json"
	revokeURL         = "https://oauth2.googleapis.com/revoke"
	credTypeUser      = "authorized_user"
)

// adcFile is the gcloud Application Default Credentials JSON we share with
// gcloud itself. We round-trip through a raw map so unknown gcloud-emitted
// fields (`account`, `universe_domain`, ...) survive a write.
type adcFile struct {
	raw            map[string]json.RawMessage
	Type           string
	ClientID       string
	ClientSecret   string
	RefreshToken   string
	QuotaProjectID string
}

func parseADC(data []byte) (*adcFile, error) {
	raw := map[string]json.RawMessage{}
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("malformed ADC JSON: %w", err)
	}
	f := &adcFile{raw: raw}
	f.Type = jsonStringField(raw, "type")
	f.ClientID = jsonStringField(raw, "client_id")
	f.ClientSecret = jsonStringField(raw, "client_secret")
	f.RefreshToken = jsonStringField(raw, "refresh_token")
	f.QuotaProjectID = jsonStringField(raw, "quota_project_id")
	return f, nil
}

func (f *adcFile) marshal() ([]byte, error) {
	if f.raw == nil {
		f.raw = map[string]json.RawMessage{}
	}
	setStringField(f.raw, "type", f.Type)
	setStringField(f.raw, "client_id", f.ClientID)
	setStringField(f.raw, "client_secret", f.ClientSecret)
	setStringField(f.raw, "refresh_token", f.RefreshToken)
	if f.QuotaProjectID == "" {
		delete(f.raw, "quota_project_id")
	} else {
		setStringField(f.raw, "quota_project_id", f.QuotaProjectID)
	}
	return json.MarshalIndent(f.raw, "", "  ")
}

func jsonStringField(raw map[string]json.RawMessage, key string) string {
	v, ok := raw[key]
	if !ok {
		return ""
	}
	var s string
	if err := json.Unmarshal(v, &s); err != nil {
		return ""
	}
	return s
}

func setStringField(raw map[string]json.RawMessage, key, value string) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return
	}
	raw[key] = encoded
}

// credentialsPath returns the gcloud ADC path. CLOUDSDK_CONFIG overrides the
// parent directory when set, matching gcloud's own behavior.
func credentialsPath() (string, error) {
	if cfg := os.Getenv(envCloudSDKConfig); cfg != "" {
		return filepath.Join(cfg, adcFilename), nil
	}
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
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, gcloudConfigDir, adcFilename), nil
}

func readADC(path string) (*adcFile, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return parseADC(data)
}

// writeADC persists the credentials atomically with mode 0600 and parent dir
// 0700, matching gcloud's permissions on POSIX.
func writeADC(path string, f *adcFile) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	data, err := f.marshal()
	if err != nil {
		return err
	}

	tmp, err := os.CreateTemp(dir, "."+adcFilename+".*")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	cleanup := func() { _ = os.Remove(tmpPath) }
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		cleanup()
		return err
	}
	if err := tmp.Chmod(0o600); err != nil && runtime.GOOS != "windows" {
		tmp.Close()
		cleanup()
		return err
	}
	if err := tmp.Close(); err != nil {
		cleanup()
		return err
	}
	if err := os.Rename(tmpPath, path); err != nil {
		cleanup()
		return err
	}
	return nil
}

// buildAuthClient returns an *http.Client that uses the cached ADC token (or
// runs the consent flow if no file exists) and injects X-Goog-User-Project on
// every outbound call when quotaProject is non-empty.
//
// existing may be nil if no credentials file is present yet; the caller is
// responsible for distinguishing missing-file from malformed-file.
func buildAuthClient(ctx context.Context, adcPath string, existing *adcFile, quotaProject string) (*http.Client, error) {
	creds := existing
	if creds == nil || creds.RefreshToken == "" {
		// No usable credentials -- need to run consent flow, which requires
		// the env-supplied OAuth client id/secret.
		cfg, err := oauthConfigFromEnv()
		if err != nil {
			return nil, fmt.Errorf(
				"no credentials at %s.\n"+
					"Run `gcloud auth application-default login` (preferred) to use the same\n"+
					"token gcloud does, or set %s and %s in your environment to run this\n"+
					"binary's own browser-based OAuth flow:\n"+
					"  %s",
				adcPath, envClientID, envClientSecret, err,
			)
		}
		fresh, err := runConsentFlowAndPersist(ctx, cfg, adcPath, creds)
		if err != nil {
			return nil, fmt.Errorf("oauth flow failed: %w", err)
		}
		creds = fresh
	}

	if creds.Type != "" && creds.Type != credTypeUser {
		return nil, fmt.Errorf(
			"credentials file %s has type %q; this binary requires user credentials (type %q). "+
				"Run `gcloud auth application-default login` or unset CLOUDSDK_CONFIG.",
			adcPath, creds.Type, credTypeUser,
		)
	}
	if creds.ClientID == "" || creds.ClientSecret == "" {
		return nil, fmt.Errorf("credentials file %s is missing client_id/client_secret", adcPath)
	}

	// Use the standard helper to build the refreshing token source from the
	// ADC bytes so we benefit from any future spec quirks the library handles.
	bytes, err := creds.marshal()
	if err != nil {
		return nil, err
	}
	googleCreds, err := google.CredentialsFromJSON(ctx, bytes, cloudPlatform)
	if err != nil {
		return nil, fmt.Errorf("could not parse credentials: %w", err)
	}

	src := &cachingADCTokenSource{
		base:  googleCreds.TokenSource,
		path:  adcPath,
		state: *creds,
	}

	transport := &oauth2.Transport{
		Source: src,
		Base:   http.DefaultTransport,
	}

	return &http.Client{
		Transport: &userProjectTransport{
			base:         transport,
			quotaProject: quotaProject,
		},
		Timeout: 60 * time.Second,
	}, nil
}

// runLogout implements `--logout`: revoke the refresh token server-side via
// https://oauth2.googleapis.com/revoke and delete the local credentials file.
// Always exits 0; revoke failures are best-effort (matches gcloud).
func runLogout(ctx context.Context) int {
	path, err := credentialsPath()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Could not resolve credentials path: %v\n", err)
		return 0
	}

	creds, err := readADC(path)
	if err != nil {
		if os.IsNotExist(err) {
			fmt.Println("Already logged out.")
			return 0
		}
		// Malformed file -- still try to delete it to leave a clean slate.
		fmt.Fprintf(os.Stderr, "Could not parse %s: %v\n", path, err)
		_ = os.Remove(path)
		fmt.Println("Logged out.")
		return 0
	}

	if creds.RefreshToken == "" {
		_ = os.Remove(path)
		fmt.Println("Already logged out.")
		return 0
	}

	revokeErr := revokeToken(ctx, creds.RefreshToken)

	if rmErr := os.Remove(path); rmErr != nil && !os.IsNotExist(rmErr) {
		fmt.Fprintf(os.Stderr, "Could not delete %s: %v\n", path, rmErr)
	}

	if revokeErr != nil {
		fmt.Fprintf(os.Stderr, "Server-side revoke failed: %v\n", revokeErr)
		fmt.Fprintln(os.Stderr, "Local credentials deleted.")
		return 0
	}
	fmt.Println("Logged out.")
	return 0
}

func revokeToken(ctx context.Context, token string) error {
	body := url.Values{"token": []string{token}}.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, revokeURL, strings.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return nil
	}
	snippet, _ := io.ReadAll(io.LimitReader(resp.Body, 200))
	return fmt.Errorf("HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(snippet)))
}

func oauthConfigFromEnv() (*oauth2.Config, error) {
	clientID := os.Getenv(envClientID)
	clientSecret := os.Getenv(envClientSecret)
	if clientID == "" || clientSecret == "" {
		return nil, fmt.Errorf(
			"%s and %s are not set; cannot run consent flow without them",
			envClientID, envClientSecret,
		)
	}
	return &oauth2.Config{
		ClientID:     clientID,
		ClientSecret: clientSecret,
		Endpoint:     google.Endpoint,
		Scopes:       []string{cloudPlatform},
	}, nil
}

// runConsentFlowAndPersist runs the browser-based OAuth flow and writes the
// resulting credentials in gcloud ADC format. existing (may be nil) is used
// only to preserve fields we don't manage (e.g. quota_project_id, account).
func runConsentFlowAndPersist(ctx context.Context, cfg *oauth2.Config, path string, existing *adcFile) (*adcFile, error) {
	tok, err := runConsentFlow(ctx, cfg)
	if err != nil {
		return nil, err
	}
	if tok.RefreshToken == "" {
		return nil, errors.New("OAuth response did not include a refresh_token (try --logout and re-run)")
	}

	out := &adcFile{}
	if existing != nil && existing.raw != nil {
		out.raw = existing.raw
		out.QuotaProjectID = existing.QuotaProjectID
	}
	out.Type = credTypeUser
	out.ClientID = cfg.ClientID
	out.ClientSecret = cfg.ClientSecret
	out.RefreshToken = tok.RefreshToken

	if err := writeADC(path, out); err != nil {
		fmt.Fprintf(os.Stderr, "Warning: could not cache credentials at %s: %v\n", path, err)
	}
	return out, nil
}

func runConsentFlow(ctx context.Context, cfg *oauth2.Config) (*oauth2.Token, error) {
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
		oauth2.ApprovalForce,
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

// cachingADCTokenSource persists a refreshed token back to the ADC file when
// Google rotates the refresh_token. Access-token-only refreshes don't need to
// touch the file.
type cachingADCTokenSource struct {
	base  oauth2.TokenSource
	path  string
	state adcFile
}

func (c *cachingADCTokenSource) Token() (*oauth2.Token, error) {
	tok, err := c.base.Token()
	if err != nil {
		return nil, err
	}
	if tok.RefreshToken != "" && tok.RefreshToken != c.state.RefreshToken {
		c.state.RefreshToken = tok.RefreshToken
		if err := writeADC(c.path, &c.state); err != nil {
			fmt.Fprintf(os.Stderr, "Warning: could not persist rotated refresh token: %v\n", err)
		}
	}
	return tok, nil
}

// userProjectTransport injects X-Goog-User-Project on every outbound call.
type userProjectTransport struct {
	base         http.RoundTripper
	quotaProject string
}

func (t *userProjectTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if t.quotaProject != "" && req.Header.Get("X-Goog-User-Project") == "" {
		clone := req.Clone(req.Context())
		clone.Header.Set("X-Goog-User-Project", t.quotaProject)
		req = clone
	}
	return t.base.RoundTrip(req)
}
