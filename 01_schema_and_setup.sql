-- ============================================================
--  HEALTHCARE CLAIMS ANALYSIS
--  Description: Database schema creation and seed data setup
-- ============================================================


-- ─────────────────────────────────────────────────────────────
--  SECTION 1: CREATE TABLES
-- ─────────────────────────────────────────────────────────────

-- Drop existing tables (safe re-run)
DROP TABLE IF EXISTS claim_errors;
DROP TABLE IF EXISTS claim_audit_log;
DROP TABLE IF EXISTS claims;
DROP TABLE IF EXISTS providers;
DROP TABLE IF EXISTS patients;
DROP TABLE IF EXISTS payers;
DROP TABLE IF EXISTS diagnosis_codes;

-- Payers (Insurance Companies)
CREATE TABLE payers (
    payer_id        SERIAL PRIMARY KEY,
    payer_name      VARCHAR(100) NOT NULL,
    payer_type      VARCHAR(50),          -- 'Commercial', 'Medicare', 'Medicaid'
    contact_email   VARCHAR(100),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Providers (Hospitals / Clinics)
CREATE TABLE providers (
    provider_id     SERIAL PRIMARY KEY,
    provider_name   VARCHAR(150) NOT NULL,
    specialty       VARCHAR(100),
    npi_number      VARCHAR(20) UNIQUE,
    state           VARCHAR(5),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Patients
CREATE TABLE patients (
    patient_id      SERIAL PRIMARY KEY,
    date_of_birth   DATE,
    gender          VARCHAR(10),
    state           VARCHAR(5),
    insurance_type  VARCHAR(50),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Diagnosis / Procedure Codes (ICD-10 reference)
CREATE TABLE diagnosis_codes (
    code_id         SERIAL PRIMARY KEY,
    icd10_code      VARCHAR(20) UNIQUE NOT NULL,
    description     VARCHAR(255),
    category        VARCHAR(100)       -- e.g. 'Cardiology', 'Orthopedics'
);

-- Main Claims Table
CREATE TABLE claims (
    claim_id            SERIAL PRIMARY KEY,
    patient_id          INT REFERENCES patients(patient_id),
    provider_id         INT REFERENCES providers(provider_id),
    payer_id            INT REFERENCES payers(payer_id),
    icd10_code          VARCHAR(20) REFERENCES diagnosis_codes(icd10_code),
    service_date        DATE NOT NULL,
    submission_date     DATE NOT NULL,
    decision_date       DATE,
    claim_amount        NUMERIC(12, 2) NOT NULL,
    approved_amount     NUMERIC(12, 2),
    status              VARCHAR(30),    -- 'Approved','Denied','Pending','Resubmitted'
    denial_reason       VARCHAR(255),
    claim_type          VARCHAR(50),    -- 'Inpatient','Outpatient','Emergency','Pharmacy'
    processing_days     INT GENERATED ALWAYS AS (
                            CASE WHEN decision_date IS NOT NULL
                            THEN decision_date - submission_date
                            ELSE NULL END
                        ) STORED,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- Claim Errors Log
CREATE TABLE claim_errors (
    error_id        SERIAL PRIMARY KEY,
    claim_id        INT REFERENCES claims(claim_id),
    error_type      VARCHAR(100),   -- 'Missing Info', 'Duplicate', 'Coding Error', etc.
    error_severity  VARCHAR(20),    -- 'Critical', 'Major', 'Minor'
    detected_at     TIMESTAMP DEFAULT NOW(),
    resolved_at     TIMESTAMP,
    resolved_by     VARCHAR(100)
);

-- Audit Log for ETL tracking
CREATE TABLE claim_audit_log (
    log_id          SERIAL PRIMARY KEY,
    operation       VARCHAR(50),    -- 'INSERT', 'UPDATE', 'DELETE', 'ETL_RUN'
    table_name      VARCHAR(50),
    record_count    INT,
    status          VARCHAR(20),
    message         TEXT,
    executed_at     TIMESTAMP DEFAULT NOW()
);


-- ─────────────────────────────────────────────────────────────
--  SECTION 2: INDEXES FOR PERFORMANCE
-- ─────────────────────────────────────────────────────────────

CREATE INDEX idx_claims_status          ON claims(status);
CREATE INDEX idx_claims_service_date    ON claims(service_date);
CREATE INDEX idx_claims_submission_date ON claims(submission_date);
CREATE INDEX idx_claims_provider        ON claims(provider_id);
CREATE INDEX idx_claims_payer           ON claims(payer_id);
CREATE INDEX idx_claims_patient         ON claims(patient_id);
CREATE INDEX idx_errors_claim_id        ON claim_errors(claim_id);
CREATE INDEX idx_errors_type            ON claim_errors(error_type);
