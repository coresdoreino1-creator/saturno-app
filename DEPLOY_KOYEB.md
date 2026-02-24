# Deploy no Koyeb

Guia rapido para publicar o Saturno Backend no plano gratuito da Koyeb.

## 1. Preparar o repositório
- Confirme que o app sobe localmente com `uvicorn apigemini:app --host 0.0.0.0 --port 8080`.
- Garanta que o novo `Dockerfile` está commitado.
- Crie um repositório no GitHub (público ou privado) e faça push do código.

## 2. Variáveis obrigatórias
Configure como **Environment Variables** (marque *Secret* no painel):

| Nome | Descrição |
| ---- | --------- |
| `GROQ_API_KEY` | Chave da API Groq (modelo principal) |
| `ACOLHEIA_API_KEY` | Token privado para autenticação da API |
| `USE_GROQ` | Defina `1` para usar Groq como provedor principal |
| `GROQ_MODEL` (opcional) | Modelo Groq, ex.: `llama-3.3-70b-versatile` |
| `GEMINI_API_KEY` (opcional) | Fallback para Gemini se o Groq indisponivel |
| `LOG_LEVEL` (opcional) | Nível de log, ex.: `INFO` |
| `MAX_MESSAGE_CHARS` (opcional) | Limite de caracteres aceito pela API |

Outras variáveis usadas em `apigemini.py` também podem ser definidas aqui se necessário.

## 3. Criar o app na Koyeb
1. Acesse [app.koyeb.com](https://app.koyeb.com) e clique em **Create App**.
2. Fonte: escolha **GitHub**, autorize a conta e selecione repositório + branch.
3. Builder: `Dockerfile`.
4. Porta: `8080`.
5. Região: escolha a mais próxima do seu público.
6. Plano: mantenha o tier gratuito **Nano** (256 MB, sempre ligado).

## 4. Deploy e validação
- Clique em **Create App** e aguarde o build.
- Acompanhe em **Logs**; o serviço deve escutar em `https://<app>.koyeb.app`.
- Teste `GET /docs` ou outro endpoint com `curl` para validar.
- Configure um health check HTTP 200 no painel para reinícios automáticos em caso de falha.

## 5. Manutenção
- Cada push na branch configurada dispara novo deploy.
- Use **Scale → Redeploy** caso precise forçar rebuild.
- Monitore consumo de memória (Analytics). Se exceder 256 MB, considere otimizações ou upgrade.
