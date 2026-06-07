-- ============================================================
--  HEALTHCARE CLAIMS ANALYSIS
--  Description: All analytical SQL queries using CTEs, JOINs,
--               Window Functions, and aggregations
-- ============================================================


-- ─────────────────────────────────────────────────────────────
--  SECTION 3: CLAIMS OVERVIEW — APPROVAL RATES & VOLUME
-- ─────────────────────────────────────────────────────────────

-- 3A. Overall claim status distribution
SELECT
    status,
    COUNT(*)                                        AS total_claims,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS pct_of_total,
    ROUND(AVG(claim_amount), 2)                     AS avg_claim_amount,
    ROUND(SUM(claim_amount), 2)                     AS total_billed,
    ROUND(SUM(COALESCE(approved_amount, 0)), 2)     AS total_approved
FROM claims
GROUP BY status
ORDER BY total_claims DESC;


-- 3B. Monthly claim volume and approval rate trend
WITH monthly_stats AS (
    SELECT
        DATE_TRUNC('month', service_date)           AS month,
        COUNT(*)                                    AS total_claims,
        COUNT(*) FILTER (WHERE status = 'Approved') AS approved_claims,
        COUNT(*) FILTER (WHERE status = 'Denied')   AS denied_claims,
        COUNT(*) FILTER (WHERE status = 'Pending')  AS pending_claims,
        ROUND(AVG(claim_amount), 2)                 AS avg_claim_amount,
        ROUND(SUM(claim_amount), 2)                 AS total_billed
    FROM claims
    GROUP BY DATE_TRUNC('month', service_date)
)
SELECT
    month,
    total_claims,
    approved_claims,
    denied_claims,
    pending_claims,
    total_billed,
    avg_claim_amount,
    ROUND(approved_claims * 100.0 / NULLIF(total_claims, 0), 2) AS approval_rate_pct,
    -- Month-over-month change
    total_claims - LAG(total_claims) OVER (ORDER BY month)      AS mom_volume_change,
    ROUND(
        (approved_claims * 100.0 / NULLIF(total_claims, 0))
        - LAG(approved_claims * 100.0 / NULLIF(total_claims, 0)) OVER (ORDER BY month),
    2)                                                          AS mom_approval_rate_change
FROM monthly_stats
ORDER BY month;


-- ─────────────────────────────────────────────────────────────
--  SECTION 4: TURNAROUND TIME (TAT) ANALYSIS
-- ─────────────────────────────────────────────────────────────

-- 4A. TAT by claim status and claim type
SELECT
    claim_type,
    status,
    COUNT(*)                                    AS claim_count,
    ROUND(AVG(processing_days), 1)              AS avg_tat_days,
    PERCENTILE_CONT(0.50) WITHIN GROUP
        (ORDER BY processing_days)              AS median_tat_days,
    PERCENTILE_CONT(0.90) WITHIN GROUP
        (ORDER BY processing_days)              AS p90_tat_days,
    MIN(processing_days)                        AS min_days,
    MAX(processing_days)                        AS max_days
FROM claims
WHERE processing_days IS NOT NULL
GROUP BY claim_type, status
ORDER BY claim_type, avg_tat_days DESC;


-- 4B. Identify claims exceeding SLA (>30 days for standard, >10 days for emergency)
WITH sla_thresholds AS (
    SELECT
        claim_id,
        claim_type,
        status,
        processing_days,
        CASE
            WHEN claim_type = 'Emergency'  THEN 10
            WHEN claim_type = 'Inpatient'  THEN 20
            ELSE 30
        END                                     AS sla_days
    FROM claims
    WHERE processing_days IS NOT NULL
)
SELECT
    claim_type,
    COUNT(*)                                    AS total_claims,
    COUNT(*) FILTER (WHERE processing_days > sla_days)  AS sla_breaches,
    ROUND(
        COUNT(*) FILTER (WHERE processing_days > sla_days) * 100.0 / COUNT(*),
    2)                                          AS breach_rate_pct,
    ROUND(AVG(processing_days - sla_days)
        FILTER (WHERE processing_days > sla_days), 1) AS avg_days_over_sla
FROM sla_thresholds
GROUP BY claim_type
ORDER BY breach_rate_pct DESC;


-- 4C. Provider-level TAT performance (with ranking)
WITH provider_tat AS (
    SELECT
        p.provider_id,
        p.provider_name,
        p.specialty,
        p.state,
        COUNT(c.claim_id)                       AS total_claims,
        ROUND(AVG(c.processing_days), 1)        AS avg_tat_days,
        ROUND(AVG(c.claim_amount), 2)           AS avg_claim_amount,
        COUNT(*) FILTER (WHERE c.status = 'Denied') * 100.0
            / NULLIF(COUNT(*), 0)               AS denial_rate_pct
    FROM providers p
    JOIN claims c USING (provider_id)
    WHERE c.processing_days IS NOT NULL
    GROUP BY p.provider_id, p.provider_name, p.specialty, p.state
)
SELECT
    provider_name,
    specialty,
    state,
    total_claims,
    avg_tat_days,
    avg_claim_amount,
    ROUND(denial_rate_pct, 2)                   AS denial_rate_pct,
    RANK() OVER (ORDER BY avg_tat_days ASC)     AS tat_rank_best,
    RANK() OVER (ORDER BY denial_rate_pct ASC)  AS denial_rank_best,
    NTILE(4) OVER (ORDER BY avg_tat_days)       AS tat_quartile   -- 1=fastest
FROM provider_tat
ORDER BY avg_tat_days;


-- ─────────────────────────────────────────────────────────────
--  SECTION 5: DENIAL ANALYSIS & ERROR PATTERN DETECTION
-- ─────────────────────────────────────────────────────────────

-- 5A. Denial reasons frequency and financial impact
SELECT
    denial_reason,
    COUNT(*)                                    AS denial_count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS pct_of_denials,
    ROUND(SUM(claim_amount), 2)                 AS total_denied_amount,
    ROUND(AVG(claim_amount), 2)                 AS avg_denied_amount,
    -- Running total by financial impact
    ROUND(SUM(SUM(claim_amount)) OVER (
        ORDER BY SUM(claim_amount) DESC
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ), 2)                                       AS cumulative_denied_amount
FROM claims
WHERE status = 'Denied'
  AND denial_reason IS NOT NULL
GROUP BY denial_reason
ORDER BY total_denied_amount DESC;


-- 5B. Error type analysis with claim correlation
WITH error_summary AS (
    SELECT
        ce.error_type,
        ce.error_severity,
        COUNT(DISTINCT ce.claim_id)             AS affected_claims,
        COUNT(*)                                AS total_errors,
        COUNT(*) FILTER (WHERE ce.resolved_at IS NOT NULL) AS resolved_errors,
        ROUND(AVG(
            EXTRACT(EPOCH FROM (ce.resolved_at - ce.detected_at)) / 3600
        ) FILTER (WHERE ce.resolved_at IS NOT NULL), 1) AS avg_resolution_hours
    FROM claim_errors ce
    GROUP BY ce.error_type, ce.error_severity
)
SELECT
    error_type,
    error_severity,
    affected_claims,
    total_errors,
    resolved_errors,
    total_errors - resolved_errors              AS open_errors,
    ROUND(resolved_errors * 100.0 / NULLIF(total_errors, 0), 1) AS resolution_rate_pct,
    avg_resolution_hours
FROM error_summary
ORDER BY
    CASE error_severity
        WHEN 'Critical' THEN 1
        WHEN 'Major'    THEN 2
        WHEN 'Minor'    THEN 3
    END,
    affected_claims DESC;


-- 5C. Bottleneck Detection — Claims stuck in Pending > 15 days
WITH pending_analysis AS (
    SELECT
        c.claim_id,
        c.submission_date,
        CURRENT_DATE - c.submission_date        AS days_pending,
        c.claim_amount,
        c.claim_type,
        p.provider_name,
        py.payer_name,
        d.category                              AS diagnosis_category,
        -- Error flag
        COUNT(ce.error_id)                      AS error_count
    FROM claims c
    JOIN providers  p  USING (provider_id)
    JOIN payers     py USING (payer_id)
    LEFT JOIN diagnosis_codes d ON c.icd10_code = d.icd10_code
    LEFT JOIN claim_errors ce ON c.claim_id = ce.claim_id
                              AND ce.resolved_at IS NULL
    WHERE c.status = 'Pending'
    GROUP BY c.claim_id, c.submission_date, c.claim_amount,
             c.claim_type, p.provider_name, py.payer_name, d.category
)
SELECT
    claim_id,
    days_pending,
    claim_amount,
    claim_type,
    provider_name,
    payer_name,
    diagnosis_category,
    error_count,
    CASE
        WHEN days_pending > 45 THEN 'CRITICAL'
        WHEN days_pending > 30 THEN 'HIGH'
        WHEN days_pending > 15 THEN 'MEDIUM'
        ELSE 'LOW'
    END                                         AS priority_flag
FROM pending_analysis
WHERE days_pending > 15
ORDER BY days_pending DESC, error_count DESC;


-- ─────────────────────────────────────────────────────────────
--  SECTION 6: PAYER PERFORMANCE BENCHMARKING
-- ─────────────────────────────────────────────────────────────

-- 6A. Payer scorecards
WITH payer_metrics AS (
    SELECT
        py.payer_id,
        py.payer_name,
        py.payer_type,
        COUNT(c.claim_id)                       AS total_claims,
        ROUND(AVG(c.processing_days), 1)        AS avg_tat_days,
        ROUND(AVG(c.claim_amount), 2)           AS avg_claim_amount,
        ROUND(SUM(c.claim_amount), 2)           AS total_billed,
        ROUND(SUM(c.approved_amount), 2)        AS total_approved,
        COUNT(*) FILTER (WHERE c.status = 'Approved')   AS approved_count,
        COUNT(*) FILTER (WHERE c.status = 'Denied')     AS denied_count,
        COUNT(*) FILTER (WHERE c.status = 'Pending')    AS pending_count
    FROM payers py
    JOIN claims c USING (payer_id)
    GROUP BY py.payer_id, py.payer_name, py.payer_type
)
SELECT
    payer_name,
    payer_type,
    total_claims,
    avg_tat_days,
    avg_claim_amount,
    total_billed,
    total_approved,
    ROUND(total_approved / NULLIF(total_billed, 0) * 100, 2) AS payment_ratio_pct,
    ROUND(approved_count * 100.0 / NULLIF(total_claims, 0), 2) AS approval_rate_pct,
    ROUND(denied_count   * 100.0 / NULLIF(total_claims, 0), 2) AS denial_rate_pct,
    -- Payer performance score (lower TAT + higher approval = better)
    ROUND(
        (RANK() OVER (ORDER BY avg_tat_days ASC) +
         RANK() OVER (ORDER BY approved_count * 100.0 / NULLIF(total_claims,0) DESC))
        / 2.0, 1
    )                                           AS composite_score
FROM payer_metrics
ORDER BY composite_score;


-- ─────────────────────────────────────────────────────────────
--  SECTION 7: FINANCIAL LEAKAGE & RECOVERY ANALYSIS
-- ─────────────────────────────────────────────────────────────

-- 7A. Identify underpaid claims (approved < billed by >20%)
WITH payment_gap AS (
    SELECT
        c.claim_id,
        c.claim_amount                          AS billed_amount,
        c.approved_amount,
        c.claim_amount - c.approved_amount      AS payment_gap,
        ROUND(
            (c.claim_amount - c.approved_amount)
            / NULLIF(c.claim_amount, 0) * 100, 2
        )                                       AS underpayment_pct,
        p.provider_name,
        py.payer_name,
        c.claim_type,
        d.category                              AS diagnosis_category,
        c.service_date
    FROM claims c
    JOIN providers  p  USING (provider_id)
    JOIN payers     py USING (payer_id)
    LEFT JOIN diagnosis_codes d ON c.icd10_code = d.icd10_code
    WHERE c.status = 'Approved'
      AND c.approved_amount IS NOT NULL
      AND c.approved_amount < c.claim_amount * 0.80  -- >20% underpaid
)
SELECT
    claim_type,
    diagnosis_category,
    payer_name,
    COUNT(*)                                    AS underpaid_claims,
    ROUND(SUM(payment_gap), 2)                  AS total_leakage,
    ROUND(AVG(underpayment_pct), 2)             AS avg_underpayment_pct,
    ROUND(MAX(payment_gap), 2)                  AS max_single_gap
FROM payment_gap
GROUP BY claim_type, diagnosis_category, payer_name
ORDER BY total_leakage DESC;


-- 7B. Resubmission success rate (denied → resubmitted → approved)
WITH resubmission_tracking AS (
    SELECT
        c1.claim_id                             AS original_claim_id,
        c1.patient_id,
        c1.provider_id,
        c1.denial_reason,
        c1.claim_amount,
        c2.claim_id                             AS resubmitted_claim_id,
        c2.status                               AS resubmission_status,
        c2.approved_amount
    FROM claims c1
    JOIN claims c2 ON c1.patient_id   = c2.patient_id
                  AND c1.provider_id  = c2.provider_id
                  AND c1.icd10_code   = c2.icd10_code
                  AND c2.submission_date > c1.submission_date
                  AND c1.status = 'Denied'
                  AND c2.status IN ('Approved', 'Pending', 'Denied')
)
SELECT
    denial_reason,
    COUNT(DISTINCT original_claim_id)           AS total_resubmissions,
    COUNT(*) FILTER (WHERE resubmission_status = 'Approved') AS successful,
    COUNT(*) FILTER (WHERE resubmission_status = 'Denied')   AS still_denied,
    ROUND(
        COUNT(*) FILTER (WHERE resubmission_status = 'Approved') * 100.0
        / NULLIF(COUNT(*), 0), 2
    )                                           AS success_rate_pct,
    ROUND(SUM(approved_amount)
        FILTER (WHERE resubmission_status = 'Approved'), 2) AS recovered_amount
FROM resubmission_tracking
GROUP BY denial_reason
ORDER BY total_resubmissions DESC;


-- ─────────────────────────────────────────────────────────────
--  SECTION 8: EXECUTIVE SUMMARY KPI VIEW
-- ─────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW vw_executive_kpis AS
WITH base AS (
    SELECT
        COUNT(*)                                                AS total_claims,
        COUNT(*) FILTER (WHERE status = 'Approved')            AS approved,
        COUNT(*) FILTER (WHERE status = 'Denied')              AS denied,
        COUNT(*) FILTER (WHERE status = 'Pending')             AS pending,
        ROUND(AVG(processing_days) FILTER
            (WHERE processing_days IS NOT NULL), 1)            AS avg_tat_days,
        ROUND(SUM(claim_amount), 2)                            AS total_billed,
        ROUND(SUM(approved_amount), 2)                         AS total_approved,
        ROUND(AVG(claim_amount), 2)                            AS avg_claim_value,
        COUNT(DISTINCT provider_id)                            AS active_providers,
        COUNT(DISTINCT payer_id)                               AS active_payers,
        COUNT(DISTINCT patient_id)                             AS unique_patients
    FROM claims
)
SELECT
    total_claims,
    approved,
    denied,
    pending,
    ROUND(approved * 100.0 / NULLIF(total_claims, 0), 2)      AS approval_rate_pct,
    ROUND(denied   * 100.0 / NULLIF(total_claims, 0), 2)      AS denial_rate_pct,
    avg_tat_days,
    total_billed,
    total_approved,
    ROUND(total_approved / NULLIF(total_billed, 0) * 100, 2)  AS collection_ratio_pct,
    avg_claim_value,
    active_providers,
    active_payers,
    unique_patients
FROM base;

SELECT * FROM vw_executive_kpis;
