package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
	"sync"
	"unicode"
)

const transferApplianceBaseURL = "https://transferappliance.googleapis.com/v1alpha1"

// Appliance is the normalized per-record shape we render. JSON keys exactly
// match python-gcloud's output so JSON/CSV/HTML are drop-in compatible.
type Appliance struct {
	Project      string `json:"project"`
	Name         string `json:"name"`
	DisplayName  string `json:"display_name"`
	State        string `json:"state"`
	Model        string `json:"model"`
	CreateTime   string `json:"create_time"`
	UpdateTime   string `json:"update_time"`
	ApplianceID  string `json:"appliance_id"`
	Location     string `json:"location"`
	ProjectURL   string `json:"project_url,omitempty"`
	ApplianceURL string `json:"appliance_url,omitempty"`
}

type ProjectError struct {
	Project string `json:"project"`
	Error   string `json:"error"`
}

type ScanResults struct {
	Appliances []Appliance
	Errors     []ProjectError
}

type rawAppliance struct {
	Name           string          `json:"name"`
	DisplayName    json.RawMessage `json:"displayName"`
	State          json.RawMessage `json:"state"`
	Status         json.RawMessage `json:"status"`
	Model          json.RawMessage `json:"model"`
	ApplianceModel json.RawMessage `json:"applianceModel"`
	ApplianceType  json.RawMessage `json:"applianceType"`
	Type           json.RawMessage `json:"type"`
	CreateTime     json.RawMessage `json:"createTime"`
	UpdateTime     json.RawMessage `json:"updateTime"`
}

type appliancesResponse struct {
	Appliances []json.RawMessage `json:"appliances"`
}

type projectScanResult struct {
	project    string
	appliances []Appliance
	err        string
}

// fetchAppliancesForProject calls the v1alpha1 REST endpoint for one project.
// On any non-2xx or transport error returns ([], "<diagnostic>"). The diagnostic
// is propagated to the user via stderr while other projects continue.
func fetchAppliancesForProject(ctx context.Context, client *http.Client, projectID string) projectScanResult {
	url := fmt.Sprintf("%s/projects/%s/locations/-/appliances", transferApplianceBaseURL, projectID)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return projectScanResult{project: projectID, err: fmt.Sprintf("[api] %s: %v", projectID, err)}
	}

	resp, err := client.Do(req)
	if err != nil {
		return projectScanResult{project: projectID, err: fmt.Sprintf("[api] %s: %v", projectID, err)}
	}
	body, readErr := io.ReadAll(resp.Body)
	resp.Body.Close()
	if readErr != nil {
		return projectScanResult{project: projectID, err: fmt.Sprintf("[api] %s: %v", projectID, readErr)}
	}

	if resp.StatusCode != http.StatusOK {
		snippet := strings.ReplaceAll(strings.TrimSpace(string(body)), "\n", " ")
		if len(snippet) > 200 {
			snippet = snippet[:200]
		}
		return projectScanResult{
			project: projectID,
			err:     fmt.Sprintf("[api] %s: HTTP %d %s", projectID, resp.StatusCode, snippet),
		}
	}

	var payload appliancesResponse
	if err := json.Unmarshal(body, &payload); err != nil {
		return projectScanResult{project: projectID, err: fmt.Sprintf("[api] %s: invalid JSON: %v", projectID, err)}
	}

	var normalized []Appliance
	var rowErrors []string

	for _, raw := range payload.Appliances {
		// Reject obvious non-objects (matches Python's "non-object record" guard).
		trimmed := bytes_trimSpace(raw)
		if len(trimmed) == 0 || trimmed[0] != '{' {
			rowErrors = append(rowErrors, fmt.Sprintf("non-object record: %s", typeNameFromJSON(trimmed)))
			continue
		}

		var rec rawAppliance
		if err := json.Unmarshal(raw, &rec); err != nil {
			rowErrors = append(rowErrors, fmt.Sprintf("malformed record: %v", err))
			continue
		}

		if rec.Name == "" {
			rowErrors = append(rowErrors, "record missing resource name")
			continue
		}

		location, applianceID, ok := parseResourceName(rec.Name)
		if !ok {
			rowErrors = append(rowErrors, fmt.Sprintf("invalid resource name: %q", rec.Name))
			continue
		}

		normalized = append(normalized, Appliance{
			Project:     projectID,
			Name:        rec.Name,
			DisplayName: sanitizeDisplayName(jsonToString(rec.DisplayName)),
			State:       firstNonEmpty(jsonToString(rec.State), jsonToString(rec.Status), "UNKNOWN"),
			Model: firstNonEmpty(
				jsonToString(rec.Model),
				jsonToString(rec.ApplianceModel),
				jsonToString(rec.ApplianceType),
				jsonToString(rec.Type),
				"N/A",
			),
			CreateTime:  jsonToStringOr(rec.CreateTime, "N/A"),
			UpdateTime:  jsonToStringOr(rec.UpdateTime, "N/A"),
			ApplianceID: applianceID,
			Location:    location,
		})
	}

	res := projectScanResult{project: projectID, appliances: normalized}
	if len(rowErrors) > 0 {
		sample := rowErrors
		if len(sample) > 3 {
			sample = append([]string{}, rowErrors[:3]...)
			sample = append(sample, "...")
		}
		res.err = fmt.Sprintf("skipped %d malformed appliance record(s): %s",
			len(rowErrors), strings.Join(sample, "; "))
	}
	return res
}

// getAllAppliances scans projectIDs in parallel, deduping first.
func getAllAppliances(ctx context.Context, client *http.Client, projectIDs []string, workers int) ScanResults {
	projectIDs = dedupePreserveOrder(projectIDs)
	if workers < 1 {
		workers = 1
	}

	var (
		mu         sync.Mutex
		appliances []Appliance
		errs       []ProjectError
	)

	jobs := make(chan string)
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for pid := range jobs {
				res := fetchAppliancesForProject(ctx, client, pid)
				mu.Lock()
				appliances = append(appliances, res.appliances...)
				if res.err != "" {
					errs = append(errs, ProjectError{Project: res.project, Error: res.err})
				}
				mu.Unlock()
			}
		}()
	}

	for _, pid := range projectIDs {
		jobs <- pid
	}
	close(jobs)
	wg.Wait()

	sort.SliceStable(appliances, func(i, j int) bool {
		if appliances[i].Project != appliances[j].Project {
			return appliances[i].Project < appliances[j].Project
		}
		if appliances[i].ApplianceID != appliances[j].ApplianceID {
			return appliances[i].ApplianceID < appliances[j].ApplianceID
		}
		return appliances[i].Name < appliances[j].Name
	})
	sort.SliceStable(errs, func(i, j int) bool { return errs[i].Project < errs[j].Project })

	return ScanResults{Appliances: appliances, Errors: errs}
}

// parseResourceName pulls (location, appliance_id) out of
// projects/X/locations/L/appliances/Z. Requires exactly 6 non-empty segments
// so subresource paths and malformed inputs fall through.
func parseResourceName(name string) (location, applianceID string, ok bool) {
	parts := strings.Split(name, "/")
	if len(parts) != 6 {
		return "", "", false
	}
	if parts[0] != "projects" || parts[2] != "locations" || parts[4] != "appliances" {
		return "", "", false
	}
	if parts[1] == "" || parts[3] == "" || parts[5] == "" {
		return "", "", false
	}
	return parts[3], parts[5], true
}

func sanitizeDisplayName(value string) string {
	var b strings.Builder
	for _, r := range value {
		if r < ' ' || r == 0x7f {
			b.WriteByte(' ')
			continue
		}
		b.WriteRune(r)
	}
	return strings.Join(strings.FieldsFunc(b.String(), unicode.IsSpace), " ")
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if v != "" {
			return v
		}
	}
	return ""
}

func dedupePreserveOrder(in []string) []string {
	seen := make(map[string]struct{}, len(in))
	out := make([]string, 0, len(in))
	for _, v := range in {
		if _, ok := seen[v]; ok {
			continue
		}
		seen[v] = struct{}{}
		out = append(out, v)
	}
	return out
}

func jsonToString(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	trimmed := bytes_trimSpace(raw)
	if len(trimmed) == 0 || string(trimmed) == "null" {
		return ""
	}
	if trimmed[0] == '"' {
		var s string
		if err := json.Unmarshal(trimmed, &s); err == nil {
			return s
		}
	}
	return string(trimmed)
}

func jsonToStringOr(raw json.RawMessage, fallback string) string {
	if v := jsonToString(raw); v != "" {
		return v
	}
	return fallback
}

func bytes_trimSpace(b []byte) []byte {
	start := 0
	for start < len(b) && (b[start] == ' ' || b[start] == '\t' || b[start] == '\n' || b[start] == '\r') {
		start++
	}
	end := len(b)
	for end > start && (b[end-1] == ' ' || b[end-1] == '\t' || b[end-1] == '\n' || b[end-1] == '\r') {
		end--
	}
	return b[start:end]
}

func typeNameFromJSON(b []byte) string {
	if len(b) == 0 {
		return "empty"
	}
	switch b[0] {
	case '"':
		return "string"
	case '[':
		return "array"
	case 't', 'f':
		return "bool"
	case 'n':
		return "null"
	default:
		return "number"
	}
}
