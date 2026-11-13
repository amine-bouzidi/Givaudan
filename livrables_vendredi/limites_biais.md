# Limites et biais des résultats issus de l'API

## Données observées

L'évaluation archive la réponse de `GET /aromes` et les réponses complètes de
`POST /ask`. Les critères de démonstration sont issus, selon l'équipe, d'un
catalogue public de Givaudan ; un script a ensuite généré des enregistrements
synthétiques. Les champs commerciaux générés sont fictifs et ne décrivent pas
des offres vérifiées.

Les sources de `/ask` portent un indicateur `is_synthetic` et des champs
structurés (identifiant, famille, notes, application, sucrosité, tenue à la
chaleur, prix et délai). Les mesures V2 sont calculées à partir de ces réponses
API. Pour V1, le modèle reçoit seulement la demande et l'historique ; le nom de
cible servant à l'évaluation est ensuite récupéré dans la réponse de
`GET /aromes` et n'est pas communiqué au modèle.

## Limites du test API

- Les 30 requêtes sont des cas de référence synthétiques, répartis en cinq
  familles, et non un échantillon de demandes réelles représentatif.
- La cible attendue apparaît dans les sources de `/ask` pour 29 cas sur 30.
  Pour `GOU-01`, l'API retourne trois alternatives conformes aux critères
  annotés, mais pas la cible `AR-biscuit-biscuits`.
- Dans la configuration sans RAG, aucun des 30 textes ne contient le libellé
  exact de la cible. Cette vérification lexicale ne reconnaît pas les
  paraphrases et ne signifie pas que chaque réponse libre est dépourvue de
  pertinence.
- Pour `AGR-01`, Qwen seul a répondu « Citron 3 Sucrosité », qui ne correspond
  pas au libellé catalogue « Citron - boissons ». Cet exemple illustre
  l'absence de rattachement à une entrée du catalogue sans RAG.
- Chaque appel reçoit un historique prérempli qui indique qu'il n'y a pas de
  contrainte de chaleur ni de certification. Le test mesure donc l'étape de
  recommandation, pas tout le dialogue interactif de qualification.
- La présence de la cible annotée dans 29 réponses sur 30 mesure la sortie
  finale API sur ce jeu ; elle ne constitue pas un taux de réussite généralisable
  à d'autres requêtes ou catalogues.
- La conformité des sources est vérifiée par rapport aux critères annotés des
  cas. Elle ne valide pas l'exactitude commerciale des valeurs synthétiques.
- L'API utilise Qwen pour formuler les réponses et choisir des recommandations.
  Les résultats peuvent varier selon la version du modèle et la configuration
  locale ; les réponses brutes sont donc archivées.
- La latence observée de 31,91 s en moyenne (p95 43,63 s) correspond aux appels
  HTTP complets à `/ask` sur une seule configuration locale ; elle est dominée
  par la génération Qwen et ne garantit pas les performances en production.
- La latence sans RAG mesure un appel au modèle seul ; la latence avec RAG
  mesure le parcours HTTP complet. Les deux chiffres ont des périmètres
  différents et ne doivent pas être interprétés comme une comparaison directe
  du coût ajouté par le RAG.

## V1 / V2

Dans ce dossier, V1 correspond à Qwen sans catalogue récupéré et V2 à l'API
`/ask` avec RAG. Le taux V1 porte sur les appels directs au modèle, pas sur une
API historique ; la mesure V2 porte sur les réponses de l'API.

Le taux exact 0/30 en V1 et 29/30 en V2 compare la présence du nom de la cible
attendue dans le texte de la réponse. Pour V2, l'identifiant correspondant
figure aussi parmi les sources dans 29/30 cas. Ce résultat est indicatif du
rattachement au catalogue sur ce jeu ; il ne prouve pas une amélioration
globale de la qualité. Les latences ont également des périmètres différents :
modèle seul pour V1, parcours HTTP complet pour V2.
