package main

import (
	"bytes"
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func sampleAppliance() Appliance {
	return Appliance{
		Project:     "p1",
		Name:        "projects/p1/locations/us-central1/appliances/appliance-123",
		DisplayName: "test",
		State:       "ACTIVE",
		Model:       "TA40",
		CreateTime:  "2026-04-01T10:00:00Z",
		UpdateTime:  "2026-04-10T12:00:00Z",
		ApplianceID: "appliance-123",
		Location:    "us-central1",
	}
}

func TestProjectURL(t *testing.T) {
	got := projectURL("proj-123")
	want := "https://pantheon.corp.google.com/appliances?project=proj-123"
	if got != want {
		t.Errorf("projectURL() = %q, want %q", got, want)
	}
}

func TestApplianceURL(t *testing.T) {
	got := applianceURL("proj-123", "us-central1", "appliance-xyz")
	want := "https://pantheon.corp.google.com/appliances/us-central1/" +
		"appliance-xyz;tab=configuration?project=proj-123"
	if got != want {
		t.Errorf("applianceURL() = %q, want %q", got, want)
	}
}

func TestApplianceURL_NoLocationFallsBack(t *testing.T) {
	got := applianceURL("p1", "", "any")
	want := "https://pantheon.corp.google.com/appliances?project=p1"
	if got != want {
		t.Errorf("applianceURL() = %q, want %q", got, want)
	}
}

func TestParseResourceName(t *testing.T) {
	loc, id, ok := parseResourceName("projects/p1/locations/us-central1/appliances/a1")
	if !ok || loc != "us-central1" || id != "a1" {
		t.Errorf("parseResourceName: got (%q, %q, %v); want (us-central1, a1, true)", loc, id, ok)
	}

	bad := []string{
		"projects/p1/locations/us-central1/orders/order-123", // wrong collection
		"projects/p1/locations/L/appliances/A/operations/op1",
		"projects/p1/locations/L/appliances/",
		"projects/p1/locations//appliances/A",
		"projects/p1/locations/L/appliances/A/",
		"projects//locations/L/appliances/A",
	}
	for _, n := range bad {
		if _, _, ok := parseResourceName(n); ok {
			t.Errorf("parseResourceName(%q) should reject", n)
		}
	}
}

func TestSanitizeDisplayName(t *testing.T) {
	got := sanitizeDisplayName("name\twith\ncontrols")
	want := "name with controls"
	if got != want {
		t.Errorf("sanitizeDisplayName: got %q want %q", got, want)
	}
}

func TestSafeCSVCell_PrefixesFormulaInjection(t *testing.T) {
	cases := []struct{ in, want string }{
		{"=HYPERLINK(\"http://evil\")", "'=HYPERLINK(\"http://evil\")"},
		{"+sum", "'+sum"},
		{"-1", "'-1"},
		{"@cmd", "'@cmd"},
		{"normal", "normal"},
		{"", ""},
	}
	for _, c := range cases {
		if got := safeCSVCell(c.in); got != c.want {
			t.Errorf("safeCSVCell(%q) = %q want %q", c.in, got, c.want)
		}
	}
}

func TestRenderJSONMatchesPythonShape(t *testing.T) {
	a := sampleAppliance()
	a = attachLinks([]Appliance{a})[0]
	var buf bytes.Buffer
	renderJSON([]Appliance{a}, &buf)

	var parsed []map[string]any
	if err := json.Unmarshal(buf.Bytes(), &parsed); err != nil {
		t.Fatalf("renderJSON produced invalid JSON: %v", err)
	}
	if len(parsed) != 1 {
		t.Fatalf("expected 1 record, got %d", len(parsed))
	}
	row := parsed[0]
	wantKeys := []string{
		"project", "name", "display_name", "state", "model",
		"create_time", "update_time", "appliance_id", "location",
		"project_url", "appliance_url",
	}
	for _, k := range wantKeys {
		if _, ok := row[k]; !ok {
			t.Errorf("JSON missing key %q", k)
		}
	}
	if row["appliance_id"] != "appliance-123" {
		t.Errorf("appliance_id = %v want appliance-123", row["appliance_id"])
	}
}

func TestRenderCSVHeaderMatchesPython(t *testing.T) {
	a := attachLinks([]Appliance{sampleAppliance()})
	var buf bytes.Buffer
	renderCSV(a, &buf)
	got := buf.String()
	wantHeader := "project,project_url,appliance_id,appliance_url,model,state,create_time,update_time"
	if !strings.HasPrefix(got, wantHeader) {
		t.Errorf("CSV header missing.\nfirst line: %q\nwant prefix: %q",
			strings.SplitN(got, "\n", 2)[0], wantHeader)
	}
}

func TestRenderTableEmitsHyperlink(t *testing.T) {
	a := attachLinks([]Appliance{sampleAppliance()})
	var buf bytes.Buffer
	loc, _ := time.LoadLocation("UTC")
	renderTable(a, loc, &buf)
	got := buf.String()
	if !strings.Contains(got, "\x1b]8;") {
		t.Errorf("table did not emit OSC 8 hyperlink escape")
	}
	wantLink := "https://pantheon.corp.google.com/appliances/us-central1/" +
		"appliance-123;tab=configuration?project=p1"
	if !strings.Contains(got, wantLink) {
		t.Errorf("table missing pantheon link.\noutput: %s", got)
	}
}

func TestRenderHTMLEmbedsJSON(t *testing.T) {
	a := attachLinks([]Appliance{sampleAppliance()})
	doc, err := buildHTMLReport(a, "999", "UTC")
	if err != nil {
		t.Fatalf("buildHTMLReport: %v", err)
	}
	if !strings.Contains(doc, `<script id="report-data" type="application/json">`) {
		t.Error("missing report-data script tag")
	}
	if !strings.Contains(doc, `"appliance_id": "appliance-123"`) {
		t.Error("HTML doesn't embed appliance_id field")
	}
	if !strings.Contains(doc, `<button data-sort="model">Model</button>`) {
		t.Error("HTML missing Model column header")
	}
	// Heading uses real org id.
	if !strings.Contains(doc, "org 999</h1>") {
		t.Error("HTML heading missing org id")
	}
}

func TestExpandRepeatedFlags(t *testing.T) {
	got := expandRepeatedFlags(
		[]string{"--projects", "a", "b", "c", "--org-id", "999"},
		"--projects", "--state-filter",
	)
	want := []string{"--projects", "a", "--projects", "b", "--projects", "c", "--org-id", "999"}
	if !equalStrings(got, want) {
		t.Errorf("expandRepeatedFlags = %v\nwant %v", got, want)
	}
}

func TestQuotaProjectResolution(t *testing.T) {
	t.Setenv("GCP_QUOTA_PROJECT", "")
	got, err := resolveQuotaProject(cliFlags{quotaProject: "explicit"}, "fromADC")
	if err != nil || got != "explicit" {
		t.Errorf("flag wins: got (%q, %v)", got, err)
	}

	t.Setenv("GCP_QUOTA_PROJECT", "fromEnv")
	got, err = resolveQuotaProject(cliFlags{}, "fromADC")
	if err != nil || got != "fromEnv" {
		t.Errorf("env wins over ADC: got (%q, %v)", got, err)
	}

	t.Setenv("GCP_QUOTA_PROJECT", "")
	got, err = resolveQuotaProject(cliFlags{projects: []string{"first", "second"}}, "fromADC")
	if err != nil || got != "fromADC" {
		t.Errorf("ADC wins over auto-derive: got (%q, %v)", got, err)
	}

	got, err = resolveQuotaProject(cliFlags{projects: []string{"first", "second"}}, "")
	if err != nil || got != "first" {
		t.Errorf("auto-derive from --projects: got (%q, %v)", got, err)
	}

	got, err = resolveQuotaProject(cliFlags{}, "")
	if err == nil {
		t.Errorf("expected error for org-wide-without-quota, got %q", got)
	}
	if err != nil && !strings.Contains(err.Error(), "set-quota-project") {
		t.Errorf("error should reference gcloud set-quota-project; got %q", err.Error())
	}
}

func TestADCRoundTripPreservesUnknownFields(t *testing.T) {
	original := []byte(`{
  "type": "authorized_user",
  "client_id": "id",
  "client_secret": "secret",
  "refresh_token": "rt-old",
  "quota_project_id": "qproj",
  "account": "user@example.com",
  "universe_domain": "googleapis.com"
}`)
	parsed, err := parseADC(original)
	if err != nil {
		t.Fatalf("parseADC: %v", err)
	}
	if parsed.RefreshToken != "rt-old" || parsed.QuotaProjectID != "qproj" {
		t.Errorf("parsed unexpected: %+v", parsed)
	}

	// Mutate refresh_token (simulating gcloud-style rotation) and re-marshal.
	parsed.RefreshToken = "rt-new"
	out, err := parsed.marshal()
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	got := string(out)
	for _, want := range []string{
		`"account"`, `"universe_domain"`, `"refresh_token": "rt-new"`,
		`"quota_project_id": "qproj"`,
	} {
		if !strings.Contains(got, want) {
			t.Errorf("marshalled output missing %s\n--- got ---\n%s", want, got)
		}
	}
	if strings.Contains(got, "rt-old") {
		t.Errorf("marshalled output still contains stale refresh token")
	}
}

func TestADCFileIsRemovedQuotaProjectWhenCleared(t *testing.T) {
	original := []byte(`{
  "type": "authorized_user",
  "client_id": "id",
  "client_secret": "secret",
  "refresh_token": "rt",
  "quota_project_id": "qproj"
}`)
	parsed, err := parseADC(original)
	if err != nil {
		t.Fatal(err)
	}
	parsed.QuotaProjectID = ""
	out, err := parsed.marshal()
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(out), "quota_project_id") {
		t.Errorf("cleared quota_project_id should be omitted, got: %s", out)
	}
}

func TestCredentialsPathHonorsCloudSDKConfig(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("CLOUDSDK_CONFIG", tmp)
	got, err := credentialsPath()
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(tmp, "application_default_credentials.json")
	if got != want {
		t.Errorf("credentialsPath = %q want %q", got, want)
	}
}

func TestCredentialsPathDefaultIsGcloudDir(t *testing.T) {
	t.Setenv("CLOUDSDK_CONFIG", "")
	got, err := credentialsPath()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(got, filepath.Join("gcloud", "application_default_credentials.json")) {
		t.Errorf("default credentials path should land in gcloud/...; got %q", got)
	}
}

func TestWriteADCSetsRestrictivePermissions(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX-only mode check")
	}
	tmp := t.TempDir()
	path := filepath.Join(tmp, "gcloud", "application_default_credentials.json")
	creds := &adcFile{
		Type: "authorized_user", ClientID: "x", ClientSecret: "y", RefreshToken: "z",
	}
	if err := writeADC(path, creds); err != nil {
		t.Fatalf("writeADC: %v", err)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if mode := st.Mode().Perm(); mode != 0o600 {
		t.Errorf("file mode = %o want 600", mode)
	}
	parent, err := os.Stat(filepath.Dir(path))
	if err != nil {
		t.Fatal(err)
	}
	if mode := parent.Mode().Perm(); mode != 0o700 {
		t.Errorf("parent dir mode = %o want 700", mode)
	}
}

func TestPantheonLinksUseQueryEncoding(t *testing.T) {
	// Ensure the URL encoder outputs `=` not `%3D` for the project value.
	encoded := url.Values{"project": []string{"p1"}}.Encode()
	if encoded != "project=p1" {
		t.Errorf("urlencode behavior changed: %q", encoded)
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
