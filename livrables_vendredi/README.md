# Livrables du vendredi — Umi

Ce dossier compare Qwen sans RAG (V1) aux réponses réellement renvoyées par
`POST /ask` avec RAG (V2). Le catalogue et les sources sont ceux exposés par
`GET /aromes` et `/ask` ; aucune métrique interne de retrieval ou SQL n'est
présentée comme une réponse utilisateur.

## Parcours des données dans l'API

- `GET /aromes` expose le catalogue utilisé pour caractériser les données de
  démonstration ; sa réponse est archivée dans `catalogue_api.json`.
- `POST /ask` expose les réponses conversationnelles, leur statut, leur texte,
  les suggestions et les sources structurées renvoyées au client. Les réponses
  de l'évaluation sont archivées dans `resultats_api_ask.json` et
  `resultats_api_ask.csv`.
- `metriques_api_ask.json` résume uniquement ces réponses API : statut final,
  présence de la cible attendue dans les sources, conformité des sources aux
  critères du cas, caractère synthétique et latence complète de `/ask`.
- `metriques_sans_rag.json` mesure Qwen seul, sans catalogue récupéré ; le
  libellé de la cible est vérifié après génération à partir de la réponse
  `GET /aromes`, sans transmettre ce catalogue au modèle.

L'évaluation appelle l'API HTTP locale pour les 30 cas de référence et préremplit
l'historique de qualification pour que chaque requête arrive à l'étape de
recommandation. La latence mesurée comprend l'API, l'embedding, le retrieval,
le filtrage et la réponse Qwen.

Pour la comparaison, **V1** désigne la configuration Qwen sans RAG et **V2**
le parcours actuel avec RAG via `POST /ask`. Ces noms décrivent les deux
configurations évaluées, pas deux déploiements historiques distincts.

Sur les mêmes 30 requêtes, le nom exact de la cible attendue apparaît dans
0/30 réponses de Qwen sans RAG et dans 29/30 réponses de l'API avec RAG. Pour
`AGR-01`, Qwen seul a répondu « Citron 3 Sucrosité » au lieu du libellé de
catalogue « Citron - boissons » ; avec RAG, l'API a retourné la source
`AR-citron-boissons`. Le critère V1 est une correspondance textuelle stricte et
ne compte pas les paraphrases.

Lors de la vérification locale, `GET /aromes` a renvoyé 201 entrées ; les
201 comportaient `is_synthetic: true`. Le catalogue archivé est la réponse de
l'API, pas un export SQL direct.

Sur les 30 appels HTTP à `/ask`, l'API a renvoyé `recommendations_ready` 30
fois. La cible de référence apparaît dans les sources 29 fois (96,7 %). Les
90 sources retournées respectent les critères annotés et sont toutes marquées
synthétiques. La latence complète mesurée est de 31,91 s en moyenne, 33,02 s
en médiane et 43,63 s au p95, sur cette machine locale.

## Lecture de la comparaison V1/V2

La comparaison repose sur les mêmes 30 questions et les réponses réelles
archivées dans `resultats_sans_rag.json` et `resultats_api_ask.json`.

## Contenu

- [Procédure de reproduction](./procedure_reproduction.md)
- [Comparaison V1/V2 fondée sur l'API](./comparaison_v1_v2.md)
- [Limites et biais de l'évaluation API](./limites_biais.md)
- [Trame de démonstration](./trame_demo.md)
- `evaluer_api_ask.py` — appelle `/aromes`, `/openapi.json` et `/ask`, puis
  produit les artefacts API listés ci-dessus.
- `evaluer_modele_sans_rag.py` — envoie les mêmes demandes au modèle seul,
  sans lui fournir de catalogue, et archive ses réponses.
- `resultats_sans_rag.json` et `metriques_sans_rag.json` — réponses et mesures
  de la configuration sans RAG.

Le script utilise le jeu `livrables_jeudi/cas_reference_30.jsonl` et ne modifie
pas les anciens résultats de retrieval dans `livrables_jeudi/`.

## Données et provenance

Selon l'équipe projet, les critères ont été définis à partir d'un catalogue
public de Givaudan, puis un script a généré les enregistrements synthétiques.
Le point de départ pour les chiffres présentés ici est toutefois la réponse
effective de `GET /aromes` et les sources retournées par `POST /ask`. Les
champs commerciaux synthétiques ne sont pas des données produits vérifiées.

## Validation

Les 33 tests unitaires présents dans `tests/` passent dans l'environnement
local vérifié le 13 novembre 2026. Les réponses et métriques des deux
configurations sont archivées dans ce dossier.
