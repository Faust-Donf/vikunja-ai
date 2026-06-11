# Changelog

## [Unreleased]

### Added

- Added deletion for individual AI-generated task preview rows before appending them to the table.
- Added a project filter for narrowing the task table to a single project.
- Added a one-click weekly plan generator with stable project-grouped output.
- Changed the task table to fit all columns without horizontal dragging and wrap long text inside textarea cells.
- Changed weekly plan generation from table output to group-message-style text by project.
- Fixed project-field typing losing focus and changed urgency to include tasks due within 3 days.

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
- Added a logout action for clearing the protected task-table session.
- Added backend validation for task status and priority values.
- Added backend validation for task plan dates on create and update.
- Added a reset-view action for clearing filters, sorting, quadrant selection, and selected rows.
- Updated README coverage for current table, AI, auth, export, and validation features.
- Added backend regression coverage for logout clearing authenticated access.
- Changed delete-task behavior to return 404 when the task is already missing.
- Added UTF-8 BOM to CSV export for better direct opening in Excel.

### Fixed

- Fixed AI assistant Markdown reply rendering for ordered lists and bold text.
- Fixed the right-side AI assistant panel width and quadrant card title alignment.
- Fixed AI task generation over-splitting single-sentence requests into speculative follow-up tasks.
- Fixed AI task generation missing project and fuzzy Chinese deadline fields when they are present in the prompt.

## [Unreleased]

### Added

- AI 生成任务预览中的"项目"输入框支持下拉选择已有项目，也支持直接打字创建新项目（基于 HTML datalist）。
- Added OpenAI-compatible API configuration in the web UI, including API Base URL, API Key, and model settings.

### Fixed

- Fixed AI task generation not parsing Chinese natural-language time expressions like "下周前三" (next week, first 3 days), "本周前N" (this week, first N days), and relative dates like "今天/明天/后天" (today/tomorrow/day after tomorrow).
