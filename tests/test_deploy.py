import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('deploy_render', Path(__file__).resolve().parents[1] / 'deploy/render.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentConfigTests(unittest.TestCase):
    def test_domain_or_base_url_and_consistency(self):
        self.assertEqual(deploy.resolve_domain('Klock.Example.com'), 'klock.example.com')
        self.assertEqual(deploy.resolve_domain(base_url='https://klock.example.com/'), 'klock.example.com')
        with self.assertRaises(ValueError):
            deploy.resolve_domain('one.example.com', 'https://two.example.com')
        with self.assertRaises(ValueError):
            deploy.resolve_domain()

    def test_reject_config_injection_and_invalid_domains(self):
        for value in ('', 'localhost', 'bad.example.com; return 200;', 'bad.example.com\n}',
                      '$(touch /tmp/bad).com', '*.example.com', 'bad..com', '-bad.example.com',
                      'bad_.example.com', 'a'*64 + '.example.com'):
            with self.subTest(domain=value), self.assertRaises(ValueError):
                deploy.validate_domain(value)
        for url in ('http://klock.example.com', 'https://user:pass@klock.example.com',
                    'https://klock.example.com:443', 'https://klock.example.com/path',
                    'https://klock.example.com?x=1', 'https://klock.example.com#fragment'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                deploy.domain_from_base_url(url)

    def test_render_preserves_nginx_and_shell_variables(self):
        with tempfile.TemporaryDirectory() as folder:
            files = deploy.render('klock.example.com', folder)
            self.assertEqual(len(files), 3)
            nginx = (Path(folder) / 'nginx.conf').read_text()
            self.assertIn('server_name klock.example.com;', nginx)
            self.assertIn('/live/klock.example.com/fullchain.pem', nginx)
            self.assertIn('https://klock.example.com$request_uri', nginx)
            self.assertIn('$realip_remote_addr', nginx)
            self.assertIn('$http_x_forwarded_proto', nginx)
            self.assertNotIn('${KLOCK_DOMAIN}', nginx)
            hook = Path(folder) / 'renew-hook.sh'
            self.assertIn('${RENEWED_DOMAINS:-}', hook.read_text())
            self.assertIn(' klock.example.com ', hook.read_text())
            self.assertEqual(hook.stat().st_mode & 0o777, 0o700)
            self.assertEqual((Path(folder) / 'nginx.conf').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
