# GitHub Analytics Dashboard

Build an analytics dashboard for the `pallets/flask` GitHub repository.

Fetch commits, issues, and releases from the GitHub REST API and store them
in a local SQLite database. Create a Flask web server that exposes JSON
endpoints for the stored data and a stats summary. Build an HTML dashboard
UI that displays the data in a readable layout.

The server should accept a `--port` CLI argument and handle unknown routes
gracefully without crashing.

Use the `GITHUB_TOKEN` environment variable for API auth if it is set.
