
from celery.task import Task

from django.contrib.admin.models import (
    CHANGE,
    LogEntry
)
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from urlographer.models import URLMap


class FixRedirectLoopsTask(Task):
    """
    Task to automatically fix redirect loops.

    Example scenario:

    Before:
    A
    B
    C -> A
    D -> C -> A

    After:
    A
    B
    C -> A
    D -> A
    """

    user_username = 'fix_redirect_loops_task'

    def get_or_create_task_user(self):
        try:
            return User.objects.get(username=self.user_username)
        except User.DoesNotExist:
            return User.objects.create_user(
                self.user_username, 'dev@consumeraffairs.com')

    def get_urlmaps_2_hops(self):
        qs_filters = {
            'redirect__status_code__range': (300, 399),
            'redirect__redirect__status_code__in': (200, 410)}
        return URLMap.objects.filter(**qs_filters)

    def run(self):
        for urlmap in self.get_urlmaps_2_hops():
            content_type_id = ContentType.objects.get_for_model(urlmap).pk
            with transaction.atomic():
                urlmap.redirect = urlmap.redirect.redirect
                urlmap.on_sitemap = False
                urlmap.save()
                change_message = (
                    'Updated to redirect directly to "{0}" by '
                    'FixRedirectLoopsTask'.format(urlmap.redirect.path)
                )
                LogEntry.objects.log_action(
                    user_id=self.get_or_create_task_user().id,
                    content_type_id=content_type_id,
                    object_id=urlmap.id,
                    object_repr=str(urlmap),
                    action_flag=CHANGE,
                    change_message=change_message
                )
