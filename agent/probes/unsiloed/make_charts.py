"""
make_charts.py — generates chart/PDF fixtures for the Unsiloed document-parsing probe.
Run with: /Users/tk/Desktop/conv-agent/agent/.venv/bin/python make_charts.py
"""

import json
import os

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — MUST come before pyplot import

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
FIXTURES_DIR = "/Users/tk/Desktop/conv-agent/agent/probes/unsiloed/fixtures"
os.makedirs(FIXTURES_DIR, exist_ok=True)

SAVE_OPTS = dict(dpi=150, bbox_inches="tight")


def fp(name: str) -> str:
    return os.path.join(FIXTURES_DIR, name)


# ---------------------------------------------------------------------------
# 1. bar_sales.png
# ---------------------------------------------------------------------------
def make_bar_sales():
    quarters = ["Q1", "Q2", "Q3", "Q4"]
    revenue = [12.4, 18.9, 15.2, 23.7]

    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar(quarters, revenue, color=["#4C72B0", "#DD8452", "#55A868", "#C44E52"], width=0.55)

    for bar, val in zip(bars, revenue):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.3,
            f"{val}",
            ha="center", va="bottom", fontsize=12, fontweight="bold"
        )

    ax.set_title("Quarterly Revenue 2025 (USD millions)", fontsize=14, fontweight="bold", pad=12)
    ax.set_xlabel("Quarter", fontsize=12)
    ax.set_ylabel("Revenue (USD millions)", fontsize=12)
    ax.set_ylim(0, 28)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(fp("bar_sales.png"), **SAVE_OPTS)
    plt.close(fig)
    print("  bar_sales.png done")


# ---------------------------------------------------------------------------
# 2. line_trend.png
# ---------------------------------------------------------------------------
def make_line_trend():
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    free_users = [120, 135, 150, 162, 158, 175]
    pro_users  = [30,  42,  55,  70,  88,  110]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(months, free_users, marker="o", linewidth=2, color="#4C72B0", label="Free")
    ax.plot(months, pro_users,  marker="s", linewidth=2, color="#DD8452", label="Pro")

    ax.set_title("Monthly Active Users (thousands)", fontsize=14, fontweight="bold", pad=12)
    ax.set_xlabel("Month", fontsize=12)
    ax.set_ylabel("Users (thousands)", fontsize=12)
    ax.legend(fontsize=11, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(fp("line_trend.png"), **SAVE_OPTS)
    plt.close(fig)
    print("  line_trend.png done")


# ---------------------------------------------------------------------------
# 3. pie_share.png
# ---------------------------------------------------------------------------
def make_pie_share():
    labels = ["Organic", "Paid", "Referral", "Social"]
    sizes  = [45, 25, 18, 12]
    colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]
    explode = (0.05, 0.05, 0.05, 0.05)

    fig, ax = plt.subplots(figsize=(7, 6))
    wedges, texts, autotexts = ax.pie(
        sizes,
        labels=labels,
        colors=colors,
        explode=explode,
        autopct="%1.0f%%",
        startangle=90,
        textprops={"fontsize": 12},
    )
    for at in autotexts:
        at.set_fontsize(12)
        at.set_fontweight("bold")

    ax.set_title("Traffic Sources", fontsize=14, fontweight="bold", pad=16)
    fig.tight_layout()
    fig.savefig(fp("pie_share.png"), **SAVE_OPTS)
    plt.close(fig)
    print("  pie_share.png done")


# ---------------------------------------------------------------------------
# 4. grouped_bar.png
# ---------------------------------------------------------------------------
def make_grouped_bar():
    categories = ["Eng", "Sales", "Support"]
    y2024 = [40, 25, 15]
    y2025 = [62, 30, 22]

    x = np.arange(len(categories))
    width = 0.35

    fig, ax = plt.subplots(figsize=(8, 5))
    bars1 = ax.bar(x - width / 2, y2024, width, label="2024", color="#4C72B0")
    bars2 = ax.bar(x + width / 2, y2025, width, label="2025", color="#DD8452")

    for bar, val in zip(bars1, y2024):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.5,
            str(val), ha="center", va="bottom", fontsize=11, fontweight="bold"
        )
    for bar, val in zip(bars2, y2025):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.5,
            str(val), ha="center", va="bottom", fontsize=11, fontweight="bold"
        )

    ax.set_title("Headcount by Department & Year", fontsize=14, fontweight="bold", pad=12)
    ax.set_xlabel("Department", fontsize=12)
    ax.set_ylabel("Headcount", fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=12)
    ax.legend(fontsize=11)
    ax.set_ylim(0, 75)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(fp("grouped_bar.png"), **SAVE_OPTS)
    plt.close(fig)
    print("  grouped_bar.png done")


# ---------------------------------------------------------------------------
# 5. scatter_corr.png
# ---------------------------------------------------------------------------
def make_scatter_corr():
    np.random.seed(0)
    n = 40
    x = np.linspace(0, 50, n)
    slope = 3
    y = slope * x + np.random.normal(0, 15, n)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(x, y, color="#4C72B0", alpha=0.75, edgecolors="white", linewidths=0.5, s=60)

    # Fit line for visual
    m, b = np.polyfit(x, y, 1)
    x_line = np.array([x.min(), x.max()])
    ax.plot(x_line, m * x_line + b, color="#C44E52", linewidth=2, linestyle="--", label=f"Fit (slope≈{m:.1f})")

    ax.set_title("Ad Spend vs. Signups", fontsize=14, fontweight="bold", pad=12)
    ax.set_xlabel("Ad Spend (USD k)", fontsize=12)
    ax.set_ylabel("Signups", fontsize=12)
    ax.legend(fontsize=11)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(fp("scatter_corr.png"), **SAVE_OPTS)
    plt.close(fig)
    print("  scatter_corr.png done")


# ---------------------------------------------------------------------------
# 6. stacked_bar.png
# ---------------------------------------------------------------------------
def make_stacked_bar():
    quarters   = ["Q1", "Q2", "Q3", "Q4"]
    salaries   = [50, 52, 55, 58]
    marketing  = [20, 28, 22, 35]
    ops        = [10, 12, 11, 14]

    x = np.arange(len(quarters))
    width = 0.5

    fig, ax = plt.subplots(figsize=(8, 5))
    p1 = ax.bar(x, salaries,  width, label="Salaries",  color="#4C72B0")
    p2 = ax.bar(x, marketing, width, label="Marketing", color="#DD8452",
                bottom=salaries)
    p3 = ax.bar(x, ops,       width, label="Ops",       color="#55A868",
                bottom=[s + m for s, m in zip(salaries, marketing)])

    ax.set_title("Expenses by Category per Quarter", fontsize=14, fontweight="bold", pad=12)
    ax.set_xlabel("Quarter", fontsize=12)
    ax.set_ylabel("Expenses (USD thousands)", fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels(quarters, fontsize=12)
    ax.legend(fontsize=11, loc="upper left")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(fp("stacked_bar.png"), **SAVE_OPTS)
    plt.close(fig)
    print("  stacked_bar.png done")


# ---------------------------------------------------------------------------
# 7. dashboard.png  (bar chart + table side by side)
# ---------------------------------------------------------------------------
def make_dashboard():
    quarters = ["Q1", "Q2", "Q3", "Q4"]
    revenue  = [12.4, 18.9, 15.2, 23.7]

    table_cols = ["Quarter", "Revenue (USD M)", "Growth %"]
    table_rows = [
        ["Q1", "12.4", "—"],
        ["Q2", "18.9", "52%"],
        ["Q3", "15.2", "-20%"],
        ["Q4", "23.7", "56%"],
    ]

    fig, (ax_chart, ax_table) = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("Revenue Dashboard", fontsize=16, fontweight="bold", y=1.02)

    # Left: bar chart
    bars = ax_chart.bar(quarters, revenue,
                        color=["#4C72B0", "#DD8452", "#55A868", "#C44E52"], width=0.55)
    for bar, val in zip(bars, revenue):
        ax_chart.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.3,
            f"{val}", ha="center", va="bottom", fontsize=11, fontweight="bold"
        )
    ax_chart.set_title("Quarterly Revenue 2025", fontsize=12, fontweight="bold")
    ax_chart.set_xlabel("Quarter", fontsize=11)
    ax_chart.set_ylabel("Revenue (USD millions)", fontsize=11)
    ax_chart.set_ylim(0, 28)
    ax_chart.grid(axis="y", alpha=0.3)

    # Right: data table
    ax_table.axis("off")
    tbl = ax_table.table(
        cellText=table_rows,
        colLabels=table_cols,
        loc="center",
        cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(12)
    tbl.scale(1.2, 1.8)

    # Style header row
    for col_idx in range(len(table_cols)):
        tbl[0, col_idx].set_facecolor("#4C72B0")
        tbl[0, col_idx].set_text_props(color="white", fontweight="bold")

    ax_table.set_title("Revenue Summary", fontsize=12, fontweight="bold", pad=12)

    fig.tight_layout()
    fig.savefig(fp("dashboard.png"), **SAVE_OPTS)
    plt.close(fig)
    print("  dashboard.png done")


# ---------------------------------------------------------------------------
# 8. report.pdf  (2-page PDF)
# ---------------------------------------------------------------------------
def make_report_pdf():
    months     = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    free_users = [120, 135, 150, 162, 158, 175]
    pro_users  = [30,  42,  55,  70,  88,  110]

    quarters   = ["Q1", "Q2", "Q3", "Q4"]
    salaries   = [50, 52, 55, 58]
    marketing  = [20, 28, 22, 35]
    ops        = [10, 12, 11, 14]

    with PdfPages(fp("report.pdf")) as pdf:
        # --- Page 1: line_trend chart with heading text ---
        fig = plt.figure(figsize=(9, 6))
        fig.text(0.5, 0.97,
                 "Clarion Analytics Report — Monthly Active Users",
                 ha="center", va="top", fontsize=15, fontweight="bold")

        ax = fig.add_axes([0.1, 0.12, 0.85, 0.78])
        ax.plot(months, free_users, marker="o", linewidth=2, color="#4C72B0", label="Free")
        ax.plot(months, pro_users,  marker="s", linewidth=2, color="#DD8452", label="Pro")
        ax.set_title("Monthly Active Users (thousands)", fontsize=13, fontweight="bold", pad=10)
        ax.set_xlabel("Month", fontsize=12)
        ax.set_ylabel("Users (thousands)", fontsize=12)
        ax.legend(fontsize=11, loc="upper left")
        ax.grid(alpha=0.3)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # --- Page 2: stacked_bar chart + data table below ---
        fig, axes = plt.subplots(2, 1, figsize=(9, 10),
                                 gridspec_kw={"height_ratios": [3, 1]})
        fig.suptitle("Clarion Analytics Report — Expense Breakdown", fontsize=15, fontweight="bold")

        ax_bar = axes[0]
        x = np.arange(len(quarters))
        width = 0.5
        ax_bar.bar(x, salaries,  width, label="Salaries",  color="#4C72B0")
        ax_bar.bar(x, marketing, width, label="Marketing", color="#DD8452",
                   bottom=salaries)
        ax_bar.bar(x, ops,       width, label="Ops",       color="#55A868",
                   bottom=[s + m for s, m in zip(salaries, marketing)])
        ax_bar.set_title("Expenses by Category per Quarter", fontsize=12, fontweight="bold")
        ax_bar.set_xlabel("Quarter", fontsize=11)
        ax_bar.set_ylabel("Expenses (USD thousands)", fontsize=11)
        ax_bar.set_xticks(x)
        ax_bar.set_xticklabels(quarters, fontsize=11)
        ax_bar.legend(fontsize=10, loc="upper left")
        ax_bar.grid(axis="y", alpha=0.3)

        ax_tbl = axes[1]
        ax_tbl.axis("off")
        tbl_data = [
            ["Salaries",  "50", "52", "55", "58"],
            ["Marketing", "20", "28", "22", "35"],
            ["Ops",       "10", "12", "11", "14"],
        ]
        tbl_cols = ["Category", "Q1", "Q2", "Q3", "Q4"]
        tbl = ax_tbl.table(
            cellText=tbl_data,
            colLabels=tbl_cols,
            loc="center",
            cellLoc="center",
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(11)
        tbl.scale(1, 1.6)
        for col_idx in range(len(tbl_cols)):
            tbl[0, col_idx].set_facecolor("#4C72B0")
            tbl[0, col_idx].set_text_props(color="white", fontweight="bold")

        ax_tbl.set_title("Expense Data (USD thousands)", fontsize=11, fontweight="bold", pad=8)

        fig.tight_layout(rect=[0, 0, 1, 0.96])
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

    print("  report.pdf done")


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------
GROUND_TRUTH = {
    "bar_sales.png": {
        "chart_type": "vertical_bar",
        "title": "Quarterly Revenue 2025 (USD millions)",
        "data": {
            "x_label": "Quarter",
            "y_label": "Revenue (USD millions)",
            "categories": ["Q1", "Q2", "Q3", "Q4"],
            "values": [12.4, 18.9, 15.2, 23.7]
        },
        "expect": "Report four bars with values 12.4, 18.9, 15.2, 23.7 for Q1-Q4 respectively; y-axis labelled 'Revenue (USD millions)'."
    },
    "line_trend.png": {
        "chart_type": "multi_series_line",
        "title": "Monthly Active Users (thousands)",
        "data": {
            "x_label": "Month",
            "y_label": "Users (thousands)",
            "x_categories": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
            "series": {
                "Free": [120, 135, 150, 162, 158, 175],
                "Pro":  [30,  42,  55,  70,  88,  110]
            }
        },
        "expect": "Two series 'Free' and 'Pro' over Jan-Jun; Free ends at 175, Pro ends at 110 (thousands)."
    },
    "pie_share.png": {
        "chart_type": "pie",
        "title": "Traffic Sources",
        "data": {
            "slices": {
                "Organic":  45,
                "Paid":     25,
                "Referral": 18,
                "Social":   12
            },
            "unit": "percentage",
            "total": 100
        },
        "expect": "Pie with four slices: Organic 45%, Paid 25%, Referral 18%, Social 12%."
    },
    "grouped_bar.png": {
        "chart_type": "grouped_bar",
        "title": "Headcount by Department & Year",
        "data": {
            "x_label": "Department",
            "y_label": "Headcount",
            "categories": ["Eng", "Sales", "Support"],
            "series": {
                "2024": [40, 25, 15],
                "2025": [62, 30, 22]
            }
        },
        "expect": "Six bars (3 departments x 2 years); Eng grew from 40 to 62, Sales 25→30, Support 15→22."
    },
    "scatter_corr.png": {
        "chart_type": "scatter",
        "title": "Ad Spend vs. Signups",
        "data": {
            "x_label": "Ad Spend (USD k)",
            "y_label": "Signups",
            "numpy_seed": 0,
            "true_slope": 3,
            "n_points": 40,
            "x_range": [0, 50],
            "noise_std": 15,
            "relationship": "positive linear correlation"
        },
        "expect": "40 scattered points showing a positive trend; slope approximately 3; a dashed fit line shown."
    },
    "stacked_bar.png": {
        "chart_type": "stacked_bar",
        "title": "Expenses by Category per Quarter",
        "data": {
            "x_label": "Quarter",
            "y_label": "Expenses (USD thousands)",
            "categories": ["Q1", "Q2", "Q3", "Q4"],
            "series": {
                "Salaries":  [50, 52, 55, 58],
                "Marketing": [20, 28, 22, 35],
                "Ops":       [10, 12, 11, 14]
            }
        },
        "expect": "Three stacked segments per quarter; Q4 total = 107 (Salaries 58 + Marketing 35 + Ops 14)."
    },
    "dashboard.png": {
        "chart_type": "mixed_bar_and_table",
        "title": "Revenue Dashboard",
        "data": {
            "bar_chart": {
                "title": "Quarterly Revenue 2025",
                "categories": ["Q1", "Q2", "Q3", "Q4"],
                "values": [12.4, 18.9, 15.2, 23.7],
                "y_label": "Revenue (USD millions)"
            },
            "table": {
                "columns": ["Quarter", "Revenue (USD M)", "Growth %"],
                "rows": [
                    ["Q1", "12.4", "—"],
                    ["Q2", "18.9", "52%"],
                    ["Q3", "15.2", "-20%"],
                    ["Q4", "23.7", "56%"]
                ]
            }
        },
        "expect": "Left half: bar chart with four bars; right half: a 4-row table with columns Quarter, Revenue, Growth %; Q2 shows 52% growth, Q3 -20%, Q4 56%."
    },
    "report.pdf": {
        "chart_type": "pdf_two_page",
        "title": "Clarion Analytics Report",
        "data": {
            "page_1": {
                "chart": "multi_series_line",
                "title": "Monthly Active Users (thousands)",
                "heading": "Clarion Analytics Report — Monthly Active Users",
                "series": {
                    "Free": [120, 135, 150, 162, 158, 175],
                    "Pro":  [30,  42,  55,  70,  88,  110]
                },
                "x_categories": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
            },
            "page_2": {
                "chart": "stacked_bar",
                "title": "Expenses by Category per Quarter",
                "heading": "Clarion Analytics Report — Expense Breakdown",
                "series": {
                    "Salaries":  [50, 52, 55, 58],
                    "Marketing": [20, 28, 22, 35],
                    "Ops":       [10, 12, 11, 14]
                },
                "x_categories": ["Q1", "Q2", "Q3", "Q4"],
                "table": {
                    "columns": ["Category", "Q1", "Q2", "Q3", "Q4"],
                    "rows": [
                        ["Salaries",  50, 52, 55, 58],
                        ["Marketing", 20, 28, 22, 35],
                        ["Ops",       10, 12, 11, 14]
                    ]
                }
            }
        },
        "expect": "Two-page PDF: page 1 has a line chart with Free/Pro series; page 2 has a stacked bar chart and a 3-row data table of expense values."
    }
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Generating fixtures...")
    make_bar_sales()
    make_line_trend()
    make_pie_share()
    make_grouped_bar()
    make_scatter_corr()
    make_stacked_bar()
    make_dashboard()
    make_report_pdf()

    gt_path = os.path.join(FIXTURES_DIR, "ground_truth.json")
    with open(gt_path, "w") as f:
        json.dump(GROUND_TRUTH, f, indent=2)
    print(f"  ground_truth.json written → {gt_path}")

    print("\nAll fixtures generated.")


if __name__ == "__main__":
    main()
