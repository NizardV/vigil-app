# ADR 0006 — Thème de l'admin : django-unfold

- Statut : acceptée (2026-11-10)
- Complète : [ADR 0004](0004-frontend-progressif-admin-puis-sveltekit-spa.md), point 1 (l'admin sert d'interface)

## Contexte

L'admin Django est l'interface de Vigil tant qu'aucun écran riche ne justifie le front SvelteKit (ADR 0004).
Son apparence par défaut rend la lecture difficile : aucune hiérarchie visuelle entre un constat critique et un
constat faible, pas de vue d'ensemble, navigation par application et non par usage.

## Décision

- Thème **django-unfold** (dépendance Python unique, CSS précompilé, aucun outillage front à maintenir).
- Identité Vigil : logo et favicon (`core/static/vigil/`), palette cyan / bleu nuit tirée du logo
  (`config/admin_theme.py`).
- Barre latérale organisée par usage : Sécurité, Veille, Projets, Système.
- **Tableau de bord** de la page d'accueil (`config/admin_dashboard.py` + `config/templates/admin/index.html`) :
  indicateurs et graphiques de sécurité, alimentés uniquement par les services publics des modules.
- **Pastilles de couleur** dans les listes (gravité, statut, couverture, verdict) via `@display(label=...)`.
- Tous les `ModelAdmin` héritent de `unfold.admin.ModelAdmin` ; comptes et groupes réenregistrés avec ses formulaires
  (`core/admin.py`).

## Conséquences

- (+) Lecture immédiate des priorités, sans écrire de front.
- (−) Une dépendance de plus, liée aux versions de Django : vérifier sa compatibilité avant chaque montée de Django
  (0.110 supporte Django 5.2 à 6.1). Épinglée en `<0.111` : une montée de version se relit.
- (−) Seules les classes Tailwind déjà présentes dans le CSS d'unfold sont utilisables dans les gabarits.
- Le formulaire de connexion d'unfold ne transmet pas `next` : `LOGIN_REDIRECT_URL = "admin:index"` (testé).
- Réversible : retirer `unfold` d'`INSTALLED_APPS` et revenir à `admin.ModelAdmin` rend l'admin d'origine.

## Alternatives écartées

- **Personnalisation sans dépendance** (variables CSS, `base_site.html`, `format_html`) : pas de tableau de bord ni
  de barre latérale sans réécrire des gabarits entiers.
- **django-jazzmin** : moins actif, basé sur Bootstrap/AdminLTE, rendu daté.
- **Avancer le front SvelteKit** : disproportionné pour un besoin de lisibilité (ADR 0004).
