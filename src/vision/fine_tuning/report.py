"""
评估报告生成

自动输出指标与可视化链接。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional


def generate_report(
    output_dir: str,
    metrics: Dict[str, float],
    figures: Optional[List[str]] = None,
    title: str = "CLIP Fine-tuning Report",
) -> str:
    """生成评估报告（JSON 与 HTML）。"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    report_path = output_path / "evaluation_report.json"
    report_html = output_path / "evaluation_report.html"
    report = {
        "title": title,
        "metrics": metrics,
        "figures": figures or [],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_html.write_text(_render_html(report), encoding="utf-8")
    return str(report_html)


def generate_comparison_report(
    output_dir: str,
    baseline_metrics: Dict[str, float],
    finetuned_metrics: Dict[str, float],
) -> str:
    """生成基线与微调对比报告。"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    report_path = output_path / "comparison_report.json"
    report_html = output_path / "comparison_report.html"
    report = {
        "baseline_metrics": baseline_metrics,
        "finetuned_metrics": finetuned_metrics,
        "delta": _compute_delta(baseline_metrics, finetuned_metrics),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_html.write_text(_render_html(report), encoding="utf-8")
    return str(report_html)


def _compute_delta(base: Dict[str, float], finetuned: Dict[str, float]) -> Dict[str, float]:
    """计算指标差值。"""
    delta = {}
    for key, value in finetuned.items():
        if key in base:
            delta[key] = value - base[key]
    return delta


def _render_html(report: Dict) -> str:
    """渲染报告 HTML 内容。"""
    metrics_rows = "\n".join(
        f"<tr><td>{key}</td><td>{value:.6f}</td></tr>" for key, value in report.get("metrics", {}).items()
    )
    figures = report.get("figures", [])
    figures_html = "\n".join(f"<li><a href='{path}'>{path}</a></li>" for path in figures)
    extra_sections = ""
    if "baseline_metrics" in report:
        extra_sections = _render_comparison(report)
    return f"""
    <html>
      <head><title>{report.get('title', 'Report')}</title></head>
      <body>
        <h1>{report.get('title', 'Report')}</h1>
        <h2>Metrics</h2>
        <table border="1">
          <tr><th>Metric</th><th>Value</th></tr>
          {metrics_rows}
        </table>
        <h2>Figures</h2>
        <ul>
          {figures_html}
        </ul>
        {extra_sections}
      </body>
    </html>
    """


def _render_comparison(report: Dict) -> str:
    """渲染对比表格 HTML 内容。"""
    rows = []
    for key, value in report.get("finetuned_metrics", {}).items():
        base = report.get("baseline_metrics", {}).get(key, 0.0)
        delta = report.get("delta", {}).get(key, 0.0)
        rows.append(f"<tr><td>{key}</td><td>{base:.6f}</td><td>{value:.6f}</td><td>{delta:.6f}</td></tr>")
    rows_html = "\n".join(rows)
    return f"""
    <h2>Comparison</h2>
    <table border="1">
      <tr><th>Metric</th><th>Baseline</th><th>Finetuned</th><th>Delta</th></tr>
      {rows_html}
    </table>
    """
