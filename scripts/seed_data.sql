-- =============================================================================
-- seed_data.sql
-- Sample data for local development / demos.
-- Idempotent: run clear_data.sql first for a completely clean re-seed, or
-- run this script standalone — it skips any row that already exists.
-- Covers 3 developers, 1 shared repository, 10 pull requests, commits, and
-- review comments (multiple per PR).
-- =============================================================================

-- ─── Developers ──────────────────────────────────────────────────────────────
INSERT INTO developers (github_username, team_name) VALUES
  ('alice_dev',  'Backend'),
  ('bob_dev',    'Backend'),
  ('carol_dev',  'Frontend')
ON CONFLICT (github_username) DO UPDATE
  SET team_name = EXCLUDED.team_name;

-- ─── Repository ──────────────────────────────────────────────────────────────
INSERT INTO repositories (name, is_active) VALUES
  ('acme-corp/platform', true)
ON CONFLICT (name) DO UPDATE
  SET is_active = EXCLUDED.is_active;

-- ─── Pull Requests ───────────────────────────────────────────────────────────
-- Uses NOT EXISTS so it is safe to run multiple times even without a
-- unique constraint on (developer_id, pr_number).

INSERT INTO pull_requests (
  repo_id, developer_id, pr_number, title,
  created_at, merged_at, cycle_time_minutes,
  review_comments_count, commit_count, changed_files,
  additions, deletions,
  review_count, reviewers_count, approvals_count, requested_changes_count,
  review_decision, first_review_comment_at, last_review_comment_at
)
SELECT
  r.id, d.id,
  s.pr_number::int, s.title,
  s.created_at::timestamp, s.merged_at::timestamp, s.cycle_mins::float,
  s.rev_comments::int, s.commits::int, s.files::int,
  s.additions::int, s.deletions::int,
  s.rev_count::int, s.rev_ors::int, s.approvals::int, s.req_changes::int,
  s.review_decision,
  s.first_rev_at::timestamp, s.last_rev_at::timestamp
FROM repositories r,
(VALUES
  ('alice_dev',  1,  'Add user authentication service',          '2024-12-01 09:00:00','2024-12-01 15:30:00', 390,  4, 3, 6, 240, 80,  2, 2, 1, 1,'APPROVED','2024-12-01 11:00:00','2024-12-01 13:00:00'),
  ('alice_dev',  2,  'Refactor DB connection pooling',           '2024-12-03 10:00:00','2024-12-04 09:00:00',1380,  6, 4, 4, 180, 60,  2, 2, 1, 1,'APPROVED','2024-12-03 14:00:00','2024-12-03 16:00:00'),
  ('alice_dev',  3,  'Fix null-pointer in payment handler',      '2024-12-06 08:00:00','2024-12-06 14:00:00', 360,  2, 2, 2,  40, 10,  1, 1, 1, 0,'APPROVED','2024-12-06 10:00:00','2024-12-06 11:00:00'),
  ('alice_dev',  4,  'Add end-to-end tests for checkout flow',   '2024-12-10 09:00:00','2024-12-11 10:00:00',1500,  8, 5, 9, 520, 90,  3, 3, 2, 2,'APPROVED','2024-12-10 12:00:00','2024-12-10 17:00:00'),
  ('alice_dev',  5,  'Optimise product-search query (N+1 fix)',  '2024-12-13 09:00:00','2024-12-13 16:00:00', 420,  3, 2, 3, 110, 50,  1, 1, 1, 0,'APPROVED','2024-12-13 11:00:00','2024-12-13 13:00:00'),
  ('bob_dev',    6,  'Implement order state machine',            '2024-12-02 10:00:00','2024-12-02 18:00:00', 480,  5, 3, 7, 310, 70,  2, 2, 1, 1,'APPROVED','2024-12-02 13:00:00','2024-12-02 15:00:00'),
  ('bob_dev',    7,  'Add Prometheus metrics endpoint',          '2024-12-05 09:00:00','2024-12-05 15:00:00', 360,  3, 2, 5, 140, 30,  2, 2, 2, 0,'APPROVED','2024-12-05 11:00:00','2024-12-05 12:00:00'),
  ('bob_dev',    8,  'Update gRPC protobuf definitions',         '2024-12-09 11:00:00','2024-12-10 09:00:00',1320,  7, 4, 8, 430,120,  3, 3, 2, 2,'APPROVED','2024-12-09 14:00:00','2024-12-09 18:00:00'),
  ('carol_dev',  9,  'Redesign product listing component',       '2024-12-04 09:00:00','2024-12-04 17:00:00', 480,  6, 3,10, 650,200,  2, 2, 1, 1,'APPROVED','2024-12-04 12:00:00','2024-12-04 14:00:00'),
  ('carol_dev', 10,  'Migrate CSS modules to Tailwind',          '2024-12-08 10:00:00','2024-12-09 10:00:00',1440,  4, 2,15, 820,460,  2, 2, 1, 0,'APPROVED','2024-12-08 14:00:00','2024-12-08 16:00:00')
) AS s(developer, pr_number, title, created_at, merged_at, cycle_mins,
       rev_comments, commits, files, additions, deletions,
       rev_count, rev_ors, approvals, req_changes,
       review_decision, first_rev_at, last_rev_at)
JOIN developers d ON d.github_username = s.developer
WHERE r.name = 'acme-corp/platform'
  AND NOT EXISTS (
    SELECT 1 FROM pull_requests ep
    WHERE ep.developer_id = d.id
      AND ep.pr_number = s.pr_number::int
  );

-- ─── Commits ─────────────────────────────────────────────────────────────────
WITH commits_seed (developer, pr_number, hash, message, committed_at) AS (
  VALUES
  ('alice_dev',  1, 'aaa00001', 'feat: add JWT middleware',             '2024-12-01 09:30:00'),
  ('alice_dev',  1, 'aaa00002', 'fix: token expiry edge case',          '2024-12-01 12:00:00'),
  ('alice_dev',  1, 'aaa00003', 'chore: update tests',                  '2024-12-01 13:30:00'),
  ('alice_dev',  2, 'aaa00004', 'refactor: extract pool factory',       '2024-12-03 10:30:00'),
  ('alice_dev',  2, 'aaa00005', 'fix: handle timeout on acquire',       '2024-12-03 14:30:00'),
  ('alice_dev',  2, 'aaa00006', 'refactor: simplify health check',      '2024-12-03 16:30:00'),
  ('alice_dev',  2, 'aaa00007', 'test: pool under load',                '2024-12-03 17:00:00'),
  ('alice_dev',  3, 'aaa00008', 'fix: null check before dereference',   '2024-12-06 08:30:00'),
  ('alice_dev',  3, 'aaa00009', 'test: add edge-case payment test',     '2024-12-06 11:30:00'),
  ('alice_dev',  4, 'aaa00010', 'test: stub external gateway',          '2024-12-10 09:30:00'),
  ('alice_dev',  4, 'aaa00011', 'test: happy-path checkout',            '2024-12-10 13:00:00'),
  ('alice_dev',  4, 'aaa00012', 'test: failure scenarios',              '2024-12-10 16:00:00'),
  ('alice_dev',  4, 'aaa00013', 'fix: address review nits',             '2024-12-10 17:30:00'),
  ('alice_dev',  4, 'aaa00014', 'test: add retry assertions',           '2024-12-10 18:00:00'),
  ('alice_dev',  5, 'aaa00015', 'perf: eager-load associated records',  '2024-12-13 09:30:00'),
  ('alice_dev',  5, 'aaa00016', 'perf: add composite index',            '2024-12-13 13:30:00'),
  ('bob_dev',    6, 'bbb00001', 'feat: initial state machine scaffold', '2024-12-02 10:30:00'),
  ('bob_dev',    6, 'bbb00002', 'feat: add CANCELLED transition',       '2024-12-02 14:00:00'),
  ('bob_dev',    6, 'bbb00003', 'fix: guard invalid transitions',       '2024-12-02 15:30:00'),
  ('bob_dev',    7, 'bbb00004', 'feat: /metrics prometheus handler',    '2024-12-05 09:30:00'),
  ('bob_dev',    7, 'bbb00005', 'feat: expose p99 latency gauge',       '2024-12-05 12:30:00'),
  ('bob_dev',    8, 'bbb00006', 'proto: add OrderEvent message',        '2024-12-09 11:30:00'),
  ('bob_dev',    8, 'bbb00007', 'proto: update ShipmentStatus enum',    '2024-12-09 15:00:00'),
  ('bob_dev',    8, 'bbb00008', 'fix: backward-compat field numbering', '2024-12-09 17:00:00'),
  ('bob_dev',    8, 'bbb00009', 'chore: regenerate Go bindings',        '2024-12-09 18:30:00'),
  ('carol_dev',  9, 'ccc00001', 'feat: product card skeleton loader',   '2024-12-04 09:30:00'),
  ('carol_dev',  9, 'ccc00002', 'feat: infinite scroll pagination',     '2024-12-04 13:00:00'),
  ('carol_dev',  9, 'ccc00003', 'fix: layout shift on image load',      '2024-12-04 15:00:00'),
  ('carol_dev', 10, 'ccc00004', 'style: migrate ProductCard to TW',     '2024-12-08 10:30:00'),
  ('carol_dev', 10, 'ccc00005', 'style: migrate FilterSidebar to TW',   '2024-12-08 15:00:00')
)
INSERT INTO commits (pr_id, developer_id, commit_hash, message, committed_at)
SELECT
  p.id, d.id,
  cs.hash, cs.message, cs.committed_at::timestamp
FROM commits_seed cs
JOIN developers d ON d.github_username = cs.developer
JOIN pull_requests p ON p.pr_number = cs.pr_number::int AND p.developer_id = d.id
ON CONFLICT (commit_hash) DO NOTHING;

-- ─── Review Comments ─────────────────────────────────────────────────────────
WITH comments_seed (pr_number, pr_developer, external_id, comment_type, author_login, body, path, review_state, created_at) AS (
  VALUES
  -- PR 1 (alice_dev) — reviewed by bob_dev and carol_dev
  (1,'alice_dev','ext-c-001','review','bob_dev',  'Consider storing tokens in Redis for distributed revocation.',       'src/auth/middleware.py',            'COMMENTED',         '2024-12-01 11:00:00'),
  (1,'alice_dev','ext-c-002','review','carol_dev','Missing CSRF check for cookie-based flows.',                         'src/auth/middleware.py',            'CHANGES_REQUESTED', '2024-12-01 12:00:00'),
  (1,'alice_dev','ext-c-003','review','bob_dev',  'Token expiry logic looks correct now.',                              'src/auth/token.py',                 'APPROVED',          '2024-12-01 13:00:00'),
  (1,'alice_dev','ext-c-004','review','carol_dev','LGTM after CSRF fix.',                                               NULL,                                'APPROVED',          '2024-12-01 14:00:00'),
  -- PR 2 (alice_dev) — reviewed by bob_dev and carol_dev
  (2,'alice_dev','ext-c-005','review','bob_dev',  'Duplicate acquire logic in two branches — extract helper.',          'src/db/pool.py',                    'CHANGES_REQUESTED', '2024-12-03 14:00:00'),
  (2,'alice_dev','ext-c-006','review','carol_dev','Style: prefer context manager for connection lifecycle.',             'src/db/pool.py',                    'COMMENTED',         '2024-12-03 15:00:00'),
  (2,'alice_dev','ext-c-007','review','bob_dev',  'Good refactor, helper is much cleaner.',                             'src/db/pool.py',                    'COMMENTED',         '2024-12-03 16:30:00'),
  (2,'alice_dev','ext-c-008','review','carol_dev','Health check endpoint could return more detail.',                    'src/db/health.py',                  'COMMENTED',         '2024-12-03 16:45:00'),
  (2,'alice_dev','ext-c-009','review','bob_dev',  'LGTM',                                                               NULL,                                'APPROVED',          '2024-12-03 17:30:00'),
  (2,'alice_dev','ext-c-010','review','carol_dev','Approving — health check nit is optional.',                          NULL,                                'APPROVED',          '2024-12-03 17:45:00'),
  -- PR 3 (alice_dev) — reviewed by carol_dev
  (3,'alice_dev','ext-c-011','review','carol_dev','Good fix. Add a unit test for the null case please.',                'src/payments/handler.py',           'CHANGES_REQUESTED', '2024-12-06 10:00:00'),
  (3,'alice_dev','ext-c-012','review','carol_dev','Test looks solid. Approving.',                                       NULL,                                'APPROVED',          '2024-12-06 12:00:00'),
  -- PR 4 (alice_dev) — heavy review by bob_dev and carol_dev
  (4,'alice_dev','ext-c-013','review','bob_dev',  'External gateway should be stubbed, not hit live in tests.',         'tests/test_checkout.py',            'CHANGES_REQUESTED', '2024-12-10 12:00:00'),
  (4,'alice_dev','ext-c-014','review','carol_dev','Test naming is inconsistent — use snake_case throughout.',           'tests/test_checkout.py',            'COMMENTED',         '2024-12-10 12:30:00'),
  (4,'alice_dev','ext-c-015','review','bob_dev',  'Add coverage for retry-exhaustion path.',                            'tests/test_checkout.py',            'CHANGES_REQUESTED', '2024-12-10 13:00:00'),
  (4,'alice_dev','ext-c-016','review','carol_dev','Missing assertions after refund flow.',                              'tests/test_refund.py',              'COMMENTED',         '2024-12-10 14:00:00'),
  (4,'alice_dev','ext-c-017','review','bob_dev',  'Gateway stub and retry coverage look good now.',                     NULL,                                'APPROVED',          '2024-12-10 17:30:00'),
  (4,'alice_dev','ext-c-018','review','carol_dev','Style nits resolved. LGTM.',                                         NULL,                                'APPROVED',          '2024-12-10 18:00:00'),
  -- PR 5 (alice_dev) — reviewed by bob_dev
  (5,'alice_dev','ext-c-019','review','bob_dev',  'Great N+1 fix. Confirm index covers the WHERE clause.',              'src/products/search.py',            'COMMENTED',         '2024-12-13 11:00:00'),
  (5,'alice_dev','ext-c-020','review','bob_dev',  'Index confirmed. LGTM.',                                             NULL,                                'APPROVED',          '2024-12-13 13:30:00'),
  -- PR 6 (bob_dev) — reviewed by alice_dev and carol_dev
  (6,'bob_dev',  'ext-c-021','review','alice_dev','Guard the CANCELLED→SHIPPED transition — it should be invalid.',    'src/orders/state.py',               'CHANGES_REQUESTED', '2024-12-02 13:00:00'),
  (6,'bob_dev',  'ext-c-022','review','carol_dev','State names should be uppercase constants, not strings.',            'src/orders/state.py',               'COMMENTED',         '2024-12-02 14:00:00'),
  (6,'bob_dev',  'ext-c-023','review','alice_dev','Invalid transition is now properly guarded. Approving.',             NULL,                                'APPROVED',          '2024-12-02 15:30:00'),
  (6,'bob_dev',  'ext-c-024','review','carol_dev','Constants are cleaner. LGTM.',                                       NULL,                                'APPROVED',          '2024-12-02 16:00:00'),
  (6,'bob_dev',  'ext-c-025','review','alice_dev','Consider adding a diagram to the README for the state machine.',    'src/orders/state.py',               'COMMENTED',         '2024-12-02 14:30:00'),
  -- PR 7 (bob_dev) — reviewed by alice_dev and carol_dev
  (7,'bob_dev',  'ext-c-026','review','alice_dev','Expose the p99 latency gauge — p50 alone is not enough.',           'src/monitoring/metrics.py',         'COMMENTED',         '2024-12-05 11:00:00'),
  (7,'bob_dev',  'ext-c-027','review','carol_dev','Missing unit for the histogram bucket — seconds or ms?',            'src/monitoring/metrics.py',         'COMMENTED',         '2024-12-05 11:30:00'),
  (7,'bob_dev',  'ext-c-028','review','alice_dev','p99 added, histogram buckets look correct. LGTM.',                  NULL,                                'APPROVED',          '2024-12-05 13:00:00'),
  -- PR 8 (bob_dev) — heavy review
  (8,'bob_dev',  'ext-c-029','review','alice_dev','Field 5 was deprecated — renaming it breaks wire compatibility.',   'proto/order.proto',                 'CHANGES_REQUESTED', '2024-12-09 14:00:00'),
  (8,'bob_dev',  'ext-c-030','review','carol_dev','ShipmentStatus enum values should have a prefix per proto style.',  'proto/shipment.proto',              'CHANGES_REQUESTED', '2024-12-09 15:00:00'),
  (8,'bob_dev',  'ext-c-031','review','alice_dev','Field numbered correctly now. LGTM on compat.',                     NULL,                                'APPROVED',          '2024-12-09 17:30:00'),
  (8,'bob_dev',  'ext-c-032','review','carol_dev','Enum prefix applied. LGTM.',                                        NULL,                                'APPROVED',          '2024-12-09 18:00:00'),
  (8,'bob_dev',  'ext-c-033','review','alice_dev','Consider adding a migration guide for consumers.',                   'proto/order.proto',                 'COMMENTED',         '2024-12-09 16:00:00'),
  (8,'bob_dev',  'ext-c-034','review','carol_dev','Go bindings regenerated cleanly — no surprises.',                   'go/bindings/order.go',              'COMMENTED',         '2024-12-09 17:00:00'),
  (8,'bob_dev',  'ext-c-035','review','alice_dev','Please add a CHANGELOG entry for breaking change.',                  NULL,                                'COMMENTED',         '2024-12-09 17:15:00'),
  -- PR 9 (carol_dev) — reviewed by alice_dev and bob_dev
  (9,'carol_dev','ext-c-036','review','alice_dev','Skeleton loader should match actual card dimensions to avoid CLS.', 'src/components/ProductCard.tsx',    'CHANGES_REQUESTED', '2024-12-04 12:00:00'),
  (9,'carol_dev','ext-c-037','review','bob_dev',  'Pagination fetch should debounce scroll events.',                   'src/components/ProductList.tsx',    'COMMENTED',         '2024-12-04 12:30:00'),
  (9,'carol_dev','ext-c-038','review','alice_dev','CLS fix looks correct. LGTM.',                                      NULL,                                'APPROVED',          '2024-12-04 14:30:00'),
  (9,'carol_dev','ext-c-039','review','bob_dev',  'Debounce added. Approving.',                                        NULL,                                'APPROVED',          '2024-12-04 15:00:00'),
  (9,'carol_dev','ext-c-040','review','alice_dev','Alt text missing on product images — accessibility concern.',       'src/components/ProductCard.tsx',    'COMMENTED',         '2024-12-04 13:00:00'),
  (9,'carol_dev','ext-c-041','review','bob_dev',  'Filter sidebar keyboard nav should be verified.',                   'src/components/FilterSidebar.tsx',  'COMMENTED',         '2024-12-04 13:30:00'),
  -- PR 10 (carol_dev) — reviewed by alice_dev and bob_dev
  (10,'carol_dev','ext-c-042','review','alice_dev','Some Tailwind class strings are duplicated across components.',    'src/components/ProductCard.tsx',    'COMMENTED',         '2024-12-08 14:00:00'),
  (10,'carol_dev','ext-c-043','review','alice_dev','Extract shared button styles into a cn() helper.',                 'src/components/FilterSidebar.tsx',  'COMMENTED',         '2024-12-08 14:30:00'),
  (10,'carol_dev','ext-c-044','review','alice_dev','LGTM — cleaner than the CSS module approach.',                     NULL,                                'APPROVED',          '2024-12-08 16:30:00'),
  (10,'carol_dev','ext-c-045','review','bob_dev',  'Verify Tailwind purge config includes new class names.',           'tailwind.config.js',                'COMMENTED',         '2024-12-08 15:00:00')
)
INSERT INTO pull_request_comments (pr_id, external_id, comment_type, author_login, body, path, review_state, created_at)
SELECT
  p.id,
  cs.external_id,
  cs.comment_type,
  cs.author_login,
  cs.body,
  cs.path,
  cs.review_state,
  cs.created_at::timestamp
FROM comments_seed cs
JOIN developers dev ON dev.github_username = cs.pr_developer
JOIN pull_requests p ON p.pr_number = cs.pr_number::int AND p.developer_id = dev.id
ON CONFLICT (external_id) DO NOTHING;
