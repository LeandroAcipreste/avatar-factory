# Avatar Factory

MVP local em Python para **receber vídeo consentido**, validar sua estrutura, extrair referências visuais e gerar um relatório básico de qualidade. Ele **não gera avatares, não treina modelos e não envia mídia para serviços externos**. Geração/treino é um conector futuro que deve ser autorizado explicitamente.

## Recursos

- Upload de MP4, MOV e WebM com limite configurável (padrão: 100 MB).
- Nome e checkbox obrigatórios para registrar consentimento.
- Validação real via `ffprobe` no PATH.
- Thumbnail e até 3 frames de amostra via `ffmpeg`, quando disponíveis.
- Relatório: tamanho, duração, resolução, codec e recomendações.
- Jobs persistidos em SQLite local (`data/avatar_factory.db`).
- Interface Jinja2 e API: `GET /api/jobs`, `POST /api/jobs`, `GET /api/jobs/{id}`, `GET /health`; documentação interativa em `/docs`.

## Pré-requisitos

- Python 3.11+ (`py` no Windows)
- `ffmpeg` e `ffprobe` instalados e acessíveis pelo `PATH`

Verifique:

```powershell
ffmpeg -version
ffprobe -version
```

## Execução (Windows)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m uvicorn app.main:app --reload
```

Acesse `http://127.0.0.1:8000`. Para mudar o limite, defina `AVATAR_FACTORY_MAX_UPLOAD_MB` antes de iniciar, por exemplo: `$env:AVATAR_FACTORY_MAX_UPLOAD_MB=250`.

## Testes

```powershell
.\.venv\Scripts\Activate.ps1
py -m pytest
```

## Estrutura

```text
app/          aplicação FastAPI, SQLite, validação e qualidade
templates/    páginas Jinja2
static/       CSS e JavaScript
tests/        testes unitários
data/         banco e mídia local em runtime (ignorado pelo Git)
exemplo/      preservado; não é alterado pelo MVP
```

## Privacidade e próximos passos

A mídia e os derivados permanecem localmente em `data/`. Antes de integrar qualquer provedor de avatar, implemente autenticação, política de retenção/exclusão, trilha de auditoria e uma confirmação específica para o envio ao provedor autorizado.
