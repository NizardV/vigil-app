from django.core.management.base import BaseCommand, CommandError

from modules.watch import seed


class Command(BaseCommand):
    help = "Installe les thèmes et sources de départ (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--channel",
            action="append",
            default=[],
            metavar="SLUG=ID",
            help="identifiant du salon Discord d'un thème, ex. --channel cybersecurite=123 (répétable)",
        )

    def handle(self, *args, **options):
        channels = {}
        for item in options["channel"]:
            slug, sep, channel_id = item.partition("=")
            if not sep or not channel_id.isdigit():
                raise CommandError(f"--channel attend SLUG=ID (identifiant numérique) : {item!r}")
            channels[slug] = channel_id
        result = seed.seed(channels)
        self.stdout.write(f"Créés : {result['themes']} thème(s), {result['sources']} source(s).")
