package main

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"html"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"

	// Embeds the IANA timezone database into the binary. Required for
	// time.LoadLocation to work on Windows, which has no /usr/share/zoneinfo.
	_ "time/tzdata"

	"github.com/jedib0t/go-pretty/v6/table"
	"github.com/jedib0t/go-pretty/v6/text"
)

const (
	defaultTimezone = "America/Los_Angeles"
	pantheonBase    = "https://pantheon.corp.google.com"
	envQuotaProject = "GCP_QUOTA_PROJECT"
)

var stateColors = map[string]text.Color{
	"DRAFT":                text.FgWhite,
	"REQUESTED":            text.FgYellow,
	"PREPARING":            text.FgYellow,
	"SHIPPING_TO_CUSTOMER": text.FgCyan,
	"ON_SITE":              text.FgGreen,
	"PROCESSING":           text.FgMagenta,
	"WIPED":                text.FgBlue,
	"CANCELLED":            text.FgRed,
}

type stringSlice []string

func (s *stringSlice) String() string { return strings.Join(*s, " ") }
func (s *stringSlice) Set(v string) error {
	*s = append(*s, v)
	return nil
}

type cliFlags struct {
	orgID        string
	projects     []string
	quotaProject string
	format       string
	workers      int
	stateFilter  []string
	timezone     string
	htmlFile     string
	logout       bool
}

func main() {
	flags, err := parseFlags(os.Args[1:])
	if err != nil {
		// `--help` is a documented success path -- the flag package prints
		// usage and returns flag.ErrHelp; we exit 0 in that case so scripts
		// (and our own release-workflow smoke test) don't see a failure.
		if errors.Is(err, flag.ErrHelp) {
			os.Exit(0)
		}
		os.Exit(2)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if flags.logout {
		os.Exit(runLogout(ctx))
	}

	loc, err := time.LoadLocation(flags.timezone)
	if err != nil {
		logf("Unknown timezone: %q. Use an IANA name like 'America/Los_Angeles' or 'UTC'.", flags.timezone)
		os.Exit(2)
	}

	adcPath, err := credentialsPath()
	if err != nil {
		logf("Could not resolve credentials path: %v", err)
		os.Exit(2)
	}

	// Read existing ADC if present so its quota_project_id can feed quota
	// resolution. A missing file is fine -- we'll run consent flow later if
	// quota resolution still succeeds.
	existing, readErr := readADC(adcPath)
	var adcQuota string
	if readErr == nil && existing != nil {
		adcQuota = existing.QuotaProjectID
	} else if readErr != nil && !os.IsNotExist(readErr) {
		logf("Warning: could not parse %s: %v", adcPath, readErr)
		existing = nil
	}

	quotaProject, err := resolveQuotaProject(flags, adcQuota)
	if err != nil {
		logf("%s", err.Error())
		os.Exit(2)
	}

	httpClient, err := buildAuthClient(ctx, adcPath, existing, quotaProject)
	if err != nil {
		logf("Auth failed: %v", err)
		os.Exit(2)
	}

	var projectIDs []string
	if len(flags.projects) > 0 {
		projectIDs = dedupePreserveOrder(flags.projects)
		logf("Using %d specified project(s).", len(projectIDs))
	} else {
		logf("Discovering projects in org %s...", flags.orgID)
		discovered, err := listOrgProjects(ctx, httpClient, flags.orgID)
		if err != nil {
			logf("Failed to discover projects: %v", err)
			os.Exit(2)
		}
		if len(discovered) == 0 {
			logf("No projects found in organization.")
			os.Exit(1)
		}
		projectIDs = dedupePreserveOrder(discovered)
		logf("Found %d project(s).", len(projectIDs))
	}

	logf("Querying Transfer Appliance status...")
	results := getAllAppliances(ctx, httpClient, projectIDs, flags.workers)

	appliances := results.Appliances
	if len(flags.stateFilter) > 0 {
		filter := make(map[string]struct{}, len(flags.stateFilter))
		for _, s := range flags.stateFilter {
			filter[strings.ToUpper(s)] = struct{}{}
		}
		filtered := appliances[:0]
		for _, a := range appliances {
			if _, ok := filter[strings.ToUpper(a.State)]; ok {
				filtered = append(filtered, a)
			}
		}
		appliances = filtered
	}

	appliances = attachLinks(appliances)

	if len(results.Errors) > 0 {
		logf("Scan failed for %d project(s); results may be incomplete:", len(results.Errors))
		for _, e := range results.Errors {
			logf("  %s: %s", e.Project, e.Error)
		}
	}

	if len(appliances) == 0 {
		if len(results.Errors) > 0 {
			logf("No Transfer Appliances found in successfully scanned projects.")
			os.Exit(2)
		}
		logf("No Transfer Appliances found across scanned projects.")
		return
	}

	logf("Found %d appliance(s).\n", len(appliances))

	switch flags.format {
	case "json":
		renderJSON(appliances, os.Stdout)
	case "csv":
		renderCSV(appliances, os.Stdout)
	case "html":
		if err := renderHTML(appliances, flags.orgID, flags.timezone, flags.htmlFile, os.Stdout); err != nil {
			logf("Failed to render HTML: %v", err)
			os.Exit(2)
		}
	default:
		renderTable(appliances, loc, os.Stdout)
	}

	if len(results.Errors) > 0 {
		os.Exit(2)
	}
}

func parseFlags(argv []string) (cliFlags, error) {
	fs := flag.NewFlagSet("gcp-appliance-status", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	fs.Usage = func() {
		fmt.Fprintln(os.Stderr, "View Google Transfer Appliance status across GCP projects in an org.")
		fmt.Fprintln(os.Stderr)
		fmt.Fprintln(os.Stderr, "Usage: gcp-appliance-status --org-id ID [flags]")
		fmt.Fprintln(os.Stderr)
		fs.PrintDefaults()
	}

	var (
		orgID        = fs.String("org-id", "", "GCP organization ID (numeric). Required (except with --logout).")
		quotaProject = fs.String("quota-project", "", "Project to charge API quota against (env: "+envQuotaProject+").")
		format       = fs.String("format", "table", "Output format: table | json | csv | html.")
		workers      = fs.Int("workers", 10, "Max parallel workers for API calls.")
		timezone     = fs.String("timezone", defaultTimezone, "IANA timezone for table timestamps. JSON/CSV keep raw ISO-8601.")
		htmlFile     = fs.String("html-file", "", "Write HTML output to this file (only meaningful with --format html).")
		logout       = fs.Bool("logout", false, "Revoke and delete the cached OAuth token, then exit.")
	)

	var (
		projects    stringSlice
		stateFilter stringSlice
	)
	fs.Var(&projects, "projects", "Specific project IDs to query (repeatable; default: discover from org).")
	fs.Var(&stateFilter, "state-filter", "Only show appliances in these states (repeatable, e.g. --state-filter ON_SITE).")

	// Pre-process argv to support the Python-style nargs='*' on --projects and
	// --state-filter (whitespace-separated values after the flag). The Go flag
	// pkg parses one value per occurrence, so we expand `--projects a b c`
	// into `--projects a --projects b --projects c` ahead of time.
	expanded := expandRepeatedFlags(argv, "--projects", "--state-filter")

	if err := fs.Parse(expanded); err != nil {
		return cliFlags{}, err
	}

	if *orgID == "" && !*logout {
		fmt.Fprintln(os.Stderr, "error: --org-id is required (unless --logout)")
		fs.Usage()
		return cliFlags{}, fmt.Errorf("missing --org-id")
	}
	if *workers < 1 {
		fmt.Fprintln(os.Stderr, "error: --workers must be greater than 0")
		return cliFlags{}, fmt.Errorf("invalid --workers")
	}
	switch *format {
	case "table", "json", "csv", "html":
	default:
		fmt.Fprintf(os.Stderr, "error: --format must be one of table|json|csv|html (got %q)\n", *format)
		return cliFlags{}, fmt.Errorf("invalid --format")
	}

	return cliFlags{
		orgID:        *orgID,
		projects:     []string(projects),
		quotaProject: *quotaProject,
		format:       *format,
		workers:      *workers,
		stateFilter:  []string(stateFilter),
		timezone:     *timezone,
		htmlFile:     *htmlFile,
		logout:       *logout,
	}, nil
}

// expandRepeatedFlags rewrites `--flag a b c` into `--flag a --flag b --flag c`
// for the given flag names so that stringSlice accumulators see every value.
// Supports `--flag=value` form unchanged and stops collecting at the next flag.
func expandRepeatedFlags(argv []string, names ...string) []string {
	nameSet := make(map[string]struct{}, len(names))
	for _, n := range names {
		nameSet[n] = struct{}{}
	}
	out := make([]string, 0, len(argv))
	for i := 0; i < len(argv); i++ {
		arg := argv[i]
		_, isRepeated := nameSet[arg]
		if !isRepeated {
			out = append(out, arg)
			continue
		}
		// Greedy: collect every following non-flag token as a value of this flag.
		out = append(out, arg)
		j := i + 1
		first := true
		for j < len(argv) && !strings.HasPrefix(argv[j], "-") {
			if !first {
				out = append(out, arg)
			}
			out = append(out, argv[j])
			first = false
			j++
		}
		if first {
			// No values supplied -- restore behavior of "missing argument" error
			// by leaving the flag name alone. flag.Parse will error out.
		}
		i = j - 1
	}
	return out
}

// resolveQuotaProject walks the SPEC's resolution order:
//  1. --quota-project flag
//  2. GCP_QUOTA_PROJECT env var
//  3. quota_project_id from the ADC file (set by
//     `gcloud auth application-default set-quota-project`)
//  4. first --projects value (auto-derive convenience)
//  5. otherwise, error out (org-wide discovery without a quota project)
func resolveQuotaProject(f cliFlags, adcQuota string) (string, error) {
	if f.quotaProject != "" {
		return f.quotaProject, nil
	}
	if env := os.Getenv(envQuotaProject); env != "" {
		return env, nil
	}
	if adcQuota != "" {
		return adcQuota, nil
	}
	if len(f.projects) > 0 {
		return f.projects[0], nil
	}
	return "", fmt.Errorf("A quota project is required for org-wide project discovery.\n" +
		"Pass --quota-project YOUR_PROJECT, or set one persistently with:\n" +
		"    gcloud auth application-default set-quota-project YOUR_PROJECT\n" +
		"Alternatively, restrict this run with --projects to auto-derive one from the list.")
}

func projectURL(project string) string {
	return pantheonBase + "/appliances?" + url.Values{"project": []string{project}}.Encode()
}

func applianceURL(project, location, applianceID string) string {
	if location == "" {
		return projectURL(project)
	}
	q := url.Values{"project": []string{project}}.Encode()
	return fmt.Sprintf("%s/appliances/%s/%s;tab=configuration?%s",
		pantheonBase,
		url.PathEscape(location),
		url.PathEscape(applianceID),
		q,
	)
}

func attachLinks(in []Appliance) []Appliance {
	out := make([]Appliance, len(in))
	for i, a := range in {
		a.ProjectURL = projectURL(a.Project)
		a.ApplianceURL = applianceURL(a.Project, a.Location, a.ApplianceID)
		out[i] = a
	}
	return out
}

func formatTimestamp(iso string, loc *time.Location) string {
	if iso == "" || iso == "N/A" {
		if iso == "" {
			return "N/A"
		}
		return iso
	}
	for _, layout := range []string{
		time.RFC3339Nano,
		time.RFC3339,
		"2006-01-02T15:04:05Z",
		"2006-01-02T15:04:05.999999999Z",
	} {
		if t, err := time.Parse(layout, iso); err == nil {
			return t.In(loc).Format("2006-01-02 15:04 MST")
		}
	}
	return iso
}

func renderJSON(appliances []Appliance, out io.Writer) {
	enc := json.NewEncoder(out)
	enc.SetIndent("", "  ")
	enc.SetEscapeHTML(false)
	_ = enc.Encode(appliances)
}

func renderCSV(appliances []Appliance, out io.Writer) {
	w := csv.NewWriter(out)
	defer w.Flush()
	_ = w.Write([]string{
		"project", "project_url", "appliance_id", "appliance_url",
		"model", "state", "create_time", "update_time",
	})
	for _, a := range appliances {
		_ = w.Write([]string{
			safeCSVCell(a.Project),
			safeCSVCell(a.ProjectURL),
			safeCSVCell(a.ApplianceID),
			safeCSVCell(a.ApplianceURL),
			safeCSVCell(a.Model),
			safeCSVCell(a.State),
			safeCSVCell(a.CreateTime),
			safeCSVCell(a.UpdateTime),
		})
	}
}

// safeCSVCell prefixes formula-injection-prone cells with a single quote so
// spreadsheet apps treat them as text. Mirrors python-gcloud's _safe_csv_cell.
func safeCSVCell(s string) string {
	if s == "" {
		return s
	}
	switch s[0] {
	case '=', '+', '-', '@', '\t', '\r':
		return "'" + s
	}
	return s
}

// hyperlink wraps text with an OSC 8 escape sequence so capable terminals
// render it as a clickable link. Falls through cleanly on terminals that
// don't recognise it.
func hyperlink(text, href string) string {
	if href == "" {
		return text
	}
	return "\x1b]8;;" + href + "\x1b\\" + text + "\x1b]8;;\x1b\\"
}

func renderTable(appliances []Appliance, loc *time.Location, out io.Writer) {
	t := table.NewWriter()
	t.SetOutputMirror(out)
	t.SetTitle("Transfer Appliance Status")
	t.SetStyle(table.StyleRounded)
	t.Style().Options.SeparateRows = true
	t.AppendHeader(table.Row{"Project", "Appliance ID", "Model", "State", "Created", "Updated"})

	for _, a := range appliances {
		state := a.State
		color, ok := stateColors[strings.ToUpper(state)]
		var stateCell string
		if ok {
			stateCell = color.Sprint(state)
		} else {
			stateCell = state
		}
		projectCell := text.Bold.Sprint(hyperlink(a.Project, a.ProjectURL))
		applianceCell := hyperlink(a.ApplianceID, a.ApplianceURL)
		t.AppendRow(table.Row{
			projectCell,
			applianceCell,
			a.Model,
			stateCell,
			formatTimestamp(a.CreateTime, loc),
			formatTimestamp(a.UpdateTime, loc),
		})
	}
	t.Render()
}

func renderHTML(appliances []Appliance, orgID, tzName, htmlFile string, out io.Writer) error {
	doc, err := buildHTMLReport(appliances, orgID, tzName)
	if err != nil {
		return err
	}

	if htmlFile != "" {
		path := expandUser(htmlFile)
		if err := writeHTMLReport(path, doc); err != nil {
			return err
		}
		logf("Wrote HTML report to %s", path)
		return nil
	}

	if isTerminal(out) {
		path := defaultHTMLReportPath()
		if err := writeHTMLReport(path, doc); err != nil {
			return err
		}
		logf("Wrote HTML report to %s", path)
		if err := openHTMLFile(path); err != nil {
			logf("Failed to open HTML report automatically: %v", err)
		}
		return nil
	}

	_, err = io.WriteString(out, doc)
	return err
}

func defaultHTMLReportPath() string {
	dir := os.TempDir()
	if runtime.GOOS != "windows" {
		dir = "/tmp"
	}
	return filepath.Join(dir, "report_"+time.Now().Format("20060102_150405")+".html")
}

func writeHTMLReport(path, content string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(content), 0o644)
}

func openHTMLFile(path string) error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", path)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", path)
	default:
		cmd = exec.Command("xdg-open", path)
	}
	return cmd.Run()
}

func expandUser(path string) string {
	if strings.HasPrefix(path, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, path[2:])
		}
	}
	return path
}

// isTerminal reports whether out is a terminal (best-effort: we treat
// non-os.File writers as non-terminals).
func isTerminal(out io.Writer) bool {
	f, ok := out.(*os.File)
	if !ok {
		return false
	}
	info, err := f.Stat()
	if err != nil {
		return false
	}
	return (info.Mode() & os.ModeCharDevice) != 0
}

func buildHTMLReport(appliances []Appliance, orgID, tzName string) (string, error) {
	// Sort just for stable output (already sorted upstream, but be defensive).
	sorted := make([]Appliance, len(appliances))
	copy(sorted, appliances)
	sort.SliceStable(sorted, func(i, j int) bool {
		if sorted[i].Project != sorted[j].Project {
			return sorted[i].Project < sorted[j].Project
		}
		return sorted[i].ApplianceID < sorted[j].ApplianceID
	})

	jsonBytes, err := json.MarshalIndent(sorted, "", "  ")
	if err != nil {
		return "", err
	}
	reportJSON := strings.ReplaceAll(string(jsonBytes), "</", "<\\/")
	tzNameJSON, err := json.Marshal(tzName)
	if err != nil {
		return "", err
	}
	generatedAt := time.Now().Format(time.RFC3339)
	heading := "Transfer Appliance Report &mdash; org " + html.EscapeString(orgID)

	doc := htmlReportTemplate
	doc = strings.ReplaceAll(doc, "__HEADING__", heading)
	doc = strings.ReplaceAll(doc, "__REPORT_JSON__", reportJSON)
	doc = strings.ReplaceAll(doc, "__TZ_NAME_JSON__", string(tzNameJSON))
	doc = strings.ReplaceAll(doc, "__GENERATED_AT__", html.EscapeString(generatedAt))
	doc = strings.ReplaceAll(doc, "__TZ_NAME__", html.EscapeString(tzName))
	return doc, nil
}

func logf(format string, a ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", a...)
}
