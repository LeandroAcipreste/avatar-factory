#!/usr/bin/env python3
"""Interactive operator-only setup. No network, deployment, or GPU activation."""
import getpass
import os
from pathlib import Path
import re
import stat
import tempfile
import warnings


class ConfigurationError(RuntimeError):
    """Sanitized, stage-specific operator diagnostic."""


def validate_token(token):
    if (not token or token == '***' or token.startswith('oc-sent-')
            or not re.fullmatch(r'[A-Za-z0-9_.~+/=-]+', token)):
        raise ValueError('Token vazio, placeholder, sentinel ou formato inseguro; nada alterado.')
    return token


def merge_env(original, token):
    validate_token(token)
    updates = {'KAGGLE_API_TOKEN': token, 'KAGGLE_GPU_ENABLED': 'false'}
    result = []
    for line in original.splitlines(keepends=True):
        match = re.match(r'^\s*(?:export\s+)?(KAGGLE_API_TOKEN|KAGGLE_GPU_ENABLED)\s*=', line)
        if match:
            key = match.group(1)
            if key in updates:
                result.append(key + '=' + updates.pop(key) + '\n')
        else:
            result.append(line)
    if result and not result[-1].endswith('\n'):
        result[-1] += '\n'
    result.extend(key + '=' + value + '\n' for key, value in updates.items())
    return ''.join(result)


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns,
            info.st_size, info.st_uid, info.st_gid, info.st_mode, info.st_nlink)


def configure(path):
    stage = 'inspeção do arquivo (existência e acesso ao diretório)'
    temporary = None
    try:
        before = path.lstat()
        stage = 'validação de arquivo regular, proprietário e ausência de hard links'
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink != 1):
            raise PermissionError()
        stage = 'leitura do arquivo pelo próprio proprietário'
        # O_NOFOLLOW plus fstat prevents swapping the checked file for a symlink.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r', encoding='utf-8', newline='') as source:
            if signature(os.fstat(source.fileno())) != signature(before):
                raise RuntimeError()
            original = source.read()
        stage = 'abertura do terminal /dev/tty (execute em console interativo)'
        # A buffered r+ stream attempts seeks on a non-seekable terminal.
        # Separate one-way streams avoid that; getpass handles echo via /dev/tty.
        with open('/dev/tty', 'r') as tty_in, open('/dev/tty', 'w') as tty_out:
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                stage = 'leitura oculta no terminal (echo inseguro é recusado)'
                token = getpass.getpass('Token Kaggle (oculto, nunca no chat): ', stream=tty_out)
            stage = 'validação do token (vazio, placeholder ou formato não aceito)'
            updated = merge_env(original, token)
            stage = 'confirmação no terminal'
            tty_out.write('Salvar token e manter GPU DESATIVADA? Digite SIM: ')
            tty_out.flush()
            if tty_in.readline().strip() != 'SIM':
                print('Cancelado; nada alterado.')
                return
        stage = 'gravação do temporário protegido no mesmo diretório'
        fd, temporary = tempfile.mkstemp(prefix='.env-kaggle-', dir=path.parent)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        stage = 'revalidação do arquivo (mudança concorrente recusada)'
        if signature(path.lstat()) != signature(before):
            raise RuntimeError()
        stage = 'substituição atômica do arquivo'
        os.replace(temporary, path)
        temporary = None
        print('Salvo com modo 0600; GPU desativada no .env. Container NÃO foi recriado; ambiente atual continua inalterado.')
    except (OSError, ValueError, RuntimeError, EOFError, getpass.GetPassWarning) as error:
        # Never include exception messages: they can contain credential/file data.
        raise ConfigurationError('Configuração recusada na etapa: ' + stage
                                 + ' [' + type(error).__name__ + ']. Nenhuma credencial exibida.') from None
    finally:
        if temporary is not None:
            os.unlink(temporary)


if __name__ == '__main__':
    try:
        configure(Path(__file__).resolve().parent.parent / '.env')
    except ConfigurationError as error:
        print(str(error))
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('\nCancelado pelo operador; nenhuma credencial exibida.')
        raise SystemExit(130)
