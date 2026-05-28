from datetime import datetime, timedelta

from django.db import models
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Item(models.Model):
    REPEAT_DAYS = 'days'
    REPEAT_WEEKS = 'weeks'
    REPEAT_MONTHS = 'months'
    REPEAT_YEARS = 'years'
    REPEAT_UNIT_CHOICES = [
        (REPEAT_DAYS, 'days'),
        (REPEAT_WEEKS, 'weeks'),
        (REPEAT_MONTHS, 'months'),
        (REPEAT_YEARS, 'years'),
    ]

    LOW = 1
    MILD = 2
    IMPORTANT = 3
    SEVERE = 4
    PRIORITY_CHOICES = [
        (SEVERE, 'Severe'),
        (IMPORTANT, 'Important'),
        (MILD, 'Mild'),
        (LOW, 'Low'),
    ]

    title = models.CharField(max_length=200)
    due_date = models.DateField()
    due_time = models.TimeField(default='00:00:00')
    warning_days = models.PositiveSmallIntegerField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(
        choices=PRIORITY_CHOICES,
        default=IMPORTANT,
    )
    starred = models.BooleanField(default=False)
    finished = models.BooleanField(default=False)
    note = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)
    repeat = models.BooleanField(default=False)
    repeat_interval = models.PositiveIntegerField(default=1)
    repeat_unit = models.CharField(max_length=10, choices=REPEAT_UNIT_CHOICES, default=REPEAT_DAYS)
    repeat_from_due_date = models.BooleanField(default=True)

    class Meta:
        ordering = ['finished', 'due_date', '-priority', 'title']

    def due_datetime(self):
        due_dt = datetime.combine(self.due_date, self.due_time)
        return timezone.make_aware(due_dt, timezone.get_current_timezone())

    @property
    def warning_active(self):
        if not self.warning_days:
            return False
        warn_dt = self.due_datetime() - timedelta(days=self.warning_days)
        return not self.finished and timezone.now() >= warn_dt

    @property
    def overdue(self):
        return not self.finished and timezone.now() >= self.due_datetime()

    def __str__(self):
        return self.title
