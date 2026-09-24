# Configuration locale et confidentialité

## Configurer les expériences

Les programmes Arduino utilisent désormais un fichier local `arduino_secrets.h`.
Dans le dossier du sketch choisi, copier `arduino_secrets.example.h` vers
`arduino_secrets.h`, puis renseigner le SSID et le mot de passe du réseau.
Installer les bibliothèques du sketch comme auparavant. Le fichier local est
exclu de Git ; ne pas utiliser `git add -f` pour l'ajouter.

Les cinq anciens scripts à cible BLE fixe lisent désormais la variable
d'environnement `TEMPO_TARGET_MAC`. La valeur de repli
`02:00:00:00:00:01` est une adresse d'exemple. Configurer localement la cible
avant de lancer ces scripts. La V10 multimode n'est pas modifiée.

## Prévenir une nouvelle publication

- Travailler dans un dossier dédié au projet, jamais à la racine du dossier personnel.
- Ne pas versionner profils de navigateur, clés privées, jetons, historiques de commandes,
  archives de sauvegarde ou captures réseau.
- Conserver les nouvelles acquisitions localement. Avant de partager des résultats,
  vérifier les identifiants d'appareils dans les CSV, captures et légendes des graphiques.
- Vérifier `git status` et `git diff --cached --stat` avant chaque commit.
  Un fichier ignoré peut toujours être ajouté de force : le .gitignore n'est pas un scanner.
- L'exemple de simulation `acquisition_20260724_154056` est conservé pour la présentation.

## Nettoyage et limites

Ce nettoyage retire les profils personnels/système, les archives et captures brutes
sélectionnées de l'état courant des branches. Il externalise les identifiants Wi-Fi
des sketches et les adresses BLE fixes des scripts concernés.

**Un commit de suppression ne retire pas les anciennes versions de l'historique.**
Les identifiants précédemment publiés doivent être considérés comme exposés,
même si le dépôt est ensuite rendu privé ou si les fichiers sont supprimés.

Actions à effectuer avec les accès du propriétaire :

1. Révoquer le jeton GitHub exposé, puis recréer uniquement les accès nécessaires.
2. Remplacer la clé SSH exposée et retirer son ancienne clé publique des services
   et machines où elle autorise un accès.
3. Changer les mots de passe Wi-Fi publiés dans les sketches.
4. Invalider les sessions du navigateur concerné : son profil et ses cookies
   figuraient dans le dépôt.
5. Après rotation, préparer la purge de toutes les branches et références affectées
   avec `git-filter-repo`, examiner les conséquences pour les clones et pull requests,
   puis coordonner la réécriture. Les clones et copies externes ne sont pas effacés
   par une réécriture du dépôt.
6. Si nécessaire, contacter GitHub Support pour les vues en cache et références de
   pull requests contenant des données sensibles.

Ce changement ne révoque aucun identifiant et ne réécrit pas l'historique.
Le contrôle effectué porte sur les fichiers texte du projet conservés ; il ne
constitue pas une certification d'absence de secret dans tout l'historique,
les dépendances tierces ou les images.

Documentation officielle :
[Retirer des données sensibles](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
