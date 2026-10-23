import io
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


if __name__ == '__main__':
    unittest.main()
