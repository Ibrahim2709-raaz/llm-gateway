from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_prometheus_loads_versioned_alert_rules() -> None:
    config = yaml.safe_load((PROJECT_ROOT / "monitoring" / "prometheus.yml").read_text())
    compose = yaml.safe_load((PROJECT_ROOT / "compose.yaml").read_text())

    assert "/etc/prometheus/rules/*.yml" in config["rule_files"]
    assert (
        "./monitoring/alerts.yml:/etc/prometheus/rules/alerts.yml:ro"
        in compose["services"]["prometheus"]["volumes"]
    )


def test_routing_alerts_have_unique_names_and_runbooks() -> None:
    rules = yaml.safe_load((PROJECT_ROOT / "monitoring" / "alerts.yml").read_text())
    alerts = [rule for group in rules["groups"] for rule in group["rules"]]
    names = [rule["alert"] for rule in alerts]

    assert len(names) == len(set(names))
    assert {
        "LLMGatewayHighFailureRate",
        "LLMGatewayProviderCircuitOpen",
        "LLMGatewayShadowEvaluationSkipped",
        "LLMGatewayShadowLowAgreement",
        "LLMGatewayRoutingConcentration",
        "LLMGatewayRoutingEvidenceExpiringSoon",
        "LLMGatewayIntelligentCostEstimateDrift",
    } <= set(names)
    assert all(rule["annotations"]["runbook_url"].startswith("https://") for rule in alerts)
