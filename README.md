# Avatar Factory

MVP em Python para **receber vídeo consentido**, validar sua estrutura, extrair referências visuais e gerar um relatório básico de qualidade. Ele **não gera avatares, não treina modelos e não envia mídia para serviços externos**. Geração/treino é um conector futuro que deve ser autorizado explicitamente.

## Recursos

- Upload de MP4, MOV e WebM com limite configurável (padrão: 100 MB).
- Nome e checkbox obrigatórios para registrar consentimento.
- Validação real via `ffprobe` no PATH.
- Thumbnail e até 3 frames de amostra via `ffmpeg`.
- Relatório: tamanho, duração, resolução, codec e recomendações.
- Jobs persistidos em SQLite local (`data/avatar_factory.db`).
- Interface Jinja2 e API: `GET /api/jobs`, `POST /api/jobs`, `GET /api/jobs/{id}`, `GET /health`; documentação interativa em `/docs`.

## Pré-requisitos locais

- Python 3.11+ (`py` no Windows)
- `ffmpeg` e `ffprobe` instalados e acessíveis pelo `PATH`

Verifique:

```powershell
ffmpeg -version
ffprobe -version
```

## Execução local (Windows)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m uvicorn app.main:app --reload
```

Acesse `http://127.0.0.1:8000`. Para mudar o limite, defina `AVATAR_FACTORY_MAX_UPLOAD_MB` antes de iniciar, por exemplo: `$env:AVATAR_FACTORY_MAX_UPLOAD_MB=250`.

## Deploy em VPS Ubuntu com Traefik

O pacote de produção usa `docker-compose.prod.yml`, uma rede bridge dedicada (`avatar-factory-network`) e o volume nomeado `avatar_factory_data`. Não publica porta no host: o Traefik já em `network_mode: host`, com Docker provider habilitado, descobre o serviço pelas labels e encaminha para a porta interna `8000` pela bridge do Docker. Portanto, não conecte este compose a uma rede compartilhada do Traefik.

### Pré-requisitos

1. VPS Ubuntu com Docker Engine e o plugin Docker Compose instalados.
2. Traefik já em execução no host com Docker provider habilitado e acesso ao socket Docker.
3. O entrypoint HTTPS do Traefik deve se chamar `websecure` e estar configurado para emitir/usar certificados TLS. Se sua instalação usar outro nome, ajuste apenas a label `traefik.http.routers.avatar-factory.entrypoints` no `docker-compose.prod.yml`.
4. Crie um registro DNS `A` (e `AAAA`, se aplicável) do domínio escolhido para o IP público da VPS. Aguarde a propagação antes de subir o serviço, para que a emissão TLS do Traefik possa funcionar.
5. Libere as portas 80 e 443 da VPS para o Traefik. Não exponha a porta 8000.

### Subida

Na VPS, copie ou clone este repositório e execute dentro dele:

```bash
cp .env.example .env
nano .env
```

Defina `AVATAR_FACTORY_HOST` com o domínio público, sem `https://`, por exemplo:

```dotenv
AVATAR_FACTORY_HOST=avatar.seudominio.com
AVATAR_FACTORY_MAX_UPLOAD_MB=100
```

O `.env` é ignorado pelo Git; mantenha segredos fora do repositório. O exemplo atual não contém segredos.

Valide a configuração e suba o serviço:

```bash
docker compose --env-file .env -f docker-compose.prod.yml config
docker compose --env-file .env -f docker-compose.prod.yml up -d --build
docker compose --env-file .env -f docker-compose.prod.yml ps
```

Verifique a saúde pelo domínio após o Traefik concluir o roteamento:

```bash
curl -f https://"$AVATAR_FACTORY_HOST"/health
```

Para acompanhar logs ou parar apenas este app:

```bash
docker compose --env-file .env -f docker-compose.prod.yml logs -f avatar-factory
docker compose --env-file .env -f docker-compose.prod.yml down
```

`down` preserva o volume `avatar_factory_data` com SQLite, uploads e derivados. Só use `docker volume rm avatar_factory_data` quando desejar apagar esses dados de forma definitiva.

## Testes

```powershell
.\.venv\Scripts\Activate.ps1
py -m pytest
```

## Estrutura

```text
app/                      aplicação FastAPI, SQLite, validação e qualidade
static/                   CSS e JavaScript
templates/                páginas Jinja2
tests/                    testes unitários
data/                     banco e mídia local em runtime (ignorado pelo Git)
Dockerfile                imagem de produção com ffmpeg e usuário sem privilégios
docker-compose.prod.yml   serviço de produção, volume e labels Traefik
.env.example              configuração pública de exemplo
```

## Privacidade e próximos passos

A mídia e os derivados permanecem no volume persistente local. Antes de integrar qualquer provedor de avatar, implemente autenticação, política de retenção/exclusão, trilha de auditoria e uma confirmação específica para o envio ao provedor autorizado.
