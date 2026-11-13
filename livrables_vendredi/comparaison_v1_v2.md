# Comparaison V1 / V2 : modèle seul et modèle avec RAG

## Cadre de la comparaison

Dans cette comparaison, V1 désigne Qwen utilisé seul, sans catalogue ni
documents récupérés. V2 désigne l'API Umi `/ask`, qui utilise le même modèle
Qwen avec le RAG et renvoie des sources de catalogue structurées. Il s'agit
des deux configurations comparées pour le projet, et non de deux déploiements
historiques distincts.

Les deux configurations reçoivent les mêmes 30 demandes et le même historique
initial. Pour V1, le modèle ne reçoit aucun arôme du catalogue ; après sa
réponse, le libellé de la cible attendue est recherché dans le texte. Pour V2,
la cible est vérifiée dans les sources réellement retournées par `/ask`.
L'évaluation du libellé V1 est stricte : elle ne reconnaît pas les paraphrases.

## Résultats sur le jeu de référence

| Mesure | V1 — Qwen sans RAG | V2 — API `/ask` avec RAG |
|---|---:|---:|
| Réponses contenant le libellé exact de la cible | 0/30 (0 %) | 29/30 (96,7 %) |
| Cible présente dans une source structurée | Non applicable, aucune source RAG | 29/30 (96,7 %) |
| Sources structurées renvoyées | Aucune source récupérée | 90 sources sur 30 réponses |
| Sources respectant les critères annotés | Non applicable sans sources | 90/90 (100 %) |
| Sources identifiées comme synthétiques | Aucune | 90/90 (100 %) |
| Latence mesurée | moyenne 5,99 s ; médiane 5,26 s ; p95 11,58 s (modèle seul) | moyenne 31,91 s ; médiane 33,02 s ; p95 43,63 s (API complète) |

Les réponses V1 sont archivées dans `resultats_sans_rag.json` et ses métriques
dans `metriques_sans_rag.json`. Les réponses V2 et leurs métriques sont
archivées dans `resultats_api_ask.json` et `metriques_api_ask.json`. Le taux
0/30 signifie que le libellé exact n'est apparu dans aucune réponse sans RAG ;
il ne juge pas les paraphrases ni la pertinence générale du texte.

## Lecture des résultats

La version avec RAG relie les recommandations au catalogue de démonstration et
retourne des sources dont les champs permettent de vérifier les critères. Sur
le jeu testé, le libellé exact de la cible apparaît dans 29 réponses V2 sur 30,
contre aucune réponse de Qwen sans RAG. Pour `AGR-01`, le modèle sans RAG
propose « Citron 3 Sucrosité », alors que le libellé attendu dans le catalogue
exposé par l'API est « Citron - boissons ». Avec RAG, `/ask` renvoie la source
`AR-citron-boissons`. Pour `GOU-01`, `/ask` retourne trois alternatives
conformes aux critères annotés, mais pas la cible `AR-biscuit-biscuits`.

La comparaison ne constitue pas une mesure générale de la qualité du modèle :
le jeu est petit et synthétique, et le taux V1 repose sur la présence textuelle
exacte du libellé de catalogue. Les latences ne sont pas strictement
équivalentes, car V1 mesure l'appel au modèle seul alors que V2 mesure tout le
parcours HTTP de l'API, y compris le retrieval, les filtres et la génération.

## Synthèse

Sur les 30 demandes testées, Qwen utilisé seul, sans accès au catalogue, n'a
mentionné le libellé exact de la cible dans aucune réponse. Avec RAG, l'API a
renvoyé ce libellé dans la réponse et la source correspondante pour 29 cas sur
30 ; les 90 sources retournées respectaient les critères annotés. Ces résultats
montrent l'apport du catalogue pour ce jeu de démonstration synthétique, sans
préjuger de la qualité sur des demandes réelles ou d'autres catalogues.
