# Teste de GPU no Kaggle — XTTS-v2 (isolado do produto)

Este diretório é um **experimento de pesquisa independente**: usa um vídeo de referência autorizado para extrair áudio e sintetizar fala em pt-BR com clonagem de voz via [XTTS-v2](https://huggingface.co/coqui/XTTS-v2). Ele não altera o app FastAPI, Docker ou dependências de produção.

> Use somente mídia e vozes para as quais haja consentimento explícito. Não use para imitar terceiros sem autorização.

## O que faz e o que não faz

- Detecta GPU/CUDA no notebook; usa GPU quando o PyTorch do Kaggle a disponibiliza.
- Extrai do vídeo um WAV mono a 24 kHz com `ffmpeg`.
- Baixa os pesos do modelo **somente dentro do ambiente temporário do Kaggle, na primeira execução**, e gera um WAV a partir de texto configurável.
- **Ainda não mede fidelidade, semelhança ou qualidade de voz**; valide por escuta humana autorizada.
- **Ainda não faz lip-sync**, animação facial ou integração com o app de produção.

## Licença e uso

O código deste diretório é apenas um invólucro de teste. Antes de qualquer uso de produto ou comercial, verifique separadamente os termos, licença, permissões de redistribuição e restrições de uso do **modelo e dos pesos XTTS-v2** na fonte oficial, bem como as licenças de suas dependências. Não presuma que uma biblioteca ou modelo disponível publicamente esteja liberado para uso comercial. Registre a revisão jurídica e a versão exata escolhida antes de integrar ao produto.

## Preparar um notebook Kaggle

1. Crie um Notebook em Kaggle e em **Settings > Accelerator** selecione `GPU`.
2. Em **Settings > Internet**, habilite-o apenas para a célula que instalará dependências/baixará o modelo. O primeiro uso do XTTS-v2 requer acesso ao repositório de pesos; depois, desative Internet se não for mais necessário.
3. Envie `gpu/xtts_kaggle.ipynb` e `gpu/xtts_kaggle.py` ao notebook, ou cole o script como arquivo auxiliar.
4. Anexe o vídeo de referência como Dataset privado no Kaggle. O notebook usa por padrão `/kaggle/input/video-referencia/referencia.mp4`; ajuste as variáveis conforme o nome do seu Dataset/arquivo.
5. Execute as células em ordem. Os WAVs ficam em `/kaggle/working/outputs/` e podem ser baixados pela aba **Output**.

## Transferir o vídeo da VPS sem expor credenciais

**Recomendação:** no seu computador, baixe o arquivo por um canal autenticado que você já administra (por exemplo, SFTP/SCP com chave SSH) e então faça upload manual pela interface Kaggle como Dataset privado. Não cole senhas, chaves privadas, tokens, URLs assinadas ou variáveis de ambiente no notebook, em células, outputs ou no Git.

Exemplo executado **no computador do operador**, com host/caminho substituídos e sem registrar segredos no repositório:

```bash
scp operador@seu-host:/caminho/autorizado/referencia.mp4 ./referencia.mp4
```

Confirme tamanho e duração localmente, faça upload como **Private Dataset** e não publique/compartilhe esse Dataset sem autorização. Alternativamente, envie pela própria UI de upload do Kaggle a partir de uma cópia local autorizada. O notebook não acessa VPS, SSH, nem credenciais.

## Execução por script

Dentro do Kaggle, após instalar as dependências:

```bash
python xtts_kaggle.py \
  --video /kaggle/input/video-referencia/referencia.mp4 \
  --text "Olá. Este é um teste autorizado de síntese de voz em português brasileiro." \
  --reference-wav /kaggle/working/outputs/reference.wav \
  --output /kaggle/working/outputs/xtts_ptbr.wav
```

Para repetir a síntese com o WAV já extraído, use `--skip-extract` e informe `--reference-wav`.

## Diagnóstico rápido

- `torch.cuda.is_available()` deve retornar `True`; se não retornar, reveja o Accelerator do notebook.
- O `ffmpeg` deve existir na imagem Kaggle. Se ausente, a célula de preparação do notebook tenta instalá-lo.
- O primeiro carregamento pode falhar sem Internet ou se a fonte dos pesos exigir aceite/autenticação. Corrija isso na configuração Kaggle; não coloque credenciais no notebook.

## Arquivos

- `requirements-kaggle.txt`: dependências exclusivas do experimento.
- `xtts_kaggle.py`: extração, diagnóstico CUDA e síntese reutilizável.
- `xtts_kaggle.ipynb`: fluxo reproduzível para Kaggle.
