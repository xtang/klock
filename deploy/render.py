"""Render only KLOCK_DOMAIN, preserving Nginx and shell variables verbatim."""
import argparse
import os
from pathlib import Path
import re
from urllib.parse import urlparse

TEMPLATES = ('nginx.conf', 'nginx-http.conf', 'renew-hook.sh')


def validate_domain(domain):
    if not domain or len(domain) > 253 or '.' not in domain:
        raise ValueError('Provide a fully qualified domain, for example klock.example.com')
    labels = domain.split('.')
    if any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in labels):
        raise ValueError('Domain must contain only valid DNS labels (use ASCII/punycode)')
    return domain.lower()


def domain_from_base_url(base):
    parsed = urlparse(base)
    if (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password
            or parsed.port is not None or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Production base URL must be https:// followed by a domain, without a port or path')
    return validate_domain(parsed.hostname)


def resolve_domain(domain=None, base_url=None):
    from_url = domain_from_base_url(base_url) if base_url else None
    chosen = validate_domain(domain) if domain else from_url
    if not chosen:
        raise ValueError('Set KLOCK_DOMAIN or KLOCK_BASE_URL, or pass --domain')
    if from_url and chosen != from_url:
        raise ValueError('KLOCK_DOMAIN and KLOCK_BASE_URL must use the same hostname')
    return chosen


def render(domain, output_dir):
    domain = validate_domain(domain)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in TEMPLATES:
        template = (Path(__file__).parent / (name + '.template')).read_text()
        result = template.replace('${KLOCK_DOMAIN}', domain)
        target = output_dir / name
        target.write_text(result)
        target.chmod(0o700 if name.endswith('.sh') else 0o600)
    return [output_dir / name for name in TEMPLATES]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', default=os.environ.get('KLOCK_DOMAIN'))
    parser.add_argument('--base-url', default=os.environ.get('KLOCK_BASE_URL'))
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).parent / 'generated')
    args = parser.parse_args()
    try:
        domain = resolve_domain(args.domain, args.base_url)
        files = render(domain, args.output_dir)
    except ValueError as error:
        parser.error(str(error))
    for file in files:
        print(file)


if __name__ == '__main__':
    main()
