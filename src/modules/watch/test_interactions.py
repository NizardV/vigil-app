import json
import time

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from nacl.signing import SigningKey

from .models import Analysis, Article, Feedback, Source, Theme

KEY = SigningKey.generate()  # vraie paire Ed25519, comme Discord
PUBLIC = KEY.verify_key.encode().hex()


def click(custom_id, user_id="42", kind=3):
    return json.dumps({"type": kind, "data": {"custom_id": custom_id}, "member": {"user": {"id": user_id}}}).encode()


@override_settings(DISCORD_PUBLIC_KEY=PUBLIC)
class InteractionTests(TestCase):
    def setUp(self):
        theme = Theme.objects.create(name="IA", slug="ia")
        source = Source.objects.create(theme=theme, url="https://x.example/f.xml")
        self.article = Article.objects.create(source=source, title="A", url="https://x.example/1", processed=True)
        Analysis.objects.create(article=self.article, summary="s", relevance_score=8)
        self.url = reverse("watch_interactions")

    def post(self, body, *, timestamp=None, signature=None, key=KEY, client=None, **extra):
        ts = str(int(timestamp if timestamp is not None else time.time()))
        sig = key.sign(ts.encode() + body).signature.hex() if signature is None else signature
        return (client or self.client).post(
            self.url, data=body, content_type="application/json",
            HTTP_X_SIGNATURE_ED25519=sig, HTTP_X_SIGNATURE_TIMESTAMP=ts, **extra,
        )  # fmt: skip

    def like(self, rating=1, user_id="42"):
        return self.post(click(f"watch:fb:{self.article.id}:{rating}", user_id))

    # --- Authentification : le seul point d'entrée public du projet --------------------------------------
    def test_ping_de_verification_discord(self):
        response = self.post(json.dumps({"type": 1}).encode())
        self.assertEqual((response.status_code, response.json()), (200, {"type": 1}))

    def test_clic_valide_enregistre_un_retour(self):
        response = self.like(1)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["flags"], 64)  # éphémère : visible seulement par la personne qui a cliqué
        self.assertEqual(data["allowed_mentions"], {"parse": []})
        feedback = Feedback.objects.get()
        self.assertEqual((feedback.rating, feedback.author, feedback.origin), (1, "discord:42", "discord"))

    def test_signature_invalide_refusee_et_rien_enregistre(self):
        for kwargs in (
            {"signature": "00" * 64},
            {"signature": "pas-de-l-hexa"},
            {"key": SigningKey.generate()},  # signé par quelqu'un d'autre
            {"timestamp": time.time() - 3600},  # requête rejouée
        ):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.post(click(f"watch:fb:{self.article.id}:1"), **kwargs).status_code, 401)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_corps_modifie_apres_signature_refuse(self):
        body = click(f"watch:fb:{self.article.id}:1")
        ts = str(int(time.time()))
        signature = KEY.sign(ts.encode() + body).signature.hex()
        forged = click(f"watch:fb:{self.article.id}:-1")  # le pirate inverse la note
        response = self.client.post(
            self.url, data=forged, content_type="application/json",
            HTTP_X_SIGNATURE_ED25519=signature, HTTP_X_SIGNATURE_TIMESTAMP=ts,
        )  # fmt: skip
        self.assertEqual(response.status_code, 401)

    def test_sans_en_tetes_de_signature(self):
        response = self.client.post(self.url, data=click("watch:fb:1:1"), content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Feedback.objects.count(), 0)

    @override_settings(DISCORD_PUBLIC_KEY="")
    def test_cle_publique_non_configuree_refuse_tout(self):
        """Sans clé pour vérifier, on n'accepte RIEN (surtout pas un clic « signé » par n'importe qui)."""
        self.assertEqual(self.post(click(f"watch:fb:{self.article.id}:1")).status_code, 503)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_methode_get_refusee(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_corps_trop_gros_refuse_avant_lecture(self):
        response = self.post(b"x" * 30_000)
        self.assertEqual(response.status_code, 413)

    def test_csrf_ne_bloque_pas_discord_mais_la_signature_reste_obligatoire(self):
        strict = Client(enforce_csrf_checks=True)  # comme en production
        self.assertEqual(self.post(click(f"watch:fb:{self.article.id}:1"), client=strict).status_code, 200)
        unsigned = strict.post(self.url, data=click("watch:fb:1:1"), content_type="application/json")
        self.assertEqual(unsigned.status_code, 401)

    # --- Contenu des clics --------------------------------------------------------------------------------
    def test_un_seul_retour_par_personne_le_dernier_remplace(self):
        self.like(1)
        self.like(-1)  # la même personne change d'avis
        self.assertEqual(Feedback.objects.count(), 1)
        self.assertEqual(Feedback.objects.get().rating, -1)
        self.like(1, user_id="43")  # une autre personne : un second retour
        self.assertEqual(Feedback.objects.count(), 2)

    def test_identifiants_malformes_ou_hostiles_refuses(self):
        for custom_id in (
            "watch:fb:1;DROP TABLE:1", "watch:fb:abc:1", "watch:fb:1:5", "watch:fb:-1:1", "feedback_like_1",
            "watch:fb:1:1 ", "watch:fb::1", "x" * 500, "", "watch:fb:99999999999999:1",
        ):  # fmt: skip
            response = self.post(click(custom_id))
            self.assertEqual(response.status_code, 200, custom_id)
            self.assertIn("inconnue", response.json()["data"]["content"], custom_id)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_article_inexistant(self):
        response = self.post(click("watch:fb:987654:1"))
        self.assertIn("n'existe plus", response.json()["data"]["content"])
        self.assertEqual(Feedback.objects.count(), 0)

    def test_type_d_interaction_inconnu(self):
        response = self.post(json.dumps({"type": 2, "data": {}}).encode())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["flags"], 64)

    def test_json_invalide_avec_signature_valide(self):
        self.assertEqual(self.post(b"{pas du json").status_code, 400)
        self.assertEqual(self.post(b"[1, 2, 3]").status_code, 400)

    def test_clic_sans_membre_ni_utilisateur(self):
        body = json.dumps({"type": 3, "data": {"custom_id": f"watch:fb:{self.article.id}:1"}}).encode()
        self.assertEqual(self.post(body).status_code, 200)
        self.assertEqual(Feedback.objects.get().author, "discord:inconnu")
