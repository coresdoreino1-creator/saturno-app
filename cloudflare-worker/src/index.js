const ALLOWED_ORIGIN = "https://saturnogestao.vercel.app";
let runtimeAllowedOrigin = ALLOWED_ORIGIN;
const BLOCKED_FILTER_PARAMS = new Set(["user_id", "id", "or", "and", "not"]);
const VALID_TOKEN_ISSUERS = new Set(["accounts.google.com", "https://accounts.google.com"]);

export default {
  async fetch(request, env) {
    try {
      runtimeAllowedOrigin = (env.CORS_ALLOW_ORIGIN || ALLOWED_ORIGIN).trim() || ALLOWED_ORIGIN;
      const url = new URL(request.url);
      const { pathname, searchParams } = url;

      // health
      if (pathname === "/health") {
        return json({
          status: "ok",
          provider: "groq",
          has_groq_key: Boolean(env.GROQ_API_KEY),
          groq_model: normalizeGroqModel(env.GROQ_MODEL),
          require_api_key: (env.REQUIRE_API_KEY || "0") === "1",
        });
      }

      // preflight
      if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors() });

      // rotas protegidas com token
      const protectedPrefixes = [
        "/pautas",
        "/fontes",
        "/templates",
        "/chat/conversas",
        "/chat/mensagens",
        "/notificacoes",
      ];
      if (protectedPrefixes.some((p) => pathname.startsWith(p))) {
        const userId = await requireAuth(request, env);
        return routeSupabase(request, env, userId, pathname, searchParams);
      }

      // chat IA
      if (pathname === "/mensagem" && request.method === "POST") {
        return await handleMensagem(request, env);
      }

      return json({ detail: "Not found" }, 404);
    } catch (err) {
      if (err instanceof Response) return err;
      console.error("Erro geral", err);
      return json({ detail: "Erro interno" }, 500);
    }
  },
};

function cors() {
  return {
    "Access-Control-Allow-Origin": runtimeAllowedOrigin === "*" ? "*" : runtimeAllowedOrigin,
    "Access-Control-Allow-Headers": "Content-Type, Authorization, x-jornasa-key, x-saturno-key",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  };
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...cors() },
  });
}

function csvFromEnv(value) {
  return (value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseFlexibleNumber(value, fallbackValue) {
  if (value === undefined || value === null || value === "") return fallbackValue;
  const normalized = String(value).trim().replace(",", ".");
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : fallbackValue;
}

function normalizeGroqModel(value) {
  const defaultModel = "llama-3.3-70b-versatile";
  if (!value) return defaultModel;
  const raw = String(value).trim();
  if (!raw) return defaultModel;

  const withoutAccent = raw.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  const lowerNoAccent = withoutAccent.toLowerCase();
  if (lowerNoAccent === "llama-3.3-70b-versatil") return defaultModel;
  return withoutAccent.replace(/versatil/gi, "versatile");
}

function resolveApiKey(env) {
  const candidates = [
    env.ACOLHEIA_API_KEY,
    env.SATURNO_API_KEY,
    env.ASSISTANT_API_KEY,
    env.NEXT_PUBLIC_SATURNO_API_KEY,
  ];
  for (const item of candidates) {
    if (typeof item !== "string") continue;
    const value = item.trim();
    if (!value) continue;
    const lower = value.toLowerCase();
    if (lower === "x-saturno-key" || lower === "x-jornasa-key") continue;
    return value;
  }
  return null;
}

function resolveApiHeaderName(env) {
  const raw = (env.ASSISTANT_API_KEY_HEADER || env.EXPO_PUBLIC_ASSISTANT_X_KEY || "x-saturno-key")
    .toString()
    .trim()
    .toLowerCase();
  if (!raw || /\s/.test(raw) || raw.length > 80) return "x-saturno-key";
  return raw;
}

async function requireAuth(request, env) {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : null;
  if (!token) throw json({ detail: "Unauthorized" }, 401);

  const resp = await fetch(`https://oauth2.googleapis.com/tokeninfo?id_token=${token}`);
  if (!resp.ok) throw json({ detail: "Invalid token" }, 401);

  const data = await resp.json();
  const sub = data.sub;
  const aud = data.aud;
  const iss = data.iss;
  const exp = Number(data.exp || 0);
  if (!sub || !iss || !VALID_TOKEN_ISSUERS.has(iss) || !Number.isFinite(exp) || exp <= Date.now() / 1000) {
    throw json({ detail: "Invalid token" }, 401);
  }

  const requireAudience = (env.REQUIRE_GOOGLE_AUDIENCE || "1") === "1";
  const allowedAudiences = csvFromEnv(env.GOOGLE_CLIENT_IDS);
  if (requireAudience && allowedAudiences.length === 0) {
    throw json({ detail: "Auth misconfigured: GOOGLE_CLIENT_IDS ausente." }, 503);
  }
  if (allowedAudiences.length > 0 && !allowedAudiences.includes(aud)) {
    throw json({ detail: "Invalid token audience" }, 403);
  }

  return sub;
}

async function routeSupabase(request, env, userId, pathname, searchParams) {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_KEY) {
    return json({ detail: "Supabase nao configurado." }, 500);
  }
  const headers = {
    apikey: env.SUPABASE_SERVICE_KEY,
    Authorization: `Bearer ${env.SUPABASE_SERVICE_KEY}`,
    "Content-Type": "application/json",
  };

  const tableMap = {
    "/pautas": "pautas",
    "/fontes": "fontes",
    "/templates": "templates",
    "/chat/conversas": "chat_conversas",
    "/chat/mensagens": "chat_mensagens",
    "/notificacoes": "notifications",
  };
  const base = Object.keys(tableMap).find((p) => pathname.startsWith(p));
  if (!base) return json({ detail: "Not found" }, 404);

  const table = tableMap[base];
  const idPart = pathname.slice(base.length).replace(/^\/+/, ""); // para PUT/DELETE /pautas/{id}
  const method = request.method.toUpperCase();
  const allowedMethods = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);
  if (!allowedMethods.has(method)) {
    return json({ detail: "Method not allowed" }, 405);
  }

  const url = new URL(`${env.SUPABASE_URL}/rest/v1/${table}`);
  url.searchParams.set("user_id", `eq.${userId}`);
  if (idPart) url.searchParams.set("id", `eq.${idPart}`);
  url.searchParams.set("order", "created_at.desc");
  searchParams.forEach((v, k) => {
    const normalized = k.toLowerCase();
    if (BLOCKED_FILTER_PARAMS.has(normalized)) return;
    url.searchParams.set(k, v);
  });

  try {
    let body = null;
    if (["POST", "PUT"].includes(method)) {
      const payload = await request.json();
      body = { ...payload, user_id: userId };
      // notificacoes: read default false se nao vier
      if (table === "notifications" && body.read === undefined) body.read = false;
      headers.Prefer = "return=representation";
    }
    const resp = await fetch(url.toString(), {
      method,
      headers,
      body: body ? JSON.stringify(body) : null,
    });
    if (!resp.ok) {
      const errTxt = await resp.text();
      console.error("Supabase error", resp.status, errTxt);
      return json({ detail: "Erro ao acessar Supabase" }, 500);
    }
    const data = await resp.json();
    return json(data);
  } catch (err) {
    console.error("Proxy Supabase error", err);
    return json({ detail: "Erro interno" }, 500);
  }
}

async function handleMensagem(request, env) {
  try {
    const requireApiKey = (env.REQUIRE_API_KEY || "0") === "1";
    const configuredApiKey = resolveApiKey(env);
    const configuredApiHeader = resolveApiHeaderName(env);
    if (requireApiKey) {
      if (!configuredApiKey) {
        return json(
          {
            detail:
              "Servico indisponivel: autenticacao nao configurada. Defina ACOLHEIA_API_KEY (ou SATURNO_API_KEY).",
          },
          503
        );
      }
      const providedKey =
        request.headers.get(configuredApiHeader) ||
        request.headers.get("x-saturno-key") ||
        request.headers.get("x-jornasa-key");
      if (providedKey !== configuredApiKey) {
        return json({ detail: "Chave de acesso invalida." }, 401);
      }
    }

    const payload = await request.json();
    const mensagem = (payload.mensagem || "").trim();
    if (!mensagem) return json({ detail: "Mensagem nao pode ser vazia." }, 400);
    const maxChars = Math.max(10, Math.floor(parseFlexibleNumber(env.MAX_MESSAGE_CHARS, 2000)));
    if (mensagem.length > maxChars) {
      return json({ detail: `Mensagem excede o limite de ${maxChars} caracteres.` }, 413);
    }

    const buscarWeb = !!payload.buscar_web;
    let snippets = null;
    if (buscarWeb && env.SERPER_API_KEY) {
      snippets = await buscarNaWeb(mensagem, env);
    }

    const prompt = buildPrompt(mensagem, snippets, env);
    if (!env.GROQ_API_KEY || !String(env.GROQ_API_KEY).trim()) {
      return json(
        {
          detail:
            "GROQ_API_KEY ausente no Worker. Configure em Settings > Variables (secret) e redeploy.",
        },
        503
      );
    }
    const resposta = await chamarGroq(prompt, env);

    const agora = new Date().toISOString();
    return json({
      resposta_markdown: resposta,
      resposta: resposta,
      resposta_com_fontes: resposta,
      fontes: [],
      contexto: env.CONTEXT_IDENTIFIER || "assistente_confeitaria",
      generated_at: agora,
      model_used: `groq:${normalizeGroqModel(env.GROQ_MODEL)}`,
      used_web_search: buscarWeb,
      is_fallback: false,
    });
  } catch (err) {
    console.error("Erro mensagem", err);
    const mensagemErro =
      err && typeof err === "object" && "message" in err ? String(err.message) : "Erro desconhecido";
    return json({ detail: `Erro ao gerar resposta: ${mensagemErro}` }, 500);
  }
}

function buildPrompt(mensagem, snippets, env) {
  const recente = snippets ? `Informacoes recentes:\n${snippets}` : "Sem buscas recentes.";
  const base = env.TRAINING_TEXT || "";
  return `Voce e o Assistente Saturno de Confeitaria.
Objetivo: orientar confeiteiros(as) com respostas praticas sobre precificacao, producao, pedidos, estoque, atendimento, vendas e rotina.

Regras:
- Linguagem simples, direta e acionavel.
- Em temas de preco/custo/lucro, mostrar formula e exemplo numerico.
- Nao inventar dados e nao prometer lucro.
- Responder completo na primeira mensagem, sem pedir confirmacao.

Informacoes recentes:
${recente}

Contexto de apoio:
${base}

Pedido do usuario: "${mensagem}"

Formato preferencial:
## Entrega
## Proximos passos`;
}

async function chamarGroq(prompt, env) {
  const apiKey = env.GROQ_API_KEY;
  if (!apiKey) throw new Error("GROQ_API_KEY ausente");
  const model = normalizeGroqModel(env.GROQ_MODEL);
  const temperature = parseFlexibleNumber(env.GROQ_TEMPERATURE, 0.3);
  const topP = parseFlexibleNumber(env.GROQ_TOP_P, 0.9);
  const maxTokens = Math.max(100, Math.floor(parseFlexibleNumber(env.GROQ_MAX_TOKENS, 700)));

  const resp = await fetch("https://api.groq.com/openai/v1/chat/completions", {
    method: "POST",
    headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      messages: [{ role: "user", content: prompt }],
      temperature,
      top_p: topP,
      max_tokens: maxTokens,
    }),
  });
  if (!resp.ok) {
    const errorText = await resp.text();
    throw new Error(`Groq HTTP ${resp.status}: ${errorText.slice(0, 300)}`);
  }
  const data = await resp.json();
  return data?.choices?.[0]?.message?.content || "";
}

async function buscarNaWeb(consulta, env) {
  try {
    const resp = await fetch(env.SERPER_SEARCH_URL || "https://google.serper.dev/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-KEY": env.SERPER_API_KEY,
      },
      body: JSON.stringify({ q: consulta, num: 5 }),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    const results = data?.organic || [];
    const snippets = results.slice(0, 5).map((r) => {
      const title = r.title || "";
      const snippet = r.snippet || "";
      const link = r.link || "";
      return [title, snippet, link].filter(Boolean).join(" - ");
    });
    return snippets.filter(Boolean).join("\n");
  } catch {
    return null;
  }
}
