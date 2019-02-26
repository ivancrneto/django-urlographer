
from django.dispatch import Signal

urlmap_bound_to_request = Signal(providing_args=['request'])
