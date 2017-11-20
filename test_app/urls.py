from django.conf.urls import include, url
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.views.generic.base import View

from urlographer.views import route

admin.autodiscover()

urlpatterns = [
    url(r'^admin/', include(admin.site.urls)),
    url(r'^test_page/$', View.as_view()),
    url(r'^.*$', route),
]
urlpatterns += staticfiles_urlpatterns()
