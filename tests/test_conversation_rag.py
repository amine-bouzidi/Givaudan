import json
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

import api
import rag


def aroma_row(aroma_id: str, name: str) -> dict:
    return {
        "aroma_id": aroma_id,
        "nom": name,
        "famille": "Fruits rouges",
        "notes": "Fruité",
        "applications": ["boissons"],
        "sucrosite": 2,
        "tenue_chaleur": "élevée",
        "certifications": [],
        "prix_indicatif_eur_kg": None,
        "delai_jours": None,
        "volume_minimum_kg": None,
        "is_synthetic": True,
        "source_information": "Donnée de démonstration",
    }


class AromaSearchTests(unittest.TestCase):
    def test_citrus_family_and_biscuit_application_are_detected_separately(self):
        question = "Je cherche un arôme citron pour un biscuit."

        self.assertEqual(api.detect_family(question), "Agrumes")
        self.assertEqual(api.detect_application(question), "biscuits")
        self.assertIsNone(api.detect_family("un arôme pour biscuit"))

    def test_build_source_exposes_frontend_card_fields(self):
        row = {
            **aroma_row("AR-1", "Citron - biscuits"),
            "famille": "Agrumes",
            "applications": ["biscuits"],
            "sucrosite": 3,
            "tenue_chaleur": "élevée",
            "prix_indicatif_eur_kg": Decimal("12.50"),
            "delai_jours": 10,
            "_similarity_score": 0.92,
        }

        source = api.build_source(row)

        self.assertEqual(source.nom, "Citron - biscuits")
        self.assertEqual(source.famille, "Agrumes")
        self.assertEqual(source.notes, "Fruité")
        self.assertEqual(source.applications, ["biscuits"])
        self.assertEqual(source.sucrosite, 3)
        self.assertEqual(source.tenue_chaleur, "élevée")
        self.assertEqual(source.prix, 12.5)
        self.assertEqual(source.delai, 10)

    def test_chat_forwards_response_schema_to_ollama(self):
        response_schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
        }

        with patch.object(
            rag.ollama,
            "chat",
            return_value={"message": {"content": '{"answer":"Bonjour"}'}},
        ) as ollama_chat:
            answer = rag.chat(
                [{"role": "user", "content": "Bonjour"}],
                response_format=response_schema,
            )

        self.assertEqual(answer, '{"answer":"Bonjour"}')
        self.assertEqual(
            ollama_chat.call_args.kwargs["format"],
            response_schema,
        )

    def test_search_aromas_queries_only_aromas_and_returns_cosine_scores(self):
        collection = MagicMock()
        collection.get.return_value = {"ids": ["AR-1", "AR-2"]}
        collection.query.return_value = {
            "documents": [["doc 1", "doc 2"]],
            "metadatas": [[
                {"source_id": "AR-1", "source_type": "arome"},
                {"source_id": "AR-2", "source_type": "arome"},
            ]],
            "distances": [[0.1, 0.4]],
        }

        with (
            patch.object(rag, "get_collection", return_value=collection),
            patch.object(rag, "embed_query", return_value=[0.1, 0.2]),
        ):
            results = rag.search_aromas("fruits rouges")

        self.assertEqual([item["score"] for item in results], [0.9, 0.6])
        self.assertEqual(collection.query.call_args.kwargs["n_results"], 2)
        self.assertEqual(
            collection.query.call_args.kwargs["where"],
            {"source_type": "arome"},
        )

    def test_sql_filters_candidates_then_sorts_by_cosine_similarity(self):
        connection = MagicMock()
        cursor_context = connection.cursor.return_value
        cursor = cursor_context.__enter__.return_value
        first_row = aroma_row("AR-1", "Fraise")
        second_row = aroma_row("AR-2", "Framboise")
        cursor.fetchall.return_value = [second_row, first_row]
        semantic_documents = [
            {"metadata": {"source_id": "AR-1"}, "score": 0.95},
            {"metadata": {"source_id": "AR-2"}, "score": 0.65},
        ]

        with patch.object(api, "get_connection", return_value=connection):
            (
                rows,
                family,
                application,
                numeric,
                heat,
                certifications,
            ) = api.search_catalogue(
                "fruits rouges pour boisson pas trop sucré pasteurisé",
                semantic_documents,
                1,
            )

        self.assertEqual([row["aroma_id"] for row in rows], ["AR-1"])
        self.assertEqual(rows[0]["_similarity_score"], 0.95)
        self.assertEqual(family, "Fruits rouges")
        self.assertEqual(application, "boissons")
        self.assertEqual(numeric[0]["operator"], "<=")
        self.assertEqual(numeric[0]["value"], Decimal("3"))
        self.assertEqual(heat, "élevée")
        self.assertEqual(certifications, [])

        query, parameters = cursor.execute.call_args.args
        self.assertIn("aroma_id = ANY(%s)", query)
        self.assertIn("famille ILIKE %s", query)
        self.assertIn("unnest(applications)", query)
        self.assertIn("sucrosite <= %s", query)
        self.assertIn("tenue_chaleur = %s", query)
        self.assertEqual(parameters[0], ["AR-1", "AR-2"])

    def test_qwen_uses_history_and_catalogue_to_write_the_reply(self):
        row = aroma_row("AR-1", "Fraise")
        row["_similarity_score"] = 0.91
        history = [
            {"role": "user", "content": "Je cherche un arôme fruité."},
            {"role": "assistant", "content": "Pour quel produit ?"},
        ]

        with patch.object(
            api.rag,
            "chat",
            return_value=json.dumps({
                "status": "recommendations_ready",
                "clarification": "none",
                "recommended_ids": ["AR-1"],
            }),
        ) as chat:
            result = api.generate_client_guidance(
                "Pour une boisson, s'il te plaît.",
                history,
                [row],
                "Fruits rouges",
                "boissons",
                [],
                None,
                [],
            )

        self.assertEqual(result["status"], "recommendations_ready")
        self.assertIn("Fraise", result["answer"])
        self.assertEqual(result["recommended_ids"], ["AR-1"])
        messages = chat.call_args.args[0]
        self.assertEqual(messages[1], history[0])
        self.assertEqual(messages[2], history[1])
        self.assertIn("AR-1", messages[-1]["content"])
        self.assertIn("Fruits rouges", messages[-1]["content"])
        self.assertEqual(
            chat.call_args.kwargs["response_format"]["properties"]
            ["recommended_ids"]["items"]["enum"],
            ["AR-1"],
        )
        response_schema = chat.call_args.kwargs["response_format"]
        self.assertEqual(
            response_schema["required"],
            ["status", "clarification", "recommended_ids"],
        )
        self.assertNotIn("answer", response_schema["properties"])

    def test_clarification_is_selected_from_safe_questions(self):
        with patch.object(
            api.rag,
            "chat",
            return_value=json.dumps({
                "status": "needs_clarification",
                "clarification": "application",
                "recommended_ids": [],
            }),
        ):
            result = api.generate_client_guidance(
                "Je cherche un arôme.",
                [],
                [],
                None,
                None,
                [],
                None,
                [],
            )

        self.assertEqual(result["status"], "needs_clarification")
        self.assertIn("Pour quel type de produit", result["answer"])

    def test_ask_collects_criteria_before_searching_or_recommending(self):
        request = api.SearchRequest(
            question="Je veux un arôme sucré.",
            history=[],
        )

        with (
            patch.object(
                api,
                "choose_next_qualification",
                return_value="application",
            ) as choose_next,
            patch.object(api.rag, "search_aromas") as semantic_search,
        ):
            response = api.ask(request)

        self.assertEqual(response["status"], "needs_clarification")
        self.assertEqual(
            response["answer"],
            api.CLARIFICATION_QUESTIONS["application"],
        )
        self.assertEqual(response["sources"], [])
        semantic_search.assert_not_called()
        choose_next.assert_called_once()

    def test_ask_has_hard_guard_for_family_and_application(self):
        request = api.SearchRequest(
            question="Je cherche un arôme certifié bio.",
            history=[],
        )

        with (
            patch.object(api, "get_missing_qualifications", return_value=[]),
            patch.object(
                api,
                "choose_next_qualification",
                return_value="application",
            ) as choose_next,
            patch.object(api.rag, "search_aromas") as semantic_search,
        ):
            response = api.ask(request)

        self.assertEqual(response["status"], "needs_clarification")
        self.assertEqual(response["answer"], api.CLARIFICATION_QUESTIONS["application"])
        self.assertEqual(response["suggestions"], [])
        self.assertEqual(
            choose_next.call_args.args[3],
            ["family", "application"],
        )
        semantic_search.assert_not_called()

    def test_qwen_chooses_only_among_missing_qualification_fields(self):
        with patch.object(
            api.rag,
            "chat",
            return_value=json.dumps({"next_field": "family"}),
        ) as chat:
            result = api.choose_next_qualification(
                "boissons",
                [{"role": "user", "content": "boissons"}],
                "boissons",
                ["family", "heat"],
            )

        self.assertEqual(result, "family")
        self.assertEqual(
            chat.call_args.kwargs["response_format"]["properties"]
            ["next_field"]["enum"],
            ["family", "heat"],
        )

    def test_client_can_start_with_family_or_application(self):
        question = "Je cherche des fruits rouges pour une boisson."
        conversation = api.get_conversation_text(question, [])

        self.assertEqual(
            api.get_missing_qualifications(question, [], conversation),
            ["sweetness", "heat", "certifications"],
        )

    def test_conversation_records_short_answers_to_asked_fields(self):
        conversation = api.get_conversation_text(
            "3",
            [{"role": "assistant", "content": "Quel niveau de sucrosité ?"}],
        )

        self.assertIn("sucrosite = 3", conversation)
        self.assertIsNone(
            api.get_missing_qualification(
                "Je veux une boisson, fruits rouges, sucrosité = 3, "
                "pasteurisé, bio.",
                [],
                "Je veux une boisson, fruits rouges, sucrosité = 3, "
                "pasteurisé, bio.",
            )
        )

    def test_qualification_tracks_answers_and_does_not_repeat_questions(self):
        history = []
        turns = [
            ("Je veux un arôme peu sucré.", "application"),
            ("boissons", "family"),
            ("fruits rouges", "heat"),
            ("je ne l'ai pas", "certifications"),
        ]

        with patch.object(
            api,
            "choose_next_qualification",
            side_effect=["application", "family", "heat", "certifications"],
        ):
            for question, expected_slot in turns:
                response = api.ask(api.SearchRequest(
                    question=question,
                    history=history,
                ))
                self.assertEqual(response["status"], "needs_clarification")
                self.assertEqual(
                    response["answer"],
                    api.CLARIFICATION_QUESTIONS[expected_slot],
                )
                history.extend([
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": response["answer"]},
                ])

        final_question = "aucune exigence de certification"
        row = aroma_row("AR-1", "Fraise")
        row["_similarity_score"] = 0.9
        with (
            patch.object(
                api.rag,
                "search_aromas",
                return_value=[{
                    "metadata": {"source_id": "AR-1"},
                    "score": 0.9,
                }],
            ),
            patch.object(
                api,
                "search_catalogue",
                return_value=(
                    [row],
                    "Fruits rouges",
                    "boissons",
                    [{
                        "column": "sucrosite",
                        "label": "sucrosité",
                        "operator": "<=",
                        "value": Decimal("3"),
                    }],
                    None,
                    [],
                ),
            ),
            patch.object(
                api,
                "generate_client_guidance",
                return_value={
                    "status": "recommendations_ready",
                    "answer": "Demande qualifiée.",
                    "recommended_ids": ["AR-1"],
                },
            ) as generate_reply,
        ):
            response = api.ask(api.SearchRequest(
                question=final_question,
                history=history,
            ))

        self.assertEqual(response["status"], "recommendations_ready")
        generate_reply.assert_called_once()

    def test_unknown_heat_answer_counts_as_no_heat_requirement(self):
        history = [
            {"role": "user", "content": "Je veux un arôme peu sucré."},
            {
                "role": "assistant",
                "content": api.CLARIFICATION_QUESTIONS["application"],
            },
            {"role": "user", "content": "boissons"},
            {
                "role": "assistant",
                "content": api.CLARIFICATION_QUESTIONS["family"],
            },
            {"role": "user", "content": "fruits rouges"},
            {
                "role": "assistant",
                "content": api.CLARIFICATION_QUESTIONS["heat"],
            },
        ]
        conversation = api.get_conversation_text("je ne sais pas", history)

        self.assertEqual(
            api.get_missing_qualification(
                "je ne sais pas",
                history,
                conversation,
            ),
            "certifications",
        )

    def test_non_sweet_is_not_mistaken_for_no_preference(self):
        self.assertFalse(api.is_no_requirement("Je veux un arôme non sucré."))
        self.assertTrue(api.is_no_requirement("je ne l'ai pas"))
        self.assertTrue(api.is_no_requirement("je ne sais pas"))
        self.assertTrue(api.is_no_requirement("pas de contrainte de chaleur"))
        self.assertEqual(
            api.get_question_slot(
                "Quel niveau de sucrositÃ© recherchez-vous, de 1 Ã  5 ?"
            ),
            "sweetness",
        )

    def test_request_allows_history_for_longer_qualification(self):
        request = api.SearchRequest(
            question="suite",
            history=[
                {"role": "user", "content": str(index)}
                for index in range(24)
            ],
        )

        self.assertEqual(len(request.history), 24)

    def test_certification_filter_is_applied_in_sql(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        semantic_documents = [
            {"metadata": {"source_id": "AR-1"}, "score": 0.9},
        ]

        with patch.object(api, "get_connection", return_value=connection):
            api.search_catalogue(
                "boisson fruits rouges sucrosité = 3 pasteurisé bio",
                semantic_documents,
                4,
            )

        query, parameters = cursor.execute.call_args.args
        self.assertIn("unnest(certifications)", query)
        self.assertEqual(parameters[-1], ["%bio%"])

    def test_recommendation_answer_does_not_append_synthetic_warning(self):
        answer = api.format_recommendation_answer(
            [aroma_row("AR-1", "Fraise")],
            [],
            None,
        )

        self.assertNotIn("Attention :", answer)

    def test_ask_uses_retrieval_and_qwen_even_for_numeric_filters(self):
        row = aroma_row("AR-1", "Fraise")
        row["_similarity_score"] = 0.9
        request = api.SearchRequest(
            question=(
                "Je cherche une boisson, fruits rouges, sucrosité = 3, "
                "pasteurisé, certification bio."
            ),
            history=[],
        )

        with (
            patch.object(
                api.rag,
                "search_aromas",
                return_value=[{
                    "metadata": {"source_id": "AR-1"},
                    "score": 0.9,
                }],
            ) as semantic_search,
            patch.object(
                api,
                "search_catalogue",
                return_value=(
                    [row],
                    None,
                    None,
                    [{"column": "sucrosite", "operator": "<=", "value": 3}],
                    None,
                    ["bio"],
                ),
            ) as sql_search,
            patch.object(
                api,
                "generate_client_guidance",
                return_value={
                    "status": "recommendations_ready",
                    "answer": "La fraise est candidate.",
                    "recommended_ids": ["AR-1"],
                },
            ) as generate_reply,
        ):
            response = api.ask(request)

        semantic_search.assert_called_once()
        sql_search.assert_called_once()
        generate_reply.assert_called_once()
        self.assertEqual(response["answer"], "La fraise est candidate.")
        self.assertEqual(response["sources"][0]["score"], 0.9)

    def test_ask_recommends_closest_aromas_when_strict_filters_have_no_match(self):
        history = [
            {"role": "user", "content": "arôme très sucré"},
            {"role": "assistant", "content": api.CLARIFICATION_QUESTIONS["heat"]},
            {"role": "user", "content": "oui"},
            {"role": "assistant", "content": api.CLARIFICATION_QUESTIONS["application"]},
            {"role": "user", "content": "dessert"},
            {"role": "assistant", "content": api.CLARIFICATION_QUESTIONS["certifications"]},
            {"role": "user", "content": "halal"},
            {"role": "assistant", "content": api.CLARIFICATION_QUESTIONS["family"]},
        ]
        request = api.SearchRequest(
            question="fruits rouges",
            history=history,
        )
        closest_row = aroma_row("AR-2", "Framboise")
        closest_row["_similarity_score"] = 0.88

        with (
            patch.object(
                api.rag,
                "search_aromas",
                return_value=[{
                    "metadata": {"source_id": "AR-1"},
                    "score": 0.9,
                }],
            ),
            patch.object(
                api,
                "search_catalogue",
                side_effect=[
                    (
                        [],
                        "Fruits rouges",
                        "desserts",
                        [{
                            "label": "sucrosité",
                            "column": "sucrosite",
                            "operator": ">=",
                            "value": 4,
                        }],
                        "élevée",
                        ["halal"],
                    ),
                    ([closest_row], None, None, [], None, []),
                ],
            ) as search_catalogue,
            patch.object(api, "generate_client_guidance") as generate,
        ):
            response = api.ask(request)

        self.assertEqual(response["status"], "recommendations_ready")
        self.assertEqual(
            [source["source_id"] for source in response["sources"]],
            ["AR-2"],
        )
        self.assertEqual(response["suggestions"], ["Framboise"])
        self.assertTrue(
            response["answer"].startswith(
                "Je n’ai pas trouvé d’arôme respectant tous vos critères."
            )
        )
        self.assertIn("Framboise", response["answer"])
        self.assertIn("options les plus proches", response["answer"])
        self.assertIn("peuvent différer de vos contraintes", response["answer"])
        self.assertEqual(
            search_catalogue.call_args.kwargs["ignored_criteria"],
            {"all"},
        )
        generate.assert_not_called()

    def test_single_variant_name_does_not_select_similarly_named_aromas(self):
        proposal = api.format_recommendation_answer(
            [
                {**aroma_row("AR-1", "Biscuit - biscuits")},
                {**aroma_row("AR-2", "Biscuit - boissons")},
                {**aroma_row("AR-3", "Biscuit - desserts")},
            ],
            [],
            None,
        )
        history = [{"role": "assistant", "content": proposal}]

        selection_response = api.handle_selection_followup(
            "Je veux prendre Biscuit - biscuits",
            history,
        )

        self.assertIn("Quel volume souhaitez-vous commander", selection_response["answer"])
        self.assertNotIn("mélange", selection_response["answer"])
        self.assertIn("Biscuit - biscuits", selection_response["answer"])
        self.assertNotIn("Biscuit - boissons", selection_response["answer"])
        self.assertNotIn("Biscuit - desserts", selection_response["answer"])

    def test_quantity_before_named_aromas_is_not_treated_as_an_option_number(self):
        proposal = api.format_recommendation_answer(
            [
                {**aroma_row("AR-1", "Mangue - confiserie")},
                {**aroma_row("AR-2", "Mûre - confiserie")},
                {**aroma_row("AR-3", "Cerise - confiserie")},
            ],
            [],
            None,
        )
        history = [{"role": "assistant", "content": proposal}]

        selection_response = api.handle_selection_followup(
            "je veux prendre 2 aromes Cerise - confiserie "
            "(Fruits rouges) et Mangue - confiserie "
            "(Fruits tropicaux)",
            history,
        )

        self.assertIn("Vous avez choisi 2 arômes", selection_response["answer"])
        self.assertIn("Mangue - confiserie", selection_response["answer"])
        self.assertIn("Cerise - confiserie", selection_response["answer"])
        self.assertNotIn("Mûre - confiserie", selection_response["answer"])
        self.assertIn("séparément ou", selection_response["answer"])

    def test_explicit_option_numbers_select_only_the_referenced_aromas(self):
        options = [
            {"number": 1, "name": "Mangue - confiserie", "family": "Fruits tropicaux"},
            {"number": 2, "name": "Mûre - confiserie", "family": "Fruits rouges"},
            {"number": 3, "name": "Cerise - confiserie", "family": "Fruits rouges"},
        ]

        selected = api.match_selected_options("Je choisis les arômes 1 et 3", options)

        self.assertEqual(
            [option["number"] for option in selected],
            [1, 3],
        )

    def test_blend_requests_one_percentage_for_each_selected_aroma(self):
        proposal = api.format_recommendation_answer(
            [
                {**aroma_row("AR-1", "Biscuit - biscuits")},
                {**aroma_row("AR-2", "Biscuit - boissons")},
                {**aroma_row("AR-3", "Biscuit - desserts")},
            ],
            [],
            None,
        )
        history = [{"role": "assistant", "content": proposal}]
        mode_response = api.handle_selection_followup(
            "Je choisis Biscuit - biscuits, Biscuit - boissons et Biscuit - desserts",
            history,
        )
        self.assertIn("séparément ou", mode_response["answer"])

        history.append({"role": "assistant", "content": mode_response["answer"]})
        blend_response = api.handle_selection_followup("En mélange", history)

        self.assertIn("mélange de 3 arômes", blend_response["answer"])
        self.assertIn("indiquez 3 proportions", blend_response["answer"])
        history.append({"role": "assistant", "content": blend_response["answer"]})
        proportions_response = api.handle_selection_followup(
            "20 %, 30 %, 50 %",
            history,
        )

        self.assertIn("Quel volume total souhaitez-vous commander", proportions_response["answer"])

    def test_multiple_aromas_can_be_ordered_separately_with_each_minimum(self):
        first = {
            **aroma_row("AR-1", "Cassis - boissons"),
            "volume_minimum_kg": Decimal("5"),
        }
        second = {
            **aroma_row("AR-2", "Fraise - boissons"),
            "volume_minimum_kg": Decimal("15"),
        }
        proposal = api.format_recommendation_answer([first, second], [], None)
        history = [{"role": "assistant", "content": proposal}]

        mode_response = api.handle_selection_followup("1 et 2", history)
        history.append({"role": "assistant", "content": mode_response["answer"]})
        separate_response = api.handle_selection_followup("séparément", history)
        self.assertIn("minimum", separate_response["answer"])

        history.append({
            "role": "assistant",
            "content": separate_response["answer"],
        })
        invalid_response = api.handle_selection_followup("5 kg, 10 kg", history)
        self.assertEqual(invalid_response["status"], "needs_clarification")
        self.assertIn("Fraise - boissons : au moins 15 kg", invalid_response["answer"])

        history.append({
            "role": "assistant",
            "content": invalid_response["answer"],
        })
        final_response = api.handle_selection_followup("5 kg, 15 kg", history)
        self.assertEqual(final_response["status"], "recommendations_ready")
        self.assertIn("Cassis - boissons : 5 kg", final_response["answer"])
        self.assertIn("Fraise - boissons : 15 kg", final_response["answer"])

    def test_blend_volume_must_meet_lowest_selected_minimum(self):
        first = {
            **aroma_row("AR-1", "Cassis - boissons"),
            "volume_minimum_kg": Decimal("5"),
        }
        second = {
            **aroma_row("AR-2", "Fraise - boissons"),
            "volume_minimum_kg": Decimal("15"),
        }
        proposal = api.format_recommendation_answer([first, second], [], None)
        history = [{"role": "assistant", "content": proposal}]
        mode_response = api.handle_selection_followup("1 et 2", history)
        history.append({"role": "assistant", "content": mode_response["answer"]})
        blend_response = api.handle_selection_followup("en mélange", history)
        history.append({"role": "assistant", "content": blend_response["answer"]})
        proportions_response = api.handle_selection_followup("50 %, 50 %", history)
        self.assertIn("5 kg", proportions_response["answer"])
        history.append({
            "role": "assistant",
            "content": proportions_response["answer"],
        })

        too_small = api.handle_selection_followup("4 kg", history)
        self.assertEqual(too_small["status"], "needs_clarification")
        self.assertIn("minimum de commande pour ce mélange est de 5 kg", too_small["answer"])

        history.append({"role": "assistant", "content": too_small["answer"]})
        accepted = api.handle_selection_followup("5 kg", history)
        self.assertEqual(accepted["status"], "recommendations_ready")
        self.assertIn("volume total de 5 kg", accepted["answer"])

    def test_aroma_selection_asks_for_order_volume_and_concludes(self):
        proposal = api.format_recommendation_answer(
            [
                {**aroma_row("AR-1", "Mûre - desserts")},
                {**aroma_row("AR-2", "Cassis - desserts")},
            ],
            [],
            None,
        )
        history = [{"role": "assistant", "content": proposal}]

        selection_response = api.handle_selection_followup(
            "Je choisis la mûre",
            history,
        )
        self.assertIn("Excellent choix", selection_response["answer"])
        self.assertIn("Quel volume souhaitez-vous commander", selection_response["answer"])
        self.assertIn("Mûre - desserts", selection_response["answer"])

        history.append({
            "role": "assistant",
            "content": selection_response["answer"],
        })
        final_response = api.handle_selection_followup("5 kg", history)

        self.assertEqual(final_response["status"], "recommendations_ready")
        self.assertIn("Excellent choix", final_response["answer"])
        self.assertIn("5 kg", final_response["answer"])

    def test_single_aroma_order_enforces_catalogue_minimum_volume(self):
        row = {
            **aroma_row("AR-1", "Cassis - boissons"),
            "volume_minimum_kg": Decimal("15"),
        }
        proposal = api.format_recommendation_answer([row], [], None)
        history = [{"role": "assistant", "content": proposal}]

        selection_response = api.handle_selection_followup(
            "Je choisis Cassis - boissons",
            history,
        )
        self.assertIn("minimum de commande pour cet arôme est de 15 kg", selection_response["answer"])
        history.append({
            "role": "assistant",
            "content": selection_response["answer"],
        })

        too_small = api.handle_selection_followup("13 kg", history)
        self.assertEqual(too_small["status"], "needs_clarification")
        self.assertIn("volume minimum de commande", too_small["answer"])
        self.assertIn("15 kg", too_small["answer"])
        history.append({"role": "assistant", "content": too_small["answer"]})

        accepted = api.handle_selection_followup("15 kg", history)
        self.assertEqual(accepted["status"], "recommendations_ready")
        self.assertIn("15 kg", accepted["answer"])

    def test_invalid_order_volume_repeats_prompt_and_accepts_valid_volume(self):
        proposal = api.format_recommendation_answer(
            [{**aroma_row("AR-1", "Mûre - desserts")}],
            [],
            None,
        )
        history = [{"role": "assistant", "content": proposal}]
        selection_response = api.handle_selection_followup("1", history)
        history.append({
            "role": "assistant",
            "content": selection_response["answer"],
        })

        invalid_response = api.handle_selection_followup("zéro", history)
        self.assertEqual(invalid_response["status"], "needs_clarification")
        self.assertIn("volume positif", invalid_response["answer"])
        self.assertIn("Quel volume souhaitez", invalid_response["answer"])
        history.append({
            "role": "assistant",
            "content": invalid_response["answer"],
        })

        final_response = api.handle_selection_followup("2 kg", history)

        self.assertEqual(final_response["status"], "recommendations_ready")
        self.assertIn("2 kg", final_response["answer"])

    def test_blend_requires_proportions_totaling_100_percent_then_volume(self):
        proposal = api.format_recommendation_answer(
            [
                {**aroma_row("AR-1", "Mûre - desserts")},
                {**aroma_row("AR-2", "Cassis - desserts")},
            ],
            [],
            None,
        )
        history = [{"role": "assistant", "content": proposal}]
        mode_response = api.handle_selection_followup(
            "Je choisis les arômes 1 et 2 en mélange",
            history,
        )
        history.append({"role": "assistant", "content": mode_response["answer"]})
        blend_response = api.handle_selection_followup("en mélange", history)
        self.assertIn("somme doit être exactement égale à 100 %", blend_response["answer"])

        history.append({"role": "assistant", "content": blend_response["answer"]})
        invalid_response = api.handle_selection_followup("60 %, 20 %", history)
        self.assertIn("totalisent 80 %", invalid_response["answer"])

        history.append({"role": "assistant", "content": invalid_response["answer"]})
        proportions_response = api.handle_selection_followup("60 %, 40 %", history)
        self.assertIn("Quel volume total souhaitez-vous commander", proportions_response["answer"])

        history.append({
            "role": "assistant",
            "content": proportions_response["answer"],
        })
        final_response = api.handle_selection_followup("10 kg", history)

        self.assertEqual(final_response["status"], "recommendations_ready")
        self.assertIn("Excellent choix", final_response["answer"])
        self.assertIn("Mûre - desserts : 60 %", final_response["answer"])
        self.assertIn("Cassis - desserts : 40 %", final_response["answer"])
        self.assertIn("10 kg", final_response["answer"])

    def test_blend_rejects_proportions_that_do_not_sum_to_100(self):
        options = [
            {"number": 1, "name": "Mûre - desserts", "family": "Fruits rouges"},
            {"number": 2, "name": "Cassis - desserts", "family": "Fruits rouges"},
        ]
        proportions_prompt = api.format_proportions_prompt(options)
        history = [{"role": "assistant", "content": proportions_prompt}]

        response = api.handle_selection_followup("60 %, 30 %", history)

        self.assertEqual(response["status"], "needs_clarification")
        self.assertIn("totalisent 90 %", response["answer"])
        self.assertIn("somme", response["answer"])

    def test_ask_carries_previous_turns_into_retrieval_and_qwen(self):
        row = aroma_row("AR-1", "Fraise")
        row["_similarity_score"] = 0.9
        request = api.SearchRequest(
            question=(
                "Pour une boisson, famille fruits rouges, sucrosité = 3, "
                "pasteurisé et certification bio."
            ),
            history=[
                {"role": "user", "content": "Je cherche de la vanille."},
                {
                    "role": "assistant",
                    "content": "Dans quel produit alimentaire ?",
                },
            ],
        )

        with (
            patch.object(
                api.rag,
                "search_aromas",
                return_value=[{
                    "metadata": {"source_id": "AR-1"},
                    "score": 0.9,
                }],
            ) as semantic_search,
            patch.object(
                api,
                "search_catalogue",
                return_value=(
                    [row],
                    "Fruits rouges",
                    "boissons",
                    [{"column": "sucrosite", "operator": "=", "value": 3}],
                    "élevée",
                    ["bio"],
                ),
            ),
            patch.object(
                api,
                "generate_client_guidance",
                return_value={
                    "status": "needs_clarification",
                    "answer": "Quel niveau de sucrosité préférez-vous ?",
                },
            ) as generate_reply,
        ):
            response = api.ask(request)

        retrieval_query = semantic_search.call_args.args[0]
        self.assertIn("vanille", retrieval_query)
        self.assertIn("boisson", retrieval_query)
        self.assertEqual(
            generate_reply.call_args.args[1][0]["content"],
            "Je cherche de la vanille.",
        )
        self.assertEqual(response["status"], "needs_clarification")


if __name__ == "__main__":
    unittest.main()
