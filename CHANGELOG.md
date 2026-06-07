# Changelog

## [0.1.0] - 2026-06-07

### Added

- Initial personal plan table app with SQLite persistence.
- Added AI assistant, weekly report generation, task generation, deduplication, and completion archive flow.
- Added Markdown rendering for AI assistant replies.
- Added AI-generated task review flow with duplicate skipping and missing DDL/project completion.
- Added backend and frontend contract tests.
- Added tests to the Docker image for server-side verification.
- Added CSV export and JSON backup download.
- Added JSON backup import with duplicate-safe merge behavior.
- Replaced deprecated FastAPI startup hook with lifespan initialization.
- Added optional access-token login for protecting personal tasks and AI endpoints.
- Added clickable table-column sorting for spreadsheet-style task review.
- Added bulk selection with batch completion archive and deletion actions.
- Added quick plan-date filters for overdue, today, this week, next 7 days, and undated tasks.
- Refined the this-week date filter to use the Monday-Sunday calendar week.
