import copy
import json
import subprocess
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from . import services, vps_runner
from .collectors import vps
from .models import CheckResult, Finding
from .outcomes import ERROR, FINDINGS, OK, UNAVAILABLE

FIXTURES = Path(__file__).parent / "fixtures"
NOW = 1_800_000_000
DAY = 86_400

HEALTHY = {
    "schema": 1,
    "collected_epoch": NOW,
    "memory": {"total_mb": 8000, "available_mb": 6000, "swap_total_mb": 2048, "swap_used_mb": 0},
    "disks": [{"mount": "/", "used_pct": 40, "avail_mb": 50000}],
    "docker": {"available": True, "containers": []},
    "listening": [
        {"proto": "tcp", "local": "0.0.0.0:443", "process": "nginx"},
        {"proto": "tcp", "local": "[::]:443", "process": "nginx"},
        {"proto": "tcp", "local": "127.0.0.1:5433", "process": "postgres"},
        {"proto": "udp", "local": "127.0.0.53%lo:53", "process": "resolved"},
    ],
    "ssh": {"password_auth": "no", "permit_root": "prohibit-password", "port": 22},
    "firewall": {"ufw": "active", "iptables_rules": 30},
    "updates": {"security_pending": 0, "reboot_required": False, "apt_index_age_h": 5},
    "certs": [{"domain": "a.example", "expires_epoch": NOW + 60 * DAY}],
    "backups": {
        "latest_epoch": NOW - 3600,
        "latest_path": "/var/backups/x.sql.gz",
        "searched": ["/var/backups"],
    },
    "nginx": {"installed": True, "config_ok": True},
    "errors": [],
}


def report(**changes):
    data = copy.deepcopy(HEALTHY)
    data.update(changes)
    return data


def by_check(outcomes):
    return {o.check_name: o for o in outcomes}


def severities(outcome):
    return {o.key: o.severity for o in outcome.observations}


class ParseTests(SimpleTestCase):
    def test_rapport_valide(self):
        self.assertEqual(vps.parse_report(json.dumps(HEALTHY))["schema"], 1)

    def test_bom_accepte(self):
        vps.parse_report("﻿" + json.dumps(HEALTHY))

    def test_refus(self):
        for bad in (
            "pas du json",
            "[]",
            json.dumps({"schema": 2, "collected_epoch": 1}),
            json.dumps({"schema": 1}),
        ):
            with self.assertRaises(vps.ReportError):
                vps.parse_report(bad)

    def test_trop_volumineux(self):
        with self.assertRaises(vps.ReportError):
            vps.parse_report(json.dumps({**HEALTHY, "pad": "x" * vps.MAX_REPORT_BYTES}))


class HealthyTests(SimpleTestCase):
    def test_serveur_sain_rien_a_signaler(self):
        outcomes = vps.evaluate(HEALTHY, "vps")
        self.assertEqual({o.status for o in outcomes}, {OK})
        self.assertEqual(len(outcomes), 10)


class PortsTests(SimpleTestCase):
    def ports(self, *entries):
        return by_check(vps.evaluate(report(listening=list(entries)), "vps"))["vps_ports"]

    def test_base_de_donnees_exposee_est_grave(self):
        out = self.ports({"proto": "tcp", "local": "0.0.0.0:5432", "process": "postgres"})
        self.assertEqual(severities(out), {"port:tcp:5432": 3})

    def test_api_docker_non_chiffree_est_critique(self):
        out = self.ports({"proto": "tcp", "local": "[::]:2375", "process": "dockerd"})
        self.assertEqual(severities(out), {"port:tcp:2375": 4})

    def test_port_inconnu_sur_toutes_les_interfaces(self):
        out = self.ports({"proto": "tcp", "local": "*:8000", "process": "uvicorn"})
        self.assertEqual(severities(out), {"port:tcp:8000": 2})

    def test_ports_en_local_ou_autorises_ignores(self):
        out = self.ports(
            {"proto": "tcp", "local": "127.0.0.1:5432", "process": "postgres"},
            {"proto": "tcp", "local": "[::1]:8000", "process": "x"},
            {"proto": "tcp", "local": "0.0.0.0:22", "process": "sshd"},
        )
        self.assertEqual(out.status, OK)

    def test_ipv4_et_ipv6_ne_font_qu_un_constat(self):
        out = self.ports(
            {"proto": "tcp", "local": "0.0.0.0:8000", "process": "x"},
            {"proto": "tcp", "local": "[::]:8000", "process": "x"},
        )
        self.assertEqual(len(out.observations), 1)

    @override_settings(VPS_AUDIT_PUBLIC_PORTS=[22, 80, 443, 8000])
    def test_ports_publics_configurables(self):
        self.assertEqual(self.ports({"proto": "tcp", "local": "0.0.0.0:8000", "process": "x"}).status, OK)

    def test_liste_illisible_est_indisponible_jamais_ok(self):
        out = by_check(vps.evaluate(report(listening=None, errors=["listening: ss absent"]), "vps"))["vps_ports"]
        self.assertEqual((out.status, out.detail), (UNAVAILABLE, "listening: ss absent"))


class SshFirewallTests(SimpleTestCase):
    def check(self, name, **changes):
        return by_check(vps.evaluate(report(**changes), "vps"))[name]

    def test_mot_de_passe_et_root(self):
        out = self.check("vps_ssh", ssh={"password_auth": "yes", "permit_root": "yes", "port": 22})
        self.assertEqual(severities(out), {"ssh:password": 3, "ssh:root": 3})

    def test_cle_seule_est_ok(self):
        self.assertEqual(self.check("vps_ssh").status, OK)

    def test_ssh_partiellement_illisible_n_est_pas_ok(self):
        out = self.check("vps_ssh", ssh={"password_auth": "unknown", "permit_root": "no", "port": 22})
        self.assertEqual(out.status, UNAVAILABLE)

    def test_pare_feu_inactif(self):
        out = self.check("vps_firewall", firewall={"ufw": "inactive", "iptables_rules": 12})
        self.assertEqual(severities(out), {"firewall:ufw": 2})

    def test_ufw_absent_est_non_couvert_pas_ok(self):
        """Un autre pare-feu (nftables, pare-feu OVH) existe peut-être : on ne peut pas conclure."""
        self.assertEqual(
            self.check("vps_firewall", firewall={"ufw": "absent", "iptables_rules": 5}).status, UNAVAILABLE
        )


class ResourcesTests(SimpleTestCase):
    def check(self, name, **changes):
        return by_check(vps.evaluate(report(**changes), "vps"))[name]

    def test_disque(self):
        for pct, expected in ((79, OK), (80, FINDINGS), (95, FINDINGS)):
            out = self.check("vps_disk", disks=[{"mount": "/", "used_pct": pct, "avail_mb": 1}])
            self.assertEqual(out.status, expected, pct)
        self.assertEqual(severities(self.check("vps_disk", disks=[{"mount": "/", "used_pct": 95}])), {"disk:/": 3})
        self.assertEqual(severities(self.check("vps_disk", disks=[{"mount": "/", "used_pct": 85}])), {"disk:/": 2})

    def test_memoire(self):
        low = {"total_mb": 1000, "available_mb": 50, "swap_total_mb": 1000, "swap_used_mb": 800}
        out = self.check("vps_memory", memory=low)
        self.assertEqual(severities(out), {"memory:low": 3, "memory:swap": 2})
        self.assertEqual(self.check("vps_memory", memory=None).status, UNAVAILABLE)

    def test_mises_a_jour(self):
        out = self.check("vps_updates", updates={"security_pending": 12, "reboot_required": True, "apt_index_age_h": 2})
        self.assertEqual(severities(out), {"updates:pending": 3, "updates:reboot": 1})

    def test_zero_mise_a_jour_sur_un_index_perime_n_est_pas_rassurant(self):
        """« 0 en attente » calculé sur un index vieux de 30 jours ne prouve rien : on le dit."""
        out = self.check(
            "vps_updates", updates={"security_pending": 0, "reboot_required": False, "apt_index_age_h": 720}
        )
        self.assertIn("updates:stale_index", severities(out))

    def test_certificats(self):
        certs = [
            {"domain": "ok.example", "expires_epoch": NOW + 60 * DAY},
            {"domain": "mois.example", "expires_epoch": NOW + 20 * DAY},
            {"domain": "semaine.example", "expires_epoch": NOW + 5 * DAY},
            {"domain": "expire.example", "expires_epoch": NOW - 3 * DAY},
        ]
        out = self.check("vps_certs", certs=certs)
        self.assertEqual(severities(out), {"cert:mois.example": 2, "cert:semaine.example": 3, "cert:expire.example": 4})

    def test_certificat_illisible_ou_dossier_inaccessible_est_non_couvert(self):
        out = self.check("vps_certs", certs=[{"domain": "x.example", "expires_epoch": None}])
        self.assertEqual(out.status, UNAVAILABLE)
        out = self.check(
            "vps_certs", certs=[], errors=["certs: /etc/letsencrypt/live/a/fullchain.pem illisible (droits)"]
        )
        self.assertEqual(out.status, UNAVAILABLE)

    def test_sauvegardes(self):
        def backups(age_s):
            return {"latest_epoch": NOW - age_s, "latest_path": "/x", "searched": ["/var/backups"]}

        self.assertEqual(self.check("vps_backups", backups=backups(3600)).status, OK)
        self.assertEqual(severities(self.check("vps_backups", backups=backups(4 * DAY))), {"backup:old": 2})
        self.assertEqual(severities(self.check("vps_backups", backups=backups(9 * DAY))), {"backup:old": 3})
        none = self.check("vps_backups", backups={"latest_epoch": None, "latest_path": None, "searched": ["/srv"]})
        self.assertEqual(severities(none), {"backup:none": 3})
        self.assertIn("/srv", none.observations[0].title)  # on dit OÙ l'on a cherché


class ContainersTests(SimpleTestCase):
    def containers(self, **fields):
        base = {
            "name": "app", "image": "app:1.2", "state": "running", "status": "Up", "user": "1000",
            "mem_limit_mb": 512, "health": "healthy", "restart": "always", "privileged": False,
            "host_network": False, "mounts_docker_sock": False,
        }  # fmt: skip
        data = report(docker={"available": True, "containers": [{**base, **fields}]})
        return by_check(vps.evaluate(data, "vps"))["vps_containers"]

    def test_conteneur_conforme(self):
        self.assertEqual(self.containers().status, OK)

    def test_privilegie_et_socket_docker(self):
        out = self.containers(privileged=True, mounts_docker_sock=True)
        self.assertEqual(severities(out), {"container:app:privileged": 3, "container:app:docker_sock": 3})

    def test_root_sans_limite_et_tag_flottant(self):
        out = self.containers(user="", mem_limit_mb=0, image="ghcr.io/x/app:latest")
        self.assertEqual(
            severities(out),
            {"container:app:root": 1, "container:app:no_mem_limit": 1, "container:app:floating_tag": 0},
        )

    def test_mauvais_etat(self):
        self.assertEqual(severities(self.containers(health="unhealthy")), {"container:app:unhealthy": 2})
        self.assertEqual(severities(self.containers(state="restarting")), {"container:app:unhealthy": 2})

    def test_conteneur_arrete_pas_de_bruit_sur_root_ou_limites(self):
        self.assertEqual(self.containers(state="exited", user="", mem_limit_mb=0).status, OK)

    def test_docker_inaccessible_est_non_couvert(self):
        data = report(docker=None, errors=["docker: démon inaccessible (droits ou arrêté)"])
        out = by_check(vps.evaluate(data, "vps"))["vps_containers"]
        self.assertEqual(out.status, UNAVAILABLE)
        self.assertIn("inaccessible", out.detail)

    def test_docker_absent_est_ok(self):
        data = report(docker={"available": False, "containers": []})
        self.assertEqual(by_check(vps.evaluate(data, "vps"))["vps_containers"].status, OK)

    def test_nginx(self):
        bad = by_check(vps.evaluate(report(nginx={"installed": True, "config_ok": False}), "vps"))["vps_nginx"]
        self.assertEqual(severities(bad), {"nginx:config": 3})
        unknown = by_check(vps.evaluate(report(nginx={"installed": True, "config_ok": None}), "vps"))["vps_nginx"]
        self.assertEqual(unknown.status, UNAVAILABLE)


class FixturesTests(SimpleTestCase):
    """Rapports RÉELS produits par vigil-audit.sh dans des conteneurs de test (voir infra/audit/)."""

    def load(self, name):
        return vps.parse_report((FIXTURES / name).read_text(encoding="utf-8"))

    def test_serveur_mal_configure(self):
        outcomes = by_check(vps.evaluate(self.load("vps_misconfigured.json"), "vps"))
        self.assertEqual(severities(outcomes["vps_ports"]), {"port:tcp:5432": 3, "port:tcp:8000": 2})
        self.assertEqual(severities(outcomes["vps_ssh"]), {"ssh:password": 3, "ssh:root": 3})
        self.assertEqual(len(outcomes["vps_certs"].observations), 2)  # 10 jours et 1 jour
        self.assertEqual(severities(outcomes["vps_backups"]), {"backup:old": 3})  # 10 jours
        self.assertNotIn("port:tcp:5433", severities(outcomes["vps_ports"]))  # 127.0.0.1 : sans risque réseau
        self.assertNotIn("port:tcp:443", severities(outcomes["vps_ports"]))  # port autorisé

    def test_serveur_minimal_les_lacunes_sont_dites(self):
        outcomes = by_check(vps.evaluate(self.load("vps_bare.json"), "vps"))
        for name in ("vps_ports", "vps_ssh", "vps_firewall"):
            self.assertEqual(outcomes[name].status, UNAVAILABLE, name)
        self.assertIn("ss absent", outcomes["vps_ports"].detail)  # la raison fournie par le script


def make_files():
    directory = Path(tempfile.mkdtemp())
    key, known = directory / "id_audit", directory / "known_hosts"
    key.write_text("clé factice")
    known.write_text("vps.example.org ssh-ed25519 AAAA")
    return key, known


class RunnerTests(SimpleTestCase):
    def conf(self, **extra):
        key, known = make_files()
        values = {
            "VPS_AUDIT_HOST": "vps.example.org", "VPS_AUDIT_USER": "vigil-audit", "VPS_AUDIT_PORT": 22,
            "VPS_AUDIT_SSH_KEY": str(key), "VPS_AUDIT_KNOWN_HOSTS": str(known),
        }  # fmt: skip
        return override_settings(**{**values, **extra})

    def test_commande_sure(self):
        with self.conf():
            command = vps_runner.build_command()
        self.assertIsInstance(command, list)  # aucun shell : une liste d'arguments
        self.assertEqual(command[0], "ssh")
        self.assertIn("StrictHostKeyChecking=yes", command)  # empreinte du serveur vérifiée
        self.assertIn("BatchMode=yes", command)  # jamais de question interactive
        self.assertIn("PasswordAuthentication=no", command)
        self.assertEqual(command[-1], "vigil-audit@vps.example.org")
        self.assertNotIn("bash", " ".join(command))  # aucune commande distante : la clé est restreinte au script

    def test_hotes_et_utilisateurs_suspects_refuses(self):
        for host in ("vps; rm -rf /", "-oProxyCommand=evil", "a b", "$(id)", "", "x\nmalicious"):
            with self.conf(VPS_AUDIT_HOST=host), self.assertRaises(vps_runner.VpsError):
                vps_runner.build_command()
        for user in ("root; id", "UPPER", "-x", "a b"):
            with self.conf(VPS_AUDIT_USER=user), self.assertRaises(vps_runner.VpsError):
                vps_runner.build_command()

    def test_port_invalide_refuse(self):
        for port in (0, 70000):
            with self.conf(VPS_AUDIT_PORT=port), self.assertRaises(vps_runner.VpsError):
                vps_runner.build_command()

    def test_sans_known_hosts_refus_net_pas_de_confiance_aveugle(self):
        with (
            self.conf(VPS_AUDIT_KNOWN_HOSTS="/inexistant/known_hosts"),
            self.assertRaises(vps_runner.VpsError) as ctx,
        ):
            vps_runner.build_command()
        self.assertIn("empreinte", str(ctx.exception))

    def test_cle_introuvable(self):
        with self.conf(VPS_AUDIT_SSH_KEY="/inexistant/cle"), self.assertRaises(vps_runner.VpsError):
            vps_runner.build_command()

    def test_configured(self):
        with self.conf():
            self.assertTrue(vps_runner.configured())
        with self.conf(VPS_AUDIT_HOST=""):
            self.assertFalse(vps_runner.configured())

    def run_with(self, completed=None, exc=None):
        def fake_run(command, **kwargs):
            self.assertIs(kwargs.get("shell", False), False)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)  # rien n'est envoyé au serveur
            if exc:
                raise exc
            return completed

        with self.conf():
            return vps_runner.fetch_report(run=fake_run)

    def test_succes(self):
        done = subprocess.CompletedProcess([], 0, stdout='{"schema":1}', stderr="")
        self.assertEqual(self.run_with(done), '{"schema":1}')

    def test_echec_ssh(self):
        done = subprocess.CompletedProcess([], 255, stdout="", stderr="Host key verification failed.\n")
        with self.assertRaises(vps_runner.VpsError) as ctx:
            self.run_with(done)
        self.assertIn("255", str(ctx.exception))
        self.assertIn("Host key verification failed", str(ctx.exception))

    def test_delai_depasse_et_ssh_absent(self):
        with self.assertRaises(vps_runner.VpsError):
            self.run_with(exc=subprocess.TimeoutExpired("ssh", 120))
        with self.assertRaises(vps_runner.VpsError):
            self.run_with(exc=FileNotFoundError())


@override_settings(
    VPS_AUDIT_HOST="vps.example.org", VPS_AUDIT_TARGET="OVH", VPS_AUDIT_SSH_KEY="", VPS_AUDIT_KNOWN_HOSTS=""
)
class ServiceTests(TestCase):
    def audit(self, data):
        return services.run_vps_audit(report=data)

    def test_non_configure_ne_fait_rien(self):
        self.assertEqual(services.run_vps_audit()["status"], "not_configured")
        self.assertEqual(Finding.objects.count() + CheckResult.objects.count(), 0)

    def test_constats_enregistres_sous_le_nom_de_la_cible(self):
        result = self.audit(report(ssh={"password_auth": "yes", "permit_root": "no", "port": 22}))
        self.assertEqual(result["status"], "ok")
        finding = Finding.objects.get()
        self.assertEqual((finding.source, finding.target, finding.check_name), ("vps", "OVH", "vps_ssh"))

    def test_constat_corrige_est_resolu(self):
        self.audit(report(ssh={"password_auth": "yes", "permit_root": "no", "port": 22}))
        self.audit(HEALTHY)
        self.assertEqual(Finding.objects.get().status, Finding.Status.RESOLVED)

    def test_section_illisible_ne_resout_rien(self):
        """Si on ne peut plus lire SSH, le constat « mot de passe actif » n'est PAS considéré comme réglé."""
        self.audit(report(ssh={"password_auth": "yes", "permit_root": "no", "port": 22}))
        self.audit(report(ssh=None, errors=["ssh: « sshd -T » indisponible"]))
        self.assertEqual(Finding.objects.get().status, Finding.Status.OPEN)
        self.assertEqual(CheckResult.objects.get(check_name="vps_ssh").status, UNAVAILABLE)

    def test_serveur_injoignable_est_un_controle_en_erreur_et_ne_resout_rien(self):
        self.audit(report(ssh={"password_auth": "yes", "permit_root": "no", "port": 22}))
        boom = mock.Mock(side_effect=vps_runner.VpsError("délai dépassé (120 s)"))
        with override_settings(VPS_AUDIT_SSH_KEY="/k", VPS_AUDIT_KNOWN_HOSTS="/h"):
            result = services.run_vps_audit(fetch=boom)
        self.assertEqual(result["status"], "error")
        self.assertEqual(CheckResult.objects.get(check_name="vps_connection").status, ERROR)
        self.assertEqual(Finding.objects.get().status, Finding.Status.OPEN)

    def test_rapport_invalide_est_une_erreur_pas_un_succes(self):
        with override_settings(VPS_AUDIT_SSH_KEY="/k", VPS_AUDIT_KNOWN_HOSTS="/h"):
            result = services.run_vps_audit(fetch=lambda: "<html>pas du json</html>")
        self.assertEqual(result["status"], "error")
        self.assertEqual(CheckResult.objects.get(check_name="vps_connection").status, ERROR)


class TaskAndCommandTests(TestCase):
    def test_tache_enregistree(self):
        from procrastinate.contrib.django import app

        app.perform_import_paths()
        self.assertIn("security.audit_vps", app.tasks)
        crons = {name: p.cron for (name, _), p in app.periodic_registry.periodic_tasks.items()}
        self.assertEqual(crons["security.audit_vps"], "10 5 * * *")

    def test_la_tache_echoue_si_le_vps_est_injoignable(self):
        from . import tasks

        failure = {"status": "error", "error": "délai dépassé"}
        with (
            mock.patch("modules.security.tasks.services.run_vps_audit", return_value=failure),
            self.assertRaises(RuntimeError),
        ):
            tasks.audit_vps(timestamp=0)

    def test_la_tache_ne_fait_rien_si_non_configure(self):
        from . import tasks

        with (
            mock.patch("modules.security.tasks.services.run_vps_audit", return_value={"status": "not_configured"}),
            mock.patch("modules.security.tasks.notifications.notify_new_findings", return_value={"status": "nothing"}),
        ):
            self.assertEqual(tasks.audit_vps(timestamp=0)["audit"]["status"], "not_configured")

    def test_commande_dry_run_n_ecrit_rien_et_s_affiche_sur_console_windows(self):
        out = StringIO()
        call_command("audit_vps", "--dry-run", "--from-file", str(FIXTURES / "vps_misconfigured.json"), stdout=out)
        text = out.getvalue()
        out.getvalue().encode("cp1252", errors="strict")
        self.assertIn("PostgreSQL", text)
        self.assertEqual(Finding.objects.count() + CheckResult.objects.count(), 0)

    def test_dry_run_exige_un_fichier_donc_jamais_de_connexion_ssh(self):
        with self.assertRaises(CommandError):
            call_command("audit_vps", "--dry-run")
