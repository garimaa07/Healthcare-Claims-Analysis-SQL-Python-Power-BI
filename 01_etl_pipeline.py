============================================================
 HEALTHCARE CLAIMS ANALYSIS
 File: 01_etl_pipeline.py
 Description: End-to-end ETL pipeline — Extract, Transform,
              Validate, Load claims data with audit logging
============================================================
"""

import pandas as pd
import numpy as np
import sqlite3
import logging
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple

# ─────────────────────────────────────────────────────────────
#  SECTION 1: CONFIGURATION & LOGGING SETUP
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler("outputs/etl_pipeline.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("claims_etl")

DB_PATH    = "outputs/claims.db"
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

# SLA thresholds by claim type (days)
SLA_THRESHOLDS = {
    "Emergency":  10,
    "Inpatient":  20,
    "Outpatient": 30,
    "Pharmacy":   15,
}


# ─────────────────────────────────────────────────────────────
#  SECTION 2: DATA GENERATION (Simulate 100K+ Claims Dataset)
# ─────────────────────────────────────────────────────────────

def generate_claims_dataset(n_claims: int = 10_000, seed: int = 42) -> pd.DataFrame:
    """
    Simulate a realistic healthcare claims dataset.
    In production this would be replaced with a file/DB read.
    """
    logger.info(f"Generating synthetic claims dataset: {n_claims:,} records")
    rng = np.random.default_rng(seed)

    providers = [
        ("P001", "City General Hospital",    "Internal Medicine", "NY"),
        ("P002", "Metro Heart Center",        "Cardiology",        "CA"),
        ("P003", "Orthopedic Specialists",    "Orthopedics",       "TX"),
        ("P004", "HealthFirst Clinic",        "Family Medicine",   "FL"),
        ("P005", "Regional Cancer Center",    "Oncology",          "IL"),
        ("P006", "QuickCare Urgent Center",   "Emergency",         "WA"),
    ]
    payers = [
        ("INS001", "BlueCross BlueShield", "Commercial"),
        ("INS002", "UnitedHealthcare",     "Commercial"),
        ("INS003", "Medicare Federal",     "Medicare"),
        ("INS004", "Medicaid State Plan",  "Medicaid"),
        ("INS005", "Aetna Health Plans",   "Commercial"),
    ]
    icd10_codes = [
        ("I21.0",  "Anterior STEMI",            "Cardiology"),
        ("M17.11", "Primary osteoarthritis",     "Orthopedics"),
        ("J18.9",  "Pneumonia unspecified",      "Pulmonology"),
        ("E11.9",  "Type 2 diabetes",            "Endocrinology"),
        ("C34.10", "Malignant lung neoplasm",    "Oncology"),
        ("S72.001","Femur fracture",             "Orthopedics"),
        ("N18.3",  "Chronic kidney disease",     "Nephrology"),
        ("F32.9",  "Major depressive disorder",  "Psychiatry"),
    ]
    claim_types     = ["Inpatient", "Outpatient", "Emergency", "Pharmacy"]
    denial_reasons  = [
        "Medical Necessity Not Established",
        "Prior Authorization Missing",
        "Duplicate Claim",
        "Invalid ICD-10 Code",
        "Member Not Eligible",
        "Timely Filing Exceeded",
        "Service Not Covered",
        None,   # Approved claims have no denial reason
    ]

    # Generate base dates
    base_date    = datetime(2023, 1, 1)
    service_dates = [base_date + timedelta(days=int(x))
                     for x in rng.integers(0, 730, n_claims)]

    # Processing delays — bimodal: fast (5-15d) or slow (30-60d)
    fast_mask    = rng.random(n_claims) > 0.30
    proc_days    = np.where(
        fast_mask,
        rng.integers(5, 16,  n_claims),
        rng.integers(30, 61, n_claims)
    )

    # Status distribution: ~65% approved, 20% denied, 15% pending
    statuses = rng.choice(
        ["Approved", "Denied", "Pending", "Resubmitted"],
        size=n_claims, p=[0.65, 0.20, 0.10, 0.05]
    )

    provider_sample = [providers[i] for i in rng.integers(0, len(providers), n_claims)]
    payer_sample    = [payers[i]    for i in rng.integers(0, len(payers),    n_claims)]
    icd10_sample    = [icd10_codes[i] for i in rng.integers(0, len(icd10_codes), n_claims)]
    ctype_sample    = rng.choice(claim_types, n_claims, p=[0.25, 0.40, 0.20, 0.15])

    claim_amounts = np.round(rng.lognormal(mean=8.5, sigma=1.2, size=n_claims), 2)
    approved_amounts = np.where(
        statuses == "Approved",
        np.round(claim_amounts * rng.uniform(0.70, 1.0, n_claims), 2),
        np.nan
    )

    denial_sample = rng.choice(denial_reasons[:-1], n_claims)
    denial_col    = np.where(statuses == "Denied", denial_sample, None)

    # Build submission + decision dates
    submission_dates = [
        sd + timedelta(days=int(rng.integers(1, 4)))
        for sd in service_dates
    ]
    decision_dates = [
        sd + timedelta(days=int(pd_))
        if status != "Pending" else None
        for sd, pd_, status in zip(submission_dates, proc_days, statuses)
    ]

    df = pd.DataFrame({
        "claim_id":         [f"CLM{str(i).zfill(7)}" for i in range(1, n_claims + 1)],
        "patient_id":       [f"PAT{str(i).zfill(6)}" for i in rng.integers(1, 5001, n_claims)],
        "provider_id":      [p[0] for p in provider_sample],
        "provider_name":    [p[1] for p in provider_sample],
        "provider_specialty":[p[2] for p in provider_sample],
        "provider_state":   [p[3] for p in provider_sample],
        "payer_id":         [p[0] for p in payer_sample],
        "payer_name":       [p[1] for p in payer_sample],
        "payer_type":       [p[2] for p in payer_sample],
        "icd10_code":       [c[0] for c in icd10_sample],
        "diagnosis":        [c[1] for c in icd10_sample],
        "diagnosis_category":[c[2] for c in icd10_sample],
        "claim_type":       ctype_sample,
        "service_date":     service_dates,
        "submission_date":  submission_dates,
        "decision_date":    decision_dates,
        "claim_amount":     claim_amounts,
        "approved_amount":  approved_amounts,
        "status":           statuses,
        "denial_reason":    denial_col,
    })

    # Inject realistic dirty data for ETL demonstration
    dirty_idx = rng.choice(df.index, size=int(n_claims * 0.05), replace=False)
    df.loc[dirty_idx[:50], "claim_amount"]    = np.nan          # Missing amounts
    df.loc[dirty_idx[50:100], "icd10_code"]   = "INVALID_CODE"  # Bad codes
    df.loc[dirty_idx[100:150], "submission_date"] = (           # Future dates
        df.loc[dirty_idx[100:150], "submission_date"]
        .apply(lambda x: x + timedelta(days=500))
    )

    logger.info(f"Dataset generated: {len(df):,} rows, {df.shape[1]} columns")
    return df


# ─────────────────────────────────────────────────────────────
#  SECTION 3: EXTRACTION LAYER
# ─────────────────────────────────────────────────────────────

class ClaimsExtractor:
    """
    Handles data extraction from various source formats.
    Supports CSV, Excel, JSON, and direct DataFrame input.
    """

    def __init__(self):
        self.extraction_log = []

    def extract_from_dataframe(self, df: pd.DataFrame, source_name: str = "generated") -> pd.DataFrame:
        """Extract from an in-memory DataFrame (used for demo)."""
        logger.info(f"[EXTRACT] Source: {source_name} | Rows: {len(df):,}")
        self.extraction_log.append({
            "source": source_name,
            "rows_extracted": len(df),
            "columns": list(df.columns),
            "timestamp": datetime.now().isoformat()
        })
        return df.copy()

    def extract_from_csv(self, filepath: str) -> pd.DataFrame:
        """Extract from CSV with robust parsing."""
        logger.info(f"[EXTRACT] Reading CSV: {filepath}")
        df = pd.read_csv(
            filepath,
            parse_dates=["service_date", "submission_date", "decision_date"],
            low_memory=False
        )
        logger.info(f"[EXTRACT] Loaded {len(df):,} rows from {filepath}")
        return df

    def get_extraction_summary(self) -> dict:
        return {
            "total_sources": len(self.extraction_log),
            "total_rows": sum(e["rows_extracted"] for e in self.extraction_log),
            "log": self.extraction_log
        }


# ─────────────────────────────────────────────────────────────
#  SECTION 4: TRANSFORMATION & CLEANING LAYER
# ─────────────────────────────────────────────────────────────

class ClaimsTransformer:
    """
    Applies all data cleaning, enrichment, and transformation rules.
    Tracks every transformation step for auditability.
    """

    def __init__(self):
        self.transform_report = {
            "steps": [],
            "total_rows_in":    0,
            "total_rows_out":   0,
            "rows_dropped":     0,
            "rows_corrected":   0
        }

    def _log_step(self, step: str, rows_before: int, rows_after: int, notes: str = ""):
        delta = rows_before - rows_after
        self.transform_report["steps"].append({
            "step": step,
            "rows_before": rows_before,
            "rows_after":  rows_after,
            "rows_affected": abs(delta),
            "notes": notes
        })
        if delta > 0:
            logger.info(f"  ↳ [{step}] Removed {delta:,} rows. Remaining: {rows_after:,}")
        else:
            logger.info(f"  ↳ [{step}] Enriched/modified {abs(delta):,} rows. Total: {rows_after:,}")

    # ── 4A. Clean missing values ────────────────────────────
    def clean_missing_values(self, df: pd.DataFrame) -> pd.DataFrame:
        logger.info("[TRANSFORM] Step 1: Cleaning missing values")
        n = len(df)

        # Drop rows with null claim_id or patient_id (critical fields)
        df = df.dropna(subset=["claim_id", "patient_id"])
        self._log_step("drop_null_ids", n, len(df), "Dropped rows with null IDs")

        # Fill missing claim_amounts with provider-specialty median
        n2 = len(df)
        null_amounts = df["claim_amount"].isna().sum()
        if null_amounts > 0:
            median_by_specialty = (
                df.groupby("provider_specialty")["claim_amount"].median()
            )
            df["claim_amount"] = df.apply(
                lambda row: median_by_specialty.get(row["provider_specialty"], df["claim_amount"].median())
                if pd.isna(row["claim_amount"]) else row["claim_amount"],
                axis=1
            )
            self.transform_report["rows_corrected"] += null_amounts
            logger.info(f"  ↳ Imputed {null_amounts:,} missing claim_amounts with specialty medians")

        # Fill missing denial reasons
        df["denial_reason"] = df["denial_reason"].fillna("Not Applicable")

        return df

    # ── 4B. Standardise date columns ───────────────────────
    def standardise_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        logger.info("[TRANSFORM] Step 2: Standardising date columns")
        n = len(df)
        date_cols = ["service_date", "submission_date", "decision_date"]

        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

        # Remove future submission dates (data entry errors)
        today = pd.Timestamp.today().normalize()
        future_mask = df["submission_date"] > today
        df = df[~future_mask]
        self._log_step("remove_future_dates", n, len(df),
                       f"Removed {future_mask.sum()} future submission dates")

        # Remove records where service_date > submission_date
        n2 = len(df)
        invalid_seq = df["service_date"] > df["submission_date"]
        df = df[~invalid_seq]
        self._log_step("remove_invalid_date_sequence", n2, len(df),
                       "service_date must not exceed submission_date")
        return df

    # ── 4C. Validate & standardise ICD-10 codes ────────────
    def validate_icd10_codes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Basic ICD-10 format validation: letter + 2 digits + optional decimal."""
        logger.info("[TRANSFORM] Step 3: Validating ICD-10 codes")

        valid_pattern = r"^[A-Z]\d{2}(\.\d+)?$"
        invalid_mask  = ~df["icd10_code"].str.match(valid_pattern, na=False)
        invalid_count = invalid_mask.sum()

        # Tag invalid codes rather than dropping (preserve for error log)
        df.loc[invalid_mask, "icd10_code"] = "Z99.999"  # placeholder / unknown
        df["icd10_flag"] = invalid_mask.astype(int)
        self.transform_report["rows_corrected"] += invalid_count
        logger.info(f"  ↳ Flagged and replaced {invalid_count:,} invalid ICD-10 codes")
        return df

    # ── 4D. Derive calculated columns ──────────────────────
    def engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        logger.info("[TRANSFORM] Step 4: Engineering derived features")

        # Processing / Turnaround Time
        df["processing_days"] = (
            (df["decision_date"] - df["submission_date"])
            .dt.days
            .where(df["decision_date"].notna())
        )

        # Submission lag (service to submission)
        df["submission_lag_days"] = (
            df["submission_date"] - df["service_date"]
        ).dt.days

        # SLA compliance
        df["sla_threshold"] = df["claim_type"].map(SLA_THRESHOLDS).fillna(30)
        df["sla_breach"] = (
            (df["processing_days"].notna()) &
            (df["processing_days"] > df["sla_threshold"])
        ).astype(int)

        # Payment gap
        df["payment_gap"] = df.apply(
            lambda r: round(r["claim_amount"] - r["approved_amount"], 2)
            if pd.notna(r.get("approved_amount")) else None,
            axis=1
        )
        df["payment_gap_pct"] = df.apply(
            lambda r: round(r["payment_gap"] / r["claim_amount"] * 100, 2)
            if pd.notna(r.get("payment_gap")) and r["claim_amount"] > 0 else None,
            axis=1
        )

        # Risk tier based on claim amount
        df["claim_risk_tier"] = pd.cut(
            df["claim_amount"],
            bins=[0, 1_000, 5_000, 25_000, np.inf],
            labels=["Low", "Medium", "High", "Critical"]
        )

        # Year-Month for time series
        df["year_month"] = df["service_date"].dt.to_period("M").astype(str)

        # Claim age bucket (for pending claims)
        df["pending_age_bucket"] = pd.cut(
            (pd.Timestamp.today().normalize() - df["submission_date"]).dt.days,
            bins=[-1, 15, 30, 45, np.inf],
            labels=["0-15d", "16-30d", "31-45d", "45d+"]
        )

        # Deduplication hash (patient + provider + service date + amount)
        df["dedup_hash"] = df.apply(
            lambda r: hashlib.md5(
                f"{r['patient_id']}|{r['provider_id']}|{r['service_date']}|{r['claim_amount']}".encode()
            ).hexdigest(), axis=1
        )

        logger.info(f"  ↳ Added 9 derived columns. Shape: {df.shape}")
        return df

    # ── 4E. Remove duplicates ───────────────────────────────
    def remove_duplicates(self, df: pd.DataFrame) -> pd.DataFrame:
        logger.info("[TRANSFORM] Step 5: Removing duplicate claims")
        n = len(df)
        df = df.drop_duplicates(subset=["dedup_hash"], keep="last")
        self._log_step("remove_duplicates", n, len(df),
                       f"Removed {n - len(df):,} exact duplicates by hash")
        return df

    # ── Main transform pipeline ─────────────────────────────
    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        logger.info("=" * 60)
        logger.info("[TRANSFORM] Starting transformation pipeline")
        self.transform_report["total_rows_in"] = len(df)

        df = self.clean_missing_values(df)
        df = self.standardise_dates(df)
        df = self.validate_icd10_codes(df)
        df = self.engineer_features(df)
        df = self.remove_duplicates(df)

        self.transform_report["total_rows_out"]  = len(df)
        self.transform_report["rows_dropped"]    = (
            self.transform_report["total_rows_in"] -
            self.transform_report["total_rows_out"]
        )
        logger.info(f"[TRANSFORM] Complete. In: {self.transform_report['total_rows_in']:,} "
                    f"| Out: {self.transform_report['total_rows_out']:,} "
                    f"| Dropped: {self.transform_report['rows_dropped']:,} "
                    f"| Corrected: {self.transform_report['rows_corrected']:,}")
        return df

    def get_report(self) -> dict:
        return self.transform_report


# ─────────────────────────────────────────────────────────────
#  SECTION 5: VALIDATION LAYER
# ─────────────────────────────────────────────────────────────

class ClaimsValidator:
    """
    Data quality checks after transformation.
    Returns a validation report with pass/fail per rule.
    """

    def __init__(self):
        self.rules_passed = []
        self.rules_failed = []

    def _check(self, rule: str, condition: bool, detail: str = ""):
        if condition:
            self.rules_passed.append({"rule": rule, "status": "PASS", "detail": detail})
            logger.info(f"  ✓ PASS | {rule}")
        else:
            self.rules_failed.append({"rule": rule, "status": "FAIL", "detail": detail})
            logger.warning(f"  ✗ FAIL | {rule} — {detail}")

    def validate(self, df: pd.DataFrame) -> bool:
        logger.info("[VALIDATE] Running data quality rules")

        # Rule 1: No null claim IDs
        self._check("no_null_claim_ids",
                    df["claim_id"].notna().all(),
                    f"Null count: {df['claim_id'].isna().sum()}")

        # Rule 2: No null claim amounts
        self._check("no_null_claim_amounts",
                    df["claim_amount"].notna().all(),
                    f"Null count: {df['claim_amount'].isna().sum()}")

        # Rule 3: Claim amounts must be positive
        neg_amounts = (df["claim_amount"] <= 0).sum()
        self._check("positive_claim_amounts",
                    neg_amounts == 0,
                    f"Non-positive: {neg_amounts}")

        # Rule 4: Valid status values
        valid_statuses  = {"Approved", "Denied", "Pending", "Resubmitted"}
        invalid_statuses = df[~df["status"].isin(valid_statuses)].shape[0]
        self._check("valid_status_values",
                    invalid_statuses == 0,
                    f"Invalid: {invalid_statuses}")

        # Rule 5: Submission date not in future
        future = (df["submission_date"] > pd.Timestamp.today().normalize()).sum()
        self._check("no_future_submission_dates", future == 0, f"Future dates: {future}")

        # Rule 6: Approved amounts ≤ claim amounts
        over_approved = (
            df[df["status"] == "Approved"]["approved_amount"] >
            df[df["status"] == "Approved"]["claim_amount"]
        ).sum()
        self._check("approved_le_claimed",
                    over_approved == 0,
                    f"Over-approved: {over_approved}")

        # Rule 7: Duplicate check
        dup_rate = df["dedup_hash"].duplicated().sum() / len(df)
        self._check("duplicate_rate_under_1pct",
                    dup_rate < 0.01,
                    f"Dup rate: {dup_rate:.2%}")

        # Rule 8: Approved claims have approved_amount
        approved_missing = (
            (df["status"] == "Approved") & (df["approved_amount"].isna())
        ).sum()
        self._check("approved_claims_have_amount",
                    approved_missing == 0,
                    f"Missing approved_amount: {approved_missing}")

        all_passed = len(self.rules_failed) == 0
        logger.info(f"[VALIDATE] Result: {len(self.rules_passed)} PASS, {len(self.rules_failed)} FAIL")
        return all_passed

    def get_report(self) -> dict:
        return {
            "passed": self.rules_passed,
            "failed": self.rules_failed,
            "total_rules": len(self.rules_passed) + len(self.rules_failed),
            "pass_count":  len(self.rules_passed),
            "fail_count":  len(self.rules_failed),
            "overall_status": "PASS" if not self.rules_failed else "FAIL"
        }


# ─────────────────────────────────────────────────────────────
#  SECTION 6: LOAD LAYER
# ─────────────────────────────────────────────────────────────

class ClaimsLoader:
    """
    Loads transformed, validated data to SQLite (or Postgres in production).
    Writes audit log entries for every batch loaded.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn    = sqlite3.connect(db_path)
        logger.info(f"[LOAD] Database connected: {db_path}")

    def load_claims(self, df: pd.DataFrame) -> int:
        """Load cleaned claims to the main table."""
        logger.info(f"[LOAD] Writing {len(df):,} rows to claims table")
        df.to_sql("claims_clean", self.conn, if_exists="replace", index=False,
                  chunksize=5_000)

        self._write_audit_log("LOAD", "claims_clean", len(df), "SUCCESS",
                              f"ETL run at {datetime.now().isoformat()}")
        logger.info(f"[LOAD] ✓ Loaded {len(df):,} rows successfully")
        return len(df)

    def load_error_flags(self, df: pd.DataFrame) -> int:
        """Extract and load rows flagged during transformation."""
        error_df = df[
            (df.get("icd10_flag", pd.Series(0)) == 1) |
            (df["sla_breach"] == 1)
        ].copy()
        error_df["error_type"] = np.where(
            error_df.get("icd10_flag", pd.Series(0)) == 1,
            "Invalid ICD-10 Code",
            "SLA Breach"
        )
        error_df["error_severity"] = np.where(
            error_df["claim_risk_tier"].isin(["High", "Critical"]),
            "Critical", "Major"
        )
        error_df[["claim_id", "error_type", "error_severity"]].to_sql(
            "claim_errors", self.conn, if_exists="replace", index=False
        )
        logger.info(f"[LOAD] ✓ Loaded {len(error_df):,} error records")
        return len(error_df)

    def _write_audit_log(self, operation, table, count, status, message):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS etl_audit_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation TEXT, table_name TEXT,
                record_count INTEGER, status TEXT,
                message TEXT, executed_at TEXT
            )
        """)
        cursor.execute("""
            INSERT INTO etl_audit_log
            (operation, table_name, record_count, status, message, executed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (operation, table, count, status, message, datetime.now().isoformat()))
        self.conn.commit()

    def close(self):
        self.conn.close()
        logger.info("[LOAD] Database connection closed")


# ─────────────────────────────────────────────────────────────
#  SECTION 7: ORCHESTRATOR — RUN FULL ETL PIPELINE
# ─────────────────────────────────────────────────────────────

def run_etl_pipeline(n_claims: int = 10_000) -> Tuple[pd.DataFrame, dict]:
    """
    Orchestrates the full Extract → Transform → Validate → Load pipeline.
    Returns the final clean DataFrame and a combined audit report.
    """
    logger.info("=" * 60)
    logger.info("  HEALTHCARE CLAIMS ETL PIPELINE — START")
    logger.info("=" * 60)
    start_time = datetime.now()

    # ── EXTRACT ────────────────────────────────────────────
    raw_df    = generate_claims_dataset(n_claims)
    extractor = ClaimsExtractor()
    raw_df    = extractor.extract_from_dataframe(raw_df, "synthetic_claims_source")

    # ── TRANSFORM ──────────────────────────────────────────
    transformer = ClaimsTransformer()
    clean_df    = transformer.transform(raw_df)

    # ── VALIDATE ───────────────────────────────────────────
    validator    = ClaimsValidator()
    is_valid     = validator.validate(clean_df)
    val_report   = validator.get_report()

    if not is_valid:
        logger.warning("[PIPELINE] Validation failures detected — proceeding with warnings")

    # ── LOAD ───────────────────────────────────────────────
    loader = ClaimsLoader(DB_PATH)
    loader.load_claims(clean_df)
    loader.load_error_flags(clean_df)
    loader.close()

    # ── AUDIT REPORT ───────────────────────────────────────
    elapsed = (datetime.now() - start_time).total_seconds()
    audit   = {
        "pipeline_status":   "SUCCESS" if is_valid else "SUCCESS_WITH_WARNINGS",
        "run_timestamp":     start_time.isoformat(),
        "elapsed_seconds":   round(elapsed, 2),
        "extraction":        extractor.get_extraction_summary(),
        "transformation":    transformer.get_report(),
        "validation":        val_report,
        "output_db":         DB_PATH,
    }

    # Save audit report
    report_path = OUTPUT_DIR / "etl_audit_report.json"
    with open(report_path, "w") as f:
        json.dump(audit, f, indent=2, default=str)

    logger.info(f"[PIPELINE] Completed in {elapsed:.1f}s | Status: {audit['pipeline_status']}")
    logger.info(f"[PIPELINE] Audit report saved to: {report_path}")
    logger.info("=" * 60)

    return clean_df, audit


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    claims_df, report = run_etl_pipeline(n_claims=10_000)
    print(f"\nETL complete. Clean rows: {len(claims_df):,}")
    print(f"Validation: {report['validation']['overall_status']}")
    print(f"Time taken: {report['elapsed_seconds']}s")
