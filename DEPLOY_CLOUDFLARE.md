# Deploy no Cloudflare Workers

Guia rapido para publicar o backend Saturno no Cloudflare Workers usando Groq.

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
npx wrangler secret put GROQ_API_KEY
npx wrangler secret put ACOLHEIA_API_KEY
```

Secrets opcionais:
```bash
npx wrangler secret put SUPABASE_SERVICE_KEY
npx wrangler secret put SERPER_API_KEY
```

## 5. Variaveis de ambiente
As variaveis nao sensiveis ja estao no `wrangler.toml`:
- `CONTEXT_IDENTIFIER=assistente_confeitaria`
- `REQUIRE_API_KEY=1`
- `MAX_MESSAGE_CHARS=2000`
- `GROQ_MODEL=llama-3.3-70b-versatile`
- `CORS_ALLOW_ORIGIN=https://saturnogestao.vercel.app`

Ajuste o `CORS_ALLOW_ORIGIN` para o dominio real do seu frontend.

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
curl -X GET "https://SEU_WORKER.workers.dev/health"
```

Exemplo de teste da rota `/mensagem`:
```bash
curl -X POST "https://SEU_WORKER.workers.dev/mensagem" \
  -H "Content-Type: application/json" \
  -H "x-saturno-key: SUA_ACOLHEIA_API_KEY" \
  -d '{"mensagem":"Me ajuda a precificar um bolo de 2kg","buscar_web":false}'
```

## 9. Observacoes
- O worker aceita `x-saturno-key` e `x-jornasa-key` por compatibilidade.
- Se `REQUIRE_API_KEY=1`, chamadas sem chave retornam `401`.
- Se faltar `GROQ_API_KEY`, a rota `/mensagem` falha por configuracao incompleta.
