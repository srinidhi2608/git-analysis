-- =============================================================================
-- clear_data.sql
-- Remove all application data and reset identity sequences.
-- Safe to run multiple times (TRUNCATE ... RESTART IDENTITY CASCADE).
-- =============================================================================

TRUNCATE
  pull_request_comments,
  commits,
  pull_requests,
  developers,
  repositories,
  azure_work_item_tags,
  azure_work_items,
  azure_pipeline_runs
RESTART IDENTITY CASCADE;
