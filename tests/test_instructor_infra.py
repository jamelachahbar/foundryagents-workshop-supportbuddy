"""Compile instructor infrastructure when Bicep is installed; never contact Azure."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def compiled():
    compiler = shutil.which("bicep") or Path.home() / ".azure" / "bin" / "bicep"
    if not Path(compiler).is_file():
        pytest.skip("Bicep CLI is not installed")
    result = subprocess.run(
        [str(compiler), "build", str(ROOT / "instructor" / "infra" / "main.bicep"), "--stdout"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def lab(compiled):
    return next(
        resource for resource in compiled["resources"]
        if resource["type"] == "Microsoft.Resources/deployments" and resource["name"] == "supportbuddy-lab"
    )["properties"]


def test_router_defaults_match_existing_deployment_and_allow_requested_profiles(compiled, lab):
    parameters = compiled["parameters"]
    for name, value in {
        "chatModelName": "model-router",
        "chatModelVersion": "2025-11-18",
        "chatModelDeploymentName": "model-router",
        "chatModelSku": "GlobalStandard",
        "chatModelRoutingMode": "balanced",
    }.items():
        assert parameters[name]["defaultValue"] == value
    assert parameters["chatModelRoutingMode"]["allowedValues"] == ["balanced", "quality", "cost"]
    assert parameters["chatModelSku"]["allowedValues"] == ["GlobalStandard", "DataZoneStandard"]
    assert lab["parameters"]["chatModel"]["value"]["routingMode"] == "[parameters('chatModelRoutingMode')]"
    assert lab["parameters"]["chatModel"]["value"]["deploymentName"] == "[parameters('chatModelDeploymentName')]"
    assert lab["template"]["outputs"]["chatDeployment"]["value"] == "[parameters('chatModel').deploymentName]"


def test_router_uses_supported_api_unrestricted_pool_and_no_routing_for_other_models(lab):
    deployments = [
        resource for resource in lab["template"]["resources"]
        if resource["type"] == "Microsoft.CognitiveServices/accounts/deployments"
    ]
    router = next(resource for resource in deployments if ".deploymentName" in resource["name"])
    assert router["apiVersion"] == "2025-10-01-preview"
    assert router["sku"] == {
        "name": "[parameters('chatModel').sku]", "capacity": "[parameters('chatModel').capacity]",
    }
    properties = router["properties"]
    assert "if(equals(parameters('chatModel').name, 'model-router')" in properties
    assert "createObject('routing', createObject('mode', parameters('chatModel').routingMode))" in properties
    assert "'models'" not in properties
    assert properties.endswith(", createObject()))]")
    embedding = next(resource for resource in deployments if "supportbuddy-embedding" in resource["name"])
    assert embedding["apiVersion"] == "2025-06-01"
    assert "routing" not in embedding["properties"]


def test_azd_parameter_defaults_and_participant_template_match_bicep(compiled):
    parameters = json.loads((ROOT / "instructor" / "infra" / "main.parameters.json").read_text())["parameters"]
    for name, environment in {
        "chatModelName": "CHAT_MODEL_NAME",
        "chatModelVersion": "CHAT_MODEL_VERSION",
        "chatModelDeploymentName": "CHAT_MODEL_DEPLOYMENT_NAME",
        "chatModelSku": "CHAT_MODEL_SKU",
        "chatModelRoutingMode": "CHAT_MODEL_ROUTING_MODE",
    }.items():
        default = compiled["parameters"][name]["defaultValue"]
        assert parameters[name]["value"] == f"${{{environment}={default}}}"
    template = (ROOT / "src" / ".env.template").read_text()
    assert "\nAZURE_AI_MODEL_DEPLOYMENT_NAME=model-router\n" in template


def test_monitoring_is_unconditional_and_exports_only_a_resource_id(compiled, lab):
    resources = lab["template"]["resources"]
    for resource_type in ["Microsoft.OperationalInsights/workspaces", "Microsoft.Insights/components"]:
        resource = next(resource for resource in resources if resource["type"] == resource_type)
        assert "condition" not in resource
    insights = next(resource for resource in resources if resource["type"] == "Microsoft.Insights/components")
    assert insights["properties"]["RetentionInDays"] == 30
    assert "WorkspaceResourceId" in insights["properties"]
    assert "APPLICATIONINSIGHTS_RESOURCE_ID" in compiled["outputs"]
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" not in compiled["outputs"]


def test_fixed_judge_is_enabled_by_default_and_serialized_after_embeddings(compiled, lab):
    assert compiled["parameters"]["deployEvaluationModel"]["defaultValue"] is True
    assert compiled["parameters"]["evaluationModelName"]["defaultValue"] == "gpt-5-mini"
    assert compiled["parameters"]["evaluationModelVersion"]["defaultValue"] == "2025-08-07"
    assert compiled["parameters"]["evaluationModelDeploymentName"]["defaultValue"] == "supportbuddy-judge"
    assert lab["parameters"]["deployEvaluationModel"]["value"] == "[parameters('deployEvaluationModel')]"
    judge = next(
        resource for resource in lab["template"]["resources"]
        if resource["type"] == "Microsoft.CognitiveServices/accounts/deployments"
        and "evaluationModel" in resource["name"]
    )
    assert judge["condition"] == "[parameters('deployEvaluationModel')]"
    assert any("supportbuddy-embedding" in dependency for dependency in judge["dependsOn"])
    assert judge["properties"]["model"] == {
        "format": "OpenAI", "name": "[parameters('evaluationModel').name]",
        "version": "[parameters('evaluationModel').version]",
    }
    assert judge["properties"]["versionUpgradeOption"] == "NoAutoUpgrade"
    assert "if(parameters('deployEvaluationModel')" in lab["template"]["outputs"]["evaluationDeployment"]["value"]
    assert "AZURE_AI_EVALUATION_MODEL_DEPLOYMENT_NAME" in compiled["outputs"]


def test_optional_judge_parameters_are_bound_to_azd_configuration(compiled, lab):
    parameters = json.loads((ROOT / "instructor" / "infra" / "main.parameters.json").read_text())["parameters"]
    assert parameters["deployEvaluationModel"]["value"] == "${DEPLOY_EVALUATION_MODEL=true}"
    for name, environment in {
        "evaluationModelName": "EVALUATION_MODEL_NAME",
        "evaluationModelVersion": "EVALUATION_MODEL_VERSION",
        "evaluationModelDeploymentName": "EVALUATION_MODEL_DEPLOYMENT_NAME",
        "evaluationModelSku": "EVALUATION_MODEL_SKU",
        "evaluationModelCapacity": "EVALUATION_MODEL_CAPACITY",
    }.items():
        default = compiled["parameters"][name]["defaultValue"]
        assert parameters[name]["value"] == f"${{{environment}={default}}}"
    assert lab["parameters"]["evaluationModel"]["value"]["deploymentName"] == "[parameters('evaluationModelDeploymentName')]"
