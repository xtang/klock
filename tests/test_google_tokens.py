"""Exercise Google's real signature verifier with locally signed tokens, no network."""
import json
import time
import unittest
from unittest.mock import Mock, patch
import google_login

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.auth import crypt, jwt
    from google.auth.exceptions import GoogleAuthError
    HAS_GOOGLE_AUTH = True
except ImportError:
    HAS_GOOGLE_AUTH = False

@unittest.skipUnless(HAS_GOOGLE_AUTH, 'Install requirements.txt to exercise Google signature validation')
class TokenVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        cls.signer = crypt.RSASigner.from_string(private, key_id='test-key')
        cls.certs = json.dumps({'test-key': public}).encode()
    def verify(self, **overrides):
        now = int(time.time())
        claims = dict(iss='https://accounts.google.com', aud='test-client', sub='google-subject',
                      iat=now, exp=now+3600, nonce='test-nonce', email='test@gmail.com', email_verified=True)
        claims.update(overrides)
        token = jwt.encode(self.signer, claims).decode()
        with patch('google.auth.transport.requests.Request') as transport:
            transport.return_value.return_value = Mock(status=200, data=self.certs)
            return google_login.verify_id_token(token, 'test-client')
    def test_valid_google_claims(self):
        self.assertEqual(self.verify()['sub'], 'google-subject')
    def test_wrong_audience_issuer_and_expiration(self):
        for changes in [{'aud':'another-client'}, {'iss':'https://attacker.example'},
                        {'exp':int(time.time())-600}, {'iat':int(time.time())+600}]:
            with self.subTest(changes=changes):
                with self.assertRaises((ValueError, GoogleAuthError)): self.verify(**changes)
    def test_bad_signature(self):
        token = jwt.encode(self.signer, {'aud':'test-client','iat':int(time.time()),'exp':int(time.time())+60}).decode()
        parts=token.split('.')
        parts[2]=('A' if parts[2][0]!='A' else 'B')+parts[2][1:]
        with patch('google.auth.transport.requests.Request') as transport:
            transport.return_value.return_value=Mock(status=200,data=self.certs)
            with self.assertRaises((ValueError, GoogleAuthError)): google_login.verify_id_token('.'.join(parts),'test-client')
