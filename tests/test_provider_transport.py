import io
import json
import os
import sys
import unittest
from email.message import Message
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import service_providers as providers


class ProviderTransportTest(unittest.TestCase):
    def test_json_request_identifies_notta_and_installs_no_redirect_handler(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"id":"accepted"}'
        with patch.object(providers.urllib.request, 'build_opener', return_value=opener) as factory:
            result = providers.request_json('https://api.resend.com/emails', method='POST',
                data={'text':'connection test'}, headers={'Authorization':'Bearer test-secret'}, timeout=25)
        self.assertEqual(result, {'id':'accepted'})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('User-agent'), 'notta/3.5.0 (+https://www.maora.co.jp/)')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-secret')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 25)
        self.assertIsInstance(factory.call_args.args[0], providers.NoRedirect)
        opener.open.assert_called_once()

    def test_redirect_response_is_not_followed(self):
        requests = []
        class RedirectServer(urllib.request.HTTPSHandler):
            def https_open(self, request):
                requests.append(request.full_url)
                headers = Message()
                headers['Location'] = 'https://other.example/collect'
                response = urllib.response.addinfourl(io.BytesIO(b''), headers, request.full_url, 302)
                response.msg = 'Found'
                return response
        opener = urllib.request.build_opener(providers.NoRedirect(), RedirectServer())
        request = urllib.request.Request('https://api.resend.com/emails', data=b'{}', method='POST')
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(request)
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(requests, ['https://api.resend.com/emails'])

    def test_http_and_network_errors_do_not_expose_provider_secrets(self):
        marker = 'test-secret-provider-body'
        cases = [
            (urllib.error.HTTPError('https://api.resend.com/emails?key='+marker, 403, marker, {}, io.BytesIO(marker.encode())), False),
            (urllib.error.HTTPError('https://api.resend.com/emails', 503, marker, {}, io.BytesIO(marker.encode())), True),
            (urllib.error.URLError(marker), True),
        ]
        for failure, uncertain in cases:
            with self.subTest(error_type=type(failure).__name__, uncertain=uncertain):
                opener = MagicMock()
                opener.open.side_effect = failure
                with patch.object(providers.urllib.request, 'build_opener', return_value=opener):
                    with self.assertRaises(providers.ProviderError) as caught:
                        providers.request_json('https://api.resend.com/emails', method='POST', headers={'Authorization':'Bearer '+marker}, data={'text':'test'})
                self.assertEqual(caught.exception.uncertain, uncertain)
                self.assertNotIn(marker, str(caught.exception))
                self.assertNotIn('Authorization', str(caught.exception))
                self.assertNotIn('api.resend.com', str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                opener.open.assert_called_once()


class GeminiTransportTest(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only',
            'NOTTA_AI_ENABLED': 'yes', 'NOTTA_SEARCH_LIMIT_VERIFIED': 'yes'})
        environment.start()
        self.addCleanup(environment.stop)

    @staticmethod
    def response(inputs=100, outputs=30, search=False):
        candidate = {'finishReason': 'STOP', 'content': {'parts': [{'text':
            '公開された事例' if search else json.dumps({'reply': '原稿を更新', 'draft': {'title': '企画'}})}]}}
        if search:
            candidate['groundingMetadata'] = {'webSearchQueries': ['公開情報'] * 30,
                'searchEntryPoint': {'renderedContent': '<p>Google Search</p>'},
                'groundingChunks': [{'web': {'title': '公開情報', 'uri': 'https://example.test'}}]}
        return {'candidates': [candidate], 'usageMetadata': {'promptTokenCount': inputs,
            'candidatesTokenCount': outputs, 'thoughtsTokenCount': 0}}

    def test_normal_dialogue_keeps_model_and_measures_schema_without_changing_generation(self):
        with patch.object(providers, 'request_json', side_effect=[{'totalTokens': 1200}, self.response()]) as transport:
            result = providers.generate({'title': '企画'}, [], '進めたい')
        count, generate = transport.call_args_list
        self.assertTrue(count.args[0].endswith('/gemini-3.5-flash-lite:countTokens'))
        self.assertTrue(generate.args[0].endswith('/gemini-3.5-flash-lite:generateContent'))
        measured = count.kwargs['data']['generateContentRequest']
        payload = generate.kwargs['data']
        self.assertEqual(measured['model'], 'models/gemini-3.5-flash-lite')
        self.assertEqual(measured['systemInstruction'], payload['systemInstruction'])
        self.assertEqual(measured['generationConfig']['responseJsonSchema'], providers.DRAFT_SCHEMA)
        self.assertEqual(json.loads(measured['contents'][-1]['parts'][-1]['text']), payload['generationConfig'])
        self.assertEqual(len(payload['contents'][-1]['parts']), 1)
        self.assertEqual(payload['generationConfig']['maxOutputTokens'], 6000)
        self.assertNotIn('tools', payload)
        self.assertEqual(result['search_queries'], 0)
        self.assertEqual(transport.call_count, 2)

    def test_search_uses_one_fixed_prompt_and_conservative_seventeen_yen_bound(self):
        responses = [{'totalTokens': 8192}, self.response(8192, 1000, search=True),
                     {'totalTokens': 32768}, self.response(32768, 6000)]
        with patch.object(providers, 'request_json', side_effect=responses) as transport:
            result = providers.generate({'title': 'private-project'},
                [{'role': 'user', 'body': 'private-chat'}], '原稿作成', '公開情報')
        count_search, search, count_draft, draft = transport.call_args_list
        self.assertTrue(count_search.args[0].endswith('/gemini-2.5-flash:countTokens'))
        self.assertTrue(search.args[0].endswith('/gemini-2.5-flash:generateContent'))
        self.assertTrue(count_draft.args[0].endswith('/gemini-3.5-flash-lite:countTokens'))
        self.assertTrue(draft.args[0].endswith('/gemini-3.5-flash-lite:generateContent'))
        config = search.kwargs['data']['generationConfig']
        self.assertEqual(config, {'maxOutputTokens': 1000, 'candidateCount': 1, 'thinkingConfig': {'thinkingBudget': 0}})
        self.assertEqual(count_search.kwargs['data']['generateContentRequest']['tools'], [{'google_search': {}}])
        for call in (count_search, search):
            self.assertNotIn('private-', json.dumps(call.kwargs['data']))
        self.assertEqual(result['search_queries'], 30)
        self.assertEqual(result['cost_yen'], 17)
        self.assertEqual(result['search_entry_point'], '<p>Google Search</p>')
        self.assertEqual(transport.call_count, 4)

    def test_invalid_or_excess_search_count_never_starts_generation(self):
        for count in (8193, None, True, '100', -1):
            with self.subTest(count=count), patch.object(providers, 'request_json', return_value={'totalTokens': count}) as transport:
                with self.assertRaises(providers.ProviderError) as caught:
                    providers.generate({}, [], '作成', '公開情報')
                self.assertFalse(caught.exception.uncertain)
                self.assertEqual(transport.call_count, 1)
                self.assertTrue(transport.call_args.args[0].endswith(':countTokens'))

    def test_excess_draft_count_after_search_preserves_uncertain_reservation(self):
        with patch.object(providers, 'request_json', side_effect=[{'totalTokens': 100},
                self.response(search=True), {'totalTokens': 32769}]) as transport:
            with self.assertRaises(providers.ProviderError) as caught:
                providers.generate({}, [], '作成', '公開情報')
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(transport.call_count, 3)
        self.assertTrue(transport.call_args.args[0].endswith('/gemini-3.5-flash-lite:countTokens'))

    def test_token_count_failure_cannot_generate_or_incur_inference(self):
        with patch.object(providers, 'request_json', side_effect=providers.ProviderError('count failed', uncertain=True)) as transport:
            with self.assertRaises(providers.ProviderError) as caught:
                providers.generate({}, [], '作成', '公開情報')
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(transport.call_args.args[0].endswith(':countTokens'))

    def test_search_rejection_does_not_retry_or_fall_back(self):
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain), patch.object(providers, 'request_json',
                    side_effect=[{'totalTokens': 100}, providers.ProviderError('rejected', uncertain=uncertain)]) as transport:
                with self.assertRaises(providers.ProviderError) as caught:
                    providers.generate({}, [], '作成', '公開情報')
                self.assertEqual(caught.exception.uncertain, uncertain)
                self.assertEqual(transport.call_count, 2)
                self.assertTrue(all('/gemini-2.5-flash:' in call.args[0] for call in transport.call_args_list))

    def test_reported_usage_outside_output_limit_is_not_accepted(self):
        with patch.object(providers, 'request_json', side_effect=[{'totalTokens': 100}, self.response(outputs=6001)]) as transport:
            with self.assertRaises(providers.ProviderError) as caught:
                providers.generate({}, [], '作成')
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(transport.call_count, 2)

    def test_missing_output_usage_and_unverified_tool_inputs_remain_uncertain(self):
        for usage in ({'promptTokenCount': 100},
                      {'promptTokenCount': 100, 'candidatesTokenCount': 30, 'toolUsePromptTokenCount': 1000000}):
            response = self.response()
            response['usageMetadata'] = usage
            with self.subTest(usage=usage), patch.object(providers, 'request_json', side_effect=[{'totalTokens': 100}, response]) as transport:
                with self.assertRaises(providers.ProviderError) as caught:
                    providers.generate({}, [], '作成')
                self.assertTrue(caught.exception.uncertain)
                self.assertEqual(transport.call_count, 2)

    def test_disabled_search_flag_makes_no_requests(self):
        with patch.dict(os.environ, {'NOTTA_SEARCH_LIMIT_VERIFIED': ''}), patch.object(providers, 'request_json') as transport:
            with self.assertRaises(providers.ProviderError):
                providers.generate({}, [], '作成', '公開情報')
        transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
