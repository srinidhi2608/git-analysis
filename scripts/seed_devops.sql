-- =============================================================================
-- seed_devops.sql
-- Demo Azure DevOps data (Azure Boards work items + Azure Pipelines runs) for
-- the DevOps Integration page. Run AFTER seed_data.sql (it links to those PRs).
-- Dates are relative to now(), so the data always falls in the last ~6 weeks.
-- Safe to re-run: rows are replaced.
--
-- To map these Azure identities to the sample GitHub developers, add to
-- config.yaml (see README "DevOps Integration"):
--   teams:
--     Backend:
--       - { name: alice_dev, azure: alice@acme.dev, role: developer }
--       - { name: bob_dev,   azure: bob@acme.dev,   role: developer }
--       - { name: tom_qa,    azure: tom@acme.dev,   role: tester }
--     Frontend:
--       - { name: carol_dev, azure: carol@acme.dev, role: developer }
-- =============================================================================

DELETE FROM azure_work_item_tags WHERE work_item_id BETWEEN 1000 AND 1999;
DELETE FROM azure_work_items WHERE id BETWEEN 1000 AND 1999;
DELETE FROM azure_pipeline_runs WHERE project = 'Platform' AND build_id BETWEEN 5000 AND 5999;

-- ─── Work items ──────────────────────────────────────────────────────────────
-- (id, type, title, assignee email, assignee name, creator email, points,
--  created days ago, activated days ago, closed days ago (NULL = open), tags)
WITH seed (id, wtype, title, email, name, creator, points, created_ago, activated_ago, closed_ago, tags) AS (
  VALUES
  (1001, 'User Story', 'Add user authentication service',        'alice@acme.dev', 'Alice Dev', 'pm@acme.dev', 5, 40, 33, 30, 'auth;release-2026.10'),
  (1002, 'User Story', 'Refactor DB connection pooling',         'alice@acme.dev', 'Alice Dev', 'pm@acme.dev', 3, 38, 31, 28, 'tech-debt'),
  (1003, 'User Story', 'Fix null-pointer in payment handler',    'alice@acme.dev', 'Alice Dev', 'pm@acme.dev', 2, 30, 26, 25, 'payments;checkout'),
  (1004, 'User Story', 'End-to-end tests for checkout flow',     'alice@acme.dev', 'Alice Dev', 'pm@acme.dev', 5, 28, 22, 20, 'checkout;testing'),
  (1005, 'User Story', 'Optimise product-search query',          'alice@acme.dev', 'Alice Dev', 'pm@acme.dev', 3, 22, 18, 16, 'performance'),
  (1006, 'User Story', 'Implement order state machine',          'bob@acme.dev',   'Bob Dev',   'pm@acme.dev', 8, 35, 29, 24, 'checkout;release-2026.10'),
  (1007, 'User Story', 'Add Prometheus metrics endpoint',        'bob@acme.dev',   'Bob Dev',   'pm@acme.dev', 3, 25, 19, 17, 'observability'),
  (1008, 'User Story', 'Update gRPC protobuf definitions',       'bob@acme.dev',   'Bob Dev',   'pm@acme.dev', 5, 20, 14, 10, 'payments;release-2026.10'),
  (1009, 'User Story', 'Redesign product listing component',     'carol@acme.dev', 'Carol Dev', 'pm@acme.dev', 5, 27, 21, 15, 'ui;release-2026.10'),
  (1010, 'User Story', 'Migrate CSS modules to Tailwind',        'carol@acme.dev', 'Carol Dev', 'pm@acme.dev', 8, 24, 16, 8,  'ui;tech-debt'),
  (1011, 'User Story', 'Saved payment methods',                  'bob@acme.dev',   'Bob Dev',   'pm@acme.dev', 5, 12, 7,  4,  'payments;checkout'),
  (1012, 'User Story', 'Dark mode for account settings',         'carol@acme.dev', 'Carol Dev', 'pm@acme.dev', 3, 10, 6,  2,  'ui'),
  (1013, 'User Story', 'Rate limiting for public API',           'alice@acme.dev', 'Alice Dev', 'pm@acme.dev', 5, 9,  5,  NULL, 'auth;performance'),
  (1014, 'User Story', 'Order export to CSV',                    'bob@acme.dev',   'Bob Dev',   'pm@acme.dev', 3, 6,  3,  NULL, 'checkout'),
  (1015, 'Task',       'Regression test pass for checkout',      'tom@acme.dev',   'Tom QA',    'tom@acme.dev', NULL, 14, 12, 9, 'testing;checkout'),
  (1016, 'Task',       'Exploratory testing of payments',        'tom@acme.dev',   'Tom QA',    'tom@acme.dev', NULL, 8,  6,  3, 'testing;payments'),
  (1017, 'Task',       'Automate login smoke tests',             'tom@acme.dev',   'Tom QA',    'tom@acme.dev', NULL, 18, 15, 11, 'testing;auth'),
  (1018, 'Bug',        'Coupon applied twice on retry',          'bob@acme.dev',   'Bob Dev',   'tom@acme.dev', 2, 16, 15, 13, 'checkout'),
  (1019, 'Bug',        'Listing images shift on load',           'carol@acme.dev', 'Carol Dev', 'tom@acme.dev', 1, 13, 12, 11, 'ui'),
  (1020, 'Bug',        'Session expires during payment',         'alice@acme.dev', 'Alice Dev', 'tom@acme.dev', 3, 7,  6,  5,  'payments;auth'),
  (1021, 'Bug',        'Checkout 500 errors after deploy',       'bob@acme.dev',   'Bob Dev',   'tom@acme.dev', 3, 19, 19, 18, 'Incident;checkout'),
  (1022, 'Bug',        'Payment provider timeout in prod',       'alice@acme.dev', 'Alice Dev', 'tom@acme.dev', 2, 6,  6,  5,  'Incident;payments')
)
INSERT INTO azure_work_items (
  id, project, work_item_type, title, state, assigned_to, assigned_to_email, created_by, created_by_email,
  created_date, activated_date, resolved_date, closed_date, changed_date, story_points,
  iteration_path, area_path, url
)
SELECT
  s.id, 'Platform', s.wtype, s.title,
  CASE WHEN s.closed_ago IS NULL THEN 'Active' ELSE 'Closed' END,
  s.name, s.email, NULL, s.creator,
  date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(days => s.created_ago),
  date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(days => s.activated_ago) + interval '2 hours',
  CASE WHEN s.closed_ago IS NULL THEN NULL
       ELSE date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(days => s.closed_ago) - interval '2 hours' END,
  CASE WHEN s.closed_ago IS NULL THEN NULL
       ELSE date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(days => s.closed_ago) END,
  date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(days => COALESCE(s.closed_ago, 1)),
  s.points, 'Platform\Sprint 42', 'Platform',
  'https://dev.azure.com/acme/Platform/_workitems/edit/' || s.id
FROM seed s;

INSERT INTO azure_work_item_tags (work_item_id, tag)
SELECT w.id, trim(t.tag)
FROM (
  VALUES
  (1001, 'auth;release-2026.10'), (1002, 'tech-debt'), (1003, 'payments;checkout'), (1004, 'checkout;testing'),
  (1005, 'performance'), (1006, 'checkout;release-2026.10'), (1007, 'observability'), (1008, 'payments;release-2026.10'),
  (1009, 'ui;release-2026.10'), (1010, 'ui;tech-debt'), (1011, 'payments;checkout'), (1012, 'ui'),
  (1013, 'auth;performance'), (1014, 'checkout'), (1015, 'testing;checkout'), (1016, 'testing;payments'),
  (1017, 'testing;auth'), (1018, 'checkout'), (1019, 'ui'), (1020, 'payments;auth'),
  (1021, 'Incident;checkout'), (1022, 'Incident;payments')
) AS w (id, tags)
CROSS JOIN LATERAL unnest(string_to_array(w.tags, ';')) AS t (tag);

-- ─── Link the sample GitHub PRs to stories (Azure Boards "AB#<id>" mentions) ──
UPDATE pull_requests pr
SET title = 'AB#' || (1000 + pr.pr_number) || ' ' || pr.title,
    merge_commit_sha = COALESCE(pr.merge_commit_sha, 'demo-merge-' || pr.pr_number)
FROM repositories r
WHERE pr.repo_id = r.id AND r.name = 'acme-corp/platform' AND pr.title NOT LIKE 'AB#%';

-- ─── Pipeline runs ───────────────────────────────────────────────────────────
-- Production deploys on weekdays over the last 42 days, a few failures,
-- plus staging deploys and CI builds (which are not deployments).
INSERT INTO azure_pipeline_runs (
  project, build_id, build_number, definition_id, definition_name, status, result, reason,
  source_branch, source_version, repository_name, repository_type, requested_for, requested_for_email,
  queue_time, start_time, finish_time, url
)
SELECT
  'Platform', 5000 + d, '2026.' || d, 10, 'Platform - Deploy Prod', 'completed',
  CASE WHEN d IN (5, 18, 33) THEN 'failed' ELSE 'succeeded' END,
  'individualCI', 'refs/heads/main', 'demo-deploy-' || d, 'acme-corp/platform', 'GitHub', 'Release Bot', 'release@acme.dev',
  day + interval '15 hours', day + interval '15 hours 5 minutes', day + interval '15 hours 25 minutes',
  'https://dev.azure.com/acme/Platform/_build/results?buildId=' || (5000 + d)
FROM (
  SELECT d, date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => d) AS day
  FROM generate_series(1, 42) AS d
) days
WHERE extract(isodow FROM day) < 6;

-- Recovery runs a few hours after each failed production deploy.
INSERT INTO azure_pipeline_runs (
  project, build_id, build_number, definition_id, definition_name, status, result, reason,
  source_branch, source_version, repository_name, repository_type, queue_time, start_time, finish_time
)
SELECT 'Platform', 5100 + d, '2026.' || d || '.1', 10, 'Platform - Deploy Prod', 'completed', 'succeeded', 'manual',
       'refs/heads/main', 'demo-fix-' || d, 'acme-corp/platform', 'GitHub',
       day + interval '18 hours', day + interval '18 hours 5 minutes', day + interval '18 hours 25 minutes'
FROM (
  SELECT d, date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => d) AS day
  FROM generate_series(1, 42) AS d
) days
WHERE d IN (5, 18, 33) AND extract(isodow FROM day) < 6;

INSERT INTO azure_pipeline_runs (
  project, build_id, build_number, definition_id, definition_name, status, result, reason,
  source_branch, source_version, repository_name, repository_type, queue_time, start_time, finish_time
)
SELECT 'Platform', 5200 + d, 'ci.' || d, 11,
       CASE WHEN d % 2 = 0 THEN 'Platform - Deploy Staging' ELSE 'Platform - CI Build' END,
       'completed', CASE WHEN d % 7 = 0 THEN 'failed' ELSE 'succeeded' END, 'pullRequest',
       'refs/pull/' || d || '/merge', 'demo-ci-' || d, 'acme-corp/platform', 'GitHub',
       day + interval '10 hours', day + interval '10 hours 2 minutes', day + interval '10 hours 12 minutes'
FROM (
  SELECT d, date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => d) AS day
  FROM generate_series(1, 42) AS d
) days;
