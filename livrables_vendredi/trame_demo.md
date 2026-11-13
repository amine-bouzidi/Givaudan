# Données et résultats de l'évaluation

## Deux configurations évaluées

La comparaison oppose deux usages du même modèle Qwen sur les mêmes 30
demandes de référence :

- **V1 — sans RAG :** le modèle reçoit la demande et l'historique, sans
  catalogue ni documents récupérés.
- **V2 — avec RAG :** l'API `/ask` utilise le catalogue et retourne une réponse
  accompagnée de sources structurées.

Le taux de cible de V1 est calculé par recherche du nom exact du produit attendu
dans la réponse textuelle. Pour V2, la cible est recherchée dans les sources
retournées par l'API ; les métriques détaillées sont conservées dans
`metriques_sans_rag.json` et `metriques_api_ask.json`.

## Résultats observés

| Indicateur | V1 — modèle seul | V2 — API avec RAG |
|---|---:|---:|
| Libellé exact de la cible dans la réponse | 0/30 (0 %) | 29/30 (96,7 %) |
| Réponses recommandées | Réponse textuelle libre | 30/30 `recommendations_ready` |
| Provenance catalogue structurée | Non disponible | 90 sources retournées |
| Sources conformes aux critères annotés | Non applicable | 90/90 (100 %) |
| Données de catalogue synthétiques | Aucune source fournie au modèle | 90/90 sources marquées synthétiques |
| Latence mesurée | Modèle seul : moyenne 5,99 s, médiane 5,26 s, p95 11,58 s | HTTP `/ask` complet : moyenne 31,91 s, médiane 33,02 s, p95 43,63 s |

Pour `AGR-01`, Qwen seul a proposé « Citron 3 Sucrosité » ; le libellé de la
cible dans le catalogue exposé par l'API est « Citron - boissons ». Avec RAG,
`/ask` a retourné la source `AR-citron-boissons`.

Pour V1, le libellé de la cible est celui retourné par `GET /aromes` ; cette
évaluation stricte ne compte pas les paraphrases. Les latences V1 et V2 portent
sur des périmètres différents : modèle seul contre chaîne HTTP complète.

## Exemple de réponse API

Pour `AGR-01`, V2 a renvoyé le statut `recommendations_ready` et les sources
`AR-citron-boissons` et `AR-lime-boissons`, marquées `is_synthetic: true`.
Elles indiquent toutes deux Agrumes, l'application boissons et une sucrosité
de 3. La réponse de l'API expose ainsi les recommandations et les informations
permettant de les rattacher au jeu de démonstration.

## Données de démonstration

La réponse de `GET /aromes`, archivée dans `catalogue_api.json`, contient 201
arômes ; les 201 sont indiqués comme synthétiques. Selon l'équipe projet, les
critères sont issus d'un catalogue public de Givaudan et les enregistrements
ont ensuite été générés par script. Les valeurs commerciales du jeu sont
fictives.

## Portée

Sur ce jeu synthétique, V2 retrouve la cible attendue dans les sources de
29 demandes sur 30. Pour `GOU-01`, la cible `AR-biscuit-biscuits` n'est pas
renvoyée ; l'API propose à la place trois alternatives respectant les critères
annotés. Ces résultats décrivent les 30 cas testés et ne constituent pas une
mesure de qualité en production.
