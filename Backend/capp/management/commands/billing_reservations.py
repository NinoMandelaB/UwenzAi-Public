"""
Inspect and release stuck Spark reservations.

A reservation that is still ``reserved`` long after it was created means the
operation it paid for never reported back — a worker died, a provider call hung,
a deploy landed mid-request. The Sparks are debited and the user has nothing to
show for them.

Releasing those holds is deliberately manual. Auto-refunding anything that looks
old would let a caller get paid work for free by stalling past the threshold, and
would race with an operation that is merely slow. So this command lists, and
refunds one explicitly named reservation at a time.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from capp.classes.billing.reservation_service import (
    BillingReservationService,
    ReservationError,
)
from capp.models import BillingReservation

DEFAULT_STALE_MINUTES = 60


class Command(BaseCommand):
    help = "List stale Spark reservations, and optionally refund one by ID."

    def add_arguments(self, parser):
        parser.add_argument(
            "--list-stale",
            action="store_true",
            help="List reservations still held after --older-than-minutes.",
        )
        parser.add_argument(
            "--older-than-minutes",
            type=int,
            default=DEFAULT_STALE_MINUTES,
            help=f"Age threshold for --list-stale (default: {DEFAULT_STALE_MINUTES}).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum rows to list (default: 50).",
        )
        parser.add_argument(
            "--refund",
            metavar="RESERVATION_ID",
            help="Refund a single reservation. Requires --confirm to write.",
        )
        parser.add_argument(
            "--reason",
            default="stale_reservation_released_by_operator",
            help="Refund reason recorded on the reservation.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually perform the refund. Without it the command is a dry run.",
        )

    def handle(self, *args, **options):
        if options["refund"]:
            self._refund(options)
        elif options["list_stale"]:
            self._list_stale(options)
        else:
            raise CommandError("Nothing to do: pass --list-stale or --refund <RESERVATION_ID>.")

    # ------------------------------------------------------------------
    def _list_stale(self, options):
        minutes = options["older_than_minutes"]
        cutoff = timezone.now() - timedelta(minutes=minutes)

        stale = (
            BillingReservation.objects.filter(
                status=BillingReservation.Status.RESERVED,
                reserved_at__lt=cutoff,
            )
            .select_related("user", "guest_identity")
            .order_by("reserved_at")
        )

        total = stale.count()
        rows = list(stale[: options["limit"]])

        if not rows:
            self.stdout.write(f"No reservations held longer than {minutes} minute(s).")
            return

        self.stdout.write(f"{total} reservation(s) held longer than {minutes} minute(s); showing {len(rows)}:")
        self.stdout.write("")
        for reservation in rows:
            age = timezone.now() - reservation.reserved_at
            self.stdout.write(
                f"  {reservation.reservation_id}  "
                f"{reservation.feature:<22}"
                f"{reservation.amount_sparks:>7} sparks  "
                f"age={self._format_age(age):<12}"
                f"{reservation.principal_label}"
            )
        self.stdout.write("")
        self.stdout.write("Refund one with: manage.py billing_reservations --refund <ID> --confirm")

    # ------------------------------------------------------------------
    def _refund(self, options):
        reservation_id = options["refund"]

        reservation = (
            BillingReservation.objects.select_related("user", "guest_identity", "claimed_by")
            .filter(pk=reservation_id)
            .first()
        )
        if reservation is None:
            raise CommandError(f"No reservation with ID {reservation_id}")

        self.stdout.write(f"Reservation:  {reservation.reservation_id}")
        self.stdout.write(f"Principal:    {reservation.principal_label}")
        self.stdout.write(f"Feature:      {reservation.feature}")
        self.stdout.write(f"Amount:       {reservation.amount_sparks} sparks")
        self.stdout.write(f"Status:       {reservation.status}")
        self.stdout.write(f"Reserved at:  {reservation.reserved_at.isoformat()}")

        if reservation.status != BillingReservation.Status.RESERVED:
            raise CommandError(
                f"Reservation is {reservation.status}, not reserved. "
                "Settled reservations are final and cannot be reversed here."
            )

        if not options["confirm"]:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(f"DRY RUN — would refund {reservation.amount_sparks} sparks. Re-run with --confirm.")
            )
            return

        try:
            refunded = BillingReservationService.refund(reservation, reason=options["reason"])
        except ReservationError as exc:
            raise CommandError(f"Refund failed: {exc}") from exc

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Refunded {refunded.amount_sparks} sparks to {refunded.principal_label}.")
        )

    @staticmethod
    def _format_age(delta) -> str:
        total_minutes = int(delta.total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"
