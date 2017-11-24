# coding: utf-8
from django import forms
from django.contrib import admin
from django.contrib.sites.models import Site

from urlographer.models import URLMap, ContentMap


SQL_COUNT_REDIRECTS = """
SELECT count(map2.id)
FROM urlographer_urlmap map2
WHERE map2.status_code=301
AND map2.redirect_id=urlographer_urlmap.id"""


class HasRedirectsToItListFilter(admin.SimpleListFilter):
    title = 'has redirects to it'
    parameter_name = 'has_redirects_to_it'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Yes'),
            ('no', 'No'),
        )

    def queryset(self, request, queryset):
        join_sql = SQL_COUNT_REDIRECTS
        value = self.value()
        if value == 'yes':
            return queryset.extra(where=["(%s)>0" % join_sql])
        if value == 'no':
            return queryset.extra(where=["(%s)=0" % join_sql])


class SiteModelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return "{0} - {1}".format(obj.name, obj.domain)


class URLMapAdminForm(forms.ModelForm):

    site = SiteModelChoiceField(queryset=Site.objects.all())

    def save(self, commit=True):
        """Override to update equivalent AMP URLMap when necessary."""
        urlmap = super(URLMapAdminForm, self).save(commit)  # saved urlmap
        if urlmap.status_code in (301, 302, 410):
            amp_urlmap = urlmap.get_amp_equivalent()
            if amp_urlmap and amp_urlmap.status_code == 200:
                amp_urlmap.update_as_main_urlmap(self.current_user, urlmap)
        return urlmap

    class Meta:
        model = URLMap
        fields = '__all__'


class URLMapAdmin(admin.ModelAdmin):
    form = URLMapAdminForm

    def get_queryset(self, request):
        try:
            from django.db.models.expressions import RawSQL
            return super(URLMapAdmin, self).get_queryset(
                request
            ).annotate(redirects_count=RawSQL(SQL_COUNT_REDIRECTS, ()))
        except ImportError:
            # Django version <= 1.7
            return super(URLMapAdmin, self).queryset(request).extra(select={
                'redirects_count': SQL_COUNT_REDIRECTS
            })

    def get_form(self, request, obj=None, **kwargs):
        form = super(URLMapAdmin, self).get_form(request, obj, **kwargs)
        form.current_user = request.user
        return form

    def redirects_count(self, obj):
        return obj.redirects_count

    list_display = (
        'id',
        'path',
        'content_map',
        'status_code',
        'on_sitemap',
        'created',
        'redirects_count')
    list_filter = (
        'status_code',
        'on_sitemap',
        ('created', admin.DateFieldListFilter),
        HasRedirectsToItListFilter)
    raw_id_fields = ('canonical', 'redirect', 'content_map')
    readonly_fields = ('hexdigest',)
    search_fields = ('path',)


admin.site.register(URLMap, URLMapAdmin)
admin.site.register(ContentMap)
