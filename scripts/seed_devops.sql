-- =============================================================================
-- seed_devops.sql
-- Demo Azure DevOps data (Azure Boards work items + Azure Pipelines runs) for
-- the DevOps Integration and Story Quality pages. Run AFTER seed_data.sql (it
-- links to those PRs).
-- Dates are relative to now(), so the data always falls in the last ~6 weeks.
-- Safe to re-run: rows are replaced.
--
-- To link these Azure DevOps identities to the sample GitHub logins (the names
-- differ, as they usually do), add to config.yaml (see README "DevOps Integration"):
--   teams:
--     Backend:
--       - name: "Alice Anderson"        # shown on every page
--         github: "alice_dev"           # GitHub login
--         azure_devops: "alice@acme.dev"
--         role: developer
--       - name: "Bob Brown"
--         github: "bob_dev"
--         azure_devops: "bob@acme.dev"
--         role: developer
--       - name: "Tom QA"                # tester without a GitHub account
--         azure_devops: "tom@acme.dev"
--         role: tester
--     Frontend:
--       - name: "Carol Chen"
--         github: "carol_dev"
--         azure_devops: "carol@acme.dev"
--         role: developer
-- =============================================================================

DELETE FROM azure_work_item_state_changes WHERE work_item_id BETWEEN 1000 AND 1999;
DELETE FROM azure_work_item_relations WHERE work_item_id BETWEEN 1000 AND 1999;
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
WHERE pr.repo_id = r.id AND r.name = 'acme-corp/platform' AND pr.pr_number <= 10 AND pr.title NOT LIKE 'AB#%';

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

-- Runs people started themselves (manual hotfix deploys, builds of their own
-- pushes) from an Azure Repos mirror named just "platform". Filtering the
-- DevOps page by a person includes them through "Requested for".
INSERT INTO azure_pipeline_runs (
  project, build_id, build_number, definition_id, definition_name, status, result, reason,
  source_branch, source_version, repository_name, repository_type, requested_for, requested_for_email,
  queue_time, start_time, finish_time, url
)
SELECT 'Platform', 5300 + n, 'person.' || n,
       CASE WHEN n % 3 = 0 THEN 12 ELSE 13 END,
       CASE WHEN n % 3 = 0 THEN 'Platform - Deploy Prod Hotfix' ELSE 'Platform - Feature Build' END,
       'completed', CASE WHEN n % 4 = 0 THEN 'failed' ELSE 'succeeded' END,
       CASE WHEN n % 3 = 0 THEN 'manual' ELSE 'individualCI' END,
       'refs/heads/feature/demo-' || n, 'demo-person-' || n, 'platform', 'TfsGit',
       (ARRAY['Alice Anderson', 'Bob Brown', 'Carol Chen', 'Tom QA'])[1 + n % 4],
       (ARRAY['alice@acme.dev', 'bob@acme.dev', 'carol@acme.dev', 'tom@acme.dev'])[1 + n % 4],
       day + interval '12 hours', day + interval '12 hours 3 minutes', day + interval '12 hours 20 minutes',
       'https://dev.azure.com/acme/Platform/_build/results?buildId=' || (5300 + n)
FROM (
  SELECT n, date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => n * 3) AS day
  FROM generate_series(1, 12) AS n
) runs;


-- ─── Story lifecycle (Story Quality page) ────────────────────────────────────
-- State histories in the default workflow (New → Active → QA Dev In Progress →
-- QA Dev Complete → QAA In Progress → QAA Complete → INT → Released / Closed),
-- with a QA bounce, a reopened story, a release without testing stages, a story
-- closed without development, and two stories still in progress.
-- (work item, revision, hours ago, changed by, email, from state, state, assignee, email)
INSERT INTO azure_work_item_state_changes (
  work_item_id, rev, changed_at, changed_by, changed_by_email, from_state, state, assigned_to, assigned_to_email
)
SELECT h.id, h.rev, date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(hours => h.hours_ago),
       h.by_name, h.by_email, h.from_state, h.state, h.assignee, h.assignee_email
FROM (VALUES
  (1001, 1, 960, 'Alice Dev', 'alice@acme.dev', NULL, 'New', 'Alice Dev', 'alice@acme.dev'),
  (1001, 2, 790, 'Alice Dev', 'alice@acme.dev', 'New', 'Active', 'Alice Dev', 'alice@acme.dev'),
  (1001, 3, 778, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1001, 4, 767, 'Tom QA', 'tom@acme.dev', 'QA Dev In Progress', 'QA Dev Complete', 'Tom QA', 'tom@acme.dev'),
  (1001, 5, 755, 'Tom QA', 'tom@acme.dev', 'QA Dev Complete', 'QAA In Progress', 'Tom QA', 'tom@acme.dev'),
  (1001, 6, 743, 'Tom QA', 'tom@acme.dev', 'QAA In Progress', 'QAA Complete', 'Tom QA', 'tom@acme.dev'),
  (1001, 7, 732, 'Tom QA', 'tom@acme.dev', 'QAA Complete', 'INT', 'Tom QA', 'tom@acme.dev'),
  (1001, 8, 720, 'Alice Dev', 'alice@acme.dev', 'INT', 'Released', 'Alice Dev', 'alice@acme.dev'),
  (1002, 1, 912, 'Alice Dev', 'alice@acme.dev', NULL, 'New', 'Alice Dev', 'alice@acme.dev'),
  (1002, 2, 742, 'Alice Dev', 'alice@acme.dev', 'New', 'Active', 'Alice Dev', 'alice@acme.dev'),
  (1002, 3, 672, 'Alice Dev', 'alice@acme.dev', 'Active', 'Closed', 'Alice Dev', 'alice@acme.dev'),
  (1003, 1, 720, 'Alice Dev', 'alice@acme.dev', NULL, 'New', 'Alice Dev', 'alice@acme.dev'),
  (1003, 2, 622, 'Alice Dev', 'alice@acme.dev', 'New', 'Active', 'Alice Dev', 'alice@acme.dev'),
  (1003, 3, 619, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1003, 4, 616, 'Alice Dev', 'alice@acme.dev', 'QA Dev In Progress', 'Active', 'Alice Dev', 'alice@acme.dev'),
  (1003, 5, 614, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1003, 6, 611, 'Tom QA', 'tom@acme.dev', 'QA Dev In Progress', 'QA Dev Complete', 'Tom QA', 'tom@acme.dev'),
  (1003, 7, 608, 'Tom QA', 'tom@acme.dev', 'QA Dev Complete', 'QAA In Progress', 'Tom QA', 'tom@acme.dev'),
  (1003, 8, 606, 'Tom QA', 'tom@acme.dev', 'QAA In Progress', 'QAA Complete', 'Tom QA', 'tom@acme.dev'),
  (1003, 9, 603, 'Tom QA', 'tom@acme.dev', 'QAA Complete', 'INT', 'Tom QA', 'tom@acme.dev'),
  (1003, 10, 600, 'Alice Dev', 'alice@acme.dev', 'INT', 'Released', 'Alice Dev', 'alice@acme.dev'),
  (1004, 1, 672, 'Alice Dev', 'alice@acme.dev', NULL, 'New', 'Alice Dev', 'alice@acme.dev'),
  (1004, 2, 526, 'Alice Dev', 'alice@acme.dev', 'New', 'Active', 'Alice Dev', 'alice@acme.dev'),
  (1004, 3, 518, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1004, 4, 511, 'Tom QA', 'tom@acme.dev', 'QA Dev In Progress', 'QA Dev Complete', 'Tom QA', 'tom@acme.dev'),
  (1004, 5, 503, 'Tom QA', 'tom@acme.dev', 'QA Dev Complete', 'QAA In Progress', 'Tom QA', 'tom@acme.dev'),
  (1004, 6, 495, 'Tom QA', 'tom@acme.dev', 'QAA In Progress', 'QAA Complete', 'Tom QA', 'tom@acme.dev'),
  (1004, 7, 488, 'Tom QA', 'tom@acme.dev', 'QAA Complete', 'INT', 'Tom QA', 'tom@acme.dev'),
  (1004, 8, 480, 'Alice Dev', 'alice@acme.dev', 'INT', 'Released', 'Alice Dev', 'alice@acme.dev'),
  (1005, 1, 528, 'Alice Dev', 'alice@acme.dev', NULL, 'New', 'Alice Dev', 'alice@acme.dev'),
  (1005, 2, 430, 'Alice Dev', 'alice@acme.dev', 'New', 'Active', 'Alice Dev', 'alice@acme.dev'),
  (1005, 3, 384, 'Alice Dev', 'alice@acme.dev', 'Active', 'Released', 'Alice Dev', 'alice@acme.dev'),
  (1006, 1, 840, 'Bob Dev', 'bob@acme.dev', NULL, 'New', 'Bob Dev', 'bob@acme.dev'),
  (1006, 2, 694, 'Bob Dev', 'bob@acme.dev', 'New', 'Active', 'Bob Dev', 'bob@acme.dev'),
  (1006, 3, 674, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1006, 4, 655, 'Tom QA', 'tom@acme.dev', 'QA Dev In Progress', 'QA Dev Complete', 'Tom QA', 'tom@acme.dev'),
  (1006, 5, 635, 'Tom QA', 'tom@acme.dev', 'QA Dev Complete', 'QAA In Progress', 'Tom QA', 'tom@acme.dev'),
  (1006, 6, 615, 'Tom QA', 'tom@acme.dev', 'QAA In Progress', 'QAA Complete', 'Tom QA', 'tom@acme.dev'),
  (1006, 7, 596, 'Tom QA', 'tom@acme.dev', 'QAA Complete', 'INT', 'Tom QA', 'tom@acme.dev'),
  (1006, 8, 576, 'Bob Dev', 'bob@acme.dev', 'INT', 'Released', 'Bob Dev', 'bob@acme.dev'),
  (1006, 9, 556, 'Bob Dev', 'bob@acme.dev', 'Released', 'Active', 'Bob Dev', 'bob@acme.dev'),
  (1006, 10, 536, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1006, 11, 526, 'Tom QA', 'tom@acme.dev', 'QA Dev In Progress', 'QA Dev Complete', 'Tom QA', 'tom@acme.dev'),
  (1006, 12, 516, 'Tom QA', 'tom@acme.dev', 'QA Dev Complete', 'QAA In Progress', 'Tom QA', 'tom@acme.dev'),
  (1006, 13, 506, 'Tom QA', 'tom@acme.dev', 'QAA In Progress', 'QAA Complete', 'Tom QA', 'tom@acme.dev'),
  (1006, 14, 496, 'Tom QA', 'tom@acme.dev', 'QAA Complete', 'INT', 'Tom QA', 'tom@acme.dev'),
  (1006, 15, 480, 'Bob Dev', 'bob@acme.dev', 'INT', 'Released', 'Bob Dev', 'bob@acme.dev'),
  (1007, 1, 600, 'Bob Dev', 'bob@acme.dev', NULL, 'New', 'Bob Dev', 'bob@acme.dev'),
  (1007, 2, 408, 'Bob Dev', 'bob@acme.dev', 'New', 'Closed', 'Bob Dev', 'bob@acme.dev'),
  (1008, 1, 480, 'Bob Dev', 'bob@acme.dev', NULL, 'New', 'Bob Dev', 'bob@acme.dev'),
  (1008, 2, 334, 'Bob Dev', 'bob@acme.dev', 'New', 'Active', 'Bob Dev', 'bob@acme.dev'),
  (1008, 3, 318, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1008, 4, 303, 'Tom QA', 'tom@acme.dev', 'QA Dev In Progress', 'QA Dev Complete', 'Tom QA', 'tom@acme.dev'),
  (1008, 5, 287, 'Tom QA', 'tom@acme.dev', 'QA Dev Complete', 'QAA In Progress', 'Tom QA', 'tom@acme.dev'),
  (1008, 6, 271, 'Tom QA', 'tom@acme.dev', 'QAA In Progress', 'QAA Complete', 'Tom QA', 'tom@acme.dev'),
  (1008, 7, 256, 'Tom QA', 'tom@acme.dev', 'QAA Complete', 'INT', 'Tom QA', 'tom@acme.dev'),
  (1008, 8, 240, 'Bob Dev', 'bob@acme.dev', 'INT', 'Released', 'Bob Dev', 'bob@acme.dev'),
  (1009, 1, 648, 'Carol Dev', 'carol@acme.dev', NULL, 'New', 'Carol Dev', 'carol@acme.dev'),
  (1009, 2, 502, 'Carol Dev', 'carol@acme.dev', 'New', 'Active', 'Carol Dev', 'carol@acme.dev'),
  (1009, 3, 484, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1009, 4, 466, 'Carol Dev', 'carol@acme.dev', 'QA Dev In Progress', 'Active', 'Carol Dev', 'carol@acme.dev'),
  (1009, 5, 449, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1009, 6, 431, 'Tom QA', 'tom@acme.dev', 'QA Dev In Progress', 'QA Dev Complete', 'Tom QA', 'tom@acme.dev'),
  (1009, 7, 413, 'Tom QA', 'tom@acme.dev', 'QA Dev Complete', 'QAA In Progress', 'Tom QA', 'tom@acme.dev'),
  (1009, 8, 396, 'Tom QA', 'tom@acme.dev', 'QAA In Progress', 'QAA Complete', 'Tom QA', 'tom@acme.dev'),
  (1009, 9, 378, 'Tom QA', 'tom@acme.dev', 'QAA Complete', 'INT', 'Tom QA', 'tom@acme.dev'),
  (1009, 10, 360, 'Carol Dev', 'carol@acme.dev', 'INT', 'Released', 'Carol Dev', 'carol@acme.dev'),
  (1010, 1, 576, 'Carol Dev', 'carol@acme.dev', NULL, 'New', 'Carol Dev', 'carol@acme.dev'),
  (1010, 2, 382, 'Carol Dev', 'carol@acme.dev', 'New', 'Active', 'Carol Dev', 'carol@acme.dev'),
  (1010, 3, 350, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1010, 4, 319, 'Tom QA', 'tom@acme.dev', 'QA Dev In Progress', 'QA Dev Complete', 'Tom QA', 'tom@acme.dev'),
  (1010, 5, 287, 'Tom QA', 'tom@acme.dev', 'QA Dev Complete', 'QAA In Progress', 'Tom QA', 'tom@acme.dev'),
  (1010, 6, 255, 'Tom QA', 'tom@acme.dev', 'QAA In Progress', 'QAA Complete', 'Tom QA', 'tom@acme.dev'),
  (1010, 7, 224, 'Tom QA', 'tom@acme.dev', 'QAA Complete', 'INT', 'Tom QA', 'tom@acme.dev'),
  (1010, 8, 192, 'Carol Dev', 'carol@acme.dev', 'INT', 'Released', 'Carol Dev', 'carol@acme.dev'),
  (1011, 1, 288, 'Bob Dev', 'bob@acme.dev', NULL, 'New', 'Bob Dev', 'bob@acme.dev'),
  (1011, 2, 166, 'Bob Dev', 'bob@acme.dev', 'New', 'Active', 'Bob Dev', 'bob@acme.dev'),
  (1011, 3, 154, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1011, 4, 143, 'Tom QA', 'tom@acme.dev', 'QA Dev In Progress', 'QA Dev Complete', 'Tom QA', 'tom@acme.dev'),
  (1011, 5, 131, 'Tom QA', 'tom@acme.dev', 'QA Dev Complete', 'QAA In Progress', 'Tom QA', 'tom@acme.dev'),
  (1011, 6, 119, 'Tom QA', 'tom@acme.dev', 'QAA In Progress', 'QAA Complete', 'Tom QA', 'tom@acme.dev'),
  (1011, 7, 108, 'Tom QA', 'tom@acme.dev', 'QAA Complete', 'INT', 'Tom QA', 'tom@acme.dev'),
  (1011, 8, 96, 'Bob Dev', 'bob@acme.dev', 'INT', 'Released', 'Bob Dev', 'bob@acme.dev'),
  (1012, 1, 240, 'Carol Dev', 'carol@acme.dev', NULL, 'New', 'Carol Dev', 'carol@acme.dev'),
  (1012, 2, 142, 'Carol Dev', 'carol@acme.dev', 'New', 'Active', 'Carol Dev', 'carol@acme.dev'),
  (1012, 3, 126, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1012, 4, 111, 'Tom QA', 'tom@acme.dev', 'QA Dev In Progress', 'QA Dev Complete', 'Tom QA', 'tom@acme.dev'),
  (1012, 5, 95, 'Tom QA', 'tom@acme.dev', 'QA Dev Complete', 'QAA In Progress', 'Tom QA', 'tom@acme.dev'),
  (1012, 6, 79, 'Tom QA', 'tom@acme.dev', 'QAA In Progress', 'QAA Complete', 'Tom QA', 'tom@acme.dev'),
  (1012, 7, 64, 'Tom QA', 'tom@acme.dev', 'QAA Complete', 'INT', 'Tom QA', 'tom@acme.dev'),
  (1012, 8, 48, 'Carol Dev', 'carol@acme.dev', 'INT', 'Released', 'Carol Dev', 'carol@acme.dev'),
  (1013, 1, 216, 'Alice Dev', 'alice@acme.dev', NULL, 'New', 'Alice Dev', 'alice@acme.dev'),
  (1013, 2, 118, 'Alice Dev', 'alice@acme.dev', 'New', 'Active', 'Alice Dev', 'alice@acme.dev'),
  (1013, 3, 30, 'Tom QA', 'tom@acme.dev', 'Active', 'QA Dev In Progress', 'Tom QA', 'tom@acme.dev'),
  (1014, 1, 144, 'Bob Dev', 'bob@acme.dev', NULL, 'New', 'Bob Dev', 'bob@acme.dev'),
  (1014, 2, 70, 'Bob Dev', 'bob@acme.dev', 'New', 'Active', 'Bob Dev', 'bob@acme.dev')
) AS h (id, rev, hours_ago, by_name, by_email, from_state, state, assignee, assignee_email);

-- Current state = last state in the history; history is up to date.
UPDATE azure_work_items w
SET state = f.state,
    changed_date = date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(hours => f.hours_ago),
    history_changed_date = date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(hours => f.hours_ago)
FROM (VALUES
  (1001, 'Released', 720),
  (1002, 'Closed', 672),
  (1003, 'Released', 600),
  (1004, 'Released', 480),
  (1005, 'Released', 384),
  (1006, 'Released', 480),
  (1007, 'Closed', 408),
  (1008, 'Released', 240),
  (1009, 'Released', 360),
  (1010, 'Released', 192),
  (1011, 'Released', 96),
  (1012, 'Released', 48),
  (1013, 'QA Dev In Progress', 30),
  (1014, 'Active', 70)
) AS f (id, state, hours_ago)
WHERE w.id = f.id;

-- Release tags ("Release <version>") on released stories; 1005 and 1012 are
-- released without one, so they show the "Released without a Release tag" flag.
INSERT INTO azure_work_item_tags (work_item_id, tag)
VALUES (1001, 'Release 2026.10.1'), (1003, 'Release 2026.10.1'), (1004, 'Release 2026.10.1'),
       (1006, 'Release 2026.10.1'), (1008, 'Release 2026.10.2'), (1009, 'Release 2026.10.1'),
       (1010, 'Release 2026.10.2'), (1011, 'Release 2026.10.2');

-- Child items roll up to their story; bug 1019 is related to story 1009 and was
-- raised after its release (an escaped defect).
UPDATE azure_work_items SET parent_id = 1011 WHERE id IN (1015, 1016);
UPDATE azure_work_items SET parent_id = 1001 WHERE id = 1017;
UPDATE azure_work_items SET parent_id = 1006 WHERE id = 1018;
INSERT INTO azure_work_item_relations (work_item_id, rel, name, url, target_id)
VALUES (1009, 'System.LinkTypes.Related', 'Related', 'https://dev.azure.com/acme/_apis/wit/workItems/1019', 1019);

-- PR states, plus three PRs linked in other ways: by branch name (1011, 1012)
-- and an open follow-up PR on story 1011 ("Open PR when finished").
UPDATE pull_requests pr
SET state = CASE WHEN pr.merged_at IS NOT NULL THEN 'MERGED' ELSE COALESCE(pr.state, 'OPEN') END
FROM repositories r
WHERE pr.repo_id = r.id AND r.name = 'acme-corp/platform';

INSERT INTO pull_requests (
  repo_id, developer_id, pr_number, title, created_at, merged_at, cycle_time_minutes,
  review_comments_count, commit_count, changed_files, additions, deletions,
  review_count, reviewers_count, approvals_count, requested_changes_count,
  base_branch, head_branch, state
)
SELECT r.id, d.id, s.num, s.title,
       date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(hours => s.created_ago),
       CASE WHEN s.merged_ago IS NULL THEN NULL
            ELSE date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(hours => s.merged_ago) END,
       CASE WHEN s.merged_ago IS NULL THEN NULL ELSE (s.created_ago - s.merged_ago) * 60.0 END,
       s.comments, 2, 4, s.additions, s.deletions, 1, 1, 1, 0,
       'main', s.branch, s.state
FROM repositories r,
(VALUES
  ('bob_dev',   11, 'Saved payment methods UI',            'feature/1011-saved-cards',  150, 110, 3, 260, 40, 'MERGED'),
  ('bob_dev',   12, 'AB#1011 Saved cards: retry on decline', 'feature/1011-card-retry',  90, NULL, 1,  60, 10, 'OPEN'),
  ('carol_dev', 13, 'Dark mode tokens',                    'feature/1012-dark-mode',    130,  80, 2, 180, 90, 'MERGED')
) AS s (developer, num, title, branch, created_ago, merged_ago, comments, additions, deletions, state)
JOIN developers d ON d.github_username = s.developer
WHERE r.name = 'acme-corp/platform'
  AND NOT EXISTS (SELECT 1 FROM pull_requests ep WHERE ep.repo_id = r.id AND ep.pr_number = s.num);
