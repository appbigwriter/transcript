"""Encrypted per-platform extraction sessions. No cookie values leave this module."""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from Cryptodome.Cipher import AES

PLATFORMS = {
    'youtube': ('YouTube', {'youtube.com', 'google.com'}),
    'instagram': ('Instagram', {'instagram.com'}),
    'facebook': ('Facebook', {'facebook.com'}),
    'tiktok': ('TikTok', {'tiktok.com'}),
    'x': ('X', {'x.com', 'twitter.com'}),
}

def platform_id(label):
    return next((key for key, (name, _) in PLATFORMS.items() if name == label), None)

def filtered_cookies(content, platform):
    if platform not in PLATFORMS:
        raise ValueError('Plataforma inválida.')
    if len(content.encode('utf-8')) > 1024 * 1024 or '\x00' in content:
        raise ValueError('Arquivo de cookies inválido ou maior que 1 MB.')
    lines = content.lstrip('\ufeff').splitlines()
    if not lines or lines[0].strip() not in {'# Netscape HTTP Cookie File', '# HTTP Cookie File'}:
        raise ValueError('Use um arquivo cookies.txt no formato Netscape.')
    kept, persistent, session = [], [], False
    for line in lines[1:]:
        if not line.strip() or (line.startswith('#') and not line.startswith('#HttpOnly_')):
            continue
        fields = line.split('\t')
        if len(fields) != 7:
            raise ValueError('Arquivo Netscape malformado: cada cookie precisa de sete campos.')
        host = fields[0].removeprefix('#HttpOnly_').lstrip('.').lower()
        if not any(host == domain or host.endswith('.' + domain) for domain in PLATFORMS[platform][1]):
            continue
        try:
            expiry = int(fields[4])
        except ValueError:
            raise ValueError('Um cookie contém uma data de expiração inválida.') from None
        if expiry < 0 or fields[1] not in {'TRUE', 'FALSE'} or fields[3] not in {'TRUE', 'FALSE'} or not fields[2].startswith('/') or not fields[5]:
            raise ValueError('Um cookie contém campos inválidos.')
        if expiry and expiry <= time.time():
            continue
        kept.append('\t'.join(fields))
        if expiry:
            persistent.append(expiry)
        else:
            session = True
    if not kept:
        raise ValueError('Nenhum cookie não expirado desta plataforma foi encontrado.')
    return '# Netscape HTTP Cookie File\n' + '\n'.join(kept) + '\n', {'count': len(kept), 'expires_at': min(persistent) if persistent else None, 'has_session_cookies': session}

class SessionStore:
    def __init__(self, settings):
        self.directory = settings.data_dir / 'sessions'
        self.enabled = not settings.allow_public and bool(settings.password)
        secret = os.getenv('FBR_SESSION_SECRET') or settings.password
        self.key = hashlib.scrypt(secret.encode(), salt=b'FBR-Transcript extraction sessions v1', n=16384, r=8, p=1, dklen=32) if self.enabled else None

    def path(self, platform):
        if platform not in PLATFORMS:
            raise ValueError('Plataforma inválida.')
        return self.directory / (platform + '.enc')

    def read(self, platform):
        path = self.path(platform)
        if not path.exists():
            return None
        if not self.enabled:
            raise ValueError('Sessões exigem acesso protegido por senha.')
        try:
            data = path.read_bytes()
            cipher = AES.new(self.key, AES.MODE_GCM, nonce=data[:16])
            cipher.update(platform.encode())
            return json.loads(cipher.decrypt_and_verify(data[32:], data[16:32]))
        except (ValueError, KeyError, UnicodeDecodeError):
            raise ValueError('Não foi possível abrir esta sessão. Reimporte os cookies; a senha ou chave de proteção pode ter mudado.') from None

    def save(self, platform, content):
        if not self.enabled:
            raise ValueError('Sessões exigem acesso protegido por senha.')
        text, metadata = filtered_cookies(content, platform)
        payload = dict(metadata, content=text, saved_at=time.time(), version=secrets.token_hex(16))
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.path(platform)
        temporary = self.directory / (platform + '-' + secrets.token_hex(8) + '.tmp')
        cipher = AES.new(self.key, AES.MODE_GCM)
        cipher.update(platform.encode())
        encrypted, tag = cipher.encrypt_and_digest(json.dumps(payload).encode())
        try:
            with temporary.open('xb') as stream:
                os.chmod(temporary, 0o600)
                stream.write(cipher.nonce + tag + encrypted)
            os.replace(temporary, path)
            path.with_suffix('.disabled').unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)
        return self.describe(platform)

    def describe(self, platform):
        result = {'platform': platform, 'name': PLATFORMS[platform][0], 'configured': False, 'status': 'not_configured'}
        try:
            payload = self.read(platform)
            if payload:
                result.update({key: value for key, value in payload.items() if key in {'count', 'expires_at', 'has_session_cookies', 'saved_at', 'version'}})
                result.update(configured=True, status='saved')
                try:
                    filtered_cookies(payload['content'], platform)
                except ValueError:
                    result['status'] = 'expired'
        except ValueError:
            result.update(status='unreadable')
        return result

    def remove(self, platform):
        path = self.path(platform)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.unlink(missing_ok=True)
        path.with_suffix('.disabled').touch(mode=0o600)

    def has_override(self, platform):
        path = self.path(platform)
        return path.exists() or path.with_suffix('.disabled').exists()

    def materialize(self, platform, destination):
        payload = self.read(platform)
        if not payload:
            return False
        text, _ = filtered_cookies(payload['content'], platform)
        with Path(destination).open('w', encoding='utf-8', newline='\n') as stream:
            os.chmod(destination, 0o600)
            stream.write(text)
        return True

    def sync_firefox(self, platform):
        self.path(platform)
        profile = os.getenv('FBR_BROWSER_PROFILE', '').strip()
        if not profile or not Path(profile).is_absolute() or not Path(profile).is_dir():
            raise ValueError('O perfil do navegador remoto ainda não está montado no servidor.')
        from yt_dlp.cookies import extract_cookies_from_browser
        class QuietLogger:
            def debug(self, *args, **kwargs): pass
            def info(self, *args, **kwargs): pass
            def warning(self, *args, **kwargs): pass
            def error(self, *args, **kwargs): pass
        try:
            jar = extract_cookies_from_browser('firefox', profile=profile, logger=QuietLogger())
            lines = ['# Netscape HTTP Cookie File']
            for cookie in jar:
                host = cookie.domain.lstrip('.').lower()
                if not any(host == domain or host.endswith('.' + domain) for domain in PLATFORMS[platform][1]):
                    continue
                fields = [cookie.domain, 'TRUE' if cookie.domain_initial_dot else 'FALSE', cookie.path, 'TRUE' if cookie.secure else 'FALSE', str(cookie.expires or 0), cookie.name, cookie.value or '']
                if any(any(character in field for character in ('\t', '\r', '\n', '\x00')) for field in fields):
                    continue
                lines.append('\t'.join(fields))
        except Exception:
            raise ValueError('Não foi possível ler o perfil Firefox. Verifique a montagem e as permissões de leitura do volume.') from None
        return self.save(platform, '\n'.join(lines) + '\n')
