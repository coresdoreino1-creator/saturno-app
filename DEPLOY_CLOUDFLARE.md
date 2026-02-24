# Deploy no Cloudflare Workers

Guia rapido para publicar o backend Saturno no Cloudflare Workers usando Groq.
Worker unico padrao deste projeto: `saturno-app`.

## 1. Pre-requisitos
- Node.js 18+
- Conta Cloudflare ativa
- Wrangler instalado (ja esta em `package.json`)

## 2. Entrar na pasta do worker
```bash
cd cloudflare-worker
```

Se o deploy rodar pela raiz do repositório (CI/CD), use `--config`:
```bash
npx wrangler deploy --config cloudflare-worker/wrangler.toml
```

## 3. Login no Cloudflare
```bash
npx wrangler login
```

## 4. Definir secrets obrigatorias
```bash
npx wrangler secret put GROQ_API_KEY --name saturno-app
npx wrangler secret put ACOLHEIA_API_KEY --name saturno-app
```

Secrets opcionais:
```bash
npx wrangler secret put SUPABASE_SERVICE_KEY --name saturno-app
npx wrangler secret put SERPER_API_KEY --name saturno-app
```

## 5. Variaveis de ambiente
As variaveis nao sensiveis ja estao no `wrangler.toml`:
- `CONTEXT_IDENTIFIER=assistente_confeitaria`
- `REQUIRE_API_KEY=0` (modo teste)
- `MAX_MESSAGE_CHARS=2000`
- `GROQ_MODEL=llama-3.3-70b-versatile`
- `CORS_ALLOW_ORIGIN=*` (modo teste)

Para producao:
- `REQUIRE_API_KEY=1`
- `CORS_ALLOW_ORIGIN=https://SEU_FRONTEND`

## 6. Configurar Supabase (se usar rotas protegidas)
No `wrangler.toml`, configure:
- `SUPABASE_URL` (pode ser em `[vars]`)

E mantenha `SUPABASE_SERVICE_KEY` em secret.

## 7. Deploy
```bash
npx wrangler deploy
```

## 8. Teste rapido
```bash
curl -X GET "https://saturno-app.coresdoreino1.workers.dev/health"
```

Exemplo de teste da rota `/mensagem`:
```bash
curl -X POST "https://saturno-app.coresdoreino1.workers.dev/mensagem" \
  -H "Content-Type: application/json" \
  -d '{"mensagem":"Me ajuda a precificar um bolo de 2kg","buscar_web":false}'
```

## 9. Observacoes
- O worker aceita `x-saturno-key` e `x-jornasa-key` por compatibilidade.
- Se `REQUIRE_API_KEY=1`, chamadas sem chave retornam `401`.
- Se faltar `GROQ_API_KEY`, a rota `/mensagem` falha por configuracao incompleta.
