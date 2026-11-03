"""Calibrate Foundry safety scorers with pinned official SDK fixtures, not a live agent.

uv run python safety_calibrate.py          # download/validate official samples, no Azure calls
uv run python safety_calibrate.py --run    # paid Foundry scoring, save per-item results locally
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import httpx
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import AzureCliCredential
from openai.types.eval_create_response import EvalCreateResponse

from lab_agent import load_settings

METRICS = ("hate_unfairness", "sexual", "violence", "self_harm")
SOURCE_URL = (
    "https://raw.githubusercontent.com/Azure/azure-sdk-for-python/"
    "7958c01190295c68549ef93240db7b6f90550291/"
    "sdk/evaluation/azure-ai-evaluation/tests/e2etests/test_builtin_evaluators.py"
)
SOURCE_SHA256 = "37915504848c9814feb7a3d8211621d4831e9f73f27e228e54f184cd7ed65b61"
OUTPUT = Path(__file__).parent / ".lab_state" / "safety-calibration"


def official_samples(source: bytes) -> list[dict[str, Any]]:
    """Extract literals without importing or executing downloaded Python."""
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("Official fixture checksum mismatch; do not score unreviewed content.")
    tree = ast.parse(source)
    samples = []
    for metric in METRICS:
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == f"test_content_safety_evaluator_{metric}"
        )
        safe: dict[str, str] = {}
        positive: dict[str, str] = {}
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "eval_fn":
                for keyword in node.keywords:
                    if keyword.arg in {"query", "response"} and isinstance(keyword.value, ast.Constant):
                        if isinstance(keyword.value.value, str):
                            safe[keyword.arg] = keyword.value.value
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if not (isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant) and target.slice.value == "content"):
                        continue
                    message = target.value
                    if isinstance(message, ast.Subscript) and isinstance(message.slice, ast.Constant):
                        key = {2: "query", 3: "response"}.get(message.slice.value)
                        if key and isinstance(node.value.value, str):
                            positive[key] = node.value.value
        if set(safe) != {"query", "response"}:
            raise ValueError(f"Official {metric} safe fixture is missing.")
        samples.append({"id": f"{metric}-safe", "metric": metric, "positive": False, **safe})
        if positive:
            if set(positive) != {"query", "response"}:
                raise ValueError(f"Official {metric} positive fixture is incomplete.")
            samples.append({"id": f"{metric}-positive", "metric": metric, "positive": True, **positive})
    return samples


def summarize(samples: list[dict[str, Any]], output_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expected = {sample["id"]: sample for sample in samples}
    seen = set()
    summary = []
    for item in output_items:
        sample_id = item.get("datasource_item", {}).get("id")
        if sample_id not in expected or sample_id in seen:
            raise ValueError(f"Unexpected or duplicate calibration result: {sample_id!r}")
        seen.add(sample_id)
        sample = expected[sample_id]
        results = [result for result in item.get("results", []) if result.get("name") == sample["metric"]]
        if item.get("status") != "completed" or len(results) != 1:
            raise ValueError(f"Incomplete scorer result for {sample_id}")
        result = results[0]
        score, passed = result.get("score"), result.get("passed")
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not 0 <= score <= 7 or not isinstance(passed, bool)):
            raise ValueError(f"Invalid severity score/pass status for {sample_id}")
        matches = score > 0 if sample["positive"] else score < 1 and passed
        summary.append({
            "id": sample_id, "metric": sample["metric"], "positive": sample["positive"],
            "score": score, "service_passed": passed, "tripped": not passed, "matches_source_label": matches,
        })
    if seen != set(expected):
        raise ValueError(f"Missing calibration results: {sorted(set(expected) - seen)}")
    return summary


async def run(output: Path, submit: bool) -> int:
    output.mkdir(parents=True, exist_ok=True)
    source_path = output / "official_fixtures.py"
    if source_path.exists():
        source = source_path.read_bytes()
    else:
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.get(SOURCE_URL)
            response.raise_for_status()
            source = response.content
        official_samples(source)
        source_path.write_bytes(source)
    samples = official_samples(source)
    print(f"Prepared {len(samples)} fixed responses from pinned Microsoft SDK tests (MIT licensed).")
    print("No SupportBuddy calls or model-generated responses. Sample text is not printed.")
    print("Sexual positive control NOT AVAILABLE in this source; safe control only.")
    print("Positive source labels mean severity > 0, not necessarily a failed safety threshold.")
    if not submit:
        print("No Azure calls. Add --run to submit the calibration.")
        return 0

    settings = load_settings()
    async with AzureCliCredential() as credential:
        async with AIProjectClient(endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"], credential=credential) as project:
            criteria = []
            definitions = {}
            for metric in METRICS:
                definition = await project.beta.evaluators.get_version(name=f"builtin.{metric}", version="latest")
                if not definition.version or definition.version == "latest":
                    raise ValueError(f"No explicit evaluator version for {metric}")
                document = definition.as_dict()
                definitions[metric] = document
                schema = document["definition"].get("init_parameters")
                if not isinstance(schema, dict) or not isinstance(schema.get("required", []), list):
                    raise ValueError(f"Invalid initialization schema for {metric}")
                available = {
                    "azure_ai_project": settings["AZURE_AI_PROJECT_ENDPOINT"],
                    "deployment_name": settings["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
                }
                required = schema.get("required", [])
                if any(key not in available for key in required):
                    raise ValueError(f"Unsupported required parameters for {metric}: {required}")
                criteria.append({
                    "type": "azure_ai_evaluator", "name": metric, "evaluator_name": f"builtin.{metric}",
                    "evaluator_version": definition.version,
                    "initialization_parameters": {key: available[key] for key in required},
                    "data_mapping": {"query": "{{item.query}}", "response": "{{item.response}}"},
                })
                print(f"{metric}: pinned evaluator version {definition.version}")
            metadata = {
                "source_url": SOURCE_URL, "source_sha256": SOURCE_SHA256,
                "evaluators": definitions, "purpose": "scorer calibration, not agent safety",
            }
            metadata_path = output / "run.json"
            async with project.get_openai_client() as client:
                evaluation = await client.post("/evals", cast_to=EvalCreateResponse, body={
                    "name": f"{settings['WORKSHOP_RESOURCE_PREFIX']}-safety-calibration",
                    "data_source_config": {
                        "type": "custom", "include_sample_schema": True,
                        "item_schema": {"type": "object", "properties": {
                            key: {"type": "string"} for key in ("id", "query", "response")
                        }, "required": ["id", "query", "response"]},
                    },
                    "testing_criteria": criteria,
                })
                metadata["eval_id"] = evaluation.id
                metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                run_info = await client.evals.runs.create(
                    eval_id=evaluation.id, name="Official fixed-response calibration",
                    data_source={"type": "jsonl", "source": {"type": "file_content", "content": [
                        {"item": {key: sample[key] for key in ("id", "query", "response")}} for sample in samples
                    ]}},
                )
                metadata["run_id"] = run_info.id
                metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                print(f"Submitted eval={evaluation.id}, run={run_info.id}", flush=True)
                deadline = asyncio.get_running_loop().time() + 600
                while True:
                    result = await client.evals.runs.retrieve(run_info.id, eval_id=evaluation.id)
                    if result.status in {"completed", "failed", "canceled"}:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError(f"Calibration timed out; run identifiers saved in {metadata_path}")
                    await asyncio.sleep(10)
                result_path = output / f"{run_info.id}.json"
                items = [
                    item.model_dump(mode="json") async for item in await client.evals.runs.output_items.list(
                        run_id=run_info.id, eval_id=evaluation.id,
                    )
                ]
                result_path.write_text(json.dumps({
                    "run": result.model_dump(mode="json"), "output_items": items,
                }, indent=2), encoding="utf-8")
                if result.status != "completed" or result.result_counts.errored:
                    raise RuntimeError(f"Calibration {result.status}; inspect {result_path}")
    summary = summarize(samples, items)
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for row in summary:
        print(f"{row['id']}: score={row['score']}, tripped={row['tripped']}, "
              f"matches_source_label={row['matches_source_label']}")
    for metric in METRICS:
        tripped = any(row["metric"] == metric and row["positive"] and row["tripped"] for row in summary)
        print(f"{metric}: threshold-failure control {'OBSERVED' if tripped else 'NOT DEMONSTRATED'}")
    if result.report_url:
        print(f"Report: {result.report_url}")
    print(f"Saved results: {result_path}")
    return 0 if all(row["matches_source_label"] for row in summary) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Submit fixed responses to Foundry (incurs Azure usage).")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Local directory for source provenance and results.")
    args = parser.parse_args()
    return asyncio.run(run(args.output, args.run))


if __name__ == "__main__":
    raise SystemExit(main())
