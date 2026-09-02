from django.core.management.base import BaseCommand, CommandError

from documentview import active


class Command(BaseCommand):
    help = (
        'Report documentview active-link manifest/filesystem disagreements. '
        'With --repair, recreate or drop app-owned manifest entries whose '
        'symlink or source is broken. Foreign symlinks (present but not in '
        'the manifest) are always reported only, never adopted or removed.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--repair', action='store_true',
            help='Explicitly repair app-owned mismatches instead of only reporting them.',
        )

    def handle(self, *args, **options):
        try:
            issues = active.reconcile(repair=options['repair'])
        except active.ManifestError as e:
            raise CommandError(str(e)) from e
        if not issues:
            self.stdout.write(self.style.SUCCESS('No manifest/filesystem disagreements found.'))
            return
        for issue in issues:
            marker = 'REPAIRED' if issue.repaired else issue.kind.upper()
            self.stdout.write(f'[{marker}] {issue.link_name}: {issue.detail}')
