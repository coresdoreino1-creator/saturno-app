from __future__ import annotations

import argparse
from collections import defaultdict, deque
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
import time
from typing import List, Optional

from pypdf import PdfReader
import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
from groq import Groq
from supabase import Client, create_client

load_dotenv()

def _int_env(nome: str, default: int, min_value: int = 1) -> int:
    """Le numero inteiro da env com limite minimo para evitar valores invalidos."""
    try:
        valor = int(os.getenv(nome, default))
    except (TypeError, ValueError):
        return max(default, min_value)
    return max(valor, min_value)

def _float_env(nome: str, default: float) -> float:
    try:
        return float(os.getenv(nome, default))
    except (TypeError, ValueError):
        return float(default)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("saturno.backend")

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

DISABLE_GEMINI = os.getenv("DISABLE_GEMINI", "0") == "1"
MODEL_NAME = os.getenv("GEMINI_MODEL", "models/gemini-flash-latest")
MAX_PROMPT_CHARS = _int_env("MAX_PROMPT_CHARS", 12000, 1000)  # reduz ainda mais prompt
MAX_KNOWLEDGE_CHARS = _int_env("MAX_KNOWLEDGE_CHARS", 6000, 1000)  # limita texto do resumo
MAX_MESSAGE_CHARS = _int_env("MAX_MESSAGE_CHARS", 2000, 10)
GEMINI_TIMEOUT_SECONDS = _int_env("GEMINI_TIMEOUT_SECONDS", 15, 5)
GEMINI_RETRIES = _int_env("GEMINI_RETRIES", 2, 0)
ALLOW_GENERAL_TOPICS = os.getenv("ALLOW_GENERAL_TOPICS", "0") == "1"
INCLUDE_DEFAULT_FONTES = os.getenv("INCLUDE_DEFAULT_FONTES", "0") == "1"
USE_GROQ = os.getenv("USE_GROQ", "1") == "1"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_TIMEOUT_SECONDS = _int_env("GROQ_TIMEOUT_SECONDS", 15, 5)
GROQ_RETRIES = _int_env("GROQ_RETRIES", 2, 0)
GROQ_TEMPERATURE = _float_env("GROQ_TEMPERATURE", 0.3)
GROQ_TOP_P = _float_env("GROQ_TOP_P", 0.9)
GROQ_MAX_TOKENS = _int_env("GROQ_MAX_TOKENS", 700, 200)
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_SEARCH_URL = os.getenv("SERPER_SEARCH_URL", "https://google.serper.dev/search")
SERPER_TIMEOUT_SECONDS = _int_env("SERPER_TIMEOUT_SECONDS", 8, 2)
FREE_MODE = os.getenv("FREE_MODE", "0") == "1"
RAW_MODE = os.getenv("RAW_MODE", "0") == "1"
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
FALLBACK_MESSAGE = (
    "Desculpe, encontrei uma instabilidade ao gerar a orientacao de confeitaria. "
    "Tente novamente em instantes ou revise seus dados de custo e planejamento para seguir com seguranca."
)
KNOWLEDGE_CACHE_PATH = Path(os.getenv("KNOWLEDGE_CACHE_PATH", "knowledge_cache.json"))
CONTEXT_IDENTIFIER = os.getenv("CONTEXT_IDENTIFIER", "assistente_confeitaria")
API_KEY = os.getenv("ACOLHEIA_API_KEY")
REQUIRE_API_KEY = os.getenv("REQUIRE_API_KEY", "1") == "1"
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "1") == "1"
RATE_LIMIT_WINDOW_SECONDS = _int_env("RATE_LIMIT_WINDOW_SECONDS", 60, 1)
RATE_LIMIT_MAX_REQUESTS = _int_env("RATE_LIMIT_MAX_REQUESTS", 30, 1)

@dataclass(frozen=True)
class ContextConfig:
    scope_prompt: str
    academic_text: str
    references: List[dict]


CONFIG = ContextConfig(
    scope_prompt="""
Voce e o Assistente Saturno de Confeitaria, focado em ajudar confeiteiros(as) brasileiros(as) a organizar producao, precificacao, pedidos, rotina e crescimento do negocio.

Suas respostas devem:
1. Ser praticas, simples e orientadas a acao, com exemplos numericos quando o tema envolver preco, custo, margem ou lucro.
2. Priorizar organizacao do dia a dia: agenda de pedidos, cronograma de producao, compras, estoque e atendimento.
3. Ensinar calculos de forma didatica: custo por receita, custo por unidade, preco com margem, markup, ticket medio e ponto de equilibrio.
4. Sugerir checklists curtos e proximos passos claros para aplicar no mesmo dia.
5. Reforcar limites: sem prometer lucro, sem inventar dados e sem substituir contador, nutricionista ou advogado.
6. Manter tom profissional, humano e direto, sem linguagem dificil.

Se a pergunta estiver fora do escopo de confeitaria e gestao do negocio, responda de forma objetiva explicando esse limite e redirecione para o tema pratico relacionado mais proximo.
""",
    academic_text="""
Contexto: Pequenos negocios de confeitaria no Brasil operam com equipes enxutas, rotina intensa e margem sensivel a variacao de insumos. Confeiteiros(as) iniciantes e intermediarios, em especial MEIs e producao sob encomenda, precisam equilibrar qualidade, prazo e lucro sem perder controle da operacao.

Objetivo: Posicionar o Saturno Gestao como assistente de bolso para confeitaria, entregando orientacoes diretas sobre precificacao, ficha tecnica, compras, estoque, producao, pedidos, atendimento e crescimento sustentavel.

Metodologia: Sintese de praticas de gestao para confeitaria, operacao de encomendas e controle financeiro basico. O conteudo foi organizado em fluxos aplicaveis no dia a dia: planejamento semanal, execucao diaria da producao e revisao de indicadores.

Boas praticas-chave: (1) Precificar com base em custo real e margem alvo, nunca por achismo; (2) manter ficha tecnica atualizada por produto; (3) organizar producao por capacidade e prazo de entrega; (4) controlar estoque com FEFO e ponto de reposicao; (5) padronizar atendimento com regras de sinal, cancelamento e confirmacao.

Impacto esperado: Um assistente treinado com esses materiais reduz retrabalho, melhora previsibilidade, aumenta margem e fortalece a tomada de decisao baseada em dados simples do proprio negocio.
""",
    references=[
        {
            "label": "SEBRAE - Gestao Financeira para Pequenos Negocios",
            "url": "https://www.sebrae.com.br",
        },
        {
            "label": "ANVISA - Boas Praticas para Servicos de Alimentacao",
            "url": "https://www.gov.br/anvisa",
        },
        {
            "label": "Sistema CFN/CRN - Referencias Tecnicas em Alimentacao",
            "url": "https://www.cfn.org.br",
        },
        {
            "label": "Portal do Empreendedor - MEI",
            "url": "https://www.gov.br/empresas-e-negocios/pt-br/empreendedor",
        },
        {
            "label": "ABIA - Boas Praticas e Qualidade em Alimentos",
            "url": "https://www.abia.org.br",
        },
    ],
)

DEFAULT_TRAINING_FILES = [
    "docs/contexto_treinamento_confeitaria_v1.md",
    "docs/intents_confeitaria_v1.json",
    "docs/faq_confeitaria_v1.json",
]

app = FastAPI()

def _cors_origins_from_env() -> List[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "https://jornasaapp.vercel.app")
    if not raw:
        return []
    cleaned = raw.strip()
    if not cleaned:
        return []
    if cleaned == "*":
        logger.warning(
            "CORS_ALLOW_ORIGINS='*' habilitado. Em producao, prefira lista explicita de dominios."
        )
        return ["*"]
    return [item.strip() for item in raw.split(",") if item.strip()]

_cors_origins = _cors_origins_from_env()
_cors_allow_credentials = "*" not in _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

if DISABLE_GEMINI:
    GEMINI_MODEL = None
else:
    try:
        GEMINI_MODEL = genai.GenerativeModel(MODEL_NAME)
    except Exception as exc:
        logger.error("Falha ao instanciar modelo Gemini '%s': %s", MODEL_NAME, exc)
        GEMINI_MODEL = None

if USE_GROQ and GROQ_API_KEY:
    try:
        GROQ_CLIENT = Groq(api_key=GROQ_API_KEY)
    except Exception as exc:
        logger.error("Falha ao instanciar cliente Groq: %s", exc)
        GROQ_CLIENT = None
else:
    GROQ_CLIENT = None
if USE_GROQ and not GROQ_CLIENT:
    logger.warning(
        "USE_GROQ=1, mas GROQ_API_KEY nao esta disponivel ou cliente falhou. "
        "Backend tentara fallback para Gemini."
    )

if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    try:
        SUPABASE_CLIENT: Optional[Client] = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as exc:
        logger.error("Falha ao instanciar cliente Supabase: %s", exc)
        SUPABASE_CLIENT = None
else:
    SUPABASE_CLIENT = None

_knowledge_cache: Optional[str] = None
_knowledge_metadata: Optional[dict] = None
_rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = Lock()

if REQUIRE_API_KEY and not API_KEY:
    logger.error(
        "REQUIRE_API_KEY=1, mas ACOLHEIA_API_KEY nao foi configurada. "
        "Requisicoes sensiveis retornarao 503 ate a chave ser definida."
    )


def _knowledge_payload(summary: str, arquivos: List[str]) -> dict:
    if USE_GROQ and GROQ_CLIENT:
        provider_model = f"groq:{GROQ_MODEL}"
    elif GEMINI_MODEL:
        provider_model = f"gemini:{MODEL_NAME}"
    else:
        provider_model = None
    return {
        "summary": summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": provider_model,
        "training_files": arquivos,
        "references": CONFIG.references,
    }


def _load_knowledge_from_disk() -> Optional[str]:
    global _knowledge_metadata
    if not KNOWLEDGE_CACHE_PATH.exists():
        return None
    try:
        with KNOWLEDGE_CACHE_PATH.open("r", encoding="utf-8") as handler:
            data = json.load(handler)
        cached_files = data.get("training_files") or []
        current_files = _training_files()
        if sorted(cached_files) != sorted(current_files):
            logger.info(
                "Cache de conhecimento invalido por mudanca em training_files. cache=%s atual=%s",
                cached_files,
                current_files,
            )
            return None
        _knowledge_metadata = data
        summary = data.get("summary")
        if summary:
            logger.info(
                "Base de conhecimento carregada de %s (gerada em %s).",
                KNOWLEDGE_CACHE_PATH,
                data.get("generated_at"),
            )
        return summary
    except Exception as exc:
        logger.warning("Falha ao ler cache de conhecimento: %s", exc)
        return None


def _save_knowledge_to_disk(payload: dict) -> None:
    try:
        KNOWLEDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with KNOWLEDGE_CACHE_PATH.open("w", encoding="utf-8") as handler:
            json.dump(payload, handler, ensure_ascii=False, indent=2)
        logger.info("Base de conhecimento salva em %s", KNOWLEDGE_CACHE_PATH)
    except Exception as exc:
        logger.warning("Nao foi possivel salvar cache de conhecimento: %s", exc)

def _ler_pdf(path: str) -> str:
    with open(path, "rb") as handler:
        leitor = PdfReader(handler)
        paginas = []
        for page in leitor.pages:
            texto = page.extract_text()
            if texto:
                paginas.append(texto)
        return "\n".join(paginas)

def carregar_arquivos_treinamento(arquivos: List[str]) -> str:
    conhecimento = []
    for arquivo in arquivos:
        try:
            if arquivo.endswith(".pdf"):
                conhecimento.append(_ler_pdf(arquivo))
            else:
                with open(arquivo, "r", encoding="utf-8") as handler:
                    conhecimento.append(handler.read())
        except Exception as exc:
            logger.warning("Erro ao carregar %s: %s", arquivo, exc)
    return "\n\n".join(filter(None, conhecimento))

def resumir_conhecimento(conhecimento_texto: str) -> str:
    if not conhecimento_texto:
        return ""
    if not GEMINI_MODEL:
        logger.warning("Modelo Gemini indisponivel; retornando conhecimento sem resumo.")
        return conhecimento_texto[:MAX_KNOWLEDGE_CHARS]
    try:
        prompt = (
            "Resuma o texto abaixo destacando boas praticas para confeitaria e gestao: precificacao,"
            " ficha tecnica, compras, controle de estoque, producao por encomenda, atendimento, ticket medio,"
            " margem, lucro e ponto de equilibrio. Gere um resumo direto, com bullets e formulas simples quando"
            " fizer sentido, em portugues do Brasil.\n\n"
            f"Texto:\n{conhecimento_texto[:MAX_PROMPT_CHARS]}"
        )
        resposta = GEMINI_MODEL.generate_content(prompt)
        if resposta and getattr(resposta, "text", None):
            return resposta.text
    except Exception as exc:
        logger.warning("Falha ao resumir conhecimento: %s", exc)
    return conhecimento_texto[:MAX_KNOWLEDGE_CHARS]

def _training_files() -> List[str]:
    env_value = [
        item.strip()
        for item in os.getenv("TRAINING_FILES", "").split(",")
        if item.strip()
    ]
    if env_value:
        return env_value
    return DEFAULT_TRAINING_FILES

def build_base_conhecimento(use_model: bool = True) -> str:
    arquivos = _training_files()
    if arquivos:
        logger.info("Carregando arquivos de conhecimento: %s", ", ".join(arquivos))
    conteudo_treinamento = carregar_arquivos_treinamento(arquivos)
    referencias_texto = "Referencias principais:\n" + "\n".join(
        f"- {ref['label']} ({ref['url']})" for ref in CONFIG.references
    )
    conteudo_total = "\n\n".join(
        fragment
        for fragment in [
            CONFIG.academic_text.strip(),
            conteudo_treinamento,
            referencias_texto,
        ]
        if fragment
    )
    resumo = (
        resumir_conhecimento(conteudo_total) if use_model else conteudo_total[:MAX_KNOWLEDGE_CHARS]
    )
    if len(resumo) > MAX_KNOWLEDGE_CHARS:
        resumo = resumo[:MAX_KNOWLEDGE_CHARS] + "\n[Texto truncado]"
    return resumo

def build_knowledge_cache(force_model: bool = True) -> dict:
    global _knowledge_cache, _knowledge_metadata
    use_model = force_model and GEMINI_MODEL is not None and not DISABLE_GEMINI
    if force_model and not use_model:
        logger.warning("Modelo Gemini indisponivel; gerando conhecimento sem IA.")
    resumo = build_base_conhecimento(use_model=use_model)
    payload = _knowledge_payload(resumo, _training_files())
    _save_knowledge_to_disk(payload)
    _knowledge_cache = resumo
    _knowledge_metadata = payload
    return payload

def get_base_conhecimento(force_refresh: bool = False) -> str:
    global _knowledge_cache, _knowledge_metadata
    if not force_refresh and _knowledge_cache:
        return _knowledge_cache

    if not force_refresh:
        cached = _load_knowledge_from_disk()
        if cached:
            _knowledge_cache = cached
            return _knowledge_cache

    logger.info("Atualizando base de conhecimento (force_refresh=%s)", force_refresh)
    use_model = GEMINI_MODEL is not None and not DISABLE_GEMINI
    payload = build_knowledge_cache(force_model=use_model)
    return payload["summary"]

def _extract_fontes_from_markdown(resposta: str) -> List[dict]:
    linhas = resposta.splitlines()
    fontes_identificadas: List[dict] = []
    header_pattern = re.compile(r"^fontes?\s*:\s*$", flags=re.IGNORECASE)
    url_pattern = re.compile(r"\(?\b(https?://[^\s)]+)\)?", flags=re.IGNORECASE)

    for idx, linha in enumerate(linhas):
        if header_pattern.match(linha.strip()):
            for seguinte in linhas[idx + 1 :]:
                if not seguinte.strip():
                    break
                item = seguinte.strip().lstrip(" -*\u2022").strip()
                if not item:
                    continue
                url_match = url_pattern.search(item)
                if not url_match:
                    continue
                url = url_match.group(1).rstrip(").,;")
                label = (
                    item[: url_match.start()]
                    .strip()
                    .rstrip(" -:\u2013\u2014\u2022")
                    .strip()
                )
                if not label:
                    label = url
                fontes_identificadas.append({"label": label, "url": url})
            break
    return fontes_identificadas


def _dedupe_fontes(fontes: List[dict]) -> List[dict]:
    resultado: List[dict] = []
    vistos = set()
    for item in fontes:
        label = (item.get("label") or "").strip()
        url = (item.get("url") or "").strip()
        if not label and not url:
            continue
        chave = (label.lower(), url.lower())
        if chave in vistos:
            continue
        vistos.add(chave)
        resultado.append({"label": label or url, "url": url})
    return resultado


def _sanitize_text(resposta: str) -> str:
    texto = resposta.strip()
    if texto:
        texto = re.sub(r"\*\*(.+?)\*\*", r"\1", texto)
        texto = re.sub(r"#+\s*(Fontes:)", r"\1", texto, flags=re.IGNORECASE)
    return texto.strip()

def _strip_model_chatter(resposta: str) -> str:
    """Remove instrucoes ou rumores de chat do modelo antes de formatar."""
    linhas_filtradas: List[str] = []
    chatter_patterns = [
        re.compile(r"^instrucao\s*:?", flags=re.IGNORECASE),
        re.compile(r"continue a conversa", flags=re.IGNORECASE),
        re.compile(r"^sou o jornasa", flags=re.IGNORECASE),
        re.compile(r"^sou o jornaia", flags=re.IGNORECASE),
        re.compile(r"^minha missao", flags=re.IGNORECASE),
        re.compile(r"protocolo de resposta", flags=re.IGNORECASE),
        re.compile(r"assistente direto", flags=re.IGNORECASE),
        re.compile(r"qual (e|é) sua duvida", flags=re.IGNORECASE),
        re.compile(r"pode me dizer", flags=re.IGNORECASE),
        re.compile(r"mande sua primeira duvida", flags=re.IGNORECASE),
        re.compile(r"estou aqui para ajudar", flags=re.IGNORECASE),
        re.compile(r"qual (e|é) a sua pergunta", flags=re.IGNORECASE),
        re.compile(r"vamos comecar", flags=re.IGNORECASE),
    ]
    for linha in resposta.splitlines():
        if any(p.search(linha) for p in chatter_patterns):
            continue
        linhas_filtradas.append(linha)
    return "\n".join(linhas_filtradas).strip()


def _trim_to_first_section(resposta: str) -> str:
    """Descarta saudacoes e texto antes do primeiro heading markdown."""
    linhas = resposta.splitlines()
    for idx, linha in enumerate(linhas):
        if re.match(r"\s*#{1,6}\s", linha):
            return "\n".join(linhas[idx:]).strip()
    return resposta.strip()


def _request_client_identifier(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
        if ip:
            return ip
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _enforce_rate_limit(request: Request, route_name: str) -> None:
    if not RATE_LIMIT_ENABLED:
        return

    client_id = _request_client_identifier(request)
    bucket_key = f"{route_name}:{client_id}"
    now = time.monotonic()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS

    with _rate_limit_lock:
        bucket = _rate_limit_buckets[bucket_key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            logger.warning(
                "Rate limit excedido em %s para %s (%s req/%ss).",
                route_name,
                client_id,
                RATE_LIMIT_MAX_REQUESTS,
                RATE_LIMIT_WINDOW_SECONDS,
            )
            raise HTTPException(
                status_code=429,
                detail="Limite de requisicoes excedido. Tente novamente em instantes.",
            )
        bucket.append(now)


def _enforce_api_key(request: Request) -> None:
    if not REQUIRE_API_KEY and not API_KEY:
        return
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Servico indisponivel: autenticacao nao configurada.",
        )
    provided_key = request.headers.get("x-jornasa-key")
    if provided_key != API_KEY:
        raise HTTPException(status_code=401, detail="Chave de acesso invalida.")


def buscar_na_web(consulta: str) -> Optional[str]:
    """Busca web opcional via Serper; retorna texto concatenado de snippets."""
    if not SERPER_API_KEY:
        return None
    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"q": consulta, "num": 5}
    try:
        with httpx.Client(timeout=SERPER_TIMEOUT_SECONDS) as client:
            resp = client.post(SERPER_SEARCH_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Falha na busca web: %s", exc)
        return None

    results = data.get("organic") or []
    if not results:
        return None

    snippets = []
    for item in results[:5]:
        title = item.get("title") or ""
        snippet = item.get("snippet") or ""
        link = item.get("link") or ""
        partes = [part for part in [title, snippet, link] if part]
        if partes:
            snippets.append(" - ".join(partes))
    return "\n".join(snippets) if snippets else None


def format_response_text(
    resposta: str, *, is_fallback: bool, model_name: Optional[str]
) -> FormattedResponse:
    raw_markdown = resposta.strip()
    cleaned_markdown = _trim_to_first_section(_strip_model_chatter(raw_markdown))
    fontes_modelo = _extract_fontes_from_markdown(cleaned_markdown)
    if fontes_modelo:
        fontes = _dedupe_fontes(fontes_modelo)
    elif INCLUDE_DEFAULT_FONTES:
        fontes = _dedupe_fontes(CONFIG.references)
    else:
        fontes = []

    fontes_texto = "\n".join(f"- {ref['label']} ({ref['url']})" for ref in fontes)
    corpo = _sanitize_text(cleaned_markdown)
    texto_com_fontes = (
        f"{corpo}\n\nFontes recomendadas:\n{fontes_texto}".strip() if fontes_texto else corpo
    )

    return FormattedResponse(
        raw_markdown=raw_markdown,
        texto=corpo,
        texto_com_fontes=texto_com_fontes,
        fontes=fontes,
        model_used=model_name,
        is_fallback=is_fallback,
    )

def _build_prompt(base_conhecimento: str, mensagem: str, recente: Optional[str]) -> str:
    if RAW_MODE:
        return f"""
            Pedido do usuario: "{mensagem}"
            Informacoes recentes (se houver): {recente or 'Nenhuma busca web solicitada.'}
        """

    if FREE_MODE:
        return f"""
            Atue como ghostwriter e entregue exatamente o que o usuario pediu (roteiro de producao, checklist, orientacao de precificacao, mensagem de atendimento, plano de vendas ou texto). Nao fale de si nem do Saturno. Se pedirem texto, escreva direto (8-12 linhas), sem saudacao.

            Informacoes recentes (se houver):
            {recente or 'Nenhuma busca web solicitada.'}

            Pedido: "{mensagem}"
        """

    return f"""
        Voce e um assistente para confeiteiros(as). Entregue exatamente o que o usuario pedir (precificacao, ficha tecnica, plano de producao, organizacao de pedidos, atendimento, vendas ou analise), de forma concisa e acionavel. Se for um texto, escreva-o direto (8-12 linhas) antes das secoes.

        Base de conhecimento adicional (use apenas o essencial):
        {base_conhecimento[:MAX_PROMPT_CHARS]}

        Informacoes recentes da web (use apenas se relevantes):
        {recente or 'Nenhuma busca web solicitada.'}

        Pedido: "{mensagem}"

        Regras:
        - Nao peça confirmacao nem pergunte de volta; responda tudo ja na primeira mensagem.
        - Se ambiguidade, escolha um angulo pratico de confeitaria e produza o conteudo completo.
        - Sem apresentacoes; comecar pela entrega.
        - Linguagem direta, sem jargoes desnecessarios; listas curtas e checklists.
        - Em temas de preco/custo/lucro, mostre formula e exemplo numerico.
        - Nao invente dados e nao prometa lucro garantido.
        - Oriente sem substituir contador, nutricionista ou advogado quando o tema exigir.
        - Formato final (use apenas o que se encaixar):
          ## Entrega (solucao pedida)
          ## Proximos passos (3-5 itens com verbo de acao)
          ## Riscos e cuidados (2-3 bullets de validacao)
          ## Fontes (2-4 referencias; omita se nao houver)
    """

def enviar_mensagem_gemini(mensagem: str, recente: Optional[str]) -> FormattedResponse:
    base_conhecimento = get_base_conhecimento()
    prompt = _build_prompt(base_conhecimento, mensagem, recente)
    prompt = prompt[:MAX_PROMPT_CHARS]

    provider = None
    used_web = bool(recente)

    if USE_GROQ and GROQ_CLIENT:
        last_exc: Optional[Exception] = None
        start = time.monotonic()
        for tentativa in range(GROQ_RETRIES + 1):
            try:
                resposta = GROQ_CLIENT.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=GROQ_TEMPERATURE,
                    top_p=GROQ_TOP_P,
                    max_tokens=GROQ_MAX_TOKENS,
                    timeout=GROQ_TIMEOUT_SECONDS,
                )
                choice = resposta.choices[0] if resposta and resposta.choices else None
                texto = choice.message.content if choice and choice.message else None
                if texto:
                    latency_ms = int((time.monotonic() - start) * 1000)
                    provider = "groq"
                    logger.info(
                        "Groq respondeu em %sms (tentativa %s/%s) | web=%s.",
                        latency_ms,
                        tentativa + 1,
                        GROQ_RETRIES + 1,
                        used_web,
                    )
                    return format_response_text(
                        texto,
                        is_fallback=False,
                        model_name=f"{provider}:{GROQ_MODEL}",
                    )
                logger.warning("Resposta vazia recebida da Groq (tentativa %s).", tentativa + 1)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Falha ao gerar conteudo com Groq (tentativa %s/%s): %s",
                    tentativa + 1,
                    GROQ_RETRIES + 1,
                    exc,
                )
        if last_exc:
            logger.exception("Erro ao gerar conteudo com Groq apos retries: %s", last_exc)
        return format_response_text(FALLBACK_MESSAGE, is_fallback=True, model_name=None)
    if USE_GROQ and not GROQ_CLIENT:
        logger.warning(
            "Groq habilitado, mas indisponivel. Aplicando fallback para Gemini nesta requisicao."
        )

    if not GEMINI_MODEL:
        logger.error("Modelo Gemini nao carregado; nao e possivel responder.")
        return format_response_text(
            "Desculpe, o servico de IA esta temporariamente indisponivel. "
            "Tente novamente em instantes ou siga com seu checklist de custos e prazos.",
            is_fallback=True,
            model_name=None,
        )

    last_exc: Optional[Exception] = None
    start = time.monotonic()
    for tentativa in range(GEMINI_RETRIES + 1):
        try:
            resposta = GEMINI_MODEL.generate_content(
                prompt,
                request_options={"timeout": float(GEMINI_TIMEOUT_SECONDS)},
            )
            if resposta and getattr(resposta, "text", None):
                latency_ms = int((time.monotonic() - start) * 1000)
                provider = "gemini"
                logger.info(
                    "Gemini respondeu em %sms (tentativa %s/%s) | web=%s.",
                    latency_ms,
                    tentativa + 1,
                    GEMINI_RETRIES + 1,
                    used_web,
                )
                return format_response_text(
                    resposta.text,
                    is_fallback=False,
                    model_name=f"{provider}:{MODEL_NAME}",
                )
            logger.warning("Resposta vazia recebida da API Gemini (tentativa %s).", tentativa + 1)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Falha ao gerar conteudo com Gemini (tentativa %s/%s): %s",
                tentativa + 1,
                GEMINI_RETRIES + 1,
                exc,
            )

    if last_exc:
        logger.exception("Erro ao gerar conteudo com Gemini apos retries: %s", last_exc)
    return format_response_text(FALLBACK_MESSAGE, is_fallback=True, model_name=None)

class FormattedResponse(BaseModel):
    raw_markdown: str
    texto: str
    texto_com_fontes: str
    fontes: List[dict]
    model_used: Optional[str]
    is_fallback: bool


class MensagemEntrada(BaseModel):
    mensagem: str
    buscar_web: bool = False

class NotificacaoEntrada(BaseModel):
    user_id: str
    titulo: str
    descricao: str
    data: str

@app.post("/mensagem")
async def mensagem(payload: MensagemEntrada, request: Request):
    _enforce_api_key(request)
    _enforce_rate_limit(request, "/mensagem")

    incoming_msg = payload.mensagem.strip()
    if not incoming_msg:
        raise HTTPException(status_code=400, detail="Mensagem nao pode ser vazia.")
    if len(incoming_msg) > MAX_MESSAGE_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Mensagem excede o limite de {MAX_MESSAGE_CHARS} caracteres.",
        )

    recente = None
    if payload.buscar_web:
        recente = buscar_na_web(incoming_msg)

    logger.info("Mensagem recebida: %s", incoming_msg)
    gemini_resposta = enviar_mensagem_gemini(incoming_msg, recente)
    preview = gemini_resposta.raw_markdown.replace("\n", " ").strip()
    if len(preview) > 160:
        preview = preview[:157] + "..."
    logger.info("Resposta do modelo (preview): %s | fallback=%s", preview, gemini_resposta.is_fallback)

    if not gemini_resposta.texto:
        raise HTTPException(status_code=502, detail="Resposta vazia do modelo.")

    global _knowledge_metadata
    if _knowledge_metadata is None:
        get_base_conhecimento()
    metadata = _knowledge_metadata or {}

    if payload.buscar_web and not SERPER_API_KEY:
        logger.warning("buscar_web solicitado, mas SERPER_API_KEY nao configurada.")

    return {
        "resposta_markdown": gemini_resposta.raw_markdown,
        "resposta": gemini_resposta.texto,
        "resposta_com_fontes": gemini_resposta.texto_com_fontes,
        "fontes": gemini_resposta.fontes,
        "contexto": CONTEXT_IDENTIFIER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "knowledge_generated_at": metadata.get("generated_at"),
        "knowledge_files": metadata.get("training_files"),
        "model_used": gemini_resposta.model_used,
        "used_web_search": bool(payload.buscar_web),
        "is_fallback": gemini_resposta.is_fallback,
    }

@app.get("/health")
async def health_check():
    return {"status": "ok"}


def _require_supabase():
    if SUPABASE_CLIENT is None:
        logger.error("Supabase nao configurado; defina SUPABASE_URL e SUPABASE_SERVICE_KEY.")
        raise HTTPException(status_code=500, detail="Supabase nao configurado.")
    return SUPABASE_CLIENT


@app.post("/notificacoes")
async def criar_notificacao(payload: NotificacaoEntrada, request: Request):
    _enforce_api_key(request)
    _enforce_rate_limit(request, "/notificacoes")
    client = _require_supabase()
    dados = {
        "user_id": payload.user_id,
        "titulo": payload.titulo,
        "descricao": payload.descricao,
        "data": payload.data,
        "read": False,
    }
    try:
        resp = client.table("notifications").insert(dados).execute()
        inserido = resp.data[0] if resp and resp.data else dados
        return inserido
    except Exception as exc:
        logger.exception("Falha ao inserir notificacao no Supabase: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao salvar notificacao.")


@app.get("/notificacoes")
async def listar_notificacoes(user_id: str, request: Request):
    _enforce_api_key(request)
    _enforce_rate_limit(request, "/notificacoes")
    client = _require_supabase()
    try:
        resp = (
            client.table("notifications")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return resp.data if resp else []
    except Exception as exc:
        logger.exception("Falha ao listar notificacoes no Supabase: %s", exc)
        raise HTTPException(status_code=500, detail="Erro ao listar notificacoes.")


@app.options("/{path:path}")
async def cors_preflight(path: str) -> Response:
    return Response(status_code=204)


@app.options("/mensagem")
async def mensagem_options() -> Response:
    return Response(status_code=204)

def parse_cli_args():
    parser = argparse.ArgumentParser(description="Ferramentas de apoio do Saturno Gestao.")
    parser.add_argument(
        "--build-knowledge",
        action="store_true",
        help="Gera e salva o cache de conhecimento local.",
    )
    parser.add_argument(
        "--skip-model",
        action="store_true",
        help="Nao utiliza o modelo Gemini ao gerar o conhecimento (usa apenas texto base).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="Host para executar o servidor (padrao: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="Porta do servidor (padrao: variavel PORT ou 8000).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_cli_args()

    if args.build_knowledge:
        payload = build_knowledge_cache(force_model=not args.skip_model)
        print(
            "Resumo salvo em {path} (tam: {chars} chars, gerado em {ts})".format(
                path=KNOWLEDGE_CACHE_PATH,
                chars=len(payload["summary"]),
                ts=payload["generated_at"],
            )
        )
    else:
        try:
            uvicorn.run(app, host=args.host, port=args.port)
        except SystemExit:
            logger.error("O servidor foi encerrado inesperadamente.")
