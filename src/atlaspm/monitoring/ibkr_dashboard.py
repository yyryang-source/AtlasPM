"""v0.4.16: strictly local, redacted HTML UI for unsigned AtlasPM risk reports.

The dashboard is a static file. It embeds only allowlisted stage states and
aggregate counts; no account balances, holdings, financing claims, full account
IDs, private labels, file paths or digests enter HTML. No scripts or remote assets.
"""

from __future__ import annotations

from atlaspm.engine.contracts import validated_object

from datetime import datetime, timezone
from html import escape
from typing import Any, Mapping

from atlaspm.monitoring.ibkr_risk_chain_audit import (
    STAGE_METHODS, audit_ibkr_risk_chain,
)
from atlaspm.monitoring.ibkr_risk_audit_diff import compare_ibkr_risk_audits

STAGE_LABELS = {
    "monitor": ("01", "保证金监控", "IBKR read-only monitor"),
    "financing": ("02", "融资字段核对", "Financing claim check"),
    "stress": ("03", "静态压力测试", "Static stress scenarios"),
    "multiasset": ("04", "相关性风险预算", "Multi-asset correlation"),
    "reverse": ("05", "逆向压力阈值", "Reverse stress thresholds"),
}
SAFE_STAGE_STATES = {
    "monitor": {"NORMAL", "WATCH", "CRITICAL", "BLOCKED_DATA"},
    "financing": {"FIELDS_CONSISTENT_REVIEW_REQUIRED", "BLOCK_FINANCING_MARGIN"},
    "stress": {"HYPOTHETICAL_STRESS_ONLY"},
    "multiasset": {"HYPOTHETICAL_RISK_BUDGET_ONLY"},
    "reverse": {"HYPOTHETICAL_REVERSE_STRESS_ONLY"},
}
SCENARIO_STATES = {
    "stress": {
        "ILLUSTRATIVE_ABOVE_FLOOR", "ILLUSTRATIVE_WATCH", "ILLUSTRATIVE_DEFICIT"
    },
    "multiasset": {
        "HYPOTHETICAL_WITHIN_LIMITS", "HYPOTHETICAL_LIMIT_BREACH",
        "HYPOTHETICAL_DEFICIT"
    },
    "reverse": {
        "ILLUSTRATIVE_NO_THRESHOLD_IN_SCAN_RANGE",
        "ILLUSTRATIVE_THRESHOLD_WITHIN_SCAN",
        "ILLUSTRATIVE_ALREADY_AT_THRESHOLD",
    },
}
STATE_LABELS = {
    "NORMAL": "未触发监控预警",
    "WATCH": "需要关注",
    "CRITICAL": "严重风险预警",
    "BLOCKED_DATA": "监控数据被阻断",
    "FIELDS_CONSISTENT_REVIEW_REQUIRED": "字段一致，仍需人工复核",
    "BLOCK_FINANCING_MARGIN": "融资字段异常",
    "HYPOTHETICAL_STRESS_ONLY": "仅用于假设测试",
    "HYPOTHETICAL_RISK_BUDGET_ONLY": "仅用于假设预算",
    "HYPOTHETICAL_REVERSE_STRESS_ONLY": "仅用于假设阈值",
    "ILLUSTRATIVE_ABOVE_FLOOR": "假设限制内",
    "ILLUSTRATIVE_WATCH": "假设风险关注",
    "ILLUSTRATIVE_DEFICIT": "假设保证金缺口",
    "HYPOTHETICAL_WITHIN_LIMITS": "假设预算内",
    "HYPOTHETICAL_LIMIT_BREACH": "假设预算超限",
    "HYPOTHETICAL_DEFICIT": "假设保证金缺口",
    "ILLUSTRATIVE_NO_THRESHOLD_IN_SCAN_RANGE": "扫描范围内未触及阈值",
    "ILLUSTRATIVE_THRESHOLD_WITHIN_SCAN": "扫描范围内触及阈值",
    "ILLUSTRATIVE_ALREADY_AT_THRESHOLD": "起点已触及阈值",
    "UNRECOGNIZED": "无法识别的状态",
}
ALERT_STATES = {
    "WATCH", "CRITICAL", "BLOCKED_DATA", "BLOCK_FINANCING_MARGIN",
    "ILLUSTRATIVE_WATCH", "ILLUSTRATIVE_DEFICIT",
    "HYPOTHETICAL_LIMIT_BREACH", "HYPOTHETICAL_DEFICIT",
    "ILLUSTRATIVE_THRESHOLD_WITHIN_SCAN",
    "ILLUSTRATIVE_ALREADY_AT_THRESHOLD", "UNRECOGNIZED",
}
TONE_SEVERE = {
    "CRITICAL", "BLOCKED_DATA", "BLOCK_FINANCING_MARGIN",
    "ILLUSTRATIVE_DEFICIT", "HYPOTHETICAL_DEFICIT",
    "ILLUSTRATIVE_ALREADY_AT_THRESHOLD", "UNRECOGNIZED",
}
COUNT_FIELDS = (
    ("hypothetical_stress_alert_count", "静态压力预警"),
    ("hypothetical_multiasset_alert_count", "多资产预算预警"),
    ("hypothetical_reverse_threshold_count", "逆向阈值触发"),
)


def _object(value: Any, label: str) -> dict[str, Any]:
    return validated_object(value, label, invalid_message=f"{label} must be an object")

def _safe_state(value: Any, allowed: set[str], errors: list[str], code: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        errors.append(code)
        return "UNRECOGNIZED"
    return value


def build_dashboard_view(
    reports: Mapping[str, Any], previous_audit: Mapping[str, Any],
    latest_audit: Mapping[str, Any], stored_diff: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a derived, non-sensitive view, verifying saved audit linkage."""
    if not isinstance(reports, dict) or set(reports) != set(STAGE_METHODS):
        raise ValueError("Five named private stage reports are required")
    stage_reports = {name: _object(reports[name], name) for name in STAGE_METHODS}
    prior = _object(previous_audit, "previous audit")
    latest = _object(latest_audit, "latest audit")
    supplied_diff = _object(stored_diff, "audit diff")
    # Raises for incompatible/forged account IDs or self-claimed verification.
    actual_diff = compare_ibkr_risk_audits(prior, latest)
    recomputed = audit_ibkr_risk_chain(stage_reports)

    errors: list[str] = []
    for field in (
        "source_sha256", "status", "account_id",
        "hypothetical_stress_alert_count",
        "hypothetical_multiasset_alert_count",
        "hypothetical_reverse_threshold_count",
    ):
        if latest.get(field) != recomputed.get(field):
            errors.append("LATEST_AUDIT_REPORT_MISMATCH")
    for field in (
        "method", "status", "account_id", "changed_source_stages",
        "source_digest_change_count", "hypothetical_alert_count_deltas",
        "hypothetical_alert_count_direction", "diff_issues",
        "previous_source_sha256", "current_source_sha256",
    ):
        if supplied_diff.get(field) != actual_diff.get(field):
            errors.append("SAVED_DIFF_REPORT_MISMATCH")
    for flag in (
        "live_execution_enabled", "ready_for_institutional_risk_gate",
        "IBKR_financing_debt_separately_verified",
        "broker_source_independently_authenticated",
        "broker_treasury_collateral_verified", "IBKR_order_bound_whatif_verified",
    ):
        if supplied_diff.get(flag) is not False:
            errors.append("SAVED_DIFF_AUTHORITY_CLAIM_INVALID")

    stages: list[dict[str, Any]] = []
    for name in STAGE_METHODS:
        stage = stage_reports[name]
        if stage.get("method") != STAGE_METHODS[name]:
            errors.append("STAGE_METHOD_MISMATCH")
        status_key = "monitor_state" if name == "monitor" else "status"
        state = _safe_state(
            stage.get(status_key), SAFE_STAGE_STATES[name],
            errors, "STAGE_STATUS_NOT_ALLOWED",
        )
        entries: list[dict[str, Any]] = []
        if name in SCENARIO_STATES:
            key = "cases" if name == "reverse" else "scenarios"
            source = stage.get(key)
            if not isinstance(source, list) or not 1 <= len(source) <= 20:
                raise ValueError("Malformed private scenario list")
            for index, item in enumerate(source, 1):
                row = _object(item, "private scenario")
                if row.get("index") != index or isinstance(row.get("index"), bool):
                    errors.append("SCENARIO_INDEX_MISMATCH")
                field = "scenario_state" if name == "stress" else "state"
                case_status = _safe_state(
                    row.get(field), SCENARIO_STATES[name],
                    errors, "SCENARIO_STATUS_NOT_ALLOWED",
                )
                entries.append({"index": index, "status": case_status})
        stages.append({
            "slug": name, "number": STAGE_LABELS[name][0],
            "title": STAGE_LABELS[name][1], "english": STAGE_LABELS[name][2],
            "status": state, "scenarios": entries,
        })

    if recomputed["integrity_issues"]:
        errors.append("CROSS_STAGE_AUDIT_INCONSISTENT")
    if latest["status"] == "CHAIN_INCONSISTENT":
        errors.append("LATEST_AUDIT_INCONSISTENT")
    if actual_diff["status"] != "NO_SOURCE_CHANGES_DETECTED_UNVERIFIED":
        errors.append("AUDIT_COMPARISON_REVIEW_REQUIRED")
    if latest["account_id"] != prior["account_id"]:
        errors.append("AUDIT_ACCOUNT_IDENTITY_MISMATCH")
    if any(latest["source_sha256"][name] != prior["source_sha256"][name]
           for name in STAGE_METHODS):
        errors.append("AUDIT_SOURCES_CHANGED")

    problems = sorted(set(errors))
    any_alerts = (
        any(recomputed[name] for name, _ in COUNT_FIELDS)
        or recomputed["upstream_monitor_state"] in {"WATCH", "CRITICAL"}
    )
    state = (
        "LOCAL_REVIEW_REQUIRED" if problems else
        "LOCAL_SIMULATED_ALERTS_PRESENT" if any_alerts else
        "LOCAL_REPORTS_CONSISTENT_UNVERIFIED"
    )
    account = recomputed["account_id"]
    return {
        "dashboard_state": state,
        "account_hint": "****" + account[-4:],
        "data_source_type": (
            "SYNTHETIC_FIXTURE" if stage_reports["monitor"].get("source")
            == "synthetic_ibkr_fixture" else "UNVERIFIED_LOCAL_REPORT"
        ),
        "validation_codes": problems,  # only hardcoded error codes
        "stages": stages,
        "risk_counts": [
            {"label": label, "value": recomputed[field]}
            for field, label in COUNT_FIELDS
        ],
        "audit_integrity_issue_count": len(recomputed["integrity_issues"]),
        "changed_stages": list(actual_diff["changed_source_stages"]),
        "audit_diff_state": actual_diff["status"],
        "risk_count_direction": actual_diff["hypothetical_alert_count_direction"],
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "broker_source_independently_authenticated": False,
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
    }


def _tone(state: str) -> str:
    return "danger" if state in TONE_SEVERE else (
        "warning" if state in ALERT_STATES else "muted"
    )


def _scenario_rows(stage: dict[str, Any]) -> str:
    if not stage["scenarios"]:
        return '<div class="empty">该阶段没有独立情景列表</div>'
    rows = []
    for item in stage["scenarios"]:
        state = item["status"]
        rows.append(
            '<div class="scenario"><span class="scenario-index">情景 '
            + str(item["index"]).zfill(2)
            + '</span><span class="dot '+_tone(state)+'"></span>'
            + '<span class="scenario-label">'+escape(STATE_LABELS[state])+'</span>'
            + '<span class="code">'+escape(state)+'</span></div>'
        )
    return "".join(rows)


def render_dashboard_html(view: Mapping[str, Any]) -> str:
    """Render a self-contained, CSP-locked HTML artifact (no JS or remote URLs)."""
    data = _object(view, "redacted view")
    status = data["dashboard_state"]
    status_labels = {
        "LOCAL_REVIEW_REQUIRED": ("需要人工核对", "数据完整性或文件版本不一致，不能将此视为可靠快照。"),
        "LOCAL_SIMULATED_ALERTS_PRESENT": ("模拟风险预警", "五阶段报告一致，包含人为设计的假设风险情景。"),
        "LOCAL_REPORTS_CONSISTENT_UNVERIFIED": ("本地报告一致", "未发现已覆盖模型的风险情景，但数据来源仍未经认证。"),
    }
    if status not in status_labels:
        raise ValueError("Unsupported dashboard state")
    status_title, status_desc = status_labels[status]
    pills = []
    for step in data["stages"]:
        state = step["status"]
        pills.append(
            '<article class="stage" id="stage-'+escape(step["slug"])+'">'
            '<div class="stage-top"><span class="stage-number">'+escape(step["number"])+'</span>'
            '<span class="stage-flag '+_tone(state)+'">'+escape(STATE_LABELS[state])+'</span></div>'
            '<h3>'+escape(step["title"])+'</h3>'
            '<p class="english">'+escape(step["english"])+'</p>'
            '<div class="status-code">'+escape(state)+'</div>'
            '<details><summary>查看情景与状态 <span>+</span></summary>'
            '<div class="scenario-list">'+_scenario_rows(step)+'</div></details></article>'
        )
    counts = list(data["risk_counts"])
    max_count = max([row["value"] for row in counts] + [1])
    meter_rows = []
    for row in counts:
        value = row["value"]
        percentage = round(100 * value / max_count, 1)
        meter_rows.append(
            '<div class="meter"><div class="meter-head"><span>'
            + escape(row["label"]) + '</span><strong>' + str(value) + '</strong></div>'
            + '<div class="track"><div class="fill" style="width:'
            +str(percentage)+'%"></div></div></div>'
        )
    audits = (
        "<span class=\"large-number\">"
        + str(data["audit_integrity_issue_count"])
        + "</span>"
    )
    changed = data["changed_stages"]
    changed_text = "无变化" if not changed else (
        "、".join(STAGE_LABELS[s][1] for s in changed if s in STAGE_LABELS) or "未知"
    )
    issues_text = (
        "无本地核对异常" if not data["validation_codes"] else
        " · ".join(escape(i) for i in data["validation_codes"])
    )
    sample = (
        "仅合成数据" if data["data_source_type"] == "SYNTHETIC_FIXTURE"
        else "仅本地未认证数据"
    )
    state_tone = "danger" if status == "LOCAL_REVIEW_REQUIRED" else "warning" if (
        status == "LOCAL_SIMULATED_ALERTS_PRESENT"
    ) else "muted"
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'none'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>AtlasPM · 本地风险仪表盘</title>
<style>
:root{color-scheme:dark;font-family:Inter,"Segoe UI","Microsoft Yahei",system-ui,sans-serif;
--bg:#080d19;--panel:#131d2f;--panel2:#18253a;--stroke:#2a3850;--soft:#8e9db4;--text:#edf3ff;--aqua:#42dbd1;--amber:#f8c879;--red:#fa8a91}
*{box-sizing:border-box}body{margin:0;min-width:340px;background:radial-gradient(ellipse at 75% -4%,#183050 0,transparent 48%),var(--bg);color:var(--text)}
.layout{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:100vh}
aside{background:#0b1423;border-right:1px solid var(--stroke);padding:32px 20px}
.brand{font-size:23px;font-weight:800;letter-spacing:.5px}.brand i{color:var(--aqua);font-style:normal}
.brand-sub{color:var(--soft);font-size:11px;letter-spacing:2px;text-transform:uppercase;margin:6px 0 44px}
.navtitle{font-size:11px;letter-spacing:1.6px;color:#66809d;font-weight:700;margin:24px 12px 14px}
nav a{display:block;text-decoration:none;color:#adbbce;font-size:13px;padding:13px;border-radius:10px;margin-bottom:4px}
nav a:hover,nav a:focus{background:#1b2b42;color:#fff;outline:none}
.aside-foot{font-size:12px;color:#8c9ab0;border-top:1px solid var(--stroke);margin-top:48px;padding:20px 10px;line-height:1.8}
main{max-width:1480px;width:100%;padding:43px clamp(20px,4vw,65px) 72px;margin:0 auto}
.eyebrow{color:var(--aqua);font-size:11px;letter-spacing:2.2px;font-weight:800;text-transform:uppercase}
h1{font-size:clamp(29px,3.4vw,43px);letter-spacing:-1.2px;margin:9px 0}
.subhead{color:#a5b2c6;line-height:1.8;max-width:740px;margin:6px 0 24px}
.topline{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:center}
.chip{border:1px solid #466579;border-radius:30px;padding:9px 13px;color:#c7e4e7;font-size:12px;background:#142939}
.overview{margin-top:23px;border:1px solid #335068;background:linear-gradient(115deg,#162b42,#111d30);border-radius:18px;padding:27px}
.overview.danger{border-color:#894d5a;background:linear-gradient(115deg,#39202c,#211c2c)}
.overview-header{display:flex;align-items:center;gap:11px;color:var(--aqua);font-weight:800;font-size:12px;letter-spacing:1.5px}
.overview.warning .overview-header{color:var(--amber)}.overview.danger .overview-header{color:var(--red)}
.lamp{display:inline-block;width:9px;height:9px;border-radius:100%;background:currentColor;box-shadow:0 0 18px currentColor}
.overview h2{font-size:27px;margin:14px 0 6px}.overview p{color:#b5c6d9;line-height:1.6;font-size:14px;margin:0}
.meta{display:flex;gap:11px;flex-wrap:wrap;margin-top:19px;color:#8aa4c0;font-size:12px}
.meta span{padding:7px 10px;border:1px solid #37516a;border-radius:7px}
.kpis{margin-top:22px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px}
.kpi,.panel,.stage{background:linear-gradient(150deg,#172237,#101929);border:1px solid var(--stroke);border-radius:15px}
.kpi{padding:22px}.kpi-title{font-size:12px;color:var(--soft)}.kpi-value{font-size:30px;letter-spacing:-1px;font-weight:750;margin:12px 0 7px}
.kpi small{font-size:11px;color:#879bb8}
.section-heading{display:flex;gap:10px;justify-content:space-between;align-items:end;margin:38px 0 15px}
h2.section{font-size:19px;margin:0}.section-hint{font-size:12px;color:var(--soft)}
.stages{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}
.stage{padding:20px;min-width:0}.stage-top{display:flex;justify-content:space-between;gap:10px;align-items:center}
.stage-number{color:#70c9ca;font-size:13px;font-weight:800;letter-spacing:1px}
.stage-flag{font-size:10px;font-weight:700;background:#24404c;color:#b2dee7;border-radius:20px;padding:7px 9px}
.stage-flag.warning{background:#463722;color:#f4ca8d}
.stage-flag.danger{background:#4a2738;color:#faa4b2}
.stage h3{font-size:16px;margin:18px 0 5px}.english{margin:0;font-size:11px;color:#8193ad}
.status-code{overflow-wrap:anywhere;font-size:10px;color:#6faeae;margin:19px 0}
details{border-top:1px solid var(--stroke);padding-top:14px}
summary{cursor:pointer;color:#bfd4e5;font-size:12px;list-style:none;display:flex;justify-content:space-between}
summary::-webkit-details-marker{display:none}
summary span{font-weight:800;color:var(--aqua)}
.scenario-list{margin-top:15px}.scenario{display:grid;grid-template-columns:53px 8px minmax(0,1fr);gap:7px;align-items:center;border-top:1px solid #26354a;padding:12px 0}
.scenario-index{font-size:10px;color:#8da1b6}.dot{width:7px;height:7px;background:var(--aqua);border-radius:50%}.dot.warning{background:var(--amber)}.dot.danger{background:var(--red)}
.scenario-label{font-size:12px}.code{grid-column:3;color:#607790;font-size:10px;overflow-wrap:anywhere}
.lower{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:15px}
.panel{padding:24px}.panel h3{margin:0 0 24px;font-size:15px}.meter{margin:19px 0}
.meter-head{display:flex;justify-content:space-between;color:#abb9ce;font-size:12px}.meter-head strong{color:#f0f6ff;font-size:15px}
.track{height:9px;border-radius:10px;background:#24344b;margin-top:9px;overflow:hidden}.fill{height:100%;border-radius:10px;background:linear-gradient(90deg,#2795a9,#50d9b4)}
.large-number{font-weight:800;font-size:46px;line-height:1.1}
.audit-desc{color:#a1b2c7;font-size:12px;line-height:1.7}
.audit-details{border-top:1px solid var(--stroke);padding-top:15px;margin-top:15px;color:#a1b2c7;font-size:12px;line-height:1.7;overflow-wrap:anywhere}
.notice{border-left:3px solid #e9bd6a;padding:16px 22px;background:#20232b;color:#cfbdac;border-radius:0 10px 10px 0;font-size:13px;line-height:1.85;margin-top:28px}
footer{color:#7f8fa8;margin-top:24px;font-size:11px;line-height:1.9}
@media(max-width:1080px){.stages{grid-template-columns:repeat(2,minmax(0,1fr))}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:740px){.layout{display:block}aside{padding:18px;border-right:0;border-bottom:1px solid var(--stroke)}
.brand-sub{margin:3px 0 0}.navtitle,nav,.aside-foot{display:none}main{padding:24px 16px 48px}
.stages{grid-template-columns:1fr}.lower{grid-template-columns:1fr}.kpis{gap:9px}.kpi{padding:16px}.kpi-value{font-size:25px}}
@media print{body{background:white;color:#111}.layout{display:block}aside,nav{display:none}main{padding:10px;max-width:100%}
.kpi,.panel,.stage,.overview{background:#fff;color:#111;border:1px solid #ddd;break-inside:avoid}*{box-shadow:none!important}}
</style>
</head>
<body><div class="layout">
<aside><div class="brand"><i>◈</i> AtlasPM</div><div class="brand-sub">Risk Intelligence / Local</div>
<div class="navtitle">WORKSPACE</div><nav>
<a href="#overview">◈ &nbsp; 风险总览</a>
<a href="#stages">▣ &nbsp; 五阶段诊断</a>
<a href="#alerts">▥ &nbsp; 模拟风险分布</a>
<a href="#integrity">◇ &nbsp; 审计与完整性</a>
</nav><div class="aside-foot">OFFLINE MODE<br>零远程请求 · 零自动交易<br>研究与审计界面</div></aside>
<main><div class="topline"><div><div class="eyebrow">Portfolio Risk / Offline Analysis</div>
<h1>风险管理总览</h1></div><span class="chip">● 只读 · 本地快照</span></div>
<p class="subhead">把 AtlasPM 的保证金监控、融资核对、相关性预算和逆向压力测试整合为单一视图。所有结果均来自未签名的本地报告。</p>
<section id="overview" class="overview """+state_tone+"""">
<div class="overview-header"><span class="lamp"></span> 当前研究风险状态 / UNVERIFIED</div>
<h2>"""+escape(status_title)+"""</h2>
<p>"""+escape(status_desc)+"""</p>
<div class="meta"><span>账户 """+escape(data["account_hint"])+"""</span>
<span>"""+escape(sample)+"""</span>
<span>生成时间 """+escape(data["generated_utc"])+"""</span></div>
</section>
<section class="kpis" aria-label="风险指标">
<div class="kpi"><div class="kpi-title">数据完整性问题</div><div class="kpi-value">"""+str(data["audit_integrity_issue_count"])+"""</div><small>本地算术交叉校验</small></div>
<div class="kpi"><div class="kpi-title">模拟预警总情景</div><div class="kpi-value">"""+str(sum(x["value"] for x in counts))+"""</div><small>三类情景合计，不等于概率</small></div>
<div class="kpi"><div class="kpi-title">变更的源报告</div><div class="kpi-value">"""+str(len(changed))+"""</div><small>仅比较保存的摘要</small></div>
<div class="kpi"><div class="kpi-title">可执行交易权限</div><div class="kpi-value" style="color:var(--amber)">OFF</div><small>风险闸门始终关闭</small></div>
</section>
<section id="stages"><div class="section-heading"><h2 class="section">五阶段诊断链</h2><span class="section-hint">点击卡片底部可展开情景</span></div>
<div class="stages">"""+"".join(pills)+"""</div></section>
<div class="section-heading"><h2 class="section">风险预警与审计</h2><span class="section-hint">Synthetic diagnostics</span></div>
<div class="lower"><section id="alerts" class="panel"><h3>假设性预警情景分布</h3>
"""+"".join(meter_rows)+"""<p class="audit-desc">这些是测试情景触发的预警数量，不是账户实际违约概率，也不能据此计算安全杠杆。</p></section>
<section id="integrity" class="panel"><h3>本地审计记录</h3>
""" + audits + """<p class="audit-desc">计算核对异常数。即使为零，也不代表券商来源真实。</p>
<div class="audit-details"><strong>文件变化：</strong>"""+escape(changed_text)+"""
</div><div class="audit-details"><strong>本地数据校验：</strong>"""+issues_text+"""
</div><div class="audit-details"><strong>审计对比：</strong>"""+escape(data["audit_diff_state"])+"""
</div></section></div>
<div class="notice"><strong>重要限制：</strong>页面不包含资金、融资余额和具体持仓，也不访问网络。
此报告是脱敏研究快照，不含真实 IBKR 贷款核实、抵押品批准、订单 What-If 或强平价格。
即使所有卡片显示正常，真实交易与风险闸门也始终关闭。</div>
<footer>AtlasPM v0.4.16 · Static offline HTML · Content-Security-Policy: no scripts / no network / no remote assets<br>
本地生成的 HTML 也应妥善保管；不要放在公共网站或通过邮件分享。</footer>
</main></div></body></html>
"""


def redacted_dashboard_console(view: Mapping[str, Any], output: str) -> dict[str, Any]:
    return {
        "written": output,
        "method": "atlaspm_local_risk_dashboard",
        "dashboard_state": view["dashboard_state"],
        "account_hint": view["account_hint"],
        "data_source_type": view["data_source_type"],
        "validation_codes": view["validation_codes"],
        "audit_integrity_issue_count": view["audit_integrity_issue_count"],
        "hypothetical_alert_count_total": sum(
            item["value"] for item in view["risk_counts"]
        ),
        "source_changed_count": len(view["changed_stages"]),
        "IBKR_financing_debt_separately_verified": False,
        "ready_for_institutional_risk_gate": False,
        "live_execution_enabled": False,
        "warnings": [
            "Static offline HTML contains only masked stage summaries, not raw private balances or holdings.",
            "Local file consistency is not a broker risk attestation or permission to trade.",
        ],
    }
