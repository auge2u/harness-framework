# Workflows Pending Activation

These workflow files were staged here because the publishing token lacked the
`workflow` OAuth scope (GitHub requires it for changes under `.github/workflows/`).

To activate: move both files to `.github/workflows/` and commit from any client
with the workflow scope (GitHub web UI: upload files, or `git mv` locally).
