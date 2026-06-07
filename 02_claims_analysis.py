============================================================
 HEALTHCARE CLAIMS ANALYSIS
 File: 02_claims_analysis.py
 Description: Pandas-based analysis — bottleneck detection,
              error patterns, TAT analysis, KPI computation,
              and Power BI–ready CSV exports
============================================================
"""

import pandas as pd
import numpy as np
import sqlite3
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.gridspec import GridSpec
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore")

DB_PATH    = "outputs/claims.db"
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────────────────────
#  SECTION 1: LOAD CLEAN DATA FROM ETL OUTPUT
# ─────────────────────────────────────────────────────────────

def load_clean_data() -> pd.DataFrame:
    """Load the ETL-processed claims from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    df   = pd.read_sql("SELECT * FROM claims_clean", conn,
                       parse_dates=["service_date", "submission_date", "decision_date"])
    conn.close()
    print(f"[LOAD] Loaded {len(df):,} clean claims from database")
    return df


# ─────────────────────────────────────────────────────────────
#  SECTION 2: KPI COMPUTATION ENGINE
# ─────────────────────────────────────────────────────────────

def compute_kpis(df: pd.DataFrame) -> dict:
    """Compute all headline KPIs for the executive dashboard."""
    print("\n[KPI] Computing executive KPIs...")
    total     = len(df)
    approved  = (df["status"] == "Approved").sum()
    denied    = (df["status"] == "Denied").sum()
    pending   = (df["status"] == "Pending").sum()

    kpis = {
        "total_claims":           total,
        "approved_claims":        int(approved),
        "denied_claims":          int(denied),
        "pending_claims":         int(pending),
        "approval_rate_pct":      round(approved / total * 100, 2),
        "denial_rate_pct":        round(denied   / total * 100, 2),
        "pending_rate_pct":       round(pending  / total * 100, 2),

        "avg_tat_days":           round(df["processing_days"].mean(), 1),
        "median_tat_days":        round(df["processing_days"].median(), 1),
        "p90_tat_days":           round(df["processing_days"].quantile(0.90), 1),
        "sla_breach_rate_pct":    round(df["sla_breach"].mean() * 100, 2),

        "total_billed":           round(df["claim_amount"].sum(), 2),
        "total_approved":         round(df["approved_amount"].sum(skipna=True), 2),
        "avg_claim_value":        round(df["claim_amount"].mean(), 2),
        "collection_ratio_pct":   round(
                                    df["approved_amount"].sum(skipna=True) /
                                    df["claim_amount"].sum() * 100, 2),

        "total_financial_leakage":round(df["payment_gap"].sum(skipna=True), 2),
        "icd10_error_rate_pct":   round(df.get("icd10_flag", pd.Series(0)).mean() * 100, 2),
        "unique_patients":        df["patient_id"].nunique(),
        "unique_providers":       df["provider_id"].nunique(),
    }

    for k, v in kpis.items():
        print(f"  {k:<35} {v:>15,}" if isinstance(v, (int, float)) else f"  {k}: {v}")
    return kpis


# ─────────────────────────────────────────────────────────────
#  SECTION 3: BOTTLENECK DETECTION
# ─────────────────────────────────────────────────────────────

def detect_bottlenecks(df: pd.DataFrame) -> pd.DataFrame:
    """
    Identify systemic processing bottlenecks:
    - By provider (slow processing facilities)
    - By payer (slow-paying insurers)
    - By diagnosis category (complex claim types)
    - By claim type
    """
    print("\n[ANALYSIS] Detecting bottlenecks...")

    # ── 3A: Provider-level bottleneck ─────────────────────
    provider_perf = (
        df.groupby(["provider_id", "provider_name", "provider_specialty"])
        .agg(
            total_claims       = ("claim_id", "count"),
            avg_tat_days       = ("processing_days", "mean"),
            median_tat_days    = ("processing_days", "median"),
            sla_breach_count   = ("sla_breach", "sum"),
            denial_rate_pct    = ("status", lambda x: (x == "Denied").mean() * 100),
            avg_claim_amount   = ("claim_amount", "mean"),
        )
        .round(2)
        .reset_index()
        .sort_values("avg_tat_days", ascending=False)
    )
    provider_perf["sla_breach_rate_pct"] = (
        provider_perf["sla_breach_count"] / provider_perf["total_claims"] * 100
    ).round(2)

    # ── 3B: Payer-level bottleneck ────────────────────────
    payer_perf = (
        df.groupby(["payer_id", "payer_name", "payer_type"])
        .agg(
            total_claims       = ("claim_id", "count"),
            avg_tat_days       = ("processing_days", "mean"),
            approval_rate_pct  = ("status", lambda x: (x == "Approved").mean() * 100),
            avg_payment_gap    = ("payment_gap", "mean"),
            total_billed       = ("claim_amount", "sum"),
            total_approved     = ("approved_amount", "sum"),
        )
        .round(2)
        .reset_index()
        .sort_values("avg_tat_days", ascending=False)
    )
    payer_perf["payment_ratio_pct"] = (
        payer_perf["total_approved"] / payer_perf["total_billed"] * 100
    ).round(2)

    # ── 3C: Diagnosis category bottleneck ─────────────────
    diag_perf = (
        df.groupby("diagnosis_category")
        .agg(
            total_claims     = ("claim_id", "count"),
            avg_tat_days     = ("processing_days", "mean"),
            avg_claim_amount = ("claim_amount", "mean"),
            denial_rate_pct  = ("status", lambda x: (x == "Denied").mean() * 100),
            sla_breach_pct   = ("sla_breach", "mean"),
        )
        .round(2)
        .reset_index()
        .sort_values("avg_tat_days", ascending=False)
    )
    diag_perf["sla_breach_pct"] = (diag_perf["sla_breach_pct"] * 100).round(2)

    print(f"  Provider bottlenecks identified: {len(provider_perf)}")
    print(f"  Payer bottlenecks identified:    {len(payer_perf)}")
    print(f"  Diagnosis categories analysed:   {len(diag_perf)}")

    # Export for Power BI
    provider_perf.to_csv(OUTPUT_DIR / "pbi_provider_performance.csv", index=False)
    payer_perf.to_csv(   OUTPUT_DIR / "pbi_payer_performance.csv",    index=False)
    diag_perf.to_csv(    OUTPUT_DIR / "pbi_diagnosis_performance.csv",index=False)

    return provider_perf, payer_perf, diag_perf


# ─────────────────────────────────────────────────────────────
#  SECTION 4: ERROR PATTERN ANALYSIS
# ─────────────────────────────────────────────────────────────

def analyse_error_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deep-dive into claim error patterns to identify root causes.
    Maps errors to financial impact, provider, and claim type.
    """
    print("\n[ANALYSIS] Analysing error patterns...")

    # ── 4A: Denial reason analysis ────────────────────────
    denied_df = df[df["status"] == "Denied"].copy()
    denial_analysis = (
        denied_df.groupby("denial_reason")
        .agg(
            count          = ("claim_id", "count"),
            total_amount   = ("claim_amount", "sum"),
            avg_amount     = ("claim_amount", "mean"),
        )
        .round(2)
        .reset_index()
        .assign(
            pct_of_denials = lambda x: (x["count"] / x["count"].sum() * 100).round(2),
            cumulative_pct = lambda x: x["pct_of_denials"].cumsum().round(2)
        )
        .sort_values("count", ascending=False)
    )
    print("\n  Top Denial Reasons:")
    print(denial_analysis[["denial_reason", "count", "pct_of_denials",
                            "total_amount"]].to_string(index=False))

    # ── 4B: Error pattern by provider ─────────────────────
    error_by_provider = (
        df[df.get("icd10_flag", pd.Series(0)) == 1]
        .groupby(["provider_name", "provider_specialty"])
        .agg(error_count=("claim_id", "count"))
        .reset_index()
        .sort_values("error_count", ascending=False)
    )

    # ── 4C: Denial trend by month ─────────────────────────
    denial_trend = (
        denied_df.groupby("year_month")
        .agg(
            denials          = ("claim_id", "count"),
            denied_amount    = ("claim_amount", "sum"),
        )
        .reset_index()
        .sort_values("year_month")
    )

    # ── 4D: Pareto — top 20% of denial reasons → 80% of impact
    pareto_threshold = denial_analysis[
        denial_analysis["cumulative_pct"] <= 80
    ]
    print(f"\n  Pareto: {len(pareto_threshold)} denial reasons = 80% of all denials")

    # Export
    denial_analysis.to_csv(  OUTPUT_DIR / "pbi_denial_analysis.csv",   index=False)
    error_by_provider.to_csv(OUTPUT_DIR / "pbi_errors_by_provider.csv",index=False)
    denial_trend.to_csv(     OUTPUT_DIR / "pbi_denial_trend.csv",       index=False)

    return denial_analysis, error_by_provider, denial_trend


# ─────────────────────────────────────────────────────────────
#  SECTION 5: TURNAROUND TIME (TAT) DEEP DIVE
# ─────────────────────────────────────────────────────────────

def analyse_tat(df: pd.DataFrame) -> pd.DataFrame:
    """
    Comprehensive TAT analysis with percentiles, SLA compliance,
    and trend decomposition.
    """
    print("\n[ANALYSIS] Turnaround time deep dive...")

    tat_df = df[df["processing_days"].notna()].copy()

    # ── 5A: TAT distribution by claim type + status ───────
    tat_matrix = (
        tat_df.groupby(["claim_type", "status"])
        .agg(
            count       = ("claim_id", "count"),
            avg_tat     = ("processing_days", "mean"),
            median_tat  = ("processing_days", "median"),
            p90_tat     = ("processing_days", lambda x: x.quantile(0.90)),
            max_tat     = ("processing_days", "max"),
        )
        .round(1)
        .reset_index()
    )

    # ── 5B: Monthly TAT trend ─────────────────────────────
    monthly_tat = (
        tat_df.groupby("year_month")
        .agg(
            avg_tat         = ("processing_days", "mean"),
            median_tat      = ("processing_days", "median"),
            sla_breach_rate = ("sla_breach", "mean"),
            total_claims    = ("claim_id", "count"),
        )
        .round(2)
        .reset_index()
    )
    monthly_tat["sla_breach_rate_pct"] = (monthly_tat["sla_breach_rate"] * 100).round(2)
    monthly_tat["rolling_avg_tat"] = (
        monthly_tat["avg_tat"].rolling(window=3, min_periods=1).mean().round(2)
    )

    # ── 5C: Claims processed within 10/20/30 days ─────────
    tat_buckets = pd.cut(
        tat_df["processing_days"],
        bins=[-1, 10, 20, 30, 45, np.inf],
        labels=["≤10d", "11-20d", "21-30d", "31-45d", "45d+"]
    ).value_counts().sort_index()

    print("\n  TAT Distribution:")
    for bucket, count in tat_buckets.items():
        pct = count / len(tat_df) * 100
        bar = "█" * int(pct // 2)
        print(f"  {str(bucket):<8} {bar:<25} {count:>6,} ({pct:>5.1f}%)")

    # ── 5D: SLA compliance by claim type ──────────────────
    sla_compliance = (
        tat_df.groupby("claim_type")
        .apply(lambda g: pd.Series({
            "total":          len(g),
            "within_sla":     (g["sla_breach"] == 0).sum(),
            "sla_breach":     (g["sla_breach"] == 1).sum(),
            "compliance_pct": round((g["sla_breach"] == 0).mean() * 100, 2),
        }))
        .reset_index()
    )
    print("\n  SLA Compliance by Claim Type:")
    print(sla_compliance.to_string(index=False))

    # Export
    tat_matrix.to_csv(    OUTPUT_DIR / "pbi_tat_matrix.csv",     index=False)
    monthly_tat.to_csv(   OUTPUT_DIR / "pbi_monthly_tat.csv",    index=False)
    sla_compliance.to_csv(OUTPUT_DIR / "pbi_sla_compliance.csv", index=False)

    return tat_matrix, monthly_tat, sla_compliance


# ─────────────────────────────────────────────────────────────
#  SECTION 6: FINANCIAL LEAKAGE ANALYSIS
# ─────────────────────────────────────────────────────────────

def analyse_financial_leakage(df: pd.DataFrame) -> pd.DataFrame:
    """
    Quantify revenue at risk from underpayments, denials,
    and pending claims.
    """
    print("\n[ANALYSIS] Financial leakage analysis...")

    # ── 6A: Underpayments (approved < 80% of billed) ──────
    approved_df = df[df["status"] == "Approved"].copy()
    underpaid   = approved_df[
        approved_df["payment_gap_pct"].notna() &
        (approved_df["payment_gap_pct"] > 20)
    ]
    underpaid_by_payer = (
        underpaid.groupby("payer_name")
        .agg(
            claims       = ("claim_id", "count"),
            total_gap    = ("payment_gap", "sum"),
            avg_gap_pct  = ("payment_gap_pct", "mean"),
        )
        .round(2)
        .reset_index()
        .sort_values("total_gap", ascending=False)
    )

    # ── 6B: Revenue at risk (pending claims) ──────────────
    pending_risk = (
        df[df["status"] == "Pending"]
        .groupby("claim_risk_tier")
        .agg(
            count          = ("claim_id", "count"),
            total_at_risk  = ("claim_amount", "sum"),
            avg_pending_days = ("processing_days", "mean"),
        )
        .round(2)
        .reset_index()
    )

    # ── 6C: Total leakage summary ─────────────────────────
    total_billed       = df["claim_amount"].sum()
    total_approved_amt = df["approved_amount"].sum(skipna=True)
    total_denied_amt   = df[df["status"] == "Denied"]["claim_amount"].sum()
    total_pending_amt  = df[df["status"] == "Pending"]["claim_amount"].sum()
    total_underpaid    = underpaid["payment_gap"].sum()

    print(f"\n  Financial Summary:")
    print(f"  {'Total Billed:':<35} ${total_billed:>15,.2f}")
    print(f"  {'Total Approved:':<35} ${total_approved_amt:>15,.2f}")
    print(f"  {'Denied (at risk):':<35} ${total_denied_amt:>15,.2f}")
    print(f"  {'Pending (at risk):':<35} ${total_pending_amt:>15,.2f}")
    print(f"  {'Underpayment Gap:':<35} ${total_underpaid:>15,.2f}")
    print(f"  {'Collection Ratio:':<35}  {total_approved_amt/total_billed*100:>14.1f}%")

    # Export
    underpaid_by_payer.to_csv(OUTPUT_DIR / "pbi_financial_leakage.csv", index=False)
    pending_risk.to_csv(      OUTPUT_DIR / "pbi_pending_risk.csv",       index=False)

    return underpaid_by_payer, pending_risk


# ─────────────────────────────────────────────────────────────
#  SECTION 7: VISUALISATIONS (Power BI Preview Charts)
# ─────────────────────────────────────────────────────────────

def create_dashboard_charts(df: pd.DataFrame, kpis: dict):
    """
    Generate a 6-panel summary dashboard saved as PNG.
    These mirror what a Power BI report would display.
    """
    print("\n[CHARTS] Building dashboard visualisations...")

    DARK_BG   = "#0F1117"
    CARD_BG   = "#1A1D27"
    ACCENT    = "#00D4FF"
    GREEN     = "#2ECC71"
    RED       = "#E74C3C"
    YELLOW    = "#F39C12"
    TEXT      = "#E8ECF0"
    GRID      = "#2A2D3A"

    fig = plt.figure(figsize=(20, 14), facecolor=DARK_BG)
    gs  = GridSpec(3, 3, figure=fig, hspace=0.45, wspace=0.35)
    fig.suptitle("Healthcare Claims Analytics Dashboard",
                 fontsize=20, color=TEXT, fontweight="bold", y=0.98)

    # ── Chart 1: Claim Status Pie ──────────────────────────
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_facecolor(CARD_BG)
    status_counts = df["status"].value_counts()
    colors = [GREEN, RED, YELLOW, ACCENT]
    wedges, texts, autotexts = ax1.pie(
        status_counts.values,
        labels=status_counts.index,
        autopct="%1.1f%%",
        colors=colors[:len(status_counts)],
        startangle=140,
        textprops={"color": TEXT, "fontsize": 9}
    )
    for at in autotexts:
        at.set_color(DARK_BG)
        at.set_fontweight("bold")
    ax1.set_title("Claim Status Distribution", color=TEXT, fontsize=11, pad=10)

    # ── Chart 2: Monthly Volume + Approval Rate ────────────
    ax2 = fig.add_subplot(gs[0, 1:])
    ax2.set_facecolor(CARD_BG)
    monthly = (
        df.groupby("year_month")
        .agg(total=("claim_id", "count"),
             approved=("status", lambda x: (x == "Approved").sum()))
        .reset_index()
        .tail(18)
    )
    monthly["approval_rate"] = monthly["approved"] / monthly["total"] * 100
    x = range(len(monthly))
    bars = ax2.bar(x, monthly["total"], color=ACCENT, alpha=0.6, label="Total Claims")
    ax2_twin = ax2.twinx()
    ax2_twin.plot(x, monthly["approval_rate"], color=GREEN, linewidth=2.5,
                  marker="o", markersize=5, label="Approval Rate %")
    ax2_twin.set_ylim(0, 110)
    ax2_twin.tick_params(colors=TEXT)
    ax2_twin.set_ylabel("Approval Rate (%)", color=GREEN, fontsize=9)
    ax2.set_xticks(x)
    ax2.set_xticklabels(monthly["year_month"], rotation=45, ha="right",
                        fontsize=7, color=TEXT)
    ax2.tick_params(colors=TEXT)
    ax2.set_facecolor(CARD_BG)
    ax2.set_ylabel("Claim Volume", color=TEXT, fontsize=9)
    ax2.set_title("Monthly Claim Volume & Approval Rate", color=TEXT, fontsize=11)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax2.grid(axis="y", color=GRID, alpha=0.5)
    fig.legend(loc="upper right", bbox_to_anchor=(0.99, 0.93),
               facecolor=CARD_BG, edgecolor=GRID, labelcolor=TEXT, fontsize=8)

    # ── Chart 3: TAT by Claim Type ────────────────────────
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.set_facecolor(CARD_BG)
    tat_by_type = (
        df[df["processing_days"].notna()]
        .groupby("claim_type")["processing_days"]
        .median()
        .sort_values(ascending=True)
    )
    bars3 = ax3.barh(tat_by_type.index, tat_by_type.values, color=ACCENT, alpha=0.8)
    for bar, val in zip(bars3, tat_by_type.values):
        ax3.text(val + 0.3, bar.get_y() + bar.get_height() / 2,
                 f"{val:.0f}d", va="center", color=TEXT, fontsize=9)
    ax3.set_xlabel("Median TAT (Days)", color=TEXT, fontsize=9)
    ax3.set_title("Median TAT by Claim Type", color=TEXT, fontsize=11)
    ax3.tick_params(colors=TEXT)
    ax3.set_facecolor(CARD_BG)
    ax3.grid(axis="x", color=GRID, alpha=0.5)

    # ── Chart 4: Denial Reasons Bar ───────────────────────
    ax4 = fig.add_subplot(gs[1, 1:])
    ax4.set_facecolor(CARD_BG)
    denial_df = (
        df[df["status"] == "Denied"]["denial_reason"]
        .value_counts()
        .head(6)
        .sort_values(ascending=True)
    )
    bars4 = ax4.barh(denial_df.index, denial_df.values, color=RED, alpha=0.8)
    for bar, val in zip(bars4, denial_df.values):
        ax4.text(val + 20, bar.get_y() + bar.get_height() / 2,
                 f"{val:,}", va="center", color=TEXT, fontsize=9)
    ax4.set_title("Top Denial Reasons", color=TEXT, fontsize=11)
    ax4.tick_params(colors=TEXT)
    ax4.set_facecolor(CARD_BG)
    ax4.grid(axis="x", color=GRID, alpha=0.5)
    # Wrap long labels
    labels = [l.get_text()[:35] for l in ax4.get_yticklabels()]
    ax4.set_yticklabels(labels, fontsize=8)

    # ── Chart 5: SLA Breach Rate by Claim Type ────────────
    ax5 = fig.add_subplot(gs[2, 0])
    ax5.set_facecolor(CARD_BG)
    sla_df = (
        df.groupby("claim_type")["sla_breach"]
        .mean()
        .mul(100)
        .sort_values(ascending=False)
    )
    bar_colors = [RED if v > 30 else YELLOW if v > 15 else GREEN for v in sla_df.values]
    bars5 = ax5.bar(sla_df.index, sla_df.values, color=bar_colors, alpha=0.85)
    for bar, val in zip(bars5, sla_df.values):
        ax5.text(bar.get_x() + bar.get_width() / 2, val + 0.5,
                 f"{val:.1f}%", ha="center", color=TEXT, fontsize=9)
    ax5.set_ylabel("SLA Breach Rate (%)", color=TEXT, fontsize=9)
    ax5.set_title("SLA Breach Rate by Claim Type", color=TEXT, fontsize=11)
    ax5.tick_params(colors=TEXT)
    ax5.set_facecolor(CARD_BG)
    ax5.grid(axis="y", color=GRID, alpha=0.5)

    # ── Chart 6: Financial Leakage by Payer ───────────────
    ax6 = fig.add_subplot(gs[2, 1:])
    ax6.set_facecolor(CARD_BG)
    payer_leakage = (
        df[df["status"] == "Denied"]
        .groupby("payer_name")["claim_amount"]
        .sum()
        .sort_values(ascending=True)
    )
    bars6 = ax6.barh(payer_leakage.index, payer_leakage.values,
                     color=YELLOW, alpha=0.8)
    for bar, val in zip(bars6, payer_leakage.values):
        ax6.text(val + 5000, bar.get_y() + bar.get_height() / 2,
                 f"${val:,.0f}", va="center", color=TEXT, fontsize=9)
    ax6.set_title("Denied Claim Value by Payer", color=TEXT, fontsize=11)
    ax6.tick_params(colors=TEXT)
    ax6.set_facecolor(CARD_BG)
    ax6.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x/1e6:.1f}M"))
    ax6.grid(axis="x", color=GRID, alpha=0.5)

    # ── KPI Annotations ───────────────────────────────────
    fig.text(0.01, 0.01,
             f"Total Claims: {kpis['total_claims']:,}  |  "
             f"Approval Rate: {kpis['approval_rate_pct']}%  |  "
             f"Avg TAT: {kpis['avg_tat_days']}d  |  "
             f"SLA Breach: {kpis['sla_breach_rate_pct']}%  |  "
             f"Collection Ratio: {kpis['collection_ratio_pct']}%  |  "
             f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             color="#666B7A", fontsize=8)

    chart_path = OUTPUT_DIR / "claims_dashboard.png"
    plt.savefig(chart_path, dpi=150, bbox_inches="tight",
                facecolor=DARK_BG, edgecolor="none")
    plt.close()
    print(f"  ✓ Dashboard saved to: {chart_path}")
    return chart_path


# ─────────────────────────────────────────────────────────────
#  SECTION 8: POWER BI EXPORT — MASTER FLAT FILE
# ─────────────────────────────────────────────────────────────

def export_powerbi_master(df: pd.DataFrame):
    """
    Export a denormalised master CSV optimised for Power BI import.
    Includes all dimensions, measures, and pre-calculated flags.
    """
    print("\n[EXPORT] Generating Power BI master dataset...")

    cols = [
        # Dimensions
        "claim_id", "patient_id", "provider_id", "provider_name",
        "provider_specialty", "provider_state", "payer_id", "payer_name",
        "payer_type", "icd10_code", "diagnosis", "diagnosis_category",
        "claim_type", "status", "denial_reason",
        # Dates
        "service_date", "submission_date", "decision_date", "year_month",
        # Measures
        "claim_amount", "approved_amount", "payment_gap", "payment_gap_pct",
        # Derived
        "processing_days", "submission_lag_days", "sla_threshold",
        "sla_breach", "claim_risk_tier", "pending_age_bucket",
    ]
    export_cols = [c for c in cols if c in df.columns]
    export_df   = df[export_cols].copy()

    # Format dates
    for d in ["service_date", "submission_date", "decision_date"]:
        if d in export_df.columns:
            export_df[d] = export_df[d].dt.strftime("%Y-%m-%d")

    path = OUTPUT_DIR / "pbi_master_claims.csv"
    export_df.to_csv(path, index=False)
    print(f"  ✓ Power BI master file: {path} ({len(export_df):,} rows, {len(export_cols)} cols)")
    return path


# ─────────────────────────────────────────────────────────────
#  SECTION 9: MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────

def run_analysis():
    print("=" * 60)
    print("  HEALTHCARE CLAIMS ANALYSIS ENGINE — START")
    print("=" * 60)

    # Load
    df = load_clean_data()

    # Analyse
    kpis                                     = compute_kpis(df)
    provider_perf, payer_perf, diag_perf     = detect_bottlenecks(df)
    denial_analysis, error_by_prov, den_trend = analyse_error_patterns(df)
    tat_matrix, monthly_tat, sla_compliance  = analyse_tat(df)
    leakage_df, pending_risk                 = analyse_financial_leakage(df)

    # Visualise
    chart_path = create_dashboard_charts(df, kpis)

    # Export master for Power BI
    pbi_path = export_powerbi_master(df)

    print("\n" + "=" * 60)
    print("  ANALYSIS COMPLETE")
    print(f"  Output files in: {OUTPUT_DIR.resolve()}")
    print("=" * 60)
    return df, kpis


if __name__ == "__main__":
    df, kpis = run_analysis()
