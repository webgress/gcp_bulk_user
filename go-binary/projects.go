package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
)

const resourceManagerSearchURL = "https://cloudresourcemanager.googleapis.com/v3/projects:search"

type rmProject struct {
	Name        string `json:"name"`
	ProjectID   string `json:"projectId"`
	DisplayName string `json:"displayName"`
	State       string `json:"state"`
}

type rmSearchResponse struct {
	Projects      []rmProject `json:"projects"`
	NextPageToken string      `json:"nextPageToken"`
}

// listOrgProjects returns the project_ids of every ACTIVE project under the
// given organization. Hand-rolled REST so the X-Goog-User-Project header
// (already injected by the http.Client transport) governs quota for these
// calls too.
func listOrgProjects(ctx context.Context, client *http.Client, orgID string) ([]string, error) {
	query := fmt.Sprintf("parent:organizations/%s state:ACTIVE", orgID)

	var ids []string
	pageToken := ""

	for {
		params := url.Values{}
		params.Set("query", query)
		if pageToken != "" {
			params.Set("pageToken", pageToken)
		}

		req, err := http.NewRequestWithContext(ctx, http.MethodGet,
			resourceManagerSearchURL+"?"+params.Encode(), nil)
		if err != nil {
			return nil, err
		}

		resp, err := client.Do(req)
		if err != nil {
			return nil, fmt.Errorf("resource manager request failed: %w", err)
		}
		body, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			return nil, fmt.Errorf("reading resource manager response: %w", err)
		}

		if resp.StatusCode != http.StatusOK {
			return nil, fmt.Errorf("resource manager returned HTTP %d: %s",
				resp.StatusCode, truncate(string(body), 400))
		}

		var page rmSearchResponse
		if err := json.Unmarshal(body, &page); err != nil {
			return nil, fmt.Errorf("decoding resource manager response: %w", err)
		}

		for _, p := range page.Projects {
			if p.ProjectID == "" {
				continue
			}
			ids = append(ids, p.ProjectID)
		}

		if page.NextPageToken == "" {
			break
		}
		pageToken = page.NextPageToken
	}

	return ids, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
