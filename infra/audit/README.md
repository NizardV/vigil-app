# Audit du VPS — installation (à faire une seule fois, par toi)

Vigil audite ton VPS **en lecture seule** : il se connecte en SSH avec une clé dédiée, qui ne sait faire qu'une chose,
lancer le script `vigil-audit.sh` et récupérer son rapport JSON. **Cette clé ne permet pas d'ouvrir un shell.**

## Pourquoi cette conception

| Risque | Parade |
|---|---|
| La clé fuite | Elle est restreinte côté serveur à *un seul programme* (commande forcée) : pas de terminal, pas de redirection de ports, pas de transfert de fichiers. |
| Le script est détourné | Il est installé en `root:root` `0755` : l'utilisateur d'audit ne peut pas le modifier. Vigil ne l'envoie jamais : ce qui tourne avec des droits élevés est fixe et relu. |
| Un faux serveur se fait passer pour le VPS | L'empreinte du serveur est vérifiée contre un `known_hosts` que tu fournis. Sans lui, Vigil refuse de se connecter. |
| Le script modifie le serveur | Il ne fait que lire (`/proc`, fichiers de configuration, commandes d'inspection). Testé dans un conteneur **en lecture seule, sans réseau, sans aucune capacité**. |

**Relis `vigil-audit.sh` avant de l'installer** : c'est lui qui tournera en root. Il fait environ 200 lignes.

## Ce que le script lit

Mémoire et disques, conteneurs Docker (nom, image, état, utilisateur, limites, mode privilégié, socket Docker monté),
ports en écoute, configuration SSH effective (`sshd -T`), état d'ufw, mises à jour de sécurité en attente (simulation
`apt-get -s`), dates d'expiration des certificats Let's Encrypt, date de la dernière sauvegarde trouvée, validité de la
configuration NGINX. **Il n'ouvre aucun fichier de données applicatives, ne lit aucun mot de passe ni aucune clé privée.**

## Étapes

### 1. Sur ta machine (celle qui fait tourner Vigil) : créer une clé dédiée

Dans Git Bash (pas dans PowerShell, qui gère mal une phrase de passe vide) :

```
ssh-keygen -t ed25519 -f ~/.ssh/vigil_audit -C "vigil-audit" -N ""
```

C'est une clé **à elle seule** : ne réutilise jamais ta clé personnelle ni celle du déploiement.
Garde la clé **privée** (`vigil_audit`) sur cette machine ; tu n'enverras que la clé **publique** (`vigil_audit.pub`).

### 2. Sur le VPS (avec ton compte administrateur habituel)

```
# a) Un utilisateur sans mot de passe, qui ne sert qu'à l'audit
sudo adduser --disabled-password --gecos "" vigil-audit

# b) Installer le script, propriétaire root, non modifiable par vigil-audit
scp infra/audit/vigil-audit.sh TON_COMPTE@TON_VPS:/tmp/vigil-audit.sh          # depuis ta machine
sudo install -o root -g root -m 0755 /tmp/vigil-audit.sh /usr/local/bin/vigil-audit
rm /tmp/vigil-audit.sh
ls -l /usr/local/bin/vigil-audit      # DOIT afficher : -rwxr-xr-x 1 root root

# c) Autoriser vigil-audit à lancer CE SEUL programme en root (et rien d'autre)
echo 'vigil-audit ALL=(root) NOPASSWD: /usr/local/bin/vigil-audit' | sudo tee /etc/sudoers.d/vigil-audit
sudo chmod 440 /etc/sudoers.d/vigil-audit
sudo visudo -c -f /etc/sudoers.d/vigil-audit      # DOIT afficher « parsed OK »

# d) Enregistrer la clé publique, VERROUILLÉE sur le script
sudo -u vigil-audit mkdir -p -m 700 /home/vigil-audit/.ssh
sudo -u vigil-audit tee /home/vigil-audit/.ssh/authorized_keys >/dev/null <<'EOF'
restrict,command="sudo -n /usr/local/bin/vigil-audit" ssh-ed25519 COLLE_ICI_LE_CONTENU_DE_vigil_audit.pub vigil-audit
EOF
sudo chmod 600 /home/vigil-audit/.ssh/authorized_keys
```

Remplace `COLLE_ICI_…` par la partie « clé » du fichier `vigil_audit.pub` (la longue chaîne `AAAA…`).
`restrict` désactive le terminal, les redirections de ports, le transfert d'agent et les scripts de connexion.

### 3. Vérifier l'empreinte du serveur (à faire par un canal sûr)

Sur le VPS : `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` — note l'empreinte `SHA256:…`.
Sur ta machine :

```
ssh-keyscan -t ed25519 TON_VPS > ~/.ssh/vigil_known_hosts
ssh-keygen -lf ~/.ssh/vigil_known_hosts      # l'empreinte DOIT être identique à celle du serveur
```

Si elles diffèrent, **arrête-toi** : quelqu'un s'interpose, ou tu vises le mauvais serveur.

### 4. Vérifier que la clé est bien verrouillée

Depuis ta machine :

```
ssh -i ~/.ssh/vigil_audit -o IdentitiesOnly=yes -o UserKnownHostsFile=$HOME/.ssh/vigil_known_hosts vigil-audit@TON_VPS
```

→ doit afficher **une ligne de JSON** (`{"schema":1,…`) puis se terminer.

```
ssh -i ~/.ssh/vigil_audit -o IdentitiesOnly=yes -o UserKnownHostsFile=$HOME/.ssh/vigil_known_hosts vigil-audit@TON_VPS "cat /etc/shadow"
```

→ doit afficher **le même JSON d'audit**, et **surtout pas** le contenu de `/etc/shadow`. Si tu vois autre chose que le
JSON, la restriction n'est pas en place : supprime la ligne de `authorized_keys` et reprends l'étape 2d.

### 5. Brancher Vigil

Dans `infra/dev/webhooks.env` (ignoré par git) ou l'environnement de production :

```
VPS_AUDIT_HOST=ton-vps.exemple.fr
VPS_AUDIT_USER=vigil-audit
VPS_AUDIT_SSH_KEY=C:\Users\Nizard\.ssh\vigil_audit
VPS_AUDIT_KNOWN_HOSTS=C:\Users\Nizard\.ssh\vigil_known_hosts
VPS_AUDIT_TARGET=OVH
```

Puis un premier audit réel : `.\infra\dev\manage.ps1 audit_vps`.
Pour évaluer un rapport sans connexion ni écriture : `audit_vps --dry-run --from-file rapport.json`.

## Ce qui sera signalé

| Contrôle | Signalé quand |
|---|---|
| Certificats | expirés (critique), < 14 jours (haute), < 30 jours (moyenne) |
| Ports | base de données ou API Docker ouverte sur toutes les interfaces (haute / critique) ; tout port hors `VPS_AUDIT_PUBLIC_PORTS` (défaut 22, 80, 443) ouvert sur toutes les interfaces (moyenne) |
| SSH | mot de passe autorisé ou connexion root directe (haute) |
| Sauvegardes | aucune trouvée ou vieille de plus de 7 jours (haute), de plus de 2 jours (moyenne) |
| Disque, mémoire | disque ≥ 80 % (moyenne) / ≥ 90 % (haute) ; mémoire disponible < 20 % / < 10 % ; swap > 50 % |
| Mises à jour | mises à jour de sécurité en attente ; index des paquets périmé (le décompte n'est alors pas fiable) |
| Conteneurs | privilégié ou socket Docker monté (haute) ; en mauvais état, réseau de l'hôte (moyenne) ; root, sans limite mémoire, tag `latest` (faible / info) |
| Pare-feu, NGINX | ufw inactif (moyenne) ; configuration NGINX invalide (haute) |

**Ce qui n'a pas pu être lu est affiché comme « non couvert », jamais comme « sans problème »** (ex. `ufw` absent :
un autre pare-feu, ou celui d'OVH, n'est pas évalué).

Limites à connaître : la détection de sauvegardes ne voit que les dossiers du serveur
(`/var/backups`, `/srv/backups`, `/opt/backups`, `/home/*/backups`) ; une sauvegarde envoyée ailleurs n'est pas vue.
Les mises à jour en attente reposent sur l'index local des paquets (`apt update`).

## Révoquer l'accès

Supprime la ligne dans `/home/vigil-audit/.ssh/authorized_keys`, ou l'utilisateur entier :
`sudo deluser --remove-home vigil-audit && sudo rm /etc/sudoers.d/vigil-audit`.

## Mettre à jour le script

Relis les changements (`git diff infra/audit/vigil-audit.sh`), puis refais l'étape 2b. Vigil refuse un rapport dont la
version de schéma est inattendue.
