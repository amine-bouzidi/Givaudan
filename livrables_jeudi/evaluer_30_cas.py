"""Runs the 30-case validation suite against the local Umi API and models."""

import csv
import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import api
import config
import rag
from database import get_connection


OUTPUT_DIR = Path(__file__).resolve().parent
CASES_PATH = OUTPUT_DIR / "cas_reference_30.jsonl"


def load_cases() -> list[dict]:
    with CASES_PATH.open(encoding="utf-8") as file:
        cases = [json.loads(line) for line in file if line.strip()]
    if len(cases) != 30:
        raise ValueError(f"30 cas attendus, {len(cases)} trouvés.")
    return cases


def no_requirement_history() -> list[dict[str, str]]:
    return [
        {
            "role": "assistant",
            "content": api.CLARIFICATION_QUESTIONS["heat"],
        },
        {"role": "user", "content": "Je ne sais pas"},
        {
            "role": "assistant",
            "content": api.CLARIFICATION_QUESTIONS["certifications"],
        },
        {"role": "user", "content": "Aucune exigence de certification"},
    ]


def source_is_compliant(source: dict, case: dict) -> bool:
    return (
        source.get("famille") == case["expected_family"]
        and case["expected_application"] in source.get("applications", [])
        and source.get("sucrosite") == case["expected_sucrosite"]
        and (
            case["expected_heat"] is None
            or source.get("tenue_chaleur") == case["expected_heat"]
        )
    )


def verify_reference_targets(cases: list[dict]) -> int:
    target_ids = [case["expected_target_id"] for case in cases]
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.food_aromas")
            catalogue_row_count = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT aroma_id, famille, applications, sucrosite, tenue_chaleur
                FROM public.food_aromas
                WHERE aroma_id = ANY(%s)
                """,
                (target_ids,),
            )
            actual = {row[0]: row[1:] for row in cursor.fetchall()}

    missing = set(target_ids) - actual.keys()
    if missing:
        raise ValueError(f"Cibles absentes du catalogue : {sorted(missing)}")

    for case in cases:
        family, applications, sweetness, heat = actual[case["expected_target_id"]]
        if (
            family != case["expected_family"]
            or case["expected_application"] not in applications
            or sweetness != case["expected_sucrosite"]
            or (
                case["expected_heat"] is not None
                and heat != case["expected_heat"]
            )
        ):
            raise ValueError(
                f"Annotations de référence incohérentes : {case['id']}"
            )

    return catalogue_row_count


def percentile(values: list[float], value: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * value
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--end-to-end",
        action="store_true",
        help=(
            "Appelle aussi Qwen via /ask pour chaque cas; cela peut prendre "
            "plusieurs minutes sur une machine locale."
        ),
    )
    args = parser.parse_args()
    cases = load_cases()
    catalogue_row_count = verify_reference_targets(cases)
    collection = rag.get_collection()
    results = []
    elapsed_values = []
    family_correct = 0
    application_correct = 0
    vector_hit_at_4 = 0
    reciprocal_ranks = []
    sql_target_hit_at_4 = 0
    compliant_sources = 0
    returned_sql_rows = 0
    end_to_end_target_hit = 0
    end_to_end_source_count = 0
    fallback_count = 0

    for case in cases:
        detected_family = api.detect_family(case["question"])
        detected_application = api.detect_application(case["question"])
        family_correct += detected_family == case["expected_family"]
        application_correct += (
            detected_application == case["expected_application"]
        )

        started = time.perf_counter()
        documents = rag.search_aromas(case["question"])
        sql_rows, *_ = api.search_catalogue(
            case["question"],
            documents,
            top_k=4,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        elapsed_values.append(elapsed_ms)

        vector_top_ids = [
            str(item["metadata"]["source_id"])
            for item in documents[:4]
        ]
        vector_rank = (
            vector_top_ids.index(case["expected_target_id"]) + 1
            if case["expected_target_id"] in vector_top_ids
            else None
        )
        vector_hit = vector_rank is not None
        vector_hit_at_4 += vector_hit
        sql_top_ids = [str(row["aroma_id"]) for row in sql_rows]
        sql_hit = case["expected_target_id"] in sql_top_ids
        sql_target_hit_at_4 += sql_hit
        reciprocal_ranks.append(1 / vector_rank if vector_rank else 0)
        returned_sql_rows += len(sql_rows)
        compliant_sources += sum(
            source_is_compliant({
                "famille": row["famille"],
                "applications": row["applications"],
                "sucrosite": row["sucrosite"],
                "tenue_chaleur": row["tenue_chaleur"],
            }, case)
            for row in sql_rows
        )

        end_to_end = None
        if args.end_to_end:
            response = api.ask(api.SearchRequest(
                question=case["question"],
                top_k=4,
                history=no_requirement_history(),
            ))
            sources = response["sources"]
            source_ids = [str(source["source_id"]) for source in sources]
            target_hit = case["expected_target_id"] in source_ids
            end_to_end_target_hit += target_hit
            end_to_end_source_count += len(sources)
            fallback = response["answer"].startswith(
                "Je n’ai pas trouvé d’arôme respectant tous vos critères."
            )
            fallback_count += fallback
            end_to_end = {
                "status": response["status"],
                "answer": response["answer"],
                "suggestions": response.get("suggestions", []),
                "source_ids": source_ids,
                "target_hit": target_hit,
                "fallback_used": fallback,
            }

        result = {
            "id": case["id"],
            "expected_target_id": case["expected_target_id"],
            "detected_family": detected_family,
            "expected_family": case["expected_family"],
            "family_correct": detected_family == case["expected_family"],
            "detected_application": detected_application,
            "expected_application": case["expected_application"],
            "application_correct": (
                detected_application == case["expected_application"]
            ),
            "vector_top4_ids": vector_top_ids,
            "vector_target_hit_at_4": vector_hit,
            "vector_target_rank": vector_rank,
            "sql_top4_ids": sql_top_ids,
            "sql_target_hit_at_4": sql_hit,
            "latency_ms": round(elapsed_ms, 2),
            "end_to_end": end_to_end,
        }
        results.append(result)
        print(
            f"{case['id']}: vector_hit@4={vector_hit}, "
            f"sql_target@4={sql_hit}, "
            f"{elapsed_ms:.0f} ms"
        )

    latencies = [float(item["latency_ms"]) for item in results]
    metrics = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(CASES_PATH.name),
        "case_count": len(cases),
        "catalogue_row_count": catalogue_row_count,
        "chroma_collection": config.COLLECTION_NAME,
        "chroma_document_count": collection.count(),
        "llm_model": config.LLM_MODEL,
        "embedding_model": config.EMBED_MODEL,
        "family_detection_accuracy": family_correct / len(cases),
        "application_detection_accuracy": application_correct / len(cases),
        "vector_target_hit_rate_at_4": vector_hit_at_4 / len(cases),
        "vector_mrr_at_4": statistics.mean(reciprocal_ranks),
        "sql_target_hit_rate_at_4": sql_target_hit_at_4 / len(cases),
        "sql_result_constraint_compliance": (
            compliant_sources / returned_sql_rows
            if returned_sql_rows
            else None
        ),
        "sql_result_count": returned_sql_rows,
        "end_to_end_run": args.end_to_end,
        "end_to_end_api_target_hit_rate": (
            end_to_end_target_hit / len(cases) if args.end_to_end else None
        ),
        "end_to_end_source_count": (
            end_to_end_source_count if args.end_to_end else None
        ),
        "fallback_case_count": fallback_count if args.end_to_end else None,
        "latency_ms_mean": round(statistics.mean(latencies), 2),
        "latency_ms_median": round(statistics.median(latencies), 2),
        "latency_ms_p95": round(percentile(latencies, 0.95), 2),
        "scope_note": (
            "Prototype integration/regression set over synthetic catalogue "
            "records. Not an independent, blinded or production validation."
        ),
    }

    results_path = OUTPUT_DIR / "resultats_30.json"
    metrics_path = OUTPUT_DIR / "metriques_30.json"
    csv_path = OUTPUT_DIR / "resultats_30.csv"
    results_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)

    print("\nMETRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"\nFichiers générés dans {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
