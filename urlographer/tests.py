# Copyright 2013 Consumers Unified LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import mox
import six

from collections import OrderedDict

from model_mommy import mommy, recipe

from django.conf import settings
from django.contrib.admin.models import (
    CHANGE,
    LogEntry
)
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import Http404, HttpRequest
from django.test.client import RequestFactory
from django.test.utils import override_settings


from django.test import TestCase

from urlographer import (
    admin,
    models,
    sample_views,
    tasks,
    utils,
    views)

try:
    # Django => 1.9
    from django.contrib.sites.shortcuts import get_current_site
except ImportError:
    from django.contrib.sites.models import get_current_site


class ContentMapTest(TestCase):
    def test_save_existing_view(self):
        content_map = models.ContentMap(view='urlographer.views.route')
        self.assertEqual(content_map.clean(), None)

    def test_save_nonexistent_view(self):
        content_map = models.ContentMap(view='urlographer.views.nonexistent')
        self.assertRaisesMessage(
            ValidationError, 'Please enter a valid view.', content_map.clean)

    def test_save(self):
        content_map = models.ContentMap.objects.create(
            view='urlographer.views.route')
        models.URLMap.objects.create(
            site=Site.objects.get(id=1), path='/test_path',
            content_map=content_map)
        # infinite recursion FTW
        mock = mox.Mox()
        mock.StubOutWithMock(models.ContentMap, 'full_clean')
        mock.StubOutWithMock(models.URLMap, 'cache_key')
        mock.StubOutWithMock(models.cache, 'set')
        models.ContentMap.full_clean()
        models.URLMap.cache_key().AndReturn('urlmap_key')
        models.cache.set('urlmap_key', None, 5)

        mock.ReplayAll()
        content_map.save()
        mock.VerifyAll()
        mock.UnsetStubs()
        self.assertEqual(content_map.id, 1)

    def test_unicode(self):
        content_map = models.ContentMap(
            view='urlographer.views.route', options={'article_id': 3})
        self.assertEqual(
            six.text_type(content_map),
            "urlographer.views.route(**{'article_id': 3})")


class URLMapTest(TestCase):
    def setUp(self):
        self.site = Site.objects.get(id=1)
        self.url = models.URLMap(site=self.site, path='/test_path',
                                 force_secure=False)
        self.hexdigest = 'a6dd1406d4e5aadaafed9c2d285d36bd'
        self.cache_key = settings.URLOGRAPHER_CACHE_PREFIX + self.hexdigest
        self.mock = mox.Mox()
        self.mock.StubOutWithMock(models.URLMapManager, 'cached_get')

    def tearDown(self):
        self.mock.UnsetStubs()

    def test_protocol(self):
        self.assertEqual(self.url.protocol(), 'http')

    def test_https_protocol(self):
        self.url.force_secure = True
        self.assertEqual(self.url.protocol(), 'https')

    def test_unicode(self):
        self.assertEqual(
            six.text_type(self.url), u'http://example.com/test_path')

    def test_get_absolute_url(self):
        self.assertEqual(self.url.get_absolute_url(), '/test_path')

    def test_get_absolute_url_other_site(self):
        self.url.site = Site(domain='other.com')
        self.assertEqual(self.url.get_absolute_url(),
                         'http://other.com/test_path')

    def test_https_unicode(self):
        self.url.force_secure = True
        self.assertEqual(
            six.text_type(self.url), u'https://example.com/test_path')

    def test_set_hexdigest(self):
        self.assertFalse(self.url.hexdigest)
        self.url.set_hexdigest()
        self.assertEqual(
            self.url.hexdigest, self.hexdigest)

    def test_cache_key(self):
        self.url.hexdigest = 'a6dd1406d4e5aadaafed9c2d285d36bd'
        self.assertEqual(self.url.cache_key(), self.cache_key)

    def test_cache_key_with_hexdigest_unset(self):
        with self.assertRaises(ValueError) as context:
            self.url.cache_key()
        self.assertEqual(
            context.exception.args[0], 'URLMap has unset hexdigest')

    def test_save(self):
        self.site.save()
        self.url.site = self.site
        self.url.status_code = 204
        self.assertFalse(self.url.id)
        self.assertFalse(self.url.hexdigest)
        self.mock.StubOutWithMock(models.cache, 'set')
        models.cache.set(
            self.cache_key, self.url,
            timeout=settings.URLOGRAPHER_CACHE_TIMEOUT)
        self.mock.ReplayAll()
        self.url.save()
        self.mock.VerifyAll()
        self.assertEqual(self.url.hexdigest, self.hexdigest)
        self.assertEqual(self.url.id, 1)

    def test_save_validates(self):
        self.url.status_code = 200
        self.assertRaisesMessage(
            ValidationError, 'Status code requires a content map',
            self.url.save)

    def test_save_perm_redirect_wo_redirect_raises(self):
        self.site.save()
        self.url.site = self.site
        self.url.status_code = 301
        self.assertRaisesMessage(
            ValidationError, 'Status code requires a redirect', self.url.save)

    def test_save_temp_redirect_wo_redirect_raises(self):
        self.site.save()
        self.url.site = self.site
        self.url.status_code = 302
        self.assertRaisesMessage(
            ValidationError, 'Status code requires a redirect', self.url.save)

    def test_save_redirect_to_self_raises(self):
        self.site.save()
        self.url.site = self.site
        self.url.status_code = 301
        self.url.redirect = self.url
        self.assertRaisesMessage(
            ValidationError, 'You cannot redirect a url to itself',
            self.url.save)

    def test_save_200_wo_content_map_raises(self):
        self.site.save()
        self.url.site = self.site
        self.url.status_code = 200
        self.assertRaisesMessage(
            ValidationError, 'Status code requires a content map',
            self.url.save)

    def test_save_data_invalid_for_field_definition_raises(self):
        self.url.path = 'x' * 2001
        self.url.status_code = 404
        m = 'Ensure this value has at most 2000 characters (it has 2001).'
        self.assertRaisesMessage(ValidationError, m, self.url.save)

    def test_delete_deletes_cache(self):
        self.site.save()
        self.url.site = self.site
        self.url.status_code = 204
        self.url.save()
        self.mock.StubOutWithMock(models.cache, 'delete')
        models.cache.delete(self.url.cache_key())
        self.mock.ReplayAll()
        self.url.delete()
        self.mock.VerifyAll()
        self.assertFalse(self.url.id)

    def test_unique_hexdigest(self):
        self.site.save()
        self.url.site = self.site
        self.url.status_code = 204
        self.url.save()
        self.url.id = None
        self.assertRaisesMessage(
            ValidationError,
            u'Url map with this Hexdigest already exists.',
            self.url.save)


class URLMapManagerTest(TestCase):
    def setUp(self):
        self.site = Site.objects.get(id=1)
        self.url = models.URLMap(site=self.site, path='/test_path',
                                 force_secure=False)
        self.hexdigest = 'a6dd1406d4e5aadaafed9c2d285d36bd'
        self.cache_key = settings.URLOGRAPHER_CACHE_PREFIX + self.hexdigest
        self.mock = mox.Mox()

    def tearDown(self):
        self.mock.UnsetStubs()

    def test_cached_get_cache_hit(self):
        self.mock.StubOutWithMock(models.cache, 'get')
        models.cache.get(self.cache_key).AndReturn(self.url)
        self.mock.ReplayAll()
        url = models.URLMap.objects.cached_get(self.site, self.url.path)
        self.mock.VerifyAll()
        self.assertEqual(url, self.url)

    def test_cached_get_cache_miss(self):
        self.site.save()
        self.url.site = self.site
        self.url.status_code = 204
        self.url.save()
        self.mock.StubOutWithMock(models.cache, 'get')
        self.mock.StubOutWithMock(models.cache, 'set')
        models.cache.get(self.cache_key)
        models.cache.set(
            self.cache_key, self.url,
            timeout=settings.URLOGRAPHER_CACHE_TIMEOUT)
        self.mock.ReplayAll()
        url = models.URLMap.objects.cached_get(self.site, self.url.path)
        self.mock.VerifyAll()
        self.assertEqual(url, self.url)

    def test_cached_get_does_not_exist(self):
        self.mock.StubOutWithMock(models.cache, 'get')
        models.cache.get(self.cache_key)
        self.mock.ReplayAll()
        self.assertRaises(
            models.URLMap.DoesNotExist, models.URLMap.objects.cached_get,
            self.site, self.url.path)
        self.mock.VerifyAll()

    def test_cached_get_force_cache_invalidation(self):
        self.site.save()
        self.url.site = self.site
        self.url.status_code = 204
        self.url.save()
        self.mock.StubOutWithMock(models.cache, 'get')
        self.mock.StubOutWithMock(models.cache, 'set')
        models.cache.set(
            self.cache_key, self.url,
            timeout=settings.URLOGRAPHER_CACHE_TIMEOUT)
        self.mock.ReplayAll()
        url = models.URLMap.objects.cached_get(
            self.site, self.url.path, force_cache_invalidation=True)
        self.mock.VerifyAll()
        self.assertEqual(url, self.url)


class RouteTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = Site.objects.get()
        self.mock = mox.Mox()

    def tearDown(self):
        self.mock.UnsetStubs()

    def test_route_not_found(self):
        request = self.factory.get('/404/', follow=True)
        self.assertEqual(request.path, '/404/')
        self.assertRaises(Http404, views.route, request)

    def test_route_gone(self):
        models.URLMap.objects.create(
            site=self.site, status_code=410, path='/410', force_secure=False)
        request = self.factory.get('/410')
        response = views.route(request)
        self.assertEqual(response.status_code, 410)

    def test_route_set_not_found(self):
        models.URLMap.objects.create(
            site=self.site, status_code=404, path='/404/', force_secure=False)
        request = self.factory.get('/404/')
        self.assertRaises(Http404, views.route, request)

    def test_route_redirect_canonical(self):
        content_map = models.ContentMap(
            view='django.views.generic.base.TemplateView')
        content_map.options['initkwargs'] = {
            'template_name': 'admin/base.html'}
        content_map.save()
        models.URLMap.objects.create(site=self.site, path='/test',
                                     content_map=content_map,
                                     force_secure=False)
        response = views.route(self.factory.get('/TEST'))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response._headers['location'][1],
                         'http://example.com/test')

    def test_permanent_redirect(self):
        target = models.URLMap.objects.create(
            site=self.site, path='/target', status_code=204,
            force_secure=False)
        models.URLMap.objects.create(
            site=self.site, path='/source', redirect=target, status_code=301,
            force_secure=False)
        response = views.route(self.factory.get('/source'))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response._headers['location'][1],
                         'http://example.com/target')

    def test_redirect(self):
        target = models.URLMap.objects.create(
            site=self.site, path='/target', status_code=204,
            force_secure=False)
        models.URLMap.objects.create(
            site=self.site, path='/source', redirect=target, status_code=302,
            force_secure=False)
        response = views.route(self.factory.get('/source'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response._headers['location'][1],
                         'http://example.com/target')

    def test_content_map_class_based_view(self):
        content_map = models.ContentMap(
            view='urlographer.sample_views.SampleClassView')
        content_map.options['initkwargs'] = {
            'test_val': 'testing 1 2 3'}
        content_map.save()
        models.URLMap.objects.create(
            site=self.site, path='/test', content_map=content_map,
            force_secure=False)
        response = views.route(self.factory.get('/test'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content,
            six.text_type('test value=testing 1 2 3').encode('utf-8'))

    def test_content_map_view_function(self):
        content_map = models.ContentMap(
            view='urlographer.sample_views.sample_view')
        content_map.options['test_val'] = 'testing 1 2 3'
        content_map.save()
        urlmap = models.URLMap.objects.create(
            site=self.site, path='/test', content_map=content_map,
            force_secure=False)
        request = self.factory.get('/test')
        response = views.route(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content,
            six.text_type('test value=testing 1 2 3').encode('utf-8'))
        self.assertEqual(request.urlmap, urlmap)

    def test_force_secure_wo_request_secure(self):
        content_map = models.ContentMap(
            view='urlographer.sample_views.sample_view')
        content_map.options['test_val'] = 'testing 1 2 3'
        content_map.save()
        urlmap = models.URLMap.objects.create(
            site=self.site, path='/test', content_map=content_map,
            force_secure=True)

        request = self.factory.get('/test')
        self.mock.StubOutWithMock(request, 'is_secure')
        self.mock.StubOutWithMock(views, 'get_redirect_url_with_query_string')
        # Calls
        request.is_secure().AndReturn(False)
        views.get_redirect_url_with_query_string(
            request,
            six.text_type(urlmap)).AndReturn(
                six.text_type(urlmap) + '?ok=true')

        self.mock.ReplayAll()
        response = views.route(request)
        self.mock.VerifyAll()

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'], six.text_type(urlmap) + '?ok=true')
        self.assertEqual(request.urlmap, urlmap)

    def test_force_secure_w_request_secure(self):
        content_map = models.ContentMap(
            view='urlographer.sample_views.sample_view')
        content_map.options['test_val'] = 'testing 1 2 3'
        content_map.save()
        urlmap = models.URLMap.objects.create(
            site=self.site, path='/test', content_map=content_map,
            force_secure=True)

        request = self.factory.get('/test')
        self.mock.StubOutWithMock(request, 'is_secure')
        self.mock.StubOutWithMock(views, 'get_redirect_url_with_query_string')
        # Calls
        request.is_secure().AndReturn(True)

        self.mock.ReplayAll()
        response = views.route(request)
        self.mock.VerifyAll()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content,
            six.text_type('test value=testing 1 2 3').encode('utf-8'))
        self.assertEqual(request.urlmap, urlmap)

    def test_not_force_secure_w_request_secure(self):
        content_map = models.ContentMap(
            view='urlographer.sample_views.sample_view')
        content_map.options['test_val'] = 'testing 1 2 3'
        content_map.save()
        urlmap = models.URLMap.objects.create(
            site=self.site, path='/test', content_map=content_map,
            force_secure=False)

        request = self.factory.get('/test')
        self.mock.StubOutWithMock(request, 'is_secure')
        self.mock.StubOutWithMock(views, 'get_redirect_url_with_query_string')

        self.mock.ReplayAll()
        response = views.route(request)
        self.mock.VerifyAll()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content,
            six.text_type('test value=testing 1 2 3').encode('utf-8'))
        self.assertEqual(request.urlmap, urlmap)

    def test_not_force_secure_wo_request_secure(self):
        content_map = models.ContentMap(
            view='urlographer.sample_views.sample_view')
        content_map.options['test_val'] = 'testing 1 2 3'
        content_map.save()
        urlmap = models.URLMap.objects.create(
            site=self.site, path='/test', content_map=content_map,
            force_secure=False)

        request = self.factory.get('/test')
        self.mock.StubOutWithMock(request, 'is_secure')
        self.mock.StubOutWithMock(views, 'get_redirect_url_with_query_string')

        self.mock.ReplayAll()
        response = views.route(request)
        self.mock.VerifyAll()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content,
            six.text_type('test value=testing 1 2 3').encode('utf-8'))
        self.assertEqual(request.urlmap, urlmap)

    def test_force_cache_invalidation(self):
        path = '/test'
        request = self.factory.get(path)
        site = get_current_site(request)
        url_map = models.URLMap(site=site, path=path, status_code=204,
                                force_secure=False)
        self.mock.StubOutWithMock(views, 'force_cache_invalidation')
        self.mock.StubOutWithMock(models.URLMapManager, 'cached_get')
        views.force_cache_invalidation(request).AndReturn(True)
        models.URLMapManager.cached_get(
            site, path, force_cache_invalidation=True).AndReturn(
                url_map)
        self.mock.ReplayAll()
        response = views.route(request)
        self.assertEqual(response.status_code, 204)

    def test_append_slash_redirect(self):
        response = self.client.get('/test_page')
        self.assertRedirects(response, '/test_page/', status_code=301,
                             target_status_code=405)

    @override_settings(APPEND_SLASH=False)
    def test_append_slash_off_no_redirect(self):
        response = self.client.get('/test_page')
        self.assertEqual(response.status_code, 404)

    def test_append_slash_w_slash_no_match(self):
        response = self.client.get('/fake_page')
        self.assertRedirects(response, '/fake_page/', status_code=301,
                             fetch_redirect_response=False)

    @override_settings(
        URLOGRAPHER_HANDLERS={
            403: 'urlographer.sample_views.sample_handler'})
    def test_handler_as_string(self):
        models.URLMap.objects.create(
            site=self.site, path='/page', status_code=403, force_secure=False)
        response = views.route(self.factory.get('/page'))
        self.assertContains(response, 'modified content', status_code=403)

    @override_settings(
        URLOGRAPHER_HANDLERS={
            206: sample_views.sample_handler})
    def test_handler_as_func(self):
        models.URLMap.objects.create(
            site=self.site, path='/page', status_code=206, force_secure=False)
        response = views.route(self.factory.get('/page'))
        self.assertContains(response, 'modified content', status_code=206)

    @override_settings(
        URLOGRAPHER_HANDLERS={
            402: sample_views.SampleClassHandler})
    def test_handler_as_class(self):
        models.URLMap.objects.create(
            site=self.site, path='/page', status_code=402, force_secure=False)
        response = views.route(self.factory.get('/page'))
        self.assertContains(response, 'payment required', status_code=402)

    @override_settings(
        URLOGRAPHER_HANDLERS={
            404: {'test': 'this'}})
    def test_handler_as_dict_fails(self):
        models.URLMap.objects.create(
            site=self.site, path='/page/', status_code=404, force_secure=False)
        self.assertRaisesMessage(
            ImproperlyConfigured,
            'URLOGRAPHER_HANDLERS values must be views or import strings',
            views.route, self.factory.get('/page/'))

    # Newrelic Tests
    @override_settings(
        URLOGRAPHER_HANDLERS={
            402: sample_views.SampleClassHandler})
    def test_handler_as_class_newrelic(self):
        self.mock.StubOutWithMock(views, 'newrelic')
        models.URLMap.objects.create(
            site=self.site, path='/page', status_code=402, force_secure=False)
        views.newrelic.agent = self.mock.CreateMockAnything()
        views.newrelic.agent.set_transaction_name(
            'urlographer.sample_views:SampleClassHandler.get',
            'Python/urlographer')
        self.mock.ReplayAll()
        response = views.route(self.factory.get('/page'))
        self.assertContains(response, 'payment required', status_code=402)

    @override_settings(
        URLOGRAPHER_HANDLERS={
            206: sample_views.sample_handler})
    def test_handler_as_func_newrelic(self):
        self.mock.StubOutWithMock(views, 'newrelic')
        models.URLMap.objects.create(
            site=self.site, path='/page', status_code=206, force_secure=False)
        views.newrelic.agent = self.mock.CreateMockAnything()
        views.newrelic.agent.set_transaction_name(
            'urlographer.sample_views:sample_handler.get',
            'Python/urlographer')
        self.mock.ReplayAll()
        response = views.route(self.factory.get('/page'))
        self.assertContains(response, 'modified content', status_code=206)

    def test_content_map_class_based_view_newrelic(self):
        self.mock.StubOutWithMock(views, 'newrelic')
        content_map = models.ContentMap(
            view='urlographer.sample_views.SampleClassView')
        content_map.options['initkwargs'] = {
            'test_val': 'testing 1 2 3'}
        content_map.save()
        models.URLMap.objects.create(
            site=self.site, path='/test', content_map=content_map,
            force_secure=False)
        views.newrelic.agent = self.mock.CreateMockAnything()
        views.newrelic.agent.set_transaction_name(
            'urlographer.sample_views:SampleClassView.get',
            'Python/urlographer')
        self.mock.ReplayAll()
        response = views.route(self.factory.get('/test'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content,
            six.text_type('test value=testing 1 2 3').encode('utf-8'))

    def test_content_map_view_function_newrelic(self):
        self.mock.StubOutWithMock(views, 'newrelic')
        content_map = models.ContentMap(
            view='urlographer.sample_views.sample_view')
        content_map.options['test_val'] = 'testing 1 2 3'
        content_map.save()
        urlmap = models.URLMap.objects.create(
            site=self.site, path='/test', content_map=content_map,
            force_secure=False)
        request = self.factory.get('/test')
        views.newrelic.agent = self.mock.CreateMockAnything()
        views.newrelic.agent.set_transaction_name(
            'urlographer.sample_views:sample_view.get',
            'Python/urlographer')
        self.mock.ReplayAll()
        response = views.route(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content,
            six.text_type('test value=testing 1 2 3').encode('utf-8'))
        self.assertEqual(request.urlmap, urlmap)


class GetRedirectUrlWithQueryStringTest(TestCase):

    def setUp(self):
        self.factory = RequestFactory()

    def test_missing_query_string(self):
        request = self.factory.get('')
        del request.META['QUERY_STRING']

        url = 'http://example.com/test'
        new_url = utils.get_redirect_url_with_query_string(request, url)
        self.assertEqual(new_url, url)

    def test_w_query_string(self):
        data_dict = OrderedDict(sorted(
            {'string': 'true', 'show': 'off'}.items(), reverse=True))
        request = self.factory.get('', data=data_dict)

        url = 'http://example.com/test'
        new_url = utils.get_redirect_url_with_query_string(request, url)
        self.assertEqual(new_url, '{}?{}'.format(url, 'string=true&show=off'))

    def test_wo_query_string(self):
        request = self.factory.get('')

        url = 'http://example.com/test'
        new_url = utils.get_redirect_url_with_query_string(request, url)
        self.assertEqual(new_url, url)


class CanonicalizePathTest(TestCase):
    def test_lower(self):
        self.assertEqual(utils.canonicalize_path('/TEST'), '/test')

    def test_slashes(self):
        self.assertEqual(utils.canonicalize_path('//t//e///s/t'),
                         '/t/e/s/t')

    def test_dots(self):
        self.assertEqual(
            utils.canonicalize_path('./../this/./is/./only/../a/./test.html'),
            '/this/is/a/test.html')
        self.assertEqual(
            utils.canonicalize_path('../this/./is/./only/../a/./test.html'),

            '/this/is/a/test.html')

    def test_non_ascii(self):
        self.assertEqual(utils.canonicalize_path(u'/te\xa0\u2013st'), '/test')


class ForceCacheInvalidationTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_header_not_set(self):
        self.assertFalse(utils.force_cache_invalidation(self.factory.get('/')))

    def test_header_set(self):
        request = self.factory.get('/')
        request.META.update({'HTTP_CACHE_CONTROL': 'no-cache'})
        self.assertTrue(utils.force_cache_invalidation(request))


class CustomSitemapTest(TestCase):

    def setUp(self):
        self.sitemap = views.CustomSitemap({'queryset': []})
        self.mock = mox.Mox()

    def tearDown(self):
        self.mock.UnsetStubs()

    def test_attrs(self):
        self.assertIsInstance(self.sitemap, views.GenericSitemap)

    def test_get_urls(self):
        site = Site(domain='example.com')
        urlmap1 = models.URLMap(path='/some/path', site=site)
        urlmap2 = models.URLMap(path='/some/path/2', site=site)

        urls = [{
            'item': urlmap1,
            'location': 'some loc',
            'lastmod': None,
            'changefreq': None,
            'priority': None,
        }, {
            'item': urlmap2,
            'location': 'some loc2',
            'lastmod': None,
            'changefreq': None,
            'priority': None,
        }]

        self.mock.StubOutWithMock(views.GenericSitemap, 'get_urls')
        views.GenericSitemap.get_urls().AndReturn(urls)

        self.mock.ReplayAll()
        urls = self.sitemap.get_urls()
        self.mock.VerifyAll()

        self.assertSequenceEqual(
            [six.text_type(urlmap1), six.text_type(urlmap2)],
            [u['location'] for u in urls])


class SitemapTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.mock = mox.Mox()
        self.mock.StubOutWithMock(views, 'force_cache_invalidation')
        self.mock.StubOutWithMock(views.URLMap.objects, 'filter')
        self.mock.StubOutWithMock(views, 'contrib_sitemap')
        self.mock.StubOutWithMock(views, 'CustomSitemap')
        self.mock.StubOutWithMock(views.cache, 'get')
        self.mock.StubOutWithMock(views.cache, 'set')
        self.site = Site.objects.get_current()
        self.cache_key = '{}{}_sitemap'.format(
            settings.URLOGRAPHER_CACHE_PREFIX, self.site)
        self.request = self.factory.get('/sitemap.xml')
        self.mock_contrib_sitemap_response = self.mock.CreateMockAnything()

        if six.PY2:
            self.mock_contrib_sitemap_response.content = '<mock>Sitemap</mock>'
        elif six.PY3:
            self.mock_contrib_sitemap_response.content = (
                b'<mock>Sitemap</mock>')

    def tearDown(self):
        self.mock.UnsetStubs()

    def test_get_cache_miss(self):
        qs = self.mock.CreateMockAnything()

        views.force_cache_invalidation(self.request)
        views.cache.get(self.cache_key)
        views.URLMap.objects.filter(
            site=self.site, status_code=200, on_sitemap=True).AndReturn(qs)
        qs.select_related('site').AndReturn('mock queryset')

        views.CustomSitemap({'queryset': 'mock queryset'}).AndReturn(
            'mock CustomSitemap')
        self.mock_contrib_sitemap_response.render()
        views.contrib_sitemap(
            self.request, {'urlmap': 'mock CustomSitemap'}).AndReturn(
                self.mock_contrib_sitemap_response)
        views.cache.set(
            self.cache_key, self.mock_contrib_sitemap_response.content,
            settings.URLOGRAPHER_CACHE_TIMEOUT)
        self.mock.ReplayAll()
        response = views.sitemap(self.request)
        self.mock.VerifyAll()
        self.assertEqual(
            response.content, self.mock_contrib_sitemap_response.content)

    def test_get_cache_hit(self):
        views.force_cache_invalidation(self.request)
        views.cache.get(self.cache_key).AndReturn(
            self.mock_contrib_sitemap_response.content)
        self.mock.ReplayAll()
        response = views.sitemap(self.request)
        self.mock.VerifyAll()
        self.assertEqual(
            response.content, self.mock_contrib_sitemap_response.content)

    def test_get_force_cache_invalidation(self):
        qs = self.mock.CreateMockAnything()

        views.force_cache_invalidation(self.request).AndReturn(True)
        views.URLMap.objects.filter(
            site=self.site, status_code=200, on_sitemap=True).AndReturn(qs)
        qs.select_related('site').AndReturn('mock queryset')

        views.CustomSitemap({'queryset': 'mock queryset'}).AndReturn(
            'mock CustomSitemap')
        views.contrib_sitemap(
            self.request, {'urlmap': 'mock CustomSitemap'}).AndReturn(
                self.mock_contrib_sitemap_response)
        self.mock_contrib_sitemap_response.render()
        views.cache.set(
            self.cache_key, self.mock_contrib_sitemap_response.content,
            settings.URLOGRAPHER_CACHE_TIMEOUT)
        self.mock.ReplayAll()
        response = views.sitemap(self.request)
        self.mock.VerifyAll()
        self.assertEqual(
            response.content, self.mock_contrib_sitemap_response.content)

    def test_get_invalidate_cache(self):
        qs = self.mock.CreateMockAnything()

        views.URLMap.objects.filter(
            site=self.site, status_code=200, on_sitemap=True).AndReturn(qs)
        qs.select_related('site').AndReturn('mock queryset')

        views.CustomSitemap({'queryset': 'mock queryset'}).AndReturn(
            'mock CustomSitemap')
        views.contrib_sitemap(
            self.request, {'urlmap': 'mock CustomSitemap'}).AndReturn(
                self.mock_contrib_sitemap_response)
        self.mock_contrib_sitemap_response.render()
        views.cache.set(
            self.cache_key, self.mock_contrib_sitemap_response.content,
            settings.URLOGRAPHER_CACHE_TIMEOUT)
        self.mock.ReplayAll()
        response = views.sitemap(self.request, invalidate_cache=True)
        self.mock.VerifyAll()
        self.assertEqual(
            response.content, self.mock_contrib_sitemap_response.content)


class UpdateSitemapCacheTaskTest(TestCase):
    def setUp(self):
        self.mock = mox.Mox()

    def tearDown(self):
        self.mock.UnsetStubs()

    def test_update_sitemap_cache(self):
        self.mock.StubOutWithMock(tasks, 'sitemap')
        tasks.sitemap(mox.IsA(HttpRequest), invalidate_cache=True)
        self.mock.ReplayAll()
        tasks.UpdateSitemapCacheTask().run()
        self.mock.VerifyAll()


class FixRedirectLoopsTaskTest(TestCase):

    def setUp(self):
        """Set up these URLs:

        A: HTTP200
        B: HTTP410
        C -> A
        D -> C -> A
        E -> B
        F -> D -> C -> A
        G -> E -> B
        """
        site = mommy.make('sites.Site', domain='www.ca.com')
        urlmap_recipe = recipe.Recipe(
            'urlographer.URLMap', site=site)

        self.urlA = urlmap_recipe.make(
            path='/a/', status_code=200,
            content_map__view='django.views.generic.base.View')
        self.urlB = urlmap_recipe.make(
            path='/b/', status_code=410)
        self.urlC = urlmap_recipe.make(
            path='/c/', redirect=self.urlA, status_code=301)
        self.urlD = urlmap_recipe.make(
            path='/d/', redirect=self.urlC, status_code=301)
        self.urlE = urlmap_recipe.make(
            path='/e/', redirect=self.urlB, status_code=301)
        self.urlF = urlmap_recipe.make(
            path='/f/', redirect=self.urlD, status_code=302)
        self.urlG = urlmap_recipe.make(
            path='/g/', redirect=self.urlE, status_code=302)

        self.task = tasks.FixRedirectLoopsTask()
        self.mock = mox.Mox()

    def tearDown(self):
        self.mock.UnsetStubs()

    def test_get_or_create_task_user_user_does_not_exist(self):
        self.assertEqual(
            User.objects.filter(username=self.task.user_username).count(), 0)

        user = self.task.get_or_create_task_user()
        self.assertEqual(user.username, self.task.user_username)

        self.assertEqual(
            User.objects.filter(username=self.task.user_username).count(), 1)

    def test_get_or_create_task_user_user_exists(self):
        mommy.make('auth.User', username=self.task.user_username)
        self.assertEqual(
            User.objects.filter(username=self.task.user_username).count(), 1)

        user = self.task.get_or_create_task_user()
        self.assertEqual(user.username, self.task.user_username)

        self.assertEqual(
            User.objects.filter(username=self.task.user_username).count(), 1)

    def test_get_urlmaps_2_hops(self):
        result = self.task.get_urlmaps_2_hops()
        self.assertQuerysetEqual(
            result,
            [repr(self.urlD), repr(self.urlG)],
            ordered=False
        )

    def test_run(self):
        task_user = mommy.make('auth.User', username=self.task.user_username)

        self.assertEqual(self.urlD.redirect, self.urlC)
        self.mock.StubOutWithMock(self.task, 'get_urlmaps_2_hops')

        # Expected calls:
        self.task.get_urlmaps_2_hops().AndReturn(
            [self.urlD, self.urlG])

        self.mock.ReplayAll()
        self.task.run()
        self.mock.VerifyAll()

        updated_url_d = models.URLMap.objects.get(pk=self.urlD.pk)
        self.assertEqual(updated_url_d.redirect, self.urlA)
        self.assertFalse(updated_url_d.on_sitemap)
        updated_url_g = models.URLMap.objects.get(pk=self.urlG.pk)
        self.assertEqual(updated_url_g.redirect, self.urlB)
        self.assertFalse(updated_url_g.on_sitemap)

        # assert LogEntry entries have been created correctly
        content_type_id = ContentType.objects.get_for_model(self.urlD).pk
        url_d_logentry = LogEntry.objects.get(object_id=self.urlD.pk)
        url_g_logentry = LogEntry.objects.get(object_id=self.urlG.pk)

        self.assertEqual(url_d_logentry.user, task_user)
        self.assertEqual(url_d_logentry.content_type_id, content_type_id)
        self.assertEqual(url_d_logentry.action_flag, CHANGE)
        self.assertEqual(
            url_d_logentry.change_message,
            'Updated to redirect directly to "/a/" by FixRedirectLoopsTask')

        self.assertEqual(url_g_logentry.user, task_user)
        self.assertEqual(url_g_logentry.content_type_id, content_type_id)
        self.assertEqual(url_g_logentry.action_flag, CHANGE)
        self.assertEqual(
            url_g_logentry.change_message,
            'Updated to redirect directly to "/b/" by FixRedirectLoopsTask')


class HasRedirectsToItListFilterTest(TestCase):
    def setUp(self):
        self.request = RequestFactory().get('')
        self.filter = admin.HasRedirectsToItListFilter(
            self.request, {}, models.URLMap, admin.URLMapAdmin)
        self.mock = mox.Mox()

    def tearDown(self):
        self.mock.UnsetStubs()

    def test_lookups(self):
        self.assertEqual(
            self.filter.lookups(self.request, admin.URLMapAdmin),
            (
                ('yes', 'Yes'),
                ('no', 'No'),
            )
        )

    def test_queryset_value_yes(self):
        queryset = self.mock.CreateMockAnything()

        self.mock.StubOutWithMock(self.filter, 'value')
        self.mock.StubOutWithMock(queryset, 'extra')

        # Expected calls:
        self.filter.value().AndReturn('yes')
        queryset.extra(
            where=["(%s)>0" % admin.SQL_COUNT_REDIRECTS]).AndReturn('qs')

        self.mock.ReplayAll()
        self.assertEqual(self.filter.queryset(self.request, queryset), 'qs')
        self.mock.VerifyAll()

    def test_queryset_value_no(self):
        queryset = self.mock.CreateMockAnything()

        self.mock.StubOutWithMock(self.filter, 'value')
        self.mock.StubOutWithMock(queryset, 'extra')

        # Expected calls:
        self.filter.value().AndReturn('no')
        queryset.extra(
            where=["(%s)=0" % admin.SQL_COUNT_REDIRECTS]).AndReturn('qs')

        self.mock.ReplayAll()
        self.assertEqual(self.filter.queryset(self.request, queryset), 'qs')
        self.mock.VerifyAll()

    def test_queryset_value_none_of_the_above(self):
        queryset = self.mock.CreateMockAnything()

        self.mock.StubOutWithMock(self.filter, 'value')
        self.mock.StubOutWithMock(queryset, 'extra')

        # Expected calls:
        self.filter.value().AndReturn(None)

        self.mock.ReplayAll()
        self.assertEqual(self.filter.queryset(self.request, queryset), None)
        self.mock.VerifyAll()


class URLMapAdminTest(TestCase):
    def setUp(self):
        content_map = models.ContentMap.objects.create(
            view='urlographer.views.route')
        self.urlmap = models.URLMap.objects.create(
            site=Site.objects.get(id=1), path='/test_path',
            content_map=content_map)
        self.admin_instance = admin.URLMapAdmin(models.URLMap, None)
        self.request = RequestFactory().get('')

    def test_get_queryset_without_redirects(self):
        # tox will currently call versions both below and above 1.7
        urlmap = self.admin_instance.get_queryset(self.request)[0]
        self.assertEqual(urlmap.redirects_count, 0)

    def test_get_queryset_with_redirects(self):
        models.URLMap.objects.create(
            site=Site.objects.get(id=1), path='/another_test_path',
            status_code=301, redirect=self.urlmap)
        for urlmap in [
            um for um in self.admin_instance.get_queryset(
                self.request
            ) if um.id == self.urlmap.id
        ]:
            self.assertEqual(urlmap.redirects_count, 1)

    def test_redirects_count(self):
        urlmap = self.admin_instance.get_queryset(self.request)[0]
        self.assertEqual(
            self.admin_instance.redirects_count(urlmap), 0)


class ShouldAppendSlashTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def build_request(self, path):
        return self.factory.get(path)

    def test_should_append_slash_yes(self):
        for path in ['/news/index', '/news/indhtm']:
            request = self.build_request(path)
            self.assertTrue(utils.should_append_slash(request))

    def test_should_append_slash_no(self):
        for path in ['/news/index/', '/news/index.htm', 'news/index.html']:
            request = self.build_request(path)
            self.assertFalse(utils.should_append_slash(request))
