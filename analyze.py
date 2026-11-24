#!/usr/bin/env python3
"""Analyze Claude Code governance CSV reports and generate visual reports."""

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import seaborn as sns

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports"
FIGURES_DIR = ROOT / "figures"
TRACKER_FILE = ROOT / ".analysis_tracker.json"
HOME_PREFIX = str(Path.home()) + "/"

sns.set_theme(style="whitegrid", palette="muted")
plt.rcParams.update({"figure.dpi": 150, "savefig.bbox": "tight"})


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def load_tracker() -> dict:
    if TRACKER_FILE.exists():
        return json.loads(TRACKER_FILE.read_text())
    return {}


def save_tracker(tracker: dict):
    TRACKER_FILE.write_text(json.dumps(tracker, indent=2))


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["tool_name"] = df["tool_name"].fillna("-")
    df["event"] = df["event"].fillna("Unknown")
    df["permission_mode"] = df["permission_mode"].fillna("-")
    df["cwd"] = df["cwd"].fillna("-")
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def extract_project(cwd: str) -> str:
    """Extract a short project name from a cwd path."""
    if cwd == "-":
        return "(unknown)"
    parts = cwd.replace(HOME_PREFIX, "").split("/")
    # Return up to 2 levels deep for meaningful grouping
    return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]


def compute_tool_latencies(df: pd.DataFrame) -> pd.DataFrame:
    """Match PreToolUse → PostToolUse/PostToolUseFailure pairs and compute latency."""
    rows = []
    pending: dict[str, tuple] = {}  # session_id -> (timestamp, tool_name, input_summary)
    for _, row in df.iterrows():
        sid = row["session_id"]
        if row["event"] == "PreToolUse":
            pending[sid] = (row["timestamp"], row["tool_name"], row["input_summary"])
        elif row["event"] in ("PostToolUse", "PostToolUseFailure") and sid in pending:
            start_ts, tool, inp = pending.pop(sid)
            delta = (row["timestamp"] - start_ts).total_seconds()
            if 0 <= delta < 300:  # ignore implausible gaps
                rows.append({
                    "tool_name": tool,
                    "latency_s": delta,
                    "failed": row["event"] == "PostToolUseFailure",
                })
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["tool_name", "latency_s", "failed"])


def analyze_single_report(csv_path: Path, df: pd.DataFrame):
    """Generate all figures and findings for a single CSV report."""
    name = csv_path.stem
    out_dir = FIGURES_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)

    total_events = len(df)
    sessions = df["session_id"].nunique()
    time_span = df["timestamp"].max() - df["timestamp"].min()
    tools_used = df[df["tool_name"] != "-"]["tool_name"]
    failures = df[df["event"] == "PostToolUseFailure"]

    # --- 1. Event type distribution ---
    fig, ax = plt.subplots(figsize=(10, 5))
    event_counts = df["event"].value_counts()
    colors = sns.color_palette("viridis", len(event_counts))
    event_counts.plot.bar(ax=ax, color=colors)
    ax.set_title(f"Event Type Distribution — {name}")
    ax.set_xlabel("Event Type")
    ax.set_ylabel("Count")
    ax.tick_params(axis="x", rotation=35)
    for i, v in enumerate(event_counts):
        ax.text(i, v + 0.5, str(v), ha="center", fontsize=8)
    fig.savefig(out_dir / "event_distribution.png")
    plt.close(fig)

    # --- 2. Tool usage frequency ---
    if not tools_used.empty:
        fig, ax = plt.subplots(figsize=(10, 5))
        tool_counts = tools_used.value_counts()
        colors = sns.color_palette("rocket", len(tool_counts))
        tool_counts.plot.barh(ax=ax, color=colors)
        ax.set_title(f"Tool Usage Frequency — {name}")
        ax.set_xlabel("Count")
        ax.set_ylabel("Tool")
        for i, v in enumerate(tool_counts):
            ax.text(v + 0.3, i, str(v), va="center", fontsize=8)
        fig.savefig(out_dir / "tool_usage.png")
        plt.close(fig)

    # --- 3. Activity timeline ---
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.scatter(df["timestamp"], [1] * total_events, alpha=0.4, s=12, c="steelblue")
    ax.set_title(f"Activity Timeline — {name}")
    ax.set_xlabel("Time (UTC)")
    ax.set_yticks([])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate()
    fig.savefig(out_dir / "activity_timeline.png")
    plt.close(fig)

    # --- 4. Permission mode breakdown ---
    fig, ax = plt.subplots(figsize=(6, 6))
    perm_counts = df["permission_mode"].value_counts()
    colors = sns.color_palette("pastel", len(perm_counts))
    perm_counts.plot.pie(ax=ax, autopct="%1.1f%%", colors=colors, startangle=90)
    ax.set_title(f"Permission Mode Breakdown — {name}")
    ax.set_ylabel("")
    fig.savefig(out_dir / "permission_modes.png")
    plt.close(fig)

    # --- 5. Top working directories ---
    cwd_data = df[df["cwd"] != "-"]["cwd"]
    if not cwd_data.empty:
        fig, ax = plt.subplots(figsize=(10, 5))
        top_dirs = cwd_data.value_counts().head(10)
        # Shorten labels for readability
        short_labels = [p.replace(HOME_PREFIX, "~/") for p in top_dirs.index]
        colors = sns.color_palette("crest", len(top_dirs))
        ax.barh(short_labels, top_dirs.values, color=colors)
        ax.set_title(f"Top Working Directories — {name}")
        ax.set_xlabel("Event Count")
        ax.invert_yaxis()
        for i, v in enumerate(top_dirs.values):
            ax.text(v + 0.3, i, str(v), va="center", fontsize=8)
        fig.savefig(out_dir / "top_directories.png")
        plt.close(fig)

    # --- 6. Session activity breakdown (events per session) ---
    if sessions > 1:
        fig, ax = plt.subplots(figsize=(10, 5))
        session_counts = df["session_id"].value_counts()
        short_ids = [s[:8] + "…" for s in session_counts.index]
        colors = sns.color_palette("flare", len(session_counts))
        ax.bar(short_ids, session_counts.values, color=colors)
        ax.set_title(f"Events Per Session — {name}")
        ax.set_xlabel("Session ID (truncated)")
        ax.set_ylabel("Event Count")
        ax.tick_params(axis="x", rotation=45)
        for i, v in enumerate(session_counts.values):
            ax.text(i, v + 0.5, str(v), ha="center", fontsize=8)
        fig.savefig(out_dir / "session_breakdown.png")
        plt.close(fig)

    # --- 7. Tool failure rate per tool ---
    pre_tools = df[df["event"] == "PreToolUse"]["tool_name"].value_counts()
    fail_tools = df[df["event"] == "PostToolUseFailure"]["tool_name"].value_counts()
    if not pre_tools.empty:
        fail_rate = (fail_tools.reindex(pre_tools.index, fill_value=0) / pre_tools * 100).sort_values(ascending=False)
        fail_rate = fail_rate[fail_rate > 0]
        if not fail_rate.empty:
            fig, ax = plt.subplots(figsize=(10, 5))
            colors = sns.color_palette("Reds_r", len(fail_rate))
            fail_rate.plot.barh(ax=ax, color=colors)
            ax.set_title(f"Tool Failure Rate (%) — {name}")
            ax.set_xlabel("Failure Rate (%)")
            ax.set_ylabel("Tool")
            for i, v in enumerate(fail_rate):
                ax.text(v + 0.3, i, f"{v:.1f}%", va="center", fontsize=8)
            fig.savefig(out_dir / "tool_failure_rate.png")
            plt.close(fig)

    # --- 8. Session duration ---
    session_durations = []
    for sid, grp in df.groupby("session_id"):
        dur = (grp["timestamp"].max() - grp["timestamp"].min()).total_seconds() / 60
        session_durations.append({"session_id": sid, "duration_min": dur})
    dur_df = pd.DataFrame(session_durations)
    if len(dur_df) > 1 and dur_df["duration_min"].sum() > 0:
        fig, ax = plt.subplots(figsize=(10, 5))
        short_ids = [s[:8] + "…" for s in dur_df["session_id"]]
        colors = sns.color_palette("coolwarm", len(dur_df))
        ax.bar(short_ids, dur_df["duration_min"], color=colors)
        ax.set_title(f"Session Duration (minutes) — {name}")
        ax.set_xlabel("Session ID (truncated)")
        ax.set_ylabel("Duration (min)")
        ax.tick_params(axis="x", rotation=45)
        for i, v in enumerate(dur_df["duration_min"]):
            ax.text(i, v + 0.2, f"{v:.1f}", ha="center", fontsize=7)
        fig.savefig(out_dir / "session_duration.png")
        plt.close(fig)

    # --- 9. Tool latency (Pre → Post timing) ---
    latency_df = compute_tool_latencies(df)
    if not latency_df.empty and len(latency_df) > 5:
        median_lat = latency_df.groupby("tool_name")["latency_s"].median().sort_values(ascending=False).head(15)
        if not median_lat.empty:
            fig, ax = plt.subplots(figsize=(10, 5))
            colors = sns.color_palette("mako", len(median_lat))
            median_lat.plot.barh(ax=ax, color=colors)
            ax.set_title(f"Median Tool Latency (seconds) — {name}")
            ax.set_xlabel("Median Latency (s)")
            ax.set_ylabel("Tool")
            for i, v in enumerate(median_lat):
                ax.text(v + 0.05, i, f"{v:.2f}s", va="center", fontsize=8)
            fig.savefig(out_dir / "tool_latency.png")
            plt.close(fig)

    # --- 10. Hourly activity heatmap ---
    df_local = df.copy()
    df_local["hour"] = df_local["timestamp"].dt.hour
    df_local["day"] = df_local["timestamp"].dt.day_name()
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    present_days = [d for d in day_order if d in df_local["day"].unique()]
    if len(present_days) > 1:
        pivot = df_local.groupby(["day", "hour"]).size().unstack(fill_value=0)
        pivot = pivot.reindex(present_days)
        fig, ax = plt.subplots(figsize=(14, 4))
        sns.heatmap(pivot, ax=ax, cmap="YlOrRd", linewidths=0.5, annot=True, fmt="d", cbar_kws={"label": "Events"})
        ax.set_title(f"Activity Heatmap (Day × Hour UTC) — {name}")
        ax.set_xlabel("Hour (UTC)")
        ax.set_ylabel("")
        fig.savefig(out_dir / "hourly_heatmap.png")
        plt.close(fig)
    else:
        # Single-day: just show hourly bar
        hourly = df_local["hour"].value_counts().sort_index()
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.bar(hourly.index, hourly.values, color="coral")
        ax.set_title(f"Hourly Activity (UTC) — {name}")
        ax.set_xlabel("Hour")
        ax.set_ylabel("Events")
        ax.set_xticks(range(24))
        fig.savefig(out_dir / "hourly_activity.png")
        plt.close(fig)

    # --- 11. Tool sequence chains (bigrams) ---
    tool_events = df[df["event"].isin(["PreToolUse"])]["tool_name"].tolist()
    if len(tool_events) > 2:
        bigrams = Counter(zip(tool_events[:-1], tool_events[1:]))
        top_bigrams = bigrams.most_common(12)
        if top_bigrams:
            labels = [f"{a} → {b}" for (a, b), _ in top_bigrams]
            counts = [c for _, c in top_bigrams]
            fig, ax = plt.subplots(figsize=(10, 5))
            colors = sns.color_palette("cubehelix", len(labels))
            ax.barh(labels[::-1], counts[::-1], color=colors)
            ax.set_title(f"Most Common Tool Sequences — {name}")
            ax.set_xlabel("Count")
            for i, v in enumerate(counts[::-1]):
                ax.text(v + 0.2, i, str(v), va="center", fontsize=8)
            fig.savefig(out_dir / "tool_chains.png")
            plt.close(fig)

    # --- 12. Project activity breakdown ---
    df_proj = df.copy()
    df_proj["project"] = df_proj["cwd"].apply(extract_project)
    proj_counts = df_proj[df_proj["project"] != "(unknown)"]["project"].value_counts().head(10)
    if not proj_counts.empty and len(proj_counts) > 1:
        fig, ax = plt.subplots(figsize=(10, 5))
        colors = sns.color_palette("Set2", len(proj_counts))
        proj_counts.plot.barh(ax=ax, color=colors)
        ax.set_title(f"Events by Project — {name}")
        ax.set_xlabel("Event Count")
        ax.set_ylabel("Project")
        for i, v in enumerate(proj_counts):
            ax.text(v + 0.3, i, str(v), va="center", fontsize=8)
        fig.savefig(out_dir / "project_activity.png")
        plt.close(fig)

    # --- 13. Prompts per session ---
    prompts = df[df["event"] == "UserPromptSubmit"]
    if not prompts.empty and sessions > 1:
        prompts_per_session = prompts.groupby("session_id").size().sort_values(ascending=False)
        short_ids = [s[:8] + "…" for s in prompts_per_session.index]
        fig, ax = plt.subplots(figsize=(10, 5))
        colors = sns.color_palette("Spectral", len(prompts_per_session))
        ax.bar(short_ids, prompts_per_session.values, color=colors)
        ax.set_title(f"User Prompts Per Session — {name}")
        ax.set_xlabel("Session ID (truncated)")
        ax.set_ylabel("Prompt Count")
        ax.tick_params(axis="x", rotation=45)
        for i, v in enumerate(prompts_per_session.values):
            ax.text(i, v + 0.2, str(v), ha="center", fontsize=8)
        fig.savefig(out_dir / "prompts_per_session.png")
        plt.close(fig)

    # --- 14. Inter-event timing distribution ---
    if total_events > 10:
        time_diffs = df["timestamp"].diff().dt.total_seconds().dropna()
        # Filter to reasonable range (0-300s) to see the useful distribution
        active_diffs = time_diffs[(time_diffs >= 0) & (time_diffs <= 300)]
        if len(active_diffs) > 10:
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.hist(active_diffs, bins=50, color="steelblue", edgecolor="white", alpha=0.8)
            ax.axvline(active_diffs.median(), color="red", linestyle="--", label=f"Median: {active_diffs.median():.1f}s")
            ax.set_title(f"Inter-Event Timing Distribution (≤5 min) — {name}")
            ax.set_xlabel("Seconds Between Events")
            ax.set_ylabel("Frequency")
            ax.legend()
            fig.savefig(out_dir / "inter_event_timing.png")
            plt.close(fig)

        # Idle gaps (>5 min)
        idle_gaps = time_diffs[time_diffs > 300]
        idle_count = len(idle_gaps)
        idle_total_min = idle_gaps.sum() / 60 if not idle_gaps.empty else 0
    else:
        idle_count = 0
        idle_total_min = 0

    # --- Compute extra stats for findings ---
    avg_prompts = len(prompts) / sessions if sessions > 0 else 0
    avg_session_dur = dur_df["duration_min"].mean() if not dur_df.empty else 0
    # Estimate active time: sum of all inter-event gaps <= 5 min
    if total_events > 10:
        all_diffs = df["timestamp"].diff().dt.total_seconds().dropna()
        total_active_min = all_diffs[all_diffs <= 300].sum() / 60
    else:
        total_active_min = time_span.total_seconds() / 60

    # --- Findings text ---
    most_used_tool = tools_used.value_counts().idxmax() if not tools_used.empty else "N/A"
    latency_summary = ""
    if not latency_df.empty:
        lat_stats = latency_df.groupby("tool_name")["latency_s"].agg(["median", "mean", "max", "count"])
        lat_stats = lat_stats.sort_values("median", ascending=False)
        latency_summary = f"\nTool Latency (seconds):\n{lat_stats.to_string()}\n"

    fail_rate_summary = ""
    if not pre_tools.empty:
        fr = (fail_tools.reindex(pre_tools.index, fill_value=0) / pre_tools * 100)
        fr = fr[fr > 0].sort_values(ascending=False)
        if not fr.empty:
            fail_rate_summary = f"\nPer-Tool Failure Rates:\n" + "\n".join(
                f"  {t}: {v:.1f}% ({int(fail_tools.get(t, 0))}/{int(pre_tools[t])})" for t, v in fr.items()
            ) + "\n"

    chain_summary = ""
    if len(tool_events) > 2:
        top_chains = Counter(zip(tool_events[:-1], tool_events[1:])).most_common(8)
        chain_summary = "\nTop Tool Sequences:\n" + "\n".join(
            f"  {a} → {b}: {c}" for (a, b), c in top_chains
        ) + "\n"

    findings = f"""Analysis Report: {name}
{'=' * 60}

Total events:         {total_events}
Unique sessions:      {sessions}
Time span:            {time_span}
First event:          {df['timestamp'].min()}
Last event:           {df['timestamp'].max()}

Session Metrics:
  Avg session duration: {avg_session_dur:.1f} min
  Avg prompts/session:  {avg_prompts:.1f}
  Total user prompts:   {len(prompts)}
  Idle gaps (>5 min):   {idle_count} ({idle_total_min:.1f} min total)
  Est. active time:     {total_active_min:.1f} min

Event Breakdown:
{event_counts.to_string()}

Tool Usage:
  Most used tool:     {most_used_tool}
  Total tool calls:   {len(tools_used)}
  Unique tools:       {tools_used.nunique()}
{tools_used.value_counts().to_string() if not tools_used.empty else '  (none)'}

Failures:             {len(failures)} events
Failure rate:         {len(failures) / total_events * 100:.1f}%
{fail_rate_summary}
{latency_summary}
{chain_summary}
Permission Modes:
{perm_counts.to_string()}

Top Working Directories:
{cwd_data.str.replace(HOME_PREFIX, "~/", regex=False).value_counts().head(5).to_string() if not cwd_data.empty else '  (none)'}

Projects:
{df_proj['project'].value_counts().head(5).to_string() if not df_proj.empty else '  (none)'}
"""
    (out_dir / "findings.txt").write_text(findings)
    return total_events


def generate_summary(all_reports: list[Path]):
    """Generate cross-report summary figures."""
    summary_dir = FIGURES_DIR / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    report_stats = []
    for p in sorted(all_reports):
        df = load_csv(p)
        frames.append(df)
        report_stats.append({"report": p.stem, "events": len(df)})

    if not frames:
        return

    combined = pd.concat(frames, ignore_index=True)
    stats_df = pd.DataFrame(report_stats)

    # --- Cumulative event growth across reports ---
    fig, ax = plt.subplots(figsize=(12, 5))
    stats_df["cumulative"] = stats_df["events"].cumsum()
    short_names = [n.replace("report_", "") for n in stats_df["report"]]
    ax.plot(short_names, stats_df["cumulative"], marker="o", linewidth=2, color="steelblue")
    ax.fill_between(range(len(short_names)), stats_df["cumulative"], alpha=0.15, color="steelblue")
    ax.set_title("Cumulative Event Growth Across Reports")
    ax.set_xlabel("Report")
    ax.set_ylabel("Cumulative Events")
    ax.tick_params(axis="x", rotation=45)
    fig.savefig(summary_dir / "cumulative_growth.png")
    plt.close(fig)

    # --- Overall tool usage ---
    tools = combined[combined["tool_name"] != "-"]["tool_name"]
    if not tools.empty:
        fig, ax = plt.subplots(figsize=(10, 5))
        tool_counts = tools.value_counts()
        colors = sns.color_palette("rocket", len(tool_counts))
        tool_counts.plot.barh(ax=ax, color=colors)
        ax.set_title("Overall Tool Usage (All Reports Combined)")
        ax.set_xlabel("Count")
        ax.set_ylabel("Tool")
        for i, v in enumerate(tool_counts):
            ax.text(v + 0.3, i, str(v), va="center", fontsize=8)
        fig.savefig(summary_dir / "overall_tool_usage.png")
        plt.close(fig)

    # --- Events per report bar chart ---
    fig, ax = plt.subplots(figsize=(12, 5))
    colors = sns.color_palette("viridis", len(stats_df))
    ax.bar(short_names, stats_df["events"], color=colors)
    ax.set_title("Events Per Report")
    ax.set_xlabel("Report")
    ax.set_ylabel("Event Count")
    ax.tick_params(axis="x", rotation=45)
    for i, v in enumerate(stats_df["events"]):
        ax.text(i, v + 1, str(v), ha="center", fontsize=8)
    fig.savefig(summary_dir / "events_per_report.png")
    plt.close(fig)

    # --- Hourly heatmap across all data ---
    combined_local = combined.copy()
    combined_local["hour"] = combined_local["timestamp"].dt.hour
    combined_local["day"] = combined_local["timestamp"].dt.day_name()
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    present_days = [d for d in day_order if d in combined_local["day"].unique()]
    if len(present_days) > 1:
        pivot = combined_local.groupby(["day", "hour"]).size().unstack(fill_value=0)
        pivot = pivot.reindex(present_days)
        fig, ax = plt.subplots(figsize=(16, 5))
        sns.heatmap(pivot, ax=ax, cmap="YlOrRd", linewidths=0.5, annot=True, fmt="d", cbar_kws={"label": "Events"})
        ax.set_title("Overall Activity Heatmap (Day × Hour UTC)")
        ax.set_xlabel("Hour (UTC)")
        ax.set_ylabel("")
        fig.savefig(summary_dir / "overall_hourly_heatmap.png")
        plt.close(fig)

    # --- Overall tool failure rate ---
    all_pre = combined[combined["event"] == "PreToolUse"]["tool_name"].value_counts()
    all_fail = combined[combined["event"] == "PostToolUseFailure"]["tool_name"].value_counts()
    if not all_pre.empty:
        all_fr = (all_fail.reindex(all_pre.index, fill_value=0) / all_pre * 100).sort_values(ascending=False)
        all_fr = all_fr[all_fr > 0]
        if not all_fr.empty:
            fig, ax = plt.subplots(figsize=(10, 5))
            colors = sns.color_palette("Reds_r", len(all_fr))
            all_fr.plot.barh(ax=ax, color=colors)
            ax.set_title("Overall Tool Failure Rate (%)")
            ax.set_xlabel("Failure Rate (%)")
            for i, v in enumerate(all_fr):
                ax.text(v + 0.3, i, f"{v:.1f}%", va="center", fontsize=8)
            fig.savefig(summary_dir / "overall_failure_rate.png")
            plt.close(fig)

    # --- Overall tool latency ---
    all_latency = compute_tool_latencies(combined)
    if not all_latency.empty and len(all_latency) > 5:
        med_lat = all_latency.groupby("tool_name")["latency_s"].median().sort_values(ascending=False).head(15)
        fig, ax = plt.subplots(figsize=(10, 5))
        colors = sns.color_palette("mako", len(med_lat))
        med_lat.plot.barh(ax=ax, color=colors)
        ax.set_title("Overall Median Tool Latency (seconds)")
        ax.set_xlabel("Median Latency (s)")
        for i, v in enumerate(med_lat):
            ax.text(v + 0.05, i, f"{v:.2f}s", va="center", fontsize=8)
        fig.savefig(summary_dir / "overall_tool_latency.png")
        plt.close(fig)

    # --- Project activity across all reports ---
    combined_proj = combined.copy()
    combined_proj["project"] = combined_proj["cwd"].apply(extract_project)
    proj_all = combined_proj[combined_proj["project"] != "(unknown)"]["project"].value_counts().head(15)
    if not proj_all.empty and len(proj_all) > 1:
        fig, ax = plt.subplots(figsize=(10, 6))
        colors = sns.color_palette("Set2", len(proj_all))
        proj_all.plot.barh(ax=ax, color=colors)
        ax.set_title("Events by Project (All Reports)")
        ax.set_xlabel("Event Count")
        for i, v in enumerate(proj_all):
            ax.text(v + 0.3, i, str(v), va="center", fontsize=8)
        fig.savefig(summary_dir / "overall_project_activity.png")
        plt.close(fig)

    # --- Tool usage trend over reports ---
    top_tools_overall = tools.value_counts().head(5).index.tolist()
    if top_tools_overall:
        trend_data = []
        for p in sorted(all_reports):
            rdf = load_csv(p)
            rtools = rdf[rdf["tool_name"] != "-"]["tool_name"]
            for t in top_tools_overall:
                trend_data.append({"report": p.stem.replace("report_", ""), "tool": t, "count": (rtools == t).sum()})
        trend_df = pd.DataFrame(trend_data)
        fig, ax = plt.subplots(figsize=(14, 5))
        for t in top_tools_overall:
            sub = trend_df[trend_df["tool"] == t]
            ax.plot(sub["report"], sub["count"], marker="o", label=t, linewidth=1.5)
        ax.set_title("Top Tool Usage Trend Across Reports")
        ax.set_xlabel("Report")
        ax.set_ylabel("Count")
        ax.tick_params(axis="x", rotation=45)
        ax.legend(loc="upper left", fontsize=8)
        fig.savefig(summary_dir / "tool_usage_trend.png")
        plt.close(fig)

    # --- Summary findings ---
    total = len(combined)
    all_tools_vc = tools.value_counts()
    total_sessions = combined["session_id"].nunique()
    total_prompts = len(combined[combined["event"] == "UserPromptSubmit"])
    total_failures = len(combined[combined["event"] == "PostToolUseFailure"])

    fr_summary = ""
    if not all_pre.empty:
        fr = (all_fail.reindex(all_pre.index, fill_value=0) / all_pre * 100)
        fr = fr[fr > 0].sort_values(ascending=False)
        if not fr.empty:
            fr_summary = "\nPer-Tool Failure Rates:\n" + "\n".join(
                f"  {t}: {v:.1f}%" for t, v in fr.items()
            ) + "\n"

    lat_summary = ""
    if not all_latency.empty:
        lat_stats = all_latency.groupby("tool_name")["latency_s"].agg(["median", "mean", "count"])
        lat_stats = lat_stats.sort_values("median", ascending=False)
        lat_summary = f"\nTool Latency (seconds):\n{lat_stats.to_string()}\n"

    summary_text = f"""Cross-Report Summary
{'=' * 60}

Total reports analyzed: {len(all_reports)}
Total events (combined): {total}
Total unique sessions:   {total_sessions}
Total user prompts:      {total_prompts}
Avg prompts/session:     {total_prompts / total_sessions:.1f}
Total failures:          {total_failures} ({total_failures / total * 100:.1f}%)

Overall Tool Usage:
{all_tools_vc.to_string() if not all_tools_vc.empty else '  (none)'}
{fr_summary}
{lat_summary}
Events Per Report:
{stats_df[['report', 'events']].to_string(index=False)}

Projects:
{proj_all.to_string() if not proj_all.empty else '  (none)'}
"""
    (summary_dir / "summary_findings.txt").write_text(summary_text)


def main():
    csv_files = sorted(REPORTS_DIR.glob("*.csv"))
    if not csv_files:
        print("No CSV files found in reports/")
        sys.exit(0)

    tracker = load_tracker()
    to_process = []

    for csv_path in csv_files:
        h = file_hash(csv_path)
        key = csv_path.name
        if tracker.get(key) == h:
            continue
        to_process.append((csv_path, h))

    if not to_process:
        print("All reports already analyzed. Nothing new to process.")
        return

    print(f"Found {len(to_process)} new/updated report(s) to analyze.\n")

    FIGURES_DIR.mkdir(exist_ok=True)

    for csv_path, h in to_process:
        print(f"Analyzing {csv_path.name} ...")
        df = load_csv(csv_path)
        n = analyze_single_report(csv_path, df)
        tracker[csv_path.name] = h
        print(f"  -> {n} events, figures saved to figures/{csv_path.stem}/")

    save_tracker(tracker)

    # Always regenerate summary across all reports
    print("\nGenerating cross-report summary ...")
    generate_summary(csv_files)
    print(f"  -> Summary saved to figures/summary/")

    print("\nDone.")


if __name__ == "__main__":
    main()
