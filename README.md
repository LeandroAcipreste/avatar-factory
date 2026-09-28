# Avatar Factory

MVP em Python para **receber vídeo consentido**, validar sua estrutura, extrair referências visuais e gerar relatórios técnicos de imagem e áudio. Ele **não clona voz, não gera mídia e não envia arquivos para serviços externos**. A declaração única de consentimento cobre imagem e voz para criação de avatar e conteúdos autorizados pelo cliente; qualquer conector externo futuro deverá ser autorizado e implementado separadamente.

## Recursos

- Upload de MP4, MOV e WebM com limite configurável (padrão: 100 MB).
- Nome e declaração única obrigatória para registrar consentimento de imagem e voz.
- Validação real via `ffprobe` no PATH, inclusive stream de áudio, codec, duração, sample rate, canais e bitrate quando disponível.
- Thumbnail e até 3 frames de amostra via `ffmpeg`.
- Análise técnica local e opcional de loudness/silêncio via `ffmpeg`, tolerante a falhas.
- Relatório informativo: tamanho, duração, resolução, codecs, áudio e recomendações. Não identifica pessoas nem mede semelhança de voz.
- Jobs persistidos em SQLite local (`data/avatar_factory.db`), com estado e histórico sanitizado de despacho GPU.
- Adaptador Kaggle opcional: após o upload o job é persistido e o encaminhamento é tentado automaticamente, sem botão manual; por padrão ele fica em `uploaded` e não faz chamadas externas.
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

## Despacho GPU Kaggle (experimental)

O adapter Kaggle é uma integração **experimental e efêmera**, não um worker de produção confiável: sessões, quotas e disponibilidade do Kaggle podem terminar sem aviso. O fluxo padrão mantém `KAGGLE_GPU_ENABLED=false`, portanto testes e uploads locais não acionam rede. Ao habilitar, o job já persistido é encaminhado automaticamente; não há segundo botão nem etapa adicional de consentimento.

Instale o **Kaggle CLI oficial >=1.8** somente no ambiente que vai usar o adapter: `py -m pip install "kaggle>=1.8"`. Configure, fora do repositório, `KAGGLE_GPU_ENABLED=true`, `KAGGLE_USERNAME`, `KAGGLE_API_TOKEN`, `KAGGLE_KERNEL_REF` e `KAGGLE_DATASET_SLUG`. `KAGGLE_USERNAME` serve apenas para montar refs como `usuario/dataset`; a autenticação usa exclusivamente o token moderno. O processo filho recebe um ambiente restrito com `KAGGLE_API_TOKEN` e um `KAGGLE_CONFIG_DIR` gravável dentro do staging do job. Nenhum arquivo de credenciais é criado e o token nunca entra no código, banco, histórico ou logs.

Para cada job, o adapter cria `data/kaggle-staging/<job>/dataset` e `kernel`, copia o vídeo para o dataset, gera `dataset-metadata.json`, `kernel-metadata.json` (GPU habilitada) e um `kernel.py`. Ele executa, via CLI, `kaggle datasets view`, cria o dataset somente diante de um 404 inequívoco ou envia uma versão nova, e por fim chama `kaggle kernels push`. Se a consulta for ambígua (por exemplo, permissão ou transporte), a operação falha em `gpu_dispatch_failed` sem criar dataset especulativamente.

`gpu_queued` significa somente que o CLI aceitou a operação de dataset e o push do kernel — **não** que o kernel terminou nem que produziu mídia. O script gerado grava apenas um manifesto em `/kaggle/working/dispatch_result.json`; um worker de produção deve substituir esse contrato por processamento e coleta de outputs confiáveis. Os estados são `uploaded` (desativado/aguardando), `gpu_queued` (push aceito) e `gpu_dispatch_failed` (falha segura, com mensagem sanitizada); a análise local fica em `processing_status`.

Para produção, implemente outro provider em `app/gpu_dispatch.py` (fila/worker durável, observabilidade e retenção) preservando a mesma interface de dispatcher e sem alterar a declaração única de consentimento.

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

A mídia e os derivados permanecem no volume persistente local. A aptidão da análise local aparece em `processing_status` como **avatar_prepared** quando imagem e áudio atendem aos critérios técnicos básicos (vídeo com pelo menos 10 s e 720p; áudio detectado com qualidade técnica adequada). O relatório é informativo, não uma avaliação de identidade ou de qualidade artística. O provider Kaggle, se habilitado, é experimental e não substitui um worker de produção com retenção, auditoria e disponibilidade controladas.
