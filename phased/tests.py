import re, unittest
from django.template import compile_string, Context, TemplateSyntaxError
from django.http import HttpRequest, HttpResponse
from django.middleware.cache import FetchFromCacheMiddleware, UpdateCacheMiddleware
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.utils.cache import patch_vary_headers
from phased.utils import second_pass_render, pickle_context, unpickle_context, flatten_context, drop_vary_headers
from phased.middleware import PhasedRenderMiddleware, PatchedVaryUpdateCacheMiddleware
from phased import settings

class TwoPhaseTestCase(unittest.TestCase):

    test_template = (
        "{% load phased_tags %}"
        "{% phased %}"
        "{% if 1 %}test{% endif %}"
        "{% endphased %}"
        "{{ test_var }}"
    )
    def setUp(self):
        self.old_keep_context = settings.KEEP_CONTEXT
        settings.KEEP_CONTEXT = False

    def tearDown(self):
        settings.KEEP_CONTEXT = self.old_keep_context

    def test_phased(self):
        context = Context({'test_var': 'TEST'})
        first_render = compile_string(self.test_template, None).render(context)
        self.assertEqual(first_render, '%s{%% if 1 %%}test{%% endif %%}%sTEST' % (settings.SECRET_DELIMITER, settings.SECRET_DELIMITER))

    def test_second_pass(self):
        request = HttpRequest()
        request.method = 'GET'

        first_render = compile_string(self.test_template, None).render(Context({'test_var': 'TEST'}))
        second_render = second_pass_render(request, first_render)
        self.assertEqual(second_render, 'testTEST')

class FancyTwoPhaseTestCase(TwoPhaseTestCase):
    def setUp(self):
        self.old_secret_delimiter = settings.SECRET_DELIMITER
        settings.SECRET_DELIMITER = "fancydelimiter"
        super(FancyTwoPhaseTestCase, self).setUp()

    def tearDown(self):
        settings.SECRET_DELIMITER = self.old_secret_delimiter
        super(FancyTwoPhaseTestCase, self).tearDown()

    def test_phased(self):
        context = Context({'test_var': 'TEST'})
        first_render = compile_string(self.test_template, None).render(context)
        self.assertEqual(first_render, 'fancydelimiter{% if 1 %}test{% endif %}fancydelimiterTEST')

    def test_second_pass(self):
        request = HttpRequest()
        request.method = 'GET'

        first_render = compile_string(self.test_template, None).render(Context({'test_var': 'TEST'}))
        second_render = second_pass_render(request, first_render)
        self.assertEqual(second_render, 'testTEST')


class NestedTwoPhaseTestCase(TwoPhaseTestCase):
    test_template = (
        "{% load phased_tags %}"
        "{% phased %}"
        "{% load phased_tags %}"
        "{% phased %}"
        "{% if 1 %}first{% endif %}"
        "{% endphased %}"
        "{% if 1 %}second{% endif %}"
        "{% endphased %}"
        "{{ test_var }}"
    )

    def test_phased(self):
        context = Context({'test_var': 'TEST'})
        first_render = compile_string(self.test_template, None).render(context)
        self.assertEqual(first_render, '%(del)s{%% load phased_tags %%}{%% phased %%}{%% if 1 %%}first{%% endif %%}{%% endphased %%}{%% if 1 %%}second{%% endif %%}%(del)sTEST' % {'del': settings.SECRET_DELIMITER})

    def test_second_pass(self):
        request = HttpRequest()
        request.method = 'GET'

        first_render = compile_string(self.test_template, None).render(Context({'test_var': 'TEST'}))
        second_render = second_pass_render(request, first_render)
        self.assertEqual(second_render, 'firstsecondTEST')


class StashedTestCase(TwoPhaseTestCase):
    test_template = (
        "{% load phased_tags %}"
        "{% phased %}"
        "{% if 1 %}test{% endif %}"
        "{% if test_condition %}"
        "stashed"
        "{% endif %}"
        "{% endphased %}"
        "{{ test_var }}"
        "{% phased %}"
        "{% if 1 %}test2{% endif %}"
        "{% if test_condition2 %}"
        "stashed"
        "{% endif %}"
        "{% endphased %}"
    )
    def setUp(self):
        super(StashedTestCase, self).setUp()
        settings.KEEP_CONTEXT = True

    def test_phased(self):
        context = Context({'test_var': 'TEST'})
        pickled_context = pickle_context(context)
        first_render = compile_string(self.test_template, None).render(context)
        self.assertEqual(first_render, '%(delimiter)s{%% if 1 %%}test{%% endif %%}{%% if test_condition %%}stashed{%% endif %%}%(pickled_context)s%(delimiter)sTEST%(delimiter)s{%% if 1 %%}test2{%% endif %%}{%% if test_condition2 %%}stashed{%% endif %%}%(pickled_context)s%(delimiter)s' % dict(delimiter=settings.SECRET_DELIMITER, pickled_context=pickled_context))

    def test_second_pass(self):
        request = HttpRequest()
        request.method = 'GET'
        context = Context({
            'test_var': 'TEST',
            'test_condition': True,
            'test_condition2': True,
        })
        first_render = compile_string(self.test_template, None).render(context)
        second_render = second_pass_render(request, first_render)
        self.assertEqual(second_render, 'teststashedTESTtest2stashed')


class PickyStashedTestCase(StashedTestCase):
    test_template = (
        '{% load phased_tags %}'
        '{% phased with "test_var" test_condition %}'
        '{% if 1 %}test{% endif %}'
        '{% if test_condition %}'
        'stashed'
        '{% endif %}'
        '{% endphased %}'
        '{{ test_var }}'
    )
    def test_phased(self):
        context = Context({'test_var': 'TEST'})
        self.assertRaises(TemplateSyntaxError,
            compile_string(self.test_template, None).render, context)
        context = Context({
            'test_var': 'TEST',
            'test_condition': True,
        })
        pickled_context = pickle_context(context)
        first_render = compile_string(self.test_template, None).render(context)
        self.assertEqual(first_render, '%(delimiter)s{%% if 1 %%}test{%% endif %%}{%% if test_condition %%}stashed{%% endif %%}%(pickled_context)s%(delimiter)sTEST' % dict(delimiter=settings.SECRET_DELIMITER, pickled_context=pickled_context))

    def test_second_pass(self):
        request = HttpRequest()
        request.method = 'GET'
        context = Context({
            'test_var': 'TEST',
            'test_var2': 'TEST2',
            'test_condition': True,
        })
        first_render = compile_string(self.test_template, None).render(context)
        original_context = unpickle_context(first_render)
        self.assertEqual(original_context.get('test_var'), 'TEST')
        second_render = second_pass_render(request, first_render)
        self.assertEqual(second_render, 'teststashedTEST')


class UtilsTestCase(unittest.TestCase):

    def test_flatten(self):
        context = Context({'test_var': 'TEST'})
        context.update({'test_var': 'TEST2', 'abc': 'def'})
        self.assertEqual(flatten_context(context), {'test_var': 'TEST2', 'abc': 'def'})

    def test_pickling(self):
        self.assertRaises(TemplateSyntaxError, pickle_context, {})
        self.assertEqual(pickle_context(Context()), '{# stashed context: "gAJ9Lg==" #}')
        context = Context({'test_var': 'TEST'})
        template = '<!-- better be careful %s yikes -->'
        self.assertEqual(pickle_context(context), '{# stashed context: "gAJ9cQFVCHRlc3RfdmFycQJVBFRFU1RxA3Mu" #}')
        self.assertEqual(pickle_context(context, template), '<!-- better be careful gAJ9cQFVCHRlc3RfdmFycQJVBFRFU1RxA3Mu yikes -->')

    def test_unpickling(self):
        self.assertEqual(unpickle_context(pickle_context(Context())), flatten_context(Context()))
        context = Context({'test_var': 'TEST'})
        pickled_context = pickle_context(context)
        unpickled_context = unpickle_context(pickled_context)
        self.assertEqual(flatten_context(context), unpickled_context)

    def test_unpickling_with_template_and_pattern(self):
        context = Context({'test_var': 'TEST'})
        template = '<!-- better be careful %s yikes -->'
        pattern = re.compile(r'.*<!-- better be careful (.*) yikes -->.*')
        pickled_context = pickle_context(context, template)
        unpickled_context = unpickle_context(pickled_context, pattern)
        self.assertEqual(flatten_context(context), unpickled_context)


class PhasedRenderMiddlewareTestCase(unittest.TestCase):
    def test_basic(self):
        request = HttpRequest()
        response = HttpResponse(
            'before '
            '%(delimiter)s '
            'inside{# a comment #} '
            '%(delimiter)s '
            'after' % dict(delimiter=settings.SECRET_DELIMITER))

        response = PhasedRenderMiddleware().process_response(request, response)

        self.assertEqual(response.content, 'before  inside  after')

class PatchedVaryUpdateCacheMiddlewareTestCase(unittest.TestCase):

    def setUp(self):
        # clear cache
        for key in cache._cache.keys():
            cache.delete(key)

    def test_no_vary(self):
        """
        Ensure basic caching works.
        """
        request = HttpRequest()
        request.method = 'GET'
        response = HttpResponse()

        SessionMiddleware().process_request(request)
        AuthenticationMiddleware().process_request(request)

        cache_hit = FetchFromCacheMiddleware().process_request(request)
        self.assertEqual(cache_hit, None)

        response = PatchedVaryUpdateCacheMiddleware().process_response(request, response)
        cache_hit = FetchFromCacheMiddleware().process_request(request)

        self.assertTrue(isinstance(cache_hit, HttpResponse))

    def test_vary(self):
        """
        Ensure caching works even when cookies are present and `Vary: Cookie` is on.
        """
        request = HttpRequest()
        request.method = 'GET'
        request.COOKIES = {'test': 'foo'}
        request.META['HTTP_COOKIE'] = 'test=foo'

        response = HttpResponse()
        patch_vary_headers(response, ['Cookie'])
        response.set_cookie('test', 'foo')

        SessionMiddleware().process_request(request)
        AuthenticationMiddleware().process_request(request)

        cache_hit = FetchFromCacheMiddleware().process_request(request)
        self.assertEqual(cache_hit, None)

        response = PatchedVaryUpdateCacheMiddleware().process_response(request, response)
        cache_hit = FetchFromCacheMiddleware().process_request(request)

        self.assertTrue(isinstance(cache_hit, HttpResponse))

        new_request = HttpRequest()
        new_request.method = 'GET'
        # note: not using cookies here. this demonstrates that cookies don't
        # affect the cache key
        cache_hit = FetchFromCacheMiddleware().process_request(new_request)
        self.assertTrue(isinstance(cache_hit, HttpResponse))

    def test_vary_with_original_update_cache_middleware(self):
        """
        Mainly to demonstrate the need to remove the Vary: Cookie header
        during caching. Same basic test as test_vary() but with django's
        UpdateCacheMiddleware instead of PatchedVaryUpdateCacheMiddleware.
        This does not get a cache hit if the cookies are not the same.
        """
        request = HttpRequest()
        request.method = 'GET'
        request.COOKIES = {'test': 'foo'}
        request.META['HTTP_COOKIE'] = 'test=foo'

        response = HttpResponse()
        patch_vary_headers(response, ['Cookie'])
        response.set_cookie('test', 'foo')

        SessionMiddleware().process_request(request)
        AuthenticationMiddleware().process_request(request)

        cache_hit = FetchFromCacheMiddleware().process_request(request)
        self.assertEqual(cache_hit, None)

        response = UpdateCacheMiddleware().process_response(request, response)
        cache_hit = FetchFromCacheMiddleware().process_request(request)

        self.assertTrue(isinstance(cache_hit, HttpResponse))

        new_request = HttpRequest()
        new_request.method = 'GET'
        # note: not using cookies here. this demonstrates that cookies don't
        # affect the cache key
        cache_hit = FetchFromCacheMiddleware().process_request(new_request)
        self.assertEqual(cache_hit, None)

    def test_drop_vary_headers(self):
        response = HttpResponse()

        self.assertFalse(response.has_header('Vary'))
        patch_vary_headers(response, ['Cookie'])
        self.assertTrue(response.has_header('Vary'))
        self.assertEqual(response['Vary'], 'Cookie')
        patch_vary_headers(response, ['Nomnomnom'])
        self.assertEqual(response['Vary'], 'Cookie, Nomnomnom')
        drop_vary_headers(response, ['Cookie'])
        self.assertEqual(response['Vary'], 'Nomnomnom')
        drop_vary_headers(response, ['Nomnomnom'])
        self.assertFalse(response.has_header('Vary'))
